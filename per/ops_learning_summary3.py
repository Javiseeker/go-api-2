import ast
import re
import json
import typing
from typing import Dict, List, Optional, Any

import tiktoken
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
        "# CONTEXT # You are an expert analyst for the International Federation of Red Cross and Red Crescent Societies (IFRC) "
        "specializing in Disaster Response Emergency Fund (DREF) operations. Your role is to analyze and summarize DREF operational data "
        "to provide actionable insights for disaster response planning and decision-making.\n"
        "# STYLE # Use a professional, clear, and analytical writing style that is accessible to humanitarian professionals.\n"
        "# TONE # Objective and informative, focusing on practical insights and strategic implications.\n"
        "# AUDIENCE # IFRC staff, National Society personnel, and humanitarian response coordinators who need concise, "
        "actionable information for operational planning and strategic decision-making."
    )

    # Short prompt for operational objective and strategy summary (max 3 lines)
    operational_summary_prompt = (
        "\nPlease analyze the provided DREF operational data and create a VERY BRIEF summary focusing on:\n"
        "1. **Overall Objective**: The main operational objectives of the DREF operation\n"
        "2. **Strategic Rationale**: The strategic reasoning behind the operation's approach\n\n"
        "IMPORTANT: Your response must be EXACTLY 3 lines maximum. Each line should be a complete sentence.\n"
        "Line 1: Summarize the overall objective of the operation\n"
        "Line 2: Explain the strategic rationale and approach\n"
        "Line 3: Highlight key operational details (target population, timeline, or scope)\n\n"
        "The output MUST be in plain text format, NOT JSON. Maximum 3 lines total.\n"
        "Example format:\n"
        "The operation aims to provide emergency assistance to 5,000 flood-affected people in Bangladesh through cash transfers and relief items.\n"
        "The strategy prioritizes rapid response through existing National Society networks and coordination with local authorities to ensure efficient delivery.\n"
        "The 4-month operation targets vulnerable households in 3 districts with a budget of CHF 250,000 focusing on immediate basic needs.\n\n"
        "Important guidelines:\n"
        "- Be extremely concise but informative\n"
        "- Focus on the most critical operational information\n"
        "- Use specific numbers and details where available\n"
        "- Do not exceed 3 lines under any circumstances\n"
        "- Reply with ONLY the 3-line summary, no additional text or formatting"
    )

    # Comprehensive prompt for budget and financial summary
    budget_summary_prompt = (
        "\nPlease analyze the provided DREF budget and financial data and create a comprehensive summary focusing on:\n"
        "1. **Budget Overview**: Total allocation, funding sources, and budget breakdown\n"
        "2. **Sectoral Analysis**: How funds are distributed across different sectors and interventions\n"
        "3. **Financial Efficiency**: Analysis of cost-effectiveness and resource allocation\n\n"
        "The output MUST strictly adhere to the following JSON format:\n"
        "{\n"
        '  "budget_overview": {\n'
        '    "total_allocation": "Total DREF amount requested/allocated with currency",\n'
        '    "operation_timeframe": "Duration of the operation in months",\n'
        '    "target_beneficiaries": "Number of people targeted",\n'
        '    "cost_per_beneficiary": "Calculated cost per person assisted",\n'
        '    "funding_status": "Current status of funding approval"\n'
        '  },\n'
        '  "sectoral_breakdown": {\n'
        '    "summary": "Detailed overview of how budget is distributed across sectors and interventions",\n'
        '    "major_sectors": [\n'
        '      {\n'
        '        "sector": "Sector name",\n'
        '        "budget": "Amount allocated",\n'
        '        "percentage": "Percentage of total budget",\n'
        '        "target_beneficiaries": "Number of people targeted in this sector",\n'
        '        "key_activities": "Main activities planned"\n'
        '      }\n'
        '    ],\n'
        '    "support_costs": "Administrative and operational support costs breakdown"\n'
        '  },\n'
        '  "financial_analysis": {\n'
        '    "summary": "Comprehensive analysis of budget efficiency, allocation rationale, and value for money",\n'
        '    "key_insights": [\n'
        '      "List of detailed insights about budget allocation priorities",\n'
        '      "Analysis of resource distribution across interventions",\n'
        '      "Assessment of operational efficiency and cost-effectiveness",\n'
        '      "Evaluation of budget alignment with operational objectives"\n'
        '    ],\n'
        '    "resource_allocation_strategy": "How resources are strategically allocated to maximize impact",\n'
        '    "cost_effectiveness_assessment": "Assessment of value for money and operational efficiency"\n'
        '  },\n'
        '  "operational_costs": {\n'
        '    "human_resources": "Staff and personnel costs breakdown",\n'
        '    "logistics_and_operations": "Operational and logistics costs",\n'
        '    "coordination_and_partnerships": "Coordination and partnership costs",\n'
        '    "monitoring_and_evaluation": "M&E and reporting costs"\n'
        '  },\n'
        '  "confidence_level": "High/Medium/Low based on data completeness and clarity",\n'
        '  "data_quality_notes": "Notes about data availability and any limitations in the analysis"\n'
        "}\n\n"
        "Important guidelines:\n"
        "- Provide detailed analysis with specific budget figures and percentages\n"
        "- Calculate cost per beneficiary and efficiency ratios where possible\n"
        "- Analyze the logical flow of budget allocation based on operational priorities\n"
        "- Include insights about resource optimization and strategic allocation\n"
        "- If certain financial data is not available, note it clearly\n"
        "- Ensure comprehensive coverage of all budget aspects and planned interventions\n"
        "- Reply with ONLY the JSON response, no additional commentary"
    )

    @staticmethod
    def count_tokens(string: str, encoding_name: str) -> int:
        """Returns the number of tokens in a text string."""
        encoding = tiktoken.get_encoding(encoding_name)
        return len(encoding.encode(string))

    @classmethod
    def extract_operational_data(cls, dref_data: Dict[str, Any]) -> str:
        """Extract operational objective and strategy data from DREF - focused on key fields"""
        # Focus on the two main fields mentioned by user
        key_fields = [
            'operation_objective',  # Overall objective of the operation
            'response_strategy',    # Operation strategy rationale
        ]
        
        # Additional context fields for better understanding
        context_fields = [
            'title',
            'total_targeted_population',
            'people_in_need',
            'amount_requested',
            'operation_timeframe',
            'country_details',
            'disaster_type_details',
            'event_date',
            'end_date'
        ]
        
        extracted_data = {}
        
        # Extract key operational data
        for field in key_fields + context_fields:
            if field in dref_data and dref_data[field] is not None:
                extracted_data[field] = dref_data[field]
        
        # Convert to formatted string for AI processing
        return json.dumps(extracted_data, indent=2, ensure_ascii=False)

    @classmethod
    def extract_budget_data(cls, dref_data: Dict[str, Any]) -> str:
        """Extract comprehensive budget and financial data from DREF"""
        budget_fields = [
            'amount_requested',
            'total_dref_allocation',
            'budget_file_details',
            'budget_file_preview',
            'planned_interventions',
            'total_targeted_population',
            'people_in_need',
            'operation_timeframe',
            'end_date',
            'publishing_date',
            'title',
            'country_details',
            'disaster_type_details',
            'human_resource',
            'logistic_capacity_of_ns',
            'coordination_and_partnerships',
            'pmer'
        ]
        
        extracted_data = {}
        
        # Extract basic budget data
        for field in budget_fields:
            if field in dref_data and dref_data[field] is not None:
                extracted_data[field] = dref_data[field]
        
        # Process planned interventions for comprehensive budget breakdown
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
        
        # Convert to formatted string for AI processing
        return json.dumps(extracted_data, indent=2, ensure_ascii=False)

    @classmethod
    def generate_summary(cls, prompt: str, data: str, summary_type: str) -> Optional[Any]:
        """Generate summary using Azure OpenAI"""
        logger.info(f"Generating DREF {summary_type} summary")
        
        def _validate_prompt_length(messages: List[Dict[str, str]]) -> bool:
            """Validate the length of the prompt"""
            message_content = [msg["content"] for msg in messages]
            text = " ".join(message_content)
            token_count = cls.count_tokens(text, cls.ENCODING_NAME)
            logger.info(f"DREF {summary_type} token count: {token_count}")
            return token_count <= cls.PROMPT_LENGTH_LIMIT

        def _create_messages(prompt: str, data: str) -> List[Dict[str, str]]:
            """Create message structure for OpenAI API"""
            return [
                {"role": "system", "content": cls.system_message},
                {"role": "user", "content": f"DREF Data to analyze:\n{data}\n\n{prompt}"},
                {
                    "role": "assistant",
                    "content": "I understand. I will analyze the DREF data and provide a structured summary according to your specifications."
                }
            ]

        def _process_operational_response(response: str) -> str:
            """Process operational response (plain text, 3 lines max)"""
            lines = response.strip().split('\n')
            # Take only first 3 non-empty lines
            filtered_lines = [line.strip() for line in lines if line.strip()][:3]
            return '\n'.join(filtered_lines)

        def _process_budget_response(response: str) -> Optional[Dict[str, Any]]:
            """Process budget response (JSON format)"""
            try:
                # Clean up the response if needed
                cleaned_response = response.strip()
                if cleaned_response.startswith("```json"):
                    cleaned_response = cleaned_response.replace("```json", "").replace("```", "").strip()
                
                # Parse JSON
                parsed_response = json.loads(cleaned_response)
                
                if isinstance(parsed_response, dict):
                    return parsed_response
                else:
                    logger.warning(f"Invalid response format for {summary_type}: not a dictionary")
                    return None
                    
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response for {summary_type}: {e}")
                try:
                    # Try to extract JSON from the response
                    json_match = re.search(r'\{.*\}', response, re.DOTALL)
                    if json_match:
                        return json.loads(json_match.group())
                except:
                    pass
                return None
            except Exception as e:
                logger.error(f"Error processing response for {summary_type}: {e}")
                return None

        # Create messages
        messages = _create_messages(prompt, data)
        
        # Validate prompt length
        if not _validate_prompt_length(messages):
            logger.warning(f"Prompt too long for {summary_type}, truncating data")
            # Truncate data if too long
            truncated_data = data[:cls.PROMPT_DATA_LENGTH_LIMIT]
            messages = _create_messages(prompt, truncated_data)
        
        # Generate response
        try:
            client = AzureOpenAiChat()
            response = client.get_response(messages)
            
            if not response:
                logger.error(f"No response received for {summary_type}")
                return None
            
            # Process response based on summary type
            if summary_type == "operational":
                processed_response = _process_operational_response(response)
                logger.info(f"Successfully generated {summary_type} summary")
                return processed_response
            else:  # budget
                processed_response = _process_budget_response(response)
                if processed_response:
                    logger.info(f"Successfully generated {summary_type} summary")
                    return processed_response
                else:
                    logger.error(f"Failed to generate valid {summary_type} summary")
                    return None
                
        except Exception as e:
            logger.error(f"Error generating {summary_type} summary: {e}", exc_info=True)
            return None

    @classmethod
    def generate_operational_summary(cls, dref_data: Dict[str, Any]) -> Optional[str]:
        """Generate operational objective and strategy summary (3 lines max)"""
        operational_data = cls.extract_operational_data(dref_data)
        return cls.generate_summary(
            cls.operational_summary_prompt, 
            operational_data, 
            "operational"
        )

    @classmethod
    def generate_budget_summary(cls, dref_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Generate comprehensive budget and financial summary"""
        budget_data = cls.extract_budget_data(dref_data)
        return cls.generate_summary(
            cls.budget_summary_prompt, 
            budget_data, 
            "budget"
        )

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
            # Generate operational summary (3 lines max)
            operational_summary = cls.generate_operational_summary(dref_data)
            if operational_summary:
                result["operational_summary"] = operational_summary
                logger.info("Operational summary generated successfully")
            else:
                result["errors"].append("Failed to generate operational summary")
                logger.error("Failed to generate operational summary")
            
            # Generate comprehensive budget summary
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