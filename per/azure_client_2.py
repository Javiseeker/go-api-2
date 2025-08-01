"""
azure_client_2.py
==================

Response generation service for RR Capacity Question processing.
Fills only the 'Notes on Response Capacity with sources' field using AI analysis.
Uses rr_parsed_excel.json as the data source with simple string References format.
"""

from __future__ import annotations

import os
from typing import List, Dict, Any, Optional

try:
    from openai import AzureOpenAI  # type: ignore
except ImportError:
    AzureOpenAI = None  # type: ignore


class ResponseGenerationService:
    """Service for generating capacity assessment responses."""

    def __init__(self) -> None:
        # Get configuration from environment variables or Django settings
        try:
            from django.conf import settings  # type: ignore
            self.endpoint: Optional[str] = getattr(settings, 'AZURE_OPENAI_ENDPOINT', None) or os.environ.get('AZURE_OPENAI_ENDPOINT')
            self.key: Optional[str] = getattr(settings, 'AZURE_OPENAI_KEY', None) or os.environ.get('AZURE_OPENAI_KEY')
            self.deployment: Optional[str] = getattr(settings, 'AZURE_OPENAI_DEPLOYMENT_NAME', None) or os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME')
        except ImportError:
            # Fallback to environment variables only
            self.endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
            self.key = os.environ.get('AZURE_OPENAI_KEY')
            self.deployment = os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME')

        if AzureOpenAI and self.endpoint and self.key and self.deployment:
            self.client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.key,
                api_version="2024-02-15-preview",
            )
        else:
            self.client = None

    def _make_request(self, messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: int = 1500) -> Optional[str]:
        """Helper method to make requests with error handling."""
        if not self.client or not self.deployment:
            return None
            
        try:
            response = self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,  # type: ignore
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception:
            return None

    def generate_response_notes(self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]], ops_learning_data: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        """Generate brief notes on response capacity based on question and appeal-driven event data."""
        if not self.client:
            return None

        if ops_learning_data is None:
            ops_learning_data = []

        area = question_data.get('Area', '')
        critical_question = question_data.get('Critical Questions', '')
        guiding_questions = question_data.get('Guiding/probing questions', '')
        examples = question_data.get('Examples of recommended actions', '')
        references = question_data.get('References', '')
        
        # Get question-specific system prompt
        system_prompt = self._get_question_specific_prompt(critical_question, area)
        
        # Handle both old and new field names for Notes
        notes_field_new = "Notes on Response Capacity with sources"
        notes_field_old = "Notes on Response include the source"
        notes_value = question_data.get(notes_field_new) or question_data.get(notes_field_old, '')
        
        # Ensure references is a string (handle current rr_parsed_excel.json format)
        if isinstance(references, list):
            reference_strings = []
            for ref in references:
                if isinstance(ref, dict):
                    text = ref.get('text', '')
                    url = ref.get('url', '')
                    if url:
                        reference_strings.append(f"{text} ({url})")
                    else:
                        reference_strings.append(text)
                else:
                    reference_strings.append(str(ref))
            references = '\n'.join(reference_strings)
        else:
            references = str(references) if references else ''

        # Format event data for context
        events_context = self._format_events_for_assessment(event_data)
        
        # Format ops learning data for context
        learning_context = self._format_ops_learning_for_assessment(ops_learning_data)

        messages = [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": (
                    f"Assessment Area: {area}\n\n"
                    f"Critical Question: {critical_question}\n\n"
                    f"Guiding Questions: {guiding_questions}\n\n"
                    f"Examples: {examples}\n\n"
                    f"References: {references}\n\n"
                    f"Events Context (appeal-driven):\n{events_context}\n\n"
                    f"Operational Learning Context:\n{learning_context}\n\n"
                    f"ANALYZE THIS SPECIFIC QUESTION: '{critical_question}'\n\n"
                    f"Question focus: {critical_question[:100]}...\n"
                    f"Detailed areas: {guiding_questions[:100]}...\n\n"
                    f"Generate 3-4 bullet points with VARIED LANGUAGE PATTERNS. Each bullet must use different sentence structures:\n\n"
                    f"EXAMPLE LANGUAGE VARIATIONS:\n"
                    f"- Current legislation establishes... (descriptive statement)\n"
                    f"- Coordination gaps emerge when... (problem identification)\n"
                    f"- Recent amendments demonstrate... (positive development)\n"
                    f"- Operational challenges include... (challenge listing)\n"
                    f"- Documentation reveals... (evidence-based finding)\n"
                    f"- Implementation varies across... (variation analysis)\n"
                    f"- Stakeholder feedback indicates... (stakeholder perspective)\n\n"
                    f"AVOID REPETITIVE PATTERNS:\n"
                    f"- Don't use 'The NS has... but...' in every bullet\n"
                    f"- Don't start every sentence the same way\n"
                    f"- Don't use identical verb patterns\n"
                    f"- Don't follow the same structure for each point\n\n"
                    f"Create diverse, specific insights that directly address what this question is assessing. "
                    f"Use concrete, varied language that reflects the unique aspects of this particular capacity area."
                )
            }
        ]

        response = self._make_request(messages, temperature=0.9, max_tokens=800)
        
        # Clean any markdown formatting that might still appear
        if response:
            response = self._clean_markdown_formatting(response)
        
        return response

    def _get_question_specific_prompt(self, critical_question: str, area: str) -> str:
        """Generate question-specific system prompts based on question content and area."""
        
        # Analyze question focus
        question_lower = critical_question.lower()
        area_lower = area.lower()
        
        # Base prompt
        base = ("You are an IFRC emergency response specialist conducting a rapid response capacity assessment. "
                "Generate 3-4 unique bullet points with varied language patterns. "
                "CRITICAL: Use different sentence structures, verbs, and perspectives for each bullet. "
                "NO repetitive 'The NS has... but...' patterns. ")
        
        # Question-specific prompts
        if 'mandate' in question_lower or 'officially recognised' in question_lower:
            specific = ("Focus on LEGAL MANDATE and OFFICIAL RECOGNITION. "
                       "Analyze: legal frameworks, government recognition, auxiliary status, formal agreements. "
                       "Use varied approaches: 'Current legislation establishes...', 'Recognition gaps include...', "
                       "'Legal frameworks demonstrate...', 'Formal agreements indicate...' ")
                       
        elif 'policy' in question_lower or 'strategic' in question_lower:
            specific = ("Focus on POLICY FRAMEWORKS and STRATEGIC DOCUMENTS. "
                       "Analyze: policy development, strategic planning, documentation quality, implementation gaps. "
                       "Use varied approaches: 'Strategic documents outline...', 'Policy gaps emerge in...', "
                       "'Documentation reveals...', 'Implementation challenges include...' ")
                       
        elif 'risk' in question_lower or 'early warning' in question_lower:
            specific = ("Focus on RISK MANAGEMENT and EARLY WARNING SYSTEMS. "
                       "Analyze: risk assessment capabilities, monitoring systems, warning mechanisms, preparedness. "
                       "Use varied approaches: 'Risk monitoring demonstrates...', 'Warning systems operate...', "
                       "'Assessment capabilities include...', 'Preparedness measures show...' ")
                       
        elif 'business continuity' in question_lower or 'continuity plan' in question_lower:
            specific = ("Focus on BUSINESS CONTINUITY and OPERATIONAL RESILIENCE. "
                       "Analyze: continuity planning, operational resilience, crisis management, recovery procedures. "
                       "Use varied approaches: 'Continuity planning encompasses...', 'Resilience measures include...', "
                       "'Recovery procedures demonstrate...', 'Crisis management reveals...' ")
                       
        elif 'operations management' in question_lower or 'coordination systems' in question_lower:
            specific = ("Focus on OPERATIONS MANAGEMENT and COORDINATION SYSTEMS. "
                       "Analyze: management structures, coordination mechanisms, operational procedures, system effectiveness. "
                       "Use varied approaches: 'Management structures operate through...', 'Coordination mechanisms facilitate...', "
                       "'Operational procedures demonstrate...', 'System effectiveness varies across...' ")
                       
        elif 'information' in question_lower or 'data' in question_lower:
            specific = ("Focus on INFORMATION MANAGEMENT and DATA SYSTEMS. "
                       "Analyze: data collection, information sharing, system integration, reporting mechanisms. "
                       "Use varied approaches: 'Data collection processes include...', 'Information sharing occurs through...', "
                       "'System integration demonstrates...', 'Reporting mechanisms reveal...' ")
                       
        elif 'coordination' in question_lower and ('mechanisms' in question_lower or 'relationships' in question_lower):
            specific = ("Focus on COORDINATION MECHANISMS and INTER-AGENCY RELATIONSHIPS. "
                       "Analyze: coordination structures, partnership frameworks, communication channels, collaboration effectiveness. "
                       "Use varied approaches: 'Coordination structures facilitate...', 'Partnership frameworks establish...', "
                       "'Communication channels operate through...', 'Collaboration effectiveness depends on...' ")
                       
        else:
            # Generic fallback for other questions
            specific = ("Focus on the SPECIFIC CAPACITY mentioned in the question. "
                       "Analyze the particular aspect being assessed and provide concrete insights. "
                       "Use varied approaches: 'Current practices demonstrate...', 'Capacity gaps emerge when...', "
                       "'Implementation varies across...', 'Effectiveness depends on...' ")
        
        formatting = ("FORMATTING: Start with '- [Specific Topic]: [Varied structure]' "
                     "Include references: '(Reference: [APPEAL_CODE] – [Event Name], [Date])' "
                     "Use DD Month YYYY date format. NO markdown - plain text only. "
                     "Make each bullet completely different in structure and approach.")
        
        return base + specific + formatting

    def _clean_markdown_formatting(self, text: str) -> str:
        """Remove all markdown formatting from text."""
        if not text:
            return text
        
        import re
        
        # Handle the specific pattern "**- Operational capacity:**" first
        text = re.sub(r'\*\*-\s*([^:]+):\*\*', r'- \1:', text)
        
        # Handle other variations of the pattern
        text = re.sub(r'-\s*\*\*([^:]+):\*\*', r'- \1:', text)  # - **text:** becomes - text:
        text = re.sub(r'\*\*([^:]+):\*\*', r'\1:', text)  # **text:** becomes text:
        
        # Remove any remaining markdown bold formatting
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # **text** becomes text
        
        # Remove any remaining asterisks used for emphasis
        text = re.sub(r'\*([^*]+)\*', r'\1', text)  # *text* becomes text
        text = re.sub(r'_([^_]+)_', r'\1', text)  # _text_ becomes text
        
        # Final cleanup of any remaining asterisks
        text = text.replace('**', '')
        
        return text

    def _format_date_for_reference(self, date_string: str) -> str:
        """Format date string to 'DD Month YYYY' format for consistent references."""
        try:
            from datetime import datetime
            # Parse the date string (handles both YYYY-MM-DD and datetime formats)
            if 'T' in date_string:
                date_obj = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
            else:
                date_obj = datetime.strptime(date_string[:10], '%Y-%m-%d')
            
            # Format as "DD Month YYYY"
            return date_obj.strftime('%d %B %Y')
        except (ValueError, TypeError):
            # Return original if parsing fails
            return date_string[:10] if len(date_string) >= 10 else date_string

    def generate_recommended_actions(self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]], ops_learning_data: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        """Generate brief recommended actions for continuation of response."""
        if not self.client:
            return None

        if ops_learning_data is None:
            ops_learning_data = []

        area = question_data.get('Area', '')
        critical_question = question_data.get('Critical Questions', '')
        guiding_questions = question_data.get('Guiding/probing questions', '')
        examples = question_data.get('Examples of recommended actions', '')

        # Format event data for context
        events_context = self._format_events_for_assessment(event_data)
        
        # Format ops learning data for context
        learning_context = self._format_ops_learning_for_assessment(ops_learning_data)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an IFRC emergency response specialist providing actionable recommendations. "
                    "Be CONCISE and SPECIFIC. Provide 2-3 brief, practical actions only. "
                    "Use clear, direct language. Avoid lengthy explanations. "
                    "IMPORTANT: Include source references (appeal codes, event names) when recommending specific approaches. "
                    "Use PLAIN TEXT ONLY - no markdown formatting, no bold text, no headers. "
                    "Use simple numbered list (1., 2., 3.) or bullet points (-)."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Assessment Area: {area}\n\n"
                    f"Critical Question: {critical_question}\n\n"
                    f"Guiding Questions: {guiding_questions}\n\n"
                    f"Example Actions: {examples}\n\n"
                    f"Events Context (appeal-driven):\n{events_context}\n\n"
                    f"Operational Learning Context:\n{learning_context}\n\n"
                    f"Provide 2-3 BRIEF, specific recommended actions for operational strategy. "
                    f"Be direct and actionable."
                )
            }
        ]

        return self._make_request(messages, temperature=0.6, max_tokens=300)

    def process_capacity_question(self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]], ops_learning_data: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Optional[str]]:
        """Process a single RR capacity question and generate only response notes."""
        if ops_learning_data is None:
            ops_learning_data = []
            
        return {
            "Notes on Response Capacity with sources": self.generate_response_notes(question_data, event_data, ops_learning_data)
        }

    def _format_events_for_assessment(self, events: List[Dict[str, Any]]) -> str:
        """Format events data for capacity assessment context with source information."""
        if not events:
            return "No appeal-driven event data available."

        formatted_events = []
        for i, event in enumerate(events[:8], 1):  # Process up to 8 events for comprehensive context
            # Use actual API field names
            event_name = event.get('name', 'Unknown')
            event_info = [f"Event {i}: {event_name}"]
            
            # Disaster type from nested structure
            dtype_info = event.get('dtype', {})
            if isinstance(dtype_info, dict) and dtype_info.get('name'):
                event_info.append(f"Type: {dtype_info['name']}")
            elif event.get('dtype_name'):  # Fallback for flat structure
                event_info.append(f"Type: {event['dtype_name']}")
                
            # Country from nested structure  
            countries = event.get('countries', [])
            if countries and len(countries) > 0:
                country_name = countries[0].get('name', 'Unknown')
                event_info.append(f"Location: {country_name}")
            elif event.get('country_name'):  # Fallback
                event_info.append(f"Location: {event['country_name']}")
                
            # Date fields - format for consistent reference style
            date_str = None
            if event.get('disaster_start_date'):
                date_str = self._format_date_for_reference(event['disaster_start_date'])
            elif event.get('start_date'):
                date_str = self._format_date_for_reference(event['start_date'])
            
            if date_str:
                event_info.append(f"Date: {date_str}")
                
            if event.get('num_affected'):
                event_info.append(f"Affected: {event['num_affected']:,}")
                
            # Severity level
            if event.get('ifrc_severity_level_display'):
                event_info.append(f"Severity: {event['ifrc_severity_level_display']}")
                
            # Add appeal code and source information
            appeal_source = event.get('appeal_source')
            if appeal_source:
                event_info.append(f"Appeal: {appeal_source}")
                
            # Add source note
            source_note = event.get('source_note')
            if source_note:
                event_info.append(f"Source: {source_note}")
            
            # Add response-specific information from field reports and appeals
            response_details = []
            
            # Extract data from field reports if available
            field_reports = event.get('field_reports', [])
            if field_reports:
                field_report = field_reports[0] if isinstance(field_reports, list) else field_reports
                if field_report.get('num_volunteers'):
                    response_details.append(f"Volunteers: {field_report['num_volunteers']}")
                if field_report.get('num_localstaff'):
                    response_details.append(f"Local Staff: {field_report['num_localstaff']}")
                if field_report.get('num_expats_delegates'):
                    response_details.append(f"International Delegates: {field_report['num_expats_delegates']}")
                if field_report.get('summary'):
                    summary_text = field_report['summary'][:100]
                    response_details.append(f"Actions: {summary_text}{'...' if len(field_report['summary']) > 100 else ''}")
            
            # Extract data from appeals if available
            appeals = event.get('appeals', [])
            if appeals:
                appeal = appeals[0] if isinstance(appeals, list) else appeals
                if appeal.get('amount_requested'):
                    response_details.append(f"Appeal: CHF {appeal['amount_requested']:,.0f}")
                if appeal.get('num_beneficiaries'):
                    response_details.append(f"Beneficiaries: {appeal['num_beneficiaries']:,}")
                if appeal.get('atype_display'):
                    response_details.append(f"Type: {appeal['atype_display']}")
            
            if response_details:
                event_info.append(f"Response: {'; '.join(response_details)}")
                
            formatted_events.append(" | ".join(event_info))
            
        return "\n".join(formatted_events)

    def _format_ops_learning_for_assessment(self, ops_learning_data: List[Dict[str, Any]]) -> str:
        """Format operational learning data for capacity assessment context with source labels."""
        if not ops_learning_data:
            return "No operational learning data available."

        formatted_learning = []
        for i, learning_item in enumerate(ops_learning_data[:10], 1):  # Process up to 10 learning items for richer context
            # Extract learning content
            learning_text = (
                learning_item.get('learning_validated_en') or 
                learning_item.get('learning_validated') or 
                learning_item.get('learning_en') or 
                'Unknown learning'
            )
            
            learning_info = [f"Learning {i}: {learning_text[:100]}{'...' if len(learning_text) > 100 else ''}"]
            
            # Extract appeal and event information
            appeal_info = learning_item.get('appeal', {})
            event_details = appeal_info.get('event_details', {})
            
            # Add appeal code with more prominence
            appeal_code = appeal_info.get('code')
            if appeal_code:
                learning_info.append(f"Appeal Code: {appeal_code}")
            elif appeal_info.get('name'):
                learning_info.append(f"Appeal: {appeal_info['name']}")
                
            if event_details.get('name'):
                learning_info.append(f"Event: {event_details['name']}")
            if appeal_info.get('start_date'):
                formatted_date = self._format_date_for_reference(appeal_info['start_date'])
                learning_info.append(f"Date: {formatted_date}")
            
            # Add document information if available
            if learning_item.get('document_name'):
                learning_info.append(f"Document: {learning_item['document_name']}")
                
            # Add source note from IFRCEventListView pattern
            source_note = learning_item.get('source_note')
            if source_note:
                learning_info.append(f"Context: {source_note}")
                
            formatted_learning.append(" | ".join(learning_info))
            
        return "\n".join(formatted_learning)


# Legacy compatibility
AzureServiceClient = ResponseGenerationService