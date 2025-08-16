# previous_crises_eval.py

import json
import asyncio
import os
import re
import pandas as pd
from typing import List, Dict, Tuple, Optional

# Initialize Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")
import django
django.setup()

from per.ucl_research.ops_learning_summary4 import PreviousCrisesTask, BaseAITask
from per.ucl_research.ifrc_client import IFRCAPIClient
from per.ucl_research.rapid_response_parser import RapidResponseCapacityParser

# Configuration: Add your country/disaster type combinations here
# Each tuple contains (country_id, disaster_type_id)
COUNTRY_DISASTER_COMBINATIONS = [
    (87, 62),  # Example: Country 87, Disaster Type 62 - replace with your actual combinations
    # Add more combinations here as needed
    # (88, 63),
    # (89, 64),
]

# --- Data Preparation Function ---
async def get_previous_crises_data(country_id: int, disaster_type_id: int):
    """
    Matches the API view's processing of previous crises insights,
    but adapted for local evaluation with safe handling of event detail errors.
    """
    print("Fetching data for Previous Crises evaluation...")
    previous_crises_task = PreviousCrisesTask()

    # Try loading RR questions template (skip if unavailable)
    try:
        rr_template = RapidResponseCapacityParser._load_questions_data()
    except Exception as e:
        rr_template = []
        print(f"⚠ Could not load RR questions template: {e}")

    async with IFRCAPIClient() as client:
        # STEP 1: Get primary and secondary learnings
        primary = await client.get_ops_learning(country_id, disaster_type_id, max_results=20)
        if not primary:
            secondary = await client.get_ops_learning(country_id, None, max_results=20)
        else:
            all_country = await client.get_ops_learning(country_id, None, max_results=20)
            primary_ids = {p['id'] for p in primary}
            secondary = [l for l in all_country if l['id'] not in primary_ids]

        merged = primary + secondary
        seen_ids = set()
        deduped = []
        for l in merged:
            lid = l.get('id')
            if lid in seen_ids:
                continue
            seen_ids.add(lid)
            deduped.append(l)

        combined_learning = deduped[:20]

        if not combined_learning:
            print("No operational learnings found for this context.")
            return None, None

        # STEP 2: Convert raw learning entries into enriched format
        processed_learnings = [previous_crises_task.create_learning_entry(l) for l in combined_learning]
        for pl in processed_learnings:
            ev_id = pl.get("event_id")

            # Bypass mechanism for failed event lookups
            event = {}
            if ev_id:
                try:
                    result = await client.get_event_detail(ev_id)
                    if isinstance(result, dict):
                        event = result
                    else:
                        event = {}
                except Exception as e:
                    print(f"⚠ Skipping event {ev_id} due to error: {e}")
                    event = {}

            countries = event.get("countries", []) if isinstance(event.get("countries", []), list) else []
            dtype_obj = event.get("dtype")
            dtype_name = dtype_obj.get("name") if isinstance(dtype_obj, dict) else str(dtype_obj) if dtype_obj else None

            pl["event"] = {
                "id":          event.get("id"),
                "name":        event.get("name"),
                "dtype":       dtype_name,
                "start":       event.get("disaster_start_date"),
                "countries":   [c.get("name") for c in countries if c],
                "description": event.get("description") or event.get("summary") or ""
            }

    # STEP 3: Generate AI summary
    summary = previous_crises_task.generate_ai_summary([{"related_ops_learning": processed_learnings}])

    if not summary:
        print("No summary generated — insufficient data.")
        return None, None

    # STEP 4: Generate RR questions
    rr_results = previous_crises_task.generate_rr_questions(rr_template, [{"related_ops_learning": summary}])
    rr_by_title = {r["title"]: r for r in rr_results}

    merged_summary = []
    for obj in summary:
        rr = rr_by_title.get(obj["title"], {})
        merged_summary.append({
            "title":        obj["title"],
            "insight":      obj["insight"],
            "area":         rr.get("area"),
            "rr_questions": rr.get("rr_questions", []),
            "source_note":  obj.get("source_note"),
            "metadata":     obj.get("metadata", {}),
        })

    summary_json = json.dumps(merged_summary, indent=2)

    # STEP 5: Create document text for evaluation
    def truncate(text: str, max_chars: int = 500) -> str:
        return text if len(text) <= max_chars else text[:max_chars] + "..."

    document = "\n".join(
        f"- ID {l['id']} | Code {l['appeal_code']} | Name {l['appeal_name']} | {l['document_name']}:\n"
        f"  {truncate(l['learning_text'])}"
        for l in processed_learnings
    )

    return document, summary_json

