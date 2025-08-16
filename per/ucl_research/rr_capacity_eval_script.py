import json
import asyncio
import os
import re
import pandas as pd

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")
import django
django.setup()

from per.ucl_research.ops_learning_summary4 import RRCapacityTask, BaseAITask
from per.ucl_research.rapid_response_parser import RapidResponseCapacityParser
from per.ucl_research.ifrc_client import IFRCAPIClient

async def get_rr_capacity_data(country_id: int, disaster_type_id: int, question_index: int = 0):
    parser = RapidResponseCapacityParser()
    all_questions = parser._load_questions_data()
    if not all_questions or question_index >= len(all_questions):
        return None, None, None
    question_data = all_questions[question_index]
    
    client = IFRCAPIClient()
    
    try:
        print(f"Fetching RR capacity data for country {country_id}, disaster type {disaster_type_id}")
        
        primary_batch = await client.get_ops_learning(
            country_id=country_id,
            disaster_type_id=disaster_type_id,
            max_results=20
        )
        
        primary_labeled = [
            {**l, "source_note": "This insight was built off similar disasters from the same country."}
            for l in primary_batch
        ]
        
        seen_appeal_codes = set()
        deduplicated_results = []
        
        for learning in primary_labeled:
            appeal_info = learning.get('appeal', {})
            if isinstance(appeal_info, dict):
                appeal_code = appeal_info.get('code')
            else:
                appeal_code = str(appeal_info) if appeal_info else None
            
            if appeal_code and appeal_code not in seen_appeal_codes:
                seen_appeal_codes.add(appeal_code)
                deduplicated_results.append(learning)
            elif not appeal_code:
                deduplicated_results.append(learning)
        
        target_count = 20
        if len(deduplicated_results) < target_count:
            remaining_needed = target_count - len(deduplicated_results)
            secondary_batch = await client.get_ops_learning(
                country_id=country_id,
                disaster_type_id=None,
                max_results=remaining_needed
            )
            
            secondary_labeled = [
                {**l, "source_note": "This insight was built off other disasters from the same country."}
                for l in secondary_batch
            ]
            
            for learning in secondary_labeled:
                if len(deduplicated_results) >= target_count:
                    break
                    
                appeal_info = learning.get('appeal', {})
                if isinstance(appeal_info, dict):
                    appeal_code = appeal_info.get('code')
                else:
                    appeal_code = str(appeal_info) if appeal_info else None
                
                if appeal_code and appeal_code not in seen_appeal_codes:
                    seen_appeal_codes.add(appeal_code)
                    deduplicated_results.append(learning)
                elif not appeal_code and len(deduplicated_results) < target_count:
                    deduplicated_results.append(learning)
        
        ops_learning_data = deduplicated_results[:target_count]
        print(f"Fetched {len(ops_learning_data)} deduplicated ops learning entries")
        
        events = []
        seen_event_ids = set()
        
        for learning in ops_learning_data:
            if not isinstance(learning, dict):
                continue
                
            appeal_info = learning.get('appeal', {})
            if not isinstance(appeal_info, dict):
                continue
                
            event_details = appeal_info.get('event_details', {})
            if not isinstance(event_details, dict):
                continue
                
            event_id = event_details.get('id')
            appeal_code = appeal_info.get('code')
            
            if not event_id or event_id in seen_event_ids:
                continue
                
            seen_event_ids.add(event_id)
            
            try:
                event = await client.get_event_detail(event_id)
                
                if event:
                    event["source_note"] = f"Event from ops learning (Appeal: {appeal_code}, Event ID: {event_id})"
                    event["appeal_source"] = appeal_code
                    event["event_source_id"] = event_id
                    events.append(event)
                    
            except Exception:
                continue
        
        event_data = events[:5]
        print(f"Fetched {len(event_data)} events with source tracking")
        
        await client.close()

        task = RRCapacityTask()
        summary = task.generate_response_notes(question_data, event_data, ops_learning_data)
        
        events_context = task._format_events_for_assessment(event_data or [])
        learning_context = task._format_ops_learning_for_assessment(ops_learning_data or [])
        
        document = (
            f"Assessment Area: {question_data.get('Area') or 'No area specified'}\n\n"
            f"Events Context:\n{events_context}\n\n"
            f"Operational Learning Context:\n{learning_context}\n\n"
            f"Guiding/Probing Questions:\n{question_data.get('Guiding/probing questions') or ''}\n\n"
            f"Examples:\n{question_data.get('Examples of recommended actions') or ''}\n"
        )
        
        print(f"Successfully prepared RR capacity data for question: {question_data.get('Critical Questions', 'Unknown')}")
        print(f"   Ops Learning Entries: {len(ops_learning_data)}")
        print(f"   Events: {len(event_data)}")
        print(f"   Summary Length: {len(summary) if summary else 0} characters")
        
        return document, summary, question_data
        
    except Exception as e:
        print(f"Error in get_rr_capacity_data: {e}")
        await client.close()
        return None, None, None

