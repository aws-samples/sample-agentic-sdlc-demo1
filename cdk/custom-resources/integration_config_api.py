"""Integration Config API Lambda.

Provides CRUD endpoints for per-project integration configuration.
Stores credentials in Secrets Manager and integration state in S3.

Routes:
  GET  /projects/{projectId}/integrations
  PUT  /projects/{projectId}/integrations/{integrationId}
  GET  /projects/{projectId}/integrations/{integrationId}/status
"""

import json
import os

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# AWS clients (module-level for Lambda container reuse)
# ---------------------------------------------------------------------------

_s3_client = None
_sm_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def _sm():
    global _sm_client
    if _sm_client is None:
        _sm_client = boto3.client("secretsmanager")
    return _sm_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,PUT,OPTIONS",
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
        "body": json.dumps(body),
    }


def _s3_key(project_id: str) -> str:
    return f"{project_id}/integrations/config.json"


def _secret_name(project_id: str, integration_id: str) -> str:
    return f"aidlc/projects/{project_id}/{integration_id}"


def _read_config(project_id: str) -> dict:
    """Read config.json from S3. Returns {"integrations": {}} if not found."""
    bucket = os.environ["PROJECT_BUCKET"]
    key = _s3_key(project_id)
    try:
        resp = _s3().get_object(Bucket=bucket, Key=key)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code in ("NoSuchKey", "404"):
            return {"integrations": {}}
        raise


def _write_config(project_id: str, config: dict) -> None:
    """Write config.json to S3."""
    bucket = os.environ["PROJECT_BUCKET"]
    key = _s3_key(project_id)
    _s3().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(config).encode("utf-8"),
        ContentType="application/json",
    )


def _upsert_secret(secret_name: str, secret_value: dict) -> None:
    """Create or update a Secrets Manager secret (upsert)."""
    secret_string = json.dumps(secret_value)
    try:
        _sm().describe_secret(SecretId=secret_name)
        # Secret exists — update it
        _sm().put_secret_value(SecretId=secret_name, SecretString=secret_string)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            # Secret does not exist — create it
            _sm().create_secret(Name=secret_name, SecretString=secret_string)
        else:
            raise


def _secret_exists(secret_name: str) -> bool:
    """Return True if the secret exists in Secrets Manager."""
    try:
        _sm().describe_secret(SecretId=secret_name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _handle_get_integrations(project_id: str) -> dict:
    """GET /projects/{projectId}/integrations"""
    try:
        config = _read_config(project_id)
    except ClientError as e:
        print(f"[CONFIG_API] S3 read error: {e}")
        return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})

    # Strip any credential fields — only return non-secret data
    integrations = config.get("integrations", {})
    safe_integrations = {}
    for integration_id, integration_data in integrations.items():
        safe_integrations[integration_id] = {
            k: v for k, v in integration_data.items()
            if k not in ("email", "api_token", "authorization", "password", "secret")
        }

    return _response(200, {"integrations": safe_integrations})


def _handle_put_integration(project_id: str, integration_id: str, body: dict) -> dict:
    """PUT /projects/{projectId}/integrations/{integrationId}"""
    enabled = body.get("enabled")
    if enabled is None:
        return _response(400, {"error": "'enabled' field is required", "code": "INVALID_REQUEST"})

    if not isinstance(enabled, bool):
        return _response(400, {"error": "'enabled' must be a boolean", "code": "INVALID_REQUEST"})

    secret_key = _secret_name(project_id, integration_id)

    if enabled:
        # Write credentials to Secrets Manager FIRST
        credentials = body.get("credentials")
        if not credentials or not isinstance(credentials, dict):
            return _response(
                400,
                {"error": "'credentials' object is required when enabling", "code": "INVALID_REQUEST"},
            )

        try:
            _upsert_secret(secret_key, credentials)
        except ClientError as e:
            print(f"[CONFIG_API] Secrets Manager write error: {e}")
            return _response(500, {"error": str(e), "code": "SECRETS_WRITE_FAILED"})

        # Then read-merge-write config.json in S3
        try:
            config = _read_config(project_id)
        except ClientError as e:
            print(f"[CONFIG_API] S3 read error after SM write: {e}")
            return _response(500, {"error": str(e), "code": "S3_WRITE_FAILED"})

        # Build non-secret fields from body (exclude credentials and enabled)
        non_secret = {
            k: v for k, v in body.items()
            if k not in ("enabled", "credentials")
        }
        integration_entry = {"enabled": True, **non_secret}

        config.setdefault("integrations", {})[integration_id] = integration_entry

        try:
            _write_config(project_id, config)
        except ClientError as e:
            print(f"[CONFIG_API] S3 write error: {e}")
            # SM write succeeded but S3 write failed — atomic enable failure
            return _response(500, {"error": str(e), "code": "S3_WRITE_FAILED"})

    else:
        # Disabling — update S3 only, no Secrets Manager touch
        try:
            config = _read_config(project_id)
        except ClientError as e:
            print(f"[CONFIG_API] S3 read error: {e}")
            return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})

        integrations = config.setdefault("integrations", {})
        existing = integrations.get(integration_id, {})
        existing["enabled"] = False
        integrations[integration_id] = existing

        try:
            _write_config(project_id, config)
        except ClientError as e:
            print(f"[CONFIG_API] S3 write error: {e}")
            return _response(500, {"error": str(e), "code": "S3_WRITE_FAILED"})

    return _response(200, {"configured": enabled})


def _handle_get_status(project_id: str, integration_id: str) -> dict:
    """GET /projects/{projectId}/integrations/{integrationId}/status"""
    secret_key = _secret_name(project_id, integration_id)
    try:
        configured = _secret_exists(secret_key)
    except ClientError as e:
        print(f"[CONFIG_API] Secrets Manager describe error: {e}")
        return _response(500, {"error": str(e), "code": "SECRETS_READ_FAILED"})

    return _response(200, {"configured": configured})


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handler(event, context):
    """Lambda entry point for API Gateway proxy integration."""
    print(f"[CONFIG_API] Event: {json.dumps(event)[:500]}")

    http_method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}

    project_id = path_params.get("projectId", "")
    integration_id = path_params.get("integrationId", "")

    if not project_id:
        return _response(400, {"error": "Missing projectId", "code": "INVALID_REQUEST"})

    # Route: GET /projects/{projectId}/integrations
    if http_method == "GET" and resource == "/projects/{projectId}/integrations":
        return _handle_get_integrations(project_id)

    # Route: PUT /projects/{projectId}/integrations/{integrationId}
    if http_method == "PUT" and resource == "/projects/{projectId}/integrations/{integrationId}":
        if not integration_id:
            return _response(400, {"error": "Missing integrationId", "code": "INVALID_REQUEST"})

        raw_body = event.get("body") or "{}"
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, TypeError):
            return _response(400, {"error": "Invalid JSON body", "code": "INVALID_REQUEST"})

        return _handle_put_integration(project_id, integration_id, body)

    # Route: GET /projects/{projectId}/integrations/{integrationId}/status
    if (
        http_method == "GET"
        and resource == "/projects/{projectId}/integrations/{integrationId}/status"
    ):
        if not integration_id:
            return _response(400, {"error": "Missing integrationId", "code": "INVALID_REQUEST"})
        return _handle_get_status(project_id, integration_id)

    return _response(400, {"error": f"Unknown route: {http_method} {resource}", "code": "INVALID_REQUEST"})
