import json
import asyncio
import os
import re
import pandas as pd
from typing import List, Dict, Tuple, Optional

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")
import django
django.setup()

from per.ucl_research.ops_learning_summary4 import DrefSummaryTask, BaseAITask
from per.dref_temp.dref_utils import dref_manager, DREFFilters
from per.ucl_research.ifrc_client import IFRCAPIClient

# Configuration: Add your event IDs here
EVENT_IDS_TO_EVALUATE = [
    6952,  # Example ID - replace with your actual IDs
    # Add more event IDs here as needed
    # 6953,
    # 6954,
    # 6955,
]

def _get_title(item):
    if hasattr(item, 'title'):
        return getattr(item, 'title', '')
    elif isinstance(item, dict):
        return item.get('title', '')
    else:
        return str(item) if item else ''

async def get_sector_summary_data(event_id: int):
    print(f"Fetching data for Sector Summary evaluation for event_id: {event_id}...")
    client = IFRCAPIClient()
    task = DrefSummaryTask()

    try:
        event = await client.get_event_detail(event_id)
        if not event:
            print(f"Event not found for ID: {event_id}")
            return []
        
        if not event.get("field_reports"):
            print(f"Field Reports not found for event: {event.get('name', 'Unknown')}")
            return []
        
        field_report_ids = [fr['id'] for fr in event["field_reports"]]
        dref_data_list = dref_manager.get_data("basic", DREFFilters(field_report_ids=field_report_ids))
        
        if not dref_data_list:
            print(f"No DREF found for event: {event.get('name', 'Unknown')}")
            print(f"   Field reports count: {len(field_report_ids)}")
            print(f"   Field report IDs: {field_report_ids}")
            return []
            
        latest_dref = dref_manager.get_latest_dref_version(dref_data_list[0])
        
        dref_dict = {
            'id': latest_dref.id,
            'title': latest_dref.title,
            'operation_objective': getattr(latest_dref, 'operation_objective', None),
            'response_strategy': getattr(latest_dref, 'response_strategy', None),
            'amount_requested': latest_dref.amount_requested,
            'total_targeted_population': latest_dref.total_targeted_population,
            'operation_timeframe': getattr(latest_dref, 'operation_timeframe', None),
            'country_details': {
                'name': latest_dref.country_details.name if latest_dref.country_details else None,
                'iso': latest_dref.country_details.iso if latest_dref.country_details else None
            },
            'disaster_type_details': {
                'name': latest_dref.disaster_type_details.name if latest_dref.disaster_type_details else None
            },
            'event_date': latest_dref.event_date,
            'end_date': getattr(latest_dref, 'end_date', None),
            'planned_interventions': getattr(latest_dref, 'planned_interventions', []),
            'national_society_actions': getattr(latest_dref, 'national_society_actions',[]),
            'needs_identified': getattr(latest_dref, 'needs_identified', []),
            'people_in_need': getattr(latest_dref, 'people_in_need', None),
            'human_resource': getattr(latest_dref, 'human_resource', None),
            'logistic_capacity_of_ns': getattr(latest_dref, 'logistic_capacity_of_ns', None),
            'pmer': getattr(latest_dref, 'pmer', None)
        }
        
        print(f"Successfully prepared DREF data for event: {event.get('name', 'Unknown')}")
        print(f"   DREF ID: {latest_dref.id}")
        print(f"   Country: {dref_dict['country_details']['name']}")
        print(f"   Disaster Type: {dref_dict['disaster_type_details']['name']}")
        print(f"   Planned Interventions: {len(dref_dict['planned_interventions'])}")
        print(f"   Needs Identified: {len(dref_dict['needs_identified'])}")

        print("Generating sector summaries...")
        all_summaries_output = task.generate_dref_summaries(dref_dict)
        
        if not all_summaries_output or all_summaries_output.get("status") == "failed":
            print(f"Failed to generate DREF summaries: {all_summaries_output.get('errors', [])}")
            return []
        
        generated_sectors = {s['title']: s for s in all_summaries_output.get("sectors", [])}
        print(f"Generated {len(generated_sectors)} sector summaries")

        eval_list = []
        for sector_title, generated_summary in generated_sectors.items():
            sector_specific_data = {
                'sector_title': sector_title,
                'needs_identified': [n for n in dref_dict['needs_identified'] if _get_title(n) == sector_title],
                'planned_interventions': [p for p in dref_dict['planned_interventions'] if _get_title(p) == sector_title],
                'context': {
                    'country': dref_dict['country_details']['name'],
                    'disaster_type': dref_dict['disaster_type_details']['name'],
                    'event_date': dref_dict['event_date'],
                    'dref_title': dref_dict['title']
                }
            }
            
            document = json.dumps(sector_specific_data, indent=2, default=str)

            summary = (
                f"Needs Summary: {generated_summary.get('needs_summary', '')}\n\n"
                "Future Actions:\n" + 
                "\n".join([f"- {action.get('intervention_summary', '')}" for action in generated_summary.get('future_actions', [])])
            )
            
            eval_list.append({
                "document": document, 
                "summary": summary, 
                "sector": sector_title,
                "needs_count": len(sector_specific_data['needs_identified']),
                "interventions_count": len(sector_specific_data['planned_interventions'])
            })
        
        print(f"Prepared {len(eval_list)} sectors for evaluation")
        for item in eval_list:
            print(f"   - {item['sector']}: {item['needs_count']} needs, {item['interventions_count']} interventions")
        
        return eval_list
        
    except Exception as e:
        print(f"Error in get_sector_summary_data: {e}")
        return []
    finally:
        await client.close()

