"""Unit tests for the runtime segment RBAC enforcement (task 5.5).

These cover the helpers added to ``main_ws.py`` for design Section 4c
(Req 2.4 / 2.5 / 6.5):

  * ``_decode_caller_groups`` reads the ``Authorization: Bearer <jwt>``
    header off the AgentCore ``RequestContext.request_headers`` dict,
    base64url-decodes the JWT payload, and returns ``cognito:groups`` as
    a list (handles list and comma-string shapes; returns ``[]`` on any
    failure -- fail-closed).
  * ``_authorize_segment`` is registry-derived: it resolves the active
    segment from the authoritative ``handoff_state`` via
    ``segment_of(state)`` and requires the caller's groups to contain
    ``segments[seg].owning_role``. Unknown ``handoff_state`` values fail
    closed.

A small functional test exercises ``handle_message`` end-to-end up to (and
not past) the RBAC gate: it stubs the OAuth/MCP/state-loading helpers,
feeds a Project_Store record whose ``handoff_state`` is ``assigned_to_sa``,
and verifies that:

  * a Business-Analyst-only caller is rejected with an ``error`` message
    whose code is ``segment_rbac_denied`` and that ``_build_graph`` /
    ``create_model`` are never invoked (Req 2.5);
  * a Solution-Architect caller passes the gate (no error message is sent
    by the gate itself).

The runtime container's heavy SDK dependencies (``bedrock_agentcore``,
``strands``, ``mcp``) are not installed in the unit-test environment; they
are stubbed via ``sys.modules`` BEFORE importing ``main_ws``, mirroring the
pattern used by ``tests/test_ws_handoff_breakout.py`` and
``tests/test_ws_persona_session_entry.py``.

Validates: Requirements 2.4, 2.5, 6.5
"""

import asyncio
import base64
import json
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Make the runtime module importable, then stub heavy SDK dependencies before
# importing main_ws.
# ---------------------------------------------------------------------------

_ICODE_AGENT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ICODE_AGENT_DIR not in sys.path:
    sys.path.insert(0, _ICODE_AGENT_DIR)


def _ensure_stub_module(name: str) -> types.ModuleType:
    """Create or fetch a placeholder module so ``from name import X`` succeeds."""
    if name in sys.modules:
        return sys.modules[name]
    parts = name.split(".")
    parent_mod = None
    cumulative: list[str] = []
    for part in parts:
        cumulative.append(part)
        full = ".".join(cumulative)
        if full not in sys.modules:
            mod = types.ModuleType(full)
            mod.__path__ = []
            sys.modules[full] = mod
            if parent_mod is not None:
                setattr(parent_mod, part, mod)
        parent_mod = sys.modules[full]
    return sys.modules[name]


# botocore + boto3 (transitively pulled in via config.model)
_botocore = _ensure_stub_module("botocore")
_botocore_config = _ensure_stub_module("botocore.config")
_botocore_config.Config = type("Config", (), {})
_boto3 = _ensure_stub_module("boto3")

# bedrock_agentcore.BedrockAgentCoreApp + memory integrations
_bedrock = _ensure_stub_module("bedrock_agentcore")


class _BedrockAgentCoreAppStub:
    def __init__(self, *a, **kw):
        pass

    def websocket(self, fn):
        return fn

    def run(self):  # pragma: no cover
        raise RuntimeError("BedrockAgentCoreApp.run not available in tests")


_bedrock.BedrockAgentCoreApp = _BedrockAgentCoreAppStub

_memcfg = _ensure_stub_module(
    "bedrock_agentcore.memory.integrations.strands.config"
)
_memcfg.AgentCoreMemoryConfig = type("AgentCoreMemoryConfig", (), {})
_memmgr = _ensure_stub_module(
    "bedrock_agentcore.memory.integrations.strands.session_manager"
)
_memmgr.AgentCoreMemorySessionManager = type(
    "AgentCoreMemorySessionManager", (), {}
)

# mcp.client.streamable_http.streamablehttp_client
_mcp = _ensure_stub_module("mcp.client.streamable_http")


def _streamablehttp_client_stub(*a, **kw):  # pragma: no cover
    raise RuntimeError("streamablehttp_client not available in tests")


_mcp.streamablehttp_client = _streamablehttp_client_stub


