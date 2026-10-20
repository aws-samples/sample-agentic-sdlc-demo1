"""State persistence tools for the AI-SDLC Multi-Agent System.

Provides tools to persist SharedState to ``{session_id}/aidlc-docs/aidlc-state.md``
in markdown format via the MCP Gateway, load it back, and update stage progress.

Operations are routed through the MCP Gateway to S3 via an MCPClient stored
in ``invocation_state["_mcp_client"]``.  The session ID (used as the S3 key
prefix) is read from ``invocation_state["_session_id"]``.
"""

import logging
import posixpath

from strands.tools import tool

from shared_state import STAGE_NAMES, initialize_shared_state
from tools.gateway import call_tool

logger = logging.getLogger(__name__)

# Private keys that should NOT be serialised into the state markdown.
# These contain non-serializable runtime objects (WebSocket, MCP client,
# event loop, etc.) that are injected per-connection.
_PRIVATE_KEYS = {
    "_mcp_client", "_session_id", "_agentcore_session_id",
    "_session_manager", "_websocket", "_ws_event_loop", "_ws_mode",
}

# Stages belonging to each phase, used by mark_stage_complete.
_INCEPTION_STAGES = {
    "workspace_detection", "reverse_engineering", "requirements_analysis",
    "user_stories", "workflow_planning", "application_design",
    "units_generation", "inception_complete",
}
# Construction stages are disabled for the inception-only flow.
# _CONSTRUCTION_STAGES = {
#     "functional_design", "nfr_requirements", "nfr_design",
#     "infrastructure_design", "code_generation", "build_and_test",
# }
_CONSTRUCTION_STAGES: set[str] = set()

# S3 key suffix for the state file.
_STATE_S3_SUFFIX = "aidlc-docs/aidlc-state.md"


def _build_state_s3_key(session_id: str) -> str:
    """Return the S3 key for the state file given a session ID."""
    return f"{session_id}/{_STATE_S3_SUFFIX}"


# ---------------------------------------------------------------------------
# Gateway helpers (same patterns as file_io.py)
# ---------------------------------------------------------------------------

def _raise_on_gateway_error(result: dict, context_msg: str = "") -> None:
    """Raise ``RuntimeError`` when the Gateway returns an error response."""
    is_error = (
        (isinstance(result, dict) and result.get("isError"))
        or (isinstance(result, dict) and result.get("status") == "error")
    )
    if is_error:
        error_text = ""
        content = result.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    error_text = item["text"]
                    break
        if not error_text:
            error_text = str(result)
        prefix = f"{context_msg}: " if context_msg else ""
        raise RuntimeError(f"{prefix}{error_text}")


def _extract_text_content(result) -> str | None:
    """Extract text content from a Gateway response, or return ``None``."""
    if isinstance(result, dict):
        content_list = result.get("content", [])
        if isinstance(content_list, list):
            for item in content_list:
                if isinstance(item, dict) and "text" in item:
                    return item["text"]
        if isinstance(result.get("content"), str):
            return result["content"]
    if isinstance(result, str):
        return result
    return None


def _is_not_found_error(result: dict) -> bool:
    """Return ``True`` if *result* is a Gateway "not found" error."""
    is_error = (
        (isinstance(result, dict) and result.get("isError"))
        or (isinstance(result, dict) and result.get("status") == "error")
    )
    if not is_error:
        return False
    error_text = ""
    content = result.get("content", [])
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and "text" in item:
                error_text = item["text"]
                break
    if not error_text:
        error_text = str(result)
    lower = error_text.lower()
    return "not found" in lower or "nosuchkey" in lower or "404" in error_text


def _persist_via_gateway(mcp_client, session_id: str, md_content: str) -> None:
    """Write *md_content* to S3 via the Gateway ``write_file`` tool."""
    s3_key = _build_state_s3_key(session_id)

    # Ensure parent folder prefix exists
    parent = posixpath.dirname(s3_key)
    if parent:
        folder_result = call_tool(mcp_client, "create_folder", {"path": parent + "/"})
        _raise_on_gateway_error(folder_result, f"create_folder for {parent}")

    write_result = call_tool(mcp_client, "write_file", {"path": s3_key, "content": md_content})
    _raise_on_gateway_error(write_result, f"write_file for {s3_key}")


