"""Delete all objects under a prefix in S3."""

from botocore.exceptions import ClientError
from shared import BUCKET_NAME, s3_client, success, error


def delete_folder(tool_input):
    """Delete all objects under a prefix in S3."""
    prefix = tool_input.get("prefix")
    if not prefix:
        return error("Missing required field: prefix")

    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        objects_to_delete = []
        for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix):
            for obj in page.get("Contents", []):
                objects_to_delete.append({"Key": obj["Key"]})

        if not objects_to_delete:
            return success(f"No objects found under prefix: {prefix}")

        deleted_count = 0
        for i in range(0, len(objects_to_delete), 1000):
            batch = objects_to_delete[i : i + 1000]
            s3_client.delete_objects(
                Bucket=BUCKET_NAME, Delete={"Objects": batch}
            )
            deleted_count += len(batch)

        return success(f"Deleted {deleted_count} objects under prefix: {prefix}")
    except ClientError as e:
        return error(f"Failed to delete folder: {e}")
