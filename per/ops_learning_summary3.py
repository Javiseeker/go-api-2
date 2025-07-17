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
    
    @staticmethod
    def get_latest_dref_version(dref_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get the latest DREF version based on operational updates.
        
        Steps:
        1. Check if operational_update_details has more than 1 object
        2. If yes, get the first position's id and check if it's published
        3. If yes, use dref manager and dref filter to get the latest DREF values
        4. If none of the above matched, return the current dref_data
        """
        logger.info("Starting DREF version resolution")
        
        try:
            # Step 1: Check if operational_update_details has more than 1 object
            operational_updates = dref_data.get('operational_update_details', [])
            
            if not operational_updates or len(operational_updates) <= 1:
                logger.info("No operational updates or only one update found, using current DREF data")
                return dref_data
            
            # Step 2: Get the first position's id and check if it's published
            first_update = operational_updates[0]
            update_id = first_update.get('id')
            is_published = first_update.get('is_published', False)
            
            if not update_id or not is_published:
                logger.info(f"First operational update (id: {update_id}) is not published, using current DREF data")
                return dref_data
            
            # Step 3: Use dref manager to get the latest DREF values from op-update source
            logger.info(f"Attempting to get updated DREF data using dref manager for update id: {update_id}")
            
            try:
                # Create filter for the specific operational update ID
                update_filter = DREFFilters(id=update_id)
                
                # Try to get the updated DREF data from op-update source
                updated_dref_list = dref_manager.get_data('op-update', update_filter)
                
                if updated_dref_list:
                    # Convert the first result back to dict format for consistency
                    updated_dref_data = updated_dref_list[0]
                    logger.info(f"Successfully retrieved updated DREF data for operational update id: {update_id}")
                    
                    # Convert DREFData object back to dict format
                    updated_dict = {
                        'id': updated_dref_data.id,
                        'title': updated_dref_data.title,
                        'operation_objective': getattr(updated_dref_data, 'operation_objective', None),
                        'response_strategy': getattr(updated_dref_data, 'response_strategy', None),
                        'amount_requested': updated_dref_data.amount_requested,
                        'total_targeted_population': updated_dref_data.total_targeted_population,
                        'operation_timeframe': getattr(updated_dref_data, 'operation_timeframe', None),
                        'country_details': {
                            'name': updated_dref_data.country_details.name,
                            'iso': updated_dref_data.country_details.iso
                        },
                        'disaster_type_details': {
                            'name': updated_dref_data.disaster_type_details.name
                        },
                        'event_date': updated_dref_data.event_date,
                        'end_date': getattr(updated_dref_data, 'end_date', None),
                        'planned_interventions': updated_dref_data.planned_interventions,
                        'national_society_actions': updated_dref_data.national_society_actions,
                        'needs_identified': updated_dref_data.needs_identified,
                        'people_in_need': getattr(updated_dref_data, 'people_in_need', None),
                        'human_resource': getattr(updated_dref_data, 'human_resource', None),
                        'logistic_capacity_of_ns': getattr(updated_dref_data, 'logistic_capacity_of_ns', None),
                        'pmer': getattr(updated_dref_data, 'pmer', None),
                        'type_of_dref_display': updated_dref_data.type_of_dref_display,
                        'type_of_onset_display': updated_dref_data.type_of_onset_display,
                        'created_at': updated_dref_data.created_at,
                        'operational_update_details': getattr(updated_dref_data, 'operational_update_details', [])
                    }
                    
                    return updated_dict
                else:
                    logger.warning(f"No updated DREF data found for operational update id: {update_id}")
                    
            except Exception as e:
                logger.error(f"Error retrieving updated DREF data: {e}")
                
        except Exception as e:
            logger.error(f"Error in DREF version resolution: {e}", exc_info=True)
        
        # Step 4: If none of the above matched, return current dref_data
        logger.info("Using current DREF data as fallback")
        return dref_data
    
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
        "- Maximum 3-4 sentences\n"
        "- Include specific needs and vulnerabilities\n"
        "- Focus on humanitarian gaps and operational requirements\n\n"
        "Example:\n"
        "The affected population faces critical water and sanitation challenges with 15,000 people lacking access to safe drinking water.\n"
        "Emergency shelter needs are urgent as 3,000 families remain displaced in overcrowded temporary accommodations.\n"
        "Health services require immediate strengthening to address increasing cases of waterborne diseases among vulnerable groups."
    )
    
    actions_taken_summary_prompt = (
        "\nAnalyze the national society actions data and create a concise summary:\n"
        "Focus on key actions implemented, resources deployed, and achievements made.\n\n"
        "Requirements:\n"
        "- Plain text format (not JSON)\n"
        "- Maximum 3-4 sentences\n"
        "- Include specific actions and outcomes\n"
        "- Highlight operational effectiveness and impact\n\n"
        "Example:\n"
        "The National Society has deployed 150 volunteers to distribute emergency relief items to 5,000 affected families.\n"
        "Mobile health units have been established in 3 affected districts providing basic healthcare services to vulnerable populations.\n"
        "Emergency communication systems have been activated to coordinate response activities with local authorities and partners."
    )
    
    @classmethod
    def generate_sector_summaries(cls, dref_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate sector-based summaries from DREF data"""
        print(f"\n🔍 GENERATE_SECTOR_SUMMARIES: Starting with dref_data keys: {list(dref_data.keys())}")
        logger.info("Generating sector-based summaries")
        
        sectors = []
        
        # Get sector data organized by title
        sector_data = cls.organize_data_by_sector(dref_data)
        print(f"🔍 GENERATE_SECTOR_SUMMARIES: organize_data_by_sector returned {len(sector_data)} sectors")
        print(f"🔍 GENERATE_SECTOR_SUMMARIES: sector_data keys: {list(sector_data.keys())}")
        
        for sector_title, sector_info in sector_data.items():
            print(f"\n🔍 PROCESSING SECTOR: {sector_title}")
            print(f"🔍 SECTOR INFO: actions={len(sector_info.get('actions', []))}, needs={len(sector_info.get('needs', []))}, planned_interventions={len(sector_info.get('planned_interventions', []))}")
            
            try:
                sector_summary = {
                    "title": sector_title,
                    "actions_taken_summary": "",
                    "needs_summary": "",
                    "future_actions": []
                }
                
                # Generate needs summary
                if sector_info.get('needs'):
                    print(f"🔍 SECTOR {sector_title}: Generating needs summary for {len(sector_info['needs'])} needs")
                    needs_summary = cls.generate_needs_summary(sector_info['needs'])
                    print(f"🔍 SECTOR {sector_title}: Needs summary result: {needs_summary[:100] if needs_summary else 'None'}...")
                    if needs_summary:
                        sector_summary["needs_summary"] = needs_summary
                else:
                    print(f"🔍 SECTOR {sector_title}: No needs data found")
                
                # Generate actions taken summary
                if sector_info.get('actions'):
                    print(f"🔍 SECTOR {sector_title}: Generating actions summary for {len(sector_info['actions'])} actions")
                    actions_summary = cls.generate_actions_summary(sector_info['actions'])
                    print(f"🔍 SECTOR {sector_title}: Actions summary result: {actions_summary[:100] if actions_summary else 'None'}...")
                    if actions_summary:
                        sector_summary["actions_taken_summary"] = actions_summary
                else:
                    print(f"🔍 SECTOR {sector_title}: No actions data found")
                
                # Process planned interventions for future actions
                if sector_info.get('planned_interventions'):
                    print(f"🔍 SECTOR {sector_title}: Processing {len(sector_info['planned_interventions'])} planned interventions")
                    future_actions = cls.process_planned_interventions(sector_info['planned_interventions'])
                    print(f"🔍 SECTOR {sector_title}: Future actions result: {len(future_actions)} actions")
                    sector_summary["future_actions"] = future_actions
                else:
                    print(f"🔍 SECTOR {sector_title}: No planned interventions found")
                
                print(f"🔍 SECTOR {sector_title}: Final sector_summary: {sector_summary}")
                sectors.append(sector_summary)
                
            except Exception as e:
                print(f"❌ ERROR processing sector {sector_title}: {e}")
                logger.error(f"Error processing sector {sector_title}: {e}")
                continue
        
        print(f"\n🔍 GENERATE_SECTOR_SUMMARIES: Final result - {len(sectors)} sectors generated")
        for i, sector in enumerate(sectors):
            print(f"🔍 SECTOR {i+1}: {sector['title']} - needs: {bool(sector['needs_summary'])}, actions: {bool(sector['actions_taken_summary'])}, future: {len(sector['future_actions'])}")
        
        logger.info(f"Generated {len(sectors)} sector summaries")
        return sectors
    
    @classmethod
    def organize_data_by_sector(cls, dref_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Organize DREF data by sector"""
        print(f"\n🔍 ORGANIZE_DATA_BY_SECTOR: Starting with dref_data keys: {list(dref_data.keys())}")
        sector_data = {}
        
        # Process national society actions
        actions = dref_data.get('national_society_actions', [])
        print(f"🔍 ORGANIZE_DATA_BY_SECTOR: Found {len(actions)} national_society_actions")
        for i, action in enumerate(actions):
            sector_title = action.get('title', 'unknown')
            print(f"🔍 ACTION {i+1}: title='{sector_title}', keys={list(action.keys())}")
            if sector_title not in sector_data:
                sector_data[sector_title] = {'actions': [], 'needs': [], 'planned_interventions': []}
            sector_data[sector_title]['actions'].append(action)
        
        # Process needs identified
        needs = dref_data.get('needs_identified', [])
        print(f"🔍 ORGANIZE_DATA_BY_SECTOR: Found {len(needs)} needs_identified")
        for i, need in enumerate(needs):
            sector_title = need.get('title', 'unknown')
            print(f"🔍 NEED {i+1}: title='{sector_title}', keys={list(need.keys())}")
            if sector_title not in sector_data:
                sector_data[sector_title] = {'actions': [], 'needs': [], 'planned_interventions': []}
            sector_data[sector_title]['needs'].append(need)
        
        # Process planned interventions
        interventions = dref_data.get('planned_interventions', [])
        print(f"🔍 ORGANIZE_DATA_BY_SECTOR: Found {len(interventions)} planned_interventions")
        for i, intervention in enumerate(interventions):
            sector_title = intervention.get('title', 'unknown')
            print(f"🔍 INTERVENTION {i+1}: title='{sector_title}', keys={list(intervention.keys())}")
            if sector_title not in sector_data:
                sector_data[sector_title] = {'actions': [], 'needs': [], 'planned_interventions': []}
            sector_data[sector_title]['planned_interventions'].append(intervention)
        
        print(f"🔍 ORGANIZE_DATA_BY_SECTOR: Final sector_data has {len(sector_data)} sectors:")
        for sector_title, data in sector_data.items():
            print(f"🔍   - {sector_title}: actions={len(data['actions'])}, needs={len(data['needs'])}, interventions={len(data['planned_interventions'])}")
        
        return sector_data
    
    @classmethod
    def generate_needs_summary(cls, needs_data: List[Dict[str, Any]]) -> Optional[str]:
        """Generate needs summary using LLM"""
        print(f"\n🔍 GENERATE_NEEDS_SUMMARY: Starting with {len(needs_data)} needs")
        if not needs_data:
            print("🔍 GENERATE_NEEDS_SUMMARY: No needs data provided, returning None")
            return None
        
        try:
            # Combine all needs descriptions
            combined_needs = "\n".join([need.get('description', '') for need in needs_data if need.get('description')])
            print(f"🔍 GENERATE_NEEDS_SUMMARY: Combined needs length: {len(combined_needs)} characters")
            print(f"🔍 GENERATE_NEEDS_SUMMARY: Combined needs preview: {combined_needs[:200]}...")
            
            if not combined_needs.strip():
                print("🔍 GENERATE_NEEDS_SUMMARY: No description content found, returning None")
                return None
            
            messages = [
                {"role": "system", "content": cls.system_message},
                {"role": "user", "content": f"Needs data:\n{combined_needs}\n\n{cls.needs_summary_prompt}"},
                {"role": "assistant", "content": "I understand. I will analyze the needs data and provide a structured summary according to your specifications."}
            ]
            
            print("🔍 GENERATE_NEEDS_SUMMARY: Calling Azure OpenAI...")
            client = AzureOpenAiChat()
            response = client.get_response(messages)
            print(f"🔍 GENERATE_NEEDS_SUMMARY: Azure OpenAI response: {response[:200] if response else 'None'}...")
            return response.strip() if response else None
            
        except Exception as e:
            print(f"❌ ERROR in generate_needs_summary: {e}")
            logger.error(f"Error generating needs summary: {e}")
            return None
    
    @classmethod
    def generate_actions_summary(cls, actions_data: List[Dict[str, Any]]) -> Optional[str]:
        """Generate actions taken summary using LLM"""
        print(f"\n🔍 GENERATE_ACTIONS_SUMMARY: Starting with {len(actions_data)} actions")
        if not actions_data:
            print("🔍 GENERATE_ACTIONS_SUMMARY: No actions data provided, returning None")
            return None
        
        try:
            # Combine all actions descriptions
            combined_actions = "\n".join([action.get('description', '') for action in actions_data if action.get('description')])
            print(f"🔍 GENERATE_ACTIONS_SUMMARY: Combined actions length: {len(combined_actions)} characters")
            print(f"🔍 GENERATE_ACTIONS_SUMMARY: Combined actions preview: {combined_actions[:200]}...")
            
            if not combined_actions.strip():
                print("🔍 GENERATE_ACTIONS_SUMMARY: No description content found, returning None")
                return None
            
            messages = [
                {"role": "system", "content": cls.system_message},
                {"role": "user", "content": f"Actions data:\n{combined_actions}\n\n{cls.actions_taken_summary_prompt}"},
                {"role": "assistant", "content": "I understand. I will analyze the actions data and provide a structured summary according to your specifications."}
            ]
            
            print("🔍 GENERATE_ACTIONS_SUMMARY: Calling Azure OpenAI...")
            client = AzureOpenAiChat()
            response = client.get_response(messages)
            print(f"🔍 GENERATE_ACTIONS_SUMMARY: Azure OpenAI response: {response[:200] if response else 'None'}...")
            return response.strip() if response else None
            
        except Exception as e:
            print(f"❌ ERROR in generate_actions_summary: {e}")
            logger.error(f"Error generating actions summary: {e}")
            return None
    
    @classmethod
    def process_planned_interventions(cls, interventions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process planned interventions into future actions format"""
        print(f"\n🔍 PROCESS_PLANNED_INTERVENTIONS: Starting with {len(interventions)} interventions")
        future_actions = []
        
        for i, intervention in enumerate(interventions):
            print(f"🔍 INTERVENTION {i+1}: keys={list(intervention.keys())}")
            try:
                # Extract indicators
                indicators = []
                intervention_indicators = intervention.get('indicators', [])
                print(f"🔍 INTERVENTION {i+1}: Found {len(intervention_indicators)} indicators")
                
                for j, indicator in enumerate(intervention_indicators):
                    print(f"🔍 INDICATOR {j+1}: keys={list(indicator.keys())}, title='{indicator.get('title', '')}', target={indicator.get('target', 0)}")
                    indicators.append({
                        "title": indicator.get('title', ''),
                        "people_targeted": indicator.get('target', 0)
                    })
                
                # Calculate total people targeted
                people_targeted_total = intervention.get('person_targeted', 0)
                budget = intervention.get('budget', 0)
                description = intervention.get('description', '')
                
                print(f"🔍 INTERVENTION {i+1}: budget={budget}, people_targeted_total={people_targeted_total}")
                print(f"🔍 INTERVENTION {i+1}: description preview: {description[:100]}...")
                
                future_action = {
                    "indicators": indicators,
                    "budget": budget,
                    "description": description,
                    "people_targeted_total": people_targeted_total
                }
                
                print(f"🔍 INTERVENTION {i+1}: Created future_action: {future_action}")
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
        print(f"\n🔍 GENERATE_DREF_SUMMARIES: Starting with dref_data keys: {list(dref_data.keys())}")
        print(f"🔍 GENERATE_DREF_SUMMARIES: dref_data id: {dref_data.get('id')}")
        print(f"🔍 GENERATE_DREF_SUMMARIES: dref_data title: {dref_data.get('title')}")
        logger.info("Starting DREF summary generation")
        
        result = {
            "operational_summary": None,
            "sectors": [],
            "status": "pending",
            "errors": []
        }
        print(f"🔍 GENERATE_DREF_SUMMARIES: Initial result structure: {result}")
        
        try:
            # Pre-processing: Get the latest DREF version
            print("🔍 GENERATE_DREF_SUMMARIES: Pre-processing - Getting latest DREF version")
            logger.info("Pre-processing: Getting latest DREF version")
            latest_dref_data = cls.get_latest_dref_version(dref_data)
            print(f"🔍 GENERATE_DREF_SUMMARIES: Latest DREF data keys: {list(latest_dref_data.keys())}")
            print(f"🔍 GENERATE_DREF_SUMMARIES: Latest DREF data id: {latest_dref_data.get('id')}")
            
            # Generate operational summary
            print("🔍 GENERATE_DREF_SUMMARIES: Generating operational summary")
            operational_summary = cls.generate_operational_summary(latest_dref_data)
            print(f"🔍 GENERATE_DREF_SUMMARIES: Operational summary result: {operational_summary[:100] if operational_summary else 'None'}...")
            if operational_summary:
                result["operational_summary"] = operational_summary
                print("🔍 GENERATE_DREF_SUMMARIES: Operational summary added to result")
                logger.info("Operational summary generated successfully")
            else:
                result["errors"].append("Failed to generate operational summary")
                print("❌ GENERATE_DREF_SUMMARIES: Failed to generate operational summary")
                logger.error("Failed to generate operational summary")
            
            # Generate sector summaries
            print("🔍 GENERATE_DREF_SUMMARIES: Generating sector summaries")
            sectors = cls.generate_sector_summaries(latest_dref_data)
            print(f"🔍 GENERATE_DREF_SUMMARIES: generate_sector_summaries returned {len(sectors)} sectors")
            print(f"🔍 GENERATE_DREF_SUMMARIES: Sectors content: {sectors}")
            if sectors:
                result["sectors"] = sectors
                print(f"🔍 GENERATE_DREF_SUMMARIES: {len(sectors)} sectors added to result")
                logger.info(f"Generated {len(sectors)} sector summaries")
            else:
                result["errors"].append("Failed to generate sector summaries")
                print("❌ GENERATE_DREF_SUMMARIES: Failed to generate sector summaries (empty sectors list)")
                logger.error("Failed to generate sector summaries")
            
            # Set status
            print(f"🔍 GENERATE_DREF_SUMMARIES: Setting status - operational_summary: {bool(result['operational_summary'])}, sectors: {len(result['sectors'])}")
            if result["operational_summary"] and result["sectors"]:
                result["status"] = "success"
                print("🔍 GENERATE_DREF_SUMMARIES: Status set to 'success'")
            elif result["operational_summary"] or result["sectors"]:
                result["status"] = "partial_success"
                print("🔍 GENERATE_DREF_SUMMARIES: Status set to 'partial_success'")
            else:
                result["status"] = "failed"
                print("🔍 GENERATE_DREF_SUMMARIES: Status set to 'failed'")
                
        except Exception as e:
            print(f"❌ GENERATE_DREF_SUMMARIES: Exception occurred: {e}")
            logger.error(f"Error in DREF summary generation: {e}", exc_info=True)
            result["status"] = "failed"
            result["errors"].append(f"Unexpected error: {str(e)}")
        
        print(f"🔍 GENERATE_DREF_SUMMARIES: Final result status: {result['status']}")
        print(f"🔍 GENERATE_DREF_SUMMARIES: Final result operational_summary: {bool(result['operational_summary'])}")
        print(f"🔍 GENERATE_DREF_SUMMARIES: Final result sectors count: {len(result['sectors'])}")
        print(f"🔍 GENERATE_DREF_SUMMARIES: Final result errors: {result['errors']}")
        logger.info(f"DREF summary generation completed with status: {result['status']}")
        return result