# --- G-Eval Setup ---
INSIGHT_RELEVANCY_SCORE_CRITERIA = """
Relevance(1-5) - selection of important content from the source. 
The summary should only be based of the information from the source document and the learning ids should match. 
Annotators were instructed to penalize summaries which contained redundancies and excess information.
- A score of 5 means all insights in the summary are relevant to the source document. There is no hallucination.
- A score of 3 means that there is some hallucination but there is some evidence of sources being used.
- A score of 1 means that none of the insights in the summary are backed up by the source document and there is a lot of hallucination.
"""

INSIGHT_RELEVANCY_SCORE_STEPS = """
1. Read the summary and the source document carefully.
2. Compare the summary to the source document and identify the main points of the article.
3. Assess how well the summary covers the main points of the article, and how much irrelevant or redundant information it contains.
4. Assign a relevance score from 1 to 5.
"""

RR_QUESTION_RELEVANCY_SCORE_CRITERIA= """
RR questions should be relevant to the insight it is based on. 
- A score of 5 means that all RR questions are relevant to the insight it is based on. 
- A score of 3 means that some RR questions are not relevant to the insight it is based on. 
- A score of 1 means that none of the RR questions are relevant to the insight it is based on. 
"""

RR_QUESTION_RELEVANCY_SCORE_STEPS = """
1. Read the summary and the source document carefully.
2. Compare the summary to the source document and identify the main points of the article.
3. Assess how well the RR questions are relevant to the insight it is based on.
4. Assign a relevance score from 1 to 5.
"""

UNIQUENESS_SCORE_CRITERIA = """
Uniqueness(1-5) - selection of unique content from the source. 
Each insight should be unique to one another. 
Every learning id in the source should not be used more than once. 
- A score of 5 means that all insights generated are unique from one another. 
- A score of 3 means that some insights are repeated but there is atleast one unique insight.
- A score of 1 means that are a lot of repeated insights.
"""

UNIQUENESS_SCORE_STEPS = """
1. Read the summary and the source document carefully.
2. Compare the summary to the source document and identify the main points of the article.
3. Assess how well the summary covers the main points of the article, and how much irrelevant or redundant information it contains.
4. Assign a uniqueness score from 1 to 5.
"""

COHERENCE_SCORE_CRITERIA = """
Coherence (1-5): The summary must be well-structured and present information in a logical order. The points should be distinct and not repetitive.
- A score of 5 means the summary's points are logical, well-organized, and easy to follow.
- A score of 3 means the points are somewhat disorganized or the flow is slightly confusing.
- A score of 1 means the summary is a jumble of unrelated or poorly structured points.
"""

COHERENCE_SCORE_STEPS = """
1. Read the summary's bullet points.
2. Assess if the points are presented in a logical sequence.
3. Check for clarity and how well each insight correlates to the title it generates.
4. Assign a coherence score from 1 to 5.
"""