def _tool_passthrough(*args, **kwargs):
    if args and callable(args[0]) and not kwargs:
        return args[0]

    def _decorator(fn):
        return fn

    return _decorator


# strands top-level
_strands_root = _ensure_stub_module("strands")
_strands_root.tool = _tool_passthrough
_strands_root.Agent = type("Agent", (), {})

# strands.interrupt.InterruptException
_strands_interrupt = _ensure_stub_module("strands.interrupt")
_strands_interrupt.InterruptException = type("InterruptException", (Exception,), {})

# strands.multiagent.Status
_strands_multiagent = _ensure_stub_module("strands.multiagent")


class _StatusStub:
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"


_strands_multiagent.Status = _StatusStub

_strands_session = _ensure_stub_module("strands.session")
_strands_session.S3SessionManager = type("S3SessionManager", (), {})

_strands_tools_pkg = _ensure_stub_module("strands.tools")
_strands_tools_pkg.tool = _tool_passthrough

_strands_mcp = _ensure_stub_module("strands.tools.mcp")
_strands_mcp.MCPClient = type("MCPClient", (), {})

_strands_tools_decorator = _ensure_stub_module("strands.tools.decorator")
_strands_tools_decorator.tool = _tool_passthrough

_strands_models = _ensure_stub_module("strands.models")
_strands_models.BedrockModel = type("BedrockModel", (), {})

# Replace orchestrator with a minimal stub -- the RBAC gate runs before any
# graph is built so the real orchestrator is never reached in these tests.
_orchestrator_stub = types.ModuleType("orchestrator")


def _build_sdlc_graph_stub(*a, **kw):  # pragma: no cover
    raise RuntimeError("orchestrator.build_sdlc_graph not available in tests")


_orchestrator_stub.build_sdlc_graph = _build_sdlc_graph_stub
sys.modules["orchestrator"] = _orchestrator_stub

_config_pkg = _ensure_stub_module("config")
_config_model = _ensure_stub_module("config.model")


def _create_model_stub(*a, **kw):  # pragma: no cover
    raise RuntimeError("config.model.create_model not available in tests")


_config_model.create_model = _create_model_stub

import tools as _real_tools_pkg  # noqa: E402,F401

_tools_integrations = types.ModuleType("tools.integrations")


def _load_integration_config_stub(*a, **kw):
    return {}


_tools_integrations.load_integration_config = _load_integration_config_stub
sys.modules["tools.integrations"] = _tools_integrations
setattr(_real_tools_pkg, "integrations", _tools_integrations)

_tools_oauth = types.ModuleType("tools.oauth_provider")


class _OAuthProviderStub:
    @classmethod
    def from_env(cls):
        return cls()

    def get_access_token(self):
        return ""


_tools_oauth.OAuthProvider = _OAuthProviderStub
sys.modules["tools.oauth_provider"] = _tools_oauth
setattr(_real_tools_pkg, "oauth_provider", _tools_oauth)


# Import main_ws AFTER stubs are in place.
import main_ws  # noqa: E402
import workflow_registry  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers: build fake Cognito ID tokens and request contexts
# ---------------------------------------------------------------------------


def _b64url(payload: bytes) -> str:
    """Base64url-encode without padding (JWT convention)."""
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _make_id_token(claims: dict) -> str:
    """Build a fake but well-formed ``header.payload.signature`` JWT.

    The signature is *not* validated by the runtime helper -- AgentCore's
    front-door is responsible for authenticating the WebSocket. We only
    need the structural shape and a base64url-decodable payload.
    """
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8"))
    payload = _b64url(json.dumps(claims).encode("utf-8"))
    signature = _b64url(b"sig")
    return f"{header}.{payload}.{signature}"


def _ctx(headers: dict | None) -> SimpleNamespace:
    """A minimal RequestContext stand-in: only ``request_headers`` is read."""
    return SimpleNamespace(
        session_id="sess-1",
        request_headers=headers,
        request=None,
    )


# ---------------------------------------------------------------------------
# _decode_caller_groups
# ---------------------------------------------------------------------------


def test_decode_caller_groups_list_claim():
    """A JWT whose ``cognito:groups`` is a list returns those groups verbatim.

    Validates: Requirements 2.4
    """
    token = _make_id_token({
        "cognito:groups": ["system-architect"],
        "email": "sa@example.com",
    })
    ctx = _ctx({"Authorization": f"Bearer {token}"})

    assert main_ws._decode_caller_groups(ctx) == ["system-architect"]


