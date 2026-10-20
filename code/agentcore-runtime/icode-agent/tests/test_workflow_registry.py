"""Unit + property tests for the Persona/Workflow Registry (task 1.5).

Covers three things from the design (Section 3a/3b, Property 11, Testing
Strategy):

  1. Closure validation (``WorkflowRegistry.validate`` / ``validate_registry``)
     REJECTS malformed registries: dangling ``forward``/``request_changes``
     targets, unknown owning role, unknown segment, and forward-edge cycles.

  2. A synthetic *three-persona* registry validates and yields the correct
     ``ordered_segments`` / ``upstream_segments`` / ``entry_node`` — proving the
     registry is extensible by adding data only, with no new transition logic
     (Property 11, Req 4.7).

  3. Property-based (Hypothesis) reachability: starting from ``initial_state``,
     only forward edges plus the configured request-changes edges are ever
     reachable, and no transition (even with an arbitrary garbage target) ever
     yields a state outside the registry's configured set — checked against
     BOTH the canonical BA->SA registry and the synthetic 3-persona registry.

The tests import the real ``workflow_registry`` module and exercise it through
its public surface (``WorkflowRegistry``, ``validate_registry``,
``validate_transition`` and the lookup helpers). They deliberately avoid
re-implementing ``validate_transition``'s body and asserting equality with it
(mirror testing); instead they assert independent structural invariants
(closure rejection, reachable-set == forward-chain, no out-of-set acceptance).

Validates: Requirements 4.3, 4.4, 4.7
"""

import copy
import os
import sys

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Make the runtime module importable regardless of pytest's rootdir/cwd:
# workflow_registry.py lives one directory up from this tests/ package.
_ICODE_AGENT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ICODE_AGENT_DIR not in sys.path:
    sys.path.insert(0, _ICODE_AGENT_DIR)

import workflow_registry as wr  # noqa: E402
from workflow_registry import (  # noqa: E402
    RegistryValidationError,
    WorkflowRegistry,
    validate_registry,
)


# ---------------------------------------------------------------------------
# Registry fixtures / builders
# ---------------------------------------------------------------------------


def _ba_sa_registry_data() -> dict:
    """Return a fresh copy of the canonical BA->SA registry loaded from disk.

    Uses the real ``workflow_registry.json`` shipped beside the module so the
    tests exercise the production registry, not a hand-rolled stand-in.
    """
    return copy.deepcopy(wr.REGISTRY.data)


def _three_persona_registry_data() -> dict:
    """A synthetic, well-formed registry with a THIRD persona appended.

    Personas: requirements-analyst (1) -> system-architect (2) ->
    software-developer (3). The forward chain threads through all three
    segments and terminates; request-changes edges bounce back to earlier
    states on the chain. This proves the BA+SA implementation is extensible
    purely by adding data (design Property 11 / Req 4.7).
    """
    return {
        "initial_state": "draft",
        "personas": {
            "requirements-analyst": {"display": "Business Analyst", "order": 1},
            "system-architect": {"display": "Solution Architect", "order": 2},
            "software-developer": {"display": "Software Developer", "order": 3},
        },
        "segments": {
            "ba_segment": {
                "owning_role": "requirements-analyst",
                "stages": ["requirements_analysis", "user_stories"],
                "entry_node": "workspace_detection_ext",
                "exit_hub": "hub_post_user_stories",
            },
            "sa_segment": {
                "owning_role": "system-architect",
                "stages": ["application_design", "units_generation"],
                "entry_node": "workflow_planning",
                "exit_hub": "hub_post_units_generation",
            },
            "dev_segment": {
                "owning_role": "software-developer",
                "stages": ["implementation", "code_review"],
                "entry_node": "development_planning",
                "exit_hub": "hub_post_code_review",
            },
        },
        "states": {
            "draft": {
                "owning_role": "requirements-analyst",
                "segment": "ba_segment",
                "forward": "requirements_ready",
            },
            "requirements_ready": {
                "owning_role": "requirements-analyst",
                "segment": "ba_segment",
                "forward": "assigned_to_sa",
            },
            "assigned_to_sa": {
                "owning_role": "system-architect",
                "segment": "sa_segment",
                "forward": "in_design",
            },
            "in_design": {
                "owning_role": "system-architect",
                "segment": "sa_segment",
                "forward": "design_ready",
            },
            "design_ready": {
                "owning_role": "system-architect",
                "segment": "sa_segment",
                "forward": "assigned_to_dev",
            },
            "assigned_to_dev": {
                "owning_role": "software-developer",
                "segment": "dev_segment",
                "forward": "in_development",
            },
            "in_development": {
                "owning_role": "software-developer",
                "segment": "dev_segment",
                "forward": "dev_complete",
            },
            "dev_complete": {
                "owning_role": "software-developer",
                "segment": "dev_segment",
                "forward": None,
            },
        },
        "request_changes": {
            "assigned_to_sa": {
                "target": "requirements_ready",
                "label": "Changes requested",
            },
            "assigned_to_dev": {
                "target": "design_ready",
                "label": "Changes requested",
            },
        },
    }


