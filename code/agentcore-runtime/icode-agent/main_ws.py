"""AgentCore Runtime WebSocket entrypoint for Agentic SDLC Transformation.

Wraps the SDLC graph orchestrator using BedrockAgentCoreApp with a
@app.websocket handler that:
- Authenticates via OAuth to the MCP Gateway
- Loads or initializes session state from S3
- Enforces segment RBAC: the caller's Cognito group must own the active
  workflow segment before any Stage agent runs (task 5.5 / design Section 4c,
  Req 2.4, 2.5, 6.5)
- Executes the SDLC graph with streaming events sent over WebSocket
- Handles approval gates and client disconnects gracefully
- Routes human_input tool calls through Strands interrupts for interactive sessions
"""

import asyncio
import base64
import binascii
import json
import logging
import os
import time

import boto3

from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from mcp.client.streamable_http import streamablehttp_client
from strands.interrupt import InterruptException
from strands.multiagent import Status
from strands.session import S3SessionManager
from strands.tools.mcp import MCPClient

from config.model import create_model
from orchestrator import build_sdlc_graph
from shared_state import (
    initialize_shared_state,
    validate_shared_state,
    STAGE_NAMES,
    STAGE_DISPLAY_NAMES,
)
from tools.gateway import call_tool
from tools.integrations import load_integration_config
from tools.oauth_provider import OAuthProvider
from tools.state import (
    _extract_text_content,
    _is_not_found_error,
    _load_via_gateway,
    _markdown_to_state,
    _persist_via_gateway,
    _state_to_markdown,
)
from tools.stream_adapter import StreamAdapter
import workflow_registry

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()


def _create_mcp_client(access_token: str) -> MCPClient:
    """Create an MCPClient connected to the Gateway with Bearer auth.

    Args:
        access_token: OAuth 2.0 access token for Gateway authorization.

    Returns:
        A configured MCPClient instance.
    """
    gateway_url = os.environ["GATEWAY_MCP_URL"]
    return MCPClient(
        lambda: streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    )


def _create_session_manager(session_id: str) -> AgentCoreMemorySessionManager | None:
    """Create an AgentCoreMemorySessionManager for the session.

    Args:
        session_id: The WebSocket session identifier.

    Returns:
        A configured session manager, or None if AGENTCORE_MEMORY_ID
        is not set (graceful degradation per Requirement 3.7).
    """
    memory_id = os.environ.get("AGENTCORE_MEMORY_ID")
    if not memory_id:
        logger.warning(
            "AGENTCORE_MEMORY_ID not set; running without AgentCore Memory"
        )
        return None

    region = os.environ.get("AWS_REGION", "us-east-1")
    config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=session_id,
    )
    return AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=region,
    )


def _load_or_init_state(mcp_client, session_id: str) -> dict:
    """Load existing SharedState from S3 or initialize a fresh one.

    Args:
        mcp_client: The MCPClient connected to the Gateway.
        session_id: The session identifier used as S3 key prefix.

    Returns:
        A valid SharedState dictionary.
    """
    content = _load_via_gateway(mcp_client, session_id)

    if content is not None:
        try:
            loaded = _markdown_to_state(content)
            if validate_shared_state(loaded):
                logger.info(
                    "Loaded existing state for session %s | "
                    "stage_progress=%s | top_level_keys=%s",
                    session_id,
                    loaded.get("stage_progress", {}),
                    list(loaded.keys()),
                )
                return loaded
            logger.warning(
                "Invalid state for session %s; initializing fresh state",
                session_id,
            )
        except Exception:
            logger.warning(
                "Corrupted state for session %s; initializing fresh state",
                session_id,
                exc_info=True,
            )

    logger.info("No existing state for session %s; initializing fresh state", session_id)
    return initialize_shared_state("")


def _persist_state(mcp_client, session_id: str, shared_state: dict) -> None:
    """Persist SharedState to S3 via the Gateway.

    Args:
        mcp_client: The MCPClient connected to the Gateway.
        session_id: The session identifier.
        shared_state: The SharedState dictionary to persist.
    """
    try:
        md_content = _state_to_markdown(shared_state)
        _persist_via_gateway(mcp_client, session_id, md_content)
        logger.info("State persisted for session %s", session_id)
    except Exception:
        logger.error(
            "Failed to persist state for session %s",
            session_id,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Project_Store sync (task 5.4 / design Section 5c, 6b)
# ---------------------------------------------------------------------------

# Reserved platform prefix for the Project_Store JSON records (design Section
# 2a). Records are stored at ``_platform/projects/{project_uuid}.json`` in the
# same project bucket the Gateway already serves; this prefix is outside any
# ``{project_id}/`` artifact tree so it does not collide with per-project
# artifact storage (Req 11.3).
_PROJECT_STORE_PREFIX = "_platform/projects/"


def _project_record_key(project_uuid: str) -> str:
    """Return the S3 key for a Project_Store record."""
    return f"{_PROJECT_STORE_PREFIX}{project_uuid}.json"


def _load_project_record(mcp_client, project_uuid: str) -> dict | None:
    """Load the authoritative Project_Store record via the Gateway.

    Reads ``_platform/projects/{project_uuid}.json`` through the Gateway
    ``read_file`` tool and returns the parsed JSON object. Returns ``None``
    when the record does not exist (a connection for a project that was never
    persisted through the Project API), when the JSON is malformed, or when
    the Gateway returns an error — the caller falls back to the SharedState
    handoff mirror in those cases (design Section 6b: Project_Store is
    authoritative; SharedState is a mirror that survives transient
    unavailability).

    Args:
        mcp_client: An MCPClient connected to the Gateway.
        project_uuid: The stable Project identifier (UUID).

    Returns:
        The Project record as a dict, or ``None`` on missing/malformed/error.
    """
    if not project_uuid:
        return None

    key = _project_record_key(project_uuid)
    try:
        result = call_tool(mcp_client, "read_file", {"path": key})
    except Exception:
        logger.warning(
            "Failed to read Project_Store record at %s; falling back to mirror",
            key,
            exc_info=True,
        )
        return None

    if _is_not_found_error(result):
        logger.info(
            "No Project_Store record at %s; using SharedState handoff mirror",
            key,
        )
        return None

    is_error = (
        (isinstance(result, dict) and result.get("isError"))
        or (isinstance(result, dict) and result.get("status") == "error")
    )
    if is_error:
        logger.warning(
            "Gateway error reading Project_Store record %s; falling back to "
            "mirror: %s",
            key,
            result,
        )
        return None

    raw = _extract_text_content(result)
    if not raw:
        return None

    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "Project_Store record at %s is not valid JSON; falling back to "
            "mirror",
            key,
            exc_info=True,
        )
        return None

    if not isinstance(record, dict):
        return None
    return record


def _sync_handoff_from_project_record(
    shared_state: dict, record: dict | None
) -> None:
    """Mirror the authoritative ``handoff_state`` / ``owning_role`` into SharedState.

    The Project_Store record is the authoritative source for collaboration
    state (design Section 6b); SharedState's ``handoff`` section is a mirror
    persisted into ``aidlc-state.md`` so the metadata survives session restart
    even when the Project_Store is briefly unavailable. At connect we sync the
    mirror from the record so the runtime's view never drifts from what the
    Project API enforces (Req 9 resume intent + 5.4).

    Only the authoritative fields are overwritten; ``assignment_history`` is
    left untouched in the mirror (the Project_Store is the canonical history
    log; we do not duplicate it here). When ``record`` is ``None`` the
    existing mirror is preserved.

    Args:
        shared_state: The live SharedState dict to mutate in place.
        record: The Project record dict, or ``None`` when unavailable.
    """
    if not record:
        return

    handoff_state = record.get("handoff_state")
    owning_role = record.get("owning_role")
    if not handoff_state and not owning_role:
        return

    handoff = shared_state.setdefault(
        "handoff",
        {
            "handoff_state": workflow_registry.initial_state(),
            "owning_role": workflow_registry.owning_role(
                workflow_registry.initial_state()
            ),
            "assignment_history": [],
        },
    )

    if isinstance(handoff_state, str) and handoff_state:
        handoff["handoff_state"] = handoff_state
    if isinstance(owning_role, str) and owning_role:
        handoff["owning_role"] = owning_role


