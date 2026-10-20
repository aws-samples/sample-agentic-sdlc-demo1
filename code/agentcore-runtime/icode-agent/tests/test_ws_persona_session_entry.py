"""Unit tests for the Project_Store sync + persona-aware session entry (task 5.4).

These cover the helpers added to ``main_ws.py`` for design Section 5c (Req
8.1-8.4 / 5.4):

  * ``_load_project_record`` reads ``_platform/projects/{uuid}.json`` via the
    Gateway, returning the parsed record on hit, ``None`` on missing/error.
  * ``_sync_handoff_from_project_record`` mirrors the authoritative
    ``handoff_state`` / ``owning_role`` from the Project_Store record into
    SharedState (Section 6b), and is a no-op when the record is unavailable
    so the existing mirror is preserved (resume durability, Req 9).
  * ``_compute_persona_skip_completed`` is registry-derived: it returns the
    union of every upstream segment's stages so the active persona never
    re-runs earlier work, and the empty set when the active segment is the
    first segment in the chain. Adding a third persona to the registry yields
    a third skip set with no code change.

The runtime container's heavy SDK dependencies (``bedrock_agentcore``,
``strands``, ``mcp``) are not installed in the unit-test environment; they
are stubbed via ``sys.modules`` BEFORE importing ``main_ws``, mirroring the
pattern used by ``tests/test_ws_handoff_breakout.py``.

Validates: Requirements 8.1, 8.2, 8.3, 8.4
"""

import json
import os
import sys
import types

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

# Replace orchestrator with a minimal stub — task 5.4 does not exercise the
# graph itself.
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


def _load_integration_config_stub(*a, **kw):  # pragma: no cover
    return {}


_tools_integrations.load_integration_config = _load_integration_config_stub
sys.modules["tools.integrations"] = _tools_integrations
setattr(_real_tools_pkg, "integrations", _tools_integrations)

_tools_oauth = types.ModuleType("tools.oauth_provider")


class _OAuthProviderStub:
    @classmethod
    def from_env(cls):  # pragma: no cover
        return cls()

    def get_access_token(self):  # pragma: no cover
        return ""


_tools_oauth.OAuthProvider = _OAuthProviderStub
sys.modules["tools.oauth_provider"] = _tools_oauth
setattr(_real_tools_pkg, "oauth_provider", _tools_oauth)


# Import main_ws AFTER stubs are in place.
import main_ws  # noqa: E402
import workflow_registry  # noqa: E402


# ---------------------------------------------------------------------------
# _load_project_record
# ---------------------------------------------------------------------------


class _StubGateway:
    """Captures call_tool calls and returns canned responses by path.

    main_ws._load_project_record uses ``tools.gateway.call_tool`` (imported
    into main_ws's module namespace). We monkeypatch that symbol so the
    helper exercises real code paths without touching the network.
    """

    def __init__(self, response_by_path: dict):
        self._response_by_path = response_by_path
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, mcp_client, tool_name, args):
        self.calls.append((tool_name, dict(args)))
        path = args.get("path", "")
        return self._response_by_path.get(
            path,
            # Default: not_found
            {"isError": True, "content": [{"text": f"not found: {path}"}]},
        )


def _ok_text(text: str) -> dict:
    """Mirror the Gateway's success envelope."""
    return {"content": [{"text": text}]}


def _record_for(uuid: str, *, handoff_state: str, owning_role: str) -> dict:
    return {
        "project_uuid": uuid,
        "project_id": "online_bookstore",
        "name": "Online Bookstore",
        "handoff_state": handoff_state,
        "owning_role": owning_role,
        "artifact_prefix": "online_bookstore",
        "assignment_history": [],
    }


def test_load_project_record_returns_parsed_record(monkeypatch):
    """A 200 response containing the JSON record is parsed and returned.

    Validates: Requirements 8.1
    """
    record = _record_for(
        "uuid-001",
        handoff_state="assigned_to_sa",
        owning_role="system-architect",
    )
    gateway = _StubGateway({
        "_platform/projects/uuid-001.json": _ok_text(json.dumps(record)),
    })
    monkeypatch.setattr(main_ws, "call_tool", gateway)

    out = main_ws._load_project_record(object(), "uuid-001")

    assert out == record
    assert gateway.calls == [
        ("read_file", {"path": "_platform/projects/uuid-001.json"})
    ]


def test_load_project_record_missing_returns_none(monkeypatch):
    """A not-found response yields ``None`` so the caller falls back to mirror.

    Validates: Requirements 8.1
    """
    gateway = _StubGateway({})  # empty -> default not_found
    monkeypatch.setattr(main_ws, "call_tool", gateway)

    assert main_ws._load_project_record(object(), "missing-uuid") is None


