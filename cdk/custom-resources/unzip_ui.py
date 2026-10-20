import boto3
import zipfile
import io
import cfnresponse
import mimetypes


def _clear_prefix(s3, bucket, prefix):
    """Delete all objects under the given S3 prefix."""
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects = page.get("Contents", [])
        if not objects:
            continue
        delete_keys = [{"Key": obj["Key"]} for obj in objects]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": delete_keys})


def _cache_control_for(name: str) -> str:
    """Cache-Control header for an extracted SPA file.

    - HTML (e.g. index.html) and other non-hashed root files: ``no-cache`` so
      browsers ALWAYS revalidate and pick up the new content-hashed bundle
      references after a deploy (prevents stale SPA shells).
    - Vite emits content-hashed files under ``assets/`` (e.g.
      ``assets/index-AbC123.js``); those are safe to cache forever.
    """
    lower = name.lower()
    if lower.endswith(".html"):
        return "no-cache"
    if lower.startswith("assets/") or "/assets/" in lower:
        return "public, max-age=31536000, immutable"
    return "no-cache"


def _deploy_zip(s3, source_bucket, source_key, dest_bucket, dest_prefix):
    """Download a zip from S3 and extract its contents to the destination."""
    response = s3.get_object(Bucket=source_bucket, Key=source_key)
    zip_bytes = response["Body"].read()

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            data = zf.read(name)
            dest_key = f"{dest_prefix}/{name}"
            content_type, _ = mimetypes.guess_type(name)
            extra = {"CacheControl": _cache_control_for(name)}
            if content_type:
                extra["ContentType"] = content_type
            s3.put_object(
                Bucket=dest_bucket,
                Key=dest_key,
                Body=data,
                **extra,
            )


def handler(event, context):
    try:
        request_type = event["RequestType"]
        props = event.get("ResourceProperties", {})

        source_bucket = props.get("SourceBucket", "")
        source_key = props.get("SourceKey", "")
        dest_bucket = props.get("DestBucket", "")
        dest_prefix = props.get("DestPrefix", "web")

        s3 = boto3.client("s3")

        if request_type == "Delete":
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            return

        if request_type == "Update":
            _clear_prefix(s3, dest_bucket, dest_prefix)

        _deploy_zip(s3, source_bucket, source_key, dest_bucket, dest_prefix)

        cfnresponse.send(event, context, cfnresponse.SUCCESS, {
            "Status": "DEPLOYED",
        })
    except Exception as e:
        print(f"Error: {e}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {"Error": str(e)})