def _load_via_gateway(mcp_client, session_id: str) -> str | None:
    """Read the state markdown from S3 via the Gateway ``read_file`` tool.

    Returns the content string, or ``None`` if the file does not exist.
    """
    s3_key = _build_state_s3_key(session_id)
    read_result = call_tool(mcp_client, "read_file", {"path": s3_key})

    # Handle not-found gracefully
    if _is_not_found_error(read_result):
        return None

    # Propagate other errors
    _raise_on_gateway_error(read_result, f"read_file for {s3_key}")

    return _extract_text_content(read_result)


# ---------------------------------------------------------------------------
# mark_stage_complete
# ---------------------------------------------------------------------------

def mark_stage_complete(state: dict, stage_name: str) -> None:
    """Mark a stage as completed, update current_stage/phase, and persist via Gateway.

    Uses ``state["_mcp_client"]`` and ``state["_session_id"]`` to write
    ``aidlc-state.md`` to S3 through the MCP Gateway.

    Args:
        state: The live SharedState dictionary (must contain ``_mcp_client``
               and ``_session_id``).
        stage_name: The stage to mark as completed.
    """
    state.get("stage_progress", {})[stage_name] = "completed"
    state["current_stage"] = stage_name

    if stage_name in _INCEPTION_STAGES:
        state["current_phase"] = "inception"
    elif stage_name in _CONSTRUCTION_STAGES:
        state["current_phase"] = "construction"

    # Persist to S3 via Gateway
    mcp_client = state.get("_mcp_client")
    session_id = state.get("_session_id")

    if mcp_client is not None and session_id:
        md_content = _state_to_markdown(state)
        _persist_via_gateway(mcp_client, session_id, md_content)
    else:
        logger.warning(
            "mark_stage_complete: _mcp_client or _session_id missing from state; "
            "skipping S3 persistence"
        )


# --- Persistence helpers ---

def _sanitize_history_cell(value) -> str:
    """Return a markdown-table-safe string for an Assignment History cell.

    Missing/``None``/empty values render as the placeholder ``—``. Any pipe
    characters are replaced with ``/`` so they cannot break the fixed column
    layout, and embedded newlines are collapsed to spaces.
    """
    if value is None:
        return "—"
    text = str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = text.replace("|", "/").strip()
    return text if text else "—"


