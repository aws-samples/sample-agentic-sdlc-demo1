"""Write content to a file in S3."""

from botocore.exceptions import ClientError
from shared import BUCKET_NAME, s3_client, success, error


def write_file(tool_input):
    """Write content to a file in S3."""
    file_path = tool_input.get("file_path")
    if not file_path:
        return error("Missing required field: file_path")

    content = tool_input.get("content")
    if content is None:
        return error("Missing required field: content")

    try:
        s3_client.put_object(Bucket=BUCKET_NAME, Key=file_path, Body=content.encode("utf-8"))
        return success(f"File written: {file_path}")
    except ClientError as e:
        return error(f"Failed to write file: {e}")
