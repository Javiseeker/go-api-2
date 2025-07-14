import json
import re
import tiktoken
from typing import Dict, Any, Optional

from django.conf import settings
from django.utils.functional import cached_property
from openai import AzureOpenAI

from api.logger import logger


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

    # Budget summary prompt (comprehensive JSON)
    budget_summary_prompt = (
        "\nAnalyze the DREF budget data and create a comprehensive financial summary in JSON format:\n\n"
        "{\n"
        '  "budget_overview": {\n'
        '    "total_allocation": "Total DREF amount with currency",\n'
        '    "operation_timeframe": "Duration in months",\n'
        '    "target_beneficiaries": "Number of people targeted",\n'
        '    "cost_per_beneficiary": "Cost per person assisted",\n'
        '    "funding_status": "Funding approval status"\n'
        '  },\n'
        '  "sectoral_breakdown": {\n'
        '    "summary": "Budget distribution across sectors",\n'
        '    "major_sectors": [{\n'
        '      "sector": "Sector name",\n'
        '      "budget": "Amount allocated",\n'
        '      "percentage": "% of total budget",\n'
        '      "target_beneficiaries": "People targeted",\n'
        '      "key_activities": "Main activities"\n'
        '    }],\n'
        '    "support_costs": "Administrative costs breakdown"\n'
        '  },\n'
        '  "financial_analysis": {\n'
        '    "summary": "Budget efficiency and allocation rationale",\n'
        '    "key_insights": ["Budget allocation priorities", "Resource distribution", "Cost-effectiveness", "Alignment with objectives"],\n'
        '    "resource_allocation_strategy": "Strategic allocation approach",\n'
        '    "cost_effectiveness_assessment": "Value for money analysis"\n'
        '  },\n'
        '  "operational_costs": {\n'
        '    "human_resources": "Staff costs",\n'
        '    "logistics_and_operations": "Operational costs",\n'
        '    "coordination_and_partnerships": "Coordination costs",\n'
        '    "monitoring_and_evaluation": "M&E costs"\n'
        '  },\n'
        '  "confidence_level": "High/Medium/Low",\n'
        '  "data_quality_notes": "Data limitations"\n'
        "}\n\n"
        "Requirements:\n"
        "- Calculate cost per beneficiary and percentages\n"
        "- Include specific budget figures\n"
        "- Analyze budget allocation logic\n"
        "- Note missing data clearly\n"
        "- Return only JSON, no commentary"
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

    @classmethod
    def generate_budget_summary(cls, dref_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Generate comprehensive budget and financial summary"""
        logger.info("Generating DREF budget summary")
        
        # Extract budget data
        budget_fields = [
            'amount_requested', 'total_dref_allocation', 'budget_file_details', 'budget_file_preview',
            'planned_interventions', 'total_targeted_population', 'people_in_need', 'operation_timeframe',
            'end_date', 'publishing_date', 'title', 'country_details', 'disaster_type_details',
            'human_resource', 'logistic_capacity_of_ns', 'coordination_and_partnerships', 'pmer'
        ]
        
        extracted_data = {}
        for field in budget_fields:
            if field in dref_data and dref_data[field] is not None:
                extracted_data[field] = dref_data[field]
        
        # Process planned interventions
        if 'planned_interventions' in extracted_data:
            budget_breakdown = []
            total_budget = 0
            
            for intervention in extracted_data['planned_interventions']:
                if isinstance(intervention, dict):
                    budget_item = {
                        'sector': intervention.get('title_display', ''),
                        'budget': intervention.get('budget', 0),
                        'target_population': intervention.get('person_targeted', 0),
                        'description': intervention.get('description', ''),
                        'indicators': intervention.get('indicators', []),
                        'title': intervention.get('title', '')
                    }
                    budget_breakdown.append(budget_item)
                    total_budget += budget_item['budget']
            
            extracted_data['budget_breakdown'] = budget_breakdown
            extracted_data['total_calculated_budget'] = total_budget
        
        data_json = json.dumps(extracted_data, indent=2, ensure_ascii=False)
        
        # Create messages
        messages = [
            {"role": "system", "content": cls.system_message},
            {"role": "user", "content": f"DREF Data to analyze:\n{data_json}\n\n{cls.budget_summary_prompt}"},
            {"role": "assistant", "content": "I understand. I will analyze the DREF data and provide a structured summary according to your specifications."}
        ]
        
        # Token count validation
        message_content = [msg["content"] for msg in messages]
        text = " ".join(message_content)
        token_count = cls.count_tokens(text, cls.ENCODING_NAME)
        logger.info(f"DREF budget token count: {token_count}")
        
        if token_count > cls.PROMPT_LENGTH_LIMIT:
            logger.warning("Prompt too long for budget summary, truncating data")
            truncated_data = data_json[:cls.PROMPT_DATA_LENGTH_LIMIT]
            messages[1]["content"] = f"DREF Data to analyze:\n{truncated_data}\n\n{cls.budget_summary_prompt}"
        
        # Call OpenAI backend
        try:
            client = AzureOpenAiChat()
            response = client.get_response(messages)
            
            if not response:
                logger.error("No response received for budget summary")
                return None
            
            # Process budget response (JSON format)
            try:
                # Clean up the response if needed
                cleaned_response = response.strip()
                if cleaned_response.startswith("```json"):
                    cleaned_response = cleaned_response.replace("```json", "").replace("```", "").strip()
                
                # Parse JSON
                parsed_response = json.loads(cleaned_response)
                
                if isinstance(parsed_response, dict):
                    logger.info("Successfully generated budget summary")
                    return parsed_response
                else:
                    logger.warning("Invalid response format for budget summary: not a dictionary")
                    return None
                    
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response for budget summary: {e}")
                # Try to extract JSON from the response
                try:
                    json_match = re.search(r'\{.*\}', response, re.DOTALL)
                    if json_match:
                        return json.loads(json_match.group())
                except:
                    pass
                return None
                
        except Exception as e:
            logger.error(f"Error generating budget summary: {e}", exc_info=True)
            return None

    @classmethod
    def generate_dref_summaries(cls, dref_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate both operational and budget summaries"""
        logger.info("Starting DREF summary generation")
        
        result = {
            "operational_summary": None,
            "budget_summary": None,
            "status": "pending",
            "errors": []
        }
        
        try:
            # Generate operational summary
            operational_summary = cls.generate_operational_summary(dref_data)
            if operational_summary:
                result["operational_summary"] = operational_summary
                logger.info("Operational summary generated successfully")
            else:
                result["errors"].append("Failed to generate operational summary")
                logger.error("Failed to generate operational summary")
            
            # Generate budget summary
            budget_summary = cls.generate_budget_summary(dref_data)
            if budget_summary:
                result["budget_summary"] = budget_summary
                logger.info("Budget summary generated successfully")
            else:
                result["errors"].append("Failed to generate budget summary")
                logger.error("Failed to generate budget summary")
            
            # Set status
            if result["operational_summary"] and result["budget_summary"]:
                result["status"] = "success"
            elif result["operational_summary"] or result["budget_summary"]:
                result["status"] = "partial_success"
            else:
                result["status"] = "failed"
                
        except Exception as e:
            logger.error(f"Error in DREF summary generation: {e}", exc_info=True)
            result["status"] = "failed"
            result["errors"].append(f"Unexpected error: {str(e)}")
        
        logger.info(f"DREF summary generation completed with status: {result['status']}")
        return result