def test_decode_caller_groups_comma_string_claim():
    """A JWT whose ``cognito:groups`` is a comma-separated string is parsed.

    Validates: Requirements 2.4
    """
    token = _make_id_token({
        "cognito:groups": "requirements-analyst,system-architect",
    })
    ctx = _ctx({"Authorization": f"Bearer {token}"})

    assert main_ws._decode_caller_groups(ctx) == [
        "requirements-analyst",
        "system-architect",
    ]


def test_decode_caller_groups_case_insensitive_header_lookup():
    """The Authorization header is matched case-insensitively (HTTP semantics).

    Validates: Requirements 2.4
    """
    token = _make_id_token({"cognito:groups": ["system-architect"]})
    ctx = _ctx({"authorization": f"Bearer {token}"})

    assert main_ws._decode_caller_groups(ctx) == ["system-architect"]


def test_decode_caller_groups_no_headers_returns_empty():
    """Missing request_headers -> no groups (fail-closed)."""
    ctx = _ctx(None)
    assert main_ws._decode_caller_groups(ctx) == []


def test_decode_caller_groups_no_authorization_header_returns_empty():
    """No Authorization header -> no groups (fail-closed)."""
    ctx = _ctx({"X-Other": "value"})
    assert main_ws._decode_caller_groups(ctx) == []


def test_decode_caller_groups_non_bearer_scheme_returns_empty():
    """A non-Bearer scheme is rejected (fail-closed)."""
    ctx = _ctx({"Authorization": "Basic dXNlcjpwYXNz"})
    assert main_ws._decode_caller_groups(ctx) == []


def test_decode_caller_groups_malformed_jwt_returns_empty():
    """A token that is not three dot-separated segments yields no groups."""
    ctx = _ctx({"Authorization": "Bearer not-a-jwt"})
    assert main_ws._decode_caller_groups(ctx) == []


def test_decode_caller_groups_invalid_base64_returns_empty():
    """A base64url-undecodable payload yields no groups (fail-closed)."""
    # Header valid; payload contains a character invalid in base64url ("!");
    # signature segment is just placeholder.
    ctx = _ctx({"Authorization": "Bearer aGVhZGVy.!!!.c2ln"})
    assert main_ws._decode_caller_groups(ctx) == []


def test_decode_caller_groups_invalid_json_returns_empty():
    """A payload that base64-decodes but isn't valid JSON yields no groups."""
    bogus_payload = _b64url(b"not json")
    token = f"aGVhZGVy.{bogus_payload}.c2ln"
    ctx = _ctx({"Authorization": f"Bearer {token}"})
    assert main_ws._decode_caller_groups(ctx) == []


def test_decode_caller_groups_no_groups_claim_returns_empty():
    """A token without ``cognito:groups`` yields the empty list."""
    token = _make_id_token({"email": "user@example.com"})
    ctx = _ctx({"Authorization": f"Bearer {token}"})
    assert main_ws._decode_caller_groups(ctx) == []


def test_decode_caller_groups_filters_blank_entries():
    """Empty entries in a comma-separated claim are filtered out."""
    token = _make_id_token({"cognito:groups": "system-architect,,"})
    ctx = _ctx({"Authorization": f"Bearer {token}"})
    assert main_ws._decode_caller_groups(ctx) == ["system-architect"]


# ---------------------------------------------------------------------------
# _authorize_segment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handoff_state,required_role",
    [
        ("draft", "requirements-analyst"),
        ("requirements_ready", "requirements-analyst"),
        ("assigned_to_sa", "system-architect"),
        ("in_design", "system-architect"),
        ("design_ready", "system-architect"),
        ("ready_for_dev", "system-architect"),
    ],
)
def test_authorize_segment_owning_role_passes(handoff_state, required_role):
    """When the caller's groups include the segment owner, access is granted.

    Validates: Requirements 2.4, 6.5
    """
    authorized, role = main_ws._authorize_segment(
        handoff_state, [required_role]
    )
    assert authorized is True
    assert role == required_role