# Factories used to parametrize the property tests over BOTH registries.
REGISTRY_FACTORIES = [
    pytest.param(_ba_sa_registry_data, id="ba_sa"),
    pytest.param(_three_persona_registry_data, id="three_persona"),
]


# ---------------------------------------------------------------------------
# Independent reference helpers (NOT copies of validate_transition's body)
# ---------------------------------------------------------------------------


def _forward_chain(reg: WorkflowRegistry) -> list:
    """Return the ordered list of states reached by following ``forward`` only.

    Independent of ``validate_transition`` — it walks the single ``forward``
    pointer from ``initial_state`` until a terminal (``None``) state, guarding
    against cycles so a malformed registry can't hang the helper.
    """
    chain: list = []
    seen: set = set()
    cursor = reg.initial_state()
    while cursor is not None and cursor not in seen:
        chain.append(cursor)
        seen.add(cursor)
        cursor = reg.forward(cursor)
    return chain


def _reachable_closure(reg: WorkflowRegistry) -> set:
    """States reachable from ``initial_state`` using only permitted transitions.

    BFS where the candidate successors of each state are the *configured*
    states ``t`` for which ``validate_transition(state, t)`` is True. This
    treats ``validate_transition`` as a black box edge oracle and computes the
    transitive closure independently.
    """
    states = list(reg.data["states"])
    start = reg.initial_state()
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for target in states:
            if target not in seen and reg.validate_transition(current, target):
                seen.add(target)
                frontier.append(target)
    return seen


# ===========================================================================
# 1. Closure validation rejects malformed registries
# ===========================================================================


def test_canonical_ba_sa_registry_validates():
    """The shipped BA->SA registry passes closure validation (control case)."""
    validate_registry(WorkflowRegistry(_ba_sa_registry_data()))


def test_reject_dangling_forward_target():
    """A ``forward`` pointer to an undefined state is rejected."""
    data = _ba_sa_registry_data()
    data["states"]["draft"]["forward"] = "nonexistent_state"
    with pytest.raises(RegistryValidationError, match="forward target"):
        validate_registry(data)


def test_reject_dangling_request_changes_target():
    """A ``request_changes.target`` pointing at an undefined state is rejected."""
    data = _ba_sa_registry_data()
    data["request_changes"]["assigned_to_sa"]["target"] = "ghost_state"
    with pytest.raises(RegistryValidationError, match="request_changes target"):
        validate_registry(data)


def test_reject_request_changes_source_not_a_state():
    """A ``request_changes`` keyed on an undefined source state is rejected."""
    data = _ba_sa_registry_data()
    data["request_changes"]["not_a_state"] = {
        "target": "requirements_ready",
        "label": "x",
    }
    with pytest.raises(RegistryValidationError, match="request_changes source"):
        validate_registry(data)


def test_reject_unknown_owning_role_on_state():
    """A state whose ``owning_role`` is not a defined persona is rejected."""
    data = _ba_sa_registry_data()
    data["states"]["draft"]["owning_role"] = "ghost-role"
    with pytest.raises(RegistryValidationError, match="owning_role"):
        validate_registry(data)


def test_reject_unknown_owning_role_on_segment():
    """A segment whose ``owning_role`` is not a defined persona is rejected."""
    data = _ba_sa_registry_data()
    data["segments"]["ba_segment"]["owning_role"] = "ghost-role"
    with pytest.raises(RegistryValidationError, match="owning_role"):
        validate_registry(data)