def _state_to_markdown(state: dict) -> str:
    """Convert a SharedState dictionary to the aidlc-state.md markdown format.

    Private keys (``_mcp_client``, ``_session_id``, ``_session_manager``)
    are excluded from the serialised output.
    """
    project = state.get("project", {})
    stage_progress = state.get("stage_progress", {})
    extensions = state.get("extensions", {})
    execution_plan = state.get("execution_plan", {})
    units = state.get("units", {})

    lines = ["# AI-DLC State Tracking", ""]

    # Project Information
    lines.append("## Project Information")
    lines.append(f"- **Project Type**: {project.get('type', 'greenfield')}")
    lines.append(f"- **Start Date**: {project.get('start_date', '')}")
    lines.append(f"- **Current Stage**: {state.get('current_phase', '')} - {state.get('current_stage', '')}")
    lines.append(f"- **Workspace Root**: {project.get('workspace_root', '')}")
    lines.append(f"- **Selected Project**: {project.get('selected_project', '')}")
    lines.append(f"- **Is New Project**: {'Yes' if project.get('is_new_project', True) else 'No'}")
    lines.append("")

    # Workspace State
    lines.append("## Workspace State")
    lines.append(f"- **Existing Code**: {'Yes' if state.get('brownfield', False) else 'No'}")
    langs = ", ".join(project.get("languages", [])) or "None"
    lines.append(f"- **Languages**: {langs}")
    lines.append(f"- **Build System**: {project.get('build_system', '') or 'None'}")
    lines.append(f"- **Project Structure**: {project.get('project_structure', 'empty')}")
    lines.append("")

    # Extension Configuration
    lines.append("## Extension Configuration")
    lines.append("| Extension | Enabled | Decided At |")
    lines.append("|---|---|---|")
    enabled_status = extensions.get("enabled_status", {})
    for ext_name, enabled in enabled_status.items():
        lines.append(f"| {ext_name} | {'Yes' if enabled else 'No'} | — |")
    lines.append("")

    # Execution Plan Summary
    lines.append("## Execution Plan Summary")
    stages_exec = ", ".join(execution_plan.get("stages_to_execute", [])) or "None"
    lines.append(f"- **Stages to Execute**: {stages_exec}")
    skip = execution_plan.get("stages_to_skip", {})
    if skip:
        skip_parts = [f"{k} ({v})" for k, v in skip.items()]
        lines.append(f"- **Stages to Skip**: {', '.join(skip_parts)}")
    else:
        lines.append("- **Stages to Skip**: None")
    lines.append(f"- **Depth Level**: {state.get('depth_level', 'standard')}")
    lines.append("")

    # Request Analysis
    # Persisted so the request scope/complexity determined during Requirements
    # Analysis survive a cross-session handoff (e.g. BA -> SA). Workflow
    # Planning's ``assess_stages`` derives which design stages are REQUIRED vs
    # SKIP from these fields; if they are lost on restore the planner sees
    # empty values and wrongly skips Application Design / Units Generation.
    req_analysis = state.get("request_analysis", {})
    lines.append("## Request Analysis")
    lines.append(f"- **Request Clarity**: {req_analysis.get('clarity', '')}")
    lines.append(f"- **Request Type**: {req_analysis.get('request_type', '')}")
    lines.append(f"- **Request Scope**: {req_analysis.get('scope', '')}")
    lines.append(f"- **Request Complexity**: {req_analysis.get('complexity', '')}")
    lines.append("")

    # Units of Work
    lines.append("## Units of Work")
    lines.append("| Unit | Status | Current Design Stage |")
    lines.append("|---|---|---|")
    unit_items = units.get("items", [])
    completed = units.get("completed_units", [])
    current_idx = units.get("current_unit_index", 0)
    for i, unit in enumerate(unit_items):
        name = unit.get("name", "unknown")
        if name in completed:
            status = "completed"
        elif i == current_idx:
            status = "in_progress"
        else:
            status = "pending"
        design_stage = ", ".join(unit.get("design_stages_completed", [])) or "—"
        lines.append(f"| {name} | {status} | {design_stage} |")
    lines.append("")

    # Stage Progress
    lines.append("## Stage Progress")
    inception_stages = [
        "workspace_detection", "reverse_engineering",
        "requirements_analysis", "user_stories", "workflow_planning",
        "application_design", "units_generation", "inception_complete",
    ]
    # Construction stages are disabled for the inception-only flow, except the
    # Developer-segment construction handoff (packages artifacts for the IDE).
    construction_stages: list[str] = [
        "construction_handoff",
        # "functional_design", "nfr_requirements", "nfr_design",
        # "infrastructure_design", "code_generation", "build_and_test",
    ]

    lines.append("### INCEPTION PHASE")
    for s in inception_stages:
        status = stage_progress.get(s, "not_started")
        mark = "x" if status in ("completed", "skipped") else "~" if status == "in_progress" else " "
        lines.append(f"- [{mark}] {s.replace('_', ' ').title()}")
    lines.append("")

    lines.append("### CONSTRUCTION PHASE")
    for s in construction_stages:
        status = stage_progress.get(s, "not_started")
        mark = "x" if status in ("completed", "skipped") else "~" if status == "in_progress" else " "
        lines.append(f"- [{mark}] {s.replace('_', ' ').title()}")
    lines.append("")

    # Current Status
    lines.append("## Current Status")
    lines.append(f"- **Phase**: {state.get('current_phase', '')}")
    lines.append(f"- **Stage**: {state.get('current_stage', '')}")
    lines.append(f"- **Next**: —")
    lines.append("")

    # Collaboration (handoff mirror).
    # Appended AFTER all existing sections so no existing parser token or
    # section header is altered (Req 11.4). Format mirrors design Section 6c.
    handoff = state.get("handoff", {})
    handoff_state = handoff.get("handoff_state") or "draft"
    owning_role = handoff.get("owning_role") or "requirements-analyst"

    lines.append("## Collaboration")
    lines.append("")
    lines.append(f"- **Handoff State**: {handoff_state}")
    lines.append(f"- **Owning Role**: {owning_role}")
    _last_bounce = handoff.get("last_processed_bounce_ts")
    if _last_bounce:
        # Idempotency marker for the request-changes re-open: records which
        # bounce has already re-opened this segment so a reconnect does not
        # repeatedly wipe in-progress re-work.
        lines.append(f"- **Last Processed Bounce**: {_last_bounce}")
    lines.append("")
    lines.append("### Assignment History")
    lines.append("| From | To | Actor | Timestamp | Note |")
    lines.append("|---|---|---|---|---|")
    for entry in handoff.get("assignment_history", []):
        if not isinstance(entry, dict):
            continue
        frm = _sanitize_history_cell(entry.get("from"))
        to = _sanitize_history_cell(entry.get("to"))
        actor = _sanitize_history_cell(entry.get("actor"))
        timestamp = _sanitize_history_cell(entry.get("timestamp"))
        note = _sanitize_history_cell(entry.get("note"))
        lines.append(f"| {frm} | {to} | {actor} | {timestamp} | {note} |")
    lines.append("")

    return "\n".join(lines)


