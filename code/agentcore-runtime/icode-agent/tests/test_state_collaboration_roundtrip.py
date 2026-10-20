"""Property-based round-trip test for the ``## Collaboration`` handoff mirror
serialization in ``tools/state.py`` (task 3.4).

This implements design **Property 6: Serialization round-trip** (design Section
"Correctness Properties", Testing Strategy):

    For every valid ``handoff`` section,
    ``_markdown_to_state(_state_to_markdown(s))`` yields equivalent
    ``handoff_state``, ``owning_role``, and ``assignment_history``.

    Validates: Requirements 9.4

The test asserts the genuine round-trip invariant against the REAL
``_state_to_markdown`` / ``_markdown_to_state`` functions — it does not
re-implement either side of the serializer (no mirror testing). The "expected"
value is simply the original input, which is exactly what the round-trip
property requires.

To keep the property faithful we generate *already-sanitized* field values
(design Section 6c documents the write-side sanitization: pipe ``|`` -> ``/``,
newlines -> spaces, ``None``/empty -> the placeholder ``—`` which parses back to
``None``). By drawing history-cell values that are either ``None`` or text that
is unaffected by that sanitization (non-empty, no pipes, no newlines, no
leading/trailing whitespace, not the placeholder), the round-trip is exact and
the asserted equivalence is unambiguous (Option (a) in the task description).

``tools.state`` imports ``strands`` (not installed in the test environment), so
we install the same minimal stub used by ``test_state_collaboration.py`` before
importing. The functions under test are pure and do not use those symbols.

Validates: Requirements 9.4
"""

import os
import string
import sys
import types

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the icode-agent dir importable (one level up from tests/).
_ICODE_AGENT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ICODE_AGENT_DIR not in sys.path:
    sys.path.insert(0, _ICODE_AGENT_DIR)


def _install_strands_stub() -> None:
    """Install a minimal ``strands`` stub so ``tools.state`` can be imported."""
    if "strands" not in sys.modules:
        strands = types.ModuleType("strands")
        sys.modules["strands"] = strands

    if "strands.tools" not in sys.modules:
        strands_tools = types.ModuleType("strands.tools")

        def _tool(*args, **kwargs):  # supports @tool and @tool(context=True)
            if args and callable(args[0]) and not kwargs:
                return args[0]

            def _decorator(fn):
                return fn

            return _decorator

        strands_tools.tool = _tool
        sys.modules["strands.tools"] = strands_tools
        sys.modules["strands"].tools = strands_tools

    if "strands.tools.mcp" not in sys.modules:
        strands_tools_mcp = types.ModuleType("strands.tools.mcp")
        strands_tools_mcp.MCPClient = object
        sys.modules["strands.tools.mcp"] = strands_tools_mcp


_install_strands_stub()

from tools.state import _markdown_to_state, _state_to_markdown  # noqa: E402


# ---------------------------------------------------------------------------
# Base state helper (mirrors test_state_collaboration.py conventions)
# ---------------------------------------------------------------------------

def _base_state(handoff=None) -> dict:
    """A minimal but realistic SharedState-shaped dict for serialization."""
    state = {
        "project": {
            "type": "greenfield",
            "start_date": "2026-06-03T12:00:00Z",
            "workspace_root": "/work",
            "languages": ["python", "typescript"],
            "build_system": "uv",
            "project_structure": "monolith",
            "selected_project": "online_bookstore",
            "is_new_project": True,
        },
        "brownfield": False,
        "stage_progress": {
            "workspace_detection": "completed",
            "requirements_analysis": "in_progress",
        },
        "current_stage": "requirements_analysis",
        "current_phase": "inception",
        "extensions": {"enabled_status": {"security": True, "performance": False}},
        "execution_plan": {"stages_to_execute": ["requirements_analysis"], "stages_to_skip": {}},
        "depth_level": "standard",
        "units": {"items": [], "current_unit_index": 0, "completed_units": []},
    }
    if handoff is not None:
        state["handoff"] = handoff
    return state


# ---------------------------------------------------------------------------
# Hypothesis strategies for VALID handoff sections
# ---------------------------------------------------------------------------

# The six registry states and the two registry roles, so the property is
# exercised against realistic values in addition to arbitrary safe text.
_KNOWN_STATES = [
    "draft",
    "requirements_ready",
    "assigned_to_sa",
    "in_design",
    "design_ready",
    "ready_for_dev",
]
_KNOWN_ROLES = ["requirements-analyst", "system-architect"]

