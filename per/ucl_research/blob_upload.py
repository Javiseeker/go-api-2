"""
Blob Upload Utility for UCL Research
====================================

Azure Blob Storage upload functionality for Excel files and other assets.
Copied from per.blob_upload for standalone functionality in ucl_research.
"""

import os
from azure.storage.blob import BlobServiceClient
from api.logger import logger


def upload_to_blob(file_path, blob_name=None):
    """
    Upload a file to Azure Blob Storage and return the public URL.
    
    Args:
        file_path: Local path to the file to upload
        blob_name: Name for the blob (defaults to basename of file_path)
        
    Returns:
        str: Public URL of the uploaded blob
        
    Raises:
        Exception: If upload fails or Azure credentials are missing
    """
    try:
        account_name = os.getenv('AZURE_ACCOUNT_NAME')
        account_key = os.getenv('AZURE_ACCOUNT_KEY')
        container = os.getenv('AZURE_CONTAINER')

        if not all([account_name, account_key, container]):
            raise ValueError("Missing Azure Storage credentials. Please set AZURE_ACCOUNT_NAME, AZURE_ACCOUNT_KEY, and AZURE_CONTAINER environment variables.")

        blob_name = blob_name or os.path.basename(file_path)

        blob_service = BlobServiceClient(
            account_url=f"https://{account_name}.blob.core.windows.net",
            credential=account_key
        )

        blob_client = blob_service.get_blob_client(container=container, blob=blob_name)

        
        with open(file_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)

        url = blob_client.url
        
        return url
        
    except Exception as e:
        logger.error(f"Error uploading to blob storage: {e}")
        raise