def test_reject_unknown_segment_on_state():
    """A state referencing an undefined segment is rejected."""
    data = _ba_sa_registry_data()
    data["states"]["draft"]["segment"] = "ghost_segment"
    with pytest.raises(RegistryValidationError, match="segment"):
        validate_registry(data)


def test_reject_forward_cycle():
    """A cycle in the ``forward`` chain from ``initial_state`` is rejected."""
    data = _ba_sa_registry_data()
    # Point the terminal state back at the head to create a cycle.
    data["states"]["ready_for_dev"]["forward"] = "draft"
    with pytest.raises(RegistryValidationError, match="cycle"):
        validate_registry(data)


def test_reject_missing_initial_state():
    """A registry lacking ``initial_state`` is rejected."""
    data = _ba_sa_registry_data()
    del data["initial_state"]
    with pytest.raises(RegistryValidationError, match="initial_state"):
        validate_registry(data)


def test_reject_initial_state_not_defined():
    """An ``initial_state`` that is not among the defined states is rejected."""
    data = _ba_sa_registry_data()
    data["initial_state"] = "not_real"
    with pytest.raises(RegistryValidationError, match="initial_state"):
        validate_registry(data)


# ===========================================================================
# 2. Synthetic three-persona registry: extensibility (Property 11)
# ===========================================================================


def test_three_persona_registry_validates():
    """The synthetic three-persona registry passes closure validation."""
    validate_registry(WorkflowRegistry(_three_persona_registry_data()))


def test_three_persona_ordered_segments():
    """``ordered_segments`` sorts by owning persona order across all three."""
    reg = WorkflowRegistry(_three_persona_registry_data())
    assert reg.ordered_segments() == ["ba_segment", "sa_segment", "dev_segment"]


def test_three_persona_upstream_segments():
    """``upstream_segments`` returns earlier-ranked segments, in order."""
    reg = WorkflowRegistry(_three_persona_registry_data())
    assert reg.upstream_segments("ba_segment") == []
    assert reg.upstream_segments("sa_segment") == ["ba_segment"]
    assert reg.upstream_segments("dev_segment") == ["ba_segment", "sa_segment"]


def test_three_persona_entry_node():
    """``entry_node`` resolves the configured entry for each segment."""
    reg = WorkflowRegistry(_three_persona_registry_data())
    assert reg.entry_node("ba_segment") == "workspace_detection_ext"
    assert reg.entry_node("sa_segment") == "workflow_planning"
    assert reg.entry_node("dev_segment") == "development_planning"


def test_three_persona_new_segment_handled_by_same_transition_logic():
    """The appended dev segment's forwards/request-changes work unchanged.

    Proves the transition logic needs no modification for a new persona: the
    new forward edge and the new request-changes bounce are both honored by the
    same ``validate_transition`` (Req 4.7 extensibility).
    """
    reg = WorkflowRegistry(_three_persona_registry_data())
    # New forward edge into the dev segment.
    assert reg.validate_transition("design_ready", "assigned_to_dev") is True
    # New request-changes bounce from the dev segment back to design_ready.
    assert reg.validate_transition("assigned_to_dev", "design_ready") is True
    # A non-adjacent / non-configured jump is still rejected.
    assert reg.validate_transition("draft", "assigned_to_dev") is False


# ===========================================================================
# 3. validate_transition — direct example checks (Req 4.3, 4.4)
# ===========================================================================


@pytest.mark.parametrize("factory", REGISTRY_FACTORIES)
def test_every_forward_edge_is_accepted(factory):
    """Each state's immediate forward successor is a permitted transition."""
    reg = WorkflowRegistry(factory())
    for state in reg.data["states"]:
        fwd = reg.forward(state)
        if fwd is not None:
            assert reg.validate_transition(state, fwd) is True, (
                f"forward {state} -> {fwd} should be permitted"
            )


@pytest.mark.parametrize("factory", REGISTRY_FACTORIES)
def test_every_configured_request_changes_edge_is_accepted(factory):
    """Each configured request-changes reassignment is a permitted transition."""
    reg = WorkflowRegistry(factory())
    for source, spec in reg.data.get("request_changes", {}).items():
        assert reg.validate_transition(source, spec["target"]) is True, (
            f"request-changes {source} -> {spec['target']} should be permitted"
        )