RELEVANCY_SCORE_CRITERIA_SECTOR = """
Relevance (1-5): The summary must accurately summarize the 'needs' and 'planned_interventions' for its specific sector from the source JSON.
- A score of 5 means the summary clearly and correctly reflects both the needs and the planned actions.
- A score of 3 means it covers one area well but misses or misrepresents the other.
- A score of 1 means it fails to address the core needs and actions for the sector.
"""
RELEVANCY_SCORE_STEPS_SECTOR = """
1. Read the summary and the source JSON document for the specific sector.
2. Check if the 'Needs Summary' accurately reflects the 'needs' data in the JSON.
3. Check if the 'Future Actions' summaries accurately reflect the 'planned_interventions' data in the JSON.
4. Assign a score based on how well both parts are covered.
"""
COHERENCE_SCORE_CRITERIA = """
Coherence (1-5): The sector summary must be well-structured and present information in a logical order.
- A score of 5 means the needs summary and future actions are clearly organized and flow logically from problem to solution.
- A score of 3 means the information is present but the organization could be improved.
- A score of 1 means the summary is poorly structured and difficult to follow.
"""
COHERENCE_SCORE_STEPS = "1. Read the sector summary. 2. Assess if the needs summary and future actions are presented in a logical order. 3. Check if the flow from identified needs to planned interventions makes sense. 4. Assign a score based on clarity and organization."

CONSISTENCY_SCORE_CRITERIA = """
Consistency (1-5): The summary must be factually aligned with the source JSON document.
- A score of 5 means all facts and details mentioned are identical to the source document.
- A score of 3 means there is a minor factual discrepancy.
- A score of 1 means the summary contains significant factual errors or details not found in the source.
"""
CONSISTENCY_SCORE_STEPS = "1. Read the summary and the source JSON side-by-side. 2. Compare every fact and detail in the summary with the corresponding values in the JSON document. 3. Assign a score based on factual accuracy."

FLUENCY_SCORE_CRITERIA = """
Fluency (1-5): The quality of the summary in terms of grammar, spelling, and readability.
- 5: Good. The summary has few or no grammatical errors and is easy to read.
- 3: Fair. The summary has some errors that affect clarity but is still understandable.
- 1: Poor. The summary has many errors that make it hard to understand.
"""
FLUENCY_SCORE_STEPS = "Read the summary and evaluate its fluency based on the given criteria. Assign a fluency score from 1 to 5."

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
        score = int(match.group(0))
        return score
    except Exception as e:
        return None

