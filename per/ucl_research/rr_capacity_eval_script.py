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
    """Prepares the document and summary for evaluating the RRCapacityTask."""
    parser = RapidResponseCapacityParser()
    all_questions = parser._load_questions_data()
    if not all_questions or question_index >= len(all_questions):
        return None, None, None
    question_data = all_questions[question_index]
    
    client = IFRCAPIClient()
    ops_learning_data = await client.get_ops_learning(country_id, disaster_type_id, max_results=10)
    event_ids = {
        learning.get('appeal', {}).get('event_details', {}).get('id')
        for learning in ops_learning_data if learning.get('appeal', {}).get('event_details', {}).get('id')
    }
    event_data = [event for eid in list(event_ids)[:5] if (event := await client.get_event_detail(eid))]
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
    return document, summary, question_data

RELEVANCY_SCORE_CRITERIA_RR = """
Relevance (1-5): The summary must directly answer the 'Critical Question' using only the information provided in the source document.
- A score of 5 means the summary provides a clear, direct answer to the question, citing specific evidence from the source text.
- A score of 3 means the summary attempts to answer the question but is somewhat indirect or misses key evidence from the source.
- A score of 1 means the summary fails to address the 'Critical Question' at all.
- NOTE: A high score is also appropriate if the summary correctly concludes that the source document does not contain enough information to answer the question.
"""
RELEVANCY_SCORE_STEPS_RR = """
1. First, identify the 'Critical Question' being asked.
2. Read the summary and assess how well it answers that specific question.
3. Verify that any evidence or facts mentioned in the summary are present in the source document ('Events Context' or 'Operational Learning Context').
4. Assign a relevance score from 1 to 5 based on how directly and accurately the summary addresses the question using ONLY the provided sources.
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
3. Check for clarity and how well each point contributes to the overall answer.
4. Assign a coherence score from 1 to 5.
"""
CONSISTENCY_SCORE_CRITERIA = """
Consistency (1-5): The summary must be factually aligned with the source document. All claims, especially references to reports (e.g., MDRKE045), must be traceable to the source.
- A score of 5 means all facts and references are identical to the source document.
- A score of 3 means there is a minor factual discrepancy or a reference is slightly misrepresented.
- A score of 1 means the summary contains significant factual errors or cites sources not present in the document.
"""
CONSISTENCY_SCORE_STEPS = """
1. Read the summary and the source document side-by-side.
2. For every claim in the summary, find the supporting evidence in the source document.
3. Pay close attention to report codes, dates, and numbers.
4. Assign a consistency score from 1 to 5 based on factual accuracy.
"""
FLUENCY_SCORE_CRITERIA = """
Fluency (1-3): The quality of the summary in terms of grammar, spelling, and readability.
- 3: Good. The summary has few or no grammatical errors and is easy to read. The language is professional and clear.
- 2: Fair. The summary has some errors that affect clarity but is still understandable.
- 1: Poor. The summary has many errors that make it hard to understand.
"""
FLUENCY_SCORE_STEPS = "Read the summary and evaluate its fluency based on the given criteria. Assign a fluency score from 1 to 3."

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
    response = task_instance.get_azure_response(messages=[{"role": "user", "content": prompt}], cache_prefix=f"geval_{metric_name}")
    if not response: return None
    match = re.search(r"\d+", response)
    if not match: return None
    try: return int(match.group(0))
    except Exception: return None

async def main():
    TEST_COUNTRY_ID = 93
    TEST_DISASTER_TYPE_ID = 12

    parser = RapidResponseCapacityParser()
    all_questions = parser._load_questions_data()
    print(f"Found {len(all_questions)} questions to evaluate.")

    all_scores = []
    task_instance = BaseAITask()

    for i, question in enumerate(all_questions):
        print(f"\n--- Evaluating Question {i+1}/{len(all_questions)} ---")
        document, summary, question_data = await get_rr_capacity_data(TEST_COUNTRY_ID, TEST_DISASTER_TYPE_ID, question_index=i)

        if not (document and summary and question_data):
            print(f"Could not get data for question {i}. Skipping.")
            continue

        critical_question = question_data.get('Critical Questions')
        full_document_for_eval = f"Critical Question to Answer:\n{critical_question}\n\n---\n\n{document}"
        
        print(f"Question: {critical_question}")

        evaluation_metrics = {
            "Relevance": (RELEVANCY_SCORE_CRITERIA_RR, RELEVANCY_SCORE_STEPS_RR),
            "Coherence": (COHERENCE_SCORE_CRITERIA, COHERENCE_SCORE_STEPS),
            "Consistency": (CONSISTENCY_SCORE_CRITERIA, CONSISTENCY_SCORE_STEPS),
            "Fluency": (FLUENCY_SCORE_CRITERIA, FLUENCY_SCORE_STEPS),
        }

        for eval_type, (criteria, steps) in evaluation_metrics.items():
            score = get_geval_score(task_instance, criteria, steps, full_document_for_eval, summary, eval_type)
            all_scores.append({
                "Question": critical_question,
                "Metric": eval_type,
                "Score": score if score is not None else 0
            })

    if not all_scores:
        print("\nNo evaluations were completed.")
        return

    results_df = pd.DataFrame(all_scores)
    average_scores = results_df.groupby('Metric')['Score'].mean().reset_index()

    print("\n\n--- Overall Evaluation Results ---")
    print("\nAverage Scores Across All Questions:")
    print(average_scores.round(2))

if __name__ == "__main__":
    asyncio.run(main())