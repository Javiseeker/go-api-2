import os
from azure.storage.blob import BlobServiceClient

def upload_to_blob(file_path, blob_name=None):
    account_name = os.getenv('AZURE_ACCOUNT_NAME')
    account_key = os.getenv('AZURE_ACCOUNT_KEY')
    container = os.getenv('AZURE_CONTAINER')

    blob_name = blob_name or os.path.basename(file_path)

    blob_service = BlobServiceClient(
        account_url=f"https://{account_name}.blob.core.windows.net",
        credential=account_key
    )

    blob_client = blob_service.get_blob_client(container=container, blob=blob_name)

    with open(file_path, "rb") as data:
        blob_client.upload_blob(data, overwrite=True)

    return blob_client.url  # return the URL if needed