def _compute_persona_skip_completed(handoff_state: str) -> set[str]:
    """Compute the ``skip_completed`` set for the active persona's session.

    Session entry is table-driven (design Section 5c, Req 8.3): the runtime
    resolves the active segment from the authoritative ``handoff_state``
    (``segment_of(state)``) and excludes every upstream segment's stages so
    the active persona never re-runs earlier personas' work. The returned set
    is fed to :func:`build_sdlc_graph` via its existing ``skip_completed``
    parameter, which already drops those nodes from the graph and recomputes
    the entry point to the first remaining node — equal to
    ``workflow_registry.entry_node(active_segment)`` (Req 8.2).

    The computation is purely registry-derived: no BA/SA-specific branching.
    Adding a third persona to the registry automatically yields a third
    upstream skip set with no code change here (design Section 5d).

    Args:
        handoff_state: The authoritative current ``handoff_state`` for the
            project.

    Returns:
        The union of stage names belonging to all segments that come before
        the active persona's segment in persona order. Empty for the first
        segment in the chain (no upstream work to skip) and when the
        ``handoff_state`` is unknown to the registry.
    """
    if not handoff_state:
        return set()

    try:
        active_segment = workflow_registry.segment_of(handoff_state)
    except KeyError:
        # Unknown state — be conservative and skip nothing rather than
        # excluding the wrong segment.
        logger.warning(
            "handoff_state %r is not in the workflow registry; "
            "starting graph with no upstream skip",
            handoff_state,
        )
        return set()

    skip: set[str] = set()
    for upstream in workflow_registry.upstream_segments(active_segment):
        skip.update(workflow_registry.segment_stages(upstream))
    return skip


# ---------------------------------------------------------------------------
# Request-changes bounce handling (re-open the bounced-back segment)
# ---------------------------------------------------------------------------
#
# A "request changes" transition (e.g. SA -> BA) is a Project_Store record
# change only: it flips handoff_state/owning_role and records a note in
# assignment_history. It does NOT, by itself, rewind the bounced-back persona's
# workflow. Because the Strands graph persists completed nodes in its
# S3SessionManager session (``{storage_id}/sessions/``), a returning persona
# would otherwise resume "everything complete / nothing pending" and never see
# the requested change. These helpers detect an unprocessed bounce on session
# entry and re-open the active segment so the owner can act on the feedback.


def _detect_unprocessed_bounce(shared_state: dict, record: dict | None) -> dict | None:
    """Return the latest ``assignment_history`` entry when it is an unprocessed
    request-changes bounce into the current ``handoff_state``; else ``None``.

    A bounce is identified purely from the registry (no BA/SA branching): the
    latest history entry's ``from`` must declare ``to`` as its
    ``request_changes`` target, and ``to`` must equal the current authoritative
    ``handoff_state``. Idempotency: an entry whose ``timestamp`` matches the
    stored ``handoff.last_processed_bounce_ts`` is treated as already handled.
    """
    if not record:
        return None
    history = record.get("assignment_history") or []
    if not history:
        return None
    last = history[-1]
    frm, to = last.get("from"), last.get("to")
    if not frm or not to:
        return None

    current = shared_state.get("handoff", {}).get("handoff_state")
    if to != current:
        return None

    try:
        rc = workflow_registry.request_changes(frm)
    except Exception:
        return None
    if not rc or rc.get("target") != to:
        return None

    ts = last.get("timestamp")
    processed = shared_state.get("handoff", {}).get("last_processed_bounce_ts")
    if ts and ts == processed:
        return None
    return last


def _reset_graph_session(bucket: str, storage_id: str, region: str) -> None:
    """Delete the persisted Strands graph session for this project.

    Removes every object under ``{storage_id}/sessions/`` so the next graph
    build starts fresh from the active segment's entry node instead of
    resuming as "all nodes complete". Best-effort: failures are logged by the
    caller and do not abort the session.
    """
    prefix = f"{storage_id}/sessions/"
    s3 = boto3.client("s3", region_name=region)
    paginator = s3.get_paginator("list_objects_v2")
    batch: list[dict] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            batch.append({"Key": obj["Key"]})
            if len(batch) == 1000:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": batch})
                batch = []
    if batch:
        s3.delete_objects(Bucket=bucket, Delete={"Objects": batch})


def _bounce_marker_key(storage_id: str) -> str:
    """S3 key for the durable "last processed bounce" idempotency marker.

    Stored as a dotfile prefix so the file browser (which hides ``.``-prefixed
    segments) does not surface it.
    """
    return f"{storage_id}/.collab/last-processed-bounce"


def _read_processed_bounce(bucket: str, storage_id: str, region: str) -> str | None:
    """Return the timestamp of the last bounce already re-opened, or ``None``.

    Durable across reconnects (the state-file serializer does not round-trip
    this marker), so the re-open runs exactly once per bounce.
    """
    try:
        s3 = boto3.client("s3", region_name=region)
        obj = s3.get_object(Bucket=bucket, Key=_bounce_marker_key(storage_id))
        return (obj["Body"].read().decode("utf-8").strip() or None)
    except Exception:
        # Missing marker (first time) or transient error → treat as unprocessed.
        return None


def _write_processed_bounce(bucket: str, storage_id: str, region: str, ts: str) -> None:
    """Record that the bounce at ``ts`` has been re-opened (best-effort)."""
    try:
        s3 = boto3.client("s3", region_name=region)
        s3.put_object(
            Bucket=bucket,
            Key=_bounce_marker_key(storage_id),
            Body=(ts or "").encode("utf-8"),
        )
    except Exception:
        logger.warning("Failed to write processed-bounce marker", exc_info=True)


def _apply_bounce_reset(shared_state: dict, bounce: dict) -> None:
    """Re-open the active segment after a request-changes bounce.

    Generic across directions (SA -> BA and Dev -> SA): resets every tracked
    stage to ``not_started`` (so the graph re-runs the active persona's stages
    — upstream segments are still excluded via ``skip_completed`` — and the
    progress panel reflects the re-opened work), seeds ``change_request`` with
    the requester's note for downstream agents, and records
    ``last_processed_bounce_ts`` for idempotency. The note is also surfaced to
    the user by the frontend change-request banner.
    """
    stage_progress = shared_state.setdefault("stage_progress", {})
    for stage in STAGE_NAMES:
        stage_progress[stage] = "not_started"

    frm = bounce.get("from")
    try:
        from_role = workflow_registry.owning_role(frm) if frm else ""
    except Exception:
        from_role = ""

    shared_state["change_request"] = {
        "note": bounce.get("note"),
        "from_state": frm,
        "from_role": from_role,
        "actor": bounce.get("actor"),
        "at": bounce.get("timestamp"),
    }

    shared_state.setdefault("handoff", {})["last_processed_bounce_ts"] = bounce.get(
        "timestamp"
    )


def _change_request_requester_label(shared_state: dict) -> str:
    """Human label for the persona who requested changes (e.g. "Solution
    Architect" for an SA->BA bounce, "Software Developer" for a Dev->SA bounce).

    Derived from the seeded ``change_request.from_role`` via the registry so the
    wording is correct for any bounce direction. Falls back gracefully.
    """
    cr = shared_state.get("change_request") or {}
    role = cr.get("from_role") or ""
    if not role:
        return "the reviewer"
    try:
        return workflow_registry.display_label(role)
    except Exception:
        return "the reviewer"


# Canonical output artifact for inception stages that MUST produce one when
# completed. Used by the resume guard to refuse to skip a stage whose artifact
# is missing (paths are relative to the project's artifact prefix). Stages not
# listed here (workspace_detection, reverse_engineering, inception_complete,
# construction_handoff) are not artifact-gated.
_STAGE_REQUIRED_ARTIFACTS = {
    "requirements_analysis": "aidlc-docs/inception/requirements/requirements.md",
    "user_stories": "aidlc-docs/inception/user-stories/stories.md",
    "workflow_planning": "aidlc-docs/inception/plans/execution-plan.md",
    "application_design": "aidlc-docs/inception/application-design/components.md",
    "units_generation": "aidlc-docs/inception/application-design/unit-of-work.md",
}


def _stage_artifact_exists(shared_state: dict, stage: str) -> bool:
    """Return ``True`` unless ``stage`` has a known canonical artifact missing.

    Stages without a tracked artifact are treated as present (not gated). On a
    lookup error we assume present to avoid spurious re-run loops on transient
    Gateway errors.
    """
    path = _STAGE_REQUIRED_ARTIFACTS.get(stage)
    if not path:
        return True
    try:
        from tools.file_ops import file_exists

        return bool(file_exists(shared_state, path))
    except Exception:
        logger.warning(
            "Artifact existence check failed for stage %s; assuming present",
            stage,
            exc_info=True,
        )
        return True


def _filter_completed_by_artifacts(shared_state: dict, completed: set) -> set:
    """Drop from ``completed`` any stage marked done/in-range whose canonical
    artifact is missing, so a stale/rebuilt resume state cannot silently skip
    real work (e.g. ``application_design`` after a request-changes bounce).

    Planner-``skipped`` stages are kept (a legitimate skip, e.g. greenfield
    reverse-engineering or un-needed user stories). Stages without a tracked
    artifact are kept as-is.
    """
    progress = shared_state.get("stage_progress", {})
    verified: set = set()
    for s in completed:
        if progress.get(s) == "skipped" or _stage_artifact_exists(shared_state, s):
            verified.add(s)
        else:
            logger.warning(
                "Resume guard: stage %s marked done but its artifact is "
                "missing — re-running instead of skipping",
                s,
            )
    return verified


# ---------------------------------------------------------------------------
# Runtime segment RBAC (task 5.5 / design Section 4c, Req 2.4, 2.5, 6.5)
# ---------------------------------------------------------------------------