def _markdown_to_state(content: str) -> dict:
    """Parse aidlc-state.md markdown content back into a SharedState dictionary."""
    state = {
        "project": {
            "type": "greenfield",
            "start_date": "",
            "workspace_root": "",
            "languages": [],
            "build_system": "",
            "project_structure": "empty",
            "selected_project": "",
            "is_new_project": True,
        },
        "brownfield": False,
        "stage_progress": {s: "not_started" for s in STAGE_NAMES},
        "current_stage": "",
        "current_phase": "",
        "artifacts": {
            "project_context": {"summary": ""},
            "reverse_engineering": {
                "business_overview": "", "architecture": "", "code_structure": "",
                "api_documentation": "", "component_inventory": "", "technology_stack": "",
                "dependencies": "", "code_quality_assessment": "",
            },
            "requirements": {"questions_file": "", "requirements_doc": ""},
            "user_stories": {"stories": "", "personas": ""},
            "workflow_planning": {"execution_plan": ""},
            "application_design": {
                "components": "", "component_methods": "", "services": "",
                "component_dependency": "", "application_design": "",
            },
            "units_generation": {"unit_of_work": "", "unit_dependency": "", "unit_story_map": ""},
            "construction": {},
            "build_and_test": {
                "build_instructions": "", "unit_test_instructions": "",
                "integration_test_instructions": "", "performance_test_instructions": "",
                "build_and_test_summary": "",
            },
        },
        "units": {"items": [], "current_unit_index": 0, "completed_units": []},
        "execution_plan": {"stages_to_execute": [], "stages_to_skip": {}, "per_unit_stages": []},
        "depth_level": "standard",
        "extensions": {"loaded_rules": [], "enabled_status": {}},
        "approval": {
            "awaiting_approval": False, "current_gate_stage": "", "summary": "",
            "options": [], "user_response": "",
        },
        "audit_buffer": [],
        "request_analysis": {"clarity": "", "request_type": "", "scope": "", "complexity": ""},
        # Handoff mirror (design Section 6a/6d). Defaults satisfy Req 9.5
        # (default-on-missing): when the markdown has no ## Collaboration
        # section, these documented defaults remain in place.
        "handoff": {
            "handoff_state": "draft",
            "owning_role": "requirements-analyst",
            "assignment_history": [],
        },
    }

    lines = content.split("\n")
    for line in lines:
        line = line.strip()

        # Project Information
        if line.startswith("- **Project Type**:"):
            state["project"]["type"] = line.split(":", 1)[1].strip()
        elif line.startswith("- **Start Date**:"):
            state["project"]["start_date"] = line.split(":", 1)[1].strip()
        elif line.startswith("- **Current Stage**:"):
            val = line.split(":", 1)[1].strip()
            parts = val.split(" - ", 1)
            if len(parts) == 2:
                state["current_phase"] = parts[0].strip()
                state["current_stage"] = parts[1].strip()
        elif line.startswith("- **Workspace Root**:"):
            state["project"]["workspace_root"] = line.split(":", 1)[1].strip()
        elif line.startswith("- **Selected Project**:"):
            state["project"]["selected_project"] = line.split(":", 1)[1].strip()
        elif line.startswith("- **Is New Project**:"):
            val = line.split(":", 1)[1].strip()
            state["project"]["is_new_project"] = val == "Yes"

        # Workspace State
        elif line.startswith("- **Existing Code**:"):
            val = line.split(":", 1)[1].strip()
            state["brownfield"] = val == "Yes"
        elif line.startswith("- **Languages**:"):
            val = line.split(":", 1)[1].strip()
            if val and val != "None":
                state["project"]["languages"] = [lang.strip() for lang in val.split(",")]
            else:
                state["project"]["languages"] = []
        elif line.startswith("- **Build System**:"):
            val = line.split(":", 1)[1].strip()
            state["project"]["build_system"] = "" if val == "None" else val
        elif line.startswith("- **Project Structure**:"):
            state["project"]["project_structure"] = line.split(":", 1)[1].strip()

        # Execution Plan
        elif line.startswith("- **Stages to Execute**:"):
            val = line.split(":", 1)[1].strip()
            if val and val != "None":
                state["execution_plan"]["stages_to_execute"] = [s.strip() for s in val.split(",")]
            else:
                state["execution_plan"]["stages_to_execute"] = []
        elif line.startswith("- **Depth Level**:"):
            state["depth_level"] = line.split(":", 1)[1].strip()

        # Request Analysis (drives Workflow Planning's stage assessment).
        # Only override the documented empty defaults when a value is present.
        elif line.startswith("- **Request Clarity**:"):
            val = line.split(":", 1)[1].strip()
            if val:
                state["request_analysis"]["clarity"] = val
        elif line.startswith("- **Request Type**:"):
            val = line.split(":", 1)[1].strip()
            if val:
                state["request_analysis"]["request_type"] = val
        elif line.startswith("- **Request Scope**:"):
            val = line.split(":", 1)[1].strip()
            if val:
                state["request_analysis"]["scope"] = val
        elif line.startswith("- **Request Complexity**:"):
            val = line.split(":", 1)[1].strip()
            if val:
                state["request_analysis"]["complexity"] = val

        # Stage Progress checkboxes
        elif line.startswith("- [x]"):
            stage_display = line[5:].strip()
            stage_key = stage_display.lower().replace(" ", "_")
            if stage_key in state["stage_progress"]:
                state["stage_progress"][stage_key] = "completed"
        elif line.startswith("- [~]"):
            stage_display = line[5:].strip()
            stage_key = stage_display.lower().replace(" ", "_")
            if stage_key in state["stage_progress"]:
                state["stage_progress"][stage_key] = "in_progress"
        elif line.startswith("- [ ]"):
            stage_display = line[5:].strip()
            stage_key = stage_display.lower().replace(" ", "_")
            if stage_key in state["stage_progress"]:
                state["stage_progress"][stage_key] = "not_started"

        # Current Status
        elif line.startswith("- **Phase**:"):
            phase = line.split(":", 1)[1].strip()
            if phase:
                state["current_phase"] = phase
        elif line.startswith("- **Stage**:"):
            stage = line.split(":", 1)[1].strip()
            if stage:
                state["current_stage"] = stage

        # Collaboration (handoff mirror). Only override the documented
        # defaults when a non-empty value is present (Req 9.3 / 9.5).
        elif line.startswith("- **Handoff State**:"):
            val = line.split(":", 1)[1].strip()
            if val and val != "—":
                state["handoff"]["handoff_state"] = val
        elif line.startswith("- **Owning Role**:"):
            val = line.split(":", 1)[1].strip()
            if val and val != "—":
                state["handoff"]["owning_role"] = val

    # Parse extension table
    _parse_extension_table(lines, state)

    # Parse the Assignment History table into the handoff mirror.
    _parse_assignment_history(lines, state)

    return state


