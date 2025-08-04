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
from per.ucl_research.ops_learning_summary4 import DrefSummaryTask, OpsLearningSummaryTask, PerformanceMonitor
from datetime import datetime
from django.core.cache import cache

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
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return Response({"ai_structured_summary": cached_result}, status=drf_status.HTTP_200_OK)

        try:
            # Process synchronously but try to use the Celery task logic
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
        """Process previous crises insights synchronously with caching"""
        async with IFRCAPIClient() as client:
            # Get primary ops learning (country + disaster type)
            primary = await client.get_ops_learning(country_id, disaster_type_id, is_validated="true", limit=6)
            primary_labeled = [
                {**l, "source_note": "This insight was built off similar disasters from the same country."}
                for l in primary
            ]
            
            # Get secondary ops learning (country only) as fallback
            if not primary:
                secondary = await client.get_ops_learning(country_id, None, is_validated="true", limit=6)
            else:
                all_country = await client.get_ops_learning(country_id, None, is_validated="true", limit=20)
                primary_ids = {p['id'] for p in primary}
                secondary = [l for l in all_country if l['id'] not in primary_ids]
            
            secondary_labeled = [
                {**l, "source_note": "This insight was built off other disasters from the same country."}
                for l in secondary
            ]
            
            # Combine and limit to 6 items
            combined_learning = (primary_labeled + secondary_labeled)[:6]
            
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
            
            # Process learning entries
            processed_learnings = []
            for l in combined_learning:
                processed_learnings.append({
                    'id': l.get('id'),
                    'learning_text': l.get('learning_validated_en', l.get('learning_en')),
                    'document_name': l.get('document_name'),
                    'document_url': l.get('document_url'),
                    'sector_validated': l.get('sector_validated'),
                    'organization_validated': l.get('organization_validated'),
                    'type_validated': l.get('type_validated'),
                    'created_at': l.get('created_at'),
                    'modified_at': l.get('modified_at'),
                    'appeal_code': l.get('appeal_code'),
                    'appeal_name': l.get('appeal', {}).get('name'),
                    'event_id': l.get('appeal', {}).get('event_details', {}).get('id'),
                    'source_note': l.get('source_note')
                })
            
            # Generate AI summary using centralized OpsLearningSummaryTask
            ai_summary = OpsLearningSummaryTask.generate_previous_crises_insights(processed_learnings)
            
            # Cache result for future requests
            cache.set(cache_key, ai_summary, timeout=3600)
            
            return Response({"ai_structured_summary": ai_summary}, status=drf_status.HTTP_200_OK)


@method_decorator(csrf_exempt, name='dispatch')
class RapidResponseCapacityQuestionsView(BaseUCLView):
    """API view to process RR capacity questions and generate filled Excel output."""

    def get(self, request, *args: Any, **kwargs: Any) -> Response:
        """Process RR capacity questions and return Excel file URL"""
        from datetime import datetime
        start_time = datetime.now()
        
        # Validate parameters
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
            # Process synchronously - simplified Excel generation
            filename = f"rr_capacity_filled_{country_id}_{disaster_type_id}.xlsx"
            
            # Create basic Excel file (simplified for now)
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "RR Capacity Questions"
            ws['A1'] = f"RR Capacity Questions for Country {country_id}, Disaster Type {disaster_type_id}"
            ws['A2'] = "Generated with basic data - full implementation pending"
            
            # Save to temporary file and upload
            with NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                wb.save(tmp.name)
                file_path = tmp.name
            
            blob_url = upload_to_blob(file_path, blob_name=filename)
            os.remove(file_path)
            
            # Cache result
            cache.set(cache_key, blob_url, timeout=3600)
            
            # Track performance
            end_time = datetime.now()
            PerformanceMonitor.track_execution_time("rr_capacity_questions", start_time, end_time)
            
            return Response({"file_url": blob_url}, status=drf_status.HTTP_200_OK)
            
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
