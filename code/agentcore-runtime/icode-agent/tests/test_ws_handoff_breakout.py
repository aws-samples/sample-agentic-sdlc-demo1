"""Unit tests for the WebSocket runner's handoff breakout handling (task 5.3).

These cover the behavior added to ``_run_graph_with_interrupts`` in
``main_ws.py`` for handoff breakouts (design Sections 5b / 9, Req 6.3):

  * when a graph yields an interrupt whose ``reason`` carries ``handoff=True``,
    the runner emits a ``handoff_required`` WebSocket message resolved against
    the registry (``from_role`` / ``to_role`` from ``from_segment``);
  * the runner persists ``SharedState`` via the Gateway so the collaboration
    metadata survives session restart;
  * the runner halts the graph loop after the handoff (no resume payload is
    fed back to the graph), because the next persona reconnects through a
    fresh WebSocket session.

The Strands graph is replaced with a tiny stub that yields a single
``Status.INTERRUPTED`` result event whose ``interrupts`` list contains one
fake handoff interrupt. The Gateway persistence call is intercepted via
monkeypatch on ``main_ws._persist_via_gateway``.

The runtime container's heavy dependencies (``bedrock_agentcore``, ``strands``,
``mcp``) are not installed in the unit-test environment; they are stubbed via
``sys.modules`` BEFORE importing ``main_ws`` so the runner's pure-Python loop
can be exercised in isolation.
"""

import asyncio
import json
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Make the runtime module importable, then stub heavy SDK dependencies before
# importing main_ws. Mirrors the lightweight import pattern used in the other
# tests/test_ws_*.py files.
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
            mod.__path__ = []  # mark as package so submodules can be attached
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

    def websocket(self, fn):  # used as @app.websocket decorator
        return fn

    def run(self):  # pragma: no cover — never invoked in tests
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
    """@tool / @tool(context=True) — return the underlying function unchanged."""
    if args and callable(args[0]) and not kwargs:
        return args[0]

    def _decorator(fn):
        return fn

    return _decorator


# strands top-level: strands.Agent + @tool decorator referenced widely.
_strands_root = _ensure_stub_module("strands")
_strands_root.tool = _tool_passthrough
_strands_root.Agent = type("Agent", (), {})

# strands.multiagent.Status — main_ws compares against Status.INTERRUPTED.
_strands_multiagent = _ensure_stub_module("strands.multiagent")


class _StatusStub:
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"


_strands_multiagent.Status = _StatusStub

# strands.interrupt.InterruptException — main_ws now catches this around
# stream_async to handle the raised-exception channel of the handoff path
# (handoff_gate__{seg} nodes ``raise InterruptException(...)`` directly).
_strands_interrupt = _ensure_stub_module("strands.interrupt")


class _InterruptExceptionStub(Exception):
    """Test stand-in for ``strands.interrupt.InterruptException``.

    Mirrors the real class shape used by ``orchestrator.create_handoff_gate_node``
    and ``test_orchestrator_handoff_gate``: constructed with a single
    ``Interrupt``-shaped object and exposes it via ``self.interrupt``.
    """

    def __init__(self, interrupt):
        super().__init__(f"Interrupt({getattr(interrupt, 'id', '?')})")
        self.interrupt = interrupt


class _InterruptStub:
    """Minimal Interrupt stand-in matching ``strands.interrupt.Interrupt`` shape."""

    def __init__(self, *, id, name=None, reason=None, response=None):
        self.id = id
        self.name = name or id
        self.reason = reason or {}
        self.response = response


_strands_interrupt.InterruptException = _InterruptExceptionStub
_strands_interrupt.Interrupt = _InterruptStub

_strands_session = _ensure_stub_module("strands.session")
_strands_session.S3SessionManager = type("S3SessionManager", (), {})

# strands.tools.tool is referenced as ``from strands.tools import tool`` in
# tools.state and similar — expose a passthrough decorator on the package.
_strands_tools_pkg = _ensure_stub_module("strands.tools")
_strands_tools_pkg.tool = _tool_passthrough

_strands_mcp = _ensure_stub_module("strands.tools.mcp")
_strands_mcp.MCPClient = type("MCPClient", (), {})

# strands.tools.decorator.tool — used by tools.state and similar
_strands_tools_decorator = _ensure_stub_module("strands.tools.decorator")
_strands_tools_decorator.tool = _tool_passthrough