def test_load_project_record_malformed_json_returns_none(monkeypatch):
    """Malformed JSON yields ``None`` so the caller falls back to mirror.

    Validates: Requirements 8.1
    """
    gateway = _StubGateway({
        "_platform/projects/bad-uuid.json": _ok_text("not valid json {{{{"),
    })
    monkeypatch.setattr(main_ws, "call_tool", gateway)

    assert main_ws._load_project_record(object(), "bad-uuid") is None


def test_load_project_record_gateway_exception_returns_none(monkeypatch):
    """An exception from the Gateway client yields ``None`` (Section 6b)."""

    def _raises(*a, **kw):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(main_ws, "call_tool", _raises)

    assert main_ws._load_project_record(object(), "any-uuid") is None


def test_load_project_record_empty_uuid_returns_none(monkeypatch):
    """An empty ``project_uuid`` is treated as no record (no Gateway call)."""

    gateway = _StubGateway({})
    monkeypatch.setattr(main_ws, "call_tool", gateway)

    assert main_ws._load_project_record(object(), "") is None
    assert gateway.calls == []


# ---------------------------------------------------------------------------
# _sync_handoff_from_project_record
# ---------------------------------------------------------------------------


def _shared_state_with_default_handoff() -> dict:
    """Return a SharedState dict carrying the registry-default handoff mirror."""
    initial = workflow_registry.initial_state()
    return {
        "handoff": {
            "handoff_state": initial,
            "owning_role": workflow_registry.owning_role(initial),
            "assignment_history": [],
        }
    }


def test_sync_handoff_overwrites_authoritative_fields():
    """Mirror is overwritten with ``handoff_state`` / ``owning_role`` from record.

    Validates: Requirements 8.1
    """
    state = _shared_state_with_default_handoff()
    record = _record_for(
        "uuid-001",
        handoff_state="assigned_to_sa",
        owning_role="system-architect",
    )

    main_ws._sync_handoff_from_project_record(state, record)

    assert state["handoff"]["handoff_state"] == "assigned_to_sa"
    assert state["handoff"]["owning_role"] == "system-architect"
    # assignment_history is canonical in the Project_Store, not duplicated.
    assert state["handoff"]["assignment_history"] == []


def test_sync_handoff_none_record_preserves_mirror():
    """When the record is unavailable, the existing mirror is preserved.

    Validates: Requirements 8.1
    """
    state = _shared_state_with_default_handoff()
    state["handoff"]["handoff_state"] = "assigned_to_sa"
    state["handoff"]["owning_role"] = "system-architect"

    main_ws._sync_handoff_from_project_record(state, None)

    assert state["handoff"]["handoff_state"] == "assigned_to_sa"
    assert state["handoff"]["owning_role"] == "system-architect"


def test_sync_handoff_creates_section_if_missing():
    """A SharedState with no ``handoff`` section gets one populated from the record."""
    state: dict = {}
    record = _record_for(
        "uuid-001",
        handoff_state="in_design",
        owning_role="system-architect",
    )

    main_ws._sync_handoff_from_project_record(state, record)

    assert state["handoff"]["handoff_state"] == "in_design"
    assert state["handoff"]["owning_role"] == "system-architect"


# ---------------------------------------------------------------------------
# _compute_persona_skip_completed
# ---------------------------------------------------------------------------


def test_skip_completed_for_initial_state_is_empty():
    """The first segment in the chain has no upstream work to skip.

    Validates: Requirements 8.3
    """
    initial = workflow_registry.initial_state()  # "draft" -> ba_segment
    skip = main_ws._compute_persona_skip_completed(initial)
    assert skip == set()


@pytest.mark.parametrize(
    "handoff_state",
    ["assigned_to_sa", "in_design", "design_ready", "ready_for_dev"],
)
def test_skip_completed_for_sa_states_excludes_ba_stages(handoff_state):
    """SA connection on any SA-owned state computes skip ⊇ ba_segment stages.

    Verifies the registry-derived contract (design Section 5c, Req 8.3): the
    SA persona never re-runs the BA persona's work. Because the result is
    the union of *all* upstream segments' stages, the BA segment's stages
    must be a subset of the computed skip.

    Validates: Requirements 8.3, 8.4
    """
    skip = main_ws._compute_persona_skip_completed(handoff_state)

    ba_stages = set(workflow_registry.segment_stages("ba_segment"))
    sa_stages = set(workflow_registry.segment_stages("sa_segment"))

    assert ba_stages.issubset(skip), (
        f"Expected SA's skip set to cover all BA stages; "
        f"missing={ba_stages - skip!r}"
    )
    # The active SA segment must NOT be in the skip set — that's the work the
    # SA is supposed to do.
    assert skip.isdisjoint(sa_stages), (
        f"SA's skip set must not contain SA segment stages; "
        f"overlap={skip & sa_stages!r}"
    )