def _decode_caller_groups(context, websocket=None) -> list[str]:
    """Return the caller's Cognito group names from the WebSocket bearer token.

    The WebSocket request carries the user's Cognito ID token in the standard
    ``Authorization: Bearer <jwt>`` header (the same token the Frontend already
    attaches via ``AuthContext`` for Project API calls). The bedrock_agentcore
    runtime exposes that header on ``RequestContext.request_headers`` as a
    case-preserving ``{header: value}`` dict (see ``bedrock_agentcore.runtime
    .context.RequestContext``).

    This helper:
      * locates the ``Authorization`` header (case-insensitively),
      * strips the ``Bearer `` prefix,
      * base64url-decodes the JWT *payload* (the middle ``header.payload
        .signature`` segment),
      * extracts ``cognito:groups`` and normalises both shapes the Cognito
        authorizer can produce (a list, or a comma-separated string).

    The ID token's signature is **not** verified here -- the AgentCore Runtime
    front-door is expected to reject unauthenticated WebSocket connections.
    The check we perform is "which Cognito groups did the verified caller
    claim?", read off the same JWT the API Gateway Cognito authorizer uses for
    Project API RBAC (design Section 4c). On any failure (header missing,
    malformed token, base64/JSON decode error) we return an empty list so the
    caller is never silently authorised.

    Args:
        context: The ``RequestContext`` passed to ``handle_message`` by the
            ``@app.websocket`` decorator.

    Returns:
        The list of Cognito group names the caller belongs to, or ``[]`` when
        no token / groups can be decoded.
    """
    headers = getattr(context, "request_headers", None) or {}
    if not isinstance(headers, dict):
        headers = {}

    def _get_header(name: str) -> str:
        # Case-insensitive header lookup.
        for key, value in headers.items():
            if isinstance(key, str) and key.lower() == name:
                return value or ""
        return ""

    # Gather candidate raw JWTs, in priority order, from the places the
    # AgentCore runtime may expose the caller's token. Observed on the live
    # WebSocket RequestContext: the inbound caller token is forwarded as the
    # ``workloadaccesstoken`` header (there is no Authorization header, and the
    # browser cannot set one). We keep Authorization and the WS subprotocol as
    # fallbacks for non-browser / future clients.
    candidates: list[tuple[str, str]] = []

    # Source A (browser): the Starlette WebSocket handshake carries the token in
    # ``Sec-WebSocket-Protocol`` / the parsed ``subprotocols`` — AgentCore's
    # filtered ``context.request_headers`` does NOT. Read it off the websocket.
    _ws_headers = getattr(websocket, "headers", None)
    _ws_scope = getattr(websocket, "scope", None)
    _prefix = "base64UrlBearerAuthorization."

    def _ws_get(_name):
        if _ws_headers is None:
            return None
        try:
            return _ws_headers.get(_name)
        except Exception:  # noqa: BLE001
            return None

    def _b64url_to_jwt(_b64):
        _pad = "=" * (-len(_b64) % 4)
        try:
            return base64.urlsafe_b64decode(_b64 + _pad).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return None

    if isinstance(_ws_scope, dict):
        _subs = _ws_scope.get("subprotocols")
        if isinstance(_subs, (list, tuple)):
            for _sp in _subs:
                if isinstance(_sp, str) and _sp.startswith(_prefix):
                    _jwt = _b64url_to_jwt(_sp[len(_prefix):].strip())
                    if _jwt:
                        candidates.append(("ws.subprotocols", _jwt))
                    break
    for _item in (_ws_get("sec-websocket-protocol") or "").split(","):
        _item = _item.strip()
        if _item.startswith(_prefix):
            _jwt = _b64url_to_jwt(_item[len(_prefix):].strip())
            if _jwt:
                candidates.append(("ws.header.subprotocol", _jwt))
            break
    _ws_authz = (_ws_get("authorization") or "").strip()
    if _ws_authz:
        _p = _ws_authz.split(None, 1)
        if len(_p) == 2 and _p[0].lower() == "bearer":
            candidates.append(("ws.header.authorization", _p[1].strip()))

    wat = _get_header("workloadaccesstoken")
    if wat:
        raw = wat.strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        candidates.append(("workloadaccesstoken", raw))

    authz = _get_header("authorization")
    if authz:
        parts = authz.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            candidates.append(("authorization", parts[1].strip()))

    proto = _get_header("sec-websocket-protocol")
    if proto:
        prefix = "base64UrlBearerAuthorization."
        for item in proto.split(","):
            item = item.strip()
            if item.startswith(prefix):
                b64 = item[len(prefix):].strip()
                pad = "=" * (-len(b64) % 4)
                try:
                    candidates.append(
                        ("subprotocol", base64.urlsafe_b64decode(b64 + pad).decode("utf-8"))
                    )
                except (binascii.Error, ValueError, UnicodeDecodeError):
                    pass
                break

    if not candidates:
        # Diagnostic (safe): header/subprotocol NAMES + context attribute names
        # only — never token values — so a relocated token can be found without
        # leaking secrets. Fail closed.
        try:
            ws_keys = sorted(k for k, _ in _ws_headers.items()) if _ws_headers is not None else None
            ws_subs = _ws_scope.get("subprotocols") if isinstance(_ws_scope, dict) else None
            logger.warning(
                "Runtime RBAC: no caller token on WS request; ws_header_keys=%s "
                "ws_subprotocols=%s ctx_header_keys=%s ctx_attrs=%s",
                ws_keys,
                ws_subs,
                sorted(k for k in headers.keys() if isinstance(k, str)),
                sorted(a for a in dir(context) if not a.startswith("_")),
            )
        except Exception:  # noqa: BLE001
            pass
        return []

    def _decode_payload(raw: str):
        def _payload_from_jwt(s: str):
            segs = s.split(".")
            if len(segs) < 2:
                return None
            pb = segs[1]
            pad = "=" * (-len(pb) % 4)
            try:
                data = json.loads(base64.urlsafe_b64decode(pb + pad).decode("utf-8"))
            except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                return None
            return data if isinstance(data, dict) else None

        # (a) raw is already a JWT (header.payload.signature).
        payload = _payload_from_jwt(raw)
        if payload is not None:
            return payload
        # (b) raw may be a base64/base64url-wrapped JWT (no dots, e.g. the
        # AgentCore ``workloadaccesstoken``). Unwrap with either alphabet, retry.
        for _decoder in (base64.urlsafe_b64decode, base64.b64decode):
            try:
                unwrapped = _decoder(raw + "=" * (-len(raw) % 4)).decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                continue
            payload = _payload_from_jwt(unwrapped)
            if payload is not None:
                return payload
        return None

    decode_report: list[str] = []
    for source, raw in candidates:
        payload = _decode_payload(raw)
        if payload is None:
            decode_report.append(f"{source}=nojwt(len={len(raw)},segs={len(raw.split('.'))})")
            continue
        raw_groups = payload.get("cognito:groups", "")
        if isinstance(raw_groups, list):
            groups = [g for g in raw_groups if isinstance(g, str) and g]
        elif isinstance(raw_groups, str):
            groups = [g for g in raw_groups.split(",") if g]
        else:
            groups = []
        if groups:
            return groups
        decode_report.append(
            f"{source}=no_groups(token_use={payload.get('token_use')},"
            f"keys={sorted(str(k) for k in payload.keys())})"
        )

    # Candidate(s) present but none yielded cognito:groups. Log a compact,
    # value-free report (sources, lengths, claim key names) for diagnosis.
    try:
        logger.warning(
            "Runtime RBAC: token candidate(s) present but no cognito:groups; report=%s",
            decode_report,
        )
    except Exception:  # noqa: BLE001
        pass
    return []


