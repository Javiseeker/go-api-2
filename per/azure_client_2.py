"""
azure_client.py
================

This module provides a thin wrapper around the Azure OpenAI Python SDK for
generating RR (Rapid Response) form suggestions based on historical event data.
It includes specialized methods for different sections of the RR form.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional, Dict, Any, Union

try:
    from openai import AzureOpenAI  # type: ignore
    from openai.types.chat import ChatCompletionMessageParam  # type: ignore
except ImportError:
    AzureOpenAI = None  # type: ignore
    ChatCompletionMessageParam = None  # type: ignore


class AzureServiceClient:
    """Enhanced Azure OpenAI client for RR form generation and learning extraction."""

    def __init__(self) -> None:
        # Attempt to read configuration from environment variables or Django settings
        try:
            from django.conf import settings  # type: ignore
            self.openai_endpoint: Optional[str] = getattr(settings, 'AZURE_OPENAI_ENDPOINT', None) or os.environ.get('AZURE_OPENAI_ENDPOINT')
            self.openai_key: Optional[str] = getattr(settings, 'AZURE_OPENAI_KEY', None) or os.environ.get('AZURE_OPENAI_KEY')
            self.openai_deployment: Optional[str] = getattr(settings, 'AZURE_OPENAI_DEPLOYMENT_NAME', None) or os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME')
        except ImportError:
            # Fallback to environment variables only
            self.openai_endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
            self.openai_key = os.environ.get('AZURE_OPENAI_KEY')
            self.openai_deployment = os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME')

        if AzureOpenAI and self.openai_endpoint and self.openai_key and self.openai_deployment:
            self.openai_client = AzureOpenAI(
                azure_endpoint=self.openai_endpoint,
                api_key=self.openai_key,
                api_version="2024-02-15-preview",
            )
        else:
            self.openai_client = None

    def _make_openai_request(self, messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: int = 1500) -> Optional[str]:
        """Helper method to make OpenAI requests with error handling."""
        if not self.openai_client or not self.openai_deployment:
            return None
            
        try:
            response = self.openai_client.chat.completions.create(
                model=self.openai_deployment,
                messages=messages,  # type: ignore
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception:
            return None

    def get_structured_summary(self, summary: Optional[str], description: Optional[str], learning_data: Optional[Iterable[Dict[str, Any]]]) -> Optional[str]:
        """Generate a structured summary using Azure OpenAI (legacy method)."""
        if not self.openai_client:
            return None

        learning_texts: List[str] = []
        if learning_data:
            sorted_learnings = sorted(
                learning_data,
                key=lambda x: x.get('created_at', ''),
                reverse=True,
            )
            for i, item in enumerate(sorted_learnings[:3]):
                text = item.get('learning_text') or ''
                if text:
                    learning_texts.append(text)

        combined_text = f"""
Summary: {summary or ''}
Description: {description or ''}

