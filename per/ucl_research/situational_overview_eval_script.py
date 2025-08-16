# situational_overview_eval.py

import json
import asyncio
import os
import re
import pandas as pd

# Initialize Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")
import django
django.setup()

# --- Your application's imports ---
from per.ucl_research.ops_learning_summary4 import DrefSummaryTask, BaseAITask
from per.dref_temp.dref_utils import dref_manager, DREFFilters
from per.ucl_research.ifrc_client import IFRCAPIClient

# --- Data Preparation Function ---
async def get_situational_overview_data(event_id: int):
    """
    Prepares the document and summary for evaluating the situational overview task.
    This function mimics the exact logic used in DrefSituationalOverviewView._process_situational_overview
    """
    print(f"Fetching data for Situational Overview evaluation for event_id: {event_id}...")
    client = IFRCAPIClient()
    task = DrefSummaryTask()

    try:
        # Step 1: Get event details (same as API)
        event = await client.get_event_detail(event_id)
        if not event:
            print(f"❌ Event not found for ID: {event_id}")
            await client.close()
            return None, None
        
        # Step 2: Get field reports (same as API)
        field_reports = event.get("field_reports", [])
        if not field_reports:
            print(f"❌ Field Reports not found for event: {event.get('name', 'Unknown')}")
            await client.close()
            return None, None
        
        # Step 3: Extract field report IDs and find linked DREF (same as API)
        field_report_ids = [fr['id'] for fr in field_reports]
        dref_data_list = dref_manager.get_data("basic", DREFFilters(field_report_ids=field_report_ids))
        
        if not dref_data_list:
            print(f"❌ No DREF found for event: {event.get('name', 'Unknown')}")
            print(f"   Field reports count: {len(field_reports)}")
            print(f"   Field report IDs: {field_report_ids}")
            await client.close()
            return None, None
        
        # Step 4: Get latest DREF version (same as API)
        latest_dref_version = dref_manager.get_latest_dref_version(dref_data_list[0])
        
        # Step 5: Prepare data for LLM generation (exact same as API)
        latest_update_dict = {
            'event_description': (getattr(latest_dref_version, 'event_description', '') or getattr(latest_dref_version, 'description', '') or getattr(latest_dref_version, 'summary', '')),
            'event_scope': (getattr(latest_dref_version, 'event_scope', '') or getattr(latest_dref_version, 'scope_and_scale', '')),
            'operation_objective': getattr(latest_dref_version, 'operation_objective', ''),
            'response_strategy': getattr(latest_dref_version, 'response_strategy', ''),
            'title': getattr(latest_dref_version, 'title', ''),
            'operational_update_number': getattr(latest_dref_version, 'operational_update_number', 0),
            'country_details': {
                'name': latest_dref_version.country_details.name if latest_dref_version.country_details else None,
                'iso': latest_dref_version.country_details.iso if latest_dref_version.country_details else None
            },
            'disaster_type_details': {
                'name': latest_dref_version.disaster_type_details.name if latest_dref_version.disaster_type_details else None
            },
            'date_of_approval': getattr(latest_dref_version, 'date_of_approval', None),
        }

        # Step 6: Generate situational overview (same as API)
        summary = task.generate_situational_overview(latest_update_dict)
        
        if not summary:
            print(f"❌ Failed to generate situational overview for DREF: {latest_dref_version.id}")
            await client.close()
            return None, None
        
        # Step 7: Prepare document for evaluation (same structure as API input)
        document = json.dumps(latest_update_dict, indent=2, ensure_ascii=False, default=str)
        
        print(f"✅ Successfully generated situational overview for event: {event.get('name', 'Unknown')}")
        print(f"   DREF ID: {latest_dref_version.id}")
        print(f"   Country: {latest_update_dict['country_details']['name']}")
        print(f"   Disaster Type: {latest_update_dict['disaster_type_details']['name']}")
        
        await client.close()
        return document, summary
        
    except Exception as e:
        print(f"❌ Error in get_situational_overview_data: {e}")
        await client.close()
        return None, None

# --- Adapted G-Eval Prompts for Situational Overview ---
RELEVANCY_SCORE_CRITERIA_SITUATIONAL = """
Relevance (1-5): The summary must accurately describe the disaster situation and the reason for the operation, based on the source JSON.
- A score of 5 means the summary clearly includes the 'event_description', 'event_scope', and 'operation_objective'.
- A score of 3 means one of these key details is missing or misrepresented.
- A score of 1 means the summary fails to describe the situation or objective from the source.
"""
RELEVANCY_SCORE_STEPS_SITUATIONAL = """
1. Read the summary and the source JSON document carefully.
2. Identify the 'event_description', 'event_scope', and 'operation_objective' in the source JSON.
3. Check if the summary accurately reflects these key pieces of information.
4. Assign a relevance score from 1 to 5 based on how well these details are covered.
"""
COHERENCE_SCORE_CRITERIA_SITUATIONAL = """
Coherence (1-5): The summary must be a well-structured paragraph.
- A score of 5 means the summary flows logically, typically starting with the event description/scope, then moving to the operational objective/strategy.
- A score of 3 means the sentences are present but arranged in a slightly confusing or unnatural order.
- A score of 1 means the sentences are jumbled and do not form a clear narrative.
"""
COHERENCE_SCORE_STEPS_SITUATIONAL = "1. Read the summary paragraph. 2. Assess if the sentences are ordered logically, telling a clear story from the disaster situation to the planned response. 3. Assign a score based on the clarity and flow."