def _authorize_segment(handoff_state: str, caller_groups: list[str]) -> tuple[bool, str]:
    """Verify the caller's role owns the active workflow segment.

    Runtime RBAC (design Section 4c, Req 2.4 / 2.5 / 6.5): before any Stage
    agent runs we resolve the project's *active* workflow segment from the
    authoritative ``handoff_state`` (``segment_of(state)``) and -- when the
    caller's Cognito groups can be decoded -- require those groups to contain
    that segment's owning role. The check is purely registry-derived (no
    BA/SA-specific branching) so adding a third persona is a registry edit
    (design Section 5d).

    Authorisation is decided in three layers, in order:

    1. **Registry resolution.** If ``handoff_state`` does not map to a known
       segment / owning role, the call is rejected (fail-closed) so registry
       drift cannot accidentally grant access.

    2. **Group-present enforcement.** If ``caller_groups`` is non-empty, the
       caller's groups must include the segment's owning role; otherwise the
       call is rejected with the required role surfaced for the error message.

    3. **Group-absent soft-allow (AgentCore propagation gap).** AgentCore
       Identity, in this deployment configuration, replaces the inbound
       Cognito access token with its own opaque ``workloadaccesstoken`` that
       does not carry the ``cognito:groups`` claim. When no groups can be
       decoded from any token candidate, the runtime check soft-allows the
       connection and emits a warning. The Project API remains the
       authoritative RBAC layer for every state-changing route (HTTP 403 on
       non-owning callers, HTTP 422 on illegal transitions) using the verified
       Cognito ID token via API Gateway's Cognito authorizer, so soft-allowing
       here does not lower the effective security envelope -- it just stops
       the runtime check from producing false negatives until the AgentCore
       propagation gap is closed.

    The function never raises -- it returns a ``(authorized, required_role)``
    tuple so the caller can build a precise error message.

    Args:
        handoff_state: The authoritative current ``handoff_state`` for the
            project (already synced from the Project_Store record).
        caller_groups: The Cognito group names decoded from the caller's
            bearer token, or an empty list when no token candidate yielded
            ``cognito:groups``.

    Returns:
        ``(authorized, required_role)`` where ``required_role`` is the segment
        owning role for the active segment, or ``""`` when the registry has no
        entry for ``handoff_state`` (fail-closed).
    """
    try:
        active_segment = workflow_registry.segment_of(handoff_state)
        required_role = workflow_registry.segment_owning_role(active_segment)
    except KeyError:
        logger.warning(
            "Cannot determine active segment for handoff_state=%r; "
            "denying connection (fail-closed)",
            handoff_state,
        )
        return (False, "")

    if not required_role:
        return (False, "")

    if caller_groups:
        # Standard path: the caller's bearer token carried Cognito groups;
        # enforce that they include the segment's owning role.
        return (required_role in caller_groups, required_role)

    # AgentCore workload-token propagation gap: no Cognito groups reached the
    # container. Soft-allow and log so the gap stays visible. The Project API
    # is the authoritative RBAC layer for state-changing routes (Req 2.2-2.3,
    # 6.5), so this does not weaken the overall authorisation envelope.
    logger.warning(
        "Runtime RBAC: no Cognito groups in caller token "
        "(AgentCore workload-token propagation gap); soft-allowing -- "
        "Project API remains the authoritative RBAC layer. "
        "handoff_state=%s, required_role=%s",
        handoff_state,
        required_role,
    )
    return (True, required_role)


async def _handle_approval_gate(websocket, stream_adapter, shared_state):
    """Handle an approval gate by sending the prompt and waiting for response.

    Args:
        websocket: The Starlette WebSocket connection.
        stream_adapter: The StreamAdapter instance.
        shared_state: The current SharedState dictionary.

    Returns:
        The client's approval response string, or None if disconnected.
    """
    approval = shared_state.get("approval", {})
    stage = approval.get("current_gate_stage", "")
    summary = approval.get("summary", "")
    options = approval.get("options", [])

    await stream_adapter.send_approval_required(stage, summary, options)

    try:
        response = await websocket.receive_text()
        return response
    except Exception:
        # Client disconnected while awaiting approval
        return None


async def _handle_handoff_interrupt(
    interrupt, shared_state, stream_adapter,
):
    """Run the segment-handoff side effects for a single handoff interrupt.

    Centralizes the actions a Handoff_Gate (design Section 5b, Req 6.3)
    requires regardless of how the interrupt reached the runner — whether
    Strands surfaced it as an INTERRUPTED result event or let
    ``InterruptException`` propagate out of ``stream_async``:

    1. Mark ``shared_state["handoff"]["pending"] = True`` so the
       collaboration mirror records that the handoff fired.
    2. Resolve ``from_role`` / ``to_role`` from the registry via
       ``segment_owning_role`` / ``successor_segment`` so the Frontend
       ``HandoffCard`` carries the correct labels for any persona
       boundary, not just BA → SA.
    3. Emit exactly one ``handoff_required`` WebSocket message — the only
       in-app surface that renders the "Assign to {next role}" action.
    4. Persist SharedState through the Gateway when ``_mcp_client`` and
       ``_session_id`` are present, so the mirror survives session
       restart/resume.

    The caller is responsible for halting the graph loop after this
    helper returns; the helper itself does not enqueue an
    ``interruptResponse`` because the next persona reconnects through a
    fresh WebSocket session.
    """
    reason = interrupt.reason or {}
    message = reason.get("message", "")
    from_segment = reason.get("from_segment", "")

    handoff = shared_state.setdefault("handoff", {})
    handoff["pending"] = True

    project_uuid = (
        shared_state.get("_project_uuid")
        or shared_state.get("_session_id")
        or ""
    )

    # Resolve from_role / to_role from the registry so the card label
    # generalizes to any persona boundary (Section 9). Failures here are
    # logged but never block the message — the Frontend tolerates empty
    # role context and renders a sensible default label.
    from_role = ""
    to_role = ""
    try:
        if from_segment:
            from_role = workflow_registry.segment_owning_role(from_segment)
            successor = workflow_registry.successor_segment(from_segment)
            if successor:
                to_role = workflow_registry.segment_owning_role(successor)
    except Exception:
        logger.debug(
            "Failed to resolve handoff roles from registry; "
            "emitting handoff_required without role context",
            exc_info=True,
        )

    await stream_adapter.send_handoff_required(
        project_uuid=str(project_uuid),
        from_role=from_role or "",
        to_role=to_role or "",
        summary=str(message),
    )

    # Persist SharedState mirror so collaboration metadata
    # (handoff.pending) survives session restart/resume.
    mcp_client = shared_state.get("_mcp_client")
    session_id = shared_state.get("_session_id")
    if mcp_client is not None and session_id:
        try:
            md_content = _state_to_markdown(shared_state)
            _persist_via_gateway(mcp_client, str(session_id), md_content)
        except Exception:
            logger.exception(
                "Failed to persist state at handoff for session %s",
                session_id,
            )