async def evaluate_single_event(event_id: int) -> Optional[List[Dict]]:
    """Evaluate a single event and return the results for all sectors"""
    print(f"\n--- Processing Event ID: {event_id} ---")
    
    evaluation_list = await get_sector_summary_data(event_id)
    if not evaluation_list:
        print(f"Skipping event {event_id} - no sectors available for evaluation")
        return None
    
    print(f"Evaluating {len(evaluation_list)} sectors for event {event_id}")
    
    all_scores = []
    task_instance = BaseAITask()

    for item in evaluation_list:
        document = item["document"]
        summary = item["summary"]
        sector = item["sector"]
        print(f"\n  --- Evaluating Sector: {sector} ---")

        evaluation_metrics = {
            "Relevance": (RELEVANCY_SCORE_CRITERIA_SECTOR, RELEVANCY_SCORE_STEPS_SECTOR),
            "Coherence": (COHERENCE_SCORE_CRITERIA, COHERENCE_SCORE_STEPS), 
            "Consistency": (CONSISTENCY_SCORE_CRITERIA, CONSISTENCY_SCORE_STEPS), 
            "Fluency": (FLUENCY_SCORE_CRITERIA, FLUENCY_SCORE_STEPS), 
        }

        sector_scores = {}
        for eval_type, (criteria, steps) in evaluation_metrics.items():
            score = get_geval_score(task_instance, criteria, steps, document, summary, eval_type)
            score_num = score if isinstance(score, int) else 0
            sector_scores[eval_type] = score_num
            print(f"    {eval_type}: {score_num}/5")
        
        all_scores.append({
            "event_id": event_id,
            "sector": sector,
            "scores": sector_scores,
            "needs_count": item["needs_count"],
            "interventions_count": item["interventions_count"],
            "summary": summary
        })
    
    return all_scores

async def evaluate_multiple_events(event_ids: List[int]) -> List[Dict]:
    """Evaluate multiple events and return all results"""
    print(f"Starting evaluation of {len(event_ids)} events...")
    
    all_results = []
    for i, event_id in enumerate(event_ids, 1):
        print(f"\nProgress: {i}/{len(event_ids)}")
        results = await evaluate_single_event(event_id)
        if results:
            all_results.extend(results)
    
    return all_results

def create_summary_dataframe(results: List[Dict]) -> pd.DataFrame:
    """Create a summary DataFrame from all evaluation results"""
    if not results:
        return pd.DataFrame()
    
    # Create detailed results DataFrame
    detailed_data = []
    for result in results:
        row = {
            "Event ID": result["event_id"],
            "Sector": result["sector"],
            "Needs Count": result["needs_count"],
            "Interventions Count": result["interventions_count"]
        }
        row.update(result["scores"])
        detailed_data.append(row)
    
    detailed_df = pd.DataFrame(detailed_data)
    
    # Create summary statistics DataFrame
    if len(results) > 1:
        # Group by sector and calculate averages
        sector_stats = detailed_df.groupby('Sector')[['Relevance', 'Coherence', 'Consistency', 'Fluency']].mean().round(2)
        
        # Overall averages across all sectors
        overall_stats = detailed_df[['Relevance', 'Coherence', 'Consistency', 'Fluency']].mean().round(2)
        
        print("\n=== SECTOR AVERAGES ===")
        print(sector_stats)
        
        print("\n=== OVERALL AVERAGES ===")
        print(overall_stats)
    
    return detailed_df

async def main():
    """Main function to run the evaluation"""
    if not EVENT_IDS_TO_EVALUATE:
        print("No event IDs specified in EVENT_IDS_TO_EVALUATE list!")
        print("Please add event IDs to the EVENT_IDS_TO_EVALUATE list at the top of the script.")
        return
    
    print(f"Evaluating {len(EVENT_IDS_TO_EVALUATE)} events for Sector Summary...")
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
    filename = f"sector_summary_evaluation_results_{timestamp}.csv"
    detailed_df.to_csv(filename, index=False)
    print(f"\nResults saved to: {filename}")
    
    # Save full results (including summaries) to JSON
    json_filename = f"sector_summary_evaluation_full_{timestamp}.json"
    with open(json_filename, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Full results saved to: {json_filename}")

if __name__ == "__main__":
    # Run the evaluation
    asyncio.run(main())