# previous_crises_eval.py

import json
import asyncio
import os
import re
import pandas as pd

# Initialize Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")
import django
django.setup()

# --- imports ---
from per.ucl_research.ops_learning_summary4 import PreviousCrisesTask, BaseAITask
from per.ucl_research.ifrc_client import IFRCAPIClient

# --- Data Preparation Function ---
async def get_previous_crises_data(country_id: int, disaster_type_id: int):
    """
    Prepares the document and summary for evaluating the PreviousCrisesTask.
    """
    print("Fetching data for Previous Crises evaluation...")
    task = PreviousCrisesTask()
    client = IFRCAPIClient()

    primary = await client.get_ops_learning(country_id, disaster_type_id, max_results=20)
    secondary = await client.get_ops_learning(country_id, None, max_results=20)
    combined_learning = (primary + secondary)[:6]
    await client.close()
    
    if not combined_learning:
        print("No operational learnings found for this context.")
        return None, None
    
    print(f"Found {len(combined_learning)} learning items.")
    
    processed_learnings = [task.create_learning_entry(l) for l in combined_learning]

    summary_list = task.generate_ai_summary([{"related_ops_learning": processed_learnings}])
    summary = json.dumps(summary_list, indent=2)

    def truncate(text: str, max_chars: int = 500) -> str:
        return text if len(text) <= max_chars else text[:max_chars] + "..."

    document = "\n".join(
        f"- ID {l['id']} | Code {l['appeal_code']} | Name {l['appeal_name']} | {l['document_name']}:\n"
        f"  {truncate(l['learning_text'])}"
        for l in processed_learnings
    )

    return document, summary

# --- G-Eval Prompts ---
RELEVANCY_SCORE_CRITERIA = """
Relevance (1-5): The summary must directly answer the 'Critical Question' using only the information provided in the source document.
- A score of 5 means the summary provides a clear, direct answer to the question, citing specific evidence from the source text.
- A score of 3 means the summary attempts to answer the question but is somewhat indirect or misses key evidence from the source.
- A score of 1 means the summary fails to address the 'Critical Question' at all.
- NOTE: A high score is also appropriate if the summary correctly concludes that the source document does not contain enough information to answer the question.
"""
RELEVANCY_SCORE_STEPS = """
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
Fluency (1-5): The quality of the summary in terms of grammar, spelling, and readability.
- 5: Good. The summary has few or no grammatical errors and is easy to read. The language is professional and clear.
- 3: Fair. The summary has some errors that affect clarity but is still understandable.
- 1: Poor. The summary has many errors that make it hard to understand.
"""
FLUENCY_SCORE_STEPS = "Read the summary and evaluate its fluency based on the given criteria. Assign a fluency score from 1 to 5."

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
    prompt = EVALUATION_PROMPT_TEMPLATE.format(criteria=criteria, steps=steps, metric_name=metric_name, document=document, summary=summary)
    response = task_instance.get_azure_response(messages=[{"role": "user", "content": prompt}], cache_prefix=f"geval_{metric_name}")
    if not response: return None
    match = re.search(r"\d+", response)
    if not match: return None
    try: return int(match.group(0))
    except Exception: return None

# --- Main Execution Block ---
async def main():
    TEST_COUNTRY_ID = 93 # Example: Kenya
    TEST_DISASTER_TYPE_ID = 12 # Example: Flood

    document, summary = await get_previous_crises_data(TEST_COUNTRY_ID, TEST_DISASTER_TYPE_ID)

    if document and summary:
        print("\n--- DOCUMENT (Input to the model) ---")
        print(document)
        print("\n--- SUMMARY (Output from the model) ---")
        print(summary)

        evaluation_metrics = {
            "Relevance": (RELEVANCY_SCORE_CRITERIA, RELEVANCY_SCORE_STEPS),
            "Coherence": (COHERENCE_SCORE_CRITERIA, COHERENCE_SCORE_STEPS),
            "Consistency": (CONSISTENCY_SCORE_CRITERIA, CONSISTENCY_SCORE_STEPS),
            "Fluency": (FLUENCY_SCORE_CRITERIA, FLUENCY_SCORE_STEPS),
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

if __name__ == "__main__":
    asyncio.run(main())