def test_skip_completed_unknown_state_returns_empty():
    """Unknown ``handoff_state`` falls back to skipping nothing (conservative)."""
    assert main_ws._compute_persona_skip_completed("not-a-real-state") == set()


def test_skip_completed_empty_state_returns_empty():
    """An empty / falsy ``handoff_state`` yields the empty set."""
    assert main_ws._compute_persona_skip_completed("") == set()


def test_skip_completed_extensible_to_third_persona(monkeypatch):
    """A registry with a third persona / segment yields a third skip set
    with no change to ``_compute_persona_skip_completed`` itself.

    The function is registry-derived: the test patches the active registry
    instance to the synthetic three-persona one used elsewhere in the suite,
    asserts that an SA-segment state still skips only BA stages (because
    the third segment is downstream of SA), and that a Dev-segment state
    skips both BA and SA stages.

    Validates: Requirements 8.3 (extensibility / Section 5d)
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
                "stages": ["requirements_analysis", "user_stories"],
                "entry_node": "requirements_analysis",
                "exit_hub": "hub_post_user_stories",
            },
            "sa_segment": {
                "owning_role": "system-architect",
                "stages": ["application_design", "units_generation"],
                "entry_node": "application_design",
                "exit_hub": "hub_post_units_generation",
            },
            "dev_segment": {
                "owning_role": "software-developer",
                "stages": ["implementation", "code_review"],
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

    sa_skip = main_ws._compute_persona_skip_completed("assigned_to_sa")
    assert sa_skip == {"requirements_analysis", "user_stories"}

    dev_skip = main_ws._compute_persona_skip_completed("assigned_to_dev")
    assert dev_skip == {
        "requirements_analysis",
        "user_stories",
        "application_design",
        "units_generation",
    }


# ---------------------------------------------------------------------------
# Request-changes bounce re-open (Part B): _detect_unprocessed_bounce /
# _apply_requirements_bounce
# ---------------------------------------------------------------------------


def _bounce_record(note="Please check the UI requirements once again.", ts="2026-06-16T18:31:55Z"):
    """A Project record whose latest history entry is an SA->BA bounce."""
    return {
        "project_uuid": "u-1",
        "handoff_state": "requirements_ready",
        "owning_role": "requirements-analyst",
        "assignment_history": [
            {"from": None, "to": "draft", "actor": "ba@example.com", "timestamp": "t0", "note": "created"},
            {"from": "requirements_ready", "to": "assigned_to_sa", "actor": "ba@example.com", "timestamp": "t1", "note": None},
            {"from": "assigned_to_sa", "to": "requirements_ready", "actor": "sa@example.com", "timestamp": ts, "note": note},
        ],
    }


def _state_at(handoff_state, last_processed_ts=None):
    handoff = {"handoff_state": handoff_state, "owning_role": "requirements-analyst", "assignment_history": []}
    if last_processed_ts is not None:
        handoff["last_processed_bounce_ts"] = last_processed_ts
    return {"handoff": handoff}


def test_detect_bounce_returns_entry_for_fresh_request_changes():
    record = _bounce_record()
    state = _state_at("requirements_ready")
    bounce = main_ws._detect_unprocessed_bounce(state, record)
    assert bounce is not None
    assert bounce["from"] == "assigned_to_sa"
    assert bounce["to"] == "requirements_ready"
    assert bounce["note"] == "Please check the UI requirements once again."


def test_detect_bounce_is_idempotent_once_processed():
    record = _bounce_record(ts="2026-06-16T18:31:55Z")
    state = _state_at("requirements_ready", last_processed_ts="2026-06-16T18:31:55Z")
    assert main_ws._detect_unprocessed_bounce(state, record) is None


def test_detect_bounce_none_for_forward_move():
    record = {
        "handoff_state": "assigned_to_sa",
        "assignment_history": [
            {"from": "requirements_ready", "to": "assigned_to_sa", "actor": "ba@example.com", "timestamp": "t1", "note": None},
        ],
    }
    state = _state_at("assigned_to_sa")
    assert main_ws._detect_unprocessed_bounce(state, record) is None


def test_detect_bounce_none_when_record_missing_or_empty():
    assert main_ws._detect_unprocessed_bounce(_state_at("requirements_ready"), None) is None
    assert main_ws._detect_unprocessed_bounce(
        _state_at("requirements_ready"), {"assignment_history": []}
    ) is None


def test_apply_bounce_reopens_stages_and_seeds_note():
    from shared_state import STAGE_NAMES

    record = _bounce_record()
    state = _state_at("requirements_ready")
    # Simulate a completed inception before the bounce.
    state["stage_progress"] = {s: "completed" for s in STAGE_NAMES}

    bounce = main_ws._detect_unprocessed_bounce(state, record)
    assert bounce is not None
    main_ws._apply_bounce_reset(state, bounce)

    # Every tracked stage is re-opened.
    assert all(state["stage_progress"][s] == "not_started" for s in STAGE_NAMES)
    # The SA note is seeded for downstream agents.
    assert state["change_request"]["note"] == "Please check the UI requirements once again."
    assert state["change_request"]["from_state"] == "assigned_to_sa"
    assert state["change_request"]["from_role"] == "system-architect"
    # Idempotency marker recorded.
    assert state["handoff"]["last_processed_bounce_ts"] == bounce["timestamp"]

    # A second detection now treats the bounce as processed.
    assert main_ws._detect_unprocessed_bounce(state, record) is None


def test_detect_bounce_dev_to_sa_direction():
    """The bounce handling is registry-generic — it also covers Dev -> SA
    (assigned_to_dev -> design_ready)."""
    record = {
        "handoff_state": "design_ready",
        "owning_role": "system-architect",
        "assignment_history": [
            {"from": "ready_for_dev", "to": "assigned_to_dev", "actor": "dev@example.com", "timestamp": "t1", "note": None},
            {"from": "assigned_to_dev", "to": "design_ready", "actor": "dev@example.com", "timestamp": "t2", "note": "Auth flow needs an OAuth option."},
        ],
    }
    state = {"handoff": {"handoff_state": "design_ready"}}
    bounce = main_ws._detect_unprocessed_bounce(state, record)
    assert bounce is not None
    assert bounce["from"] == "assigned_to_dev"
    assert bounce["to"] == "design_ready"

    main_ws._apply_bounce_reset(state, bounce)
    # Requester role is the Developer; the SA now owns the re-opened design.
    assert state["change_request"]["from_role"] == "software-developer"
    assert main_ws._change_request_requester_label(state) == "Software Developer"


# ---------------------------------------------------------------------------
# Resume artifact-existence guard: _filter_completed_by_artifacts
# ---------------------------------------------------------------------------


def _install_fake_file_exists(monkeypatch, present_paths):
    """Inject a fake tools.file_ops.file_exists returning True for present paths."""
    fake = types.ModuleType("tools.file_ops")
    fake.file_exists = lambda state, path: path in set(present_paths)
    monkeypatch.setitem(sys.modules, "tools.file_ops", fake)


def test_artifact_guard_keeps_stages_with_present_artifacts(monkeypatch):
    _install_fake_file_exists(monkeypatch, {
        "aidlc-docs/inception/requirements/requirements.md",
        "aidlc-docs/inception/user-stories/stories.md",
        "aidlc-docs/inception/plans/execution-plan.md",
        "aidlc-docs/inception/application-design/components.md",
        "aidlc-docs/inception/application-design/unit-of-work.md",
    })
    state = {"stage_progress": {}}
    completed = {
        "requirements_analysis", "user_stories", "workflow_planning",
        "application_design", "units_generation", "inception_complete",
        "workspace_detection",
    }
    out = main_ws._filter_completed_by_artifacts(state, completed)
    assert out == completed  # all artifacts present (or non-gated) → all kept


def test_artifact_guard_forces_rerun_of_design_when_artifacts_missing(monkeypatch):
    # Only the BA artifacts exist; the SA design artifacts are missing — the
    # exact HelpDesk post-bounce situation.
    _install_fake_file_exists(monkeypatch, {
        "aidlc-docs/inception/requirements/requirements.md",
        "aidlc-docs/inception/user-stories/stories.md",
        "aidlc-docs/inception/plans/execution-plan.md",
    })
    state = {"stage_progress": {}}
    completed = {
        "requirements_analysis", "user_stories", "workflow_planning",
        "application_design", "units_generation", "inception_complete",
    }
    out = main_ws._filter_completed_by_artifacts(state, completed)
    # Design stages with missing artifacts are dropped → they will run.
    assert "application_design" not in out
    assert "units_generation" not in out
    # Stages with present artifacts (and non-gated inception_complete) stay.
    assert {"requirements_analysis", "user_stories", "workflow_planning",
            "inception_complete"}.issubset(out)


def test_artifact_guard_keeps_planner_skipped_stages(monkeypatch):
    # A stage the planner legitimately skipped must stay skipped even though
    # it has no artifact (e.g. user_stories skipped for a tiny project).
    _install_fake_file_exists(monkeypatch, set())
    state = {"stage_progress": {"user_stories": "skipped"}}
    out = main_ws._filter_completed_by_artifacts(state, {"user_stories"})
    assert out == {"user_stories"}
