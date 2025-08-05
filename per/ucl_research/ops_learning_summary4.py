"""
ops_learning_summary4.py
========================

Consolidated operational learning summary processor that combines functionality from
ops_learning_summary2.py and ops_learning_summary3.py with enhanced error handling,
caching, and performance monitoring.

This file maintains the existing logic intact while adding:
- Enhanced caching with Redis
- Comprehensive error handling
- Performance indicators and monitoring
- Celery task management support
- Better maintainability through common utilities

Architecture:
- BaseOpsLearningTask: Common utilities and caching
- OpsLearningSummaryTask: Complex ops learning analysis (from v2)
- DrefSummaryTask: DREF-specific operations (from v3)
"""

import ast
import json
import hashlib
import re
import typing
import tiktoken
from datetime import datetime
from itertools import chain, zip_longest
from typing import Dict, Any, Optional, List, Union

import pandas as pd
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.test import override_settings
from django.utils.functional import cached_property
from openai import AzureOpenAI

from api.logger import logger
from api.models import Country
from api.utils import get_model_name
from deployments.models import SectorTag
from lang.tasks import translate_model_fields
from main.lock import RedisLockKey, redis_lock
from per.cache import OpslearningSummaryCacheHelper
from per.models import (
    FormComponent,
    FormPrioritization,
    OpsLearning,
    OpsLearningCacheResponse,
    OpsLearningComponentCacheResponse,
    OpsLearningPromptResponseCache,
    OpsLearningSectorCacheResponse,
    Overview,
)
from per.dref_temp.dref_utils import dref_manager, DREFFilters


class PerformanceMonitor:
    """Performance monitoring utilities for tracking execution metrics"""
    
    @staticmethod
    def track_execution_time(func_name: str, start_time: datetime, end_time: datetime) -> None:
        """Track execution time for performance monitoring"""
        execution_time = (end_time - start_time).total_seconds()
        cache_key = f"perf_monitor:{func_name}:{datetime.now().strftime('%Y%m%d')}"
        
        # Store execution metrics in cache for 24 hours
        existing_data = cache.get(cache_key, {"count": 0, "total_time": 0, "avg_time": 0})
        existing_data["count"] += 1
        existing_data["total_time"] += execution_time
        existing_data["avg_time"] = existing_data["total_time"] / existing_data["count"]
        
        cache.set(cache_key, existing_data, timeout=86400)  # 24 hours


class BaseAITask:
    """Base class with Azure OpenAI integration and common utilities for AI-powered tasks"""
    
    ENCODING_NAME = "cl100k_base"
    MAX_RETRIES = 3
    
    @cached_property
    def azure_client(self):
        """Azure OpenAI client with enhanced error handling"""
        return AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT, 
            api_key=settings.AZURE_OPENAI_KEY, 
            api_version="2023-05-15"
        )
    
    @staticmethod
    def count_tokens(string: str, encoding_name: str) -> int:
        """Returns the number of tokens in a text string."""
        try:
            encoding = tiktoken.get_encoding(encoding_name)
            return len(encoding.encode(string))
        except Exception as e:
            logger.error(f"Error counting tokens: {e}")
            # Fallback to character count approximation
            return len(string) // 4
    
    @staticmethod
    def generate_cache_key(data: Any, prefix: str = "ops_learning") -> str:
        """Generate consistent cache keys for data"""
        try:
            if isinstance(data, str):
                content = data
            else:
                content = json.dumps(data, sort_keys=True, default=str)
            
            hash_obj = hashlib.md5(content.encode('utf-8'))
            return f"{prefix}:{hash_obj.hexdigest()}"
        except Exception as e:
            logger.error(f"Error generating cache key: {e}")
            return f"{prefix}:{hash(str(data))}"
    
    CACHE_TTL = 3600  # 1 hour
    
    @classmethod
    def get_cached_result(cls, cache_key: str, default=None):
        """Get cached result with error handling"""
        try:
            return cache.get(cache_key, default)
        except Exception as e:
            logger.error(f"Cache retrieval error for key {cache_key}: {e}")
            return default
    
    @classmethod
    def set_cached_result(cls, cache_key: str, data: Any, timeout: int = None) -> bool:
        """Set cached result with error handling"""
        try:
            timeout = timeout or cls.CACHE_TTL
            cache.set(cache_key, data, timeout)
            return True
        except Exception as e:
            logger.error(f"Cache storage error for key {cache_key}: {e}")
            return False
    
    @staticmethod
    def calculate_confidence_score(
        ai_response: str, 
        source_data_count: int = 0, 
        has_specific_facts: bool = False,
        response_length: int = 0
    ) -> str:
        """
        Calculate confidence score based on multiple factors.
        
        Args:
            ai_response: The AI-generated response text
            source_data_count: Number of source data points used
            has_specific_facts: Whether response contains specific facts/numbers
            response_length: Length of the response
            
        Returns:
            Confidence level: 'high', 'medium', or 'low'
        """
        if not ai_response or ai_response.strip() == "":
            return "low"
            
        # Check if response indicates insufficient data
        insufficient_indicators = [
            "not available", "insufficient", "no data", "no information",
            "enough source is not available", "limited data", "unclear from sources"
        ]
        if any(indicator in ai_response.lower() for indicator in insufficient_indicators):
            return "low"
        
        # Check for explicit confidence mentions in AI response
        if "confidence level" in ai_response.lower():
            import re
            confidence_match = re.search(r'confidence level[:\s]*(\w+)', ai_response.lower())
            if confidence_match:
                level = confidence_match.group(1).strip()
                if level in ['high', 'medium', 'low']:
                    return level
        
        # Calculate score based on multiple factors
        score = 0
        
        # Factor 1: Source data availability (0-3 points)
        if source_data_count >= 5:
            score += 3
        elif source_data_count >= 2:
            score += 2
        elif source_data_count >= 1:
            score += 1
        
        # Factor 2: Response quality indicators (0-2 points)
        if has_specific_facts or any(indicator in ai_response.lower() for indicator in 
                                   ['specific', 'according to', 'based on', 'documented', 'reported']):
            score += 2
        elif response_length > 100:  # Substantial response
            score += 1
            
        # Factor 3: Response length and detail (0-1 point)
        if response_length > 200:
            score += 1
        
        # Map score to confidence level
        if score >= 5:
            return "high"
        elif score >= 3:
            return "medium"
        else:
            return "low"
    
    def get_azure_response(self, messages: List[Dict[str, str]], cache_prefix: str = "ops_learning") -> Optional[str]:
        """Get Azure OpenAI response with enhanced caching and error handling"""
        start_time = datetime.now()
        
        # Generate new response with retries
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.azure_client.chat.completions.create(
                    model=settings.AZURE_OPENAI_DEPLOYMENT_NAME, 
                    messages=messages, 
                    temperature=0.7
                )
                response_content = response.choices[0].message.content
                
                # Track performance
                end_time = datetime.now()
                PerformanceMonitor.track_execution_time(
                    f"azure_openai_{cache_prefix}", start_time, end_time
                )
                
                return response_content
                
            except Exception as e:
                logger.warning(f"Azure OpenAI attempt {attempt + 1} failed for {cache_prefix}: {e}")
                if attempt == self.MAX_RETRIES - 1:
                    logger.error(f"All {self.MAX_RETRIES} attempts failed for {cache_prefix}: {e}")
                    return None
        
        return None


