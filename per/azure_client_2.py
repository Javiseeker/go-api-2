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

    def generate_response_notes(self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]]) -> Optional[str]:
        """Generate brief notes on response capacity based on question and historical data."""
        if not self.client:
            return None

        area = question_data.get('Area', '')
        critical_question = question_data.get('Critical Questions', '')
        guiding_questions = question_data.get('Guiding/probing questions', '')
        examples = question_data.get('Examples of recommended actions', '')
        references = question_data.get('References', '')

        # Format event data for context
        events_context = self._format_events_for_assessment(event_data)

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
                    f"Historical Context:\n{events_context}\n\n"
                    f"Provide BRIEF notes on response capacity (max 3-4 bullet points). "
                    f"Include source references where appropriate."
                )
            }
        ]

        return self._make_request(messages, temperature=0.6, max_tokens=400)

    def generate_recommended_actions(self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]]) -> Optional[str]:
        """Generate brief recommended actions for continuation of response."""
        if not self.client:
            return None

        area = question_data.get('Area', '')
        critical_question = question_data.get('Critical Questions', '')
        guiding_questions = question_data.get('Guiding/probing questions', '')
        examples = question_data.get('Examples of recommended actions', '')

        # Format event data for context
        events_context = self._format_events_for_assessment(event_data)

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
                    f"Historical Context:\n{events_context}\n\n"
                    f"Provide 2-3 BRIEF, specific recommended actions for operational strategy. "
                    f"Be direct and actionable."
                )
            }
        ]

        return self._make_request(messages, temperature=0.6, max_tokens=300)

    def process_capacity_question(self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
        """Process a single RR capacity question and generate missing fields."""
        return {
            "Notes on Response include the source": self.generate_response_notes(question_data, event_data),
            "Recommended actions for continuation of response": self.generate_recommended_actions(question_data, event_data)
        }

    def _format_events_for_assessment(self, events: List[Dict[str, Any]]) -> str:
        """Format events data for capacity assessment context."""
        if not events:
            return "No historical event data available."

        formatted_events = []
        for i, event in enumerate(events[:3], 1):  # Limit to top 3 most relevant events
            event_info = [f"Event {i}: {event.get('event_name', 'Unknown')}"]
            
            if event.get('disaster_type'):
                event_info.append(f"Type: {event['disaster_type']}")
            if event.get('country_names'):
                event_info.append(f"Location: {event['country_names']}")
            if event.get('disaster_start_date'):
                event_info.append(f"Date: {event['disaster_start_date']}")
            if event.get('num_affected'):
                event_info.append(f"Affected: {event['num_affected']:,}")
            
            # Add response-specific information
            response_details = []
            if event.get('actions_taken'):
                response_details.append(f"Actions: {event['actions_taken']}")
            if event.get('num_volunteers'):
                response_details.append(f"Volunteers: {event['num_volunteers']}")
            if event.get('eru_type'):
                response_details.append(f"ERU: {event['eru_type']}")
            if event.get('appeal_amount_requested'):
                response_details.append(f"Appeal: CHF {event['appeal_amount_requested']:,.0f}")
            
            if response_details:
                event_info.append(f"Response: {'; '.join(response_details)}")
                
            formatted_events.append(" | ".join(event_info))
            
        return "\n".join(formatted_events)


# Legacy compatibility
AzureServiceClient = ResponseGenerationService