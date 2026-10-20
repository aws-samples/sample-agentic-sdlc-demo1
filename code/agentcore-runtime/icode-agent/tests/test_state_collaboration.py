"""Tests for the ``## Collaboration`` serialization in ``tools/state.py`` (task 3.2).

These cover the additive handoff-mirror serialization added to
``_state_to_markdown`` (design Section 6c, Req 9.2 / 11.4):

  * the new ``## Collaboration`` section renders the Handoff State / Owning Role
    bullets and the fixed-column ``### Assignment History`` table for a state
    that carries handoff data;
  * a state with no ``handoff`` section falls back to the documented defaults
    (``draft`` / ``requirements-analyst``);
  * pipe characters and newlines inside history cells are sanitized so they
    cannot break the table layout;
  * the additive section does NOT disturb the existing sections / parser
    tokens — round-tripping the non-handoff fields through the existing
    ``_markdown_to_state`` still works (Req 11.4).

``tools.state`` imports ``strands`` (not installed in the test environment),
so we stub the tiny surface it touches (``strands.tools.tool`` and
``strands.tools.mcp.MCPClient``) before importing. The functions under test
are pure and do not use those symbols.

Validates: Requirements 9.2, 11.4
"""

import os
import sys
import types

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
# Helpers
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


def _section(md: str, header: str) -> str:
    """Return the text of a ``## header`` section (up to the next ``## ``)."""
    lines = md.split("\n")
    out = []
    capturing = False
    for line in lines:
        if line.strip() == header:
            capturing = True
            out.append(line)
            continue
        if capturing and line.startswith("## ") and line.strip() != header:
            break
        if capturing:
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Tests: section rendering with data
# ---------------------------------------------------------------------------

def test_collaboration_section_renders_bullets_and_history_with_data():
    handoff = {
        "handoff_state": "assigned_to_sa",
        "owning_role": "system-architect",
        "assignment_history": [
            {
                "from": "draft",
                "to": "requirements_ready",
                "actor": "ba@example.com",
                "timestamp": "2026-06-03T12:10:00Z",
                "note": "requirements ready",
            },
            {
                "from": "requirements_ready",
                "to": "assigned_to_sa",
                "actor": "ba@example.com",
                "timestamp": "2026-06-03T12:15:00Z",
                "note": "assigned",
            },
        ],
    }
    md = _state_to_markdown(_base_state(handoff))

    assert "## Collaboration" in md
    assert "- **Handoff State**: assigned_to_sa" in md
    assert "- **Owning Role**: system-architect" in md
    assert "### Assignment History" in md
    assert "| From | To | Actor | Timestamp | Note |" in md
    assert "|---|---|---|---|---|" in md
    assert (
        "| draft | requirements_ready | ba@example.com | 2026-06-03T12:10:00Z | requirements ready |"
        in md
    )
    assert (
        "| requirements_ready | assigned_to_sa | ba@example.com | 2026-06-03T12:15:00Z | assigned |"
        in md
    )


def test_collaboration_section_appended_after_current_status():
    md = _state_to_markdown(_base_state({"handoff_state": "draft", "owning_role": "requirements-analyst", "assignment_history": []}))
    assert "## Current Status" in md
    assert "## Collaboration" in md
    # Additive placement: Collaboration must come AFTER Current Status (Req 11.4).
    assert md.index("## Current Status") < md.index("## Collaboration")


# ---------------------------------------------------------------------------
# Tests: defaults when handoff missing / partial
# ---------------------------------------------------------------------------

def test_collaboration_defaults_when_handoff_absent():
    md = _state_to_markdown(_base_state(handoff=None))
    assert "- **Handoff State**: draft" in md
    assert "- **Owning Role**: requirements-analyst" in md
    # Table header + separator present even with no history rows.
    collab = _section(md, "## Collaboration")
    assert "| From | To | Actor | Timestamp | Note |" in collab
    assert "|---|---|---|---|---|" in collab
    # No data rows beyond the header/separator.
    data_rows = [
        ln for ln in collab.split("\n")
        if ln.startswith("|") and ln.strip() not in (
            "| From | To | Actor | Timestamp | Note |", "|---|---|---|---|---|"
        )
    ]
    assert data_rows == []


def test_collaboration_defaults_when_fields_empty_or_none():
    handoff = {"handoff_state": "", "owning_role": None, "assignment_history": []}
    md = _state_to_markdown(_base_state(handoff))
    assert "- **Handoff State**: draft" in md
    assert "- **Owning Role**: requirements-analyst" in md


# ---------------------------------------------------------------------------
# Tests: cell sanitization
# ---------------------------------------------------------------------------

def test_history_cell_pipes_and_newlines_sanitized():
    handoff = {
        "handoff_state": "requirements_ready",
        "owning_role": "requirements-analyst",
        "assignment_history": [
            {
                "from": "draft",
                "to": "requirements_ready",
                "actor": "ba@example.com",
                "timestamp": "2026-06-03T12:10:00Z",
                "note": "needs A | B\nand C",
            }
        ],
    }
    md = _state_to_markdown(_base_state(handoff))
    collab = _section(md, "## Collaboration")
    row = [ln for ln in collab.split("\n") if ln.startswith("| draft |")][0]
    # Exactly 5 columns => 6 pipe delimiters; the embedded pipe must be gone.
    assert row.count("|") == 6
    assert "needs A / B and C" in row


def test_history_missing_fields_render_placeholder():
    handoff = {
        "handoff_state": "draft",
        "owning_role": "requirements-analyst",
        "assignment_history": [
            {"to": "draft", "actor": "ba@example.com", "timestamp": "2026-06-03T12:00:00Z"}
        ],
    }
    md = _state_to_markdown(_base_state(handoff))
    collab = _section(md, "## Collaboration")
    row = [ln for ln in collab.split("\n") if "| draft |" in ln and "ba@example.com" in ln][0]
    # from + note missing -> placeholder em dash; still 5 columns.
    assert row.count("|") == 6
    assert "| — |" in row


# ---------------------------------------------------------------------------
# Tests: existing tokens / sections preserved (Req 11.4)
# ---------------------------------------------------------------------------

def test_existing_sections_unchanged_and_roundtrip_preserved():
    handoff = {
        "handoff_state": "assigned_to_sa",
        "owning_role": "system-architect",
        "assignment_history": [
            {"from": "draft", "to": "requirements_ready", "actor": "ba@example.com",
             "timestamp": "2026-06-03T12:10:00Z", "note": "ready"}
        ],
    }
    state = _base_state(handoff)
    md = _state_to_markdown(state)

    # Existing headers/tokens still present and intact.
    for token in (
        "# AI-DLC State Tracking",
        "## Project Information",
        "## Workspace State",
        "## Extension Configuration",
        "## Execution Plan Summary",
        "## Units of Work",
        "## Stage Progress",
        "### INCEPTION PHASE",
        "## Current Status",
    ):
        assert token in md, token

    # The existing parser must still recover the non-handoff fields unchanged
    # (the appended Collaboration section does not disturb it).
    parsed = _markdown_to_state(md)
    assert parsed["project"]["type"] == "greenfield"
    assert parsed["project"]["selected_project"] == "online_bookstore"
    assert parsed["project"]["languages"] == ["python", "typescript"]
    assert parsed["project"]["build_system"] == "uv"
    assert parsed["project"]["project_structure"] == "monolith"
    assert parsed["current_phase"] == "inception"
    assert parsed["current_stage"] == "requirements_analysis"
    assert parsed["stage_progress"]["workspace_detection"] == "completed"
    assert parsed["extensions"]["enabled_status"] == {"security": True, "performance": False}
