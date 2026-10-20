"""Unit tests for the orchestrator's Handoff_Gate wiring (task 5.2).

Covers the orchestrator-side contract for design Section 5b / Req 6.2:

  * ``create_handoff_gate_node(seg)`` returns a :class:`MultiAgentBase` whose
    ``invoke_async`` marks ``state["handoff"]["pending"] = True``, persists
    SharedState through the Gateway when ``_mcp_client`` and ``_session_id``
    are present, and raises a Strands breakout interrupt with reason
    ``{breakout: True, handoff: True, from_segment, message}``.
  * The factory's node id follows the documented ``handoff_gate__{seg}``
    naming convention so the runner's handoff branch sees the same id that
    the test fakes in ``tests/test_ws_handoff_breakout.py`` use.
  * In ``build_sdlc_graph``, every segment with a successor (per
    ``workflow_registry.ordered_segments()``) gets a ``handoff_gate__{seg}``
    node and the segment's ``exit_hub`` is rewired to route to the gate
    instead of into the next segment's ``entry_node``. For the BA→SA
    boundary that means the existing ``hub_post_user_stories →
    workflow_planning`` edge is replaced with two registry-derived edges:
    ``hub_post_user_stories → handoff_gate__ba_segment → workflow_planning``.
  * SA-side build (``skip_completed`` covering the BA segment) does not
    include ``handoff_gate__ba_segment`` in its graph: the upstream
    ``entry_node`` is excluded, the gate is added to ``_skip_ids``, and
    ``_edge`` silently drops every boundary edge involving the gate.

The orchestrator pulls in heavy SDK dependencies; we stub the few that are
not available in the unit-test environment and stub the per-stage agent
factories to return tiny ``MultiAgentBase`` instances so the real
``GraphBuilder`` can build the graph without invoking BedrockModel.

Validates: Requirements 6.2
"""

import asyncio
import os
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Make the runtime module importable.
# ---------------------------------------------------------------------------

_ICODE_AGENT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ICODE_AGENT_DIR not in sys.path:
    sys.path.insert(0, _ICODE_AGENT_DIR)


# Strands SDK is available in the test environment, but a couple of optional
# dependencies the orchestrator pulls in transitively are not. Stub them so
# the orchestrator import chain succeeds.
def _ensure_stub_module(name: str) -> types.ModuleType:
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


# strands_tools.handoff_to_user is referenced unconditionally by
# tools.human_input.
_strands_tools_pkg = _ensure_stub_module("strands_tools")


def _handoff_to_user_stub(*a, **kw):  # pragma: no cover
    return {"status": "success", "content": [{"text": ""}]}


_strands_tools_pkg.handoff_to_user = _handoff_to_user_stub

# bedrock_agentcore is referenced by config.model + memory integrations.
_bedrock = _ensure_stub_module("bedrock_agentcore")
_ensure_stub_module("bedrock_agentcore.memory")
_ensure_stub_module("bedrock_agentcore.memory.integrations")
_ensure_stub_module("bedrock_agentcore.memory.integrations.strands")


# Now import the real orchestrator + workflow_registry + Strands pieces.
import orchestrator  # noqa: E402
import workflow_registry  # noqa: E402
from strands.interrupt import InterruptException  # noqa: E402
from strands.multiagent.base import MultiAgentBase, MultiAgentResult  # noqa: E402
from strands.multiagent.graph import Status  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny MultiAgentBase used to stand in for Stage_Agents when building a real
# Strands graph. Each instance is unique so duplicate-instance validation in
# ``GraphBuilder`` is satisfied.
# ---------------------------------------------------------------------------


class _NoOpNode(MultiAgentBase):
    """No-op MultiAgentBase node used to populate the graph during tests."""

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:  # pragma: no cover - never executed
        return MultiAgentResult(status=Status.COMPLETED, execution_time=0)


