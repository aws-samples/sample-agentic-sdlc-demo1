"""Read a file from S3."""

from botocore.exceptions import ClientError
from shared import BUCKET_NAME, s3_client, success, error


def read_file(tool_input):
    """Read a file from S3 and return its contents."""
    file_path = tool_input.get("file_path")
    if not file_path:
        return error("Missing required field: file_path")

    try:
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=file_path)
        content = response["Body"].read().decode("utf-8")
        return success(content)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return error(f"File not found: {file_path}")
        return error(f"Failed to read file: {e}")