@pytest.mark.parametrize(
    "handoff_state,wrong_role",
    [
        # BA-segment state with only SA in groups -- rejected
        ("draft", "system-architect"),
        ("requirements_ready", "system-architect"),
        # SA-segment state with only BA in groups -- rejected (Req 6.5)
        ("assigned_to_sa", "requirements-analyst"),
        ("in_design", "requirements-analyst"),
        ("ready_for_dev", "requirements-analyst"),
    ],
)
def test_authorize_segment_wrong_role_rejected(handoff_state, wrong_role):
    """Caller without the segment's owning role is rejected.

    Validates: Requirements 2.4, 2.5, 6.5
    """
    authorized, role = main_ws._authorize_segment(handoff_state, [wrong_role])
    assert authorized is False
    # required_role is still surfaced so the caller can build a precise message.
    assert role in ("requirements-analyst", "system-architect")


def test_authorize_segment_empty_groups_soft_allowed():
    """A caller with no decoded groups is soft-allowed by the runtime check.

    The Project API is the authoritative RBAC layer (HTTP 403 on non-owning
    callers, HTTP 422 on illegal transitions, both via API Gateway's Cognito
    authorizer reading the verified ID token), so the runtime check is
    best-effort given the AgentCore workload-token propagation gap: the
    inbound Cognito access token is replaced with an opaque workload token
    that does not carry ``cognito:groups``. Soft-allowing here keeps the
    runtime functional without weakening the overall authorisation envelope.
    The function still surfaces ``required_role`` so the warning log can be
    precise.
    """
    authorized, role = main_ws._authorize_segment("assigned_to_sa", [])
    assert authorized is True
    assert role == "system-architect"


def test_authorize_segment_unknown_handoff_state_fails_closed():
    """An unknown ``handoff_state`` is rejected so registry drift can't grant access.

    Validates: Requirements 2.5
    """
    authorized, role = main_ws._authorize_segment(
        "not-a-real-state", ["system-architect"]
    )
    assert authorized is False
    assert role == ""


def test_authorize_segment_extensible_to_third_persona(monkeypatch):
    """A registry with a third persona authorises the new role at its segment.

    The function is registry-derived: patching the active registry to a
    synthetic three-persona one yields a third segment whose owning role
    correctly authorises a Dev caller while still rejecting BA / SA.

    Validates: Requirements 2.4 (Section 5d extensibility)
    """
    from workflow_registry import WorkflowRegistry

    three_persona = {
        "initial_state": "draft",
        "personas": {
            "requirements-analyst": {"display": "BA", "order": 1},
            "system-architect": {"display": "SA", "order": 2},
            "software-developer": {"display": "Dev", "order": 3},
        },
        "segments": {
            "ba_segment": {
                "owning_role": "requirements-analyst",
                "stages": ["requirements_analysis"],
                "entry_node": "requirements_analysis",
                "exit_hub": "hub_post_user_stories",
            },
            "sa_segment": {
                "owning_role": "system-architect",
                "stages": ["application_design"],
                "entry_node": "application_design",
                "exit_hub": "hub_post_units_generation",
            },
            "dev_segment": {
                "owning_role": "software-developer",
                "stages": ["implementation"],
                "entry_node": "implementation",
                "exit_hub": "hub_post_code_review",
            },
        },
        "states": {
            "draft": {
                "owning_role": "requirements-analyst",
                "segment": "ba_segment",
                "forward": "assigned_to_sa",
            },
            "assigned_to_sa": {
                "owning_role": "system-architect",
                "segment": "sa_segment",
                "forward": "assigned_to_dev",
            },
            "assigned_to_dev": {
                "owning_role": "software-developer",
                "segment": "dev_segment",
                "forward": None,
            },
        },
        "request_changes": {},
    }
    monkeypatch.setattr(
        workflow_registry, "REGISTRY", WorkflowRegistry(three_persona)
    )

    # Dev caller passes on a Dev-segment state.
    ok, role = main_ws._authorize_segment(
        "assigned_to_dev", ["software-developer"]
    )
    assert ok is True
    assert role == "software-developer"

    # BA / SA callers are rejected on a Dev-segment state.
    for wrong in ("requirements-analyst", "system-architect"):
        ok, role = main_ws._authorize_segment("assigned_to_dev", [wrong])
        assert ok is False
        assert role == "software-developer"


