# azure_client_2.py
# ==================
#
# Response generation service for RR Capacity Question processing.
# Fills only the 'Notes on Response Capacity with sources' field using AI analysis.
# Uses rr_parsed_excel.json as the data source with simple string References format.
# Enhanced to pass rich event context (full summary/description, GLIDE, contacts,
# key numeric figures, all field reports/appeals [capped], and coordinates).

from __future__ import annotations

import os
import re
from html import unescape
from typing import List, Dict, Any, Optional

try:
    from django.conf import settings  # type: ignore
except ImportError:
    settings = None  # type: ignore

try:
    from openai import AzureOpenAI  # type: ignore
except ImportError:
    AzureOpenAI = None  # type: ignore


class AzureServiceClient:
    """Service for generating capacity assessment responses (Azure OpenAI)."""

    def __init__(self) -> None:
        # Get configuration from Django settings or environment variables
        self.endpoint: Optional[str] = None
        self.key: Optional[str] = None
        self.deployment: Optional[str] = None

        try:
            if settings is not None and hasattr(settings, 'configured') and settings.configured:
                self.endpoint = getattr(settings, "AZURE_OPENAI_ENDPOINT", None) or os.environ.get("AZURE_OPENAI_ENDPOINT")
                self.key = getattr(settings, "AZURE_OPENAI_KEY", None) or os.environ.get("AZURE_OPENAI_KEY")
                self.deployment = getattr(settings, "AZURE_OPENAI_DEPLOYMENT_NAME", None) or os.environ.get(
                    "AZURE_OPENAI_DEPLOYMENT_NAME"
                )
            else:
                raise AttributeError("Settings not configured")
        except (AttributeError, ImportError):
            # Fall back to environment variables only
            self.endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
            self.key = os.environ.get("AZURE_OPENAI_KEY")
            self.deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME")
        


        if AzureOpenAI and self.endpoint and self.key and self.deployment:
            self.client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.key,
                api_version="2024-02-15-preview",
            )
        else:
            self.client = None

    # ----------------------
    # Low-level request
    # ----------------------
    def _make_request(
        self, messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: int = 1200
    ) -> Optional[str]:
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

    # ----------------------
    # Public entry points
    # ----------------------
    def process_capacity_question(
        self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]], ops_learning_data: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Optional[str]]:
        """Process a single RR capacity question and generate only response notes."""
        if ops_learning_data is None:
            ops_learning_data = []

        return {
            "Notes on Response Capacity with sources": self.generate_response_notes(
                question_data, event_data, ops_learning_data
            )
        }

    def generate_response_notes(
        self, question_data: Dict[str, Any], event_data: List[Dict[str, Any]], ops_learning_data: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[str]:
        """Generate brief notes on response capacity based on question and appeal-driven event data."""
        if not self.client:
            return None

        if ops_learning_data is None:
            ops_learning_data = []

        area = question_data.get("Area", "")
        critical_question = question_data.get("Critical Questions", "")
        guiding_questions = question_data.get("Guiding/probing questions", "")
        examples = question_data.get("Examples of recommended actions", "")
        references = question_data.get("References", "")

        # Improve references handling - check for different formats
        if isinstance(references, list):
            references = "; ".join(str(ref) for ref in references if ref)
        elif references:
            references = str(references)
        else:
            references = "No specific references provided"

        # Build rich context from events and ops-learning
        events_context = self._format_events_for_assessment(event_data or [])
        learning_context = self._format_ops_learning_for_assessment(ops_learning_data or [])

        # Check if we have real data - if not, return early
        if (not event_data or len(event_data) == 0) and (not ops_learning_data or len(ops_learning_data) == 0):
            return "Enough source is not available to answer this question"

        # Extract top facts for front-loading in system prompt
        top_facts = self._extract_key_facts(event_data, ops_learning_data)
        
        # Concise system prompt with key facts front-loaded
        system_prompt = self._build_concise_system_prompt(critical_question, area, top_facts)

        messages = [
            {"role": "system", "content": system_prompt},
            # Few-shot example to anchor the format
            {"role": "assistant", "content": 
                "LEGAL FRAMEWORK: National Disaster Management Act 2019 establishes Red Cross auxiliary status with government coordination mandate (Reference: MDRBGD025 – Bangladesh Cyclone Response, 15 January 2024)\n"
                "OPERATIONAL CAPACITY: Field Report FR-2023-000045 documents 1,200 volunteers deployed across 8 districts with 25,000 beneficiaries reached (Reference: MDRBGD025 – Bangladesh Cyclone Response, 18 January 2024)\n"
                "COORDINATION GAPS: Ops-learning from MDRBGD024 identifies 5-day delay in government liaison compared to previous response cycle (Reference: MDRBGD024 – Flood Response Review, 10 November 2023)"
            },
            {
                "role": "user",
                "content": (
                    f"Now analyze this question: {critical_question}\n\n"
                    f"Assessment Area: {area}\n\n"
                    f"Events Context:\n{events_context}\n\n"
                    f"Operational Learning Context:\n{learning_context}\n\n"
                    f"Generate 3-4 bullets using the exact format shown above. Each bullet must include specific facts from the provided sources."
                ),
            },
        ]

        response = self._make_request(messages, temperature=0.0, max_tokens=1200)

        if response:
            response = self._clean_markdown_formatting(response)

        return response

    # ----------------------
    # Prompting helpers
    # ----------------------
    def _extract_key_facts(self, event_data: List[Dict[str, Any]], ops_learning_data: List[Dict[str, Any]]) -> str:
        """Extract 2-3 most salient facts from events and ops-learning for front-loading."""
        facts = []
        
        # Extract top event facts
        for event in (event_data or [])[:2]:  # Top 2 events
            name = event.get("name", "Unknown Event")
            appeals = event.get("appeals") or []
            if appeals:
                appeal_code = appeals[0].get("code", "")
                start_date = appeals[0].get("start_date", "")
                if appeal_code and start_date:
                    date_fmt = self._format_date_for_reference(start_date) if start_date else ""
                    severity = event.get("ifrc_severity_level_display", "")
                    num_affected = event.get("num_affected")
                    fact_parts = [f"Event: {name}", f"Code: {appeal_code}"]
                    if date_fmt:
                        fact_parts.append(f"Date: {date_fmt}")
                    if severity:
                        fact_parts.append(f"Severity: {severity}")
                    if num_affected:
                        fact_parts.append(f"Affected: {self._fmt_num(num_affected)}")
                    facts.append(" | ".join(fact_parts))
        
        # Extract top ops-learning facts
        for learning in (ops_learning_data or [])[:2]:  # Top 2 learning items
            learning_text = (
                learning.get("learning_validated_en") or 
                learning.get("learning_validated") or 
                learning.get("learning_en", "")
            )
            appeal_info = learning.get("appeal", {})
            appeal_code = appeal_info.get("code", "")
            if learning_text and appeal_code:
                short_learning = learning_text[:80] + "..." if len(learning_text) > 80 else learning_text
                facts.append(f"Learning: {short_learning} | Appeal: {appeal_code}")
        
        return "\n".join(facts) if facts else "No key facts available"

    def _build_concise_system_prompt(self, critical_question: str, area: str, top_facts: str) -> str:
        """Build a concise system prompt with key facts front-loaded."""
        return (
            f"You are an IFRC emergency response specialist conducting rapid response capacity assessment.\n\n"
            f"KEY FACTS FROM SOURCES:\n{top_facts}\n\n"
            f"RULES:\n"
            f"- ONLY use information explicitly stated in the provided sources\n"
            f"- NEVER create, invent, or assume any information\n"
            f"- Each bullet MUST include specific facts (numbers, dates, places, names) from sources\n"
            f"- Format: UPPERCASE LABEL: analysis with specific facts (Reference: CODE – Event, Date)\n"
            f"- If insufficient source data, respond: 'Enough source is not available to answer this question'\n"
            f"- Generate 3-4 bullets with diverse analytical perspectives\n"
            f"- Plain text only, no markdown"
        )

    def _get_question_specific_prompt(self, critical_question: str, area: str) -> str:
        """Generate question-specific system prompts based on question content and area."""
        question_lower = (critical_question or "").lower()
        area_lower = (area or "").lower()

        base = (
            "You are an IFRC emergency response specialist conducting a rapid response capacity assessment. "
            "ABSOLUTE RULE: You MUST ONLY use information that is EXPLICITLY provided in the sources (events context and operational learning context). "
            "NEVER create, invent, infer, assume, or generate ANY information that is not directly stated in the provided sources. "
            "NEVER make up facts, numbers, dates, places, names, or any details. "
            "If you cannot answer a question based on the provided sources, respond with: 'Enough source is not available to answer this question' "
            "Generate 3–4 distinct insights with diverse analytical approaches and varied language patterns. "
            "Each bullet MUST begin with a concise UPPERCASE LABEL followed by a colon, then your analysis. "
            "Use different sentence structures, perspectives, and analytical angles across bullets. "
            "PLAIN TEXT ONLY (no markdown). "
            "REQUIREMENTS: Each bullet MUST include at least one specific fact (number, date, place, or named unit) FROM THE PROVIDED SOURCES ONLY. "
            "Tie each bullet to a specific field report/appeal ID and quote exact figures FROM THE PROVIDED SOURCES ONLY. "
            "Vary conclusions across bullets (strengths, contradictions, deltas vs previous ops) BASED ON PROVIDED SOURCES ONLY. "
            "Ensure appeal code country matches the NS context FROM THE PROVIDED SOURCES ONLY. "
            "Diversify analytical lenses: legal doc review, FR metrics, ops-learning, contacts' statements USING ONLY PROVIDED SOURCES. "
        )

        if "mandate" in question_lower or "officially recognised" in question_lower:
            specific = (
                "Focus on LEGAL MANDATE and OFFICIAL RECOGNITION: legal frameworks, auxiliary status, formal agreements. "
                "Analyze legislative status, recognition gaps, and formal agreements in context. "
            )
        elif "policy" in question_lower or "strategic" in question_lower:
            specific = (
                "Focus on POLICY FRAMEWORKS and STRATEGIC DOCUMENTS: policy development, strategic planning, "
                "documentation quality, implementation gaps. "
            )
        elif "risk" in question_lower or "early warning" in question_lower:
            specific = (
                "Focus on RISK MANAGEMENT and EARLY WARNING SYSTEMS: risk assessment capabilities, monitoring systems, "
                "warning mechanisms, preparedness. "
            )
        elif "business continuity" in question_lower or "continuity plan" in question_lower:
            specific = (
                "Focus on BUSINESS CONTINUITY and OPERATIONAL RESILIENCE: continuity planning, resilience measures, "
                "crisis management, recovery procedures. "
            )
        elif "operations management" in question_lower or "coordination systems" in question_lower:
            specific = (
                "Focus on OPERATIONS MANAGEMENT and COORDINATION SYSTEMS: management structures, coordination mechanisms, "
                "operational procedures, system effectiveness. "
            )
        elif "information" in question_lower or "data" in question_lower:
            specific = (
                "Focus on INFORMATION MANAGEMENT and DATA SYSTEMS: data collection, information sharing, integration, reporting. "
            )
        elif "coordination" in question_lower and ("mechanisms" in question_lower or "relationships" in question_lower):
            specific = (
                "Focus on COORDINATION MECHANISMS and INTER-AGENCY RELATIONSHIPS: structures, partnership frameworks, "
                "communication channels, collaboration effectiveness. "
            )
        else:
            specific = (
                "Focus on the SPECIFIC CAPACITY referenced by the question; provide concrete, context-grounded insights. "
            )

        formatting = (
            "Format: Begin each bullet with a relevant UPPERCASE LABEL and colon. "
            "Include a reference citation at the end when supported by event/appeal data FROM THE PROVIDED SOURCES ONLY. "
            "Vary your label choices and analytical perspectives. "
            "ABSOLUTE RULE: ONLY use information from the provided sources. "
            "NEVER create, invent, infer, assume, or generate ANY information not directly stated in the sources. "
            "If insufficient source information is available, respond with: 'Enough source is not available to answer this question' "
            "FACT ENFORCEMENT: Each bullet must include specific facts FROM THE PROVIDED SOURCES ONLY (numbers, dates, places, named units). "
            "Cross-check appeal codes match the country context FROM THE PROVIDED SOURCES ONLY. "
            "Diversify analytical approaches across bullets USING ONLY PROVIDED SOURCES. "
            "Plain text only."
        )

        return base + specific + formatting

    def _clean_markdown_formatting(self, text: str) -> str:
        """Remove common markdown artifacts."""
        if not text:
            return text
        # Handle bolded label patterns
        text = re.sub(r"\*\*-\s*([^:]+):\*\*", r"- \1:", text)
        text = re.sub(r"-\s*\*\*([^:]+):\*\*", r"- \1:", text)
        text = re.sub(r"\*\*([^:]+):\*\*", r"\1:", text)
        # Remove remaining emphasis
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        text = re.sub(r"_([^_]+)_", r"\1", text)
        return text.replace("**", "")

    # ----------------------
    # Formatting helpers
    # ----------------------
    def _format_date_for_reference(self, date_string: str) -> str:
        """Format date string to 'DD Month YYYY' format for consistent references."""
        try:
            from datetime import datetime

            if "T" in date_string:
                date_obj = datetime.fromisoformat(date_string.replace("Z", "+00:00"))
            else:
                date_obj = datetime.strptime(date_string[:10], "%Y-%m-%d")
            return date_obj.strftime("%d %B %Y")
        except Exception:
            return date_string[:10] if date_string else ""

    def _strip_html(self, html: str) -> str:
        """Rudimentary HTML → text cleaner for summaries/descriptions from GO."""
        if not html:
            return ""
        text = re.sub(r"<\s*br\s*/?>", "\n", html, flags=re.I)
        text = re.sub(r"<\s*/p\s*>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = unescape(text)
        text = re.sub(r"\r?\n\s*\n+", "\n\n", text)
        return text.strip()

    def _fmt_num(self, v: Any) -> str:
        try:
            n = float(v)
            if n.is_integer():
                return f"{int(n):,}"
            return f"{n:,.2f}"
        except Exception:
            return str(v)

    def _coords_of(self, event: Dict[str, Any]) -> Optional[str]:
        lat = event.get("lat") or event.get("latitude")
        lon = event.get("lon") or event.get("lng") or event.get("longitude")
        if lat and lon:
            return f"{lat}, {lon}"
        if isinstance(event.get("centroid"), dict):
            c = event["centroid"]
            if "lat" in c and "lon" in c:
                return f"{c['lat']}, {c['lon']}"
        if isinstance(event.get("bbox"), (list, tuple)) and len(event["bbox"]) == 4:
            # minLon, minLat, maxLon, maxLat → show center
            minLon, minLat, maxLon, maxLat = event["bbox"]
            try:
                clat = (float(minLat) + float(maxLat)) / 2
                clon = (float(minLon) + float(maxLon)) / 2
                return f"{clat:.5f}, {clon:.5f}"
            except Exception:
                pass
        return None

    # ----------------------
    # Context builders
    # ----------------------
    def _format_events_for_assessment(self, events: List[Dict[str, Any]]) -> str:
        """
        Rich event context for the assessment.
        Includes: full summary/description (HTML stripped), GLIDE, contacts, key figures,
        all field reports (capped), all appeals (capped), coordinates if available.
        """
        if not events:
            return "No event data available from sources."

        # === UPDATED: allow up to 10 events with concise formatting ===
        MAX_EVENTS = 10
        MAX_FR_PER_EVENT = 2  # Reduced for conciseness
        MAX_APPEALS_PER_EVENT = 2  # Reduced for conciseness
        MAX_EVENT_CHARS = 2000  # Reduced for better token efficiency

        formatted: List[str] = []

        for i, event in enumerate(events[:MAX_EVENTS], 1):
            parts: List[str] = []

            # Header / IDs
            name = event.get("name", "Unknown")
            dtype = (event.get("dtype") or {}).get("name") or event.get("dtype_name") or "Unknown"
            countries = event.get("countries") or []
            country_names = ", ".join([c.get("name", "Unknown") for c in countries]) if countries else event.get(
                "country_name", "Unknown"
            )
            date_str = ""
            if event.get("disaster_start_date"):
                date_str = self._format_date_for_reference(event["disaster_start_date"])
            elif event.get("start_date"):
                date_str = self._format_date_for_reference(event["start_date"])
            glide = event.get("glide") or ""
            coords = self._coords_of(event)

            header = [f"Event {i}: {name}", f"Type: {dtype}", f"Location: {country_names}"]
            if date_str:
                header.append(f"Date: {date_str}")
            if glide:
                header.append(f"GLIDE: {glide}")
            if coords:
                header.append(f"Coordinates: {coords}")
            parts.append(" | ".join(header))

            # Severity / Figures
            sev = event.get("ifrc_severity_level_display")
            num_aff = event.get("num_affected")
            figbits = []
            if sev:
                figbits.append(f"Severity: {sev}")
            if num_aff is not None:
                figbits.append(f"Affected: {self._fmt_num(num_aff)}")
            if figbits:
                parts.append("Figures: " + " | ".join(figbits))

            # Narrative (full cleaned)
            full_summary = self._strip_html(event.get("summary", ""))
            full_desc = self._strip_html(event.get("description", ""))
            if full_summary:
                parts.append("Summary:\n" + full_summary)
            if full_desc and full_desc != full_summary:
                parts.append("Description:\n" + full_desc)

            # Appeals (capped)
            appeals = event.get("appeals") or []
            if appeals:
                a_lines = []
                for ap in appeals[:MAX_APPEALS_PER_EVENT]:
                    code = ap.get("code") or ""
                    atype = ap.get("atype_display") or ""
                    amt_req = ap.get("amount_requested")
                    amt_fund = ap.get("amount_funded")
                    n_ben = ap.get("num_beneficiaries")
                    st = ap.get("status_display") or ""
                    sd = ap.get("start_date")
                    ed = ap.get("end_date")
                    sd_fmt = self._format_date_for_reference(sd) if sd else ""
                    ed_fmt = self._format_date_for_reference(ed) if ed else ""
                    line = []
                    if code:
                        line.append(f"{code}")
                    if atype:
                        line.append(f"Type: {atype}")
                    if st:
                        line.append(f"Status: {st}")
                    if sd_fmt or ed_fmt:
                        line.append(f"Dates: {sd_fmt} – {ed_fmt}".strip(" –"))
                    if amt_req is not None:
                        line.append(f"Requested: CHF {self._fmt_num(amt_req)}")
                    if amt_fund is not None:
                        line.append(f"Funded: CHF {self._fmt_num(amt_fund)}")
                    if n_ben is not None:
                        line.append(f"Beneficiaries: {self._fmt_num(n_ben)}")
                    a_lines.append(" ; ".join(line))
                if a_lines:
                    parts.append("Appeals:\n- " + "\n- ".join(a_lines))

            # Field Reports (capped) with contacts & figures
            frs = event.get("field_reports") or []
            if frs:
                fr_blocks = []
                for idx, fr in enumerate(frs[:MAX_FR_PER_EVENT], 1):
                    fr_lines = [f"Field Report {idx} (id {fr.get('id', '')}):"]
                    fr_date = fr.get("report_date") or fr.get("created_at")
                    fr_date_fmt = self._format_date_for_reference(fr_date) if fr_date else ""
                    if fr_date_fmt:
                        fr_lines.append(f"  Date: {fr_date_fmt}")
                    # Key numerics
                    keys = [
                        ("num_dead", "Dead"),
                        ("num_injured", "Injured"),
                        ("num_missing", "Missing"),
                        ("num_affected", "Affected"),
                        ("num_displaced", "Displaced"),
                        ("num_assisted", "Assisted"),
                        ("num_localstaff", "Local Staff"),
                        ("num_volunteers", "Volunteers"),
                        ("num_expats_delegates", "International Delegates"),
                        ("gov_num_dead", "Gov Dead"),
                        ("gov_num_affected", "Gov Affected"),
                        ("other_num_affected", "Other Affected"),
                    ]
                    fig_entries = []
                    for k, label in keys:
                        val = fr.get(k)
                        if val not in (None, "", 0):
                            fig_entries.append(f"{label}: {self._fmt_num(val)}")
                    if fig_entries:
                        fr_lines.append("  Figures: " + " | ".join(fig_entries))

                    # Narrative
                    fr_sum = self._strip_html(fr.get("summary", ""))
                    fr_desc = self._strip_html(fr.get("description", ""))
                    if fr_sum:
                        fr_lines.append("  Summary: " + fr_sum)
                    if fr_desc and fr_desc != fr_sum:
                        fr_lines.append("  Description: " + fr_desc)

                    # Contacts
                    contacts = fr.get("contacts") or []
                    if contacts:
                        c_lines = []
                        for c in contacts:
                            cname = c.get("name") or ""
                            ctitle = c.get("title") or ""
                            ctype = c.get("ctype") or ""
                            cemail = c.get("email") or ""
                            cphone = c.get("phone") or ""
                            frag = ", ".join([p for p in [cname, ctitle, ctype] if p])
                            if cemail:
                                frag += f" | {cemail}"
                            if cphone:
                                frag += f" | {cphone}"
                            if frag:
                                c_lines.append(f"    - {frag}")
                        if c_lines:
                            fr_lines.append("  Contacts:\n" + "\n".join(c_lines))

                    fr_blocks.append("\n".join(fr_lines))
                if fr_blocks:
                    parts.append("Field Reports:\n" + "\n\n".join(fr_blocks))

            # Provenance
            appeal_src = event.get("appeal_source") or ""
            source_note = event.get("source_note") or ""
            prov = []
            if appeal_src:
                prov.append(f"Appeal Source: {appeal_src}")
            if source_note:
                prov.append(f"Source Note: {source_note}")
            if prov:
                parts.append("Provenance: " + " | ".join(prov))

            # Join and enforce per-event cap
            block = "\n".join(parts).strip()
            if len(block) > MAX_EVENT_CHARS:
                block = block[:MAX_EVENT_CHARS].rstrip() + " … [truncated]"
            formatted.append(block)

        return "\n\n".join(formatted)

    def _format_ops_learning_for_assessment(self, ops_learning_data: List[Dict[str, Any]]) -> str:
        """Format operational learning data for capacity assessment context with source labels."""
        if not ops_learning_data:
            return "No operational learning data available from sources."

        formatted_learning = []
        # === UPDATED: allow up to 10 ops-learning items for conciseness ===
        for i, learning_item in enumerate(ops_learning_data[:10], 1):
            learning_text = (
                learning_item.get("learning_validated_en")
                or learning_item.get("learning_validated")
                or learning_item.get("learning_en")
                or "Unknown learning"
            )

            learning_info = [f"Learning {i}: {learning_text[:100]}{'...' if len(learning_text) > 100 else ''}"]

            appeal_info = learning_item.get("appeal", {})
            event_details = appeal_info.get("event_details", {})

            appeal_code = appeal_info.get("code")
            if appeal_code:
                learning_info.append(f"Appeal Code: {appeal_code}")
            elif appeal_info.get("name"):
                learning_info.append(f"Appeal: {appeal_info['name']}")

            if event_details.get("name"):
                learning_info.append(f"Event: {event_details['name']}")

            if appeal_info.get("start_date"):
                formatted_date = self._format_date_for_reference(appeal_info["start_date"])
                learning_info.append(f"Date: {formatted_date}")

            if learning_item.get("document_name"):
                learning_info.append(f"Document: {learning_item['document_name']}")

            source_note = learning_item.get("source_note")
            if source_note:
                learning_info.append(f"Context: {source_note}")

            formatted_learning.append(" | ".join(learning_info))

        return "\n".join(formatted_learning)


# Backwards-compatibility alias (older code imported AzureServiceClient under another name)
ResponseGenerationService = AzureServiceClient
