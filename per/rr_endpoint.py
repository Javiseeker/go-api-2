"""
rr_endpoint.py
==============

RR Capacity Questions processing endpoint.
Loads rr_parsed_excel.json data, fills only the 'Notes on Response Capacity with sources' field using AI service, generates Excel output.

Updates:
- Uses GO's correct ops-learning API filters: appeal_code__country and appeal_code__dtype__in
- Implements two-stage fetch strategy with deduplication by appeal code
- Caps results via limit parameter for optimal performance
- Events are fetched ONLY from appeal codes in ops-learning data (no historical events)
- Default target: 20 ops-learning items with appeal-driven events only
- Removed all historical event fetching logic (_fetch_relevant_events, _combine_events_data, etc.)
- Uses simple string References format from rr_parsed_excel.json
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
import os
from datetime import datetime
import httpx
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.core.cache import cache
from api.models import Country
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.hyperlink import Hyperlink


from .azure_client_2 import AzureServiceClient

from tempfile import NamedTemporaryFile
from per.blob_upload import upload_to_blob


class RRCapacityQuestionsView(APIView):
    """API view to process RR capacity questions and generate filled Excel output."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.base_url: str = "https://goadmin.ifrc.org/api/v2"
        self.response_service = AzureServiceClient()

    def get(self, request, *args: Any, **kwargs: Any) -> Response:
        country_param = request.query_params.get("country")
        dtype_param = request.query_params.get("disaster_type")

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

        # Step 1: Check Redis cache
        cache_key = f"rr_capacity_excel:{country_id}:{disaster_type_id}"
        cached_url = cache.get(cache_key)
        if cached_url:
            return Response({"file_url": cached_url}, status=status.HTTP_200_OK)

        try:
            questions_data = self._load_questions_data()
            
            # Only fetch events for the deduplicated ops-learning appeal codes
            ops_learning_data = self._fetch_ops_learning_data(country_id, disaster_type_id)
            if not ops_learning_data:
                ops_learning_data = []
            events_data = self._fetch_events_from_ops_learning(ops_learning_data)
            
            # Now events_data is fetched directly using event_details.id from ops learning
            

            
            # Process questions and fill missing fields
            processed_questions = self._process_questions(questions_data, events_data, ops_learning_data)
            
            filename = f"rr_capacity_filled_{country_id}_{disaster_type_id}.xlsx"
            # Generate Excel file with source information
            workbook = self._create_rr_capacity_excel(processed_questions, country_id, disaster_type_id, events_data, ops_learning_data)

            with NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                workbook.save(tmp.name)
                file_path = tmp.name

            blob_url = upload_to_blob(file_path, blob_name=filename)
            os.remove(file_path)

            
            cache.set(cache_key, blob_url, timeout=3600)

            return Response({"file_url": blob_url}, status=status.HTTP_200_OK)

        except FileNotFoundError:
            return Response(
                {"detail": "RR capacity questions data file not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"detail": f"Error processing RR capacity questions: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _load_questions_data(self) -> List[Dict[str, Any]]:
        """Load the parsed questions data from rr_parsed_excel.json with simple string References format."""
        # Get the path relative to the ucl_research folder
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, 'ucl_research', 'rr_parsed_excel.json')
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _fetch_events_from_ops_learning(self, ops_learning_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Fetch events directly using event_details.id from ops learning data."""
        events = []
        seen_event_ids = set()
        
        # Handle None or empty ops_learning_data
        if not ops_learning_data:
            return events
        
        for i, learning in enumerate(ops_learning_data):
            # SAFETY CHECK: Ensure learning is a dictionary
            if not isinstance(learning, dict):
                continue
                
            appeal_info = learning.get('appeal', {})
            if not isinstance(appeal_info, dict):
                continue
                
            # Get event details from appeal
            event_details = appeal_info.get('event_details', {})
            if not isinstance(event_details, dict):
                continue
                
            event_id = event_details.get('id')
            appeal_code = appeal_info.get('code')
            country_info = appeal_info.get('country', {})
            
            if not event_id:
                continue
                
            if event_id in seen_event_ids:
                continue
                
            seen_event_ids.add(event_id)
            
            # Fetch event directly by ID
            try:
                url = f"{self.base_url}/event/{event_id}/"
                response = httpx.get(url, timeout=10.0)
                response.raise_for_status()
                event = response.json()
                
                if event:
                    # Add source information to the event
                    event["source_note"] = f"Event from ops learning (Appeal: {appeal_code}, Event ID: {event_id})"
                    event["appeal_source"] = appeal_code
                    event["event_source_id"] = event_id
                    events.append(event)
                    
            except Exception as e:
                continue  # Skip failed requests
        
        return events

    def _fetch_ops_learning_data(self, country_id: int, disaster_type_id: int, target_count: int = 10) -> List[Dict[str, Any]]:
        """
        Fetch ops-learning data using GO's new API filters with two-stage approach.
        
        STAGE 1: Fetch up to 10 using both appeal_code__country and appeal_code__dtype__in
        STAGE 2: If fewer results than desired, fetch additional using only appeal_code__country
        
        Returns empty list if no data found in either stage.
        Deduplicates by appeal_code to ensure no operation appears twice.
        Caps final results to 10 via limit parameter.
        """
        
        # STAGE 1: Fetch with both country and disaster type filters
        primary_batch = self._fetch_ops_learning(country_id, disaster_type_id, limit=10)
        print(f"=== DEBUG: PRIMARY BATCH ===")
        print(f"Primary batch count: {len(primary_batch)}")
        for i, learning in enumerate(primary_batch[:3]):
            appeal_info = learning.get('appeal', {})
            if isinstance(appeal_info, dict):
                country_info = appeal_info.get('country', {})
                dtype_info = appeal_info.get('dtype', {})
                if isinstance(country_info, dict):
                    country_name = country_info.get('name', 'N/A')
                    country_id_str = str(country_info.get('id', 'N/A'))
                else:
                    country_name = f"Country ID: {country_info}" if country_info else 'N/A'
                    country_id_str = str(country_info) if country_info else 'N/A'
                
                if isinstance(dtype_info, dict):
                    dtype_name = dtype_info.get('name', 'N/A')
                    dtype_id_str = str(dtype_info.get('id', 'N/A'))
                else:
                    dtype_name = f"Disaster Type ID: {dtype_info}" if dtype_info else 'N/A'
                    dtype_id_str = str(dtype_info) if dtype_info else 'N/A'
                
                print(f"Primary {i+1}: Country: {country_name} (ID: {country_id_str}), "
                      f"Disaster Type: {dtype_name} (ID: {dtype_id_str})")
            else:
                # Handle integer appeal IDs in debug output
                print(f"Primary {i+1}: Appeal ID: {appeal_info} (no detailed info available)")
        print("=== END PRIMARY DEBUG ===")
        
        primary_labeled = [
            {**l, "source_note": "This insight was built off similar disasters from the same country."}
            for l in primary_batch
        ]
        
        # Track appeal codes to avoid duplicates
        seen_appeal_codes = set()
        deduplicated_results = []
        
        # Add primary results and track their appeal codes
        for learning in primary_labeled:
            appeal_info = learning.get('appeal', {})
            if isinstance(appeal_info, dict):
                appeal_code = appeal_info.get('code')
            else:
                # Handle integer appeal IDs
                appeal_code = str(appeal_info) if appeal_info else None
            
            if appeal_code and appeal_code not in seen_appeal_codes:
                seen_appeal_codes.add(appeal_code)
                deduplicated_results.append(learning)
            elif not appeal_code:  # Include entries without appeal codes
                deduplicated_results.append(learning)
        
        # STAGE 2: If we need more results, fetch country-only data BUT filter by disaster type in code
        if len(deduplicated_results) < 10:
            remaining_needed = 10 - len(deduplicated_results)
            secondary_batch = self._fetch_ops_learning_by_country_only(country_id, limit=remaining_needed * 4)  # Fetch more for filtering
            
            # Filter secondary batch to only include the specific disaster type
            filtered_secondary = []
            print(f"=== DEBUG: SECONDARY BATCH FILTERING ===")
            print(f"Secondary batch count: {len(secondary_batch)}")
            print(f"Looking for disaster type ID: {disaster_type_id}")
            
            for learning in secondary_batch:
                appeal_info = learning.get('appeal', {})
                if isinstance(appeal_info, dict):
                    appeal_dtype = appeal_info.get('dtype', {})
                    if isinstance(appeal_dtype, dict):
                        dtype_id = appeal_dtype.get('id')
                        country_info = appeal_info.get('country', {})
                        if isinstance(country_info, dict):
                            country_id_str = str(country_info.get('id', 'N/A'))
                        else:
                            country_id_str = str(country_info) if country_info else 'N/A'
                        print(f"Checking: Country ID {country_id_str}, Disaster Type ID {dtype_id}")
                        if dtype_id == disaster_type_id:
                            filtered_secondary.append(learning)
                            print(f"  -> MATCH: Added to filtered results")
                        else:
                            print(f"  -> SKIP: Disaster type mismatch")
                    else:
                        print(f"  -> SKIP: No disaster type info")
                else:
                    print(f"  -> SKIP: No appeal info")
            
            print(f"Filtered secondary count: {len(filtered_secondary)}")
            print("=== END SECONDARY DEBUG ===")
            
            secondary_labeled = [
                {**l, "source_note": "This insight was built off similar disasters from the same country."}
                for l in filtered_secondary
            ]
            
            # Add secondary results, avoiding duplicates by appeal code
            for learning in secondary_labeled:
                if len(deduplicated_results) >= 10:
                    break
                    
                appeal_info = learning.get('appeal', {})
                if isinstance(appeal_info, dict):
                    appeal_code = appeal_info.get('code')
                else:
                    # Handle integer appeal IDs
                    appeal_code = str(appeal_info) if appeal_info else None
                
                if appeal_code and appeal_code not in seen_appeal_codes:
                    seen_appeal_codes.add(appeal_code)
                    deduplicated_results.append(learning)
                elif not appeal_code and len(deduplicated_results) < 10:  # Include entries without appeal codes if space
                    deduplicated_results.append(learning)
        
        # If no data found in either stage, return empty list
        if not deduplicated_results:
            return []
        
        # Cap final results to target count and return
        return deduplicated_results[:10]

    def _fetch_ops_learning(self, country_id: int, disaster_type_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch validated ops-learning data filtered by appeal country and disaster type using GO's correct API filters."""
        params: Dict[str, Any] = {
            "appeal_code__country": str(country_id),
            "appeal_code__dtype__in": str(disaster_type_id),
            "limit": limit,
            "is_validated": "true"
        }
        url = f"{self.base_url}/ops-learning/"
        try:
            response = httpx.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception:
            return []

    def _fetch_ops_learning_by_country_only(self, country_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch validated ops-learning data filtered only by appeal country (fallback strategy)."""
        params: Dict[str, Any] = {
            "appeal_code__country": str(country_id),
            "limit": limit,
            "is_validated": "true"
        }
        url = f"{self.base_url}/ops-learning/"
        try:
            response = httpx.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception:
            return []

    def _process_questions(self, questions_data: List[Dict[str, Any]], events_data: List[Dict[str, Any]], ops_learning_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process each question and fill only the 'Notes on Response Capacity with sources' field using AI service."""
        processed_questions = []
        
        # Handle None values
        if not events_data:
            events_data = []
        if not ops_learning_data:
            ops_learning_data = []
        
        for question in questions_data:
            # Skip non-dictionary entries to avoid .get() errors
            if not isinstance(question, dict):
                continue
                
            # Create a copy of the question
            processed_question = question.copy()
            
            # Check if the Notes on Response field is missing or null (only this field)
            # Handle both old and new field names
            notes_field_new = "Notes on Response Capacity with sources"
            notes_field_old = "Notes on Response include the source"
            
            notes_value = question.get(notes_field_new) or question.get(notes_field_old)
            needs_processing = (
                not notes_value or 
                str(notes_value).lower() in ['nan', 'null', 'none', '']
            )
            
            if needs_processing:
                try:
                    # Use response service to fill missing fields
                    generated_responses = self.response_service.process_capacity_question(question, events_data, ops_learning_data)
                    
                    # Update the question with generated responses
                    for field_name, response in generated_responses.items():
                        if response:
                            # Handle both old and new field names for Notes field
                            if field_name == "Notes on Response Capacity with sources":
                                # Update both field names to ensure compatibility
                                processed_question["Notes on Response Capacity with sources"] = response
                                processed_question["Notes on Response include the source"] = response
                            elif field_name in processed_question:
                                # Only update if the original field was empty/null
                                original_value = question.get(field_name)
                                if not original_value or str(original_value).lower() in ['nan', 'null', 'none', '']:
                                    processed_question[field_name] = response
                        
                except Exception as e:
                    # If processing fails, add error note for Notes on Response field only
                    error_message = f"Processing failed: {str(e)}"
                    processed_question["Notes on Response Capacity with sources"] = error_message
                    processed_question["Notes on Response include the source"] = error_message
            
            processed_questions.append(processed_question)
        
        return processed_questions

    def _create_rr_capacity_excel(self, questions_data: List[Dict[str, Any]], country_id: int, disaster_type_id: int, events_data: Optional[List[Dict[str, Any]]] = None, ops_learning_data: Optional[List[Dict[str, Any]]] = None) -> Workbook:
        """Create Excel file with the same structure as the original, but with filled data and sources."""
        
        wb = Workbook()
        ws = wb.active
        if ws:
            ws.title = "RR Capacity Assessment"
        else:
            ws = wb.create_sheet("RR Capacity Assessment")
        
        # Define headers (same as original structure)
        headers = [
            "Area",
            "Critical Questions", 
            "Guiding/probing questions",
            "Notes on Response Capacity with sources",
            "Status",
            "Recommended actions for continuation of response",
            "Examples of recommended actions",
            "References"
        ]
        
        # Style the headers
        header_font = Font(bold=True, size=11, color="000000")  # Black text, size 11
        header_fill = PatternFill(start_color="D3D3D3", end_color="D3D3D3", fill_type="solid")  # Light grey
        
        # Add main title row
        ws.merge_cells('A1:H1')
        title_cell = ws['A1']
        title_cell.value = "Rapid Response Capacity Check"
        title_cell.font = Font(bold=True, size=20, color="FFFFFF")  # White text, size 20
        title_cell.fill = PatternFill(start_color="000000", end_color="000000", fill_type="solid")  # Black background
        title_cell.alignment = Alignment(horizontal="center")
        ws.row_dimensions[1].height = 34  # Title row height
        
        # Add Country and Date identifiers in row 2
        country_cell = ws['A2']
        country_cell.value = "Country:"
        country_cell.font = Font(bold=True, size=20, color="000000")  # Black text, size 20
        country_cell.alignment = Alignment(horizontal="left")
        
        # Get country name from country_id
        try:
            country = get_object_or_404(Country, id=country_id)
            country_name = country.name
        except:
            country_name = f"Country ID: {country_id}"
        
        country_value_cell = ws['B2']
        country_value_cell.value = country_name
        country_value_cell.font = Font(size=20, color="000000")  # Size 20, black, not bold
        country_value_cell.alignment = Alignment(horizontal="left")
        
        date_cell = ws['D2']
        date_cell.value = "Date:"
        date_cell.font = Font(bold=True, size=20, color="000000")  # Black text, size 20
        date_cell.alignment = Alignment(horizontal="left")
        
        # Add current date
        current_date = datetime.now().strftime("%d %B %Y")
        date_value_cell = ws['E2']
        date_value_cell.value = current_date
        date_value_cell.font = Font(size=20, color="000000")  # Size 20, black, not bold
        date_value_cell.alignment = Alignment(horizontal="left")
        ws.row_dimensions[2].height = 26  # Country and date row height
        
        # Add headers in row 3 (same as original Excel structure)
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=3, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
        ws.row_dimensions[3].height = 31  # Header row height
        
        # Add data rows with area-block merging
        row_idx = 4  # first data row (after headers)
        area_start = row_idx
        current_area = None
        merged_areas = []  # Track merged areas for styling

        # First pass: write all cells and track area changes
        for question in questions_data:
            # Skip non-dictionary entries to avoid .get() errors
            if not isinstance(question, dict):
                continue
                
            area = question.get("Area", "")
            
            # If area is null/empty, use the previous area
            if not area or area == "null" or str(area).lower() == 'nan':
                area = current_area  # Keep using the previous area
            
            # Extract main area name for comparison (to handle variations in description text)
            if area:
                current_main_area = area.split('\n')[0].strip()
            else:
                current_main_area = None
                
            if current_area:
                previous_main_area = current_area.split('\n')[0].strip()
            else:
                previous_main_area = None
            
            # Check if we're starting a new area (compare main area names)
            if current_main_area != previous_main_area:
                if current_area is not None:  # close previous block
                    ws.merge_cells(start_row=area_start, start_column=1,
                                    end_row=row_idx-1, end_column=1)
                    # Track this merged area for styling
                    merged_areas.append({
                        'start_row': area_start,
                        'end_row': row_idx - 1,
                        'area': current_area
                    })
                area_start = row_idx
                # Use the first complete area text we encounter for this area group
                if current_area is None or previous_main_area != current_main_area:
                    current_area = area

            # write the cells exactly as before
            for col_idx, header in enumerate(headers, 1):
                value = question.get(header, "")
                
                # Handle NaN values
                if str(value).lower() == 'nan':
                    value = ""
                
                # Final safety check - ensure value is not a list (for any column)
                if isinstance(value, list):
                    # Convert list to string representation
                    if value:
                        # Special handling for References column (column 8) with structured data
                        if col_idx == 8:
                            reference_strings = []
                            for item in value:
                                if isinstance(item, dict):
                                    text = item.get('text', '')
                                    url = item.get('url', '')
                                    if url:
                                        reference_strings.append(f"{text} ({url})")
                                    else:
                                        reference_strings.append(text)
                                else:
                                    reference_strings.append(str(item))
                            value = '\n'.join(reference_strings)
                        else:
                            value = '\n'.join([str(item) for item in value])
                    else:
                        value = ""
                
                # Handle line breaks in all text columns
                if value and isinstance(value, str):
                    # Replace \n\n with actual line breaks for proper display
                    value = value.replace('\\n\\n', '\n')
                    # Also handle single \n if present
                    value = value.replace('\\n', '\n')
                

                

                
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                cell.font = Font(size=11, color="000000")  # Black text, size 11
                

                
                # Clean any remaining markdown formatting from all text
                if value and isinstance(value, str) and "**" in value:
                    # Apply the same cleaning logic as in azure_client_2.py
                    import re
                    
                    # Handle the specific pattern "**- Operational capacity:**" first
                    value = re.sub(r'\*\*-\s*([^:]+):\*\*', r'- \1:', value)
                    
                    # Handle other variations of the pattern
                    value = re.sub(r'-\s*\*\*([^:]+):\*\*', r'- \1:', value)  # - **text:** becomes - text:
                    value = re.sub(r'\*\*([^:]+):\*\*', r'\1:', value)  # **text:** becomes text:
                    
                    # Remove any remaining markdown bold formatting
                    value = re.sub(r'\*\*([^*]+)\*\*', r'\1', value)  # **text** becomes text
                    
                    # Remove any remaining asterisks used for emphasis
                    value = re.sub(r'\*([^*]+)\*', r'\1', value)  # *text* becomes text
                    value = re.sub(r'_([^_]+)_', r'\1', value)  # _text_ becomes text
                    
                    # Final cleanup of any remaining asterisks
                    value = value.replace('**', '')
                    
                    # Update the cell value with cleaned text
                    cell.value = value
                
                # Set row height for better readability
                ws.row_dimensions[row_idx].height = 60
            
            row_idx += 1

        # merge the final block
        if current_area is not None:
            ws.merge_cells(start_row=area_start, start_column=1,
                            end_row=row_idx-1, end_column=1)
            # Track this merged area for styling
            merged_areas.append({
                'start_row': area_start,
                'end_row': row_idx - 1,
                'area': current_area
            })
            
            # Style the merged area cells with appropriate colors
            for merged_area in merged_areas:
                area_color = self._get_area_color(merged_area['area'])
                # Style the first cell of each merged area
                area_cell = ws.cell(merged_area['start_row'], 1)
                # Center both horizontally and vertically, with text wrapping
                area_cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
                area_cell.fill = PatternFill(start_color=area_color, end_color=area_color, fill_type="solid")
                
                # Apply formatting for area cells
                area_text = merged_area['area']
                if area_text and '\n' in area_text:
                    # Split into main area name and description
                    parts = area_text.split('\n', 1)
                    main_area = parts[0].strip()
                    description = parts[1].strip() if len(parts) > 1 else ""
                    
                    # Show both main area and description
                    # Replace \n\n with \n for better display
                    full_text = f"{main_area}\n{description}".replace('\n\n', '\n')
                    area_cell.value = full_text
                    area_cell.font = Font(bold=True, size=11)
                else:
                    # If no description, just make the whole text bold, size 11
                    area_cell.value = area_text
                    area_cell.font = Font(bold=True, size=11)
                
                # Ensure all cells in the merged area are properly aligned
                for row in range(merged_area['start_row'], merged_area['end_row'] + 1):
                    cell = ws.cell(row=row, column=1)
                    cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        
        # Adjust column widths
        column_widths = [25, 35, 30, 40, 15, 40, 30, 20]  # Adjust based on content
        column_letters = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
        for col_idx, width in enumerate(column_widths):
            if col_idx < len(column_letters):
                ws.column_dimensions[column_letters[col_idx]].width = width
        
        # Create Sources sheet with appeal-driven events and ops learning data
        self._add_sources_sheet(wb, events_data, ops_learning_data)
        
        return wb

    def _get_area_color(self, area: str) -> str:
        """Get the appropriate pastel color for each area based on the image."""
        if not area:
            return "FFFFFF"  # White for null areas
        
        # Extract the main area name (before the first newline)
        area_name = area.split('\n')[0].strip()
        
        # Color mapping based on the image description
        area_colors = {
            "Policy, Strategy and Standards": "E6E6FA",  # Light lavender/purple
            "Analysis and Planning": "FFFACD",           # Light yellow/cream
            "Operational Capacity": "E6F3FF",           # Light blue
            "Coordination": "E6FFE6",                   # Light green
            "Operations Support": "FFE6F0"              # Light pink/rose
        }
        
        return area_colors.get(area_name, "FFFFFF")  # Default to white if not found



    def _process_markdown_bold(self, text: str) -> tuple[str, list]:
        """Process markdown-style bold formatting (**text**) and return cleaned text and bold parts."""
        import re
        # Pattern to match **text** and extract the text inside
        bold_pattern = r'\*\*(.*?)\*\*'
        bold_matches = re.findall(bold_pattern, text)
        
        if bold_matches:
            # Remove the ** markers and return the text
            cleaned_text = re.sub(bold_pattern, r'\1', text)
            
            # Find positions of bold parts in cleaned text
            bold_parts = []
            for match in bold_matches:
                start_pos = cleaned_text.find(match)
                if start_pos != -1:
                    bold_parts.append({
                        'start': start_pos,
                        'end': start_pos + len(match)
                    })
            
            return cleaned_text, bold_parts
        else:
            # No bold formatting found
            return text, []

    def _add_sources_sheet(self, workbook: Workbook, events_data: Optional[List[Dict[str, Any]]] = None, ops_learning_data: Optional[List[Dict[str, Any]]] = None):
        """Add a Sources sheet with appeal codes and source information for appeal-driven events and ops learning."""
        sources_ws = workbook.create_sheet("Sources Used")
        
        # Style definitions
        header_font = Font(bold=True, size=12, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        section_font = Font(bold=True, size=11, color="203764")
        
        row = 1
        
        # Title
        sources_ws.merge_cells(f'A{row}:E{row}')
        title_cell = sources_ws[f'A{row}']
        title_cell.value = "Data Sources Used in AI Analysis"
        title_cell.font = Font(bold=True, size=14, color="FFFFFF")
        title_cell.fill = PatternFill(start_color="203764", end_color="203764", fill_type="solid")
        title_cell.alignment = Alignment(horizontal="center")
        row += 2
        
        # Appeal-driven events section
        if events_data:
            sources_ws[f'A{row}'] = "APPEAL-DRIVEN EVENTS"
            sources_ws[f'A{row}'].font = section_font
            row += 1
            
            # Headers for events
            event_headers = ["Event Name", "Appeal Code", "Disaster Type", "Location", "Source Context"]
            for col, header in enumerate(event_headers, 1):
                cell = sources_ws.cell(row=row, column=col, value=header)
                cell.font = header_font
                cell.fill = header_fill
            row += 1
            
            # Appeal-driven event data
            for event in events_data[:15]:  # Display up to 15 appeal-sourced events for comprehensive source tracking
                event_name = event.get('name', 'Unknown')
                appeal_code = event.get('appeal_source', 'N/A')
                
                dtype_info = event.get('dtype', {})
                disaster_type = dtype_info.get('name') if isinstance(dtype_info, dict) else event.get('dtype_name', 'Unknown')
                
                countries = event.get('countries', [])
                if countries:
                    first_country = countries[0]
                    if isinstance(first_country, dict):
                        location = first_country.get('name', 'Unknown')
                    else:
                        # Handle integer country IDs
                        location = f"Country ID: {first_country}"
                else:
                    location = event.get('country_name', 'Unknown')
                
                source_context = event.get('source_note', 'Appeal-driven event')
                
                sources_ws[f'A{row}'] = event_name
                sources_ws[f'B{row}'] = appeal_code
                sources_ws[f'C{row}'] = disaster_type
                sources_ws[f'D{row}'] = location
                sources_ws[f'E{row}'] = source_context
                row += 1
            
            row += 1
        
        # Ops Learning section
        if ops_learning_data:
            sources_ws[f'A{row}'] = "OPERATIONAL LEARNING"
            sources_ws[f'A{row}'].font = section_font
            row += 1
            
            # Headers for ops learning
            learning_headers = ["Learning Summary", "Appeal Code", "Event", "Document", "Source Context"]
            for col, header in enumerate(learning_headers, 1):
                cell = sources_ws.cell(row=row, column=col, value=header)
                cell.font = header_font
                cell.fill = header_fill
            row += 1
            
            # Ops learning data
            for learning in ops_learning_data[:20]:  # Display all available learning entries for complete source documentation
                learning_text = (
                    learning.get('learning_validated_en') or 
                    learning.get('learning_validated') or 
                    learning.get('learning_en') or 
                    'Learning content'
                )
                # Truncate if too long
                if len(learning_text) > 80:
                    learning_text = learning_text[:80] + "..."
                
                appeal_info = learning.get('appeal', {})
                if isinstance(appeal_info, dict):
                    appeal_code = appeal_info.get('code', 'N/A')
                    event_details = appeal_info.get('event_details', {})
                    if isinstance(event_details, dict):
                        event_name = event_details.get('name', 'N/A')
                    else:
                        event_name = 'N/A'
                else:
                    # Handle integer appeal IDs
                    appeal_code = str(appeal_info) if appeal_info else 'N/A'
                    event_name = 'N/A'
                document = learning.get('document_name', 'N/A')
                source_context = learning.get('source_note', 'Operational learning')
                
                sources_ws[f'A{row}'] = learning_text
                sources_ws[f'B{row}'] = appeal_code
                sources_ws[f'C{row}'] = event_name
                sources_ws[f'D{row}'] = document
                sources_ws[f'E{row}'] = source_context
                row += 1
        
        # Auto-adjust column widths for sources sheet
        for col in range(1, 6):
            sources_ws.column_dimensions[chr(64 + col)].width = 25