RELEVANCY_SCORE_CRITERIA_RR = """
Relevance (1-5): The summary must directly answer the 'Critical Question' about humanitarian response capacity using only the information provided in the source document.
- A score of 5 means the summary provides a clear, direct answer to the capacity question, citing specific evidence about response capabilities, resources, or operational readiness from the source text.
- A score of 3 means the summary attempts to answer the capacity question but is somewhat indirect or misses key evidence about response capabilities from the source.
- A score of 1 means the summary fails to address the 'Critical Question' about response capacity at all.
- NOTE: A high score is also appropriate if the summary correctly concludes that the source document does not contain enough information to assess the specific capacity question.
"""
RELEVANCY_SCORE_STEPS_RR = """
1. First, identify the 'Critical Question' being asked about humanitarian response capacity.
2. Read the summary and assess how well it answers that specific capacity question.
3. Verify that any evidence about response capabilities, resources, or operational readiness mentioned in the summary are present in the source document ('Events Context' or 'Operational Learning Context').
4. Assign a relevance score from 1 to 5 based on how directly and accurately the summary addresses the capacity question using ONLY the provided sources.
"""
COHERENCE_SCORE_CRITERIA = """
Coherence (1-5): The capacity assessment summary must be well-structured and present information in a logical order that builds a clear picture of response capabilities.
- A score of 5 means the summary's points about response capacity are logical, well-organized, and easy to follow, creating a coherent assessment of capabilities.
- A score of 3 means the capacity points are somewhat disorganized or the flow is slightly confusing, but still understandable.
- A score of 1 means the summary is a jumble of unrelated or poorly structured points that don't form a clear capacity assessment.
"""
COHERENCE_SCORE_STEPS = """
1. Read the summary's bullet points about response capacity.
2. Assess if the points about capabilities, resources, and readiness are presented in a logical sequence.
3. Check for clarity and how well each point contributes to the overall capacity assessment.
4. Assign a coherence score from 1 to 5 based on how well the capacity information flows and connects.
"""
CONSISTENCY_SCORE_CRITERIA = """
Consistency (1-5): The capacity assessment summary must be factually aligned with the source document. All claims about response capabilities, especially references to reports (e.g., MDRKE045), must be traceable to the source.
- A score of 5 means all facts about response capabilities and references are identical to the source document.
- A score of 3 means there is a minor factual discrepancy about capabilities or a reference is slightly misrepresented.
- A score of 1 means the summary contains significant factual errors about response capabilities or cites sources not present in the document.
"""
CONSISTENCY_SCORE_STEPS = """
1. Read the capacity assessment summary and the source document side-by-side.
2. For every claim about response capabilities in the summary, find the supporting evidence in the source document.
3. Pay close attention to report codes, dates, numbers, and specific details about response resources and readiness.
4. Assign a consistency score from 1 to 5 based on factual accuracy of the capacity assessment.
"""
FLUENCY_SCORE_CRITERIA = """
Fluency (1-5): The quality of the capacity assessment summary in terms of grammar, spelling, and readability for humanitarian responders.
- 5: Excellent. The summary has few or no grammatical errors and is easy to read. The language is professional, clear, and appropriate for humanitarian response planning.
- 3: Good. The summary has some errors that affect clarity but is still understandable for responders.
- 1: Poor. The summary has many errors that make it hard to understand, which could impact response planning decisions.
"""
FLUENCY_SCORE_STEPS = "Read the capacity assessment summary and evaluate its fluency based on the given criteria. Consider whether humanitarian responders could easily understand and act on this information. Assign a fluency score from 1 to 5."

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
    response = task_instance.get_azure_response(messages=[{"role": "user", "content": prompt}], cache_prefix=f"geval_{metric_name}")
    if not response: return None
    match = re.search(r"\d+", response)
    if not match: return None
    try: return int(match.group(0))
    except Exception: return None

