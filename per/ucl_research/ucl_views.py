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
from per.ucl_research.ops_learning_summary4 import DrefSummaryTask, OpsLearningSummaryTask, PerformanceMonitor, RRCapacityTask, BaseAITask, PreviousCrisesTask
from per.ucl_research.rapid_response_parser import RapidResponseCapacityParser
from datetime import datetime
from typing import Dict, List, Optional, Any

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
        self.previous_crises_task = PreviousCrisesTask()
        try:
            self.rr_template = RapidResponseCapacityParser._load_questions_data()
        except Exception as e:
            self.rr_template = []
            logger.warning(f"Could not load RR questions template: {e}")

    def get(self, request) -> Response:
        """Get IFRC events with operational learning insights"""
        start_time = datetime.now()
        
        # Validate parameters
        params, error_response = self._validate_country_disaster_params(request)
        if error_response:
            return error_response

        country_id, disaster_type_id = params

        # Check cache first for fast response
        cache_key = f"ucl_previous_crises:{country_id}:{disaster_type_id}"
        cached_result = BaseAITask.get_cached_result(cache_key)
        if cached_result is not None:
            return Response({"ai_structured_summary": cached_result}, status=drf_status.HTTP_200_OK)

        try:
            # Process asynchronously using IFRCAPIClient
            import asyncio
            result = asyncio.run(self._process_previous_crises_insights(country_id, disaster_type_id, cache_key))
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("ifrc_event_list", start_time, end_time)
            
            return result
            
        except Exception as e:
            logger.error(f"Error in PreviousCrisesInsightsView: {e}", exc_info=True)
            return Response({
                "error": "Internal server error occurred while processing IFRC events",
                "details": str(e)
            }, status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    async def _process_previous_crises_insights(self, country_id: int, disaster_type_id: int, cache_key: str) -> Response:
        """Process previous crises insights using dedicated task class"""
        
        async with IFRCAPIClient() as client:
            primary = await client.get_ops_learning(country_id, disaster_type_id, max_results=20)
            if not primary:
                secondary = await client.get_ops_learning(country_id, None, max_results=20)
            else:
                all_country = await client.get_ops_learning(country_id, None, max_results=20)
                primary_ids = {p['id'] for p in primary}
                secondary = [l for l in all_country if l['id'] not in primary_ids]

            merged = primary + secondary
            seen_ids = set()
            deduped = []
            for l in merged:
                lid = l.get('id')
                if lid in seen_ids:
                    continue
                seen_ids.add(lid)
                deduped.append(l)

            combined_learning = deduped[:20]

            if not combined_learning:
                return Response({
                    "ai_structured_summary": [],
                    "fallback_note": (
                        "No operational learnings have been recorded in the system for this context yet. "
                        "You're welcome to check the [Ops Learning dashboard]"
                        "(https://go.ifrc.org/deployments/ops-learning) "
                        "and the [IFRC's evaluations database]"
                        "(https://www.ifrc.org/evaluations) to learn more."
                    )
                }, status=drf_status.HTTP_200_OK)

            # STEP 4: Convert raw learning entries into the expected format
            processed_learnings = [self.previous_crises_task.create_learning_entry(l) for l in combined_learning]
            for pl in processed_learnings:
                ev_id = pl.get("event_id")
                event = await client.get_event_detail(ev_id) if ev_id else {}

                if not event:
                    event = {}
                countries = event.get("countries", [])
                if not isinstance(countries, list):
                    countries = []

                dtype_obj = event.get("dtype")
                if isinstance(dtype_obj, dict):
                    dtype_name = dtype_obj.get("name")
                else:
                    dtype_name = str(dtype_obj) if dtype_obj is not None else None

                pl["event"] = {
                    "id":          event.get("id"),
                    "name":        event.get("name"),
                    "dtype":       dtype_name,
                    "start":       event.get("disaster_start_date"),
                    "countries":   [c.get("name") for c in countries if c],
                    "description": event.get("description") or event.get("summary") or ""
                }

            ai_summary = self.previous_crises_task.generate_ai_summary([{"related_ops_learning": processed_learnings}])
            
        if not ai_summary:
            return Response({
                "ai_structured_summary": [],
                "fallback_note": (
                    "No operational learnings have been recorded in the system for this context yet. "
                    "You're welcome to check the [Ops Learning dashboard]"
                    "(https://go.ifrc.org/deployments/ops-learning) "
                    "and the [IFRC's evaluations database]"
                    "(https://www.ifrc.org/evaluations) to learn more."
                )
            }, status=drf_status.HTTP_200_OK)
        
        rr_results = self.previous_crises_task.generate_rr_questions(self.rr_template, [{"related_ops_learning": ai_summary}])
        rr_by_title = {r["title"]: r for r in rr_results}
        merged = []
        for obj in ai_summary:
            rr = rr_by_title.get(obj["title"], {})
            merged.append({
                "title":        obj["title"],
                "insight":      obj["insight"],
                "area":         rr.get("area"),
                "rr_questions": rr.get("rr_questions", []),
                "source_note":  obj.get("source_note"),
                "metadata":     obj.get("metadata", {}),
            })
        
        response_data = {
            "ai_structured_summary": merged
        }
        
        # Cache result for future requests
        BaseAITask.set_cached_result(cache_key, response_data)
        
        return Response(response_data, status=drf_status.HTTP_200_OK)


@method_decorator(csrf_exempt, name='dispatch')
class RapidResponseCapacityQuestionsView(BaseUCLView):
    """API view to process RR capacity questions and generate filled Excel output."""

    def get(self, request, *args: Any, **kwargs: Any) -> Response:
        """Process RR capacity questions and return Excel file URL"""
        
        start_time = datetime.now()
        
        # Validate parameters
        params, error_response = self._validate_country_disaster_params(request)
        if error_response:
            return error_response

        country_id, disaster_type_id = params

        # Check cache first for fast response
        cache_key = f"ucl_rr_capacity:{country_id}:{disaster_type_id}"
        cached_url = BaseAITask.get_cached_result(cache_key)
        if cached_url:
            return Response({"file_url": cached_url}, status=drf_status.HTTP_200_OK)

        try:
            # Process using the dedicated parser with async API calls
            import asyncio
            result = asyncio.run(self._process_rr_capacity_questions(country_id, disaster_type_id, cache_key))
            
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
    
    async def _process_rr_capacity_questions(self, country_id: int, disaster_type_id: int, cache_key: str) -> Response:
        """Process RR capacity questions using the dedicated parser"""
        
        try:
            # Fetch data using IFRCAPIClient
            async with IFRCAPIClient() as client:
                ops_learning_data = await self._fetch_rr_ops_learning_data(client, country_id, disaster_type_id)
                events_data = await self._fetch_events_from_ops_learning(client, ops_learning_data)
            
            # Process using the dedicated parser
            parser = RapidResponseCapacityParser()
            blob_url = parser.process_rr_capacity_questions_with_data(
                country_id, disaster_type_id, cache_key, ops_learning_data, events_data
            )
            
            response_data = {
                "file_url": blob_url
            }
            
            return Response(response_data, status=drf_status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Error in RR capacity parser: {e}", exc_info=True)
            return Response(
                {"detail": f"Error processing RR capacity questions: {str(e)}"},
                status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    
    async def _fetch_rr_ops_learning_data(
        self, 
        client: IFRCAPIClient, 
        country_id: int, 
        disaster_type_id: int, 
        target_count: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Fetch ops-learning data using two-stage approach.
        
        STAGE 1: Fetch using both country and disaster type filters
        STAGE 2: If fewer results, fill remaining with country-only data (any disaster type)
        """
        from typing import Set
        
        # STAGE 1: Primary batch with both filters
        primary_batch = await client.get_ops_learning(
            country_id=country_id,
            disaster_type_id=disaster_type_id,
            max_results=20
        )
        
        primary_labeled = [
            {**l, "source_note": "This insight was built off similar disasters from the same country."}
            for l in primary_batch
        ]
        
        # Track appeal codes to avoid duplicates
        seen_appeal_codes: Set[str] = set()
        deduplicated_results = []
        
        # Add primary results and track their appeal codes
        for learning in primary_labeled:
            appeal_info = learning.get('appeal', {})
            if isinstance(appeal_info, dict):
                appeal_code = appeal_info.get('code')
            else:
                appeal_code = str(appeal_info) if appeal_info else None
            
            if appeal_code and appeal_code not in seen_appeal_codes:
                seen_appeal_codes.add(appeal_code)
                deduplicated_results.append(learning)
            elif not appeal_code:
                deduplicated_results.append(learning)
        
        # STAGE 2: If we need more results, fetch country-only data (any disaster type)
        if len(deduplicated_results) < target_count:
            remaining_needed = target_count - len(deduplicated_results)
            secondary_batch = await client.get_ops_learning(
                country_id=country_id,
                disaster_type_id=None,  # No disaster type filter - get any disaster type for this country
                max_results=remaining_needed
            )
            
            secondary_labeled = [
                {**l, "source_note": "This insight was built off similar disasters from the same country."}
                for l in secondary_batch
            ]
            
            # Add secondary results, avoiding duplicates
            for learning in secondary_labeled:
                if len(deduplicated_results) >= target_count:
                    break
                    
                appeal_info = learning.get('appeal', {})
                if isinstance(appeal_info, dict):
                    appeal_code = appeal_info.get('code')
                else:
                    appeal_code = str(appeal_info) if appeal_info else None
                
                if appeal_code and appeal_code not in seen_appeal_codes:
                    seen_appeal_codes.add(appeal_code)
                    deduplicated_results.append(learning)
                elif not appeal_code and len(deduplicated_results) < target_count:
                    deduplicated_results.append(learning)
        
        return deduplicated_results[:target_count]
    
    async def _fetch_events_from_ops_learning(
        self, 
        client: IFRCAPIClient, 
        ops_learning_data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Fetch events directly using event_details.id from ops learning data."""
        from typing import Set
        
        events = []
        seen_event_ids: Set[int] = set()
        
        if not ops_learning_data:
            return events
        
        for learning in ops_learning_data:
            if not isinstance(learning, dict):
                continue
                
            appeal_info = learning.get('appeal', {})
            if not isinstance(appeal_info, dict):
                continue
                
            event_details = appeal_info.get('event_details', {})
            if not isinstance(event_details, dict):
                continue
                
            event_id = event_details.get('id')
            appeal_code = appeal_info.get('code')
            
            if not event_id or event_id in seen_event_ids:
                continue
                
            seen_event_ids.add(event_id)
            
            # Fetch event directly by ID
            try:
                event = await client.get_event_detail(event_id)
                
                if event:
                    # Add source information to the event
                    event["source_note"] = f"Event from ops learning (Appeal: {appeal_code}, Event ID: {event_id})"
                    event["appeal_source"] = appeal_code
                    event["event_source_id"] = event_id
                    events.append(event)
                    
            except Exception:
                continue  # Skip failed requests
        
        return events


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
        cached_result = BaseAITask.get_cached_result(cache_key)
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
            dref_summary_task = DrefSummaryTask()
            summaries = dref_summary_task.generate_dref_summaries(dref_dict)
            
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
            BaseAITask.set_cached_result(cache_key, summary_data)
            
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
        cached_result = BaseAITask.get_cached_result(cache_key)
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
            dref_summary_task = DrefSummaryTask()
            situational_overview = dref_summary_task.generate_situational_overview(latest_update_dict)
            
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
            BaseAITask.set_cached_result(cache_key, response_data)
            
            # Return serialized response (exact same as original)
            serializer = PerDrefSituationalOverviewSerializer(response_data)
            return Response(serializer.data, status=drf_status.HTTP_200_OK)