class OpsLearningSummaryTask(BaseAITask):
    """
    Complex operational learning summary task (from ops_learning_summary2.py)
    Maintains original logic with enhanced error handling and caching
    """
    
    PROMPT_DATA_LENGTH_LIMIT = 5000
    PROMPT_LENGTH_LIMIT = 7500
    MIN_DIF_COMPONENTS = 3
    MIN_DIF_EXCERPTS = 3

    primary_prompt = (
        "\nBelow is a list of event summaries and their associated operational learnings. "
        "Your task is to synthesize across all these data points and produce **3 to 6 clear, concise, evidence‑based highlights** "
        "that capture the key similarities, patterns or lessons learned. For each highlight include:\n\n"
        " 1. **Title**: A very short bold summary (20–30 characters).\n"
        " 2. **Content**: One or two sentences explaining the highlight, referencing the type of event or country (e.g. 'Based on droughts in Ethiopia, 2023').\n"
        " 3. **Supporting IDs**: A list of the excerpt IDs that back up the highlight (do not include IDs in the prose itself).\n\n"
        "Please return the top 3–6 insights as plain text bullet points—no JSON, no extra markup.\n\n"
    )
    
    component_prompt = (
        "\n Please aggregate and summarize this data into structured paragraphs (as few as possible, as many as necessary). \n "
        "The output SHOULD ALWAYS follow the format below:\n"
        "- *Type*: 'component'\n"
        "- *Subtype*: Provides the name of the component to which the paragraph refers.\n"
        "- *Excerpts ID*: Identify the ids of the excerpts you took into account for creating the summary.\n"
        "*Content*: A short summary aggregating findings related to the Subtype, "
        "so that they are supported by evidence coming from more than one report, "
        "and there is ONLY ONE entry per subtype. Always integrate in the paragraph evidence that supports "
        "it from the data available from multiples reports or items, include year and country of the evidence. "
        "The length of each paragraph MUST be between 20 and 30 words.\n"
        " Important:\n\n"
        "- ONLY create one summary per subtype\n"
        "- DO NOT mention the ids of the excerpts in the content of the summary.\n"
        "- DO NOT use data from any source other than the one provided.\n\n"
        "Output Format:\n"
        "Provide your answer in valid JSON form. Reply with ONLY the answer in JSON form and include NO OTHER COMMENTARY.\n"
        '{"0": {"type": "component", "subtype": "Information Management", "excerpts id":"23, 235", "content": "lorem ipsum"}, '
        '"1": {"type": "component", "subtype": "Logistics", "excerpts id":"45, 678", "content": "lorem ipsum"}}'
    )

    sector_prompt = (
        "\n Please aggregate and summarize this data into structured paragraphs (as few as possible, as many as necessary). \n "
        "The output SHOULD ALWAYS follow the format below:\n"
        "- *Type*: 'sector'\n"
        "- *Subtype*: Provides the name of the sector to which the paragraph refers.\n"
        "- *Excerpts ID*: Identify the ids of the excerpts you took into account for creating the summary.\n"
        "*Content*: A short summary aggregating findings related to the Subtype, "
        "so that they are supported by evidence coming from more than one report, "
        "and there is ONLY ONE entry per subtype. Always integrate in the paragraph evidence that supports "
        "it from the data available from multiples reports or items, include year and country of the evidence. "
        "The length of each paragraph MUST be between 20 and 30 words.\n"
        " Important:\n\n"
        "- ONLY create one summary per subtype\n"
        "- DO NOT mention the ids of the excerpts in the content of the summary.\n"
        "- DO NOT use data from any source other than the one provided.\n\n"
        "Output Format:\n"
        "Provide your answer in valid JSON form. Reply with ONLY the answer in JSON form and include NO OTHER COMMENTARY.\n"
        '{"0": {"type": "sector", "subtype": "shelter", "excerpts id":"43, 1375, 14543", "content": "lorem ipsum"}, '
        '"1": {"type": "sector", "subtype": "WASH", "excerpts id":"30, 40", "content": "lorem ipsum"}}'
    )

    system_message = (
        "# CONTEXT # You are assisting with extracting operational learning from validated reports. "
        "Your goal is to generate concise, evidence-based summaries that support future emergency response planning. "
        "# STYLE # Use a writing style that is professional but informal."
        "# TONE # Encouraging and motivating."
        "# AUDIENCE # The audience is emergency response personnel from the Red Cross and Red Crescent. "
        "They are action-oriented people who have very little time so they need concise, "
        "not obvious information that can be easily consumed and acted upon in the time of a response."
    )

    primary_instruction_prompt = (
        "You should:\n"
        "1. Spot operational patterns: What approaches worked or didn't.\n"
        "2. Explain cause and effect: What led to outcomes.\n"
        "3. Include brief context: Say where the insight came from (e.g. 'based on response to cyclone in Mozambique').\n"
        "4. Choose top 3: Focus on the most important and recurring findings.\n"
        "5. Mention if any country's reports contradict the trend.\n"
    )

    secondary_instruction_prompt = (
        "You should for each section in the data (TYPE & SUBTYPE combination):\n"
        "1. Describe, Summarize and Compare: Identify and detail the who, what, where and when "
        "2. Explain and Connect: Analyze why events happened and how they are related "
        "3. Identify gaps: Assess what data is available, what is missing and potential biases "
        "4. Identify key messages: Determine if there are important stories and signals hidden in the data "
        "5. Conclude and make your case "
    )

    @staticmethod
    def change_ops_learning_status(instance: OpsLearningCacheResponse, status: OpsLearningCacheResponse.Status):
        """Changes the status of the OPS learning instance."""
        try:
            instance.status = status
            instance.save(update_fields=["status"])
            logger.info(f"Updated ops learning status to {status}")
        except Exception as e:
            logger.error(f"Error updating ops learning status: {e}")

    @staticmethod
    def add_used_ops_learnings(instance: OpsLearningCacheResponse, used_ops_learnings: typing.List[int]):
        """Adds the used OPS learnings to the cache response."""
        try:
            instance.used_ops_learning.add(*used_ops_learnings)
            logger.info(f"Added {len(used_ops_learnings)} ops learnings to cache response")
        except Exception as e:
            logger.error(f"Error adding ops learnings: {e}")

    @staticmethod
    def add_used_ops_learnings_sector(
        instance: OpsLearningCacheResponse, content: str, used_ops_learnings: typing.List[int], sector: str
    ):
        """Adds the used OPS learnings to the cache response."""
        try:
            sector_instance = (
                SectorTag.objects.exclude(is_deprecated=True)
                .filter(title__iexact=sector)
                .first()
            )
            if not sector_instance:
                logger.info(f"Sector '{sector}' not found.")
                return
            
            ops_learning_instances = OpsLearning.objects.filter(is_validated=True, id__in=used_ops_learnings)
            if len(ops_learning_instances):
                ops_learning_sector, created = (
                    OpsLearningSectorCacheResponse.objects.select_related("filter_response", "sector")
                    .prefetch_related("used_ops_learning")
                    .get_or_create(
                        sector=sector_instance,
                        filter_response=instance,
                        defaults={"content": content},
                    )
                )
                if created:
                    ops_learning_sector.used_ops_learning.add(*ops_learning_instances)
                    transaction.on_commit(
                        lambda: translate_model_fields.delay(
                            get_model_name(type(ops_learning_sector)),
                            ops_learning_sector.pk,
                        )
                    )
                    logger.info(f"Created sector cache response for {sector}")
        except Exception as e:
            logger.error(f"Error adding sector ops learnings: {e}")

    @staticmethod
    def add_used_ops_learnings_component(
        instance: OpsLearningCacheResponse,
        content: str,
        used_ops_learnings: typing.List[int],
        component: str,
    ):
        """Adds the used OPS learnings to the cache response."""
        try:
            component_instance = FormComponent.objects.filter(title__iexact=component).first()
            if not component_instance:
                logger.info(f"Component '{component}' not found.")
                return
            
            ops_learning_instances = OpsLearning.objects.filter(is_validated=True, id__in=used_ops_learnings)
            if len(ops_learning_instances):
                ops_learning_component, created = (
                    OpsLearningComponentCacheResponse.objects.select_related("filter_response", "component")
                    .prefetch_related("used_ops_learning")
                    .get_or_create(
                        component=component_instance,
                        filter_response=instance,
                        defaults={"content": content},
                    )
                )
                if created:
                    ops_learning_component.used_ops_learning.add(*ops_learning_instances)
                    transaction.on_commit(
                        lambda: translate_model_fields.delay(
                            get_model_name(type(ops_learning_component)),
                            ops_learning_component.pk,
                        )
                    )
                    logger.info(f"Created component cache response for {component}")
        except Exception as e:
            logger.error(f"Error adding component ops learnings: {e}")

    @classmethod
    def fetch_ops_learnings(cls, filter_data):
        """Fetches the OPS learnings from the database with enhanced error handling."""
        start_time = datetime.now()
        
        try:
            ops_learning_qs = (
                OpsLearning.objects.filter(is_validated=True)
                .select_related(
                    "per_component_validated", "sector_validated", "appeal_code__country", 
                    "appeal_code__region", "appeal_code__dtype"
                )
                .annotate(
                    excerpts_id=F("id"),
                    component_title=F("per_component_validated__title"),
                    sector_title=F("sector_validated__title"),
                    country_id=F("appeal_code__country__id"),
                    country_name=F("appeal_code__country__name"),
                    region_id=F("appeal_code__region__id"),
                    region_name=F("appeal_code__region__label"),
                    appeal_name=F("appeal_code__name"),
                    appeal_year=F("appeal_code__start_date"),
                    dtype_name=F("appeal_code__dtype__name"),
                )
            )
            
            from per.drf_views import OpsLearningFilter
            ops_learning_filtered_qs = OpsLearningFilter(filter_data, queryset=ops_learning_qs).qs
            
            if not ops_learning_filtered_qs.exists():
                logger.info("No OPS learnings found for the given filter.")
                return pd.DataFrame(columns=[
                    "id", "excerpts_id", "component", "sector", "learning",
                    "country_id", "country_name", "region_id", "region_name",
                    "appeal_name", "appeal_year", "dtype_name",
                ])
            
            ops_learning_df = pd.DataFrame.from_records(
                ops_learning_filtered_qs.values(
                    "id", "excerpts_id", "component_title", "sector_title", "learning_validated",
                    "country_id", "country_name", "region_id", "region_name",
                    "appeal_name", "appeal_year", "dtype_name",
                )
            )
            
            ops_learning_df = ops_learning_df.rename(
                columns={"component_title": "component", "sector_title": "sector", "learning_validated": "learning"}
            )
            ops_learning_df.set_index("id", inplace=True)
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("fetch_ops_learnings", start_time, end_time)
            
            return ops_learning_df
            
        except Exception as e:
            logger.error(f"Error fetching ops learnings: {e}")
            return pd.DataFrame()

    @classmethod
    def slice_dataframe(cls, df, limit=2000, encoding_name="cl100k_base"):
        """Slice dataframe based on token count with error handling"""
        try:
            df.loc[:, "count_temp"] = [cls.count_tokens(str(x), encoding_name) for x in df["learning"]]
            df.loc[:, "cumsum"] = df["count_temp"].cumsum()

            slice_index = None
            for i in range(1, len(df)):
                if df["cumsum"].iloc[i - 1] <= limit and df["cumsum"].iloc[i] > limit:
                    slice_index = i - 1
                    break

            if slice_index is not None:
                df_sliced = df.iloc[: slice_index + 1]
            else:
                df_sliced = df
                
            return df_sliced
            
        except Exception as e:
            logger.error(f"Error slicing dataframe: {e}")
            return df

    def generate_summary(self, prompt, type: OpsLearningPromptResponseCache.PromptType) -> dict:
        """Generates summaries using the provided system message and prompt with enhanced error handling."""
        start_time = datetime.now()

        def _validate_length_prompt(messages, prompt_length_limit, type):
            """Validates the length of the prompt."""
            try:
                message_content = [msg["content"] for msg in messages]
                text = " ".join(message_content)
                count = self.count_tokens(text, self.ENCODING_NAME)
                return count <= prompt_length_limit
            except Exception as e:
                logger.error(f"Error validating prompt length: {e}")
                return False

        def _summarize(prompt, type: OpsLearningPromptResponseCache.PromptType, system_message="You are a helpful assistant"):
            """Summarizes the prompt using the provided system message."""
            try:
                messages = [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                    {
                        "role": "assistant",
                        "content": "Understood, thank you for providing the data, and formatting requests. "
                        + "I am ready to proceed with the task.",
                    },
                ]

                if not _validate_length_prompt(messages, self.PROMPT_LENGTH_LIMIT, type):
                    logger.warning("The length of the prompt might be too long.")
                    return "{}"

                # Using Enhanced Azure OpenAI to summarize the prompt
                response = self.get_azure_response(messages, cache_prefix=f"ops_summary_{type.name}")
                return response or "{}"
                
            except Exception as e:
                logger.error(f"Error in summarization: {e}")
                return "{}"

        def _validate_format(summary, MAX_RETRIES=3):
            """Validates the format of the summary and modifies it if necessary."""
            
            def _validate_text_is_dictionary(text) -> bool:
                """Try to parse the text as a dictionary and check if it is a valid dictionary"""
                try:
                    formatted_text = ast.literal_eval(text)
                    return isinstance(formatted_text, dict)
                except (SyntaxError, ValueError):
                    return False

            def _modify_format(summary) -> str:
                try:
                    formatted_summary = summary
                    # If the content is wrapped in ```json and clean it up
                    if formatted_summary.startswith("```json") and formatted_summary.endswith("```"):
                        formatted_summary = formatted_summary.strip("```json").strip().strip("```")

                    # Find the index of the last closing brace before the "Note"
                    end_index = formatted_summary.rfind("}")
                    formatted_summary = formatted_summary[: end_index + 1]

                    logger.info("Modification realized to response")
                    return formatted_summary

                except Exception:
                    return "{}"

            try:
                formatted_summary = {}
                retries = 0

                # Attempt to parse the summary as a dictionary
                if _validate_text_is_dictionary(summary):
                    formatted_summary = ast.literal_eval(summary)
                else:
                    formatted_summary = _modify_format(summary)
                    formatted_summary = ast.literal_eval(formatted_summary)

                # Checking if the generated summary is empty
                if bool(formatted_summary):
                    return formatted_summary

                # NOTE: Generating the summary if summary is empty
                while retries < MAX_RETRIES:
                    self.generate_summary(prompt, type)
                    retries += 1
                    logger.info(f"Retrying.... Attempt {retries}/{MAX_RETRIES}")
                
                return formatted_summary
                
            except Exception as e:
                logger.error(f"Error validating format: {e}")
                return {}

        def _modify_summary(summary: dict) -> dict:
            """Cleans up the summary and adds fallback for missing context."""
            try:
                for key, value in summary.items():
                    if key == "contradictory reports":
                        continue

                    content = value.get("content", "")
                    excerpt_ids = value.get("excerpts id", "")
                    excerpt_id_list = (
                        list(set(excerpt_ids))
                        if isinstance(excerpt_ids, list)
                        else list(set(int(id.strip()) for id in excerpt_ids.split(",") if excerpt_ids and excerpt_ids != ""))
                    )

                    # Check if any excerpt id is in the content
                    if any(re.search(rf"\b{id}\b", content) for id in excerpt_id_list):
                        return self.generate_summary(prompt, type)

                    # Add fallback if no location/disaster info detected
                    if "based on" not in (content or "").lower():
                        fallback_context = "based on responses from unspecified locations or disaster types."
                        if content and content.endswith("."):
                            content = content + " " + fallback_context
                        else:
                            content = (content or "") + ". " + fallback_context

                    value["content"] = (content or "").strip()
                    value["excerpts id"] = excerpt_id_list

                    # Optional: extract and move confidence level
                    if "confidence level" not in value and "confidence level" in (content or "").lower():
                        parts = re.split(r"(?i)\bconfidence level\b", content, maxsplit=1)
                        value["content"] = parts[0].strip() + "."
                        value["confidence level"] = parts[1].strip()

                return summary
                
            except Exception as e:
                logger.error(f"Error modifying summary: {e}")
                return summary

        try:
            summary = _summarize(prompt, type, self.system_message)
            formatted_summary = _validate_format(summary)
            processed_summary = _modify_summary(formatted_summary)
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time(f"generate_summary_{type.name}", start_time, end_time)
            
            return processed_summary
            
        except Exception as e:
            logger.error(f"Error in generate_summary: {e}")
            return {}

    @classmethod
    def _generate_regional_prioritization_list(cls, df: pd.DataFrame):
        """Generates a list of regional prioritizations from the given data."""
        try:
            df_exploded = df.explode("components")
            regional_df = df_exploded.groupby(["region", "components"]).size().reset_index(name="count")
            regional_df = regional_df[regional_df["count"] > 2]
            regional_list = regional_df.groupby("region")["components"].apply(list).reset_index()
            return regional_list
        except Exception as e:
            logger.error(f"Error generating regional prioritization list: {e}")
            return pd.DataFrame()

    @classmethod
    def _generate_global_prioritization_list(cls, regional_df: pd.DataFrame):
        """Generates a global prioritization list from regional data."""
        try:
            global_df = regional_df.explode("components").groupby("components").size().reset_index(name="count")
            global_components = global_df[global_df["count"] > 2]["components"].tolist()
            global_list = {"global": global_components}
            return global_list
        except Exception as e:
            logger.error(f"Error generating global prioritization list: {e}")
            return {"global": []}

    @classmethod
    def _generate_country_prioritization_list(
        cls, regional_df: pd.DataFrame, global_components: list, prioritization_df: pd.DataFrame, country_df: pd.DataFrame
    ):
        """Generates a country-level prioritization list."""
        try:
            regional_dict = dict(zip(regional_df["region"], regional_df["components"]))
            merged_df = country_df.merge(prioritization_df, on=["country", "region"], how="left")
            no_prioritization_df = merged_df[merged_df["components"].isna()].astype(object)

            for index, row in no_prioritization_df.iterrows():
                region_id = row["region"]
                components = regional_dict.get(region_id, global_components["global"])
                no_prioritization_df.at[index, "components"] = components

            final_df = pd.concat([merged_df.dropna(subset=["components"]), no_prioritization_df])
            final_df["components"] = final_df["components"].apply(lambda x: int(x) if isinstance(x, float) else x)
            final_df = final_df[["country", "components"]]
            return final_df
        except Exception as e:
            logger.error(f"Error generating country prioritization list: {e}")
            return pd.DataFrame()

    @classmethod
    def generate_priotization_list(cls):
        """Generates prioritization list with enhanced error handling."""
        start_time = datetime.now()
        logger.info("Generating prioritization list.")
        
        try:
            exclusion_list = [
                "IFRC Africa", "IFRC Americas", "IFRC Asia-Pacific", "IFRC Europe",
                "IFRC Geneva", "IFRC MENA", "Benelux ERU", "ICRC",
            ]

            # Get all countries
            country_qs = (
                Country.objects.filter(is_deprecated=False, society_name__isnull=False, region__isnull=False)
                .exclude(name__in=exclusion_list)
                .values("id", "region_id")
            )
            country_df = pd.DataFrame(list(country_qs))
            country_df = country_df.rename(columns={"id": "country", "region_id": "region"})

            # Get all PER Overview
            per_overview_qs = Overview.objects.select_related("country").values(
                "id", "country_id", "country__region", "assessment_number",
            )
            per_overview_df = pd.DataFrame(list(per_overview_qs))
            per_overview_df = per_overview_df.rename(columns={"id": "overview", "country_id": "country", "country__region": "region"})

            # Get all PER Prioritization
            per_priotization_qs = (
                FormPrioritization.objects.filter(
                    is_draft=False,
                    prioritized_action_responses__isnull=False,
                )
                .annotate(
                    components=F("prioritized_action_responses__component"),
                )
                .values(
                    "overview",
                    "components",
                )
            )
            per_priotization_df = pd.DataFrame(list(per_priotization_qs))
            per_priotization_df = per_priotization_df.merge(
                per_overview_df[["overview", "country", "region", "assessment_number"]], on="overview", how="left"
            )
            per_priotization_df = per_priotization_df.sort_values("assessment_number").drop_duplicates(subset="country", keep="last")
            per_priotization_df = per_priotization_df[["region", "country", "components"]]

            # Generate the prioritization list that are in dataframes
            regional_list = cls._generate_regional_prioritization_list(per_priotization_df)
            global_list = cls._generate_global_prioritization_list(regional_list)
            country_list = cls._generate_country_prioritization_list(regional_list, global_list, per_priotization_df, country_df)
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("generate_prioritization_list", start_time, end_time)
            
            logger.info("Prioritization list generated.")
            return regional_list, global_list, country_list
            
        except Exception as e:
            logger.error(f"Error generating prioritization list: {e}")
            return pd.DataFrame(), {"global": []}, pd.DataFrame()

    @classmethod
    def prioritize(
        cls,
        df: pd.DataFrame,
        components_countries: dict,
        components_regions: dict,
        components_global: dict,
        type_prioritization: typing.Union[list, None],
    ):
        """Prioritizes components based on the type of prioritization."""
        try:
            def _add_new_component(prioritized_components, per_prioritized_components, df):
                """Adds new components to the prioritized list based on availability and frequency."""
                available_components = list(df["component"].unique())
                remaining_components = [item for item in available_components if item not in prioritized_components]

                intersect_components = list(set(per_prioritized_components) & set(remaining_components))

                if intersect_components:
                    mask = df["component"].isin(intersect_components)
                else:
                    mask = df["component"].isin(remaining_components)

                component_counts = df[mask]["component"].value_counts()
                most_frequent_components = component_counts[component_counts == component_counts.max()].index.tolist()

                return prioritized_components + most_frequent_components

            if type_prioritization == "single-country":
                country_id = str(df["country_id"].iloc[0])
                per_prioritized_components = components_countries.get(country_id, [])
            elif type_prioritization == "single-region":
                region_id = str(df["region_id"].iloc[0])
                per_prioritized_components = components_regions.get(region_id, [])
            else:
                per_prioritized_components = components_global.get("global", [])

            component_counts = df["component"].value_counts()
            most_frequent_components = component_counts[component_counts == component_counts.max()].index.tolist()

            while len(most_frequent_components) < 3:
                most_frequent_components = _add_new_component(most_frequent_components, per_prioritized_components, df)

            mask = df["component"].isin(most_frequent_components)
            return df[mask]
            
        except Exception as e:
            logger.error(f"Error in prioritization: {e}")
            return df

    @classmethod
    def prioritize_components(
        cls,
        ops_learning_df: pd.DataFrame,
        regional_list,
        global_list,
        country_list,
    ):
        """Prioritizes components with enhanced error handling."""
        start_time = datetime.now()
        logger.info("Prioritizing components.")

        try:
            def _need_component_prioritization(df, MIN_DIF_COMPONENTS, MIN_DIF_EXCERPTS):
                """Determines if prioritization is needed based on unique components and learnings."""
                nb_dif_components = len(df["component"].unique())
                nb_dif_learnings = len(df["learning"].unique())
                return nb_dif_components > MIN_DIF_COMPONENTS and nb_dif_learnings > MIN_DIF_EXCERPTS

            def _identify_type_prioritization(df):
                """Identifies the type of prioritization required based on the data."""
                if len(df["country_id"].unique()) == 1:
                    return "single-country"
                elif len(df["region_id"].unique()) == 1:
                    return "single-region"
                elif len(df["region_id"].unique()) > 1:
                    return "multi-region"
                return None

            def _contextualize_learnings(df):
                """Adds appeal year and event name as a contextualization of the learnings."""
                for index, row in df.iterrows():
                    df.at[index, "learning"] = (
                        f"{row['excerpts_id']}. In {row['appeal_year']} in {row['appeal_name']}: {row['learning']}"
                    )
                logger.info("Contextualization added to DataFrame.")
                return df

            components_countries = country_list.to_dict(orient="records")
            components_countries = {item["country"]: item["components"] for item in components_countries}

            components_regions = regional_list.to_dict(orient="records")
            components_regions = {item["region"]: item["components"] for item in components_regions}

            # Contextualize the learnings
            ops_learning_df = _contextualize_learnings(ops_learning_df)

            if _need_component_prioritization(ops_learning_df, cls.MIN_DIF_COMPONENTS, cls.MIN_DIF_EXCERPTS):
                type_prioritization = _identify_type_prioritization(ops_learning_df)
                prioritized_learnings = cls.prioritize(
                    ops_learning_df, components_countries, components_regions, global_list, type_prioritization
                )
            else:
                prioritized_learnings = ops_learning_df
                
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("prioritize_components", start_time, end_time)
            
            logger.info("Prioritization of components completed.")
            return prioritized_learnings
            
        except Exception as e:
            logger.error(f"Error prioritizing components: {e}")
            return ops_learning_df

    @classmethod
    def primary_prioritize_excerpts(cls, df: pd.DataFrame):
        """Prioritize the most recent excerpts within the token limit for primary insights."""
        start_time = datetime.now()
        logger.info("Prioritizing primary excerpts within token limit.")

        try:
            primary_learning_df = df.drop_duplicates(subset="learning")

            # Sort by 'appeal_name' and 'appeal_year' (descending for recency)
            primary_learning_df = primary_learning_df.sort_values(by=["appeal_name", "appeal_year"], ascending=[True, False])

            grouped = primary_learning_df.groupby("appeal_name")

            # Interleaved list of rows
            interleaved = list(chain(*zip_longest(*[group[1].itertuples(index=False) for group in grouped], fillvalue=None)))

            # Convert back to a DataFrame, removing any placeholder rows
            result = (
                pd.DataFrame(interleaved, columns=primary_learning_df.columns).dropna(subset=["appeal_name"]).reset_index(drop=True)
            )

            # Slice the Primary DataFrame
            sliced_primary_learning_df = cls.slice_dataframe(result, cls.PROMPT_DATA_LENGTH_LIMIT, cls.ENCODING_NAME)
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("primary_prioritize_excerpts", start_time, end_time)
            
            logger.info("Primary excerpts prioritized within token limit.")
            return sliced_primary_learning_df
            
        except Exception as e:
            logger.error(f"Error prioritizing primary excerpts: {e}")
            return df

    @classmethod
    def seconday_prioritize_excerpts(cls, df: pd.DataFrame):
        """Prioritize the most recent excerpts within the token limit for secondary insights."""
        start_time = datetime.now()
        logger.info("Prioritizing secondary excerpts within token limit.")

        try:
            # Dropping duplicates based on 'appeal_name' 'learning' and 'component' columns for secondary DataFrame
            secondary_learning_df = df.drop_duplicates(subset=["learning", "component", "sector"]).sort_values(
                by=["appeal_name", "component", "appeal_year"], ascending=[True, True, False]
            )
            grouped = secondary_learning_df.groupby(["component", "appeal_name"])

            # Create an interleaved list of rows
            interleaved = list(chain(*zip_longest(*[group[1].itertuples(index=False) for group in grouped], fillvalue=None)))

            # Convert the interleaved list of rows back to a DataFrame
            result = (
                pd.DataFrame(interleaved, columns=secondary_learning_df.columns).dropna(subset=["component"]).reset_index(drop=True)
            )

            # Slice secondary DataFrame
            sliced_secondary_learning_df = cls.slice_dataframe(result, cls.PROMPT_DATA_LENGTH_LIMIT, cls.ENCODING_NAME)
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("secondary_prioritize_excerpts", start_time, end_time)
            
            logger.info("Excerpts prioritized within token limit.")
            return sliced_secondary_learning_df
            
        except Exception as e:
            logger.error(f"Error prioritizing secondary excerpts: {e}")
            return df

    @classmethod
    def _build_intro_section(cls):
        """Builds the introductory section of the prompt."""
        return (
            "I will provide you with a set of instructions, data, and formatting requests in three sections."
            + " I will pass you the INSTRUCTIONS section, are you ready?"
            + "\n\n"
        )

    @classmethod
    def _build_instruction_section(cls, request_filter: dict, df: pd.DataFrame, instruction: str):
        """Builds the instruction section of the prompt based on the request filter and DataFrame."""
        try:
            instructions = ["INSTRUCTIONS\n========================\nSummarize essential insights from the DATA"]

            if "appeal_code__dtype__in" in request_filter:
                dtypes = df["dtype_name"].dropna().unique()
                dtype_str = '", "'.join(dtypes)
                instructions.append(f'concerning "{dtype_str}" occurrences')

            if "appeal_code__country__in" in request_filter:
                countries = df["country_name"].dropna().unique()
                country_str = '", "'.join(countries)
                instructions.append(f'in "{country_str}"')

            if "appeal_code__region" in request_filter:
                regions = df["region_name"].dropna().unique()
                region_str = '", "'.join(regions)
                instructions.append(f'in "{region_str}"')

            if "sector_validated__in" in request_filter:
                sectors = df["sector"].dropna().unique()
                sector_str = '", "'.join(sectors)
                instructions.append(f'focusing on "{sector_str}" aspects')

            if "per_component_validated__in" in request_filter:
                components = df["component"].dropna().unique()
                component_str = '", "'.join(components)
                instructions.append(f'and "{component_str}" aspects')

            instructions.append("in Emergency Response. ")
            instructions.append("\n\n" + instruction)
            instructions.append("\n\nI will pass you the DATA section, are you ready?\n\n")
            return "\n".join(instructions)
            
        except Exception as e:
            logger.error(f"Error building instruction section: {e}")
            return instruction

    @classmethod
    def format_primary_prompt(
        cls,
        ops_learning_summary_instance: OpsLearningCacheResponse,
        primary_learning_df: pd.DataFrame,
        filter_data: dict,
    ):
        """Formats the primary prompt based on request filter and prioritized learnings."""
        logger.info("Formatting primary prompt.")

        try:
            # Primary learnings intro section
            prompt_intro = cls._build_intro_section()
            primary_prompt_instruction = cls._build_instruction_section(
                filter_data, primary_learning_df, cls.primary_instruction_prompt
            )

            # Primary learnings section
            primary_learnings_data = "\n----------------\n".join(primary_learning_df["learning"].dropna())

            primary_learning_data = primary_learning_df["excerpts_id"].dropna().tolist()

            # Adding the used extracts in primary insights
            cls.add_used_ops_learnings(
                ops_learning_summary_instance,
                used_ops_learnings=primary_learning_data,
            )

            # format the prompts
            primary_learning_prompt = "".join([prompt_intro, primary_prompt_instruction, primary_learnings_data, cls.primary_prompt])
            logger.info("Primary Prompt formatted.")
            return primary_learning_prompt
            
        except Exception as e:
            logger.error(f"Error formatting primary prompt: {e}")
            return ""

    @classmethod
    def format_secondary_prompt(
        cls,
        secondary_learning_df: pd.DataFrame,
        filter_data: dict,
    ):
        """Formats the prompt based on request filter and prioritized learnings."""
        logger.info("Formatting secondary prompt.")

        try:
            def get_main_sectors(df: pd.DataFrame):
                """Get only information from technical sectorial information"""
                temp = df[df["component"] == "NS-specific areas of intervention"]
                available_sectors = list(temp["sector"].unique())
                nb_sectors = len(available_sectors)
                if nb_sectors == 0:
                    logger.info("There were not specific technical sectorial learnings")
                    return []
                logger.info("Main sectors for secondary summaries selected")
                return available_sectors

            def get_main_components(df: pd.DataFrame):
                temp = df[df["component"] != "NS-specific areas of intervention"]
                available_components = list(temp["component"].unique())
                nb_components = len(available_components)
                if nb_components == 0:
                    logger.info("There were not specific components")
                    return []
                logger.info("All components for secondary summaries selected")
                return available_components

            def process_learnings_sector(sector, df, max_length_per_section):
                df = df[df["sector"] == sector].dropna()
                df_sliced = cls.slice_dataframe(df, max_length_per_section, cls.ENCODING_NAME)

                if df_sliced["learning"].empty:
                    return ""

                learnings_sector = (
                    "\n----------------\n"
                    + "SUBTYPE: "
                    + sector
                    + "\n----------------\n"
                    + "\n----------------\n".join(df_sliced["learning"])
                    + "\n\n"
                )
                return learnings_sector

            def process_learnings_component(component, df, max_length_per_section):
                df = df[df["component"] == component].dropna()
                df_sliced = cls.slice_dataframe(df, max_length_per_section, cls.ENCODING_NAME)

                if df_sliced["learning"].empty:
                    return ""

                learnings_component = (
                    "\n----------------\n"
                    + "SUBTYPE: "
                    + component
                    + "\n----------------\n"
                    + "\n----------------\n".join(df_sliced["learning"])
                    + "\n\n"
                )
                return learnings_component

            def _build_component_data_section(secondary_df: pd.DataFrame) -> typing.Union[str, None]:
                # Component learnings section
                components = get_main_components(secondary_df)
                max_length_per_section = cls.PROMPT_DATA_LENGTH_LIMIT

                if len(components) > 0:
                    max_length_per_section = cls.PROMPT_DATA_LENGTH_LIMIT / len(components)
                else:
                    logger.info("No main components found. Skipping...")
                    return None

                learnings_components = (
                    "\n----------------\n\n"
                    + "TYPE: COMPONENT"
                    + "\n----------------\n".join(
                        [process_learnings_component(x, secondary_df, max_length_per_section) for x in components if pd.notna(x)]
                    )
                )
                secondary_learnings_data = learnings_components
                return secondary_learnings_data

            def _build_sector_data_section(secondary_df: pd.DataFrame) -> typing.Union[str, None]:
                # Sector learnings section
                sectors = get_main_sectors(secondary_df)
                max_length_per_section = cls.PROMPT_DATA_LENGTH_LIMIT

                if len(sectors) > 0:
                    max_length_per_section = cls.PROMPT_DATA_LENGTH_LIMIT / len(sectors)
                else:
                    logger.info("No main sectors found. Skipping...")
                    return None

                learnings_sectors = (
                    "\n----------------\n\n"
                    + "TYPE: SECTORS"
                    + "\n----------------\n".join(
                        [process_learnings_sector(x, secondary_df, max_length_per_section) for x in sectors if pd.notna(x)]
                    )
                )
                secondary_learnings_data = learnings_sectors
                return secondary_learnings_data

            # Prompt intro section
            prompt_intro = cls._build_intro_section()

            # Sector Prompt and Data
            sector_prompt_instruction = cls._build_instruction_section(
                filter_data, secondary_learning_df, cls.secondary_instruction_prompt
            )
            sector_learning_data = _build_sector_data_section(secondary_learning_df)

            # Components Prompt and Data
            component_prompt_instruction = cls._build_instruction_section(
                filter_data, secondary_learning_df, cls.secondary_instruction_prompt
            )
            component_learning_data = _build_component_data_section(secondary_learning_df)

            # format the prompts
            sector_learning_prompt = None
            component_learning_prompt = None

            if sector_learning_data:
                sector_learning_prompt = "".join([prompt_intro, sector_prompt_instruction, sector_learning_data, cls.sector_prompt])

            if component_learning_data:
                component_learning_prompt = "".join(
                    [prompt_intro, component_prompt_instruction, component_learning_data, cls.component_prompt]
                )

            logger.info("Secondary Prompt formatted.")
            return sector_learning_prompt, component_learning_prompt
            
        except Exception as e:
            logger.error(f"Error formatting secondary prompt: {e}")
            return None, None

    @classmethod
    def _get_or_create_summary(
        cls, prompt: str, type: OpsLearningPromptResponseCache.PromptType, overwrite_prompt_cache: bool = False
    ) -> dict:
        """Retrieves or Generates the summary based on the provided prompt."""
        try:
            prompt_hash = OpslearningSummaryCacheHelper.generate_hash(prompt)
            instance, created = OpsLearningPromptResponseCache.objects.update_or_create(
                prompt_hash=prompt_hash,
                type=type,
                defaults={"prompt": prompt},
            )
            
            if overwrite_prompt_cache or created or bool(instance.response) is False:
                summary = cls.generate_summary(prompt, type)
                instance.response = summary
                instance.save(update_fields=["response"])
                return summary
            return instance.response
            
        except Exception as e:
            logger.error(f"Error in get_or_create_summary: {e}")
            return {}

    @classmethod
    def primary_response_save_to_db(
        cls,
        ops_learning_summary_instance: OpsLearningCacheResponse,
        primary_summary: dict,
    ):
        """Saves the primary response to the database."""
        logger.info("Saving primary response to the database.")

        try:
            # Mapping between summary keys and model fields
            fields_mapping = {
                "0": {
                    "title": OpsLearningCacheResponse.insights1_title,
                    "content": OpsLearningCacheResponse.insights1_content,
                    "confidence level": OpsLearningCacheResponse.insights1_confidence_level,
                },
                "1": {
                    "title": OpsLearningCacheResponse.insights2_title,
                    "content": OpsLearningCacheResponse.insights2_content,
                    "confidence level": OpsLearningCacheResponse.insights2_confidence_level,
                },
                "2": {
                    "title": OpsLearningCacheResponse.insights3_title,
                    "content": OpsLearningCacheResponse.insights3_content,
                    "confidence level": OpsLearningCacheResponse.insights3_confidence_level,
                },
                "contradictory reports": OpsLearningCacheResponse.contradictory_reports,
            }
            for summary_key, model_fields in fields_mapping.items():
                if summary_key in primary_summary:
                    summary_data = primary_summary[summary_key]

                    if isinstance(model_fields, dict):
                        for summary_field, model_field in model_fields.items():
                            if summary_field in summary_data:
                                setattr(ops_learning_summary_instance, model_field.field.name, summary_data[summary_field].strip())
                    else:
                        setattr(ops_learning_summary_instance, model_fields.field.name, primary_summary[summary_key])
            ops_learning_summary_instance.save()

            logger.info("Primary response saved to the database.")
            
        except Exception as e:
            logger.error(f"Error saving primary response: {e}")

    @classmethod
    def secondary_response_save_to_db(
        cls,
        ops_learning_summary_instance: OpsLearningCacheResponse,
        secondary_summary: dict,
    ):
        """Saves secondary response to database with error handling."""
        logger.info("Saving secondary response to the database.")
        
        try:
            # Secondary summary
            for _, value in secondary_summary.items():
                type = value["type"].strip()
                subtype = value["subtype"].strip()
                content = value["content"].strip()
                excerpt_id_list = value["excerpts id"]

                if type == "component" and len(excerpt_id_list) > 0:
                    cls.add_used_ops_learnings_component(
                        instance=ops_learning_summary_instance,
                        content=content,
                        used_ops_learnings=excerpt_id_list,
                        component=subtype,
                    )

                if type == "sector" and len(excerpt_id_list) > 0:
                    cls.add_used_ops_learnings_sector(
                        instance=ops_learning_summary_instance,
                        content=content,
                        used_ops_learnings=excerpt_id_list,
                        sector=subtype,
                    )
            logger.info("Secondary response saved to the database.")
            
        except Exception as e:
            logger.error(f"Error saving secondary response: {e}")

    @classmethod
    def get_or_create_primary_summary(
        cls,
        ops_learning_summary_instance: OpsLearningCacheResponse,
        primary_learning_prompt: str,
        overwrite_prompt_cache: bool = False,
    ):
        """Retrieves or Generates the primary summary based on the provided prompt."""
        logger.info("Retrieving or generating primary summary.")

        try:
            # Checking the response for primary prompt
            primary_summary = cls._get_or_create_summary(
                prompt=primary_learning_prompt,
                type=OpsLearningPromptResponseCache.PromptType.PRIMARY,
                overwrite_prompt_cache=overwrite_prompt_cache,
            )

            # Saving into the database
            cls.primary_response_save_to_db(
                ops_learning_summary_instance=ops_learning_summary_instance,
                primary_summary=primary_summary,
            )

            # Translating the primary summary
            transaction.on_commit(
                lambda: translate_model_fields.delay(
                    get_model_name(type(ops_learning_summary_instance)),
                    ops_learning_summary_instance.pk,
                )
            )
            
        except Exception as e:
            logger.error(f"Error in get_or_create_primary_summary: {e}")

    @classmethod
    def get_or_create_secondary_summary(
        cls,
        ops_learning_summary_instance: OpsLearningCacheResponse,
        sector_learning_prompt: typing.Union[str, None],
        component_learning_prompt: typing.Union[str, None],
        overwrite_prompt_cache: bool = False,
    ):
        """Retrieves or Generates the summary based on the provided prompts."""
        logger.info("Retrieving or generating secondary summary.")

        try:
            if overwrite_prompt_cache:
                logger.info("Clearing the cache for secondary summary.")
                # NOTE: find a better way to update the cache
                OpsLearningComponentCacheResponse.objects.filter(filter_response=ops_learning_summary_instance).delete()
                OpsLearningSectorCacheResponse.objects.filter(filter_response=ops_learning_summary_instance).delete()

            # Checking the response for sector prompt
            if sector_learning_prompt:
                sector_summary = cls._get_or_create_summary(
                    prompt=sector_learning_prompt,
                    type=OpsLearningPromptResponseCache.PromptType.SECTOR,
                    overwrite_prompt_cache=overwrite_prompt_cache,
                )
                cls.secondary_response_save_to_db(
                    ops_learning_summary_instance=ops_learning_summary_instance,
                    secondary_summary=sector_summary,
                )

            if component_learning_prompt:
                # Checking the response for component prompt
                component_summary = cls._get_or_create_summary(
                    prompt=component_learning_prompt,
                    type=OpsLearningPromptResponseCache.PromptType.COMPONENT,
                    overwrite_prompt_cache=overwrite_prompt_cache,
                )
                cls.secondary_response_save_to_db(
                    ops_learning_summary_instance=ops_learning_summary_instance,
                    secondary_summary=component_summary,
                )
                
        except Exception as e:
            logger.error(f"Error in get_or_create_secondary_summary: {e}")

    @classmethod
    def generate_previous_crises_insights(cls, learning_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Generate AI-powered insights from operational learning data for Previous Crises endpoint.
        Moved from azure_service.py to centralize all Azure OpenAI logic in the correct class.
        """
        logger.info(f"Generating previous crises insights from {len(learning_data)} learning items")
        start_time = datetime.now()
        
        try:
            if not learning_data:
                return []
            
            # Create prompt from learning data (limit to top 6)
            learning_texts = []
            for learning in learning_data[:6]:
                text = learning.get('learning_text', '')
                if text:
                    learning_texts.append(text)
            
            if not learning_texts:
                logger.warning("No learning texts found in data")
                return []
            
            combined_learning = "\n\n".join([f"{i+1}. {text}" for i, text in enumerate(learning_texts)])
            
            prompt = f"""
Analyze the following operational learning insights and create structured recommendations for humanitarian operations:

{combined_learning}

Please provide 3-5 key actionable insights that can help future humanitarian operations. 
For each insight, include:
1. A clear title (5-8 words)
2. A detailed explanation (2-3 sentences)
3. Source reference to the original learning

Format your response as clear, actionable recommendations for humanitarian responders.
Structure each insight as a numbered item with title and detailed content.
"""
            
            logger.info(f"Generated previous crises prompt: {len(prompt)} characters")
            
            # Generate using existing Azure OpenAI infrastructure
            response = cls.generate_summary(prompt, OpsLearningPromptResponseCache.PromptType.PREVIOUS_CRISES)
            
            if not response or 'content' not in response:
                logger.warning("No valid response from Azure OpenAI for previous crises insights")
                return []
            
            content = response['content']
            
            # Parse response into structured format
            insights = cls._parse_previous_crises_response(content, learning_data)
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("generate_previous_crises_insights", start_time, end_time)
            
            return insights
            
        except Exception as e:
            logger.error(f"Error generating previous crises insights: {e}", exc_info=True)
            return []
    
    @classmethod
    def _parse_previous_crises_response(cls, content: str, learning_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse AI response for previous crises insights into structured format"""
        try:
            insights = []
            sections = content.split('\n\n')
            
            for i, section in enumerate(sections[:5]):  # Max 5 insights
                if section.strip():
                    # Extract title (first line) and content (rest)
                    lines = section.strip().split('\n')
                    title = lines[0].strip().lstrip('1234567890. ')
                    content_text = ' '.join(lines[1:]) if len(lines) > 1 else section
                    
                    insights.append({
                        'id': i + 1,
                        'title': title,
                        'content': content_text.strip(),
                        'confidence_level': 'medium',
                        'source_learnings': learning_data[i:i+2] if i < len(learning_data) else []
                    })
            
            return insights
            
        except Exception as e:
            logger.error(f"Error parsing previous crises response: {e}")
            return []


class DrefSummaryTask(BaseAITask):
    """
    DREF-specific summary task (from ops_learning_summary3.py)
    Maintains original logic with enhanced error handling and caching
    """
    
    PROMPT_DATA_LENGTH_LIMIT = 8000
    PROMPT_LENGTH_LIMIT = 10000
    
    # System message for DREF summaries
    system_message = (
        "You are an IFRC expert analyst specializing in DREF operations. "
        "Analyze DREF data and provide clear, actionable insights for humanitarian response planning. "
        "Use professional, analytical writing accessible to IFRC staff and National Society personnel."
    )

    # Operational summary prompt (3 lines max)
    operational_summary_prompt = (
        "\nAnalyze the DREF data and create a 3-line summary:\n"
        "Line 1: Overall objective of the operation\n"
        "Line 2: Strategic rationale and approach\n"
        "Line 3: Key operational details (target population, timeline, budget)\n\n"
        "Requirements:\n"
        "- Plain text format (not JSON)\n"
        "- Exactly 3 lines, one complete sentence each\n"
        "- Include specific numbers and details\n"
        "- Focus on operation_objective and response_strategy fields\n\n"
        "Example:\n"
        "The operation aims to provide emergency assistance to 5,000 flood-affected people in Bangladesh through cash transfers and relief items.\n"
        "The strategy prioritizes rapid response through existing National Society networks and coordination with local authorities to ensure efficient delivery.\n"
        "The 4-month operation targets vulnerable households in 3 districts with a budget of CHF 250,000 focusing on immediate basic needs."
    )

    # Situational overview prompt for new endpoint
    situational_overview_prompt = (
        "Create a comprehensive 5-line situational overview paragraph based on the provided DREF operational update data.\n\n"
        "STRUCTURE REQUIREMENTS:\n"
        "- Lines 1-3: Focus on summarizing the event_description and event_scope from the latest operational update. These lines should provide context about the disaster situation, affected areas, and scale of impact.\n"
        "- Lines 4-5: Focus on summarizing the operation_objective and response_strategy. These lines should explain WHY the operation is needed and the strategic rationale behind the response approach.\n\n"
        "GUIDELINES:\n"
        "- Each line should be a complete, well-structured sentence\n"
        "- Maintain consistency and flow between all 5 lines\n"
        "- Use professional humanitarian language\n"
        "- Focus on factual information from the provided data\n"
        "- Ensure the paragraph provides a clear situational understanding for decision-makers\n"
        "- Lines 1-3 should paint the disaster picture, lines 4-5 should explain the response reasoning\n\n"
        "Return only the 5-line paragraph without additional formatting or explanations."
    )

    def generate_operational_summary(self, dref_data: Dict[str, Any]) -> Optional[str]:
        """Generate operational objective and strategy summary (3 lines max)"""
        start_time = datetime.now()
        
        # Check cache first
        cache_key = self.generate_cache_key(dref_data, "dref_operational")
        cached_result = self.get_cached_result(cache_key)
        if cached_result:
            return cached_result
        
        try:
            # Extract operational data
            operational_fields = [
                'operation_objective', 'response_strategy', 'title', 'total_targeted_population',
                'people_in_need', 'amount_requested', 'operation_timeframe', 'country_details',
                'disaster_type_details', 'event_date', 'end_date'
            ]
            
            extracted_data = {}
            for field in operational_fields:
                if field in dref_data and dref_data[field] is not None:
                    extracted_data[field] = dref_data[field]
            
            data_json = json.dumps(extracted_data, indent=2, ensure_ascii=False)
            
            # Create messages
            messages = [
                {"role": "system", "content": self.system_message},
                {"role": "user", "content": f"DREF Data to analyze:\n{data_json}\n\n{self.operational_summary_prompt}"},
                {"role": "assistant", "content": "I understand. I will analyze the DREF data and provide a structured summary according to your specifications."}
            ]
            
            # Token count validation
            message_content = [msg["content"] for msg in messages]
            text = " ".join(message_content)
            token_count = self.count_tokens(text, self.ENCODING_NAME)
            
            if token_count > self.PROMPT_LENGTH_LIMIT:
                logger.warning("Prompt too long for operational summary, truncating data")
                truncated_data = data_json[:self.PROMPT_DATA_LENGTH_LIMIT]
                messages[1]["content"] = f"DREF Data to analyze:\n{truncated_data}\n\n{self.operational_summary_prompt}"
            
            # Call OpenAI backend
            response = self.get_azure_response(messages, cache_prefix="dref_operational")
            
            if not response:
                logger.error("No response received for operational summary")
                return None
            
            # Process operational response - take only first 3 non-empty lines
            lines = response.strip().split('\n')
            filtered_lines = [line.strip() for line in lines if line.strip()][:3]
            result = '\n'.join(filtered_lines)
            
            # Cache the result
            self.set_cached_result(cache_key, result)
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("generate_operational_summary", start_time, end_time)
            
            return result
            
        except Exception as e:
            logger.error(f"Error generating operational summary: {e}", exc_info=True)
            return None

    def generate_situational_overview(self, latest_operational_update: Dict[str, Any]) -> Optional[str]:
        """Generate situational overview based on event data and operational objectives"""
        start_time = datetime.now()
        
        # Check cache first
        cache_key = self.generate_cache_key(latest_operational_update, "dref_situational")
        cached_result = self.get_cached_result(cache_key)
        if cached_result:
            return cached_result
        
        try:
            # Extract data for situational overview: event info + operational objectives
            situational_data = {
                'event_description': latest_operational_update.get('event_description', ''),
                'event_scope': latest_operational_update.get('event_scope', ''),
                'operation_objective': latest_operational_update.get('operation_objective', ''),
                'response_strategy': latest_operational_update.get('response_strategy', ''),
                'title': latest_operational_update.get('title', ''),
                'country_details': latest_operational_update.get('country_details', {}),
                'disaster_type_details': latest_operational_update.get('disaster_type_details', {}),
                'date_of_approval': latest_operational_update.get('date_of_approval', ''),
            }
            
            data_json = json.dumps(situational_data, indent=2, ensure_ascii=False, default=str)
            
            # Create messages
            messages = [
                {"role": "system", "content": self.system_message},
                {"role": "user", "content": f"DREF Data to analyze:\n{data_json}\n\n{self.situational_overview_prompt}"},
                {"role": "assistant", "content": "I understand. I will analyze the DREF operational data and create a comprehensive 5-line situational overview paragraph focusing on event situation and operational objectives."}
            ]
            
            # Token count validation
            message_content = [msg["content"] for msg in messages]
            text = " ".join(message_content)
            token_count = self.count_tokens(text, self.ENCODING_NAME)
            
            if token_count > self.PROMPT_LENGTH_LIMIT:
                logger.warning("Prompt too long for situational overview")
                return None
            
            # Generate summary using Azure OpenAI
            ai_response = self.get_azure_response(messages, cache_prefix="dref_situational")
            
            if ai_response and ai_response.strip():
                result = ai_response.strip()
                
                # Cache the result
                self.set_cached_result(cache_key, result)
                
                # Track performance
                end_time = datetime.now()
                PerformanceMonitor.track_execution_time("generate_situational_overview", start_time, end_time)
                
                return result
            else:
                logger.warning("Empty response from Azure OpenAI for situational overview")
                return None
                
        except Exception as e:
            logger.error(f"Error generating situational overview: {e}", exc_info=True)
            return None

    def generate_sector_summaries(self, dref_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate sector-based summaries from DREF data with enhanced error handling"""
        start_time = datetime.now()
        
        # Check cache first
        cache_key = self.generate_cache_key(dref_data, "dref_sectors")
        cached_result = self.get_cached_result(cache_key)
        if cached_result:
            logger.info("Using cached DREF sector summaries")
            return cached_result
        
        try:
            sectors = []
            
            # Get sector data organized by title
            sector_data = self.organize_data_by_sector(dref_data)
            
            for sector_title, sector_info in sector_data.items():
                try:
                    # Get title_display (use first available title_display from any item in this sector)
                    title_display = sector_title
                    for item_list in [sector_info.get('needs', []), sector_info.get('planned_interventions', [])]:
                        for item in item_list:
                            if hasattr(item, 'title_display'):
                                title_display = getattr(item, 'title_display', sector_title)
                                break
                            elif isinstance(item, dict) and 'title_display' in item:
                                title_display = item['title_display']
                                break
                        if title_display != sector_title:
                            break
                    
                    sector_summary = {
                        "title": sector_title,
                        "title_display": title_display,
                        "needs_summary": "",
                        "future_actions": []
                    }
                    
                    # Generate needs summary using LLM
                    if sector_info.get('needs'):
                        needs_summary = self.generate_needs_summary(sector_info['needs'])
                        if needs_summary:
                            sector_summary["needs_summary"] = needs_summary
                        else:
                            logger.warning(f"Failed to generate needs summary for {sector_title}")
                    
                    # Process planned interventions for future actions
                    if sector_info.get('planned_interventions'):
                        future_actions = self.process_planned_interventions(sector_info['planned_interventions'])
                        
                        # Generate intervention_summary for each future action if needs_summary exists
                        if sector_summary["needs_summary"]:
                            for action in future_actions:
                                intervention_summary = self.generate_intervention_summary(
                                    sector_summary["needs_summary"], 
                                    action
                                )
                                if intervention_summary:
                                    action["intervention_summary"] = intervention_summary
                        
                        sector_summary["future_actions"] = future_actions
                    
                    # Only include sectors that have meaningful content
                    has_needs_summary = bool(sector_summary["needs_summary"].strip())
                    has_future_actions = bool(sector_summary["future_actions"])
                    
                    if has_needs_summary or has_future_actions:
                        sectors.append(sector_summary)
                    else:
                        logger.info(f"Skipping empty sector '{sector_title}' - no meaningful content")
                    
                except Exception as e:
                    logger.error(f"Error processing sector {sector_title}: {e}")
                    continue
            
            # Cache the result
            self.set_cached_result(cache_key, sectors)
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("generate_sector_summaries", start_time, end_time)
            
            logger.info(f"Generated {len(sectors)} sector summaries")
            return sectors
            
        except Exception as e:
            logger.error(f"Error generating sector summaries: {e}", exc_info=True)
            return []

    def generate_dref_summaries(self, dref_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate operational and sector-based summaries with comprehensive error handling"""
        start_time = datetime.now()
        
        result = {
            "operational_summary": None,
            "sectors": [],
            "status": "pending",
            "errors": [],
            "performance_metrics": {}
        }
        
        try:
            # Generate operational summary
            operational_summary = self.generate_operational_summary(dref_data)
            if operational_summary:
                result["operational_summary"] = operational_summary
            else:
                result["errors"].append("Failed to generate operational summary")
                logger.error("Failed to generate operational summary")
            
            # Generate sector summaries
            sectors = self.generate_sector_summaries(dref_data)
            if sectors:
                result["sectors"] = sectors
            else:
                result["errors"].append("Failed to generate sector summaries")
                logger.error("Failed to generate sector summaries")
            
            # Determine overall status
            if result["operational_summary"] and result["sectors"]:
                result["status"] = "success"
            elif result["operational_summary"] or result["sectors"]:
                result["status"] = "partial_success"
            else:
                result["status"] = "failed"
            
            # Add performance metrics
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            result["performance_metrics"] = {
                "execution_time_seconds": execution_time,
                "timestamp": end_time.isoformat(),
                "cache_hits": len([key for key in ["operational_summary", "sectors"] if self.get_cached_result(self.generate_cache_key(dref_data, f"dref_{key}"))])
            }
            
            PerformanceMonitor.track_execution_time("generate_dref_summaries", start_time, end_time)
            
        except Exception as e:
            logger.error(f"Critical error in DREF summary generation: {e}", exc_info=True)
            result["status"] = "failed"
            result["errors"].append(f"Critical error: {str(e)}")

        return result

    def generate_planned_intervention_summary(self, dref_data: Dict[str, Any]) -> Optional[str]:
        """Generate planned intervention summary using LLM with enhanced error handling"""
        logger.info("Generating DREF planned intervention summary")
        
        # Check cache first
        cache_key = self.generate_cache_key(dref_data, "dref_planned_intervention")
        cached_result = self.get_cached_result(cache_key)
        if cached_result:
            logger.info("Using cached DREF planned intervention summary")
            return cached_result
        
        try:
            # Extract planned intervention data
            planned_interventions = dref_data.get('planned_interventions', [])
            if not planned_interventions:
                logger.info("No planned interventions found")
                return None
            
            # Format intervention data for AI processing
            intervention_data = {
                'planned_interventions': planned_interventions,
                'total_targeted_population': dref_data.get('total_targeted_population', 0),
                'amount_requested': dref_data.get('amount_requested', 0),
                'operation_timeframe': dref_data.get('operation_timeframe', ''),
                'country_details': dref_data.get('country_details', {}),
                'disaster_type_details': dref_data.get('disaster_type_details', {})
            }
            
            data_json = json.dumps(intervention_data, indent=2, ensure_ascii=False, default=str)
            
            # Create messages
            messages = [
                {"role": "system", "content": self.system_message},
                {"role": "user", "content": f"DREF Data to analyze:\n{data_json}\n\n{self.planned_intervention_summary_prompt}"},
                {"role": "assistant", "content": "I understand. I will analyze the DREF planned interventions and create a comprehensive summary according to your specifications."}
            ]
            
            # Token count validation
            message_content = [msg["content"] for msg in messages]
            text = " ".join(message_content)
            token_count = self.count_tokens(text, self.ENCODING_NAME)
            logger.info(f"DREF planned intervention token count: {token_count}")
            
            if token_count > self.PROMPT_LENGTH_LIMIT:
                logger.warning("Prompt too long for planned intervention summary")
                return None
            
            # Generate summary using Azure OpenAI
            ai_response = self.get_azure_response(messages, cache_prefix="dref_planned_intervention")
            
            if ai_response and ai_response.strip():
                result = ai_response.strip()
                
                # Cache the result
                self.set_cached_result(cache_key, result)
                
                logger.info("Successfully generated planned intervention summary")
                return result
            else:
                logger.warning("Empty response from Azure OpenAI for planned intervention summary")
                return None
                
        except Exception as e:
            logger.error(f"Error generating planned intervention summary: {e}", exc_info=True)
            return None

    def organize_data_by_sector(self, dref_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Organize DREF data by sector - prioritizing planned_interventions as sector definitions"""
        try:
            sector_data = {}
            
            # STEP 1: Process planned interventions FIRST to define sectors
            interventions = dref_data.get('planned_interventions', [])
            for i, intervention in enumerate(interventions):
                try:
                    # Handle both dict and dataclass objects
                    if hasattr(intervention, 'title'):
                        sector_title = getattr(intervention, 'title', 'unknown')
                    else:
                        sector_title = intervention.get('title', 'unknown')
                    if sector_title not in sector_data:
                        sector_data[sector_title] = {'needs': [], 'planned_interventions': []}
                    sector_data[sector_title]['planned_interventions'].append(intervention)
                except Exception as e:
                    logger.error(f"Error processing intervention {i}: {e}")
                    continue
            
            # STEP 2: Match needs_identified to sectors defined by planned_interventions
            needs = dref_data.get('needs_identified', [])
            
            for i, need in enumerate(needs):
                try:
                    # Handle both dict and dataclass objects
                    if hasattr(need, 'title'):
                        sector_title = getattr(need, 'title', 'unknown')
                    else:
                        sector_title = need.get('title', 'unknown')
                    
                    # Try exact match first
                    if sector_title in sector_data:
                        sector_data[sector_title]['needs'].append(need)
                    else:
                        # Try fuzzy matching for common mismatches
                        matched = False
                        for existing_sector in sector_data.keys():
                            if self._sectors_match(sector_title, existing_sector):
                                sector_data[existing_sector]['needs'].append(need)
                                matched = True
                                break
                        
                        if not matched:
                            logger.info(f"No matching sector found for need: {sector_title}")
                except Exception as e:
                    logger.error(f"Error processing need {i}: {e}")
                    continue
            
            return sector_data
            
        except Exception as e:
            logger.error(f"Error organizing data by sector: {e}")
            return {}

    def _sectors_match(self, need_title: str, intervention_title: str) -> bool:
        """Check if sector titles match with fuzzy logic for common mismatches"""
        try:
            # Common title mappings
            mappings = {
                'multi_purpose_cash_grants': 'multi_purpose_cash',
                'shelter_housing_and_settlements': 'shelter',
                'water_sanitation_and_hygiene': 'wash',
                'livelihoods_and_basic_needs': 'livelihoods',
                'protection_gender_and_inclusion': 'protection',
                'coordination_and_partnerships': 'coordination',
                'disaster_risk_reduction': 'drr',
                'migration_and_displacement': 'migration'
            }
            
            # Check direct mapping (need_title -> intervention_title)
            if need_title in mappings and mappings[need_title] == intervention_title:
                return True
            
            # Check reverse mapping (intervention_title -> need_title)
            if intervention_title in mappings and mappings[intervention_title] == need_title:
                return True
            
            # Special case: multi_purpose_cash_grants <-> multi_purpose_cash
            if (need_title == 'multi_purpose_cash_grants' and intervention_title == 'multi_purpose_cash') or \
               (need_title == 'multi_purpose_cash' and intervention_title == 'multi_purpose_cash_grants'):
                return True
            
            # Check if one contains the other (partial match)
            if need_title in intervention_title or intervention_title in need_title:
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"Error in sector matching: {e}")
            return False

    def generate_needs_summary(self, needs_data: List[Dict[str, Any]]) -> Optional[str]:
        """Generate needs summary using LLM with enhanced error handling"""
        if not needs_data:
            return None
        
        try:
            # Combine all needs descriptions
            combined_needs = "\n".join([
                getattr(need, 'description', '') if hasattr(need, 'description') else need.get('description', '')
                for need in needs_data
                if (getattr(need, 'description', '') if hasattr(need, 'description') else need.get('description', ''))
            ])
            
            if not combined_needs.strip():
                return None
            
            try:
                needs_summary_prompt = (
                    "\nAnalyze the needs identified data and create a concise summary:\n"
                    "Focus on key challenges, gaps, and priority needs for humanitarian response.\n\n"
                    "Requirements:\n"
                    "- Plain text format (not JSON)\n"
                    "- Maximum 2 sentences\n"
                    "- Include specific needs and vulnerabilities\n"
                    "- Focus on humanitarian gaps and operational requirements\n"
                    "- No extra spaces or line breaks\n\n"
                    "Example:\n"
                    "The affected population faces critical water and sanitation challenges with 15,000 people lacking access to safe drinking water. Emergency shelter needs are urgent as 3,000 families remain displaced in overcrowded temporary accommodations."
                )
                
                messages = [
                    {"role": "system", "content": self.system_message},
                    {"role": "user", "content": f"Needs data:\n{combined_needs}\n\n{needs_summary_prompt}"},
                    {"role": "assistant", "content": "I understand. I will analyze the needs data and provide a structured summary according to your specifications."}
                ]
                
                response = self.get_azure_response(messages, cache_prefix="dref_needs")
                # Clean response: strip whitespace and remove extra line breaks
                cleaned_response = ' '.join(response.strip().split()) if response else None
                return cleaned_response
                
            except Exception as llm_error:
                # Fallback: Create a simple summary from the needs descriptions
                logger.warning(f"LLM failed for needs summary, using fallback: {llm_error}")
                return self._create_fallback_needs_summary(combined_needs)
            
        except Exception as e:
            logger.error(f"Error generating needs summary: {e}")
            return None

    def _create_fallback_needs_summary(self, combined_needs: str) -> str:
        """Create a fallback summary when LLM is not available"""
        try:
            # Extract key phrases and create a simple summary
            sentences = combined_needs.split('.')
            key_sentences = []
            
            for sentence in sentences[:3]:  # Take first 3 sentences
                sentence = sentence.strip()
                if len(sentence) > 20:  # Only meaningful sentences
                    key_sentences.append(sentence)
            
            if key_sentences:
                return '. '.join(key_sentences[:2]) + '.'  # Max 2 sentences
            else:
                return "Critical needs have been identified requiring immediate humanitarian response."
        except Exception as e:
            logger.error(f"Error creating fallback needs summary: {e}")
            return "Critical needs have been identified requiring immediate humanitarian response."

    def generate_intervention_summary(self, needs_summary: str, future_action: Dict[str, Any]) -> Optional[str]:
        """Generate intervention summary for a single future action using LLM"""
        if not needs_summary or not future_action:
            return None
        
        try:
            # Get hidden description for internal use only
            description = future_action.get('_description', 'No description available')
            
            # Format future action data without showing description anywhere
            action_text = "Future Action:\n"
            action_text += f"- Budget: {future_action.get('budget', 0)}\n"
            action_text += f"- People Targeted: {future_action.get('people_targeted_total', 0)}\n"
            
            # Enhanced system message with description context but don't show description in user prompt
            enhanced_system_message = f"{self.system_message} The intervention involves: {description}"
            
            intervention_summary_prompt = (
                "\nExplain how this future action addresses the identified needs:\n"
                "Write a concise sentence focusing on the specific solution and measurable outcomes, not repeating the problem statement.\n\n"
                "Requirements:\n"
                "- Be direct and solution-focused\n"
                "- Plain text format (not JSON)\n"
                "- Single flowing sentence\n"
                "- Focus on what will be done and the impact, not what the problems are\n"
                "- Include target numbers and specific actions\n"
                "- Avoid repeating needs summary content\n"
                "- No extra spaces or line breaks\n\n"
                "Example:\n"
                "The intervention will establish 12 water distribution points and distribute 5,000 hygiene kits to provide safe water access to 15,000 people in temporary settlements."
            )
            
            prompt_content = f"Needs Summary:\n{needs_summary}\n\n{action_text}\n\n{intervention_summary_prompt}"
            
            messages = [
                {"role": "system", "content": enhanced_system_message},
                {"role": "user", "content": prompt_content},
                {"role": "assistant", "content": "I understand. I will analyze this intervention and provide a summary according to your specifications."}
            ]
            
            response = self.get_azure_response(messages, cache_prefix="dref_intervention")
            # Clean response: strip whitespace and remove extra line breaks
            cleaned_response = ' '.join(response.strip().split()) if response else None
            return cleaned_response
            
        except Exception as e:
            logger.error(f"Error generating intervention summary: {e}")
            return None

    @classmethod
    def process_planned_interventions(cls, interventions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process planned interventions into future actions format"""
        future_actions = []
        
        for i, intervention in enumerate(interventions):
            try:
                # Extract indicators
                indicators = []
                # Handle both dict and dataclass objects
                if hasattr(intervention, 'indicators'):
                    intervention_indicators = getattr(intervention, 'indicators', [])
                else:
                    intervention_indicators = intervention.get('indicators', [])
                
                for j, indicator in enumerate(intervention_indicators):
                    try:
                        # Handle both dict and dataclass objects for indicators
                        if hasattr(indicator, 'title'):
                            indicator_title = getattr(indicator, 'title', '')
                            indicator_target = getattr(indicator, 'target', 0)
                        else:
                            indicator_title = indicator.get('title', '')
                            indicator_target = indicator.get('target', 0)
                        
                        indicators.append({
                            "title": indicator_title,
                            "people_targeted": indicator_target
                        })
                    except Exception as e:
                        logger.error(f"Error processing indicator {j} for intervention {i}: {e}")
                        continue
                
                # Calculate total people targeted
                if hasattr(intervention, 'person_targeted'):
                    people_targeted_total = getattr(intervention, 'person_targeted', 0)
                    budget = getattr(intervention, 'budget', 0)
                    description = getattr(intervention, 'description', '')
                else:
                    people_targeted_total = intervention.get('person_targeted', 0)
                    budget = intervention.get('budget', 0)
                    description = intervention.get('description', '')
                
                future_action = {
                    "indicators": indicators,
                    "budget": budget,
                    "people_targeted_total": people_targeted_total,
                    "intervention_summary": "",
                    "_description": description
                }
                
                future_actions.append(future_action)
                
            except Exception as e:
                logger.error(f"Error processing planned intervention {i}: {e}")
                continue
        return future_actions


class RRCapacityTask(BaseAITask):
    """
    Rapid Response Capacity Assessment task using enhanced Azure OpenAI client.
    Handles RR-specific prompt logic and formatting for capacity question processing.
    """

    def process_capacity_question(
        self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]], ops_learning_data: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Optional[str]]:
        """Process a single RR capacity question and generate only response notes."""
        if ops_learning_data is None:
            ops_learning_data = []

        return {
            "Notes on Response Capacity with sources": self.generate_response_notes(
                question_data, event_data, ops_learning_data
            )
        }

    def generate_response_notes(
        self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]], ops_learning_data: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[str]:
        """Generate brief notes on response capacity based on question and appeal-driven event data."""
        if ops_learning_data is None:
            ops_learning_data = []

        area = question_data.get("Area") or ""
        critical_question = question_data.get("Critical Questions") or ""
        guiding_questions = question_data.get("Guiding/probing questions") or ""
        examples = question_data.get("Examples of recommended actions") or ""
        references = question_data.get("References") or ""

        # Improve references handling - check for different formats
        if isinstance(references, list):
            references = "; ".join(str(ref) for ref in references if ref)
        elif references:
            references = str(references)
        else:
            references = "No specific references provided"

        # Build rich context from events and ops-learning
        events_context = self._format_events_for_assessment(event_data or [])
        learning_context = self._format_ops_learning_for_assessment(ops_learning_data or [])

        # Check if we have real data - if not, return early
        if (not event_data or len(event_data) == 0) and (not ops_learning_data or len(ops_learning_data) == 0):
            return "Enough source is not available to answer this question"

        # Extract top facts for front-loading in system prompt
        top_facts = self._extract_key_facts(event_data, ops_learning_data)
        
        # Build consolidated system prompt with key facts and question-specific guidance
        system_prompt = self._build_system_prompt(critical_question, area, top_facts)

        messages = [
            {"role": "system", "content": system_prompt},
            # Few-shot example to anchor the format
            {"role": "assistant", "content": 
                "LEGAL FRAMEWORK: National Disaster Management Act 2019 establishes Red Cross auxiliary status with government coordination mandate (Reference: MDRBGD025 – Bangladesh Cyclone Response, 15 January 2024)\n"
                "OPERATIONAL CAPACITY: Field Report FR-2023-000045 documents 1,200 volunteers deployed across 8 districts with 25,000 beneficiaries reached (Reference: MDRBGD025 – Bangladesh Cyclone Response, 18 January 2024)\n"
                "COORDINATION GAPS: Ops-learning from MDRBGD024 identifies 5-day delay in government liaison compared to previous response cycle (Reference: MDRBGD024 – Flood Response Review, 10 November 2023)"
            },
            {
                "role": "user",
                "content": (
                    f"Now analyze this question: {critical_question or 'No critical question provided'}\n\n"
                    f"Assessment Area: {area or 'No area specified'}\n\n"
                    f"Events Context:\n{events_context or 'No events context available'}\n\n"
                    f"Operational Learning Context:\n{learning_context or 'No learning context available'}\n\n"
                    f"Generate 3-4 bullets using the exact format shown above. Each bullet must include specific facts from the provided sources."
                ),
            },
        ]

        response = self.get_azure_response(messages, cache_prefix="rr_capacity")

        if response:
            response = self._clean_markdown_formatting(response)
            # Validate that response only uses information from provided sources
            response = self._validate_response_sources(response, events_context, learning_context)

        return response

    def _extract_key_facts(self, event_data: List[Dict[str, Any]], ops_learning_data: List[Dict[str, Any]]) -> str:
        """Extract 2-3 most salient facts from events and ops-learning for front-loading."""
        facts = []
        
        # Extract top event facts
        for event in (event_data or [])[:2]:  # Top 2 events
            # Skip non-dictionary entries to avoid .get() errors
            if not isinstance(event, dict):
                continue
                
            name = event.get("name", "Unknown Event")
            appeals = event.get("appeals") or []
            if appeals:
                first_appeal = appeals[0]
                if isinstance(first_appeal, dict):
                    appeal_code = first_appeal.get("code", "")
                    start_date = first_appeal.get("start_date", "")
                else:
                    # Handle integer appeal IDs
                    appeal_code = str(first_appeal)
                    start_date = ""
                
                if appeal_code:
                    date_fmt = self._format_date_for_reference(start_date) if start_date else ""
                    severity = event.get("ifrc_severity_level_display", "")
                    num_affected = event.get("num_affected")
                    fact_parts = [f"Event: {name}", f"Code: {appeal_code}"]
                    if date_fmt:
                        fact_parts.append(f"Date: {date_fmt}")
                    if severity:
                        fact_parts.append(f"Severity: {severity}")
                    if num_affected:
                        fact_parts.append(f"Affected: {self._fmt_num(num_affected)}")
                    facts.append(" | ".join(fact_parts))
        
        # Extract top ops-learning facts
        for learning in (ops_learning_data or [])[:2]:  # Top 2 learning items
            # Skip non-dictionary entries to avoid .get() errors
            if not isinstance(learning, dict):
                continue
                
            learning_text = (
                learning.get("learning_validated_en") or 
                learning.get("learning_validated") or 
                learning.get("learning_en", "")
            )
            appeal_info = learning.get("appeal", {})
            if isinstance(appeal_info, dict):
                appeal_code = appeal_info.get("code", "")
            else:
                # Handle integer appeal IDs
                appeal_code = str(appeal_info) if appeal_info else ""
            
            if learning_text and appeal_code:
                short_learning = learning_text[:80] + "..." if len(learning_text) > 80 else learning_text
                facts.append(f"Learning: {short_learning} | Appeal: {appeal_code}")
        
        return "\n".join(facts) if facts else "No key facts available"

    def _validate_response_sources(self, response: str, events_context: str, learning_context: str) -> str:
        """Validate that response only uses information from provided sources."""
        if not response:
            return response
            
        import re
        
        # Check if response mentions countries/places not in sources
        response_lower = response.lower()
        
        # Extract country names from sources
        source_countries = set()
        if "philippines" in (events_context or "").lower() or "philippines" in (learning_context or "").lower():
            source_countries.add("philippines")
        if "djibouti" in (events_context or "").lower() or "djibouti" in (learning_context or "").lower():
            source_countries.add("djibouti")
            
        # Check for problematic patterns
        if "djibouti" in response_lower and "philippines" not in response_lower:
            if "djibouti" not in source_countries:
                return "Enough source is not available to answer this question"
                
        # Check for appeal codes that don't match sources
        appeal_codes_in_response = re.findall(r'MDR[A-Z]{3}\d+', response)
        appeal_codes_in_sources = re.findall(r'MDR[A-Z]{3}\d+', events_context + learning_context)
        
        for code in appeal_codes_in_response:
            if code not in appeal_codes_in_sources:
                return "Enough source is not available to answer this question"
                
        return response

    def _build_system_prompt(self, critical_question: str, area: str, top_facts: str) -> str:
        """Build a comprehensive system prompt with key facts and question-specific guidance."""
        question_lower = (critical_question or "").lower() if critical_question else ""
        area_lower = (area or "").lower() if area else ""

        # Base rules and requirements
        base_prompt = (
            f"You are an IFRC emergency response specialist conducting rapid response capacity assessment.\n\n"
            f"KEY FACTS FROM SOURCES:\n{top_facts or 'No key facts available'}\n\n"
            f"CRITICAL INSTRUCTION: You are ONLY allowed to use information that is EXPLICITLY provided in the sources above.\n"
            f"You are FORBIDDEN from using any information from your training data, general knowledge, or any other source.\n"
            f"If the sources do not contain enough information to answer the question, you MUST respond with:\n"
            f"'Enough source is not available to answer this question'\n\n"
            f"ABSOLUTE RULES:\n"
            f"- ONLY use information explicitly stated in the provided sources above\n"
            f"- NEVER create, invent, infer, assume, or generate ANY information not directly stated in sources\n"
            f"- NEVER use information from your training data or general knowledge\n"
            f"- If insufficient source data, respond: 'Enough source is not available to answer this question'\n"
            f"- Each bullet MUST include specific facts (numbers, dates, places, named units) from sources\n"
            f"- Format: UPPERCASE LABEL: analysis with specific facts (Reference: CODE – Event, Date)\n"
            f"- Generate 3-4 bullets with diverse analytical perspectives\n"
            f"- Plain text only, no markdown\n\n"
        )

        # Question-specific focus areas
        if "mandate" in question_lower or "officially recognised" in question_lower:
            specific_focus = (
                "FOCUS AREA: LEGAL MANDATE and OFFICIAL RECOGNITION\n"
                "Analyze legal frameworks, auxiliary status, formal agreements, legislative status, and recognition gaps.\n\n"
            )
        elif "policy" in question_lower or "strategic" in question_lower:
            specific_focus = (
                "FOCUS AREA: POLICY FRAMEWORKS and STRATEGIC DOCUMENTS\n"
                "Analyze policy development, strategic planning, documentation quality, and implementation gaps.\n\n"
            )
        elif "risk" in question_lower or "early warning" in question_lower:
            specific_focus = (
                "FOCUS AREA: RISK MANAGEMENT and EARLY WARNING SYSTEMS\n"
                "Analyze risk assessment capabilities, monitoring systems, warning mechanisms, and preparedness.\n\n"
            )
        elif "business continuity" in question_lower or "continuity plan" in question_lower:
            specific_focus = (
                "FOCUS AREA: BUSINESS CONTINUITY and OPERATIONAL RESILIENCE\n"
                "Analyze continuity planning, resilience measures, crisis management, and recovery procedures.\n\n"
            )
        elif "operations management" in question_lower or "coordination systems" in question_lower:
            specific_focus = (
                "FOCUS AREA: OPERATIONS MANAGEMENT and COORDINATION SYSTEMS\n"
                "Analyze management structures, coordination mechanisms, operational procedures, and system effectiveness.\n\n"
            )
        elif "information" in question_lower or "data" in question_lower:
            specific_focus = (
                "FOCUS AREA: INFORMATION MANAGEMENT and DATA SYSTEMS\n"
                "Analyze data collection, information sharing, integration, and reporting capabilities.\n\n"
            )
        elif "coordination" in question_lower and ("mechanisms" in question_lower or "relationships" in question_lower):
            specific_focus = (
                "FOCUS AREA: COORDINATION MECHANISMS and INTER-AGENCY RELATIONSHIPS\n"
                "Analyze structures, partnership frameworks, communication channels, and collaboration effectiveness.\n\n"
            )
        else:
            specific_focus = (
                "FOCUS AREA: CAPACITY ASSESSMENT\n"
                "Analyze the specific capacity referenced by the question with concrete, context-grounded insights.\n\n"
            )

        # Final requirements
        requirements = (
            "REQUIREMENTS:\n"
            "- Tie each bullet to specific field report/appeal IDs with exact figures from sources\n"
            "- Vary conclusions across bullets (strengths, contradictions, operational deltas)\n"
            "- Ensure appeal codes match country context from sources\n"
            "- Diversify analytical lenses: legal review, FR metrics, ops-learning, contacts' statements\n"
            "- Cross-check all facts against provided sources only"
        )

        return base_prompt + specific_focus + requirements

    def _clean_markdown_formatting(self, text: str) -> str:
        """Remove common markdown artifacts."""
        if not text:
            return text
        
        import re
        
        # Handle bolded label patterns
        text = re.sub(r"\*\*-\s*([^:]+):\*\*", r"- \1:", text)
        text = re.sub(r"-\s*\*\*([^:]+):\*\*", r"- \1:", text)
        text = re.sub(r"\*\*([^:]+):\*\*", r"\1:", text)
        # Remove remaining emphasis
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        text = re.sub(r"_([^_]+)_", r"\1", text)
        return text.replace("**", "")

    def _format_date_for_reference(self, date_string: str) -> str:
        """Format date string to 'DD Month YYYY' format for consistent references."""
        try:
            if "T" in date_string:
                date_obj = datetime.fromisoformat(date_string.replace("Z", "+00:00"))
            else:
                date_obj = datetime.strptime(date_string[:10], "%Y-%m-%d")
            return date_obj.strftime("%d %B %Y")
        except Exception:
            return date_string[:10] if date_string else ""

    def _strip_html(self, html: str) -> str:
        """Rudimentary HTML → text cleaner for summaries/descriptions from GO."""
        if not html:
            return ""
        
        import re
        from html import unescape
        
        text = re.sub(r"<\s*br\s*/?>", "\n", html, flags=re.I)
        text = re.sub(r"<\s*/p\s*>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = unescape(text)
        text = re.sub(r"\r?\n\s*\n+", "\n\n", text)
        return text.strip()

    def _fmt_num(self, v: Any) -> str:
        try:
            n = float(v)
            if n.is_integer():
                return f"{int(n):,}"
            return f"{n:,.2f}"
        except Exception:
            return str(v)

    def _coords_of(self, event: Dict[str, Any]) -> Optional[str]:
        lat = event.get("lat") or event.get("latitude")
        lon = event.get("lon") or event.get("lng") or event.get("longitude")
        if lat and lon:
            return f"{lat}, {lon}"
        if isinstance(event.get("centroid"), dict):
            c = event["centroid"]
            if "lat" in c and "lon" in c:
                return f"{c['lat']}, {c['lon']}"
        if isinstance(event.get("bbox"), (list, tuple)) and len(event["bbox"]) == 4:
            # minLon, minLat, maxLon, maxLat → show center
            minLon, minLat, maxLon, maxLat = event["bbox"]
            try:
                clat = (float(minLat) + float(maxLat)) / 2
                clon = (float(minLon) + float(maxLon)) / 2
                return f"{clat:.5f}, {clon:.5f}"
            except Exception:
                pass
        return None

    def _format_events_for_assessment(self, events: List[Dict[str, Any]]) -> str:
        """
        Rich event context for the assessment.
        Includes: full summary/description (HTML stripped), GLIDE, contacts, key figures,
        all field reports (capped), all appeals (capped), coordinates if available.
        """
        if not events:
            return "No event data available from sources."

        # === UPDATED: allow up to 10 events with concise formatting ===
        MAX_EVENTS = 10
        MAX_FR_PER_EVENT = 2  # Reduced for conciseness
        MAX_APPEALS_PER_EVENT = 2  # Reduced for conciseness
        MAX_EVENT_CHARS = 2000  # Reduced for better token efficiency

        formatted: List[str] = []

        for i, event in enumerate(events[:MAX_EVENTS], 1):
            parts: List[str] = []

            # Header / IDs
            name = event.get("name", "Unknown")
            dtype_obj = event.get("dtype")
            if isinstance(dtype_obj, dict):
                dtype = dtype_obj.get("name") or event.get("dtype_name") or "Unknown"
            else:
                # Handle integer disaster type ID
                dtype = event.get("dtype_name") or f"Disaster Type ID: {dtype_obj}" if dtype_obj else "Unknown"
            countries = event.get("countries") or []
            # Updated logic: handle dicts and ints
            if countries:
                country_names_list: List[str] = []
                for c in countries:
                    if isinstance(c, dict):
                        country_names_list.append(c.get("name", "Unknown"))
                    else:
                        # For integer IDs, represent them explicitly
                        country_names_list.append(f"Country ID: {c}")
                country_names = ", ".join(country_names_list)
            else:
                country_names = event.get("country_name", "Unknown")
            date_str = ""
            if event.get("disaster_start_date"):
                date_str = self._format_date_for_reference(event["disaster_start_date"])
            elif event.get("start_date"):
                date_str = self._format_date_for_reference(event["start_date"])
            glide = event.get("glide") or ""
            coords = self._coords_of(event)

            header = [f"Event {i}: {name}", f"Type: {dtype}", f"Location: {country_names}"]
            if date_str:
                header.append(f"Date: {date_str}")
            if glide:
                header.append(f"GLIDE: {glide}")
            if coords:
                header.append(f"Coordinates: {coords}")
            parts.append(" | ".join(header))

            # Severity / Figures
            sev = event.get("ifrc_severity_level_display")
            num_aff = event.get("num_affected")
            figbits = []
            if sev:
                figbits.append(f"Severity: {sev}")
            if num_aff is not None:
                figbits.append(f"Affected: {self._fmt_num(num_aff)}")
            if figbits:
                parts.append("Figures: " + " | ".join(figbits))

            # Narrative (full cleaned)
            full_summary = self._strip_html(event.get("summary", ""))
            full_desc = self._strip_html(event.get("description", ""))
            if full_summary:
                parts.append("Summary:\n" + full_summary)
            if full_desc and full_desc != full_summary:
                parts.append("Description:\n" + full_desc)

            # Appeals (capped)
            appeals = event.get("appeals") or []
            if appeals:
                a_lines = []
                for ap in appeals[:MAX_APPEALS_PER_EVENT]:
                    if isinstance(ap, dict):
                        code = ap.get("code") or ""
                        atype = ap.get("atype_display") or ""
                        amt_req = ap.get("amount_requested")
                        amt_fund = ap.get("amount_funded")
                        n_ben = ap.get("num_beneficiaries")
                        st = ap.get("status_display") or ""
                        sd = ap.get("start_date")
                        ed = ap.get("end_date")
                    else:
                        # Handle integer appeal IDs
                        code = str(ap)
                        atype = ""
                        amt_req = None
                        amt_fund = None
                        n_ben = None
                        st = ""
                        sd = ""
                        ed = ""
                    
                    sd_fmt = self._format_date_for_reference(sd) if sd else ""
                    ed_fmt = self._format_date_for_reference(ed) if ed else ""
                    line = []
                    if code:
                        line.append(f"{code}")
                    if atype:
                        line.append(f"Type: {atype}")
                    if st:
                        line.append(f"Status: {st}")
                    if sd_fmt or ed_fmt:
                        line.append(f"Dates: {sd_fmt} – {ed_fmt}".strip(" –"))
                    if amt_req is not None:
                        line.append(f"Requested: CHF {self._fmt_num(amt_req)}")
                    if amt_fund is not None:
                        line.append(f"Funded: CHF {self._fmt_num(amt_fund)}")
                    if n_ben is not None:
                        line.append(f"Beneficiaries: {self._fmt_num(n_ben)}")
                    a_lines.append(" ; ".join(line))
                if a_lines:
                    parts.append("Appeals:\n- " + "\n- ".join(a_lines))

            # Field Reports (capped) with contacts & figures
            frs = event.get("field_reports") or []
            if frs:
                fr_blocks = []
                for idx, fr in enumerate(frs[:MAX_FR_PER_EVENT], 1):
                    if isinstance(fr, dict):
                        fr_lines = [f"Field Report {idx} (id {fr.get('id', '')}):"]
                        fr_date = fr.get("report_date") or fr.get("created_at")
                        fr_date_fmt = self._format_date_for_reference(fr_date) if fr_date else ""
                        if fr_date_fmt:
                            fr_lines.append(f"  Date: {fr_date_fmt}")
                        # Key numerics
                        keys = [
                            ("num_dead", "Dead"),
                            ("num_injured", "Injured"),
                            ("num_missing", "Missing"),
                            ("num_affected", "Affected"),
                            ("num_displaced", "Displaced"),
                            ("num_assisted", "Assisted"),
                            ("num_localstaff", "Local Staff"),
                            ("num_volunteers", "Volunteers"),
                            ("num_expats_delegates", "International Delegates"),
                            ("gov_num_dead", "Gov Dead"),
                            ("gov_num_affected", "Gov Affected"),
                            ("other_num_affected", "Other Affected"),
                        ]
                        fig_entries = []
                        for k, label in keys:
                            val = fr.get(k)
                            if val not in (None, "", 0):
                                fig_entries.append(f"{label}: {self._fmt_num(val)}")
                        if fig_entries:
                            fr_lines.append("  Figures: " + " | ".join(fig_entries))

                        # Narrative
                        fr_sum = self._strip_html(fr.get("summary", ""))
                        fr_desc = self._strip_html(fr.get("description", ""))
                        if fr_sum:
                            fr_lines.append("  Summary: " + fr_sum)
                        if fr_desc and fr_desc != fr_sum:
                            fr_lines.append("  Description: " + fr_desc)

                        # Contacts
                        contacts = fr.get("contacts") or []
                        if contacts:
                            c_lines = []
                            for c in contacts:
                                if isinstance(c, dict):
                                    cname = c.get("name") or ""
                                    ctitle = c.get("title") or ""
                                    ctype = c.get("ctype") or ""
                                    cemail = c.get("email") or ""
                                    cphone = c.get("phone") or ""
                                    frag = ", ".join([p for p in [cname, ctitle, ctype] if p])
                                    if cemail:
                                        frag += f" | {cemail}"
                                    if cphone:
                                        frag += f" | {cphone}"
                                    if frag:
                                        c_lines.append(f"    - {frag}")
                                else:
                                    # Handle integer contact IDs
                                    c_lines.append(f"    - Contact ID: {c}")
                            if c_lines:
                                fr_lines.append("  Contacts:\n" + "\n".join(c_lines))

                        fr_blocks.append("\n".join(fr_lines))
                    else:
                        # Handle integer field report IDs
                        fr_lines = [f"Field Report {idx} (ID: {fr}):"]
                        fr_lines.append("  Note: Field report details not available")
                        fr_blocks.append("\n".join(fr_lines))
                if fr_blocks:
                    parts.append("Field Reports:\n" + "\n\n".join(fr_blocks))

            # Provenance
            appeal_src = event.get("appeal_source") or ""
            source_note = event.get("source_note") or ""
            prov = []
            if appeal_src:
                prov.append(f"Appeal Source: {appeal_src}")
            if source_note:
                prov.append(f"Source Note: {source_note}")
            if prov:
                parts.append("Provenance: " + " | ".join(prov))

            # Join and enforce per-event cap
            block = "\n".join(parts).strip()
            if len(block) > MAX_EVENT_CHARS:
                block = block[:MAX_EVENT_CHARS].rstrip() + " … [truncated]"
            formatted.append(block)

        return "\n\n".join(formatted)

    def _format_ops_learning_for_assessment(self, ops_learning_data: List[Dict[str, Any]]) -> str:
        """Format operational learning data for capacity assessment context with source labels."""
        if not ops_learning_data:
            return "No operational learning data available from sources."

        formatted_learning = []
        # === UPDATED: allow up to 10 ops-learning items for conciseness ===
        for i, learning_item in enumerate(ops_learning_data[:10], 1):
            # Skip non-dictionary entries to avoid .get() errors
            if not isinstance(learning_item, dict):
                continue
                
            learning_text = (
                learning_item.get("learning_validated_en")
                or learning_item.get("learning_validated")
                or learning_item.get("learning_en")
                or "Unknown learning"
            )

            learning_info = [f"Learning {i}: {learning_text[:100]}{'...' if len(learning_text) > 100 else ''}"]

            appeal_info = learning_item.get("appeal", {})
            if isinstance(appeal_info, dict):
                event_details = appeal_info.get("event_details", {})
                appeal_code = appeal_info.get("code")
                if appeal_code:
                    learning_info.append(f"Appeal Code: {appeal_code}")
                elif appeal_info.get("name"):
                    learning_info.append(f"Appeal: {appeal_info['name']}")

                if isinstance(event_details, dict) and event_details.get("name"):
                    learning_info.append(f"Event: {event_details['name']}")

                if appeal_info.get("start_date"):
                    formatted_date = self._format_date_for_reference(appeal_info["start_date"])
                    learning_info.append(f"Date: {formatted_date}")
            else:
                # Handle integer appeal IDs
                appeal_code = str(appeal_info) if appeal_info else ""
                if appeal_code:
                    learning_info.append(f"Appeal Code: {appeal_code}")

            if learning_item.get("document_name"):
                learning_info.append(f"Document: {learning_item['document_name']}")

            source_note = learning_item.get("source_note")
            if source_note:
                learning_info.append(f"Context: {source_note}")

            formatted_learning.append(" | ".join(learning_info))

        return "\n".join(formatted_learning) if formatted_learning else "No operational learning data available from sources."


class PreviousCrisesTask(BaseAITask):
    """Task for processing previous crises insights with operational learning data"""
    
    def __init__(self):
        from per.ucl_research.ifrc_client import IFRCAPIClient
        self.ifrc_client = IFRCAPIClient()
    
    async def process_previous_crises_insights(self, country_id: int, disaster_type_id: int) -> List[Dict[str, Any]]:
        """Process previous crises insights and generate AI summaries"""
        
        async with self.ifrc_client as client:
            # STEP 1: primary (country AND disaster) 
            primary = await client.get_ops_learning(country_id, disaster_type_id)
            primary_labeled = [
                {**l, "source_note": "This insight was built off similar disasters from the same country."}
                for l in primary
            ]

            # STEP 2: fallback (country only)
            if not primary:
                secondary = await client.get_ops_learning(country_id, None)
            else:
                all_country = await client.get_ops_learning(country_id, None)
                primary_ids = {p['id'] for p in primary}
                secondary = [l for l in all_country if l['id'] not in primary_ids]

        secondary_labeled = [
            {**l, "source_note": "This insight was built off other disasters from the same country."}
            for l in secondary
        ]

        # STEP 3: Combine both sets of learning and pad out to up to 6 items
        combined_learning = (primary_labeled + secondary_labeled)[:6]

        if not combined_learning:
            return []

        # STEP 4: Convert raw learning entries into the expected format
        processed_learnings = [self.create_learning_entry(l) for l in combined_learning]
        for pl in processed_learnings:
            pl["source_note"] = next(
                l["source_note"]
                for l in combined_learning
                if l["id"] == pl["id"]
            )

        # STEP 5: Call AI summary generator
        ai_summary = self.generate_ai_summary([{"related_ops_learning": processed_learnings}])
        
        return ai_summary
    
    def fetch_ops_learning(
        self,
        country_id: int,
        disaster_type_id: Optional[int],
        max_results: int = 6
    ) -> List[Dict[str, Any]]:
        """
        Fetch up to max_results validated learnings for (country + optional dtype),
        letting the server do the heavy lifting.
        """
        params = {
            "is_validated": "true",
            "limit": max_results,
            "appeal_code__country": country_id,
        }
        if disaster_type_id is not None:
            # actually filter by the nested event dtype field
            params["appeal__event_details__dtype"] = disaster_type_id

        resp = self.make_api_request(
            "https://goadmin.ifrc.org/api/v2/ops-learning/",
            params,
            "ops learning"
        ).get("results", [])

        return resp

    def make_api_request(self, url: str, params: Dict[str, Any], data_type: str) -> Dict[str, Any]:
        """Make HTTP request to external API with error handling."""
        import httpx
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
            
            results = response.json().get('results', [])
            
            return {'results': results}
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
            logger.error(f"Error in {data_type} request: {exc}")
            return {
                'error': True,
                'detail': f'Error fetching {data_type}: {exc}',
                'results': []
            }

    def create_learning_entry(self, learning: Dict[str, Any]) -> Dict[str, Any]:
        """Create learning entry dict."""
        return {
            'id': learning.get('id'),
            'learning_text': learning.get('learning_validated_en', learning.get('learning_en')),
            'document_name': learning.get('document_name'),
            'document_url': learning.get('document_url'),
            'sector_validated': learning.get('sector_validated'),
            'organization_validated': learning.get('organization_validated'),
            'type_validated': learning.get('type_validated'),
            'created_at': learning.get('created_at'),
            'modified_at': learning.get('modified_at'),
            'appeal_code': learning.get('appeal_code'),
            'appeal_name': learning.get('appeal', {}).get('name'),
            'event_id': learning.get('appeal', {}).get('event_details', {}).get('id'),
        }

    def generate_ai_summary(self, structured_data):
        """Generate AI summary using Azure OpenAI"""
        import json
        
        # 1) Build flat list of learnings
        all_learnings = [l for e in structured_data for l in e.get('related_ops_learning', [])][:20]

        def truncate(text: str, max_chars: int = 500) -> str:
            return text if len(text) <= max_chars else text[:max_chars] + "..."

        # 2) System prompt with an explicit example
        system_message = {
                "role": "system",
                "content": (
                    "You MUST return a JSON array of up to 6 objects, each with a clear suggestion at the end. Each insight **must** merge "
                    "between **one** and **three** distinct learnings inclusive and be very detailed. "
                    "The tone should be to help with a current similar crisis.  "
                    "For each insight also include a short list of 1-2 clear **recommendations** "
                    "labeled 'recommendations' that follow from the insight.\n\n"
                    "Example of correct output:\n\n"
                    "[\n"
                    "  {\n"
                    "    \"title\": \"Customizing Data Tools\",\n"
                    "    \"insight\": \"...\",\n"
                    "    \"recommendations\": [\n"
                    "       \"Do X within the first week of response\",\n"
                    "       \"Train local staff on Y tool\"\n"
                    "    ],\n"
                    "    \"source_note\": \"…\",\n"
                    "    \"metadata\": { … }\n"
                    "  }\n"
                    "]\n\n"
                    "Return ONLY the JSON array (no markdown)."
                )
            }

        # 3) Build the user-visible list of learnings
        learnings_block = "\n".join(
            f"- ID {l['id']} | Code {l['appeal_code']} | Name {l['appeal_name']} | {l['document_name']}:\n"
            f"  {truncate(l['learning_text'])}"
            for l in all_learnings
        )
        user_message = {
            "role": "user",
            "content": (
                "Here are the learnings:\n" + learnings_block +
                "\n\nPlease synthesize up to 6 actionable insights by combining any learnings that share a theme. Explain how the insight is buiilt using the sources and appeal codes"
                "Each insight must draw on at least two of the above. If the insight is based off different disasters, then try to link it to the current disaster. "
                "Then for each insight, under a key called `recommendations`, list 1–2 clear next steps that an operational team could take.  "
                "In `metadata.operational_learning_source` list every source you used (with its `id`, `code`, and `name`).  "
                "Make each insight no more than 5 sentences, include the country name, and return only valid JSON."
            )
        }

        # 4) Call OpenAI using enhanced client
        raw = self.get_azure_response([system_message, user_message], cache_prefix="previous_crises")

        if not raw:
            return []

        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.strip("```").strip()
            parsed = json.loads(clean)
        except Exception as e:
            logger.error(f"AI parsing error: {e}")
            return [{
                "title": "ParsingError",
                "insight": raw.strip(),
                "metadata": { "operational_learning_source": [] }
            }]

        out = []
        for obj in parsed:
            title = obj.get("title")
            insight_text = obj.get("insight")
            recs = obj.get("recommendations", [])
            meta = obj.get("metadata", {})
            if not (title and insight_text and isinstance(meta, dict)):
                continue

            # build the list of source dicts
            srcs = []
            for entry in meta.get("operational_learning_source", []):
                rid = entry["id"] if isinstance(entry, dict) else entry
                match = next((l for l in all_learnings if str(l["id"]) == str(rid)), None)
                if not match:
                    continue
                srcs.append({
                    "id":       match["id"],
                    "code":     match["appeal_code"],
                    "name":     match["appeal_name"],
                    "event_id": match.get("event_id"),
                })

            # if none matched, fall back to first two learnings
            if not srcs and len(all_learnings) >= 2:
                for l in all_learnings[:2]:
                    srcs.append({
                        "id":       l["id"],
                        "code":     l["appeal_code"],
                        "name":     l["appeal_name"],
                        "event_id": l.get("event_id"),
                    })

            # New: simple, descriptive source_note
            insight_source_note = (
                f"This insight was synthesized from {len(srcs)} operational-learning source"
                + ("s." if len(srcs) != 1 else ".")
            )

            out.append({
                "title":           title,
                "insight":         insight_text,
                "recommendations": recs,
                "source_note":     insight_source_note,
                "metadata": {
                    "operational_learning_source": srcs
                }
            })

        return out or [{
            "title": "ParsingError",
            "insight": raw.strip(),
            "metadata": { "operational_learning_source": [] }
        }]