async def main():
    TEST_COUNTRY_ID = 93
    TEST_DISASTER_TYPE_ID = 12

    print(f"Starting RR Capacity evaluation for country {TEST_COUNTRY_ID}, disaster type {TEST_DISASTER_TYPE_ID}")

    parser = RapidResponseCapacityParser()
    all_questions = parser._load_questions_data()
    print(f"Found {len(all_questions)} questions to evaluate.")

    all_scores = []
    task_instance = BaseAITask()

    for i, question in enumerate(all_questions):
        print(f"\n--- Evaluating Question {i+1}/{len(all_questions)} ---")
        print(f"Question: {question.get('Critical Questions', 'Unknown')}")
        
        try:
            document, summary, question_data = await get_rr_capacity_data(TEST_COUNTRY_ID, TEST_DISASTER_TYPE_ID, question_index=i)

            if not (document and summary and question_data):
                print(f"Could not get data for question {i+1}. Skipping.")
                continue

            critical_question = question_data.get('Critical Questions') if question_data else 'Unknown'
            full_document_for_eval = f"Critical Question to Answer:\n{critical_question}\n\n---\n\n{document}"
            
            print(f"Evaluating question: {critical_question[:100]}...")

            evaluation_metrics = {
                "Relevance": (RELEVANCY_SCORE_CRITERIA_RR, RELEVANCY_SCORE_STEPS_RR),
                "Coherence": (COHERENCE_SCORE_CRITERIA, COHERENCE_SCORE_STEPS),
                "Consistency": (CONSISTENCY_SCORE_CRITERIA, CONSISTENCY_SCORE_STEPS),
                "Fluency": (FLUENCY_SCORE_CRITERIA, FLUENCY_SCORE_STEPS),
            }

            question_scores = []
            for eval_type, (criteria, steps) in evaluation_metrics.items():
                score = get_geval_score(task_instance, criteria, steps, full_document_for_eval, summary, eval_type)
                question_scores.append({
                    "Question": critical_question,
                    "Metric": eval_type,
                    "Score": score if score is not None else 0
                })
                all_scores.extend(question_scores)
                
            question_df = pd.DataFrame(question_scores)
            avg_score = question_df['Score'].mean()
            print(f"Question {i+1} average score: {avg_score:.2f}")
            
        except Exception as e:
            print(f"Error evaluating question {i+1}: {e}")
            continue

    if not all_scores:
        print("\nNo evaluations were completed.")
        return

    results_df = pd.DataFrame(all_scores)
    if results_df.empty:
        print("\nNo evaluation data available.")
        return
        
    average_scores = results_df.groupby('Metric')['Score'].mean().reset_index()

    print("\n\n--- Overall Evaluation Results ---")
    print("\nAverage Scores Across All Questions:")
    print(average_scores.round(2))
    
    total_questions = len(all_questions)
    successful_evaluations = len(set(results_df['Question']))
    print(f"\nSummary:")
    print(f"   Total Questions: {total_questions}")
    print(f"   Successfully Evaluated: {successful_evaluations}")
    print(f"   Success Rate: {(successful_evaluations/total_questions)*100:.1f}%")
    
    question_performance = results_df.groupby('Question')['Score'].mean().sort_values(ascending=False)
    if not question_performance.empty:
        print(f"\nBest performing question: {question_performance.index[0][:50]}... (Score: {question_performance.iloc[0]:.2f})")
        print(f"Worst performing question: {question_performance.index[-1][:50]}... (Score: {question_performance.iloc[-1]:.2f})")
    else:
        print("\nNo question performance data available")

if __name__ == "__main__":
    asyncio.run(main())