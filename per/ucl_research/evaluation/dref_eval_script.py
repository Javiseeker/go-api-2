import json
import asyncio
import os
import re
import pandas as pd
from typing import List, Dict, Tuple, Optional

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")
try:
    import django
    django.setup()
except Exception:
    pass

from per.ucl_research.ops_learning_summary4 import DrefSummaryTask, BaseAITask
from per.dref_temp.dref_utils import dref_manager, DREFFilters
from per.ucl_research.ifrc_client import IFRCAPIClient

# Configuration: Add your event IDs here
EVENT_IDS_TO_EVALUATE = [
    6950,  # Example ID - replace with your actual IDs
    # Add more event IDs here as needed
    # 6951,
    # 6952,
    # 6953,
]

async def get_evaluation_data(event_id: int):
    client = IFRCAPIClient()
    try:
        event = await client.get_event_detail(event_id)
        if not event or not event.get("field_reports"):
            print(f"No event or field reports found for event_id: {event_id}")
            return None, None
        
        field_report_ids = [fr['id'] for fr in event["field_reports"]]
        filters = DREFFilters(field_report_ids=field_report_ids)
        dref_data_list = dref_manager.get_data("basic", filters)
        
        if not dref_data_list:
            print(f"No DREF data found for event_id: {event_id}")
            return None, None
        
        dref_data = dref_manager.get_latest_dref_version(dref_data_list[0])
        dref_dict = {
            'id': dref_data.id,
            'title': dref_data.title,
            'operation_objective': getattr(dref_data, 'operation_objective', None),
            'response_strategy': getattr(dref_data, 'response_strategy', None),
            'amount_requested': dref_data.amount_requested,
            'total_targeted_population': dref_data.total_targeted_population,
            'operation_timeframe': getattr(dref_data, 'operation_timeframe', None),
            'country_details': {
                'name': dref_data.country_details.name if dref_data.country_details else None,
                'iso': dref_data.country_details.iso if dref_data.country_details else None
            },
            'disaster_type_details': {
                'name': dref_data.disaster_type_details.name if dref_data.disaster_type_details else None
            },
            'event_date': dref_data.event_date,
            'end_date': getattr(dref_data, 'end_date', None),
        }
        
        task = DrefSummaryTask()
        summary = task.generate_operational_summary(dref_dict)
        document = json.dumps(dref_dict, indent=2)
        
        return document, summary
    except Exception as e:
        print(f"Error processing event_id {event_id}: {str(e)}")
        return None, None
    finally:
        await client.close()

RELEVANCY_SCORE_CRITERIA_OPERATIONAL = """
Relevance (1-5): The summary must accurately capture the key operational details from the source JSON document.
- A score of 5 means all key details (objective, target population, amount requested, and timeframe) are present and correct.
- A score of 3 means some key details are present, but one or two are missing or inaccurate.
- A score of 1 means the summary fails to mention the core operational details from the source.
- Penalize summaries that include information NOT present in the source JSON.
"""

RELEVANCY_SCORE_STEPS_OPERATIONAL = """
1. Read the summary and the source JSON document carefully.
2. Identify the 'operation_objective', 'total_targeted_population', 'amount_requested', and 'operation_timeframe' in the source JSON.
3. Check if the summary accurately reflects these key pieces of information.
4. Assign a relevance score from 1 to 5 based on how well the key details are covered.
"""

COHERENCE_SCORE_CRITERIA_OPERATIONAL = """
Coherence (1-5): The summary must be well-structured and present information in a logical order.
- A score of 5 means the summary flows logically, typically starting with the overall objective, then the strategy, and finally the scope (e.g., budget, population, timeframe).
- A score of 3 means the key information is present but arranged in a slightly confusing or unnatural order.
- A score of 1 means the sentences are jumbled and do not form a clear, understandable narrative.
"""

COHERENCE_SCORE_STEPS_OPERATIONAL = """
1. Read the summary carefully.
2. Identify the sentences that describe the objective, the strategy, and the operational scope (budget, population, timeframe).
3. Assess if these sentences are arranged in a logical sequence that is easy to follow.
4. Assign a coherence score from 1 to 5 based on the clarity and logical structure of the summary.
"""

CONSISTENCY_SCORE_CRITERIA_OPERATIONAL = """
Consistency (1-5): The summary must be factually aligned with the source JSON document.
- A score of 5 means all facts, especially numbers (population, budget, timeframe), are identical to the source document.
- A score of 3 means there is a minor factual discrepancy (e.g., a number is slightly off, a detail is misrepresented).
- A score of 1 means the summary contains significant factual errors or "hallucinated" details not found in the source.
"""

CONSISTENCY_SCORE_STEPS_OPERATIONAL = """
1. Read the summary and the source JSON document side-by-side.
2. Compare every fact and number mentioned in the summary with the corresponding values in the JSON document.
3. Specifically check 'total_targeted_population', 'amount_requested', and 'operation_timeframe'.
4. Assign a consistency score from 1 to 5 based on the factual accuracy of the summary.
"""

FLUENCY_SCORE_CRITERIA_OPERATIONAL = """
Fluency (1-5): The quality of the summary in terms of grammar, spelling, and readability.
- 5: Good. The summary has few or no grammatical errors and is easy to read. The language is professional and appropriate for the intended audience.
- 3: Fair. The summary has some errors that affect clarity but is still understandable.
- 1: Poor. The summary has many errors that make it hard to understand.
"""

