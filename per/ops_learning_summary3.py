import json
import tiktoken
from typing import Dict, Any, Optional, List

from django.conf import settings
from django.utils.functional import cached_property
from openai import AzureOpenAI

from api.logger import logger

from per.dref_temp.dref_utils import dref_manager
from per.dref_temp.dref_utils import DREFFilters


class AzureOpenAiChat:
    """Azure OpenAI client for DREF summary generation"""

    @cached_property
    def client(self):
        return AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT, 
            api_key=settings.AZURE_OPENAI_KEY, 
            api_version="2023-05-15"
        )

    def get_response(self, message):
        try:
            response = self.client.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT_NAME, 
                messages=message, 
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error while generating DREF summary response: {e}", exc_info=True)
            return None


class DrefSummaryTask:
    """Task class for generating DREF operation summaries using Azure OpenAI"""
    
    PROMPT_DATA_LENGTH_LIMIT = 8000
    PROMPT_LENGTH_LIMIT = 10000
    ENCODING_NAME = "cl100k_base"
    
    # System message for DREF summaries
    system_message = (
        "You are an IFRC expert analyst specializing in DREF operations. "
        "Analyze DREF data and provide clear, actionable insights for humanitarian response planning. "
        "Use professional, analytical writing accessible to IFRC staff and National Society personnel."
    )

    # Operational summary prompt (3 lines max)
    operational_summary_prompt = (
        "\nAnalyze the DREF data and create a 3-line summary:\n"
        "Line 1: Overall objective of the operation\n"
        "Line 2: Strategic rationale and approach\n"
        "Line 3: Key operational details (target population, timeline, budget)\n\n"
        "Requirements:\n"
        "- Plain text format (not JSON)\n"
        "- Exactly 3 lines, one complete sentence each\n"
        "- Include specific numbers and details\n"
        "- Focus on operation_objective and response_strategy fields\n\n"
        "Example:\n"
        "The operation aims to provide emergency assistance to 5,000 flood-affected people in Bangladesh through cash transfers and relief items.\n"
        "The strategy prioritizes rapid response through existing National Society networks and coordination with local authorities to ensure efficient delivery.\n"
        "The 4-month operation targets vulnerable households in 3 districts with a budget of CHF 250,000 focusing on immediate basic needs."
    )


    # !!!!!!!!!!!!!! TO BE CHECKED !!!!!!!!!!
    # Planned intervention summary prompt
    planned_intervention_summary_prompt = (
        "\nAnalyze the DREF planned interventions data and create a comprehensive summary of intervention activities:\n\n"
        "Provide a detailed analysis of each planned intervention including:\n"
        "- Intervention objectives and rationale\n"
        "- Target populations and geographic scope\n"
        "- Key activities and implementation approach\n"
        "- Expected outcomes and impact\n"
        "- Budget allocation and resource requirements\n"
        "- Timeline and implementation phases\n"
        "- Risk factors and mitigation strategies\n"
        "- Coordination mechanisms and partnerships\n\n"
        "Requirements:\n"
        "- Plain text format (not JSON)\n"
        "- Structure as clear sections for each intervention\n"
        "- Include specific numbers, timelines, and budget figures\n"
        "- Focus on planned_interventions field data\n"
        "- Highlight cross-cutting themes and synergies\n"
        "- Provide strategic analysis of intervention portfolio\n\n"
        "Format each intervention as:\n"
        "INTERVENTION: [Title]\n"
        "Objective: [Clear statement of what this intervention aims to achieve]\n"
        "Target: [Population numbers, demographics, geographic areas]\n"
        "Activities: [Key implementation activities and approach]\n"
        "Budget: [Allocation amount and percentage of total]\n"
        "Timeline: [Implementation phases and duration]\n"
        "Partnerships: [Key implementing partners and coordination mechanisms]\n\n"
        "End with a STRATEGIC OVERVIEW section summarizing the overall intervention strategy and expected collective impact."
    )
    
    @staticmethod
    def count_tokens(string: str, encoding_name: str) -> int:
        """Returns the number of tokens in a text string."""
        encoding = tiktoken.get_encoding(encoding_name)
        return len(encoding.encode(string))
    
    @classmethod
    def generate_operational_summary(cls, dref_data: Dict[str, Any]) -> Optional[str]:
        """Generate operational objective and strategy summary (3 lines max)"""
        logger.info("Generating DREF operational summary")
        
        # Extract operational data
        operational_fields = [
            'operation_objective', 'response_strategy', 'title', 'total_targeted_population',
            'people_in_need', 'amount_requested', 'operation_timeframe', 'country_details',
            'disaster_type_details', 'event_date', 'end_date'
        ]
        
        extracted_data = {}
        for field in operational_fields:
            if field in dref_data and dref_data[field] is not None:
                extracted_data[field] = dref_data[field]
        
        data_json = json.dumps(extracted_data, indent=2, ensure_ascii=False)
        
        # Create messages
        messages = [
            {"role": "system", "content": cls.system_message},
            {"role": "user", "content": f"DREF Data to analyze:\n{data_json}\n\n{cls.operational_summary_prompt}"},
            {"role": "assistant", "content": "I understand. I will analyze the DREF data and provide a structured summary according to your specifications."}
        ]
        
        # Token count validation
        message_content = [msg["content"] for msg in messages]
        text = " ".join(message_content)
        token_count = cls.count_tokens(text, cls.ENCODING_NAME)
        logger.info(f"DREF operational token count: {token_count}")
        
        if token_count > cls.PROMPT_LENGTH_LIMIT:
            logger.warning("Prompt too long for operational summary, truncating data")
            truncated_data = data_json[:cls.PROMPT_DATA_LENGTH_LIMIT]
            messages[1]["content"] = f"DREF Data to analyze:\n{truncated_data}\n\n{cls.operational_summary_prompt}"
        
        # Call OpenAI backend
        try:
            client = AzureOpenAiChat()
            response = client.get_response(messages)
            
            if not response:
                logger.error("No response received for operational summary")
                return None
            
            # Process operational response - take only first 3 non-empty lines
            lines = response.strip().split('\n')
            filtered_lines = [line.strip() for line in lines if line.strip()][:3]
            result = '\n'.join(filtered_lines)
            
            logger.info("Successfully generated operational summary")
            return result
            
        except Exception as e:
            logger.error(f"Error generating operational summary: {e}", exc_info=True)
            return None


    # Sector-based summary prompts following operational_summary_prompt structure
    needs_summary_prompt = (
        "\nAnalyze the needs identified data and create a concise summary:\n"
        "Focus on key challenges, gaps, and priority needs for humanitarian response.\n\n"
        "Requirements:\n"
        "- Plain text format (not JSON)\n"
        "- Maximum 2 sentences\n"
        "- Include specific needs and vulnerabilities\n"
        "- Focus on humanitarian gaps and operational requirements\n"
        "- No extra spaces or line breaks\n\n"
        "Example:\n"
        "The affected population faces critical water and sanitation challenges with 15,000 people lacking access to safe drinking water. Emergency shelter needs are urgent as 3,000 families remain displaced in overcrowded temporary accommodations."
    )
    
    actions_taken_summary_prompt = (
        "\nAnalyze the national society actions data and create a concise summary:\n"
        "Focus on key actions implemented, resources deployed, and achievements made.\n\n"
        "Requirements:\n"
        "- Plain text format (not JSON)\n"
        "- Maximum 2 sentences\n"
        "- Include specific actions and outcomes\n"
        "- Highlight operational effectiveness and impact\n"
        "- No extra spaces or line breaks\n\n"
        "Example:\n"
        "The National Society has deployed 150 volunteers to distribute emergency relief items to 5,000 affected families. Mobile health units have been established in 3 affected districts providing basic healthcare services to vulnerable populations."
    )
    
    @classmethod
    def generate_sector_summaries(cls, dref_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate sector-based summaries from DREF data"""
        logger.info("Generating sector-based summaries")
        
        sectors = []
        
        # Get sector data organized by title
        sector_data = cls.organize_data_by_sector(dref_data)
        
        for sector_title, sector_info in sector_data.items():
            try:
                # Get title_display (use first available title_display from any item in this sector)
                title_display = sector_title
                for item_list in [sector_info.get('actions', []), sector_info.get('needs', []), sector_info.get('planned_interventions', [])]:
                    for item in item_list:
                        if hasattr(item, 'title_display'):
                            title_display = getattr(item, 'title_display', sector_title)
                            break
                        elif isinstance(item, dict) and 'title_display' in item:
                            title_display = item['title_display']
                            break
                    if title_display != sector_title:
                        break
                
                sector_summary = {
                    "title": sector_title,
                    "title_display": title_display,
                    "actions_taken_summary": "",
                    "needs_summary": "",
                    "future_actions": []
                }
                
                # Generate needs summary using LLM
                if sector_info.get('needs'):
                    needs_summary = cls.generate_needs_summary(sector_info['needs'])
                    if needs_summary:
                        sector_summary["needs_summary"] = needs_summary
                    else:
                        print(f"NEEDS_SUMMARY: {sector_title} - FAILED Empty/None")
                else:
                    print(f"NEEDS_SUMMARY: {sector_title} - No needs data found")
                
                # Generate actions taken summary using LLM (combine actions + planned interventions)
                combined_actions_data = []
                if sector_info.get('actions'):
                    combined_actions_data.extend(sector_info['actions'])
                if sector_info.get('planned_interventions'):
                    combined_actions_data.extend(sector_info['planned_interventions'])
                
                if combined_actions_data:
                    actions_taken_summary = cls.generate_actions_taken_summary(combined_actions_data)
                    if actions_taken_summary:
                        sector_summary["actions_taken_summary"] = actions_taken_summary
                
                # Process planned interventions for future actions
                if sector_info.get('planned_interventions'):
                    future_actions = cls.process_planned_interventions(sector_info['planned_interventions'])
                    sector_summary["future_actions"] = future_actions
                
                sectors.append(sector_summary)
                
            except Exception as e:
                logger.error(f"Error processing sector {sector_title}: {e}")
                continue
        
        logger.info(f"Generated {len(sectors)} sector summaries")
        return sectors
    
    @classmethod
    def organize_data_by_sector(cls, dref_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Organize DREF data by sector - prioritizing planned_interventions as sector definitions"""
        sector_data = {}
        
        # STEP 1: Process planned interventions FIRST to define sectors
        interventions = dref_data.get('planned_interventions', [])
        for i, intervention in enumerate(interventions):
            # Handle both dict and dataclass objects
            if hasattr(intervention, 'title'):
                sector_title = getattr(intervention, 'title', 'unknown')
            else:
                sector_title = intervention.get('title', 'unknown')
            if sector_title not in sector_data:
                sector_data[sector_title] = {'actions': [], 'needs': [], 'planned_interventions': []}
            sector_data[sector_title]['planned_interventions'].append(intervention)
        
        # STEP 2: Match needs_identified to sectors defined by planned_interventions
        needs = dref_data.get('needs_identified', [])
        
        for i, need in enumerate(needs):
            # Handle both dict and dataclass objects
            if hasattr(need, 'title'):
                sector_title = getattr(need, 'title', 'unknown')
            else:
                sector_title = need.get('title', 'unknown')
            
            # Try exact match first
            if sector_title in sector_data:
                sector_data[sector_title]['needs'].append(need)
            else:
                # Try fuzzy matching for common mismatches
                matched = False
                for existing_sector in sector_data.keys():
                    if cls._sectors_match(sector_title, existing_sector):
                        sector_data[existing_sector]['needs'].append(need)
                        matched = True
                        break
                
                if not matched:
                    print(f"NEEDS_MATCHING: NO MATCH '{sector_title}' - skipping need {i+1}")
        
        # STEP 3: Match national society actions to sectors
        actions = dref_data.get('national_society_actions', [])
        for i, action in enumerate(actions):
            # Handle both dict and dataclass objects
            if hasattr(action, 'title'):
                sector_title = getattr(action, 'title', 'unknown')
            else:
                sector_title = action.get('title', 'unknown')
            
            # Only add actions if the sector was defined by planned_interventions
            if sector_title in sector_data:
                sector_data[sector_title]['actions'].append(action)
        
        return sector_data
    
    @classmethod
    def _sectors_match(cls, need_title: str, intervention_title: str) -> bool:
        """Check if sector titles match with fuzzy logic for common mismatches"""
        # Common title mappings
        mappings = {
            'multi_purpose_cash_grants': 'multi_purpose_cash',
            'shelter_housing_and_settlements': 'shelter',
            'water_sanitation_and_hygiene': 'wash',
            'livelihoods_and_basic_needs': 'livelihoods',
            'protection_gender_and_inclusion': 'protection',
            'coordination_and_partnerships': 'coordination',
            'disaster_risk_reduction': 'drr',
            'migration_and_displacement': 'migration'
        }
        
        # Check direct mapping (need_title -> intervention_title)
        if need_title in mappings and mappings[need_title] == intervention_title:
            return True
        
        # Check reverse mapping (intervention_title -> need_title)
        if intervention_title in mappings and mappings[intervention_title] == need_title:
            return True
        
        # Special case: multi_purpose_cash_grants <-> multi_purpose_cash
        if (need_title == 'multi_purpose_cash_grants' and intervention_title == 'multi_purpose_cash') or \
           (need_title == 'multi_purpose_cash' and intervention_title == 'multi_purpose_cash_grants'):
            return True
        
        # Check if one contains the other (partial match)
        if need_title in intervention_title or intervention_title in need_title:
            return True
            
        return False
    
    @classmethod
    def _create_fallback_needs_summary(cls, combined_needs: str) -> str:
        """Create a fallback summary when LLM is not available"""
        # Extract key phrases and create a simple summary
        sentences = combined_needs.split('.')
        key_sentences = []
        
        for sentence in sentences[:3]:  # Take first 3 sentences
            sentence = sentence.strip()
            if len(sentence) > 20:  # Only meaningful sentences
                key_sentences.append(sentence)
        
        if key_sentences:
            return '. '.join(key_sentences[:2]) + '.'  # Max 2 sentences
        else:
            return "Critical needs have been identified requiring immediate humanitarian response."
    
    @classmethod
    def generate_needs_summary(cls, needs_data: List[Dict[str, Any]]) -> Optional[str]:
        """Generate needs summary using LLM"""
        if not needs_data:
            return None
        
        try:
            # Combine all needs descriptions
            combined_needs = "\n".join([
                getattr(need, 'description', '') if hasattr(need, 'description') else need.get('description', '')
                for need in needs_data
                if (getattr(need, 'description', '') if hasattr(need, 'description') else need.get('description', ''))
            ])
            
            if not combined_needs.strip():
                return None
            
            try:
                messages = [
                    {"role": "system", "content": cls.system_message},
                    {"role": "user", "content": f"Needs data:\n{combined_needs}\n\n{cls.needs_summary_prompt}"},
                    {"role": "assistant", "content": "I understand. I will analyze the needs data and provide a structured summary according to your specifications."}
                ]
                
                client = AzureOpenAiChat()
                response = client.get_response(messages)
                # Clean response: strip whitespace and remove extra line breaks
                cleaned_response = ' '.join(response.strip().split()) if response else None
                return cleaned_response
            except Exception as llm_error:
                # Fallback: Create a simple summary from the needs descriptions
                print(f"NEEDS_SUMMARY: LLM failed, using fallback: {llm_error}")
                return cls._create_fallback_needs_summary(combined_needs)
            
        except Exception as e:
            logger.error(f"Error generating needs summary: {e}")
            return None
    
    @classmethod
    def generate_actions_summary(cls, actions_data: List[Dict[str, Any]]) -> Optional[str]:
        """Generate actions taken summary using LLM"""
        if not actions_data:
            print("🔍 GENERATE_ACTIONS_SUMMARY: No actions data provided, returning None")
            return None
        
        try:
            # Combine all actions descriptions
            combined_actions = "\n".join([
                getattr(action, 'description', '') if hasattr(action, 'description') else action.get('description', '')
                for action in actions_data
                if (getattr(action, 'description', '') if hasattr(action, 'description') else action.get('description', ''))
            ])
            
            if not combined_actions.strip():
                print("🔍 GENERATE_ACTIONS_SUMMARY: No description content found, returning None")
                return None
            
            messages = [
                {"role": "system", "content": cls.system_message},
                {"role": "user", "content": f"Actions data:\n{combined_actions}\n\n{cls.actions_taken_summary_prompt}"},
                {"role": "assistant", "content": "I understand. I will analyze the actions data and provide a structured summary according to your specifications."}
            ]
            
            client = AzureOpenAiChat()
            response = client.get_response(messages)
            # Clean response: strip whitespace and remove extra line breaks
            cleaned_response = ' '.join(response.strip().split()) if response else None
            return cleaned_response
            
        except Exception as e:
            print(f"❌ ERROR in generate_actions_summary: {e}")
            logger.error(f"Error generating actions summary: {e}")
            return None
    
    @classmethod
    def generate_actions_taken_summary(cls, combined_actions_data: List[Dict[str, Any]]) -> Optional[str]:
        """Generate actions taken summary using LLM (combines national_society_actions + planned_interventions)"""
        if not combined_actions_data:
            print("🔍 GENERATE_ACTIONS_TAKEN_SUMMARY: No combined actions data provided, returning None")
            return None
        
        try:
            # Combine all descriptions from both actions and planned interventions
            combined_descriptions = []
            for item in combined_actions_data:
                if hasattr(item, 'description'):
                    desc = getattr(item, 'description', '')
                else:
                    desc = item.get('description', '')
                
                if desc:
                    combined_descriptions.append(desc)
            
            combined_text = "\n".join(combined_descriptions)
            
            if not combined_text.strip():
                print("🔍 GENERATE_ACTIONS_TAKEN_SUMMARY: No description content found, returning None")
                return None
            
            # Create LLM prompt for actions taken summary
            actions_taken_prompt = """
            Based on the combined actions data provided (including both national society actions and planned interventions), 
            create a comprehensive summary of all actions taken or planned in this sector.
            
            Requirements:
            - Plain text format (not JSON)
            - Maximum 2 sentences
            - Describe key actions and interventions implemented or planned
            - Highlight main outcomes and impacts
            - Focus on what was done or will be done to address the needs
            - No extra spaces or line breaks
            
            Example:
            The National Society has provided emergency shelter assistance to 2,000 displaced families through distribution of tents and basic household items. Mobile health clinics have been deployed to affected areas, providing primary healthcare services to 5,000 vulnerable individuals.
            """
            
            messages = [
                {"role": "system", "content": cls.system_message},
                {"role": "user", "content": f"Combined actions data:\n{combined_text}\n\n{actions_taken_prompt}"},
                {"role": "assistant", "content": "I understand. I will analyze the combined actions data and provide a structured summary of actions taken."}
            ]
            
            client = AzureOpenAiChat()
            response = client.get_response(messages)
            # Clean response: strip whitespace and remove extra line breaks
            cleaned_response = ' '.join(response.strip().split()) if response else None
            return cleaned_response
            
        except Exception as e:
            logger.error(f"Error generating actions taken summary: {e}")
            return None
    
    @classmethod
    def process_planned_interventions(cls, interventions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process planned interventions into future actions format"""
        future_actions = []
        
        for i, intervention in enumerate(interventions):
            try:
                # Extract indicators
                indicators = []
                # Handle both dict and dataclass objects
                if hasattr(intervention, 'indicators'):
                    intervention_indicators = getattr(intervention, 'indicators', [])
                else:
                    intervention_indicators = intervention.get('indicators', [])
                
                for j, indicator in enumerate(intervention_indicators):
                    # Handle both dict and dataclass objects for indicators
                    if hasattr(indicator, 'title'):
                        indicator_title = getattr(indicator, 'title', '')
                        indicator_target = getattr(indicator, 'target', 0)
                    else:
                        indicator_title = indicator.get('title', '')
                        indicator_target = indicator.get('target', 0)
                    
                    indicators.append({
                        "title": indicator_title,
                        "people_targeted": indicator_target
                    })
                
                # Calculate total people targeted
                if hasattr(intervention, 'person_targeted'):
                    people_targeted_total = getattr(intervention, 'person_targeted', 0)
                    budget = getattr(intervention, 'budget', 0)
                    description = getattr(intervention, 'description', '')
                else:
                    people_targeted_total = intervention.get('person_targeted', 0)
                    budget = intervention.get('budget', 0)
                    description = intervention.get('description', '')
                
                future_action = {
                    "indicators": indicators,
                    "budget": budget,
                    "description": description,
                    "people_targeted_total": people_targeted_total
                }
                
                future_actions.append(future_action)
                
            except Exception as e:
                print(f"❌ ERROR processing planned intervention {i+1}: {e}")
                logger.error(f"Error processing planned intervention: {e}")
                continue
        
        print(f"🔍 PROCESS_PLANNED_INTERVENTIONS: Final result - {len(future_actions)} future actions")
        return future_actions
    
    @classmethod
    def generate_dref_summaries(cls, dref_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate operational and sector-based summaries"""
        logger.info("Starting DREF summary generation")
        
        result = {
            "operational_summary": None,
            "sectors": [],
            "status": "pending",
            "errors": []
        }
        
        try:
            operational_summary = cls.generate_operational_summary(dref_data)
            if operational_summary:
                result["operational_summary"] = operational_summary
            else:
                result["errors"].append("Failed to generate operational summary")
                print("❌ GENERATE_DREF_SUMMARIES: Failed to generate operational summary")
            
            sectors = cls.generate_sector_summaries(dref_data)

            if sectors:
                result["sectors"] = sectors
            else:
                result["errors"].append("Failed to generate sector summaries")
                print("❌ GENERATE_DREF_SUMMARIES: Failed to generate sector summaries (empty sectors list)")
                logger.error("Failed to generate sector summaries")
            
            if result["operational_summary"] and result["sectors"]:
                result["status"] = "success"
            elif result["operational_summary"] or result["sectors"]:
                result["status"] = "partial_success"
            else:
                result["status"] = "failed"
                
        except Exception as e:
            print(f"❌ GENERATE_DREF_SUMMARIES: Exception occurred: {e}")
            result["status"] = "failed"
            result["errors"].append(f"Unexpected error: {str(e)}")

        return result