CONSISTENCY_SCORE_CRITERIA_SITUATIONAL = """
Consistency (1-5): The summary must be factually aligned with the source JSON document.
- A score of 5 means all facts and details mentioned are identical to the source document.
- A score of 3 means there is a minor factual discrepancy.
- A score of 1 means the summary contains significant factual errors or details not found in the source.
"""
CONSISTENCY_SCORE_STEPS_SITUATIONAL = "1. Read the summary and the source JSON side-by-side. 2. Compare every fact and detail in the summary with the corresponding values in the JSON document. 3. Assign a score based on factual accuracy."

FLUENCY_SCORE_CRITERIA_SITUATIONAL = """
Fluency (1-5): The quality of the summary in terms of grammar, spelling, and readability.
- 5: Good. The summary has few or no grammatical errors and is easy to read.
- 3: Fair. The summary has some errors that affect clarity but is still understandable.
- 1: Poor. The summary has many errors that make it hard to understand.
"""
FLUENCY_SCORE_STEPS_SITUATIONAL = "Read the summary and evaluate its fluency based on the given criteria. Assign a fluency score from 1 to 5."

# --- G-Eval Functions ---
EVALUATION_PROMPT_TEMPLATE = (
    "You will be given one summary written for an article. Your task is to rate the summary on the metric: {metric_name}.\n\n"
    "Criteria:\n{criteria}\n\nSteps:\n{steps}\n\n"
    "STRICT OUTPUT REQUIREMENT:\n"
    "- Return ONLY a single integer on its own line.\n"
    "- Do NOT include any extra words, symbols, or explanation.\n\n"
    "Source Document:\n{document}\n\n"
    "Summary to evaluate:\n{summary}\n"
)

def get_geval_score(task_instance: BaseAITask, criteria: str, steps: str, document: str, summary: str, metric_name: str):
    """Build prompt, call model, and parse a numeric score; return int or None."""
    prompt = EVALUATION_PROMPT_TEMPLATE.format(
        criteria=criteria,
        steps=steps,
        metric_name=metric_name,
        document=document,
        summary=summary,
    )
    
    response = task_instance.get_azure_response(
        messages=[{"role": "user", "content": prompt}],
        cache_prefix=f"geval_{metric_name}"
    )
    
    if not response:
        return None
    
    # Safely find the first number in the response string
    match = re.search(r"\d+", response)
    if not match:
        return None
        
    try:
        score = int(match.group(0))
        return score
    except Exception as e:
        return None

# --- Main Execution Block ---
async def main():
    TEST_EVENT_ID = 6955 # Example Event ID

    document, summary = await get_situational_overview_data(TEST_EVENT_ID)

    if document and summary:
        print("\n--- DOCUMENT (Input to the model) ---")
        print(document)
        print("\n--- SUMMARY (Output from the model) ---")
        print(summary)

        evaluation_metrics = {
            "Relevance": (RELEVANCY_SCORE_CRITERIA_SITUATIONAL, RELEVANCY_SCORE_STEPS_SITUATIONAL),
            "Coherence": (COHERENCE_SCORE_CRITERIA_SITUATIONAL, COHERENCE_SCORE_STEPS_SITUATIONAL),
            "Consistency": (CONSISTENCY_SCORE_CRITERIA_SITUATIONAL, CONSISTENCY_SCORE_STEPS_SITUATIONAL),
            "Fluency": (FLUENCY_SCORE_CRITERIA_SITUATIONAL, FLUENCY_SCORE_STEPS_SITUATIONAL),
        }
        data = {"Evaluation Metric": [], "Score": []}
        task_instance = BaseAITask()

        for eval_type, (criteria, steps) in evaluation_metrics.items():
            score = get_geval_score(task_instance, criteria, steps, document, summary, eval_type)
            data["Evaluation Metric"].append(eval_type)
            data["Score"].append(score if score is not None else 0)

        df = pd.DataFrame(data).set_index("Evaluation Metric")
        print("\n--- Evaluation Results ---")
        print(df)
        
    else:
        print("❌ Failed to get document or summary data")
        print(f"Document: {document}")
        print(f"Summary: {summary}")

if __name__ == "__main__":
    asyncio.run(main())