# strands.models.BedrockModel — used by config.model
_strands_models = _ensure_stub_module("strands.models")
_strands_models.BedrockModel = type("BedrockModel", (), {})

# orchestrator imports many agents + Strands GraphBuilder; replace with a
# minimal stub since we never actually build a real graph in these tests.
_orchestrator_stub = types.ModuleType("orchestrator")


def _build_sdlc_graph_stub(*a, **kw):  # pragma: no cover
    raise RuntimeError("orchestrator.build_sdlc_graph not available in tests")


_orchestrator_stub.build_sdlc_graph = _build_sdlc_graph_stub
sys.modules["orchestrator"] = _orchestrator_stub

# config.model.create_model — also stubbed; main_ws imports the symbol but
# only invokes it inside handle_message (which we never call in these tests).
_config_pkg = _ensure_stub_module("config")
_config_model = _ensure_stub_module("config.model")


def _create_model_stub(*a, **kw):  # pragma: no cover
    raise RuntimeError("config.model.create_model not available in tests")


_config_model.create_model = _create_model_stub

# tools.integrations.load_integration_config and tools.oauth_provider — only
# referenced inside handle_message; stub them so module import succeeds. We
# preserve the real ``tools`` package (so ``tools.state`` and
# ``tools.stream_adapter`` keep their real implementations) by importing it
# first, then attaching stubbed submodules.
import tools as _real_tools_pkg  # noqa: E402,F401  # populate sys.modules

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


