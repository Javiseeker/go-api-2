from django.conf import settings
from api.logger import logger
from openai import AzureOpenAI


class AzureServiceClient:
    """Minimal Azure OpenAI client for summarization"""
    
    def __init__(self):
        self.openai_endpoint = getattr(settings, 'AZURE_OPENAI_ENDPOINT', None)
        self.openai_key = getattr(settings, 'AZURE_OPENAI_KEY', None)
        self.openai_deployment = getattr(settings, 'AZURE_OPENAI_DEPLOYMENT_NAME', None)
        
        if self.openai_endpoint and self.openai_key and self.openai_deployment:
            print(f"=== DEBUG: Initializing Azure OpenAI client ===")
            print(f"=== DEBUG: Endpoint: {self.openai_endpoint} ===")
            print(f"=== DEBUG: Deployment: {self.openai_deployment} ===")
            print(f"=== DEBUG: API Version: 2025-01-01 ===")
            self.openai_client = AzureOpenAI(
                azure_endpoint=self.openai_endpoint,
                api_key=self.openai_key,
                api_version="2024-02-15-preview"
            )
        else:
            self.openai_client = None
            logger.warning("Azure OpenAI not configured")
    
    def get_structured_summary(self, summary, description, learning_data):
        """Send summary and description to Azure OpenAI and get structured summary with top 3 learnings"""
        if not self.openai_client:
            return None
        
        # Combine summary, description, and learning data
        learning_texts = []
        if learning_data:
            # Sort by creation date (newest first) and take top 3
            sorted_learnings = sorted(learning_data, key=lambda x: x.get('created_at', ''), reverse=True)
            top_learnings = sorted_learnings[:3]
            learning_texts = [item.get('learning_text', '') for item in top_learnings if item.get('learning_text')]
        
        combined_text = f"""
Summary: {summary}
Description: {description}

Top 3 Learning Items:
{chr(10).join([f"{i+1}. {text}" for i, text in enumerate(learning_texts)])}
"""
        
        try:
            response = self.openai_client.chat.completions.create(
                model=self.openai_deployment,
                messages=[
                    {"role": "system", "content": "Create a structured summary with 'Top 3 Learnings' as a heading followed by descriptions. Format the response with clear headings and bullet points."},
                    {"role": "user", "content": f"Based on this information, create a structured summary with 'Top 3 Learnings' as a heading and descriptions: {combined_text}"}
                ],
                temperature=0.7,
                max_tokens=15000
            )
            
            structured_summary = response.choices[0].message.content
            
            # Print the structured summary
            print(f"\n{structured_summary}")
            
            logger.info(f"Azure OpenAI structured summary completed", extra={
                'input_length': len(combined_text),
                'summary_length': len(structured_summary),
                'learning_items_count': len(learning_data),
                'structured_summary': structured_summary
            })
            
            return structured_summary
            
        except Exception as e:
            logger.error(f"Azure OpenAI structured summary failed: {e}")
            return None 