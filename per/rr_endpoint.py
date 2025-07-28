"""
rr_endpoint.py
================

Enhanced RR Form Generation System that creates comprehensive rapid response 
form suggestions based on analysis of similar historical events. The system 
generates AI-powered recommendations for different sections of the RR form 
including situation analysis, response strategy, resource requirements, and 
timeline suggestions.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from .azure_client_2 import AzureServiceClient


class RRFormView(APIView):
    """Enhanced API view that generates comprehensive RR form suggestions based on historical events.

    This view creates an Excel file with multiple sheets containing:
    1. Historical Events Data - Raw data from similar past events
    2. AI-Generated Suggestions - Evidence-based recommendations for RR form sections
    3. Summary Analysis - Key insights and patterns from historical data
    """

    MAX_EVENTS: int = 6

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.base_url: str = "https://goadmin.ifrc.org/api/v2"
        self.azure_client = AzureServiceClient()

    def get(self, request, *args: Any, **kwargs: Any) -> Response:
        """Generate comprehensive RR form with AI-powered suggestions."""
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

        # Fetch and select events with improved strategy
        primary_events = self._fetch_events(country_id, disaster_type_id, limit=2)
        country_events = self._fetch_events_by_country_only(country_id, limit=self.MAX_EVENTS * 2)

        selected_events = self._select_events_strategically(primary_events, country_events)

        if not selected_events:
            return Response(
                {"detail": "No events found for the specified country/disaster type."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Aggregate data for each event
        records = self._build_event_records(selected_events)
        
        # Generate AI-powered form suggestions
        form_suggestions = self._generate_form_suggestions(records)

        # Create comprehensive Excel file
        workbook = self._build_comprehensive_excel(records, form_suggestions, country_id, disaster_type_id)
        
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        filename = f"rr_form_suggestions_{country_id}_{disaster_type_id}.xlsx"
        response["Content-Disposition"] = f"attachment; filename={filename}"
        workbook.save(response)
        return response

    def _select_events_strategically(self, primary_events: List[Dict[str, Any]], country_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Strategically select events ensuring at least 1 similar disaster type + country match."""
        selected_events: List[Dict[str, Any]] = []
        
        # Prioritize events with matching disaster type + country
        if primary_events:
            selected_events.extend(primary_events[:2])  # Take up to 2 primary matches
        
        # Add diverse country events (different disaster types for variety)
        primary_event_ids = {e.get("id") for e in selected_events}
        
        for event in country_events:
            if event.get("id") not in primary_event_ids and len(selected_events) < self.MAX_EVENTS:
                selected_events.append(event)
        
        return selected_events

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

    def _build_event_records(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build comprehensive records for all selected events."""
        records: List[Dict[str, Any]] = []
        for event in events:
            try:
                record = self._build_event_record(event)
                records.append(record)
            except Exception:
                continue
        return records

    def _build_event_record(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Build a comprehensive record for a single event."""
        event_id = event.get("id")
        if not event_id:
            return event

        # Start with basic event data - safely handle None values
        def safe_get_num(data: Dict[str, Any], key: str, default: int = 0) -> int:
            value = data.get(key, default)
            return value if value is not None else default
            
        record = {
            "event_id": event_id,
            "event_name": event.get("name", ""),
            "disaster_type": event.get("dtype", {}).get("name", ""),
            "country": event.get("countries", [{}])[0].get("name", "") if event.get("countries") else "",
            "disaster_start_date": event.get("disaster_start_date", ""),
            "num_affected": safe_get_num(event, "num_affected"),
            "amount_funded": safe_get_num(event, "amount_funded"),
            "amount_requested": safe_get_num(event, "amount_requested"),
        }

        # Add additional data from various APIs
        record.update({
            "field_reports": self._fetch_field_reports(event_id),
            "personnel": self._fetch_personnel(event_id),
            "eru_deployments": self._fetch_eru_deployments(event_id),
            "situation_reports": self._fetch_situation_reports(event_id),
        })

        return record

    def _fetch_field_reports(self, event_id: int) -> List[Dict[str, Any]]:
        """Fetch field reports for an event."""
        try:
            url = f"{self.base_url}/field-report/"
            params = {"event": event_id, "limit": 10}
            response = httpx.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception:
            return []

    def _fetch_personnel(self, event_id: int) -> List[Dict[str, Any]]:
        """Fetch personnel deployments for an event."""
        try:
            url = f"{self.base_url}/personnel_by_event/"
            params = {"event": event_id, "limit": 10}
            response = httpx.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception:
            return []

    def _fetch_eru_deployments(self, event_id: int) -> List[Dict[str, Any]]:
        """Fetch ERU deployments for an event."""
        try:
            url = f"{self.base_url}/deployed_eru_by_event/"
            params = {"event": event_id, "limit": 10}
            response = httpx.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception:
            return []

    def _fetch_situation_reports(self, event_id: int) -> List[Dict[str, Any]]:
        """Fetch situation reports for an event."""
        try:
            url = f"{self.base_url}/situation_report/"
            params = {"event": event_id, "limit": 5}
            response = httpx.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception:
            return []

    def _generate_form_suggestions(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate AI-powered suggestions for different RR form sections."""
        if not records:
            return {}

        suggestions = {
            "situation_analysis": None,
            "response_strategy": None,
            "resource_recommendations": None,
            "timeline_suggestions": None,
            "key_considerations": None,
            "lessons_learned": None
        }

        # Generate AI suggestions using the enhanced Azure client
        suggestions["situation_analysis"] = self.azure_client.generate_situation_analysis(records)
        suggestions["response_strategy"] = self.azure_client.generate_response_strategy(records)
        suggestions["resource_recommendations"] = self.azure_client.generate_resource_recommendations(records)
        suggestions["timeline_suggestions"] = self.azure_client.generate_timeline_suggestions(records)
        
        # Generate additional insights
        suggestions["key_considerations"] = self._generate_key_considerations(records)
        suggestions["lessons_learned"] = self._extract_lessons_learned(records)

        return suggestions

    def _generate_key_considerations(self, records: List[Dict[str, Any]]) -> Optional[str]:
        """Generate key considerations from historical data."""
        try:
            return self.azure_client.generate_key_considerations(records)
        except Exception:
            return "Key considerations based on historical patterns should be analyzed."

    def _extract_lessons_learned(self, records: List[Dict[str, Any]]) -> Optional[str]:
        """Extract lessons learned from historical events."""
        try:
            return self.azure_client.extract_lessons_learned(records)
        except Exception:
            return "Lessons learned from similar past events should be reviewed."

    def _build_comprehensive_excel(self, records: List[Dict[str, Any]], suggestions: Dict[str, Any], country_id: int, disaster_type_id: int) -> Workbook:
        """Build the comprehensive Excel workbook with all sheets."""
        wb = Workbook()
        
        # Remove default sheet
        if "Sheet" in wb.sheetnames:
            wb.remove(wb["Sheet"])
        
        # Create all sheets
        self._create_suggestions_sheet(wb, suggestions, country_id, disaster_type_id)
        self._create_historical_data_sheet(wb, records)
        self._create_summary_analysis_sheet(wb, records, suggestions)
        
        return wb

    def _generate_key_considerations(self, records: List[Dict[str, Any]]) -> str:
        """Generate key considerations based on historical patterns."""
        considerations = []
        
        # Analyze severity patterns
        severity_levels = [r.get('severity_level') for r in records if r.get('severity_level')]
        if severity_levels:
            considerations.append(f"Severity patterns: {', '.join(set(severity_levels))}")
        
        # Analyze affected populations
        affected_numbers = [r.get('num_affected') for r in records if r.get('num_affected')]
        if affected_numbers:
            avg_affected = sum(affected_numbers) // len(affected_numbers)
            considerations.append(f"Average affected population: {avg_affected:,} people")
        
        # Analyze response patterns
        eru_types = [r.get('eru_type') for r in records if r.get('eru_type')]
        if eru_types:
            considerations.append(f"Common ERU deployments: {'; '.join(set(eru_types))}")
        
        return "; ".join(considerations) if considerations else "No specific patterns identified from historical data."

    def _extract_lessons_learned(self, records: List[Dict[str, Any]]) -> str:
        """Extract key lessons from past events."""
        lessons = []
        
        # Analyze response timing
        rapid_responses = [r for r in records if r.get('field_report_date') and r.get('disaster_start_date')]
        if rapid_responses:
            lessons.append("Early field reporting correlates with more effective responses")
        
        # Analyze funding patterns
        well_funded = [r for r in records if r.get('appeal_amount_funded', 0) > r.get('appeal_amount_requested', 0) * 0.8]
        if well_funded:
            lessons.append(f"{len(well_funded)}/{len(records)} similar events achieved >80% funding")
        
        # Analyze volunteer engagement
        high_volunteer = [r for r in records if r.get('num_volunteers', 0) > 100]
        if high_volunteer:
            lessons.append("High volunteer engagement observed in similar contexts")
        
        return "; ".join(lessons) if lessons else "Limited historical data available for lesson extraction."

    def _build_comprehensive_excel(self, records: List[Dict[str, Any]], suggestions: Dict[str, Any], country_id: int, disaster_type_id: int) -> Workbook:
        """Create a comprehensive Excel workbook with multiple sheets."""
        wb = Workbook()
        
        # Remove default sheet
        wb.remove(wb.active)
        
        # Create sheets
        self._create_suggestions_sheet(wb, suggestions, country_id, disaster_type_id)
        self._create_historical_data_sheet(wb, records)
        self._create_summary_analysis_sheet(wb, records, suggestions)
        
        return wb

    def _create_suggestions_sheet(self, wb: Workbook, suggestions: Dict[str, Any], country_id: int, disaster_type_id: int) -> None:
        """Create the main RR Form Suggestions sheet."""
        ws = wb.create_sheet("RR Form Suggestions", 0)
        
        # Define styling
        header_font = Font(bold=True, size=14, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        section_font = Font(bold=True, size=12, color="FFFFFF")
        section_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
        
        row = 1
        
        # Header
        ws.merge_cells(f'A{row}:D{row}')
        cell = ws[f'A{row}']
        cell.value = f"IFRC Rapid Response Form Suggestions - Country: {country_id}, Disaster Type: {disaster_type_id}"
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        row += 2
        
        # RR Form Sections with AI suggestions
        sections = [
            ("Situation Analysis", suggestions.get("situation_analysis", "No analysis available")),
            ("Response Strategy", suggestions.get("response_strategy", "No strategy available")),
            ("Resource Requirements", suggestions.get("resource_recommendations", "No recommendations available")),
            ("Timeline & Milestones", suggestions.get("timeline_suggestions", "No timeline available")),
            ("Key Considerations", suggestions.get("key_considerations", "No considerations available")),
            ("Lessons Learned", suggestions.get("lessons_learned", "No lessons available"))
        ]
        
        for section_title, content in sections:
            # Section header
            ws.merge_cells(f'A{row}:D{row}')
            cell = ws[f'A{row}']
            cell.value = section_title
            cell.font = section_font
            cell.fill = section_fill
            row += 1
            
            # Content
            ws.merge_cells(f'A{row}:D{row+2}')
            cell = ws[f'A{row}']
            cell.value = content or f"No {section_title.lower()} generated - AI service may not be configured"
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[row].height = 60
            row += 4
        
        # Adjust column widths
        ws.column_dimensions['A'].width = 20
        ws.column_dimensions['B'].width = 20
        ws.column_dimensions['C'].width = 20
        ws.column_dimensions['D'].width = 20

    def _create_historical_data_sheet(self, wb: Workbook, records: List[Dict[str, Any]]) -> None:
        """Create the Historical Events Data sheet."""
        ws = wb.create_sheet("Historical Events Data", 1)
        
        if not records:
            ws['A1'] = "No historical events data available"
            return
        
        # Headers
        headers = [
            "Event ID", "Event Name", "Disaster Type", "Start Date", "Countries",
            "Affected", "Injured", "Dead", "Displaced", "Assisted",
            "Appeal Code", "Amount Requested", "Amount Funded", 
            "Volunteers", "Local Staff", "ERU Types", "Actions Taken"
        ]
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True)
        
        # Data rows
        for row, record in enumerate(records, 2):
            data = [
                record.get("event_id"),
                record.get("event_name"),
                record.get("disaster_type"),
                record.get("disaster_start_date"),
                record.get("country_names"),
                record.get("num_affected"),
                record.get("num_injured"),
                record.get("num_dead"),
                record.get("num_displaced"),
                record.get("num_assisted"),
                record.get("appeal_code"),
                record.get("appeal_amount_requested"),
                record.get("appeal_amount_funded"),
                record.get("num_volunteers"),
                record.get("num_localstaff"),
                record.get("eru_type"),
                record.get("actions_taken")
            ]
            
            for col, value in enumerate(data, 1):
                ws.cell(row=row, column=col, value=value)

    def _create_summary_analysis_sheet(self, wb: Workbook, records: List[Dict[str, Any]], suggestions: Dict[str, Any]) -> None:
        """Create the Summary Analysis sheet with key statistics."""
        ws = wb.create_sheet("Summary Analysis", 2)
        
        row = 1
        
        # Title
        ws.merge_cells(f'A{row}:C{row}')
        cell = ws[f'A{row}']
        cell.value = "Summary Analysis of Historical Events"
        cell.font = Font(bold=True, size=14)
        row += 2
        
        if not records:
            ws[f'A{row}'] = "No data available for analysis"
            return
        
        # Statistics - Handle None values safely
        def safe_get_number(record: Dict[str, Any], key: str, default: int = 0) -> int:
            """Safely get a numeric value, handling None explicitly."""
            value = record.get(key, default)
            return value if value is not None else default
        
        total_affected = sum(safe_get_number(r, "num_affected") for r in records)
        avg_affected = total_affected // len(records) if records and total_affected > 0 else 0
        
        stats = [
            ("Total Events Analyzed", len(records)),
            ("Events with Exact Match", len([r for r in records if r.get("disaster_type")])),
            ("Average Affected Population", avg_affected),
            ("Events with Appeals", len([r for r in records if r.get("appeal_code")])),
            ("Events with ERU Deployment", len([r for r in records if r.get("eru_type")])),
            ("Events with Field Reports", len([r for r in records if r.get("field_report_date")])),
        ]
        
        for label, value in stats:
            ws[f'A{row}'] = label
            ws[f'B{row}'] = value
            ws[f'A{row}'].font = Font(bold=True)
            row += 1