# Import main_ws AFTER the stubs are in place. tools.stream_adapter is pure
# Python and imports cleanly without stubs; we still import it via the same
# package path so the StreamAdapter class lives at its real location.
import main_ws  # noqa: E402
from tools.stream_adapter import StreamAdapter  # noqa: E402


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    """Minimal Starlette-WebSocket stand-in capturing every send_text call."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


class _FakeInterruptState:
    """Mirrors the fields ``_run_graph_with_interrupts`` reads off ``graph._interrupt_state``."""

    def __init__(self, interrupts: dict) -> None:
        self.activated = True
        self.interrupts = interrupts


class _FakeGraph:
    """Stub graph that yields a single INTERRUPTED result event then stops.

    ``stream_async`` is an async generator that emits exactly one event
    matching the runner's expected shape:

        {"result": GraphResult(status=INTERRUPTED, interrupts=[...])}

    Subsequent calls (after a resume) raise ``AssertionError`` — the handoff
    branch is supposed to halt the loop, never feed an interruptResponse back
    in.
    """

    def __init__(self, interrupts: list) -> None:
        self._interrupts = interrupts
        self._interrupt_state = _FakeInterruptState(
            {i.id: i for i in interrupts}
        )
        self.calls = 0
        self.last_input = None

    def stream_async(self, current_input, invocation_state=None):
        self.calls += 1
        self.last_input = current_input
        if self.calls > 1:  # pragma: no cover — defensive guard
            raise AssertionError(
                "stream_async must NOT be invoked again after a handoff "
                "breakout — the runner is required to halt the graph loop."
            )

        graph = self
        result = SimpleNamespace(
            status=_StatusStub.INTERRUPTED,
            interrupts=list(graph._interrupts),
            interrupted_nodes=["handoff_gate__ba_segment"],
        )

        async def _gen():
            yield {"result": result}

        return _gen()


def _make_handoff_interrupt(
    *,
    from_segment: str = "ba_segment",
    message: str = "User stories are ready. Assign to Solution Architect?",
):
    """Build a fake interrupt matching what ``handoff_gate__{seg}`` raises."""
    return SimpleNamespace(
        id="handoff_gate__ba_segment",
        reason={
            "breakout": True,
            "handoff": True,
            "from_segment": from_segment,
            "message": message,
        },
    )


def _minimal_shared_state(**overrides) -> dict:
    """A SharedState-shaped dict that ``tools.state._state_to_markdown`` accepts."""
    state = {
        "_mcp_client": MagicMock(),
        "_session_id": "online_bookstore",
        "_project_uuid": "uuid-001",
        "handoff": {
            "handoff_state": "requirements_ready",
            "owning_role": "requirements-analyst",
            "assignment_history": [],
        },
        "current_phase": "Inception",
        "current_stage": "user_stories",
        "stage_progress": {},
        "project": {
            "name": "Online Bookstore",
            "selected_project": "Online Bookstore",
        },
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
        "units": {"items": [], "current_unit_index": 0, "completed_units": []},
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_handoff_breakout_emits_handoff_required_message(monkeypatch):
    """A handoff breakout interrupt makes the runner emit ``handoff_required``
    with registry-derived ``from_role`` / ``to_role``, persist the SharedState
    mirror via the Gateway, and halt the graph loop without resuming.

    Validates: Requirements 6.3
    """
    websocket = _FakeWebSocket()
    stream_adapter = StreamAdapter(websocket)

    interrupt = _make_handoff_interrupt(
        from_segment="ba_segment",
        message="User stories are ready. Assign to Solution Architect?",
    )
    graph = _FakeGraph([interrupt])

    persisted_calls: list[tuple[str, str]] = []

    def _fake_persist(_mcp_client, session_id, md_content):
        persisted_calls.append((session_id, md_content))

    monkeypatch.setattr(main_ws, "_persist_via_gateway", _fake_persist)

    shared_state = _minimal_shared_state()

    asyncio.run(
        main_ws._run_graph_with_interrupts(
            graph, "Build a software project", shared_state, stream_adapter,
        )
    )

    # The graph loop did not resume after the handoff (Section 5b / Req 6.3).
    assert graph.calls == 1, (
        "Expected the graph loop to halt after a handoff breakout, but "
        f"stream_async was invoked {graph.calls} times."
    )

    # Exactly one handoff_required message reached the WebSocket.
    handoff_msgs = [m for m in websocket.sent if m.get("type") == "handoff_required"]
    assert len(handoff_msgs) == 1, (
        f"Expected exactly one handoff_required message, got: {websocket.sent!r}"
    )
    msg = handoff_msgs[0]
    assert msg["project_uuid"] == "uuid-001"
    assert msg["from_role"] == "requirements-analyst"
    assert msg["to_role"] == "system-architect"
    assert msg["summary"] == (
        "User stories are ready. Assign to Solution Architect?"
    )

    # No human_input prompt was sent for the handoff interrupt — the breakout
    # is terminal, not an interactive question.
    human_input_prompts = [
        m for m in websocket.sent if m.get("type") == "human_input"
    ]
    assert human_input_prompts == [], (
        "handoff breakout should not send a human_input prompt; "
        f"got: {human_input_prompts!r}"
    )

    # SharedState was persisted via the Gateway exactly once at the boundary
    # so the handoff mirror survives session restart/resume.
    assert len(persisted_calls) == 1, (
        f"Expected one persist_via_gateway call, got {len(persisted_calls)}"
    )
    persisted_session_id, persisted_md = persisted_calls[0]
    assert persisted_session_id == "online_bookstore"
    assert "## Collaboration" in persisted_md  # mirror is included

    # The handoff mirror was marked pending before persisting.
    assert shared_state["handoff"].get("pending") is True


def test_handoff_breakout_falls_back_to_session_id_when_uuid_missing(monkeypatch):
    """When ``_project_uuid`` is missing, the runner falls back to ``_session_id``
    so the Frontend always receives a non-empty project identifier in the card.

    Validates: Requirements 6.3
    """
    websocket = _FakeWebSocket()
    stream_adapter = StreamAdapter(websocket)

    interrupt = _make_handoff_interrupt(from_segment="ba_segment")
    graph = _FakeGraph([interrupt])

    monkeypatch.setattr(main_ws, "_persist_via_gateway", lambda *a, **k: None)

    shared_state = _minimal_shared_state()
    shared_state.pop("_project_uuid", None)
    shared_state["_session_id"] = "fallback_id"

    asyncio.run(
        main_ws._run_graph_with_interrupts(
            graph, "Build", shared_state, stream_adapter,
        )
    )

    handoff_msgs = [m for m in websocket.sent if m.get("type") == "handoff_required"]
    assert len(handoff_msgs) == 1
    assert handoff_msgs[0]["project_uuid"] == "fallback_id"


def test_non_handoff_breakout_still_acks_and_continues_loop(monkeypatch):
    """A breakout interrupt without ``handoff=True`` retains the prior behavior:
    a notification is sent, an ``acknowledged`` interruptResponse is enqueued,
    and the runner resumes the graph (does not treat it as a handoff halt).

    Validates: Requirements 6.3 (negative — ensures handoff path is opt-in)
    """
    websocket = _FakeWebSocket()
    stream_adapter = StreamAdapter(websocket)

    plain_breakout = SimpleNamespace(
        id="plain_breakout",
        reason={"breakout": True, "message": "End of workflow"},
    )

    class _ResumingGraph:
        """Yields the breakout once, then completes normally on resume."""

        def __init__(self) -> None:
            self._interrupt_state = _FakeInterruptState(
                {plain_breakout.id: plain_breakout}
            )
            self.calls = 0
            self.resume_payloads: list = []

        def stream_async(self, current_input, invocation_state=None):
            self.calls += 1

            if self.calls == 1:
                result = SimpleNamespace(
                    status=_StatusStub.INTERRUPTED,
                    interrupts=[plain_breakout],
                    interrupted_nodes=["inception_complete"],
                )

                async def _gen_interrupt():
                    yield {"result": result}

                return _gen_interrupt()

            # Second call: the runner must resume with the interruptResponse.
            self.resume_payloads.append(current_input)
            self._interrupt_state.activated = False
            self._interrupt_state.interrupts = {}
            completed = SimpleNamespace(
                status=_StatusStub.COMPLETED,
                interrupts=[],
                interrupted_nodes=[],
            )

            async def _gen_done():
                yield {"result": completed}

            return _gen_done()

    graph = _ResumingGraph()

    asyncio.run(
        main_ws._run_graph_with_interrupts(
            graph, "Build", {}, stream_adapter,
        )
    )

    # Loop ran twice (first interrupted, second completed).
    assert graph.calls == 2

    # Plain breakout was surfaced as a human_input notification, not a
    # handoff_required card.
    notifications = [
        m for m in websocket.sent if m.get("type") == "human_input_notify"
    ]
    assert len(notifications) == 1
    assert notifications[0]["message"] == "End of workflow"

    handoffs = [m for m in websocket.sent if m.get("type") == "handoff_required"]
    assert handoffs == []

    # The resume payload contains the acknowledged interruptResponse.
    assert len(graph.resume_payloads) == 1
    payload = graph.resume_payloads[0]
    assert isinstance(payload, list) and len(payload) == 1
    assert payload[0]["interruptResponse"]["interruptId"] == "plain_breakout"
    assert payload[0]["interruptResponse"]["response"] == "acknowledged"


# ---------------------------------------------------------------------------
# Raised-exception channel: regression for the handoff_gate InterruptException
# leak. When Strands lets InterruptException propagate out of stream_async
# (instead of yielding an INTERRUPTED result event), the runner must still
# emit handoff_required, persist state, and halt the loop. Before this fix,
# the exception fell through to the outer ``except Exception`` and reached
# the user as ``Workflow execution failed: InterruptException: ...``.
# ---------------------------------------------------------------------------


class _RaisingGraph:
    """Stub graph whose ``stream_async`` raises ``InterruptException`` directly.

    Models the handoff_gate__{seg} node calling ``raise InterruptException(...)``
    when Strands does not surface the interrupt as a result event. The
    ``_interrupt_state`` is left empty so the runner cannot fall back to
    the result-event path — only the new try/except around ``async for``
    can satisfy the test.
    """

    def __init__(self, exception_to_raise) -> None:
        self._exception = exception_to_raise
        self._interrupt_state = _FakeInterruptState({})  # not activated
        self._interrupt_state.activated = False
        self.calls = 0

    def stream_async(self, current_input, invocation_state=None):
        self.calls += 1
        if self.calls > 1:  # pragma: no cover — defensive guard
            raise AssertionError(
                "stream_async must NOT be invoked again after a raised "
                "handoff InterruptException — the runner is required to "
                "halt the graph loop."
            )

        exc = self._exception

        async def _gen():
            # Async generators must yield at least once before raising for
            # ``async for`` to enter the loop body, but raising on the first
            # iteration is sufficient to model stream_async propagating the
            # exception out instead of yielding a final result event.
            raise exc
            yield  # pragma: no cover — unreachable, makes this a generator

        return _gen()


def test_raised_handoff_interrupt_exception_emits_handoff_required(monkeypatch):
    """Regression: when ``stream_async`` raises ``InterruptException`` (rather
    than yielding an INTERRUPTED result event), the runner catches it,
    routes through the handoff branch, persists SharedState, and halts the
    graph loop without surfacing a ``Workflow execution failed`` error.

    Before this fix, the exception leaked past ``_run_graph_with_interrupts``
    and the user saw ``Workflow execution failed: InterruptException: ...``
    in the chat — and the Frontend ``HandoffCard`` (the only surface
    carrying "Assign to Solution Architect") never appeared.

    Validates: bugfix spec clauses 2.1–2.4.
    """
    websocket = _FakeWebSocket()
    stream_adapter = StreamAdapter(websocket)

    interrupt = _InterruptStub(
        id="handoff_gate__ba_segment",
        name="handoff_gate__ba_segment",
        reason={
            "breakout": True,
            "handoff": True,
            "from_segment": "ba_segment",
            "message": (
                "ba_segment segment is finished. Ownership transfers to "
                "the next persona — assign the project through the Project "
                "API to continue."
            ),
        },
    )
    graph = _RaisingGraph(_InterruptExceptionStub(interrupt))

    persisted_calls: list[tuple[str, str]] = []

    def _fake_persist(_mcp_client, session_id, md_content):
        persisted_calls.append((session_id, md_content))

    monkeypatch.setattr(main_ws, "_persist_via_gateway", _fake_persist)

    shared_state = _minimal_shared_state()

    asyncio.run(
        main_ws._run_graph_with_interrupts(
            graph, "Build a software project", shared_state, stream_adapter,
        )
    )

    # The graph loop did not retry after the raised handoff (Req 2.3).
    assert graph.calls == 1, (
        "Expected the graph loop to halt after a raised handoff "
        f"InterruptException, but stream_async was invoked {graph.calls} times."
    )

    # Exactly one handoff_required message reached the WebSocket — the same
    # contract the result-event path satisfies (Req 2.1).
    handoff_msgs = [m for m in websocket.sent if m.get("type") == "handoff_required"]
    assert len(handoff_msgs) == 1, (
        f"Expected exactly one handoff_required message, got: {websocket.sent!r}"
    )
    msg = handoff_msgs[0]
    assert msg["project_uuid"] == "uuid-001"
    assert msg["from_role"] == "requirements-analyst"
    assert msg["to_role"] == "system-architect"
    assert "ba_segment segment is finished" in msg["summary"]

    # No ``Workflow execution failed`` error message was sent — handoff is
    # a controlled boundary, not a workflow failure (Req 2.4).
    error_msgs = [m for m in websocket.sent if m.get("type") == "error"]
    assert error_msgs == [], (
        f"Raised handoff InterruptException must not surface as an error "
        f"message; got: {error_msgs!r}"
    )

    # SharedState was persisted via the Gateway exactly once at the boundary
    # (Req 2.2).
    assert len(persisted_calls) == 1, (
        f"Expected one persist_via_gateway call, got {len(persisted_calls)}"
    )
    persisted_session_id, persisted_md = persisted_calls[0]
    assert persisted_session_id == "online_bookstore"
    assert "## Collaboration" in persisted_md  # mirror is included

    # The handoff mirror was marked pending before persisting (Req 2.2).
    assert shared_state["handoff"].get("pending") is True


def test_raised_non_handoff_interrupt_exception_re_raises(monkeypatch):
    """An ``InterruptException`` whose reason is NOT a handoff (e.g. a plain
    breakout that escapes through the raised channel) must re-raise so the
    outer ``except Exception`` in ``main_ws`` can surface it as a
    ``Workflow execution failed`` message exactly as it does today. This
    keeps the fix opt-in: only handoff-flagged interrupts are intercepted.

    Validates: bugfix spec clause 3.3 (regression prevention for non-handoff
    raised exceptions).
    """
    websocket = _FakeWebSocket()
    stream_adapter = StreamAdapter(websocket)

    plain = _InterruptStub(
        id="plain_raised",
        reason={"breakout": True, "message": "raised plain breakout"},
    )
    graph = _RaisingGraph(_InterruptExceptionStub(plain))

    monkeypatch.setattr(main_ws, "_persist_via_gateway", lambda *a, **k: None)

    shared_state = _minimal_shared_state()

    with pytest.raises(_InterruptExceptionStub):
        asyncio.run(
            main_ws._run_graph_with_interrupts(
                graph, "Build", shared_state, stream_adapter,
            )
        )

    # Nothing was sent for the non-handoff raised exception — the runner's
    # outer except is responsible for surfacing it.
    handoffs = [m for m in websocket.sent if m.get("type") == "handoff_required"]
    assert handoffs == [], (
        "Non-handoff raised InterruptException must not emit handoff_required"
    )

    # Pending mirror was NOT set; re-raising leaves the state untouched so
    # the outer error path persists state without contaminating it.
    assert shared_state["handoff"].get("pending") is not True
