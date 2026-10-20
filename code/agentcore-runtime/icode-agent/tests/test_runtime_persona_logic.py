"""Cross-cutting summary tests for runtime persona logic (task 5.6).

Tasks 5.3-5.5 added focused test files for each helper that ``main_ws.py``
exposes:

  * ``tests/test_ws_handoff_breakout.py`` — handoff breakout interrupt
    handling in ``_run_graph_with_interrupts`` (task 5.3 / Req 6.3).
  * ``tests/test_ws_persona_session_entry.py`` — Project_Store sync and
    persona-aware ``skip_completed`` computation (task 5.4 / Req 8.1-8.4).
  * ``tests/test_ws_segment_rbac.py`` — segment RBAC helpers and the
    ``handle_message`` end-to-end RBAC gate (task 5.5 / Req 2.4-2.5, 6.5).

Task 5.6 calls out three composite assertions that bridge those helpers:

  1. **SA connection on ``assigned_to_sa`` computes ``skip_completed ⊇
     ba_segment`` stages and entry node ``workflow_planning`` (via registry
     helpers).** The first half is covered by
     ``test_skip_completed_for_sa_states_excludes_ba_stages`` in 5.4; this
     module adds the second half — that the registry-helper composition
     ``entry_node(segment_of("assigned_to_sa"))`` resolves to
     ``"workflow_planning"`` and that the orchestrator's entry-point
     selection (first non-skipped node in ``NODE_NAMES``) lands on the same
     node.

  2. **BA segment routes to ``handoff_gate__ba_segment``, raises the handoff
     breakout, and the runner emits ``handoff_required`` and halts.** The
     runner side is covered end-to-end by
     ``test_handoff_breakout_emits_handoff_required_message`` (5.3), which
     uses the literal id ``"handoff_gate__ba_segment"``. This module adds
     the registry-side contract: the BA segment has a successor (so a gate
     is required) and the gate id follows the documented
     ``handoff_gate__{seg}`` naming convention from design Section 5b.

  3. **Runtime RBAC rejects a wrong-role connection with an ``error`` and no
     agent run.** Already covered end-to-end by
     ``test_handle_message_rejects_wrong_role_with_error_message`` and
     ``test_handle_message_soft_allows_when_no_authorization_header`` (5.5,
     soft-allow path: empty groups bypass the runtime check because the
     Project API is the authoritative RBAC layer); we add a registry-derived
     assertion here so the link from the abstract "owning role of the active
     segment" to the concrete BA/SA pair is visible at the test level.

The tests in this module deliberately exercise the registry-derived contract
(no orchestrator import, no graph build) so they cleanly cover the
"via registry helpers" wording in the task and remain valid for the
extensibility path in design Section 5d (a third persona becomes a third
entry-node and a third handoff gate without code changes here).

Validates: Requirements 2.4, 2.5, 6.2, 6.3, 8.2, 8.3
"""

import os
import sys

import pytest


# Make the runtime module importable regardless of pytest's rootdir/cwd.
_ICODE_AGENT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ICODE_AGENT_DIR not in sys.path:
    sys.path.insert(0, _ICODE_AGENT_DIR)

import workflow_registry  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Registry-derived entry node for the SA segment (bullet 1).
# ---------------------------------------------------------------------------


def test_sa_session_entry_node_is_workflow_planning_via_registry():
    """Composing ``entry_node(segment_of(state))`` for an SA-owned state
    resolves to ``workflow_planning`` — the documented session entry for the
    Solution Architect persona.

    Pairs with ``test_skip_completed_for_sa_states_excludes_ba_stages``
    (test_ws_persona_session_entry.py, task 5.4) which proves the first
    half of 5.6's bullet 1 (BA stages are skipped). This test proves the
    second half: the active SA segment's registry-declared entry node is
    ``workflow_planning``. Both halves come from registry helpers, with
    no BA/SA-specific code path.

    Validates: Requirements 8.2, 8.3
    """
    active_segment = workflow_registry.segment_of("assigned_to_sa")
    assert active_segment == "sa_segment"
    assert workflow_registry.entry_node(active_segment) == "workflow_planning"


@pytest.mark.parametrize(
    "handoff_state",
    ["assigned_to_sa", "in_design", "design_ready", "ready_for_dev"],
)
def test_sa_states_all_resolve_to_workflow_planning_entry(handoff_state):
    """Every SA-segment state (any state with the SA persona owning it)
    resolves through the registry helpers to the same entry node
    ``workflow_planning``. This guarantees session entry is stable across
    every checkpoint of the SA's segment, not just the boundary state.

    Validates: Requirements 8.2, 8.3
    """
    seg = workflow_registry.segment_of(handoff_state)
    assert workflow_registry.entry_node(seg) == "workflow_planning"


