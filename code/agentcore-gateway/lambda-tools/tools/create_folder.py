"""Create a folder prefix in S3."""

from botocore.exceptions import ClientError
from shared import BUCKET_NAME, s3_client, success, error


def create_folder(tool_input):
    """Create a folder prefix in S3 by putting a zero-byte object with trailing /."""
    folder_path = tool_input.get("folder_path")
    if not folder_path:
        return error("Missing required field: folder_path")

    key = folder_path if folder_path.endswith("/") else folder_path + "/"

    try:
        s3_client.put_object(Bucket=BUCKET_NAME, Key=key, Body=b"")
        return success(f"Folder created: {key}")
    except ClientError as e:
        return error(f"Failed to create folder: {e}")
