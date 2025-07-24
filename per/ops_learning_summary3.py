import json
import hashlib
import tiktoken
from typing import Dict, Any, Optional, List

from django.conf import settings
from django.core.cache import cache
from django.utils.functional import cached_property
from openai import AzureOpenAI

from api.logger import logger

from per.dref_temp.dref_utils import dref_manager
from per.dref_temp.dref_utils import DREFFilters


class AzureOpenAiChat:
    """Azure OpenAI client for DREF summary generation with Redis caching"""
    
    CACHE_TTL = 3600  # 1 hour in seconds

    @cached_property
    def client(self):
        return AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT, 
            api_key=settings.AZURE_OPENAI_KEY, 
            api_version="2023-05-15"
        )
    
    @staticmethod
    def _generate_cache_key(messages: List[Dict[str, str]]) -> str:
        """Generate a unique cache key based on messages content"""
        # Create a deterministic hash from the messages
        content = json.dumps(messages, sort_keys=True)
        hash_obj = hashlib.md5(content.encode('utf-8'))
        return f"dref_llm_response:{hash_obj.hexdigest()}"

    def get_response(self, message):
        """Get LLM response with 1-hour Redis caching"""
        # Generate cache key
        cache_key = self._generate_cache_key(message)
        
        # Try to get from cache first
        cached_response = cache.get(cache_key)
        if cached_response is not None:
            logger.info(f"Cache hit for key: {cache_key}")
            return cached_response
        
        logger.info(f"Cache miss for key: {cache_key}")
        
        # Generate new response
        try:
            response = self.client.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT_NAME, 
                messages=message, 
                temperature=0.7
            )
            response_content = response.choices[0].message.content
            
            # Cache the response for 1 hour
            cache.set(cache_key, response_content, self.CACHE_TTL)
            logger.info(f"Cached response for key: {cache_key}")
            
            return response_content
        except Exception as e:
            logger.error(f"Error while generating DREF summary response: {e}", exc_info=True)
            return None