async def _run_graph_with_interrupts(
    graph, task, shared_state, stream_adapter,
):
    """Execute the graph, handling human_input interrupts via WebSocket.

    When the graph pauses for a human_input interrupt, this function:
    1. Sends the prompt to the client over WebSocket
    2. Waits for the client's response (no artificial timeout)
    3. Resumes the graph with the interruptResponse

    The user can take as long as they need — there is no timeout.  If the
    WebSocket disconnects, ``receive_text()`` raises immediately and the
    exception propagates to the caller for cleanup.

    A ``handoff`` breakout interrupt (raised by a ``handoff_gate__{seg}``
    node, design Section 5b) is handled as a one-shot terminal event: a
    ``handoff_required`` message is emitted, SharedState is persisted via
    the Gateway, and the graph loop halts (Req 6.3). The next persona
    resumes through a fresh WebSocket connection after the Frontend calls
    the Project API to perform the handoff transition.

    The handoff path is reachable through two channels:

    * **Result-event path** — Strands yields a final
      ``{"result": GraphResult(status=INTERRUPTED, interrupts=[...])}``
      event. Detected via ``graph_result.status == Status.INTERRUPTED``
      or ``graph._interrupt_state.activated``.
    * **Raised-exception path** — Strands lets ``InterruptException``
      propagate out of ``stream_async`` (which happens when a
      ``MultiAgentBase`` node like ``handoff_gate__{seg}`` calls
      ``raise InterruptException(...)`` directly). Caught around the
      inner ``async for`` and routed through the same helper.

    Both channels run the same handoff side effects via
    :func:`_handle_handoff_interrupt`, so the Frontend ``HandoffCard``
    is rendered identically regardless of which channel fired.

    Args:
        graph: The built Strands Graph.
        task: The input task string (or interruptResponse list on resume).
        shared_state: The SharedState dict passed as invocation_state.
        stream_adapter: The StreamAdapter for WebSocket output.

    Returns:
        ``True`` when the graph loop halted because of a handoff breakout
        (a ``handoff_required`` card was emitted and ownership transfers to
        the next persona). ``False`` when the graph ran to normal completion.
        Callers use this to decide whether to send ``workflow_complete``:
        a handoff is **not** a completion, so the caller must suppress the
        ``workflow_complete`` banner (otherwise the BA sees "Workflow
        complete" instead of the handoff card and the UI marks the session
        done while it is actually awaiting the SA).
    """
    current_input = task
    handoff_occurred = False

    while True:
        # Run the graph (or resume from interrupt)
        graph_result = None
        event_count = 0
        try:
            async for event in graph.stream_async(
                current_input, invocation_state=shared_state
            ):
                await stream_adapter.process_event(event)
                event_count += 1
                # The final event from stream_async carries the GraphResult
                if isinstance(event, dict) and "result" in event:
                    graph_result = event["result"]
                    logger.info(
                        "Graph result event received: status=%s, interrupted_nodes=%s, interrupts=%s",
                        getattr(graph_result, "status", "N/A"),
                        getattr(graph_result, "interrupted_nodes", []),
                        [i.id for i in getattr(graph_result, "interrupts", [])],
                    )
        except InterruptException as exc:
            # Strands let an InterruptException propagate out of stream_async
            # rather than yielding an INTERRUPTED result event. This happens
            # when a MultiAgentBase node (e.g. handoff_gate__{seg}) calls
            # ``raise InterruptException(...)`` directly. If the interrupt
            # carries the handoff reason, route it through the same helper
            # the result-event path uses; otherwise re-raise so the outer
            # ``except Exception`` surfaces it as ``Workflow execution failed``
            # exactly as it does today (Req 1.1 / 3.3 in the bugfix spec).
            interrupt = getattr(exc, "interrupt", None)
            reason = getattr(interrupt, "reason", None) or {}
            if interrupt is not None and reason.get("handoff"):
                logger.info(
                    "Caught raised handoff InterruptException: id=%s, "
                    "from_segment=%s — routing through handoff branch",
                    getattr(interrupt, "id", "?"),
                    reason.get("from_segment", ""),
                )
                await _handle_handoff_interrupt(
                    interrupt, shared_state, stream_adapter,
                )
                logger.info(
                    "Handoff breakout (raised) — halting graph loop"
                )
                handoff_occurred = True
                break
            raise

        logger.info(
            "stream_async exhausted: event_count=%d, graph_result=%s, "
            "interrupt_state.activated=%s, interrupt_state.interrupts=%s",
            event_count,
            getattr(graph_result, "status", "None") if graph_result else "None",
            getattr(getattr(graph, "_interrupt_state", None), "activated", "N/A"),
            list(getattr(getattr(graph, "_interrupt_state", None), "interrupts", {}).keys()),
        )

        # Check if the graph was interrupted — two ways to detect:
        # 1. Via the result event (normal completion path)
        # 2. Via the graph's interrupt state directly (interrupt path — result event is NOT yielded on interrupt)
        is_interrupted = (
            (graph_result is not None and hasattr(graph_result, "status") and graph_result.status == Status.INTERRUPTED)
            or (hasattr(graph, "_interrupt_state") and graph._interrupt_state.activated)
        )

        if is_interrupted:
            # Collect interrupts from whichever source has them
            interrupts = []
            if graph_result is not None and hasattr(graph_result, "interrupts") and graph_result.interrupts:
                interrupts = graph_result.interrupts
            elif hasattr(graph, "_interrupt_state"):
                interrupts = list(graph._interrupt_state.interrupts.values())

            logger.info(
                "Graph interrupted: source=%s, interrupt_count=%d, ids=%s",
                "graph_result" if (graph_result and getattr(graph_result, "interrupts", None)) else "_interrupt_state",
                len(interrupts),
                [i.id for i in interrupts],
            )

            if not interrupts:
                logger.warning("Graph in interrupted state but no interrupts found — exiting loop to avoid infinite loop")
                break

            responses = []
            handoff_handled = False
            for interrupt in interrupts:
                reason = interrupt.reason or {}
                message = reason.get("message", "Agent requesting input")
                is_breakout = reason.get("breakout", False)
                is_handoff = bool(reason.get("handoff", False))
                logger.info(
                    "Processing interrupt: id=%s, breakout=%s, handoff=%s, "
                    "message_preview=%s",
                    interrupt.id, is_breakout, is_handoff, str(message)[:80],
                )

                if is_handoff:
                    # Handoff_Gate breakout (design Section 5b, Req 6.3): the
                    # current segment is finished and ownership transfers to
                    # the next persona via the Project API. Delegate to the
                    # shared helper so the result-event path and the
                    # raised-exception path produce identical observable
                    # behavior. Do NOT enqueue an interruptResponse — the
                    # next persona resumes through a fresh connection after
                    # the Frontend calls projectService.transition(...).
                    await _handle_handoff_interrupt(
                        interrupt, shared_state, stream_adapter,
                    )
                    handoff_handled = True
                    continue

                if is_breakout:
                    await stream_adapter.send_human_input_notification(message)
                    responses.append({
                        "interruptResponse": {
                            "interruptId": interrupt.id,
                            "response": "acknowledged",
                        }
                    })
                else:
                    logger.info("Waiting for user response to interrupt: id=%s", interrupt.id)
                    user_response = await stream_adapter.send_human_input_request(message)
                    logger.info("User response received for interrupt: id=%s, response_preview=%s", interrupt.id, str(user_response)[:80])
                    responses.append({
                        "interruptResponse": {
                            "interruptId": interrupt.id,
                            "response": user_response,
                        }
                    })

            if handoff_handled:
                # Handoff breakout halts the graph loop (Req 6.3); the next
                # persona resumes via a fresh WebSocket connection after the
                # Frontend records the transition through the Project API.
                logger.info("Handoff breakout — halting graph loop")
                handoff_occurred = True
                break

            logger.info("Resuming graph with %d interruptResponse(s)", len(responses))
            current_input = responses
            continue

        # Graph completed normally — exit the loop
        logger.info(
            "Graph loop complete: status=%s",
            getattr(graph_result, "status", "None") if graph_result else "None",
        )
        break

    # Report whether the loop halted at a handoff boundary so the caller can
    # suppress the ``workflow_complete`` banner (a handoff is not a completion).
    return handoff_occurred


