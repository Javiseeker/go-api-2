"""
UCL Research Views
==================

Unified API views for the 4 operational learning summary endpoints.
All HTTP requests use httpx async/await syntax via the IFRCAPIClient.

Views included:
1. PreviousCrisesInsightsView - Previous Crises/Events Insights generated using LLMs.
2. RapidResponseCapacityQuestionsView -  Rapid Response  Capacity Questions with Excel output.
3. DrefSummaryView - DREF LLM summaries with operational objectives and financial analysis including sectors.
4. DrefSituationalOverviewView - DREF situational overview with event situation and changes.

"""

import asyncio
import os
from django.conf import settings
from tempfile import NamedTemporaryFile
from typing import Any, Optional

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status as drf_status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.logger import logger
from per.dref_temp.dref_utils import dref_manager, DREFFilters
from per.ucl_research.blob_upload import upload_to_blob
from per.ucl_research.serializers import (
    PerDrefLLMSummarySerializer,
    PerDrefSituationalOverviewSerializer,
)
from per.ucl_research.ifrc_client import IFRCAPIClient
from per.ucl_research.ops_learning_summary4 import DrefSummaryTask, OpsLearningSummaryTask, PerformanceMonitor, EnhancedAzureOpenAiChat, RRCapacityTask
from per.ucl_research.rapid_response_parser import RapidResponseCapacityParser
from datetime import datetime
from django.core.cache import cache
from typing import Dict, List, Optional, Any
import json


class BaseUCLView(APIView):
    """Base class for UCL research views with common functionality"""
    
    CACHE_TIMEOUT = 3600  # 1 hour default
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ifrc_client = IFRCAPIClient()
    
    async def aclose(self):
        """Close async resources"""
        if hasattr(self, 'ifrc_client'):
            await self.ifrc_client.close()
    
    def _validate_event_id(self, request) -> tuple[Optional[int], Optional[Response]]:
        """Validate event_id parameter from request"""
        event_id = request.query_params.get("id", None)
        
        if not event_id:
            return None, Response(
                {"error": "Event ID is required"}, 
                status=drf_status.HTTP_400_BAD_REQUEST
            )
        
        try:
            event_id = int(event_id)
            return event_id, None
        except ValueError:
            return None, Response(
                {"error": "Event ID must be a valid integer"}, 
                status=drf_status.HTTP_400_BAD_REQUEST
            )
    
    def _validate_country_disaster_params(self, request) -> tuple[Optional[tuple], Optional[Response]]:
        """Validate country and disaster_type parameters"""
        country = request.query_params.get('country')
        disaster_type = request.query_params.get('disaster_type')
        
        if not country or not disaster_type:
            return None, Response(
                {'detail': 'Both "country" and "disaster_type" query parameters are required.'},
                status=drf_status.HTTP_400_BAD_REQUEST
            )
        
        try:
            country_id = int(country)
            disaster_type_id = int(disaster_type)
            return (country_id, disaster_type_id), None
        except ValueError:
            return None, Response(
                {'detail': '"country" and "disaster_type" must be integer IDs.'},
                status=drf_status.HTTP_400_BAD_REQUEST
            )