class DrefSummaryTask:
    """Task class for generating DREF operation summaries using Azure OpenAI"""
    
    PROMPT_DATA_LENGTH_LIMIT = 8000
    PROMPT_LENGTH_LIMIT = 10000
    ENCODING_NAME = "cl100k_base"
    
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

    planned_intervention_summary_prompt = (
        "\nAnalyze the DREF planned interventions data and create a comprehensive summary of intervention activities:\n\n"
        "Provide a detailed analysis of each planned intervention including:\n"
        "- Intervention objectives and rationale\n"
        "- Target populations and geographic scope\n"
        "- Key activities and implementation approach\n"
        "- Expected outcomes and impact\n"
        "- Budget allocation and resource requirements\n"
        "- Timeline and implementation phases\n"
        "- Risk factors and mitigation strategies\n"
        "- Coordination mechanisms and partnerships\n\n"
        "Requirements:\n"
        "- Plain text format (not JSON)\n"
        "- Structure as clear sections for each intervention\n"
        "- Include specific numbers, timelines, and budget figures\n"
        "- Focus on planned_interventions field data\n"
        "- Highlight cross-cutting themes and synergies\n"
        "- Provide strategic analysis of intervention portfolio\n\n"
        "Format each intervention as:\n"
        "INTERVENTION: [Title]\n"
        "Objective: [Clear statement of what this intervention aims to achieve]\n"
        "Target: [Population numbers, demographics, geographic areas]\n"
        "Activities: [Key implementation activities and approach]\n"
        "Budget: [Allocation amount and percentage of total]\n"
        "Timeline: [Implementation phases and duration]\n"
        "Partnerships: [Key implementing partners and coordination mechanisms]\n\n"
        "End with a STRATEGIC OVERVIEW section summarizing the overall intervention strategy and expected collective impact."
    )
    
    @staticmethod
    def count_tokens(string: str, encoding_name: str) -> int:
        """Returns the number of tokens in a text string."""
        encoding = tiktoken.get_encoding(encoding_name)
        return len(encoding.encode(string))
    
    @classmethod
    def generate_operational_summary(cls, dref_data: Dict[str, Any]) -> Optional[str]:
        """Generate operational objective and strategy summary (3 lines max)"""
        logger.info("Generating DREF operational summary")
        
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
            {"role": "system", "content": cls.system_message},
            {"role": "user", "content": f"DREF Data to analyze:\n{data_json}\n\n{cls.operational_summary_prompt}"},
            {"role": "assistant", "content": "I understand. I will analyze the DREF data and provide a structured summary according to your specifications."}
        ]
        
        # Token count validation
        message_content = [msg["content"] for msg in messages]
        text = " ".join(message_content)
        token_count = cls.count_tokens(text, cls.ENCODING_NAME)
        logger.info(f"DREF operational token count: {token_count}")
        
        if token_count > cls.PROMPT_LENGTH_LIMIT:
            logger.warning("Prompt too long for operational summary, truncating data")
            truncated_data = data_json[:cls.PROMPT_DATA_LENGTH_LIMIT]
            messages[1]["content"] = f"DREF Data to analyze:\n{truncated_data}\n\n{cls.operational_summary_prompt}"
        
        # Call OpenAI backend
        try:
            client = AzureOpenAiChat()
            response = client.get_response(messages)
            
            if not response:
                logger.error("No response received for operational summary")
                return None
            
            # Process operational response - take only first 3 non-empty lines
            lines = response.strip().split('\n')
            filtered_lines = [line.strip() for line in lines if line.strip()][:3]
            result = '\n'.join(filtered_lines)
            
            logger.info("Successfully generated operational summary")
            return result
            
        except Exception as e:
            logger.error(f"Error generating operational summary: {e}", exc_info=True)
            return None


    # Sector-based summary prompts following operational_summary_prompt structure
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

    needs_addressing_prompt = (
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
    
    @classmethod
    def generate_sector_summaries(cls, dref_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate sector-based summaries from DREF data"""
        logger.info("Generating sector-based summaries")
        
        sectors = []
        
        # Get sector data organized by title
        sector_data = cls.organize_data_by_sector(dref_data)
        
        for sector_title, sector_info in sector_data.items():
            try:
                # Get title_display (use first available title_display from any item in this sector)
                title_display = sector_title
                for item_list in [sector_info.get('actions', []), sector_info.get('needs', []), sector_info.get('planned_interventions', [])]:
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
                    needs_summary = cls.generate_needs_summary(sector_info['needs'])
                    if needs_summary:
                        sector_summary["needs_summary"] = needs_summary
                    else:
                        print(f"NEEDS_SUMMARY: {sector_title} - FAILED Empty/None")
                else:
                    print(f"NEEDS_SUMMARY: {sector_title} - No needs data found")
                
                
                # Process planned interventions for future actions
                if sector_info.get('planned_interventions'):
                    future_actions = cls.process_planned_interventions(sector_info['planned_interventions'])
                    
                    # Generate needs_addressed for each future action if needs_summary exists
                    if sector_summary["needs_summary"]:
                        for action in future_actions:
                            needs_addressed = cls.generate_needs_addressed(
                                sector_summary["needs_summary"], 
                                action
                            )
                            if needs_addressed:
                                action["needs_addressed"] = needs_addressed
                            else:
                                print(f"NEEDS_ADDRESSED: {sector_title} - FAILED Empty/None for action")
                    
                    sector_summary["future_actions"] = future_actions
                
                sectors.append(sector_summary)
                
            except Exception as e:
                logger.error(f"Error processing sector {sector_title}: {e}")
                continue
        
        logger.info(f"Generated {len(sectors)} sector summaries")
        return sectors
    
    @classmethod
    def organize_data_by_sector(cls, dref_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Organize DREF data by sector - prioritizing planned_interventions as sector definitions"""
        sector_data = {}
        
        # STEP 1: Process planned interventions FIRST to define sectors
        interventions = dref_data.get('planned_interventions', [])
        for i, intervention in enumerate(interventions):
            # Handle both dict and dataclass objects
            if hasattr(intervention, 'title'):
                sector_title = getattr(intervention, 'title', 'unknown')
            else:
                sector_title = intervention.get('title', 'unknown')
            if sector_title not in sector_data:
                sector_data[sector_title] = {'actions': [], 'needs': [], 'planned_interventions': []}
            sector_data[sector_title]['planned_interventions'].append(intervention)
        
        # STEP 2: Match needs_identified to sectors defined by planned_interventions
        needs = dref_data.get('needs_identified', [])
        
        for i, need in enumerate(needs):
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
                    if cls._sectors_match(sector_title, existing_sector):
                        sector_data[existing_sector]['needs'].append(need)
                        matched = True
                        break
                
                if not matched:
                    print(f"NEEDS_MATCHING: NO MATCH '{sector_title}' - skipping need {i+1}")
        
        # STEP 3: Match national society actions to sectors
        actions = dref_data.get('national_society_actions', [])
        for i, action in enumerate(actions):
            # Handle both dict and dataclass objects
            if hasattr(action, 'title'):
                sector_title = getattr(action, 'title', 'unknown')
            else:
                sector_title = action.get('title', 'unknown')
            
            # Only add actions if the sector was defined by planned_interventions
            if sector_title in sector_data:
                sector_data[sector_title]['actions'].append(action)
        
        return sector_data
    
    @classmethod
    def _sectors_match(cls, need_title: str, intervention_title: str) -> bool:
        """Check if sector titles match with fuzzy logic for common mismatches"""
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
    
    @classmethod
    def _create_fallback_needs_summary(cls, combined_needs: str) -> str:
        """Create a fallback summary when LLM is not available"""
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
    
    @classmethod
    def generate_needs_addressed(cls, needs_summary: str, future_action: Dict[str, Any]) -> Optional[str]:
        """Generate needs addressed summary for a single future action using LLM"""
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
            enhanced_system_message = f"{cls.system_message} The intervention involves: {description}"
            
            prompt_content = f"Needs Summary:\n{needs_summary}\n\n{action_text}\n\n{cls.needs_addressing_prompt}"
            
            messages = [
                {"role": "system", "content": enhanced_system_message},
                {"role": "user", "content": prompt_content},
                {"role": "assistant", "content": "I understand. I will analyze how this specific action addresses the identified needs according to your specifications."}
            ]
            
            client = AzureOpenAiChat()
            response = client.get_response(messages)
            # Clean response: strip whitespace and remove extra line breaks
            cleaned_response = ' '.join(response.strip().split()) if response else None
            return cleaned_response
            
        except Exception as e:
            logger.error(f"Error generating needs addressed: {e}")
            return None

    @classmethod
    def generate_needs_summary(cls, needs_data: List[Dict[str, Any]]) -> Optional[str]:
        """Generate needs summary using LLM"""
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
                messages = [
                    {"role": "system", "content": cls.system_message},
                    {"role": "user", "content": f"Needs data:\n{combined_needs}\n\n{cls.needs_summary_prompt}"},
                    {"role": "assistant", "content": "I understand. I will analyze the needs data and provide a structured summary according to your specifications."}
                ]
                
                client = AzureOpenAiChat()
                response = client.get_response(messages)
                # Clean response: strip whitespace and remove extra line breaks
                cleaned_response = ' '.join(response.strip().split()) if response else None
                return cleaned_response
            except Exception as llm_error:
                # Fallback: Create a simple summary from the needs descriptions
                print(f"NEEDS_SUMMARY: LLM failed, using fallback: {llm_error}")
                return cls._create_fallback_needs_summary(combined_needs)
            
        except Exception as e:
            logger.error(f"Error generating needs summary: {e}")
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
                    "needs_addressed": "",
                    "_description": description
                }
                
                future_actions.append(future_action)
                
            except Exception as e:
                print(f"❌ ERROR processing planned intervention {i+1}: {e}")
                logger.error(f"Error processing planned intervention: {e}")
                continue
        
        print(f"🔍 PROCESS_PLANNED_INTERVENTIONS: Final result - {len(future_actions)} future actions")
        return future_actions
    
    @classmethod
    def generate_dref_summaries(cls, dref_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate operational and sector-based summaries"""
        logger.info("Starting DREF summary generation")
        
        result = {
            "operational_summary": None,
            "sectors": [],
            "status": "pending",
            "errors": []
        }
        
        try:
            operational_summary = cls.generate_operational_summary(dref_data)
            if operational_summary:
                result["operational_summary"] = operational_summary
            else:
                result["errors"].append("Failed to generate operational summary")
                print("❌ GENERATE_DREF_SUMMARIES: Failed to generate operational summary")
            
            sectors = cls.generate_sector_summaries(dref_data)

            if sectors:
                result["sectors"] = sectors
            else:
                result["errors"].append("Failed to generate sector summaries")
                print("❌ GENERATE_DREF_SUMMARIES: Failed to generate sector summaries (empty sectors list)")
                logger.error("Failed to generate sector summaries")
            
            if result["operational_summary"] and result["sectors"]:
                result["status"] = "success"
            elif result["operational_summary"] or result["sectors"]:
                result["status"] = "partial_success"
            else:
                result["status"] = "failed"
                
        except Exception as e:
            print(f"❌ GENERATE_DREF_SUMMARIES: Exception occurred: {e}")
            result["status"] = "failed"
            result["errors"].append(f"Unexpected error: {str(e)}")

        return result