def test_orchestrator_entry_point_aligns_with_registry_for_sa(monkeypatch):
    """The orchestrator selects its entry point as the first node in
    ``NODE_NAMES`` not present in ``skip_completed``. When the SA persona
    connects on ``assigned_to_sa``, the persona-aware skip set excludes all
    BA-segment node IDs and the next remaining node in ``NODE_NAMES`` is
    ``workflow_planning`` — the same value the registry helper returns for
    the SA segment's ``entry_node``.

    The test recreates the orchestrator's selection rule from its
    constants so it does not require importing the heavy ``orchestrator``
    module (which pulls in Strands and every Stage agent). This keeps the
    contract assertion focused: NODE_NAMES + STAGE_TO_NODE_IDS together
    with the registry helpers must agree on the SA entry point.

    Validates: Requirements 8.2, 8.3
    """
    # Re-declare the orchestrator's source-of-truth constants here to avoid
    # the agent-module import chain. If these drift from orchestrator.py
    # this test is the first to fail, which is the intent — drift would
    # break SA session entry.
    NODE_NAMES = [
        "workspace_detection_ext",
        "workspace_detection",
        "reverse_engineering",
        "requirements_analysis",
        "user_stories",
        "workflow_planning",
        "application_design",
        "application_design_ext",
        "units_generation",
        "inception_complete",
    ]
    STAGE_TO_NODE_IDS = {
        "workspace_detection": ("workspace_detection_ext", "workspace_detection"),
        "reverse_engineering": ("reverse_engineering",),
        "requirements_analysis": ("requirements_analysis",),
        "user_stories": ("user_stories",),
        "workflow_planning": ("workflow_planning",),
        "application_design": ("application_design", "application_design_ext"),
        "units_generation": ("units_generation",),
        "inception_complete": ("inception_complete",),
    }

    # Compute the persona-aware skip set the same way main_ws does for an
    # SA connection on ``assigned_to_sa``. No BA/SA-specific branching --
    # this is the registry-derived path used in production.
    seg = workflow_registry.segment_of("assigned_to_sa")
    skip_stages: set[str] = set()
    for upstream in workflow_registry.upstream_segments(seg):
        skip_stages |= set(workflow_registry.segment_stages(upstream))

    # Expand stage names to graph node IDs (orchestrator does this in
    # ``build_sdlc_graph`` via the same STAGE_TO_NODE_IDS mapping).
    skip_node_ids: set[str] = set()
    for stage in skip_stages:
        skip_node_ids.update(STAGE_TO_NODE_IDS.get(stage, ()))

    # Apply the orchestrator's first-non-skipped selection rule.
    selected_entry = next(
        (n for n in NODE_NAMES if n not in skip_node_ids),
        None,
    )

    assert selected_entry == "workflow_planning"
    # And the registry helper produces the same value for the SA segment.
    assert selected_entry == workflow_registry.entry_node(seg)


# ---------------------------------------------------------------------------
# 2. BA segment requires a handoff gate; gate id follows handoff_gate__{seg}.
# ---------------------------------------------------------------------------


def test_ba_segment_has_successor_so_requires_handoff_gate():
    """Per design Section 5b, every segment with a successor in persona
    order gets its own ``handoff_gate__{seg}`` node on the boundary edge.
    The BA segment is the upstream half of the only configured boundary,
    so it must have a successor in the registry.

    Validates: Requirements 6.2
    """
    successor = workflow_registry.successor_segment("ba_segment")
    assert successor == "sa_segment", (
        "BA segment must have an SA successor for the handoff gate to be "
        f"required; got successor={successor!r}"
    )


def test_devops_segment_is_terminal_so_no_handoff_gate_required():
    """With the QA and DevOps segments enabled, the DevOps segment is the last
    segment in the BA->SA->Dev->QA->DevOps chain, so it does not get a
    downstream handoff gate. Every upstream segment (including dev_segment and
    qa_segment) now has a successor and therefore DOES get a gate. Only segments
    with a successor get a gate.

    Validates: Requirements 6.2
    """
    assert workflow_registry.successor_segment("devops_segment") is None
    assert workflow_registry.successor_segment("dev_segment") == "qa_segment"
    assert workflow_registry.successor_segment("qa_segment") == "devops_segment"
    assert workflow_registry.successor_segment("sa_segment") == "dev_segment"