Top 3 Learning Items:
{chr(10).join([f"{i+1}. {t}" for i, t in enumerate(learning_texts)])}
"""

        messages = [
            {
                "role": "system",
                "content": (
                    "Create a structured summary with 'Top 3 Learnings' as a heading followed by descriptions. "
                    "Format the response with clear headings and bullet points."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Based on this information, create a structured summary with 'Top 3 Learnings' as a heading and descriptions: "
                    f"{combined_text}"
                ),
            },
        ]

        return self._make_openai_request(messages, temperature=0.7, max_tokens=1500)

    def generate_situation_analysis(self, events: List[Dict[str, Any]]) -> Optional[str]:
        """Generate situation analysis based on similar past events."""
        if not events:
            return None

        events_data = self._format_events_for_analysis(events)
        
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an emergency response analyst for the IFRC. Analyze historical disaster events "
                    "to provide a situation analysis for rapid response planning. Focus on patterns, scale, "
                    "and key characteristics that would inform current response decisions."
                )
            },
            {
                "role": "user", 
                "content": (
                    f"Based on these {len(events)} similar events, provide a concise situation analysis "
                    f"covering: 1) Typical impact patterns, 2) Common challenges, 3) Scale expectations:\n\n"
                    f"{events_data}"
                )
            }
        ]
        
        return self._make_openai_request(messages, temperature=0.6)

    def generate_response_strategy(self, events: List[Dict[str, Any]]) -> Optional[str]:
        """Generate recommended response strategy based on past successful interventions."""
        if not events:
            return None

        response_data = self._extract_response_patterns(events)
        
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a rapid response coordinator for the IFRC. Based on historical response "
                    "data, recommend strategic approaches that have proven effective for similar disasters."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Based on response patterns from {len(events)} similar events, recommend a response "
                    f"strategy covering: 1) Priority sectors, 2) Deployment approach, 3) Key partnerships:\n\n"
                    f"{response_data}"
                )
            }
        ]
        
        return self._make_openai_request(messages, temperature=0.6)

    def generate_resource_recommendations(self, events: List[Dict[str, Any]]) -> Optional[str]:
        """Generate resource deployment recommendations based on historical patterns."""
        if not events:
            return None

        resource_data = self._extract_resource_patterns(events)
        
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a resource planning specialist for the IFRC. Analyze historical deployment "
                    "patterns to recommend optimal resource allocation for similar disaster responses."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Based on resource deployment from {len(events)} similar events, recommend: "
                    f"1) Personnel needs, 2) ERU requirements, 3) Funding estimates:\n\n"
                    f"{resource_data}"
                )
            }
        ]
        
        return self._make_openai_request(messages, temperature=0.6)

    def generate_timeline_suggestions(self, events: List[Dict[str, Any]]) -> Optional[str]:
        """Generate timeline recommendations based on historical response patterns."""
        if not events:
            return None

        timeline_data = self._extract_timeline_patterns(events)
        
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an operations planning specialist for the IFRC. Based on historical "
                    "response timelines, provide realistic timeline recommendations for rapid response."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Based on response timelines from {len(events)} similar events, suggest: "
                    f"1) Immediate actions (0-72 hours), 2) Short-term goals (1-2 weeks), "
                    f"3) Medium-term planning (1-3 months):\n\n{timeline_data}"
                )
            }
        ]
        
        return self._make_openai_request(messages, temperature=0.6)

    def _format_events_for_analysis(self, events: List[Dict[str, Any]]) -> str:
        """Format events data for situation analysis."""
        formatted_events = []
        for i, event in enumerate(events, 1):
            event_info = [
                f"Event {i}: {event.get('event_name', 'Unknown')}"
            ]
            
            if event.get('disaster_type'):
                event_info.append(f"Type: {event['disaster_type']}")
            if event.get('country_names'):
                event_info.append(f"Location: {event['country_names']}")
            if event.get('num_affected'):
                event_info.append(f"Affected: {event['num_affected']:,}")
            if event.get('severity_level'):
                event_info.append(f"Severity: {event['severity_level']}")
                
            formatted_events.append(" | ".join(event_info))
            
        return "\n".join(formatted_events)

    def _extract_response_patterns(self, events: List[Dict[str, Any]]) -> str:
        """Extract response patterns from events data."""
        patterns = []
        
        for i, event in enumerate(events, 1):
            response_info = [f"Event {i} Response:"]
            
            if event.get('actions_taken'):
                response_info.append(f"Actions: {event['actions_taken']}")
            if event.get('num_volunteers'):
                response_info.append(f"Volunteers: {event['num_volunteers']}")
            if event.get('eru_type'):
                response_info.append(f"ERU: {event['eru_type']}")
            if event.get('appeal_amount_requested'):
                response_info.append(f"Appeal: CHF {event['appeal_amount_requested']:,.0f}")
                
            patterns.append(" | ".join(response_info))
            
        return "\n".join(patterns)

    def _extract_resource_patterns(self, events: List[Dict[str, Any]]) -> str:
        """Extract resource deployment patterns from events data."""
        resources = []
        
        for i, event in enumerate(events, 1):
            resource_info = [f"Event {i} Resources:"]
            
            if event.get('num_volunteers'):
                resource_info.append(f"Volunteers: {event['num_volunteers']}")
            if event.get('num_localstaff'):
                resource_info.append(f"Staff: {event['num_localstaff']}")
            if event.get('personnel_roles'):
                resource_info.append(f"Personnel: {event['personnel_roles']}")
            if event.get('appeal_amount_funded'):
                resource_info.append(f"Funded: CHF {event['appeal_amount_funded']:,.0f}")
                
            resources.append(" | ".join(resource_info))
            
        return "\n".join(resources)

    def _extract_timeline_patterns(self, events: List[Dict[str, Any]]) -> str:
        """Extract timeline patterns from events data."""
        timelines = []
        
        for i, event in enumerate(events, 1):
            timeline_info = [f"Event {i} Timeline:"]
            
            if event.get('disaster_start_date'):
                timeline_info.append(f"Start: {event['disaster_start_date']}")
            if event.get('field_report_date'):
                timeline_info.append(f"First Report: {event['field_report_date']}")
            if event.get('appeal_start_date'):
                timeline_info.append(f"Appeal: {event['appeal_start_date']}")
                
            timelines.append(" | ".join(timeline_info))
            
        return "\n".join(timelines)