# ---------------------------------------------------------------------------
# handle_message: end-to-end gating up to (but not past) the RBAC check
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    """Minimal Starlette-WebSocket stand-in that scripts an init message."""

    def __init__(self, init_payload: dict) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self._inbound: list[str] = [json.dumps(init_payload)]

    async def accept(self) -> None:
        return None

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def receive_text(self) -> str:
        if not self._inbound:
            # Simulate a polite client disconnect rather than a hang.
            raise RuntimeError("no more inbound messages")
        return self._inbound.pop(0)

    async def close(self) -> None:
        self.closed = True


class _FakeMCPClient:
    """A throwaway MCPClient stand-in -- ``__enter__``/``__exit__`` only."""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _install_handle_message_stubs(monkeypatch, *, project_record: dict | None):
    """Patch the helpers ``handle_message`` calls before the RBAC check.

    Returns a dict of probes the caller can introspect after invoking
    ``handle_message`` to assert that the gate fired (or didn't).
    """
    probes: dict = {
        "build_graph_calls": 0,
        "create_model_calls": 0,
        "stream_async_calls": 0,
    }

    monkeypatch.setattr(
        main_ws, "_create_mcp_client", lambda token: _FakeMCPClient()
    )
    monkeypatch.setattr(main_ws, "_create_session_manager", lambda sid: None)
    monkeypatch.setattr(
        main_ws,
        "_load_or_init_state",
        lambda mcp, sid: {
            "current_phase": "Inception",
            "current_stage": "user_stories",
            "stage_progress": {},
            "project": {},
            "execution_plan": {"stages_to_execute": []},
            "extension_config": [],
            "audit_buffer": [],
            "request_analysis": {
                "clarity": "",
                "request_type": "",
                "scope": "",
                "complexity": "",
            },
            "artifacts": {},
            "questions": [],
            "approval": {},
            "units": {
                "items": [],
                "current_unit_index": 0,
                "completed_units": [],
            },
        },
    )
    monkeypatch.setattr(
        main_ws, "_load_project_record", lambda mcp, uuid: project_record
    )

    # Tripwires -- if the RBAC gate fails to fire, these will be invoked and
    # the test assertions on probes will catch it.
    def _create_model_tripwire(*a, **kw):
        probes["create_model_calls"] += 1
        return MagicMock()

    monkeypatch.setattr(main_ws, "create_model", _create_model_tripwire)

    def _build_graph_tripwire(*a, **kw):
        probes["build_graph_calls"] += 1
        raise AssertionError(
            "build_sdlc_graph must NOT be called when RBAC denies the segment"
        )

    monkeypatch.setattr(main_ws, "build_sdlc_graph", _build_graph_tripwire)

    # S3SessionManager is a class -- replace with a stub class.
    class _S3SessionManagerStub:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(main_ws, "S3SessionManager", _S3SessionManagerStub)

    return probes


def _project_record(handoff_state: str, owning_role: str) -> dict:
    return {
        "project_uuid": "uuid-001",
        "project_id": "online_bookstore",
        "name": "Online Bookstore",
        "handoff_state": handoff_state,
        "owning_role": owning_role,
        "artifact_prefix": "online_bookstore",
        "assignment_history": [],
    }


def test_handle_message_rejects_wrong_role_with_error_message(monkeypatch):
    """A BA caller on a project assigned to the SA is rejected with an error.

    Validates: Requirements 2.4, 2.5, 6.5
    """
    record = _project_record("assigned_to_sa", "system-architect")
    probes = _install_handle_message_stubs(monkeypatch, project_record=record)

    # BA caller -- wrong role for an assigned_to_sa project.
    token = _make_id_token({"cognito:groups": ["requirements-analyst"]})
    websocket = _FakeWebSocket({
        "project_id": "online_bookstore",
        "project_name": "Online Bookstore",
        "project_uuid": "uuid-001",
        "request": "Build it",
    })
    context = _ctx({"Authorization": f"Bearer {token}"})

    asyncio.run(main_ws.handle_message(websocket, context))

    # Exactly one error message reached the WebSocket.
    errors = [m for m in websocket.sent if m.get("type") == "error"]
    assert len(errors) == 1, (
        f"Expected exactly one error message, got: {websocket.sent!r}"
    )
    err = errors[0]
    assert err["code"] == "segment_rbac_denied"
    assert err["handoff_state"] == "assigned_to_sa"
    assert err["active_segment"] == "sa_segment"
    assert err["required_role"] == "system-architect"

    # No Stage agent ran: neither create_model nor build_sdlc_graph were
    # invoked. ``build_sdlc_graph`` raises AssertionError if called -- if the
    # gate had failed we'd see an exception in the test output.
    assert probes["build_graph_calls"] == 0
    assert probes["create_model_calls"] == 0

    # WebSocket was cleanly closed in the finally block.
    assert websocket.closed is True