def _stub_agent_factories(monkeypatch):
    """Replace every per-stage agent factory in ``orchestrator`` with a
    factory returning a fresh ``_NoOpNode``.

    The orchestrator's ``agents`` dict needs exactly one unique node per
    stage; the real factories require BedrockModel + Strands Agent which
    pull in heavyweight setup. ``_NoOpNode`` instances satisfy
    ``GraphBuilder.add_node`` (which accepts ``Agent | MultiAgentBase``).

    We also short-circuit the per-agent ``tool_registry.process_tools`` /
    ``system_prompt`` injections since ``_NoOpNode`` has no
    ``tool_registry``; those branches in ``build_sdlc_graph`` are gated on
    ``agent.system_prompt`` truthiness.
    """
    factory_names = (
        "create_workspace_detection_ext_agent",
        "create_workspace_detection_agent",
        "create_reverse_engineering_agent",
        "create_requirements_analysis_agent",
        "create_user_stories_agent",
        "create_workflow_planning_agent",
        "create_nfr_requirements_agent",
        "create_nfr_design_agent",
        "create_application_design_agent",
        "create_application_design_ext_agent",
        "create_units_generation_agent",
        "create_inception_complete_agent",
        "create_construction_handoff_agent",
        "create_test_planning_agent",
        "create_test_execution_agent",
        "create_qa_report_agent",
        "create_deployment_planning_agent",
        "create_release_agent",
    )

    def _make_stub_factory():
        def _factory(*a, **kw):
            node = _NoOpNode()
            # Attach attributes the orchestrator touches so the injection
            # branches don't AttributeError. ``system_prompt`` is set to
            # ``None`` (falsy) so the appendment branches skip.
            node.tool_registry = MagicMock()
            node.system_prompt = None
            return node

        return _factory

    for name in factory_names:
        monkeypatch.setattr(orchestrator, name, _make_stub_factory())


def _minimal_shared_state() -> dict:
    return {
        "execution_plan": {"stages_to_execute": []},
        "artifacts": {},
        "brownfield": False,
        "units": {"items": [], "current_unit_index": 0, "completed_units": []},
    }


# ---------------------------------------------------------------------------
# 1. create_handoff_gate_node — direct factory tests
# ---------------------------------------------------------------------------


def test_handoff_gate_node_id_follows_documented_convention():
    """The factory tags its returned node with ``name = handoff_gate__{seg}``.

    GraphBuilder uses ``executor.name`` as a default node id when none is
    supplied. The orchestrator passes the id explicitly, but pinning the
    convention at the factory level prevents drift between the runner's
    handoff branch (which compares against ``"handoff_gate__ba_segment"``)
    and the orchestrator's wiring.

    Validates: Requirements 6.2
    """
    node = orchestrator.create_handoff_gate_node("ba_segment")
    assert node.name == "handoff_gate__ba_segment"


