"""Generate a presigned GET URL for an S3 object."""

from botocore.exceptions import ClientError
from shared import BUCKET_NAME, s3_client, success, error


def get_presigned_url(tool_input):
    """Generate a short-lived presigned GET URL for an S3 object."""
    file_path = tool_input.get("file_path")
    if not file_path:
        return error("Missing required field: file_path")

    expires_in = tool_input.get("expires_in", 3600)  # default 1 hour

    try:
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": file_path},
            ExpiresIn=expires_in,
        )
        return success(url)
    except ClientError as e:
        return error(f"Failed to generate presigned URL: {e}")