# Placeholder the serializer emits for None/empty cells (parses back to None).
_PLACEHOLDER = "—"

# A "safe" alphabet for text that must survive serialization unchanged:
# no pipe ``|`` (would become ``/``) and no newline/CR (would become a space).
# Includes identifier-like characters plus realistic punctuation/spacing.
_SAFE_CHARS = string.ascii_letters + string.digits + " -_.@:/()#+,"


def _safe_text(max_size: int):
    """Text that is unaffected by the documented sanitization.

    Drawn from ``_SAFE_CHARS`` (no pipes, no newlines), stripped of any
    leading/trailing whitespace (so the parser's ``strip()`` is a no-op), and
    constrained to be non-empty and not equal to the ``—`` placeholder.
    """
    return (
        st.text(alphabet=_SAFE_CHARS, min_size=1, max_size=max_size)
        .map(str.strip)
        .filter(lambda s: s != "" and s != _PLACEHOLDER)
    )


# handoff_state / owning_role bullet values are NOT cell-sanitized; they are
# read back via ``split(":", 1)[1].strip()``. Drawing from the registry plus
# safe text keeps the round-trip exact and the property meaningful.
_handoff_state_strategy = st.one_of(st.sampled_from(_KNOWN_STATES), _safe_text(20))
_owning_role_strategy = st.one_of(st.sampled_from(_KNOWN_ROLES), _safe_text(20))

# Each assignment-history field is either None (-> ``—`` -> None) or safe text
# that survives the round-trip byte-for-byte.
_field_strategy = st.one_of(st.none(), _safe_text(24))

_entry_strategy = st.fixed_dictionaries(
    {
        "from": _field_strategy,
        "to": _field_strategy,
        "actor": _field_strategy,
        "timestamp": _field_strategy,
        "note": _field_strategy,
    }
)

_handoff_strategy = st.fixed_dictionaries(
    {
        "handoff_state": _handoff_state_strategy,
        "owning_role": _owning_role_strategy,
        "assignment_history": st.lists(_entry_strategy, max_size=6),
    }
)


# ---------------------------------------------------------------------------
# Property: round-trip reproduces the handoff section (Property 6 / Req 9.4)
# ---------------------------------------------------------------------------

@given(handoff=_handoff_strategy)
@settings(max_examples=300)
def test_handoff_section_survives_serialize_parse_roundtrip(handoff):
    """**Validates: Requirements 9.4**

    For an arbitrary valid ``handoff`` section,
    ``_markdown_to_state(_state_to_markdown(s))["handoff"]`` reproduces
    equivalent ``handoff_state``, ``owning_role``, and ``assignment_history``.
    """
    md = _state_to_markdown(_base_state(handoff))
    parsed = _markdown_to_state(md)["handoff"]

    assert parsed["handoff_state"] == handoff["handoff_state"]
    assert parsed["owning_role"] == handoff["owning_role"]
    # Faithful equivalence: None<->None preserved, sanitized-safe text preserved.
    assert parsed["assignment_history"] == handoff["assignment_history"]


# ---------------------------------------------------------------------------
# Fixed example: the realistic seed + transition history
# ---------------------------------------------------------------------------

def test_roundtrip_fixed_seed_and_transition_history():
    """**Validates: Requirements 9.4**

    A concrete, realistic handoff section — the seed entry (``from=None``) plus
    a forward transition entry — round-trips to an equivalent handoff section,
    with the ``None`` ``from`` preserved across the ``—`` placeholder mapping.
    """
    handoff = {
        "handoff_state": "assigned_to_sa",
        "owning_role": "system-architect",
        "assignment_history": [
            {
                "from": None,
                "to": "draft",
                "actor": "ba@example.com",
                "timestamp": "2026-06-03T12:00:00Z",
                "note": "created",
            },
            {
                "from": "requirements_ready",
                "to": "assigned_to_sa",
                "actor": "ba@example.com",
                "timestamp": "2026-06-03T12:15:00Z",
                "note": "assigned to SA",
            },
        ],
    }

    parsed = _markdown_to_state(_state_to_markdown(_base_state(handoff)))["handoff"]

    assert parsed["handoff_state"] == "assigned_to_sa"
    assert parsed["owning_role"] == "system-architect"
    assert parsed["assignment_history"] == handoff["assignment_history"]
    # The seed entry's absent ``from`` survives as None (placeholder mapping).
    assert parsed["assignment_history"][0]["from"] is None