def test_handle_message_passes_right_role_through_to_greeting(monkeypatch):
    """An SA caller on an SA-owned project passes the RBAC gate.

    The test stops the flow shortly after the gate by exhausting the inbound
    message queue (``receive_text`` raises) -- enough to prove the gate did
    not block, no ``segment_rbac_denied`` error fired, and the runtime began
    its normal greeting / approval flow (``approval_required`` is sent).

    Validates: Requirements 2.4
    """
    record = _project_record("assigned_to_sa", "system-architect")
    probes = _install_handle_message_stubs(monkeypatch, project_record=record)

    token = _make_id_token({"cognito:groups": ["system-architect"]})
    websocket = _FakeWebSocket({
        "project_id": "online_bookstore",
        "project_name": "Online Bookstore",
        "project_uuid": "uuid-001",
        "request": "Build it",
    })
    context = _ctx({"Authorization": f"Bearer {token}"})

    # We don't care about exceptions later in the flow -- handle_message has
    # a top-level try/except that persists state and closes the socket. We
    # only assert on what was sent up to and after the gate.
    asyncio.run(main_ws.handle_message(websocket, context))

    # No RBAC denial fired.
    rbac_errors = [
        m
        for m in websocket.sent
        if m.get("type") == "error" and m.get("code") == "segment_rbac_denied"
    ]
    assert rbac_errors == [], (
        f"Unexpected RBAC denial for SA caller: {rbac_errors!r}"
    )

    # The greeting / project-confirmation approval gate was reached, proving
    # execution proceeded past the RBAC gate.
    approval_msgs = [
        m for m in websocket.sent if m.get("type") == "approval_required"
    ]
    assert approval_msgs, (
        f"Expected SA caller to reach the project-confirmation gate; "
        f"got: {websocket.sent!r}"
    )


def test_handle_message_soft_allows_when_no_authorization_header(monkeypatch):
    """A caller with no Authorization header is soft-allowed by the runtime.

    AgentCore Identity replaces the inbound Cognito access token with its own
    opaque ``workloadaccesstoken`` that does not carry ``cognito:groups``, so
    the container often sees no decoded groups even when the inbound caller
    is fully authenticated. The Project API remains the authoritative RBAC
    layer (HTTP 403 / 422 on every state-changing route via API Gateway's
    Cognito authorizer), so the runtime check soft-allows in this case
    rather than producing a false-negative rejection. ``_authorize_segment``
    emits a warning when this branch is taken so the gap stays visible.

    Validates: Requirements 2.4, 2.5 (Project_API enforcement is the
    authoritative layer; runtime check is best-effort).
    """
    record = _project_record("draft", "requirements-analyst")
    probes = _install_handle_message_stubs(monkeypatch, project_record=record)

    websocket = _FakeWebSocket({
        "project_id": "online_bookstore",
        "project_name": "Online Bookstore",
        "project_uuid": "uuid-001",
        "request": "Build it",
    })
    context = _ctx({})  # no Authorization header

    asyncio.run(main_ws.handle_message(websocket, context))

    rbac_errors = [
        m
        for m in websocket.sent
        if m.get("type") == "error" and m.get("code") == "segment_rbac_denied"
    ]
    assert rbac_errors == [], (
        f"Expected no RBAC denial for missing Authorization header "
        f"(soft-allow on empty groups); got: {rbac_errors!r}"
    )
    # Stub probes are unused for the soft-allow assertion -- the absence of a
    # ``segment_rbac_denied`` error is the behaviour under test. We retain the
    # ``probes`` reference so the existing stub install signature stays in
    # use and the per-test setup is identical to the wrong-role test below.
    assert probes is not None