FLUENCY_SCORE_STEPS_OPERATIONAL = """
Read the summary and evaluate its fluency based on the given criteria. Assign a fluency score from 1 to 5.
"""

EVALUATION_PROMPT_TEMPLATE = (
    "You will be given one summary written for an article. Your task is to rate the summary on the metric: {metric_name}.\n\n"
    "Criteria:\n{criteria}\n\nSteps:\n{steps}\n\n"
    "STRICT OUTPUT REQUIREMENT:\n"
    "- Return ONLY a single integer on its own line.\n"
    "- Do NOT include any extra words, symbols, or explanation.\n\n"
    "Source JSON document:\n{document}\n\n"
    "Summary to evaluate:\n{summary}\n"
)

def get_geval_score(task_instance: BaseAITask, criteria: str, steps: str, document: str, summary: str, metric_name: str):
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
    match = re.search(r"\d+", response)
    if not match:
        return None
    try:
        return int(match.group(0))
    except Exception:
        return None

async def evaluate_single_event(event_id: int) -> Optional[Dict]:
    """Evaluate a single event and return the results"""
    print(f"\n--- Processing Event ID: {event_id} ---")
    
    document, summary = await get_evaluation_data(event_id)
    if not document or not summary:
        print(f"Skipping event {event_id} - no data available")
        return None
    
    print(f"Generated summary for event {event_id}")
    
    evaluation_metrics = {
        "Relevance": (RELEVANCY_SCORE_CRITERIA_OPERATIONAL, RELEVANCY_SCORE_STEPS_OPERATIONAL),
        "Coherence": (COHERENCE_SCORE_CRITERIA_OPERATIONAL, COHERENCE_SCORE_STEPS_OPERATIONAL),
        "Consistency": (CONSISTENCY_SCORE_CRITERIA_OPERATIONAL, CONSISTENCY_SCORE_STEPS_OPERATIONAL),
        "Fluency": (FLUENCY_SCORE_CRITERIA_OPERATIONAL, FLUENCY_SCORE_STEPS_OPERATIONAL),
    }
    
    results = {
        "event_id": event_id,
        "summary": summary,
        "scores": {}
    }
    
    for eval_type, (criteria, steps) in evaluation_metrics.items():
        score_value = get_geval_score(EVALUATION_TASK, criteria, steps, document, summary, eval_type)
        score_num = score_value if isinstance(score_value, int) else 0
        results["scores"][eval_type] = score_num
        print(f"  {eval_type}: {score_num}/5")
    
    return results

async def evaluate_multiple_events(event_ids: List[int]) -> List[Dict]:
    """Evaluate multiple events and return all results"""
    print(f"Starting evaluation of {len(event_ids)} events...")
    
    results = []
    for i, event_id in enumerate(event_ids, 1):
        print(f"\nProgress: {i}/{len(event_ids)}")
        result = await evaluate_single_event(event_id)
        if result:
            results.append(result)
    
    return results

def create_summary_dataframe(results: List[Dict]) -> pd.DataFrame:
    """Create a summary DataFrame from all evaluation results"""
    if not results:
        return pd.DataFrame()
    
    # Create detailed results DataFrame
    detailed_data = []
    for result in results:
        row = {"Event ID": result["event_id"]}
        row.update(result["scores"])
        detailed_data.append(row)
    
    detailed_df = pd.DataFrame(detailed_data)
    
    # Create summary statistics DataFrame
    if len(results) > 1:
        summary_stats = detailed_df.describe()
        summary_stats = summary_stats.drop("Event ID", axis=1, errors='ignore')
        summary_stats = summary_stats.round(2)
        
        print("\n=== SUMMARY STATISTICS ===")
        print(summary_stats)
    
    return detailed_df

async def main():
    """Main function to run the evaluation"""
    if not EVENT_IDS_TO_EVALUATE:
        print("No event IDs specified in EVENT_IDS_TO_EVALUATE list!")
        print("Please add event IDs to the EVENT_IDS_TO_EVALUATE list at the top of the script.")
        return
    
    print(f"Evaluating {len(EVENT_IDS_TO_EVALUATE)} events...")
    print(f"Event IDs: {EVENT_IDS_TO_EVALUATE}")
    
    # Evaluate all events
    results = await evaluate_multiple_events(EVENT_IDS_TO_EVALUATE)
    
    if not results:
        print("No events were successfully evaluated!")
        return
    
    # Create and display results
    detailed_df = create_summary_dataframe(results)
    
    print("\n=== DETAILED RESULTS ===")
    print(detailed_df)
    
    # Save results to file
    timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(results_dir, exist_ok=True)
    
    filename = os.path.join(results_dir, f"dref_evaluation_results_{timestamp}.csv")
    detailed_df.to_csv(filename, index=False)
    print(f"\nResults saved to: {os.path.abspath(filename)}")
    
    # Save full results (including summaries) to JSON
    json_filename = os.path.join(results_dir, f"dref_evaluation_full_{timestamp}.json")
    with open(json_filename, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Full results saved to: {os.path.abspath(json_filename)}")

if __name__ == "__main__":
    EVALUATION_TASK = BaseAITask()
    
    # Run the evaluation
    asyncio.run(main())