def _parse_extension_table(lines: list, state: dict) -> None:
    """Parse the extension configuration table from markdown lines."""
    in_ext_table = False
    for line in lines:
        stripped = line.strip()
        if stripped == "| Extension | Enabled | Decided At |":
            in_ext_table = True
            continue
        if in_ext_table and stripped.startswith("|---"):
            continue
        if in_ext_table and stripped.startswith("|"):
            parts = [p.strip() for p in stripped.split("|") if p.strip()]
            if len(parts) >= 2:
                ext_name = parts[0]
                enabled = parts[1] == "Yes"
                state["extensions"]["enabled_status"][ext_name] = enabled
        elif in_ext_table:
            in_ext_table = False


# Placeholder rendered by ``_sanitize_history_cell`` for missing/None values.
_HISTORY_PLACEHOLDER = "—"

# Header identifying the Assignment History table (must match the serializer
# in ``_state_to_markdown`` exactly so it is parsed distinctly from the
# extension and units tables).
_HISTORY_HEADER = "| From | To | Actor | Timestamp | Note |"

# Ordered column names mapping the 5 table cells to history-entry keys.
_HISTORY_COLUMNS = ("from", "to", "actor", "timestamp", "note")


def _unplaceholder(cell: str):
    """Map the em-dash placeholder back to ``None``; otherwise return the cell.

    The serializer renders ``None`` (and empty values) as the ``—`` placeholder.
    Parsing the placeholder back to ``None`` keeps a serialized-then-parsed
    history equivalent to the original for entries whose absent fields are
    ``None`` (e.g. the seed entry's ``from`` and any omitted ``note``).
    """
    return None if cell == _HISTORY_PLACEHOLDER else cell


