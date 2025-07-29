"""
response_service.py
===================

Response generation service for RR Capacity Question processing.
Fills missing fields: Notes and Recommended Actions.
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

    def generate_response_notes(self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]], ops_learning_data: List[Dict[str, Any]] = None) -> Optional[str]:
        """Generate brief notes on response capacity based on question and historical data."""
        if not self.client:
            return None

        if ops_learning_data is None:
            ops_learning_data = []

        area = question_data.get('Area', '')
        critical_question = question_data.get('Critical Questions', '')
        guiding_questions = question_data.get('Guiding/probing questions', '')
        examples = question_data.get('Examples of recommended actions', '')
        references = question_data.get('References', '')

        # Format event data for context
        events_context = self._format_events_for_assessment(event_data)
        
        # Format ops learning data for context
        learning_context = self._format_ops_learning_for_assessment(ops_learning_data)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an IFRC emergency response specialist conducting a rapid response capacity assessment. "
                    "Provide BRIEF, CONCISE notes on response capacity. Keep responses short and focused. "
                    "Use simple bullet points when appropriate (- for bullets). Avoid lengthy explanations. "
                    "Focus on key observations and actionable insights only. "
                    "Use PLAIN TEXT ONLY - no markdown formatting, no bold text, no headers."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Assessment Area: {area}\n\n"
                    f"Critical Question: {critical_question}\n\n"
                    f"Guiding Questions: {guiding_questions}\n\n"
                    f"Examples: {examples}\n\n"
                    f"References: {references}\n\n"
                    f"Historical Events Context:\n{events_context}\n\n"
                    f"Operational Learning Context:\n{learning_context}\n\n"
                    f"Provide BRIEF notes on response capacity (max 3-4 bullet points). "
                    f"Include source references where appropriate."
                )
            }
        ]

        return self._make_request(messages, temperature=0.6, max_tokens=400)

    def generate_recommended_actions(self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]], ops_learning_data: List[Dict[str, Any]] = None) -> Optional[str]:
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
                    f"Historical Events Context:\n{events_context}\n\n"
                    f"Operational Learning Context:\n{learning_context}\n\n"
                    f"Provide 2-3 BRIEF, specific recommended actions for operational strategy. "
                    f"Be direct and actionable."
                )
            }
        ]

        return self._make_request(messages, temperature=0.6, max_tokens=300)

    def process_capacity_question(self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]], ops_learning_data: List[Dict[str, Any]] = None) -> Dict[str, Optional[str]]:
        """Process a single RR capacity question and generate missing fields."""
        if ops_learning_data is None:
            ops_learning_data = []
            
        return {
            "Notes on Response include the source": self.generate_response_notes(question_data, event_data, ops_learning_data),
            "Recommended actions for continuation of response": self.generate_recommended_actions(question_data, event_data, ops_learning_data)
        }

    def _format_events_for_assessment(self, events: List[Dict[str, Any]]) -> str:
        """Format events data for capacity assessment context."""
        if not events:
            return "No historical event data available."

        formatted_events = []
        for i, event in enumerate(events[:3], 1):  # Limit to top 3 most relevant events
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
                
            # Date fields
            if event.get('disaster_start_date'):
                event_info.append(f"Date: {event['disaster_start_date'][:10]}")
            elif event.get('start_date'):
                event_info.append(f"Date: {event['start_date'][:10]}")
                
            if event.get('num_affected'):
                event_info.append(f"Affected: {event['num_affected']:,}")
                
            # Severity level
            if event.get('ifrc_severity_level_display'):
                event_info.append(f"Severity: {event['ifrc_severity_level_display']}")
            
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
        """Format operational learning data for capacity assessment context."""
        if not ops_learning_data:
            return "No operational learning data available."

        formatted_learning = []
        for i, learning_item in enumerate(ops_learning_data[:3], 1):  # Limit to top 3 most relevant learning items
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
            
            if appeal_info.get('name'):
                learning_info.append(f"Appeal: {appeal_info['name']}")
            if event_details.get('name'):
                learning_info.append(f"Event: {event_details['name']}")
            if appeal_info.get('start_date'):
                learning_info.append(f"Date: {appeal_info['start_date'][:10]}")  # Just the date part
            
            # Add document information if available
            if learning_item.get('document_name'):
                learning_info.append(f"Source: {learning_item['document_name']}")
                
            formatted_learning.append(" | ".join(learning_info))
            
        return "\n".join(formatted_learning)


# Legacy compatibility
AzureServiceClient = ResponseGenerationService