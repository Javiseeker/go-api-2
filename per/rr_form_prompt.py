"""
response_templates.py
=====================

Template system for generating comprehensive RR form suggestions.
This module provides specialized templates for different sections of the rapid
response form, designed to extract actionable insights from historical event data.

The system generates evidence-based recommendations for:
- Situation analysis and context assessment
- Response strategy and approach
- Resource allocation and deployment
- Timeline planning and milestones
- Risk considerations and mitigation
- Lessons learned and best practices
"""

from __future__ import annotations

import json
from typing import Iterable, List, Dict, Any, Optional

from django.conf import settings  # type: ignore
from openai import AzureOpenAI


class ResponseGenerationClient:
    """Response generation client for RR form creation."""

    def __init__(self) -> None:
        self._client: AzureOpenAI = AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version="2023-05-15",
        )

    def get_response(self, messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: int = 2000) -> str:
        """Send messages to response service and return the assistant's reply."""
        try:
            response = self._client.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT_NAME,
                messages=messages,  # type: ignore
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            raise RuntimeError(f"Response generation request failed: {e}")


class RRFormTemplateProcessor:
    """Template processor for comprehensive RR form suggestions."""

    # Base system message for all RR form sections
    base_system_message: str = (
        "You are an expert IFRC emergency response coordinator with extensive experience "
        "in rapid response planning and deployment. Your role is to analyze historical "
        "disaster response data and provide evidence-based recommendations for current "
        "rapid response planning. Focus on actionable insights that can guide immediate "
        "decision-making and resource allocation."
    )

    # Specialized templates for different RR form sections
    situation_analysis_template: str = (
        "Based on the historical events provided, create a comprehensive situation analysis "
        "that covers:\n\n"
        "1. **Context & Scale**: Typical impact patterns, affected populations, and geographic scope\n"
        "2. **Vulnerability Assessment**: Key at-risk populations and infrastructure\n"
        "3. **Operational Environment**: Access challenges, security considerations, local capacity\n"
        "4. **Precedent Analysis**: How similar events evolved and what factors influenced outcomes\n\n"
        "Provide specific data points and reference similar historical events. "
        "Format as clear, evidence-based paragraphs suitable for briefing senior management."
    )

    response_strategy_template: str = (
        "Based on successful response patterns from similar historical events, recommend "
        "an optimal response strategy that addresses:\n\n"
        "1. **Strategic Approach**: Primary response modalities and intervention priorities\n"
        "2. **Sector Focus**: Which sectors (shelter, health, WASH, etc.) to prioritize based on historical needs\n"
        "3. **Partnership Strategy**: Key partnerships and coordination mechanisms that proved effective\n"
        "4. **Implementation Approach**: Phased deployment strategy and operational methodology\n\n"
        "Reference specific examples from the historical data and explain why certain approaches "
        "were successful. Include recommendations for adapting strategies to current context."
    )

    resource_planning_template: str = (
        "Analyze historical resource deployment patterns to recommend optimal resource allocation:\n\n"
        "1. **Personnel Requirements**: Staffing levels, skill mix, and deployment timeline\n"
        "2. **ERU Deployment**: Emergency Response Unit types and capacity based on historical needs\n"
        "3. **Financial Planning**: Funding requirements and appeal strategy based on similar operations\n"
        "4. **Logistics Support**: Supply chain and operational support requirements\n\n"
        "Provide specific numbers where available from historical data and explain the rationale "
        "for resource recommendations. Include scaling factors for different scenario sizes."
    )

    timeline_planning_template: str = (
        "Based on historical response timelines, create a realistic operational timeline:\n\n"
        "1. **Immediate Actions (0-72 hours)**: Critical first response priorities\n"
        "2. **Short-term Goals (1-2 weeks)**: Early stabilization and assessment objectives\n"
        "3. **Medium-term Planning (1-3 months)**: Sustained response and transition planning\n"
        "4. **Critical Milestones**: Key decision points and success indicators\n\n"
        "Reference actual timelines from historical events and identify factors that "
        "accelerated or delayed responses. Include contingency considerations."
    )

    risk_assessment_template: str = (
        "Identify key risks and mitigation strategies based on historical challenges:\n\n"
        "1. **Operational Risks**: Access, security, coordination challenges from past events\n"
        "2. **Contextual Risks**: Political, social, environmental factors that affected responses\n"
        "3. **Resource Risks**: Funding, staffing, logistics challenges encountered historically\n"
        "4. **Mitigation Strategies**: Proven approaches for managing identified risks\n\n"
        "Provide specific examples of how similar risks were managed in historical events "
        "and recommend preventive measures for current planning."
    )

    lessons_learned_template: str = (
        "Extract key lessons and best practices from historical events:\n\n"
        "1. **Success Factors**: What worked well and should be replicated\n"
        "2. **Challenges Overcome**: How historical responses overcame significant obstacles\n"
        "3. **Innovations Applied**: Novel approaches or adaptations that proved effective\n"
        "4. **Recommendations**: Specific actions to improve current response effectiveness\n\n"
        "Focus on actionable insights that can directly inform current planning. "
        "Include specific examples and quantitative results where available."
    )

    @staticmethod
    def _format_events_for_template(events: List[Dict[str, Any]], focus_area: str) -> str:
        """Format events data with focus on specific aspects relevant to the template."""
        formatted_events = []
        
        for i, event in enumerate(events, 1):
            event_summary = [f"Event {i}: {event.get('event_name', 'Unknown')}"]
            
            # Basic event information
            if event.get('disaster_type'):
                event_summary.append(f"Type: {event['disaster_type']}")
            if event.get('country_names'):
                event_summary.append(f"Location: {event['country_names']}")
            if event.get('disaster_start_date'):
                event_summary.append(f"Date: {event['disaster_start_date']}")
            
            # Focus-specific information
            if focus_area == "situation":
                if event.get('num_affected'):
                    event_summary.append(f"Affected: {event['num_affected']:,}")
                if event.get('severity_level'):
                    event_summary.append(f"Severity: {event['severity_level']}")
                    
            elif focus_area == "response":
                if event.get('actions_taken'):
                    event_summary.append(f"Actions: {event['actions_taken']}")
                if event.get('eru_type'):
                    event_summary.append(f"ERU: {event['eru_type']}")
                    
            elif focus_area == "resources":
                if event.get('num_volunteers'):
                    event_summary.append(f"Volunteers: {event['num_volunteers']}")
                if event.get('appeal_amount_requested'):
                    event_summary.append(f"Appeal: CHF {event['appeal_amount_requested']:,.0f}")
                if event.get('personnel_roles'):
                    event_summary.append(f"Personnel: {event['personnel_roles']}")
                    
            elif focus_area == "timeline":
                if event.get('field_report_date'):
                    event_summary.append(f"First Report: {event['field_report_date']}")
                if event.get('appeal_start_date'):
                    event_summary.append(f"Appeal Launch: {event['appeal_start_date']}")
                    
            formatted_events.append(" | ".join(event_summary))
            
        return "\n".join(formatted_events)

    def generate_situation_analysis(self, events: List[Dict[str, Any]], temperature: float = 0.6) -> str:
        """Generate situation analysis based on historical events."""
        events_text = self._format_events_for_template(events, "situation")
        
        messages = [
            {"role": "system", "content": self.base_system_message},
            {"role": "user", "content": f"{self.situation_analysis_template}\n\nHistorical Events Data:\n{events_text}"}
        ]
        
        client = ResponseGenerationClient()
        return client.get_response(messages, temperature=temperature)

    def generate_response_strategy(self, events: List[Dict[str, Any]], temperature: float = 0.6) -> str:
        """Generate response strategy recommendations."""
        events_text = self._format_events_for_template(events, "response")
        
        messages = [
            {"role": "system", "content": self.base_system_message},
            {"role": "user", "content": f"{self.response_strategy_template}\n\nHistorical Events Data:\n{events_text}"}
        ]
        
        client = ResponseGenerationClient()
        return client.get_response(messages, temperature=temperature)

    def generate_resource_planning(self, events: List[Dict[str, Any]], temperature: float = 0.6) -> str:
        """Generate resource planning recommendations."""
        events_text = self._format_events_for_template(events, "resources")
        
        messages = [
            {"role": "system", "content": self.base_system_message},
            {"role": "user", "content": f"{self.resource_planning_template}\n\nHistorical Events Data:\n{events_text}"}
        ]
        
        client = ResponseGenerationClient()
        return client.get_response(messages, temperature=temperature)

    def generate_timeline_planning(self, events: List[Dict[str, Any]], temperature: float = 0.6) -> str:
        """Generate timeline and milestone recommendations."""
        events_text = self._format_events_for_template(events, "timeline")
        
        messages = [
            {"role": "system", "content": self.base_system_message},
            {"role": "user", "content": f"{self.timeline_planning_template}\n\nHistorical Events Data:\n{events_text}"}
        ]
        
        client = ResponseGenerationClient()
        return client.get_response(messages, temperature=temperature)

    def generate_risk_assessment(self, events: List[Dict[str, Any]], temperature: float = 0.6) -> str:
        """Generate risk assessment and mitigation strategies."""
        events_text = self._format_events_for_template(events, "situation")
        
        messages = [
            {"role": "system", "content": self.base_system_message},
            {"role": "user", "content": f"{self.risk_assessment_template}\n\nHistorical Events Data:\n{events_text}"}
        ]
        
        client = ResponseGenerationClient()
        return client.get_response(messages, temperature=temperature)

    def generate_lessons_learned(self, events: List[Dict[str, Any]], temperature: float = 0.6) -> str:
        """Extract lessons learned and best practices."""
        events_text = self._format_events_for_template(events, "response")
        
        messages = [
            {"role": "system", "content": self.base_system_message},
            {"role": "user", "content": f"{self.lessons_learned_template}\n\nHistorical Events Data:\n{events_text}"}
        ]
        
        client = ResponseGenerationClient()
        return client.get_response(messages, temperature=temperature)

    def generate_comprehensive_rr_suggestions(self, events: List[Dict[str, Any]]) -> Dict[str, str]:
        """Generate comprehensive RR form suggestions for all sections."""
        if not events:
            return {}

        suggestions = {}
        
        try:
            suggestions["situation_analysis"] = self.generate_situation_analysis(events)
        except Exception:
            suggestions["situation_analysis"] = "Unable to generate situation analysis - please check service configuration"

        try:
            suggestions["response_strategy"] = self.generate_response_strategy(events)
        except Exception:
            suggestions["response_strategy"] = "Unable to generate response strategy - please check service configuration"

        try:
            suggestions["resource_planning"] = self.generate_resource_planning(events)
        except Exception:
            suggestions["resource_planning"] = "Unable to generate resource planning - please check service configuration"

        try:
            suggestions["timeline_planning"] = self.generate_timeline_planning(events)
        except Exception:
            suggestions["timeline_planning"] = "Unable to generate timeline planning - please check service configuration"

        try:
            suggestions["risk_assessment"] = self.generate_risk_assessment(events)
        except Exception:
            suggestions["risk_assessment"] = "Unable to generate risk assessment - please check service configuration"

        try:
            suggestions["lessons_learned"] = self.generate_lessons_learned(events)
        except Exception:
            suggestions["lessons_learned"] = "Unable to generate lessons learned - please check service configuration"

        return suggestions

    # Legacy method for backward compatibility
    def get_rr_summary(self, events: Iterable[Dict[str, Any]], temperature: float = 0.5) -> str:
        """Generate a narrative summary for the RR form (legacy method)."""
        events_list = list(events)
        
        # Use the comprehensive generation but return as a single summary
        suggestions = self.generate_comprehensive_rr_suggestions(events_list)
        
        summary_parts = []
        for section, content in suggestions.items():
            if content and not content.startswith("Unable to generate"):
                summary_parts.append(f"**{section.replace('_', ' ').title()}**\n{content}")
        
        return "\n\n".join(summary_parts) if summary_parts else "Unable to generate comprehensive summary"


# Legacy compatibility
AzureOpenAiChat = ResponseGenerationClient
RRFormPromptTask = RRFormTemplateProcessor