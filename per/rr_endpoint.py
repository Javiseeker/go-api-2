"""
rr_endpoint.py
==============

RR Capacity Questions processing endpoint.
Loads JSON data, fills missing fields using response generation service, generates Excel output.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from .azure_client_2 import AzureServiceClient


class RRCapacityQuestionsView(APIView):
    """API view to process RR capacity questions and generate filled Excel output."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.base_url: str = "https://goadmin.ifrc.org/api/v2"
        self.response_service = AzureServiceClient()

    def get(self, request, *args: Any, **kwargs: Any) -> Response:
        """Process RR capacity questions and generate filled Excel file."""
        country_param = request.query_params.get("country")
        dtype_param = request.query_params.get("disaster_type")

        # Validate required parameters
        if not country_param or not dtype_param:
            return Response(
                {"detail": 'Both "country" and "disaster_type" query parameters are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            country_id = int(country_param)
            disaster_type_id = int(dtype_param)
        except ValueError:
            return Response(
                {"detail": '"country" and "disaster_type" must be integer IDs.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Load the parsed questions data
            questions_data = self._load_questions_data()
            
            # Fetch relevant events for context
            events_data = self._fetch_relevant_events(country_id, disaster_type_id)
            
            # Process questions and fill missing fields
            processed_questions = self._process_questions(questions_data, events_data)
            
            # Generate Excel file
            workbook = self._create_rr_capacity_excel(processed_questions, country_id, disaster_type_id)
            
            response = HttpResponse(
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            filename = f"rr_capacity_filled_{country_id}_{disaster_type_id}.xlsx"
            response["Content-Disposition"] = f"attachment; filename={filename}"
            workbook.save(response)
            return response
            
        except FileNotFoundError:
            return Response(
                {"detail": "RR capacity questions data file not found. Please ensure rr_parsed_excel.json exists."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"detail": f"Error processing RR capacity questions: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _load_questions_data(self) -> List[Dict[str, Any]]:
        """Load the parsed questions data from JSON file."""
        with open('rr_parsed_excel.json', 'r', encoding='utf-8') as f:
            return json.load(f)

    def _fetch_relevant_events(self, country_id: int, disaster_type_id: int) -> List[Dict[str, Any]]:
        """Fetch relevant events for context."""
        # Fetch events with matching country and disaster type
        primary_events = self._fetch_events(country_id, disaster_type_id, limit=3)
        
        # Also fetch events from the same country for broader context
        country_events = self._fetch_events_by_country_only(country_id, limit=6)
        
        # Combine and deduplicate
        all_events = []
        event_ids = set()
        
        for event in primary_events + country_events:
            if event.get('id') not in event_ids:
                all_events.append(event)
                event_ids.add(event.get('id'))
        
        return all_events[:6]  # Limit to 6 events for context

    def _fetch_events(self, country_id: int, disaster_type_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch events filtered by both country and disaster type."""
        params: Dict[str, Any] = {
            "countries__in": country_id, 
            "dtype": disaster_type_id,
            "limit": limit, 
            "ordering": "-disaster_start_date"
        }
        url = f"{self.base_url}/event/"
        try:
            response = httpx.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception:
            return []

    def _fetch_events_by_country_only(self, country_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch events filtered only by country for broader context."""
        params: Dict[str, Any] = {"countries__in": country_id, "limit": limit, "ordering": "-disaster_start_date"}
        url = f"{self.base_url}/event/"
        try:
            response = httpx.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception:
            return []

    def _process_questions(self, questions_data: List[Dict[str, Any]], events_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process each question and fill missing fields using response generation service."""
        processed_questions = []
        
        for question in questions_data:
            # Create a copy of the question
            processed_question = question.copy()
            
            # Check if any of the target fields are missing or null (excluding Status)
            needs_processing = (
                not question.get("Notes on Response include the source") or 
                str(question.get("Notes on Response include the source")).lower() in ['nan', 'null', 'none', ''] or
                not question.get("Recommended actions for continuation of response") or 
                str(question.get("Recommended actions for continuation of response")).lower() in ['nan', 'null', 'none', '']
            )
            
            if needs_processing:
                try:
                    # Use response service to fill missing fields
                    generated_responses = self.response_service.process_capacity_question(question, events_data)
                    
                    # Update the question with generated responses
                    for field_name, response in generated_responses.items():
                        if response and field_name in processed_question:
                            # Only update if the original field was empty/null
                            original_value = question.get(field_name)
                            if not original_value or str(original_value).lower() in ['nan', 'null', 'none', '']:
                                processed_question[field_name] = response
                        
                except Exception as e:
                    # If processing fails, add error notes (excluding Status)
                    if not processed_question.get("Notes on Response include the source") or str(processed_question.get("Notes on Response include the source")).lower() in ['nan', 'null', 'none', '']:
                        processed_question["Notes on Response include the source"] = f"Processing failed: {str(e)}"
                    if not processed_question.get("Recommended actions for continuation of response") or str(processed_question.get("Recommended actions for continuation of response")).lower() in ['nan', 'null', 'none', '']:
                        processed_question["Recommended actions for continuation of response"] = "Manual assessment required due to processing error."
            
            processed_questions.append(processed_question)
        
        return processed_questions

    def _create_rr_capacity_excel(self, questions_data: List[Dict[str, Any]], country_id: int, disaster_type_id: int) -> Workbook:
        """Create Excel file with the same structure as the original, but with filled data."""
        wb = Workbook()
        ws = wb.active
        ws.title = "RR Capacity Assessment"
        
        # Define headers (same as original structure)
        headers = [
            "Area",
            "Critical Questions", 
            "Guiding/probing questions",
            "Notes on Response include the source",
            "Status",
            "Recommended actions for continuation of response",
            "Examples of recommended actions",
            "References"
        ]
        
        # Style the headers
        header_font = Font(bold=True, size=12, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        
        # Add title row
        ws.merge_cells('A1:H1')
        title_cell = ws['A1']
        title_cell.value = f"RR Capacity Assessment - Country: {country_id}, Disaster Type: {disaster_type_id}"
        title_cell.font = Font(bold=True, size=14, color="FFFFFF")
        title_cell.fill = PatternFill(start_color="203764", end_color="203764", fill_type="solid")
        title_cell.alignment = Alignment(horizontal="center")
        
        # Add headers in row 3 (same as original Excel structure)
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=3, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(wrap_text=True, horizontal="center")
        
        # Add data rows
        for row_idx, question in enumerate(questions_data, 4):  # Start from row 4
            for col_idx, header in enumerate(headers, 1):
                value = question.get(header, "")
                
                # Handle NaN values
                if str(value).lower() == 'nan':
                    value = ""
                
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                
                # Set row height for better readability
                ws.row_dimensions[row_idx].height = 60
        
        # Adjust column widths
        column_widths = [25, 35, 30, 40, 15, 40, 30, 20]  # Adjust based on content
        column_letters = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
        for col_idx, width in enumerate(column_widths):
            if col_idx < len(column_letters):
                ws.column_dimensions[column_letters[col_idx]].width = width
        
        return wb