@pytest.mark.parametrize("factory", REGISTRY_FACTORIES)
def test_terminal_state_has_no_permitted_transition(factory):
    """A terminal state (no forward, no request-changes) permits no transition."""
    reg = WorkflowRegistry(factory())
    states = list(reg.data["states"])
    for state in states:
        if reg.forward(state) is None and reg.request_changes(state) is None:
            for target in states:
                assert reg.validate_transition(state, target) is False, (
                    f"terminal {state} should not permit -> {target}"
                )


def test_unknown_current_state_is_rejected():
    """A transition from an unknown ``current`` state is never permitted."""
    reg = WorkflowRegistry(_ba_sa_registry_data())
    assert reg.validate_transition("not_a_state", "draft") is False
    assert reg.validate_transition("not_a_state", "anything") is False


# ===========================================================================
# 4. Property-based reachability (Hypothesis) — Req 4.3, 4.4, 4.7
#
#    Named property (design "State-machine reachability"): from initial_state,
#    only forward + configured request-changes edges are reachable, and no
#    transition yields a state outside the configured set — for both registries.
# ===========================================================================


@pytest.mark.parametrize("factory", REGISTRY_FACTORIES)
def test_reachable_closure_equals_forward_chain(factory):
    """**Validates: Requirements 4.3, 4.4, 4.7**

    The set of states reachable from ``initial_state`` via permitted
    transitions equals exactly the forward chain. request-changes edges only
    bounce back to states already on the chain, so they open no new states;
    and every state on the chain is reachable. This is the structural form of
    "only forward + configured request-changes edges are reachable".
    """
    reg = WorkflowRegistry(factory())
    chain = _forward_chain(reg)
    chain_set = set(chain)
    configured = set(reg.data["states"])

    reachable = _reachable_closure(reg)

    # No reachable state is outside the configured set.
    assert reachable <= configured
    # Reachable set is exactly the forward chain (request-changes adds nothing).
    assert reachable == chain_set
    # For these two registries every configured state lies on the chain.
    assert chain_set == configured


@pytest.mark.parametrize("factory", REGISTRY_FACTORIES)
@given(data=st.data())
@settings(max_examples=300)
def test_validate_transition_never_accepts_out_of_set_target(factory, data):
    """**Validates: Requirements 4.3, 4.4, 4.7**

    For an arbitrary ``current`` (drawn from configured states) and an
    arbitrary ``target`` (a configured state, a deliberately out-of-set token,
    or random text), if ``validate_transition`` ACCEPTS the move then ``target``
    MUST be a configured state. No transition ever yields an out-of-set state.
    """
    reg = WorkflowRegistry(factory())
    configured = list(reg.data["states"])

    current = data.draw(st.sampled_from(configured), label="current")
    target = data.draw(
        st.one_of(
            st.sampled_from(configured),
            st.sampled_from(["", "draft_", "DRAFT", "ready_for_dev2", "ghost"]),
            st.text(max_size=24),
        ),
        label="target",
    )

    if reg.validate_transition(current, target):
        assert target in configured, (
            f"validate_transition accepted {current!r} -> {target!r} "
            f"but {target!r} is not a configured state"
        )


@pytest.mark.parametrize("factory", REGISTRY_FACTORIES)
@given(data=st.data())
@settings(max_examples=300)
def test_random_walk_stays_on_forward_chain(factory, data):
    """**Validates: Requirements 4.3, 4.4, 4.7**

    A random walk that only ever follows permitted transitions, starting from
    ``initial_state``, stays within the forward chain at every step (never
    reaching an out-of-set state). Uses Hypothesis to explore arbitrary
    sequences of permitted moves, including request-changes bounces.
    """
    reg = WorkflowRegistry(factory())
    configured = list(reg.data["states"])
    chain_set = set(_forward_chain(reg))

    current = reg.initial_state()
    assert current in chain_set

    steps = data.draw(st.integers(min_value=0, max_value=25), label="steps")
    for _ in range(steps):
        permitted = sorted(
            t for t in configured if reg.validate_transition(current, t)
        )
        if not permitted:
            break
        current = data.draw(st.sampled_from(permitted), label="next")
        # Independent invariant: permitted moves never leave the forward chain
        # (and therefore never leave the configured set).
        assert current in chain_set