def _parse_assignment_history(lines: list, state: dict) -> None:
    """Parse the ``### Assignment History`` table into the handoff mirror.

    Scans for the fixed-column history header (matched exactly so it cannot
    collide with the extension or units tables), skips the header and the
    ``|---|`` separator row, and maps each subsequent data row's 5 cells onto
    ``{from,to,actor,timestamp,note}`` entries appended to
    ``state["handoff"]["assignment_history"]``. The em-dash placeholder is
    converted back to ``None`` so the serialize→parse round-trip is faithful.
    """
    history: list = []
    in_history = False
    for line in lines:
        stripped = line.strip()
        if stripped == _HISTORY_HEADER:
            in_history = True
            continue
        if not in_history:
            continue
        if stripped.startswith("|---"):
            continue
        if stripped.startswith("|"):
            # Inner cells: drop the leading/trailing empty split fragments so
            # cells are read positionally (the serializer never emits an empty
            # cell — missing values render as the placeholder).
            cells = [p.strip() for p in stripped.split("|")[1:-1]]
            if len(cells) == len(_HISTORY_COLUMNS):
                entry = {
                    col: _unplaceholder(cell)
                    for col, cell in zip(_HISTORY_COLUMNS, cells)
                }
                history.append(entry)
        else:
            # First non-table line ends the history table.
            break

    state["handoff"]["assignment_history"] = history


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

@tool(context=True)
def persist_state(tool_context: dict) -> str:
    """Persist current SharedState to S3 at ``{session_id}/aidlc-docs/aidlc-state.md``
    via the Gateway ``write_file`` tool.

    Args:
        tool_context: Strands tool context with invocation_state.

    Returns:
        Confirmation message with the S3 key written.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state["_mcp_client"]
    session_id = state["_session_id"]

    md_content = _state_to_markdown(state)
    _persist_via_gateway(mcp_client, session_id, md_content)

    s3_key = _build_state_s3_key(session_id)
    return f"State persisted to s3://{s3_key}"


@tool(context=True)
def load_state(tool_context: dict) -> dict:
    """Load SharedState from S3 at ``{session_id}/aidlc-docs/aidlc-state.md``
    via the Gateway ``read_file`` tool.

    If the state file does not exist, a fresh SharedState is initialised.
    If the state file is corrupted (unparseable), a fresh SharedState is
    returned with a warning logged.

    Args:
        tool_context: Strands tool context with invocation_state.

    Returns:
        A SharedState dictionary parsed from the state file, or a fresh one.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state["_mcp_client"]
    session_id = state["_session_id"]

    content = _load_via_gateway(mcp_client, session_id)

    # Missing state file → fresh state
    if content is None:
        logger.info("State file not found for session %s; initialising fresh state", session_id)
        return initialize_shared_state("")

    # Attempt to parse; corrupted → fresh state with warning
    try:
        loaded = _markdown_to_state(content)
        # Basic sanity check: must have stage_progress
        if not isinstance(loaded.get("stage_progress"), dict):
            raise ValueError("Parsed state missing stage_progress")
        return loaded
    except Exception as exc:
        logger.warning(
            "Corrupted state file for session %s (error: %s); initialising fresh state",
            session_id,
            exc,
        )
        return initialize_shared_state("")


@tool(context=True)
def update_stage_progress(tool_context: dict, stage: str, status: str) -> str:
    """Update stage completion status in SharedState and persist via Gateway.

    Args:
        tool_context: Strands tool context with invocation_state.
        stage: Stage name to update (e.g., "workspace_detection").
        status: New status (not_started, in_progress, completed, skipped).

    Returns:
        Confirmation message.
    """
    valid_statuses = {"not_started", "in_progress", "completed", "skipped"}
    if status not in valid_statuses:
        return f"Invalid status '{status}'. Must be one of: {valid_statuses}"

    from tools._compat import get_state
    state = get_state(tool_context)
    stage_progress = state.get("stage_progress", {})

    if stage not in stage_progress:
        return f"Unknown stage '{stage}'. Valid stages: {list(stage_progress.keys())}"

    stage_progress[stage] = status

    # Persist state via Gateway
    mcp_client = state.get("_mcp_client")
    session_id = state.get("_session_id")

    if mcp_client is not None and session_id:
        md_content = _state_to_markdown(state)
        _persist_via_gateway(mcp_client, session_id, md_content)
    else:
        logger.warning(
            "update_stage_progress: _mcp_client or _session_id missing; "
            "skipping S3 persistence"
        )

    return f"Stage '{stage}' updated to '{status}' and state persisted"