def test_handoff_gate_invoke_async_marks_pending_persists_and_raises(monkeypatch):
    """invoke_async sets ``state["handoff"]["pending"] = True``, persists
    via the Gateway when ``_mcp_client`` and ``_session_id`` are present,
    and raises a Strands ``InterruptException`` carrying the breakout
    reason the runner expects (Section 5b, Req 6.2/6.3).

    Validates: Requirements 6.2
    """
    persist_calls: list[tuple[Any, str, str]] = []

    def _fake_persist(mcp_client, session_id, md_content):
        persist_calls.append((mcp_client, session_id, md_content))

    # ``create_handoff_gate_node`` imports ``_persist_via_gateway`` from
    # ``tools.state`` inside its body, so the closure captures whatever the
    # module attribute is at factory-call time. Patch the source attribute
    # BEFORE constructing the gate so the captured reference is the fake.
    import tools.state as _tools_state

    monkeypatch.setattr(_tools_state, "_persist_via_gateway", _fake_persist)

    node = orchestrator.create_handoff_gate_node("ba_segment")

    fake_mcp = object()
    state = {
        "_mcp_client": fake_mcp,
        "_session_id": "online_bookstore",
        "current_phase": "Inception",
        "current_stage": "user_stories",
        "stage_progress": {},
        "project": {"name": "Online Bookstore", "selected_project": "ob"},
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

    with pytest.raises(InterruptException) as exc_info:
        asyncio.run(node.invoke_async("ignored", invocation_state=state))

    # 1. Pending mirror set on SharedState.
    assert state["handoff"]["pending"] is True

    # 2. State persisted through the Gateway exactly once at the boundary.
    assert len(persist_calls) == 1, (
        f"Expected exactly one persist call, got {len(persist_calls)}"
    )
    persisted_mcp, persisted_session, persisted_md = persist_calls[0]
    assert persisted_mcp is fake_mcp
    assert persisted_session == "online_bookstore"
    assert "## Collaboration" in persisted_md  # serialized handoff section

    # 3. Breakout interrupt carries the runner-expected reason shape.
    interrupt = exc_info.value.interrupt
    assert interrupt.id == "handoff_gate__ba_segment"
    reason = interrupt.reason
    assert reason["breakout"] is True
    assert reason["handoff"] is True
    assert reason["from_segment"] == "ba_segment"
    assert isinstance(reason["message"], str) and reason["message"]


def test_handoff_gate_invoke_async_skips_persist_without_gateway(monkeypatch):
    """Without ``_mcp_client``/``_session_id`` the factory still raises the
    breakout but does not attempt persistence — parity with
    ``create_integration_hub_node``'s best-effort persistence rule.

    Validates: Requirements 6.2
    """
    persist_calls: list[tuple] = []

    def _fake_persist(*a, **kw):
        persist_calls.append(a)

    import tools.state as _tools_state

    monkeypatch.setattr(_tools_state, "_persist_via_gateway", _fake_persist)

    node = orchestrator.create_handoff_gate_node("ba_segment")

    with pytest.raises(InterruptException):
        asyncio.run(node.invoke_async("ignored", invocation_state={}))

    assert persist_calls == [], (
        "Persistence must be skipped when _mcp_client/_session_id are absent."
    )


# ---------------------------------------------------------------------------
# 2. build_sdlc_graph wiring — registry-derived gate placement
# ---------------------------------------------------------------------------


def _graph_internals(graph):
    """Return ``({node_id, ...}, {(from_id, to_id), ...})`` for a built graph.

    Strands ``Graph`` exposes nodes as a dict of ``GraphNode`` and edges as a
    set of ``GraphEdge`` (with ``from_node`` / ``to_node`` references). We
    flatten to plain id sets so assertions read naturally.
    """
    node_ids = set(graph.nodes.keys())
    edge_pairs = {(e.from_node.node_id, e.to_node.node_id) for e in graph.edges}
    return node_ids, edge_pairs


def test_ba_side_build_inserts_handoff_gate_on_user_stories_boundary(monkeypatch):
    """A BA-side build (``skip_completed=None``) contains the
    ``handoff_gate__ba_segment`` node on the BA segment's exit-hub edge.

    The literal ``hub_post_user_stories → workflow_planning`` boundary is
    replaced with two registry-derived edges that route through the gate:
    ``hub_post_user_stories → handoff_gate__ba_segment → workflow_planning``.

    Validates: Requirements 6.2
    """
    _stub_agent_factories(monkeypatch)

    shared_state = _minimal_shared_state()
    graph = orchestrator.build_sdlc_graph(
        shared_state,
        model=object(),  # bypass create_model()
        interactive=False,
        skip_completed=None,
    )

    nodes, edges = _graph_internals(graph)

    # Gate node was added.
    assert "handoff_gate__ba_segment" in nodes, (
        f"Expected handoff_gate__ba_segment in graph nodes, got: {sorted(nodes)}"
    )

    # The BA exit hub now routes into the gate, not directly into the SA's
    # entry stage. Both registry-derived endpoints are derived helpers so
    # the test is itself registry-anchored.
    ba_exit_hub = workflow_registry.exit_hub("ba_segment")
    sa_entry = workflow_registry.entry_node("sa_segment")
    assert (ba_exit_hub, "handoff_gate__ba_segment") in edges, (
        f"Expected exit-hub to route to gate ({ba_exit_hub} -> "
        f"handoff_gate__ba_segment); edges: {sorted(edges)}"
    )
    assert ("handoff_gate__ba_segment", sa_entry) in edges, (
        f"Expected gate to route to next-segment entry "
        f"(handoff_gate__ba_segment -> {sa_entry}); edges: {sorted(edges)}"
    )

    # And the original BA→SA boundary edge that bypassed the gate is gone.
    assert (ba_exit_hub, sa_entry) not in edges, (
        f"Expected original direct boundary edge ({ba_exit_hub} -> {sa_entry}) "
        f"to be removed in favor of routing through the gate; edges: "
        f"{sorted(edges)}"
    )


def test_sa_side_build_excludes_ba_segment_handoff_gate(monkeypatch):
    """An SA-side build (``skip_completed`` covering BA segment stages) does
    not include ``handoff_gate__ba_segment`` in its graph.

    The persona-aware skip set excludes ``workspace_detection_ext`` (the BA
    segment's ``entry_node``); the gate is added to ``_skip_ids`` so the
    boundary edges into it are silently dropped, and the SA's exit-hub →
    next-stage edges (``hub_post_workflow_planning → application_design``,
    etc.) remain wired correctly.

    Validates: Requirements 6.2, 8.3
    """
    _stub_agent_factories(monkeypatch)

    # Build the persona-aware skip set the same way main_ws does for an SA
    # connection on ``assigned_to_sa`` — fully registry-derived.
    seg = workflow_registry.segment_of("assigned_to_sa")
    skip_completed: set[str] = set()
    for upstream in workflow_registry.upstream_segments(seg):
        skip_completed |= set(workflow_registry.segment_stages(upstream))

    shared_state = _minimal_shared_state()
    graph = orchestrator.build_sdlc_graph(
        shared_state,
        model=object(),
        interactive=False,
        skip_completed=skip_completed,
    )

    nodes, edges = _graph_internals(graph)

    # The BA-segment gate must not be present in the SA's view of the graph.
    assert "handoff_gate__ba_segment" not in nodes, (
        f"SA-side build must not include handoff_gate__ba_segment; "
        f"got nodes: {sorted(nodes)}"
    )

    # No edge involving the BA gate survives the skip rewriting.
    assert not any(
        "handoff_gate__ba_segment" in (a, b) for (a, b) in edges
    ), (
        "SA-side build must not contain edges referencing "
        f"handoff_gate__ba_segment; edges: {sorted(edges)}"
    )

    # And the SA segment's downstream wiring is intact (sanity check).
    assert ("hub_post_workflow_planning", "application_design") in edges or any(
        a == "hub_post_workflow_planning" and b == "application_design"
        for (a, b) in edges
    )



def test_sa_side_build_inserts_handoff_gate_on_dev_boundary(monkeypatch):
    """A BA/SA-side build routes the SA segment's terminal exit hub through
    ``handoff_gate__sa_segment`` into the Developer segment's entry node.

    The SA segment now has a successor (dev_segment), so the registry-derived
    gate loop wires ``hub_post_inception_complete → handoff_gate__sa_segment →
    construction_handoff``. This is the runtime-gate SA→Dev handoff (symmetric
    to BA→SA).

    Validates: Requirements 6.2
    """
    _stub_agent_factories(monkeypatch)

    shared_state = _minimal_shared_state()
    graph = orchestrator.build_sdlc_graph(
        shared_state, model=object(), interactive=False, skip_completed=None,
    )
    nodes, edges = _graph_internals(graph)

    sa_exit_hub = workflow_registry.exit_hub("sa_segment")  # hub_post_inception_complete
    dev_entry = workflow_registry.entry_node("dev_segment")  # construction_handoff

    assert "handoff_gate__sa_segment" in nodes
    assert "construction_handoff" in nodes
    assert (sa_exit_hub, "handoff_gate__sa_segment") in edges
    assert ("handoff_gate__sa_segment", dev_entry) in edges
    # inception_complete feeds the SA segment's terminal exit hub.
    assert ("inception_complete", sa_exit_hub) in edges


def test_dev_side_build_enters_at_construction_handoff(monkeypatch):
    """A Developer-side build (skip set covers BA + SA stages) enters at
    ``construction_handoff`` and excludes both upstream handoff gates.

    Validates: Requirements 6.2, 8.3
    """
    _stub_agent_factories(monkeypatch)

    seg = workflow_registry.segment_of("assigned_to_dev")
    skip_completed: set[str] = set()
    for upstream in workflow_registry.upstream_segments(seg):
        skip_completed |= set(workflow_registry.segment_stages(upstream))

    shared_state = _minimal_shared_state()
    graph = orchestrator.build_sdlc_graph(
        shared_state, model=object(), interactive=False, skip_completed=skip_completed,
    )
    nodes, edges = _graph_internals(graph)

    # construction_handoff is present; both upstream gates are excluded.
    assert "construction_handoff" in nodes
    assert "handoff_gate__ba_segment" not in nodes
    assert "handoff_gate__sa_segment" not in nodes
    assert not any(
        "handoff_gate__sa_segment" in (a, b) or "handoff_gate__ba_segment" in (a, b)
        for (a, b) in edges
    )
    # The graph entry point is the dev segment's entry node.
    assert graph.entry_points, "expected an entry point on the dev-side build"
    entry_ids = {n.node_id for n in graph.entry_points}
    assert entry_ids == {"construction_handoff"}, (
        f"dev-side entry should be construction_handoff, got {entry_ids}"
    )