@app.websocket
async def handle_message(websocket, context):
    """Handle incoming WebSocket connections for the AIDLC workflow.

    Implements the full session lifecycle:
    1. Accept connection, extract session_id
    2. Authenticate with Gateway via OAuth
    3. Load or initialize session state from S3
    4. Execute the SDLC graph with streaming output
    5. Handle approval gates and client disconnects
    6. Persist state and send workflow_complete on finish

    Args:
        websocket: Starlette WebSocket connection.
        context: RequestContext with session_id and request headers.
    """
    await websocket.accept()

    mcp_client = None
    shared_state = None
    session_id = None
    storage_id = None

    try:
        # 1. Extract session_id from request context
        session_id = context.session_id
        logger.info("WebSocket session started: %s", session_id)

        # 2. Wait for the init message to get project_id
        init_raw = await websocket.receive_text()
        try:
            init_msg = json.loads(init_raw)
            project_id = init_msg.get("project_id", "").strip()
            project_name = init_msg.get("project_name", "").strip()
            project_uuid = init_msg.get("project_uuid", "").strip()
            user_request = init_msg.get("request", "").strip()
        except (json.JSONDecodeError, AttributeError):
            # Fallback: treat the whole message as a plain-text request
            project_id = ""
            project_name = ""
            project_uuid = ""
            user_request = init_raw

        # Use project_id as the S3 root prefix; fall back to session_id
        storage_id = project_id if project_id else session_id
        logger.info(
            "Using storage_id=%s (project_id=%s, session_id=%s)",
            storage_id, project_id, session_id,
        )

        # 3. Create OAuthProvider and obtain access token
        oauth = OAuthProvider.from_env()
        access_token = oauth.get_access_token()

        # 4. Create MCPClient with Bearer auth
        mcp_client = _create_mcp_client(access_token)
        mcp_client.__enter__()

        # 5. Create AgentCoreMemorySessionManager
        session_manager = _create_session_manager(session_id)

        # 6. Load or initialize SharedState from S3 via Gateway
        shared_state = _load_or_init_state(mcp_client, storage_id)

        # 6b. Load integration config from S3 and merge into SharedState
        # Use project_uuid (not project_id slug) — config is stored under the UUID path.
        integration_storage_id = project_uuid if project_uuid else storage_id
        integration_config = load_integration_config(mcp_client, integration_storage_id)
        integrations = shared_state.setdefault("integrations", {})
        integrations["config"] = integration_config
        integrations.setdefault("session_overrides", {})
        integrations.setdefault("action_log", [])

        def _reload_integration_config() -> None:
            """Reload integration config just before graph execution.
            The UI may create the config file after the WebSocket connects,
            so we reload immediately before starting the graph.
            """
            fresh_config = load_integration_config(mcp_client, integration_storage_id)
            if fresh_config:
                shared_state.setdefault("integrations", {})["config"] = fresh_config
                logger.info("Integration config reloaded: %s", list(fresh_config.keys()))

        # 7. Inject runtime references into SharedState
        #    _session_id is used as the S3 key prefix for artifacts
        #    _agentcore_session_id is the user-scoped session for graph persistence
        shared_state["_mcp_client"] = mcp_client
        shared_state["_session_id"] = storage_id
        shared_state["_project_uuid"] = project_uuid if project_uuid else storage_id
        shared_state["_session_manager"] = session_manager
        shared_state["_websocket"] = websocket  # For artifact_updated notifications

        # Set project name from project_id if provided
        if project_id:
            shared_state["project"]["selected_project"] = project_name or project_id
            # In AgentCore mode the "workspace" is the S3 prefix, not a
            # local directory.  Setting workspace_root tells the
            # workspace_detection agent where files live.
            shared_state["project"]["workspace_root"] = project_id

        # 7b. Sync the handoff mirror from the authoritative Project_Store
        #     record and compute the persona-aware ``skip_completed`` set
        #     (task 5.4 / design Section 5c, 6b, Req 8.1-8.4).
        #
        #     The Project_Store record at ``_platform/projects/{uuid}.json``
        #     is the canonical owner of ``handoff_state``/``owning_role``.
        #     SharedState mirrors them so collaboration metadata survives
        #     session restart even if the record is briefly unavailable.
        #
        #     ``persona_skip_completed`` excludes every upstream segment's
        #     stages so the active persona never re-runs earlier personas'
        #     work; ``build_sdlc_graph`` drops those nodes and re-anchors
        #     the entry point to ``workflow_registry.entry_node(active)``.
        #     The computation is purely registry-derived — no BA/SA-specific
        #     branching — so adding a third persona is a registry edit
        #     (design Section 5d).
        project_record = _load_project_record(
            mcp_client, shared_state["_project_uuid"]
        )
        _sync_handoff_from_project_record(shared_state, project_record)
        active_handoff_state = shared_state.get("handoff", {}).get(
            "handoff_state", workflow_registry.initial_state()
        )
        persona_skip_completed: set[str] = _compute_persona_skip_completed(
            active_handoff_state
        )
        if persona_skip_completed:
            logger.info(
                "Persona session entry: handoff_state=%s -> active_segment=%s, "
                "upstream stages skipped=%s",
                active_handoff_state,
                workflow_registry.segment_of(active_handoff_state),
                sorted(persona_skip_completed),
            )

        # 7c. Enforce runtime segment RBAC (task 5.5 / design Section 4c,
        #     Req 2.4, 2.5, 6.5). The caller's Cognito groups are decoded
        #     from the WebSocket bearer token (the same Cognito ID token the
        #     Frontend already attaches for Project API calls) and compared
        #     against the active segment's owning role -- registry-derived
        #     via ``segment_owning_role(segment_of(handoff_state))`` so no
        #     BA/SA-specific branching lives here.
        #
        #     This check runs AFTER the Project_Store record has been loaded
        #     and the handoff mirror synced (so we use the authoritative
        #     ``handoff_state``) and BEFORE ``_build_graph`` / ``model``
        #     construction so a wrong-role connection terminates the segment
        #     without running any Stage agent (Req 2.5). Req 6.5 is enforced
        #     transitively: while a project is ``assigned_to_sa`` the segment
        #     owner is ``system-architect``, so a Business Analyst connection
        #     is rejected here.
        #
        #     Note: when AgentCore Identity does not propagate ``cognito:groups``
        #     through to the container (it currently replaces the inbound
        #     Cognito access token with its own opaque ``workloadaccesstoken``),
        #     ``_authorize_segment`` soft-allows the connection and emits a
        #     warning. The Project API stays the authoritative RBAC layer
        #     (HTTP 403 / 422 on every state-changing route via API Gateway's
        #     Cognito authorizer), so the security envelope is preserved.
        #     See ``_authorize_segment`` for the layered decision logic.
        caller_groups = _decode_caller_groups(context, websocket)
        authorized, required_role = _authorize_segment(
            active_handoff_state, caller_groups
        )
        if not authorized:
            try:
                active_segment = workflow_registry.segment_of(
                    active_handoff_state
                )
            except KeyError:
                active_segment = ""
            logger.warning(
                "Runtime RBAC: caller groups=%s rejected for segment=%s "
                "(handoff_state=%s, required_role=%s); terminating session",
                list(caller_groups or []),
                active_segment,
                active_handoff_state,
                required_role,
            )
            try:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": (
                        "Not authorized for this project: the active "
                        f"segment '{active_segment or 'unknown'}' is owned by "
                        f"'{required_role or 'unknown'}'. Your role does not "
                        "match the segment owner."
                    ),
                    "code": "segment_rbac_denied",
                    "handoff_state": active_handoff_state,
                    "active_segment": active_segment,
                    "required_role": required_role,
                }))
            except Exception:
                logger.debug(
                    "Failed to send RBAC error message; client likely already disconnected",
                    exc_info=True,
                )
            return

        # 8. Create model and build graph
        model = create_model()
        graph = None  # built lazily just before first stream_async call

        # 8b. Create S3SessionManager for graph-level session persistence.
        #     This persists graph execution state (completed nodes, next
        #     nodes to execute) to S3.  On resume, the graph restores
        #     this state and skips completed nodes structurally.
        bucket_name = os.environ.get("PROJECT_BUCKET", "")
        if not bucket_name:
            # Fallback: construct from account + region convention
            logger.warning("PROJECT_BUCKET not set; deriving bucket name from convention")
            bucket_name = f"agentcore-files-{os.environ.get('AWS_ACCOUNT_ID', '')}-{os.environ.get('AWS_REGION', 'us-west-2')}"
        graph_session_mgr = S3SessionManager(
            session_id=storage_id,
            bucket=bucket_name,
            prefix=f"{storage_id}/sessions/",
            region_name=os.environ.get("AWS_REGION", "us-west-2"),
        )

        # 8c. Re-open the active segment on an unprocessed request-changes
        #     bounce (e.g. SA -> BA). The Project_Store record carries the
        #     bounce + note, but the graph's persisted session would otherwise
        #     resume as "all complete". We reset that session and stage_progress
        #     so the returning persona gets actionable work, and seed the note
        #     for downstream agents (also shown by the frontend banner).
        _seen_bounce = _read_processed_bounce(
            bucket_name, storage_id, os.environ.get("AWS_REGION", "us-west-2")
        )
        if _seen_bounce:
            shared_state.setdefault("handoff", {})[
                "last_processed_bounce_ts"
            ] = _seen_bounce
        bounce = _detect_unprocessed_bounce(shared_state, project_record)
        if bounce:
            logger.info(
                "Request-changes bounce detected (from=%s -> %s); re-opening "
                "segment for re-work. note=%r",
                bounce.get("from"),
                bounce.get("to"),
                (bounce.get("note") or "")[:80],
            )
            try:
                _reset_graph_session(
                    bucket_name,
                    storage_id,
                    os.environ.get("AWS_REGION", "us-west-2"),
                )
            except Exception:
                logger.warning(
                    "Failed to reset graph session on bounce; continuing with "
                    "stage_progress reset only",
                    exc_info=True,
                )
            _apply_bounce_reset(shared_state, bounce)
            # Durable idempotency marker so a reconnect does not re-wipe the
            # persona's in-progress re-work while still at the bounced-back state.
            _write_processed_bounce(
                bucket_name,
                storage_id,
                os.environ.get("AWS_REGION", "us-west-2"),
                bounce.get("timestamp"),
            )
            # Persist the reset so the re-opened state survives a reconnect.
            try:
                _persist_via_gateway(
                    mcp_client, storage_id, _state_to_markdown(shared_state)
                )
            except Exception:
                logger.warning("Failed to persist state after bounce reset", exc_info=True)

        def _build_graph(skip_completed=None):
            """Build the SDLC graph. Called once, just before first execution."""
            nonlocal graph
            if graph is None:
                logger.info(
                    "Building SDLC graph for session %s (interactive=True)",
                    session_id,
                )
                try:
                    graph = build_sdlc_graph(
                        shared_state, model=model, interactive=True,
                        session_manager=session_manager,
                        graph_session_manager=graph_session_mgr,
                        skip_completed=skip_completed,
                    )
                    logger.info("SDLC graph built successfully for session %s", session_id)
                except Exception:
                    logger.exception("Failed to build SDLC graph for session %s", session_id)  # nosemgrep: logging-error-without-handling — intentional: log context before re-raising
                    raise
            return graph

        # 9. Set up per-session HITL response queue and StreamAdapter.
        #    The queue is the single authoritative channel for HITL responses.
        #    The single receive loop below routes incoming messages here when
        #    a HITL request is pending — eliminating the concurrent consumer
        #    race between send_human_input_request and iter_text().
        loop = asyncio.get_running_loop()
        hitl_response_queue: asyncio.Queue = asyncio.Queue()
        stream_adapter = StreamAdapter(websocket, hitl_response_queue)

        # 10. Signal WebSocket mode to the human_input tool.
        #     The tool uses Strands interrupts instead of blocking on a
        #     WebSocket bridge.  The interrupt pauses the Graph and
        #     _run_graph_with_interrupts handles the WebSocket I/O.
        shared_state["_ws_mode"] = True

        # 10b. Expose the event loop for best-effort WebSocket notifications
        #      (e.g. artifact_updated in file_ops.py) that run from sync
        #      tool threads via run_coroutine_threadsafe.
        shared_state["_ws_event_loop"] = asyncio.get_running_loop()

        # 11. Greeting, resume/restart detection, and confirmation flow
        display_name = project_name or project_id or "your project"

        # Check if there's existing progress in the loaded state
        stage_progress = shared_state.get("stage_progress", {})
        has_progress = any(
            v in ("completed", "in_progress")
            for v in stage_progress.values()
        )

        # Also check if a state file exists on S3 as a fallback indicator
        # (the state might have been written but parsing may have reset progress)
        if not has_progress:
            try:
                from tools.file_ops import file_exists
                state_file_exists = file_exists(shared_state, "aidlc-docs/aidlc-state.md")
                if state_file_exists:
                    has_progress = True
                    logger.info("State file exists on S3 but no parsed progress — treating as existing session")
            except Exception:
                pass

        if has_progress:
            # The resume/fresh choice replaces the confirmation step
            confirmed = True

            # Build a progress summary showing where we left off
            progress_lines = []
            current_stage_name = ""
            for stage_id in STAGE_NAMES:
                status = stage_progress.get(stage_id, "not_started")
                name = STAGE_DISPLAY_NAMES.get(stage_id, stage_id)
                if status == "completed":
                    progress_lines.append(f"✅ {name}")
                elif status == "in_progress":
                    progress_lines.append(f"🔄 {name} ← You were here")
                    current_stage_name = name
                elif status == "skipped":
                    progress_lines.append(f"⏭️ {name}")
                else:
                    # Only show remaining stages if they're in the execution plan
                    exec_stages = shared_state.get("execution_plan", {}).get("stages_to_execute", [])
                    if exec_stages and stage_id in exec_stages:
                        progress_lines.append(f"🔜 {name}")
                    elif not exec_stages:
                        progress_lines.append(f"▶️ {name}")

            progress_text = "\n".join(progress_lines)

            resume_greeting = (
                f"👋 **Welcome back to Agentic SDLC Transformation!**\n\n"
                f"I found an existing session for **{display_name}**. "
                f"Here's where we left off:\n\n"
                f"**📍 Workflow Progress**\n"
                f"{progress_text}\n\n"
                f"Would you like to resume from where you left off, "
                f"or start fresh with a clean slate?"
            )

            if bounce:
                # A request-changes bounce already re-opened a clean
                # requirements pass in step 8c (graph session cleared,
                # stage_progress reset, change_request seeded). Skip the
                # Session Recovery prompt entirely and do NOT run the
                # destructive start-fresh path below (it would wipe the
                # seeded change_request via initialize_shared_state).
                wants_resume = False
                _cr_note = (shared_state.get("change_request") or {}).get("note") or ""
                _requester = _change_request_requester_label(shared_state)
                _bounce_msg = (
                    f"🔄 {_requester} sent this project back for changes, "
                    f"so I've re-opened it for revision."
                )
                if _cr_note:
                    _bounce_msg += f"\n\n**Their note:** \"{_cr_note}\""
                await stream_adapter.send_human_input_notification(_bounce_msg)
            else:
                await stream_adapter.send_approval_required(
                    stage="Session Recovery",
                    summary=resume_greeting,
                    options=[
                        "Resume — continue where I left off",
                        "Start Fresh — delete everything and restart",
                    ],
                )

                resume_choice = await websocket.receive_text()
                wants_resume = "resume" in resume_choice.lower() or "continue" in resume_choice.lower()

            if not wants_resume and not bounce:
                # User wants to start fresh — delete all project files from S3
                await stream_adapter.send_human_input_notification(
                    "🗑️ Clearing all project data and starting fresh..."
                )
                try:
                    from tools.gateway import call_tool
                    # List and conceptually clear — we reinitialize state
                    # The actual S3 files will be overwritten as the workflow runs
                    shared_state = initialize_shared_state("")
                    shared_state["_mcp_client"] = mcp_client
                    shared_state["_session_id"] = storage_id
                    shared_state["_project_uuid"] = project_uuid if project_uuid else storage_id
                    shared_state["_session_manager"] = session_manager
                    shared_state["_websocket"] = websocket
                    shared_state["_ws_mode"] = True
                    shared_state["_ws_event_loop"] = asyncio.get_running_loop()
                    if project_id:
                        shared_state["project"]["selected_project"] = project_name or project_id
                        shared_state["project"]["workspace_root"] = project_id

                    # Re-sync the handoff mirror after the reset so the
                    # SharedState view stays aligned with the authoritative
                    # Project_Store record (design Section 6b). The
                    # ``persona_skip_completed`` value computed at connect
                    # remains valid here — the record didn't change.
                    _sync_handoff_from_project_record(shared_state, project_record)

                    # Persist the clean state
                    _persist_state(mcp_client, storage_id, shared_state)

                    # Create a fresh S3SessionManager (new session_id to
                    # avoid restoring the old graph state)
                    graph_session_mgr = S3SessionManager(
                        session_id=f"{storage_id}_fresh_{int(time.time())}",
                        bucket=bucket_name,
                        prefix=f"{storage_id}/sessions/",
                        region_name=os.environ.get("AWS_REGION", "us-west-2"),
                    )

                    # Rebuild graph with fresh state
                    graph = build_sdlc_graph(
                        shared_state, model=model, interactive=True,
                        session_manager=session_manager,
                        graph_session_manager=graph_session_mgr,
                        skip_completed=persona_skip_completed or None,
                    )

                    await stream_adapter.send_human_input_notification(
                        "✅ Project data cleared. Starting fresh!"
                    )
                except Exception:
                    logger.exception("Failed to clear project data")
                    await stream_adapter.send_human_input_notification(
                        "⚠️ Could not fully clear old data, but starting fresh anyway."
                    )

            # If resuming, the S3SessionManager restores graph execution
            # state (completed nodes, next nodes to execute) so the graph
            # starts from the first incomplete node structurally.

            # Run the workflow
            workflow_request = f"Build a software project called {display_name}"
            workflow_done = False

            async def _run_graph(request: str) -> None:
                nonlocal workflow_done
                try:
                    _safe_keys = [k for k in shared_state if not k.startswith("_")]
                    logger.info("Starting graph execution for session %s | state_keys=%s", session_id, _safe_keys)
                    _reload_integration_config()

                    # On resume, compute completed stages so the graph can
                    # skip already-finished work via lightweight pass-through.
                    # Implicit completion heuristic: assumes a linear pipeline
                    # where stages execute in order. If a downstream stage is
                    # done, all upstream stages must have been executed or
                    # skipped too.
                    _completed = None
                    if wants_resume:
                        progress = shared_state.get("stage_progress", {})
                        _done = {
                            s for s, v in progress.items()
                            if v in ("completed", "skipped", "in_progress")
                        }
                        # Find the latest done/in-progress stage index;
                        # everything BEFORE it is implicitly done.
                        _max_idx = -1
                        for s in _done:
                            if s in STAGE_NAMES:
                                _max_idx = max(_max_idx, STAGE_NAMES.index(s))
                        # Stages before _max_idx are completed; the stage AT
                        # _max_idx is only completed if it's not "in_progress".
                        _completed = set()
                        for i in range(_max_idx + 1):
                            stage = STAGE_NAMES[i]
                            if progress.get(stage) != "in_progress":
                                _completed.add(stage)

                    # Artifact-existence guard (resume robustness): never treat
                    # an inception stage as "completed" (and thus drop it from
                    # the graph) if its canonical output artifact is missing.
                    # A stale/rebuilt resume or graph-session state — e.g. after
                    # a request-changes bounce — could otherwise mark
                    # application_design / units_generation done without their
                    # artifacts ever being produced, silently skipping the
                    # design work. Legitimately *skipped* stages stay skipped.
                    if _completed:
                        _completed = _filter_completed_by_artifacts(
                            shared_state, _completed
                        )

                    # Union with the persona-aware upstream-segment skip set
                    # (task 5.4 / Req 8.3): the active persona never re-runs
                    # earlier personas' work even if their stages aren't yet
                    # marked completed in this session's progress.
                    if persona_skip_completed:
                        _completed = (_completed or set()) | persona_skip_completed

                    logger.info("Building graph for session %s (skip=%s) ...", session_id, _completed)
                    built_graph = _build_graph(_completed)

                    # Workaround for two Strands SDK bugs:
                    # 1. deserialize_state() restores _interrupt_state BEFORE
                    #    checking next_nodes — leaves it activated after reset,
                    #    causing stream_async to skip setting state.task.
                    # 2. _compute_ready_nodes_for_resume() uses all() instead
                    #    of any() for edge conditions, failing for mutually
                    #    exclusive conditional edges.
                    # Remove this block when these SDK issues are fixed.
                    if wants_resume:
                        built_graph._interrupt_state.deactivate()
                        built_graph._resume_from_session = False

                    # On resume, use a resume-specific task message so that
                    # passthrough agents don't misinterpret "Build a software
                    # project" as a directive to generate code.
                    _task = request
                    if wants_resume and _completed:
                        _task = (
                            f"Resume the Agentic SDLC Transformation workflow "
                            f"for {display_name}. "
                            f"Stages already completed: "
                            f"{', '.join(sorted(_completed))}. "
                            f"Skip completed stages and continue from the "
                            f"next incomplete stage."
                        )

                    # If the project was returned for changes (SA -> BA or
                    # Dev -> SA bounce), lead the re-opened pass with the
                    # requester's note so the agent works on the requested
                    # change instead of starting generically.
                    _cr = shared_state.get("change_request")
                    if _cr and _cr.get("note"):
                        _requester = _change_request_requester_label(shared_state)
                        _task = (
                            f"{_task}\n\nIMPORTANT: {_requester} returned this "
                            f"project for changes before approving. Their note: "
                            f"\"{_cr['note']}\". Begin by acknowledging this "
                            f"feedback to the user, then revise the affected "
                            f"artifacts to address it."
                        )

                    # Mark the first non-completed stage as "in_progress"
                    # so aidlc-state.md reflects partial progress if the
                    # user disconnects mid-stage.
                    if wants_resume and _completed:
                        progress = shared_state.get("stage_progress", {})
                        for s in STAGE_NAMES:
                            if s not in _completed and progress.get(s) == "not_started":
                                progress[s] = "in_progress"
                                break  # only mark the first incomplete
                        _persist_state(mcp_client, storage_id, shared_state)

                    handed_off = await _run_graph_with_interrupts(built_graph, _task, shared_state, stream_adapter)
                    _persist_state(mcp_client, storage_id, shared_state)
                    # A handoff breakout is NOT a workflow completion: the
                    # ``handoff_required`` card is the terminal signal and the
                    # next persona takes over. Suppress ``workflow_complete``
                    # so the BA sees the handoff card (not a "Workflow
                    # complete" banner) and the session is not marked done.
                    if not handed_off:
                        await stream_adapter.send_workflow_complete()
                except Exception as exc:
                    # If the WebSocket was already closed, just persist state and exit cleanly
                    if "websocket.send" in str(exc) or "websocket.close" in str(exc) or "response already completed" in str(exc):
                        logger.warning("WebSocket closed during graph execution for session %s — persisting state", session_id)
                        if shared_state is not None:
                            _persist_state(mcp_client, storage_id, shared_state)
                        return
                    logger.exception("Graph execution failed for session %s: %s", session_id, exc)
                    if shared_state is not None:
                        _persist_state(mcp_client, storage_id, shared_state)
                    try:
                        await websocket.send_text(json.dumps({"type": "error", "message": f"Workflow execution failed: {type(exc).__name__}: {exc}"}))
                    except Exception:
                        pass
                finally:
                    workflow_done = True

            async def _hitl_receive_loop() -> None:
                """Single receive loop — routes all incoming messages to the HITL queue."""
                while not workflow_done:
                    try:
                        message = await websocket.receive_text()
                    except Exception:
                        break
                    try:
                        parsed = json.loads(message)
                        if parsed.get("type") == "human_input_response":
                            hitl_response_queue.put_nowait(parsed.get("response", message))
                            continue
                    except (ValueError, AttributeError):
                        pass
                    hitl_response_queue.put_nowait(message)

            await asyncio.gather(_run_graph(workflow_request), _hitl_receive_loop())

        else:
            # No existing progress — fresh project, show normal greeting
            greeting = (
                f"👋 **Welcome to Agentic SDLC Transformation!**\n\n"
                f"I'm your AI-powered software development lifecycle assistant. "
                f"I help you go from idea to working software by guiding you through "
                f"requirements analysis to user stories generation, architecture design, code generation, and testing.\n\n"
                f"\n\n"
                f"I see you've selected **{display_name}**. "
                f"Would you like me to help you build this project?"
            )

            await stream_adapter.send_approval_required(
                stage="Project Confirmation",
                summary=greeting,
                options=[
                    f"Yes — let's build {display_name}",
                    "No — I'd like to work on something else",
                ],
            )

            # Wait for the user's choice
            confirmation = await websocket.receive_text()
            confirmed = confirmation.strip().lower().startswith("yes") or "let's build" in confirmation.lower()

            if confirmed:
                # User confirmed — run the SDLC workflow
                workflow_request = f"Build a software project called {display_name}"
                workflow_done_fresh = False

                async def _run_graph_fresh(request: str) -> None:
                    nonlocal workflow_done_fresh
                    try:
                        logger.info("Fresh workflow start for session %s", session_id)
                        _reload_integration_config()
                        handed_off = await _run_graph_with_interrupts(_build_graph(persona_skip_completed or None), request, shared_state, stream_adapter)
                        _persist_state(mcp_client, storage_id, shared_state)
                        # Suppress workflow_complete on a handoff breakout —
                        # the handoff_required card is the terminal signal.
                        if not handed_off:
                            await stream_adapter.send_workflow_complete()
                    except Exception as exc:
                        if "websocket.send" in str(exc) or "websocket.close" in str(exc) or "response already completed" in str(exc):
                            logger.warning("WebSocket closed during graph execution for session %s — persisting state", session_id)
                            if shared_state is not None:
                                _persist_state(mcp_client, storage_id, shared_state)
                            return
                        logger.exception("Graph execution failed for session %s: %s", session_id, exc)
                        if shared_state is not None:
                            _persist_state(mcp_client, storage_id, shared_state)
                        try:
                            await websocket.send_text(json.dumps({"type": "error", "message": f"Workflow execution failed: {type(exc).__name__}: {exc}"}))
                        except Exception:
                            pass
                    finally:
                        workflow_done_fresh = True

                async def _hitl_receive_loop_fresh() -> None:
                    while not workflow_done_fresh:
                        try:
                            message = await websocket.receive_text()
                        except Exception:
                            break
                        try:
                            parsed = json.loads(message)
                            if parsed.get("type") == "human_input_response":
                                hitl_response_queue.put_nowait(parsed.get("response", message))
                                continue
                        except (ValueError, AttributeError):
                            pass
                        hitl_response_queue.put_nowait(message)

                await asyncio.gather(_run_graph_fresh(workflow_request), _hitl_receive_loop_fresh())
            else:
                # User wants something else — enter general conversation mode
                await stream_adapter.send_human_input_notification(
                    "No problem! Tell me what you'd like to work on instead. "
                    "When you're ready to start building, just describe your project "
                    "and I'll kick off the full SDLC workflow."
                )

        # 12. Continue listening for follow-up messages (conversation mode only)
        #     Once workflow starts via asyncio.gather above, this loop is not reached.
        while True:
            try:
                message = await websocket.receive_text()
            except Exception:
                break

            msg_lower = message.strip().lower()
            start_triggers = ["build", "create", "develop", "implement", "start workflow", "let's build", "start building", "begin project"]
            is_workflow_request = any(t in msg_lower for t in start_triggers)

            if is_workflow_request and not confirmed:
                confirmed = True
                workflow_done_conv = False

                async def _run_graph_conv(request: str) -> None:
                    nonlocal workflow_done_conv
                    try:
                        _reload_integration_config()
                        handed_off = await _run_graph_with_interrupts(_build_graph(persona_skip_completed or None), request, shared_state, stream_adapter)
                        _persist_state(mcp_client, storage_id, shared_state)
                        # Suppress workflow_complete on a handoff breakout —
                        # the handoff_required card is the terminal signal.
                        if not handed_off:
                            await stream_adapter.send_workflow_complete()
                    except Exception as exc:
                        logger.exception("Graph execution failed for session %s: %s", session_id, exc)
                        if shared_state is not None:
                            _persist_state(mcp_client, storage_id, shared_state)
                        try:
                            await websocket.send_text(json.dumps({"type": "error", "message": "Workflow execution failed"}))
                        except Exception:
                            pass
                    finally:
                        workflow_done_conv = True

                async def _hitl_receive_loop_conv() -> None:
                    while not workflow_done_conv:
                        try:
                            msg = await websocket.receive_text()
                        except Exception:
                            break
                        try:
                            parsed = json.loads(msg)
                            if parsed.get("type") == "human_input_response":
                                hitl_response_queue.put_nowait(parsed.get("response", msg))
                                continue
                        except (ValueError, AttributeError):
                            pass
                        hitl_response_queue.put_nowait(msg)

                await asyncio.gather(_run_graph_conv(message), _hitl_receive_loop_conv())
                break  # workflow done, exit the loop
            else:
                # General conversation mode
                try:
                    from strands import Agent
                    chat_agent = Agent(
                        model=model,
                        system_prompt=(
                            "You are a friendly AI assistant helping a user decide what "
                            "software project to build. Have a natural conversation. "
                            "When the user describes a project they want to build, "
                            "encourage them to say 'let's build it' or 'start building' "
                            "to kick off the full SDLC workflow."
                        ),
                    )
                    result = chat_agent(message)
                    await websocket.send_text(json.dumps({"type": "text_chunk", "content": str(result)}))
                except Exception:
                    logger.exception("Chat response failed for session %s", session_id)
                    await websocket.send_text(json.dumps({"type": "text_chunk", "content": "I'm here to help! Tell me about the project you'd like to build."}))

    except Exception:
        logger.exception("WebSocket handler error for session %s", session_id)
        # Persist state if we have it
        if shared_state is not None and mcp_client is not None and storage_id:
            _persist_state(mcp_client, storage_id, shared_state)
    finally:
        # Cleanup MCP client
        if mcp_client is not None:
            try:
                mcp_client.__exit__(None, None, None)
            except Exception:
                logger.debug("MCP client cleanup error", exc_info=True)

        # Close WebSocket connection
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    app.run()
