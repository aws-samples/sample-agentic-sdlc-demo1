"""List S3 objects matching a prefix."""

import json
from botocore.exceptions import ClientError
from shared import BUCKET_NAME, s3_client, success, error


def list_files(tool_input):
    """List S3 objects matching a prefix."""
    prefix = tool_input.get("prefix")
    if prefix is None:
        return error("Missing required field: prefix")

    max_keys = tool_input.get("max_keys", 1000)

    try:
        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME, Prefix=prefix, MaxKeys=max_keys
        )
        keys = [obj["Key"] for obj in response.get("Contents", [])]
        return success(json.dumps(keys))
    except ClientError as e:
        return error(f"Failed to list files: {e}")