FLUENCY_SCORE_CRITERIA = """
Fluency (1-5): The quality of the summary in terms of grammar, spelling, and readability.
- 5: Good. The summary has few or no grammatical errors and is easy to read. The language is professional and clear.
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
        criteria=criteria, steps=steps, metric_name=metric_name, document=document, summary=summary
    )
    response = task_instance.get_azure_response(messages=[{"role": "user", "content": prompt}], cache_prefix=f"geval_{metric_name}")
    if not response:
        return None
    match = re.search(r"\d+", response)
    return int(match.group(0)) if match else None

async def evaluate_single_combination(country_id: int, disaster_type_id: int) -> Optional[Dict]:
    """Evaluate a single country/disaster type combination and return the results"""
    print(f"\n--- Processing Country {country_id}, Disaster Type {disaster_type_id} ---")
    
    document, summary = await get_previous_crises_data(country_id, disaster_type_id)
    
    if not document or not summary:
        print(f"Skipping combination - no data available")
        return None
    
    print(f"Generated previous crises analysis for Country {country_id}, Disaster Type {disaster_type_id}")
    
    evaluation_metrics = {
        "Relevance": (INSIGHT_RELEVANCY_SCORE_CRITERIA, INSIGHT_RELEVANCY_SCORE_STEPS),
        "Coherence": (COHERENCE_SCORE_CRITERIA, COHERENCE_SCORE_STEPS),
        "Fluency": (FLUENCY_SCORE_CRITERIA, FLUENCY_SCORE_STEPS),
        "RR Question Relevance": (RR_QUESTION_RELEVANCY_SCORE_CRITERIA, RR_QUESTION_RELEVANCY_SCORE_STEPS),
        "Uniqueness": (UNIQUENESS_SCORE_CRITERIA, UNIQUENESS_SCORE_STEPS),
    }
    
    results = {
        "country_id": country_id,
        "disaster_type_id": disaster_type_id,
        "scores": {},
        "summary": summary
    }
    
    task_instance = BaseAITask()
    
    for eval_type, (criteria, steps) in evaluation_metrics.items():
        score_value = get_geval_score(task_instance, criteria, steps, document, summary, eval_type)
        score_num = score_value if isinstance(score_value, int) else 0
        results["scores"][eval_type] = score_num
        print(f"  {eval_type}: {score_num}/5")
    
    return results

async def evaluate_multiple_combinations(combinations: List[Tuple[int, int]]) -> List[Dict]:
    """Evaluate multiple country/disaster type combinations and return all results"""
    print(f"Starting evaluation of {len(combinations)} country/disaster type combinations...")
    
    all_results = []
    for i, (country_id, disaster_type_id) in enumerate(combinations, 1):
        print(f"\nProgress: {i}/{len(combinations)}")
        result = await evaluate_single_combination(country_id, disaster_type_id)
        if result:
            all_results.append(result)
    
    return all_results

def create_summary_dataframe(results: List[Dict]) -> pd.DataFrame:
    """Create a summary DataFrame from all evaluation results"""
    if not results:
        return pd.DataFrame()
    
    # Create detailed results DataFrame
    detailed_data = []
    for result in results:
        row = {
            "Country ID": result["country_id"],
            "Disaster Type ID": result["disaster_type_id"]
        }
        row.update(result["scores"])
        detailed_data.append(row)
    
    detailed_df = pd.DataFrame(detailed_data)
    
    # Create summary statistics DataFrame
    if len(results) > 1:
        # Overall averages across all combinations
        overall_stats = detailed_df[['Relevance', 'Coherence', 'Fluency', 'RR Question Relevance', 'Uniqueness']].mean().round(2)
        
        print("\n=== OVERALL AVERAGES ===")
        print(overall_stats)
    
    return detailed_df

async def main():
    """Main function to run the evaluation"""
    if not COUNTRY_DISASTER_COMBINATIONS:
        print("No country/disaster type combinations specified in COUNTRY_DISASTER_COMBINATIONS list!")
        print("Please add combinations to the COUNTRY_DISASTER_COMBINATIONS list at the top of the script.")
        return
    
    print(f"Evaluating {len(COUNTRY_DISASTER_COMBINATIONS)} country/disaster type combinations for Previous Crises...")
    for country_id, disaster_type_id in COUNTRY_DISASTER_COMBINATIONS:
        print(f"  - Country {country_id}, Disaster Type {disaster_type_id}")
    
    # Evaluate all combinations
    results = await evaluate_multiple_combinations(COUNTRY_DISASTER_COMBINATIONS)
    
    if not results:
        print("No combinations were successfully evaluated!")
        return
    
    # Create and display results
    detailed_df = create_summary_dataframe(results)
    
    print("\n=== DETAILED RESULTS ===")
    print(detailed_df)
    
    # Save results to file
    timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(results_dir, exist_ok=True)
    
    filename = os.path.join(results_dir, f"previous_crises_evaluation_results_{timestamp}.csv")
    detailed_df.to_csv(filename, index=False)
    print(f"\nResults saved to: {os.path.abspath(filename)}")
    
    # Save full results (including summaries) to JSON
    json_filename = os.path.join(results_dir, f"previous_crises_evaluation_full_{timestamp}.json")
    with open(json_filename, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Full results saved to: {os.path.abspath(json_filename)}")
    
    # Print summary statistics
    total_combinations = len(COUNTRY_DISASTER_COMBINATIONS)
    successful_combinations = len(results)
    
    print(f"\n=== EVALUATION SUMMARY ===")
    print(f"Total Combinations: {total_combinations}")
    print(f"Successfully Evaluated: {successful_combinations}")
    print(f"Success Rate: {(successful_combinations/total_combinations)*100:.1f}%")

if __name__ == "__main__":
    # Run the evaluation
    asyncio.run(main())