@method_decorator(csrf_exempt, name='dispatch')  
class PreviousCrisesInsightsView(BaseUCLView):
    """API view for fetching and enriching IFRC event data with operational learning insights."""
    
    DISASTER_TYPE_EVENT_THRESHOLD = 1
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.azure_client = EnhancedAzureOpenAiChat()

        # load JSON from project root
        json_path = os.path.join(settings.BASE_DIR, 'rr_parsed_excel.json')
        logger.debug(f"[RR TEMPLATE] loading from {json_path} (exists={os.path.exists(json_path)})")
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                self.rr_template = json.load(f)
            logger.debug(f"[RR TEMPLATE] loaded {len(self.rr_template)} entries")
        except Exception as e:
            self.rr_template = []
            logger.warning(f"Could not load RR questions template JSON from {json_path}: {e}")

    def get(self, request) -> Response:
        start_time = datetime.now()
        params, error_response = self._validate_country_disaster_params(request)
        if error_response:
            return error_response

        country_id, disaster_type_id = params
        cache_key = f"ucl_previous_crises:{country_id}:{disaster_type_id}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response({"ai_structured_summary": cached}, status=drf_status.HTTP_200_OK)

        try:
            response = self._process_previous_crises_insights(country_id, disaster_type_id, cache_key)
            PerformanceMonitor.track_execution_time("ifrc_event_list", start_time, datetime.now())
            return response
        except Exception as e:
            logger.error(f"Error in PreviousCrisesInsightsView: {e}", exc_info=True)
            return Response({
                "error": "Internal server error occurred while processing IFRC events",
                "details": str(e)
            }, status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        except Exception as e:
            logger.error(f"Error in PreviousCrisesInsightsView: {e}", exc_info=True)
            return Response({
                "error": "Internal server error occurred while processing IFRC events",
                "details": str(e)
            }, status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _process_previous_crises_insights(
        self, country_id: int, disaster_type_id: int, cache_key: str
    ) -> Response:
        primary = self._fetch_ops_learning(country_id, disaster_type_id)
        if not primary:
            secondary = self._fetch_ops_learning(country_id, None)
        else:
            all_country = self._fetch_ops_learning(country_id, None)
            p_ids = {p["id"] for p in primary}
            secondary = [l for l in all_country if l["id"] not in p_ids]

        logger.debug(f"[FALLBACK] primary_count={len(primary)}, secondary_count={len(secondary)}")

        combined = (primary + secondary)[:6]
        if not combined:
            return Response({
                "ai_structured_summary": [],
                "fallback_note": "No operational learnings have been recorded in the system for this context yet. You're welcome to check the Ops Learning dashboard or evaluations database."
            }, status=drf_status.HTTP_200_OK)

        processed_learnings = [self._create_learning_entry(l) for l in combined]
        import httpx
        for pl in processed_learnings:
            ev_id = pl.get("event_id")
            event = {}
            if ev_id:
                try:
                    logger.debug(f"[EVENT] querying /api/v2/event/?id={ev_id}")
                    with httpx.Client(timeout=10.0) as client:
                        r = client.get("https://goadmin.ifrc.org/api/v2/event/", params={"id": ev_id})
                        r.raise_for_status()
                        results = r.json().get("results", [])
                    if results and isinstance(results[0], dict):
                        event = results[0]
                        logger.debug(f"[EVENT] loaded event {ev_id}: {event.get('name')!r}")
                    else:
                        logger.warning(f"[EVENT] no event in results for id={ev_id}")
                except Exception as e:
                    logger.warning(f"[EVENT] failed to fetch event?id={ev_id}: {e}")

            pl["event"] = {
                "id":          event.get("id"),
                "name":        event.get("name"),
                "dtype":       (event.get("dtype") or {}).get("name"),
                "start":       event.get("disaster_start_date"),
                "countries":   [c.get("name") for c in event.get("countries", [])] if isinstance(event.get("countries"), list) else [],
                "description": event.get("description") or event.get("summary") or ""
            }

        ai_insights = self._generate_ai_summary([{"related_ops_learning": processed_learnings}])
        rr_results = self._generate_rr_questions([{"related_ops_learning": ai_insights}])
        rr_by_title = {r["title"]: r for r in rr_results}
        merged = []
        for obj in ai_insights:
            rr = rr_by_title.get(obj["title"], {})
            merged.append({
                "title":        obj["title"],
                "insight":      obj["insight"],
                "area":         rr.get("area"),
                "rr_questions": rr.get("rr_questions", []),
                "source_note":  obj.get("source_note"),
                "metadata":     obj.get("metadata", {}),
            })

        # 7) cache & return
        cache.set(cache_key, merged, timeout=3600)
        return Response({"ai_structured_summary": merged}, status=drf_status.HTTP_200_OK)



    def _fetch_ops_learning(
        self,
        country_id: int,
        disaster_type_id: Optional[int],
        max_results: int = 6
    ) -> List[Dict[str, Any]]:
        """
        Fetch up to max_results validated learnings for (country + optional dtype),
        letting the server do the heavy lifting. Copied from IFRCEventListView.
        """
        params = {
            "is_validated": "true",
            "limit": max_results,
            "appeal_code__country": country_id,
        }
        if disaster_type_id is not None:
            params["appeal__event_details__dtype"] = disaster_type_id

        resp = self._make_api_request(
            "https://goadmin.ifrc.org/api/v2/ops-learning/",
            params,
            "ops learning"
        ).get("results", [])

        logger.info(f"=== DEBUG: {len(resp)} learnings fetched from API "
                  f"(country={country_id}, dtype={disaster_type_id}) ===")
        return resp

    def _make_api_request(self, url: str, params: Dict[str, Any], data_type: str) -> Dict[str, Any]:
        """Make HTTP request to external API with error handling. Copied from IFRCEventListView."""
        import httpx
        try:
            logger.info(f"=== DEBUG: Making {data_type} request to {url} with params: {params} ===")
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
            
            results = response.json().get('results', [])
            logger.info(f"=== DEBUG: {data_type} request returned {len(results)} results ===")
            
            return {'results': results}
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
            logger.info(f"=== DEBUG: Error in {data_type} request: {exc} ===")
            return {
                'error': True,
                'detail': f'Error fetching {data_type}: {exc}',
                'results': []
            }

    def _create_learning_entry(self, learning: Dict[str, Any]) -> Dict[str, Any]:
        """Create learning entry dict. Copied from IFRCEventListView."""
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
            'appeal_name': (learning.get('appeal') or {}).get('name'),
            'event_id':    ((learning.get('appeal') or {}).get('event_details') or {}).get('id'),
        }

    def _generate_ai_summary(self, structured_data):
        """Generate AI summary using the same method as working IFRCEventListView"""
        import json
        from django.conf import settings
        
        if not hasattr(self.azure_client, 'client') or not self.azure_client.client:
            return []

        all_learnings = [l for e in structured_data for l in e.get('related_ops_learning', [])][:20]

        def truncate(text: str, max_chars: int = 500) -> str:
            return text if len(text) <= max_chars else text[:max_chars] + "..."

        system_message = {
            "role": "system",
            "content": (
                "You MUST return a JSON array of up to 6 objects, each merging between two and three distinct learnings into a single, detailed insight."
                "You MUST include the source of learning you are referencing within the insight "
                "Prioritise showing insights that are based on learnings that have a matching disaster type. "
                "The tone should be to help with a current similar crisis. "
                "Include for each insight a key called `source_note` and a `metadata.operational_learning_source` array of {id,code,name}.  "
                "Example of correct output:\n\n"
                "[\n"
                "  {\n"
                "    \"title\": \"Customizing Data Tools\",\n"
                "    \"insight\": \"...\",\n"
                "    \"source_note\": \"…\",\n"
                "    \"metadata\": { … }\n"
                "  }\n"
                "]\n\n"
                "Return ONLY the JSON array (no markdown)."
            )
        }
        learnings_block = "\n".join(
            f"- ID {l['id']} | Code {l['appeal_code']} | Name {l['appeal_name']} | {l['document_name']}:\n"
            f"  {truncate(l['learning_text'])}"
            for l in all_learnings
        )
        user_message = {
            "role": "user",
            "content": (
                "Here are the learnings:\n" + learnings_block +
                "\n\nPlease synthesize up to 6 actionable insights by combining any learnings that share a theme. "
                "Explain how each insight builds on the sources and appeal codes, and enrich them with the event details (description, disaster type, country).  "
                "You MUST include the source of learning you are referencing within the insight "
                "In `metadata.operational_learning_source` list every source you used (with its `id`, `code`, and `name`).  "
                "Make each insight no less than 4 sentences, include the country name, and return only valid JSON."
            )
        }
        raw = self.azure_client.get_response([system_message, user_message], cache_prefix="previous_crises")

        if not raw:
            return []

        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.strip("```").strip()
            parsed = json.loads(clean)
        except Exception as e:
            print("AI parsing error:", e)
            print("Raw content was:", raw)
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

    def _generate_rr_questions(self, structured_data):
        import json

        ai_insights = structured_data[0].get("related_ops_learning", [])

        def truncate(text: str, n: int = 100) -> str:
            return text if len(text) <= n else text[:n] + "…"

        insights_block = "\n".join(
            f"- {truncate(l.get('title','Untitled'))}: {truncate(l.get('insight',''))}"
            for l in ai_insights
        )

        system_message = {
            "role": "system",
            "content": (
                "Generate RR questions for each insight. "
                "Match each insight to the most relevant 'Area' in the template JSON, "
                "generate 1–2 focused RR questions based on that Area's 'Critical Questions', "
                "and return a JSON array of objects with keys: "
                "'title', 'insight', 'area', 'rr_questions'."
            )
        }

        user_message = {
            "role": "user",
            "content": (
                f"Insights:\n{insights_block}\n\n"
                f"Template JSON:\n{json.dumps(self.rr_template)}\n\n"
                "Now generate the RR questions based on this template."
            )
        }

        raw = self.azure_client.get_response(
            [system_message, user_message], cache_prefix="previous_crises"
        )
        if not raw:
            return []

        try:
            payload = json.loads(raw.strip().strip("```json").strip("```").strip())
        except Exception as e:
            logger.error(f"RR questions JSON parse error: {e}")
            return []

        return [
            {
                "title":        item.get("title"),
                "insight":      item.get("insight"),
                "area":         item.get("area"),
                "rr_questions": item.get("rr_questions", []),
            }
            for item in payload
        ]

@method_decorator(csrf_exempt, name='dispatch')
class RapidResponseCapacityQuestionsView(BaseUCLView):
    """API view to process RR capacity questions and generate filled Excel output."""

    def get(self, request, *args: Any, **kwargs: Any) -> Response:
        """Process RR capacity questions and return Excel file URL"""
        
        start_time = datetime.now()
    
        params, error_response = self._validate_country_disaster_params(request)
        if error_response:
            return error_response

        country_id, disaster_type_id = params

        # Check cache first for fast response
        cache_key = f"ucl_rr_capacity:{country_id}:{disaster_type_id}"
        cached_url = cache.get(cache_key)
        if cached_url:
            return Response({"file_url": cached_url}, status=drf_status.HTTP_200_OK)

        try:
            # Process using the dedicated parser (now synchronous)
            result = self._process_rr_capacity_questions(country_id, disaster_type_id, cache_key)
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("rr_capacity_questions", start_time, end_time)
            
            return result
            
        except FileNotFoundError:
            return Response(
                {"detail": "RR capacity questions data file not found."},
                status=drf_status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.error(f"Error in RRCapacityQuestionsView: {e}", exc_info=True)
            return Response(
                {"detail": f"Error processing RR capacity questions: {str(e)}"},
                status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    
    def _process_rr_capacity_questions(self, country_id: int, disaster_type_id: int, cache_key: str) -> Response:
        """Process RR capacity questions using the dedicated parser"""
        parser = RapidResponseCapacityParser()
        
        try:
            blob_url = parser.process_rr_capacity_questions(country_id, disaster_type_id, cache_key)
            return Response({"file_url": blob_url}, status=drf_status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Error in RR capacity parser: {e}", exc_info=True)
            return Response(
                {"detail": f"Error processing RR capacity questions: {str(e)}"},
                status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@method_decorator(csrf_exempt, name='dispatch')
class DrefSummaryView(BaseUCLView):
    """
    API view for generating DREF LLM summaries.
    Returns brief and long summaries for DREF operations.
    
    Summary 1 (briefSummary): Operational objectives and strategy rationale (max 3 lines)
    Summary 2 (longSummary): Comprehensive budget and financial analysis (detailed JSON)
    """
    
    def get(self, request):
        """Generate DREF LLM summaries for given event ID"""
        from datetime import datetime
        start_time = datetime.now()
        
        # Validate event ID
        event_id, error_response = self._validate_event_id(request)
        if error_response:
            return error_response
        
        # Check cache first for fast response
        cache_key = f"ucl_dref_summary:{event_id}"
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return Response(cached_result, status=drf_status.HTTP_200_OK)

        try:
            # Process synchronously using existing DrefSummaryTask
            result = asyncio.run(self._process_dref_summary(event_id, cache_key))
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("per_dref_llm_summary", start_time, end_time)
            
            return result
            
        except Exception as e:
            logger.error(f"Error in DrefSummaryView: {e}", exc_info=True)
            return Response({
                "error": "Internal server error occurred while generating DREF summaries",
                "details": str(e)
            }, status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    async def _process_dref_summary(self, event_id: int, cache_key: str) -> Response:
        """Process DREF summary using exact same logic as PerDrefLLMSummaryView"""
        async with IFRCAPIClient() as client:
            # Step 1: Get event details (replaces EventAPIClient)
            event = await client.get_event_detail(event_id)
            
            if not event:
                return Response(
                    {"error": "Event not found"}, 
                    status=drf_status.HTTP_404_NOT_FOUND
                )
            
            # Step 2: Get field reports
            field_reports = event.get("field_reports", [])
            
            if len(field_reports) == 0:
                return Response({
                    "error": "Field Reports not found",
                    "event_id": event_id,
                    "event_name": event.get("name")
                }, status=drf_status.HTTP_404_NOT_FOUND)
            
            # Step 3: Get DREF data (exact same logic)
            field_report_ids = [fr['id'] for fr in field_reports]
            filters = DREFFilters(field_report_ids=field_report_ids)
            dref_data = dref_manager.get_data("basic", filters)

            if len(dref_data) == 0:
                return Response({
                    "error": "DREFs not found",
                    "event_id": event_id,
                    "event_name": event.get("name")
                }, status=drf_status.HTTP_404_NOT_FOUND)
            
            dref_data = dref_data[0]
            dref_data = dref_manager.get_latest_dref_version(dref_data)

            # Step 4: Prepare DREF dictionary (exact same as original)
            dref_dict = {
                'id': dref_data.id,
                'title': dref_data.title,
                'operation_objective': getattr(dref_data, 'operation_objective', None),
                'response_strategy': getattr(dref_data, 'response_strategy', None),
                'amount_requested': dref_data.amount_requested,
                'total_targeted_population': dref_data.total_targeted_population,
                'operation_timeframe': getattr(dref_data, 'operation_timeframe', None),
                'country_details': {
                    'name': dref_data.country_details.name if dref_data.country_details else None,
                    'iso': dref_data.country_details.iso if dref_data.country_details else None
                },
                'disaster_type_details': {
                    'name': dref_data.disaster_type_details.name if dref_data.disaster_type_details else None
                },
                'event_date': dref_data.event_date,
                'end_date': getattr(dref_data, 'end_date', None),
                'planned_interventions': getattr(dref_data, 'planned_interventions', []),
                'national_society_actions': getattr(dref_data, 'national_society_actions',[]),
                'needs_identified': getattr(dref_data, 'needs_identified', []),
                'people_in_need': getattr(dref_data, 'people_in_need', None),
                'human_resource': getattr(dref_data, 'human_resource', None),
                'logistic_capacity_of_ns': getattr(dref_data, 'logistic_capacity_of_ns', None),
                'pmer': getattr(dref_data, 'pmer', None)
            }
            
            # Step 5: Get operational update number (exact same logic)
            op_update_number = 1  # Default value
            operational_updates = getattr(dref_data, 'operational_update_details', [])
            if operational_updates and isinstance(operational_updates, list):
                first_update = operational_updates[0]
                op_update_number = getattr(first_update, 'operational_update_number', 1)

            # Step 6: Generate summaries using existing DrefSummaryTask (exact same)
            summaries = DrefSummaryTask.generate_dref_summaries(dref_dict)
            
            # Step 7: Prepare response data (exact same format)
            sectors_data = summaries.get("sectors", [])
            summary_data = {
                "operational_summary": summaries.get("operational_summary", ""),
                "sectors": sectors_data,
                "dref_type": dref_data.type_of_dref_display if hasattr(dref_data, 'type_of_dref_display') else "",
                "dref_onset": dref_data.type_of_onset_display if hasattr(dref_data, 'type_of_onset_display') else "",
                "metadata": {
                    "dref_id": dref_data.id,
                    "dref_title": dref_data.title,
                    "dref_appeal_code": dref_data.appeal_code,
                    "dref_date": dref_data.event_date,
                    "dref_created_at": dref_data.created_at if hasattr(dref_data, 'created_at') else None,
                    "dref_budget_file": getattr(dref_data, 'budget_file_preview', None),
                    "dref_op_update_number": op_update_number
                }
            }
            
            # Cache result
            cache.set(cache_key, summary_data, timeout=3600)
            
            # Return serialized response (exact same as original)
            serializer = PerDrefLLMSummarySerializer(summary_data)
            return Response(serializer.data, status=drf_status.HTTP_200_OK)


@method_decorator(csrf_exempt, name='dispatch')
class DrefSituationalOverviewView(BaseUCLView):
    """
    API view for generating DREF situational overview.
    Returns a 5-line paragraph summarizing the event situation and key changes.
    """
    
    def get(self, request):
        """Generate DREF situational overview for given event ID"""
        from datetime import datetime
        start_time = datetime.now()
        
        # Validate event ID
        event_id, error_response = self._validate_event_id(request)
        if error_response:
            return error_response
        
        # Check cache first for fast response
        cache_key = f"ucl_dref_situational:{event_id}"
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return Response(cached_result, status=drf_status.HTTP_200_OK)

        try:
            # Process synchronously using existing DrefSummaryTask
            result = asyncio.run(self._process_situational_overview(event_id, cache_key))
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("per_dref_situational_overview", start_time, end_time)
            
            return result
            
        except Exception as e:
            logger.error(f"Error in DrefSituationalOverviewView: {e}", exc_info=True)
            return Response({
                "error": "Internal server error occurred while generating situational overview",
                "details": str(e)
            }, status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    async def _process_situational_overview(self, event_id: int, cache_key: str) -> Response:
        """Process situational overview using exact same logic as PerDrefSituationalOverviewView"""
        async with IFRCAPIClient() as client:
            # Step 1: Get event details (replaces EventAPIClient)
            event = await client.get_event_detail(event_id)
            if not event:
                return Response(
                    {"error": "Event not found"}, 
                    status=drf_status.HTTP_404_NOT_FOUND
                )
            
            # Step 2: Get field reports
            field_reports = event.get("field_reports", [])
            if not field_reports:
                return Response({
                    "error": "Field Reports not found",
                    "event_id": event_id,
                    "event_name": event.get("name")
                }, status=drf_status.HTTP_404_NOT_FOUND)
            
            # Step 3: Extract field report IDs and find linked DREF (exact same logic)
            field_report_ids = [fr['id'] for fr in field_reports]
            dref_data = dref_manager.get_data("basic", DREFFilters(field_report_ids=field_report_ids))

            if not dref_data:
                return Response({
                    "error": "No DREF found for the given event ID",
                    "event_id": event_id,
                    "event_name": event.get("name"),
                    "field_reports_count": len(field_reports),
                    "field_report_ids": field_report_ids
                }, status=drf_status.HTTP_404_NOT_FOUND)
            
            dref_data = dref_data[0]

            # Step 4: Get latest DREF version (exact same as original)
            latest_dref_version = dref_manager.get_latest_dref_version(dref_data)
            
            # Step 5: Prepare data for LLM generation (exact same as original)
            latest_update_dict = {
                'event_description': (getattr(latest_dref_version, 'event_description', '') or 
                                   getattr(latest_dref_version, 'description', '') or 
                                   getattr(latest_dref_version, 'summary', '')),
                'event_scope': (getattr(latest_dref_version, 'event_scope', '') or 
                              getattr(latest_dref_version, 'scope_and_scale', '')),
                'operation_objective': getattr(latest_dref_version, 'operation_objective', ''),
                'response_strategy': getattr(latest_dref_version, 'response_strategy', ''),
                'title': getattr(latest_dref_version, 'title', ''),
                'operational_update_number': getattr(latest_dref_version, 'operational_update_number', 0),
                'country_details': {
                    'name': latest_dref_version.country_details.name if latest_dref_version.country_details else None,
                    'iso': latest_dref_version.country_details.iso if latest_dref_version.country_details else None
                },
                'disaster_type_details': {
                    'name': latest_dref_version.disaster_type_details.name if latest_dref_version.disaster_type_details else None
                },
                'date_of_approval': getattr(latest_dref_version, 'date_of_approval', None),
            }

            # Step 6: Generate situational overview (exact same as original)
            situational_overview = DrefSummaryTask.generate_situational_overview(latest_update_dict)
            
            if not situational_overview:
                return Response({
                    "error": "Failed to generate situational overview",
                    "dref_id": dref_data.id,
                    "event_id": event_id
                }, status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Step 7: Prepare response data (exact same format as original)
            response_data = {
                "situational_overview": situational_overview,
                "metadata": {
                    # Event-focused information (primary for situational overview)
                    "event_id": event_id,
                    "event_name": event.get("name"),
                    "disaster_type": latest_update_dict['disaster_type_details']['name'],
                    "country": latest_update_dict['country_details']['name'],
                    
                    # Operational update context (key for understanding situation changes)
                    "latest_update_number": latest_update_dict.get('operational_update_number'),
                    "total_operational_updates": len(getattr(dref_data, 'operational_update_details', [])),
                    
                    # Basic DREF information (minimal, for reference)
                    "dref_id": dref_data.id,
                    "dref_title": getattr(dref_data, 'title', None),
                    "dref_appeal_code": getattr(dref_data, 'appeal_code', None),
                    "dref_date": getattr(dref_data, 'date_of_approval', None)
                }
            }
            
            # Cache result
            cache.set(cache_key, response_data, timeout=3600)
            
            # Return serialized response (exact same as original)
            serializer = PerDrefSituationalOverviewSerializer(response_data)
            return Response(serializer.data, status=drf_status.HTTP_200_OK)
