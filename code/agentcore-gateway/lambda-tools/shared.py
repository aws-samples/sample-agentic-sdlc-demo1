"""Shared utilities and clients for Lambda tool functions."""

import os
import boto3

BUCKET_NAME = os.environ.get("BUCKET_NAME")
if not BUCKET_NAME:
    raise RuntimeError("BUCKET_NAME environment variable is required")

s3_client = boto3.client("s3")


def success(text):
    """Return a success response in MCP content format."""
    return {"content": [{"type": "text", "text": text}]}


def error(text):
    """Return an error response in MCP content format."""
    return {"content": [{"type": "text", "text": text}], "isError": True}