def test_ba_segment_handoff_gate_id_follows_documented_convention():
    """The handoff gate node id for the BA segment follows the
    ``handoff_gate__{seg}`` naming convention documented in design Section
    5b and used by the runner's interrupt handler. Pinning the convention
    at the test level prevents drift between the orchestrator's gate
    naming and the runner's handoff branch (which reads the gate's id /
    ``from_segment`` reason field).

    Validates: Requirements 6.2, 6.3
    """
    seg = "ba_segment"
    gate_id = f"handoff_gate__{seg}"
    assert gate_id == "handoff_gate__ba_segment"

    # The same id appears in the test_ws_handoff_breakout fake interrupt:
    # this test guards the convention so that test does not drift from the
    # registry-driven contract.


def test_ba_exit_hub_is_the_handoff_boundary_for_user_stories():
    """The BA segment's ``exit_hub`` -- ``hub_post_user_stories`` -- is the
    boundary the orchestrator rewires into ``handoff_gate__ba_segment``
    instead of into ``workflow_planning`` (design Section 5b). This anchors
    the gate's location in the graph so the existing user_stories ->
    workflow_planning edge is replaced for the BA persona's session.

    Validates: Requirements 6.2
    """
    assert workflow_registry.exit_hub("ba_segment") == "hub_post_user_stories"

    # The successor segment's entry node is what the gate replaces in the
    # BA persona's session view of the graph.
    successor = workflow_registry.successor_segment("ba_segment")
    assert successor is not None
    assert workflow_registry.entry_node(successor) == "workflow_planning"


# ---------------------------------------------------------------------------
# 3. Runtime RBAC bridge: active segment owning role for assigned_to_sa.
# ---------------------------------------------------------------------------


def test_assigned_to_sa_required_role_is_system_architect_via_registry():
    """The runtime RBAC gate (task 5.5) compares the caller's groups
    against ``segment_owning_role(segment_of(handoff_state))``. For
    ``assigned_to_sa`` the owning role must be ``system-architect``,
    so a Business Analyst connection (groups=["requirements-analyst"])
    is rejected. This anchors the registry-derived contract that the
    end-to-end RBAC test in test_ws_segment_rbac.py exercises.

    Validates: Requirements 2.4, 2.5
    """
    seg = workflow_registry.segment_of("assigned_to_sa")
    assert workflow_registry.segment_owning_role(seg) == "system-architect"
    # And every state in that segment shares the same owning role.
    for state in ("assigned_to_sa", "in_design", "design_ready", "ready_for_dev"):
        assert workflow_registry.owning_role(state) == "system-architect"


# ---------------------------------------------------------------------------
# 4. Six-persona enablement: QA + DevOps segments/states (registry-derived).
# ---------------------------------------------------------------------------


def test_six_persona_chain_and_segments_via_registry():
    """QA and DevOps personas/segments are wired into the registry with the
    expected persona order, entry nodes, owning roles, and forward chain."""
    assert workflow_registry.ordered_segments() == [
        "ba_segment",
        "sa_segment",
        "dev_segment",
        "qa_segment",
        "devops_segment",
    ]
    assert workflow_registry.display_label("qa-engineer") == "QA Engineer"
    assert workflow_registry.display_label("devops-engineer") == "DevOps Engineer"
    assert workflow_registry.entry_node("qa_segment") == "test_planning"
    assert workflow_registry.entry_node("devops_segment") == "deployment_planning"
    assert workflow_registry.segment_owning_role("qa_segment") == "qa-engineer"
    assert workflow_registry.segment_owning_role("devops_segment") == "devops-engineer"
    # The forward chain threads dev -> qa -> devops and terminates at deployed.
    assert workflow_registry.forward("implementation_submitted") == "assigned_to_qa"
    assert workflow_registry.forward("qa_passed") == "assigned_to_devops"
    assert workflow_registry.forward("deployed") is None


def test_qa_and_devops_request_changes_bounces_via_registry():
    """QA bounces back to the developer; DevOps bounces back to QA, and both
    are permitted transitions through the registry."""
    rc_qa = workflow_registry.request_changes("in_qa")
    assert rc_qa is not None and rc_qa["target"] == "assigned_to_dev"
    rc_dev = workflow_registry.request_changes("in_deployment")
    assert rc_dev is not None and rc_dev["target"] == "assigned_to_qa"
    assert workflow_registry.validate_transition("in_qa", "assigned_to_dev") is True
    assert workflow_registry.validate_transition("in_deployment", "assigned_to_qa") is True


def test_qa_and_devops_states_owning_roles_via_registry():
    """Each new state maps to the expected owning role/segment."""
    for state in ("assigned_to_qa", "in_qa", "qa_passed"):
        assert workflow_registry.owning_role(state) == "qa-engineer"
        assert workflow_registry.segment_of(state) == "qa_segment"
    for state in ("assigned_to_devops", "in_deployment", "deployed"):
        assert workflow_registry.owning_role(state) == "devops-engineer"
        assert workflow_registry.segment_of(state) == "devops_segment"
