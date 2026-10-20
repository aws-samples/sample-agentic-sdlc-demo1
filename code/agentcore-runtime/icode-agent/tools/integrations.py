"""Integration config loading and action logging for the AI-SDLC Multi-Agent System.

Provides helpers used by ``main_ws.py`` (at session start) and by all
Integration_Agents at runtime:

- ``load_integration_config(mcp_client, session_id)`` — reads and validates
  ``{session_id}/integrations/config.json`` from S3 via the Gateway.
- ``validate_integration_config(config)`` — validates the config schema.
- ``log_integration_action(state, ...)`` — appends an entry to the action log.
- ``disable_integration_for_session`` — ``@tool`` that sets a session override.
- ``read_s3_artifact`` — ``@tool`` thin wrapper for reading S3 files.
"""

import json
import logging
from datetime import datetime, timezone

from strands.tools import tool

from tools.gateway import call_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_text(result) -> str | None:
    """Extract text content from a Gateway response."""
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


def _is_not_found_error(result) -> bool:
    """Return True if the Gateway result indicates a missing file (404 / NoSuchKey)."""
    is_error = (
        (isinstance(result, dict) and result.get("isError"))
        or (isinstance(result, dict) and result.get("status") == "error")
    )
    if not is_error:
        return False
    text = (_extract_text(result) or str(result)).lower()
    return "not found" in text or "nosuchkey" in text or "404" in text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_integration_config(mcp_client, session_id: str) -> dict:
    """Load ``integrations/config.json`` from S3 for the given session.

    Reads ``{session_id}/integrations/config.json`` via the Gateway, parses
    the JSON, and validates the schema.  Returns ``{}`` on any error:
    missing file, malformed JSON, or schema violation.

    Args:
        mcp_client: An MCPClient instance (or test mock).
        session_id: The project/session storage ID (S3 prefix).

    Returns:
        Parsed and validated config dict, or ``{}`` on any error.
    """
    path = f"{session_id}/integrations/config.json"
    logger.info("[INTEGRATION] load_integration_config: reading path=%s", path)
    try:
        result = call_tool(mcp_client, "read_file", {"path": path})
    except Exception as exc:
        logger.error("[INTEGRATION] load_integration_config: exception calling read_file path=%s error=%s", path, exc, exc_info=True)
        return {}

    if _is_not_found_error(result):
        logger.warning("[INTEGRATION] load_integration_config: config file NOT FOUND at %s — all integrations disabled", path)
        return {}

    is_error = (
        (isinstance(result, dict) and result.get("isError"))
        or (isinstance(result, dict) and result.get("status") == "error")
    )
    if is_error:
        logger.error(
            "[INTEGRATION] load_integration_config: Gateway error reading %s: %s",
            path,
            _extract_text(result) or str(result),
        )
        return {}

    raw = _extract_text(result)
    if raw is None:
        logger.error("[INTEGRATION] load_integration_config: empty response reading %s", path)
        return {}

    logger.info("[INTEGRATION] load_integration_config: raw config content (first 500 chars): %s", raw[:500])

    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            "[INTEGRATION] load_integration_config: malformed JSON in %s: %s", path, exc
        )
        return {}

    if not isinstance(config, dict):
        logger.error(
            "[INTEGRATION] load_integration_config: config at %s is not a JSON object", path
        )
        return {}

    validation = validate_integration_config(config)
    if not validation["valid"]:
        logger.error(
            "[INTEGRATION] load_integration_config: schema violation in %s: %s",
            path,
            validation["error"],
        )
        return {}

    logger.info(
        "[INTEGRATION] load_integration_config: SUCCESS — integrations=%s atlassian_enabled=%s",
        list(config.keys()),
        config.get("atlassian", {}).get("enabled"),
    )
    return config


def validate_integration_config(config: dict) -> dict:
    """Validate an Integration_Config dict against the defined schema.

    Only checks that each integration entry's ``enabled`` field, if present,
    is a boolean. The Atlassian MCP proxy creates Jira projects and Confluence
    spaces automatically, so ``confluence_space_key`` and ``jira_project_key``
    are no longer required.

    Args:
        config: The parsed config dict.

    Returns:
        ``{"valid": True}`` on success, or
        ``{"valid": False, "error": "<description>"}`` on failure.
    """
    if not isinstance(config, dict):
        return {"valid": False, "error": "config must be a JSON object"}
    for integration_id, integration_config in config.items():
        if not isinstance(integration_config, dict):
            return {"valid": False, "error": f"'{integration_id}' must be an object"}
        enabled = integration_config.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            return {"valid": False, "error": f"'{integration_id}.enabled' must be a boolean"}
    return {"valid": True}


@tool(context=True)
def log_integration_action(
    tool_context: dict,
    agent_name: str,
    integration: str,
    action_type: str,
    outcome: str,
    detail: str = "",
) -> str:
    """Append an integration action entry to the action log in SharedState.

    Each entry records a discrete integration action (approved, skipped, or
    failed) with an ISO 8601 UTC timestamp.

    Args:
        tool_context: Strands tool context with invocation_state.
        agent_name: Name of the Integration_Agent (e.g. ``"atlassian_requirements"``).
        integration: Integration name (e.g. ``"atlassian"``).
        action_type: Type of action (e.g. ``"confluence_create_page"``).
        outcome: One of ``"approved"``, ``"skipped"``, or ``"failed"``.
        detail: Optional human-readable detail string.

    Returns:
        Confirmation message.
    """
    from tools._compat import get_state
    state = get_state(tool_context)

    timestamp = datetime.now(timezone.utc).isoformat()
    entry = {
        "timestamp": timestamp,
        "agent_name": agent_name,
        "integration": integration,
        "action_type": action_type,
        "outcome": outcome,
        "detail": detail,
    }
    integrations = state.setdefault("integrations", {})
    action_log = integrations.setdefault("action_log", [])
    action_log.append(entry)
    logger.info(
        "[INTEGRATION] action_log entry: agent=%s integration=%s action=%s outcome=%s",
        agent_name, integration, action_type, outcome,
    )
    return f"Logged integration action: {agent_name} {action_type} {outcome}"


# ---------------------------------------------------------------------------
# @tool-decorated helpers (used directly in agent TOOLS lists)
# ---------------------------------------------------------------------------

@tool(context=True)
def disable_integration_for_session(
    tool_context: dict, integration: str = "atlassian"
) -> str:
    """Disable an integration for the remainder of this session.

    Sets ``state["integrations"]["session_overrides"][integration] = False``
    so that all subsequent Integration_Graph_Nodes for that integration
    self-skip without calling any external tools.

    Args:
        tool_context: Strands tool context with invocation_state.
        integration: Integration name to disable (default: ``"atlassian"``).

    Returns:
        Confirmation message.
    """
    from tools._compat import get_state

    state = get_state(tool_context)
    state.setdefault("integrations", {}).setdefault("session_overrides", {})[integration] = False
    logger.info("[INTEGRATION] disable_integration_for_session: integration=%s disabled for this session", integration)
    return f"Integration '{integration}' disabled for this session."


@tool(context=True)
def read_s3_artifact(tool_context: dict, path: str) -> str:
    """Read an S3 artifact via the Gateway.

    Thin wrapper around ``call_tool(mcp_client, "read_file", {"path": path})``.
    The ``_mcp_client`` is extracted from SharedState.

    Args:
        tool_context: Strands tool context with invocation_state.
        path: Full S3 key (e.g. ``"{session_id}/aidlc-docs/inception/requirements/requirements.md"``).

    Returns:
        File content as a string, or an error message.
    """
    from tools._compat import get_state

    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        logger.error("[INTEGRATION] read_s3_artifact: _mcp_client not found in shared state")
        return "Error: _mcp_client not found in shared state"

    logger.info("[INTEGRATION] read_s3_artifact: reading path=%s", path)
    
    # Auto-prepend session_id if the path doesn't already include it
    session_id = state.get("_session_id", "")
    if session_id and not path.startswith(session_id):
        path = f"{session_id}/{path}"
        logger.info("[INTEGRATION] read_s3_artifact: prepended session_id, full path=%s", path)
    
    try:
        result = call_tool(mcp_client, "read_file", {"path": path})
    except Exception as exc:
        logger.error("[INTEGRATION] read_s3_artifact: exception reading path=%s error=%s", path, exc, exc_info=True)
        return f"Error reading {path}: {exc}"

    if _is_not_found_error(result):
        logger.warning("[INTEGRATION] read_s3_artifact: file NOT FOUND at path=%s", path)
        return f"Error: file not found at {path}"

    is_error = (
        (isinstance(result, dict) and result.get("isError"))
        or (isinstance(result, dict) and result.get("status") == "error")
    )
    if is_error:
        logger.error("[INTEGRATION] read_s3_artifact: Gateway error path=%s detail=%s", path, _extract_text(result) or str(result))
        return f"Error reading {path}: {_extract_text(result) or str(result)}"

    content = _extract_text(result)
    if content is None:
        logger.error("[INTEGRATION] read_s3_artifact: empty response for path=%s", path)
        return f"Error: empty response reading {path}"

    logger.info("[INTEGRATION] read_s3_artifact: SUCCESS path=%s content_length=%d", path, len(content))
    return content


# ---------------------------------------------------------------------------
# Atlassian tool wrappers — @tool-decorated so agents can call them directly
# ---------------------------------------------------------------------------

@tool(context=True)
def atlassian_confluence_create_page(
    tool_context: dict,
    space_key: str,
    title: str,
    content: str,
    parent_page_id: str = "",
) -> str:
    """Create a Confluence page via the AgentCore Gateway.

    Args:
        tool_context: Strands tool context with invocation_state.
        space_key: Confluence space key (e.g. "PROJ").
        title: Page title.
        content: Page content (plain text or Confluence storage format).
        parent_page_id: Optional parent page ID.

    Returns:
        Success message with page details, or error message.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        return "Error: _mcp_client not found in shared state"

    args = {"space_key": space_key, "title": title, "content": content}
    if parent_page_id:
        args["parent_page_id"] = parent_page_id

    # Pass project_id (UUID) so the proxy resolves per-project Atlassian credentials
    project_uuid = state.get("_project_uuid") or state.get("_session_id", "")
    if project_uuid:
        args["project_id"] = project_uuid

    logger.info("[INTEGRATION] atlassian_confluence_create_page: space=%s title=%s", space_key, title)
    try:
        result = call_tool(mcp_client, "atlassian___confluence_create_page", args)
        is_error = isinstance(result, dict) and (result.get("isError") or result.get("status") == "error")
        if is_error:
            error_msg = _extract_text(result) or str(result)
            logger.error("[INTEGRATION] atlassian_confluence_create_page ERROR: %s", error_msg)
            return f"Error creating Confluence page: {error_msg}"
        content_text = _extract_text(result) or str(result)
        logger.info("[INTEGRATION] atlassian_confluence_create_page SUCCESS: %s", content_text[:200])
        return f"Successfully created Confluence page '{title}' in space {space_key}: {content_text}"
    except Exception as exc:
        logger.error("[INTEGRATION] atlassian_confluence_create_page EXCEPTION: %s", exc, exc_info=True)
        return f"Error creating Confluence page: {exc}"


@tool(context=True)
def atlassian_jira_create_issue(
    tool_context: dict,
    project_key: str,
    summary: str,
    issue_type: str,
    description: str = "",
    parent_key: str = "",
) -> str:
    """Create a Jira issue via the AgentCore Gateway.

    Args:
        tool_context: Strands tool context with invocation_state.
        project_key: Jira project key (e.g. "PROJ").
        summary: Issue summary.
        issue_type: Issue type (e.g. "Epic", "Story", "Task").
        description: Optional issue description.
        parent_key: Optional parent issue key for sub-tasks.

    Returns:
        Success message with issue key, or error message.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        return "Error: _mcp_client not found in shared state"

    args = {"project_key": project_key, "summary": summary, "issue_type": issue_type}
    if description:
        args["description"] = description
    if parent_key:
        args["parent_key"] = parent_key

    # Pass project_id (UUID) so the proxy resolves per-project Atlassian credentials
    project_uuid = state.get("_project_uuid") or state.get("_session_id", "")
    if project_uuid:
        args["project_id"] = project_uuid

    logger.info("[INTEGRATION] atlassian_jira_create_issue: project=%s type=%s summary=%s", project_key, issue_type, summary[:50])
    try:
        result = call_tool(mcp_client, "atlassian___jira_create_issue", args)
        is_error = isinstance(result, dict) and (result.get("isError") or result.get("status") == "error")
        if is_error:
            error_msg = _extract_text(result) or str(result)
            logger.error("[INTEGRATION] atlassian_jira_create_issue ERROR: %s", error_msg)
            return f"Error creating Jira issue: {error_msg}"
        content_text = _extract_text(result) or str(result)
        logger.info("[INTEGRATION] atlassian_jira_create_issue SUCCESS: %s", content_text[:200])
        return f"Successfully created Jira {issue_type} in {project_key}: {content_text}"
    except Exception as exc:
        logger.error("[INTEGRATION] atlassian_jira_create_issue EXCEPTION: %s", exc, exc_info=True)
        return f"Error creating Jira issue: {exc}"


@tool(context=True)
def atlassian_jira_transition_issue(
    tool_context: dict,
    issue_key: str,
    transition_name: str,
) -> str:
    """Transition a Jira issue to a new status via the AgentCore Gateway.

    Args:
        tool_context: Strands tool context with invocation_state.
        issue_key: Jira issue key (e.g. "PROJ-123").
        transition_name: Target status name (e.g. "Done", "In Progress").

    Returns:
        Success message, or error message.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        return "Error: _mcp_client not found in shared state"

    logger.info("[INTEGRATION] atlassian_jira_transition_issue: issue=%s transition=%s", issue_key, transition_name)
    try:
        result = call_tool(mcp_client, "atlassian___jira_transition_issue", {"issue_key": issue_key, "transition_name": transition_name})
        is_error = isinstance(result, dict) and (result.get("isError") or result.get("status") == "error")
        if is_error:
            error_msg = _extract_text(result) or str(result)
            logger.error("[INTEGRATION] atlassian_jira_transition_issue ERROR: %s", error_msg)
            return f"Error transitioning Jira issue: {error_msg}"
        content_text = _extract_text(result) or str(result)
        logger.info("[INTEGRATION] atlassian_jira_transition_issue SUCCESS: %s", content_text[:200])
        return f"Successfully transitioned {issue_key} to '{transition_name}': {content_text}"
    except Exception as exc:
        logger.error("[INTEGRATION] atlassian_jira_transition_issue EXCEPTION: %s", exc, exc_info=True)
        return f"Error transitioning Jira issue: {exc}"


@tool(context=True)
def atlassian_jira_get_board(
    tool_context: dict,
    project_key: str,
) -> str:
    """Get a Jira board for a project via the AgentCore Gateway.

    Args:
        tool_context: Strands tool context with invocation_state.
        project_key: Jira project key.

    Returns:
        Board details as string, or error message.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        return "Error: _mcp_client not found in shared state"

    logger.info("[INTEGRATION] atlassian_jira_get_board: project=%s", project_key)
    try:
        result = call_tool(mcp_client, "atlassian___jira_get_board", {"project_key": project_key})
        content_text = _extract_text(result) or str(result)
        return content_text
    except Exception as exc:
        return f"Error getting Jira board: {exc}"


@tool(context=True)
def atlassian_jira_create_sprint(
    tool_context: dict,
    board_id: int,
    name: str,
    goal: str = "",
) -> str:
    """Create a Jira sprint via the AgentCore Gateway.

    Args:
        tool_context: Strands tool context with invocation_state.
        board_id: Jira board ID.
        name: Sprint name.
        goal: Optional sprint goal.

    Returns:
        Success message with sprint details, or error message.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        return "Error: _mcp_client not found in shared state"

    args = {"board_id": board_id, "name": name}
    if goal:
        args["goal"] = goal

    logger.info("[INTEGRATION] atlassian_jira_create_sprint: board=%s name=%s", board_id, name)
    try:
        result = call_tool(mcp_client, "atlassian___jira_create_sprint", args)
        content_text = _extract_text(result) or str(result)
        return f"Successfully created sprint '{name}': {content_text}"
    except Exception as exc:
        return f"Error creating Jira sprint: {exc}"


@tool(context=True)
def atlassian_ensure_jira_project(
    tool_context: dict,
    project_name: str,
    project_key: str = "",
) -> str:
    """Ensure a Jira project exists, creating it if needed.

    Args:
        tool_context: Strands tool context with invocation_state.
        project_name: Human-readable project name.
        project_key: Optional Jira key (derived from name if not provided).

    Returns:
        JSON string with project key, id, name, and whether it already existed.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        return "Error: _mcp_client not found in shared state"

    args = {"project_name": project_name}
    if project_key:
        args["project_key"] = project_key

    # Pass project_id (UUID) so the proxy resolves per-project Atlassian credentials
    project_uuid = state.get("_project_uuid") or state.get("_session_id", "")
    if project_uuid:
        args["project_id"] = project_uuid

    logger.info("[INTEGRATION] atlassian_ensure_jira_project: name=%s key=%s", project_name, project_key)
    try:
        result = call_tool(mcp_client, "atlassian___ensure_jira_project", args)
        content = _extract_text(result) or str(result)
        logger.info("[INTEGRATION] atlassian_ensure_jira_project result: %s", content[:200])
        return content
    except Exception as exc:
        logger.error("[INTEGRATION] atlassian_ensure_jira_project EXCEPTION: %s", exc, exc_info=True)
        return f"Error ensuring Jira project: {exc}"


@tool(context=True)
def atlassian_ensure_confluence_space(
    tool_context: dict,
    space_name: str,
    space_key: str = "",
) -> str:
    """Ensure a Confluence space exists, creating it if needed.

    Args:
        tool_context: Strands tool context with invocation_state.
        space_name: Human-readable space name.
        space_key: Optional space key (derived from name if not provided).

    Returns:
        JSON string with space key, id, name, and whether it already existed.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        return "Error: _mcp_client not found in shared state"

    args = {"space_name": space_name}
    if space_key:
        args["space_key"] = space_key

    # Pass project_id (UUID) so the proxy resolves per-project Atlassian credentials
    project_uuid = state.get("_project_uuid") or state.get("_session_id", "")
    if project_uuid:
        args["project_id"] = project_uuid

    logger.info("[INTEGRATION] atlassian_ensure_confluence_space: name=%s key=%s", space_name, space_key)
    try:
        result = call_tool(mcp_client, "atlassian___ensure_confluence_space", args)
        content = _extract_text(result) or str(result)
        logger.info("[INTEGRATION] atlassian_ensure_confluence_space result: %s", content[:200])
        return content
    except Exception as exc:
        logger.error("[INTEGRATION] atlassian_ensure_confluence_space EXCEPTION: %s", exc, exc_info=True)
        return f"Error ensuring Confluence space: {exc}"


@tool(context=True)
def atlassian_confluence_add_label(
    tool_context: dict,
    page_id: str,
    label: str,
) -> str:
    """Add a label to a Confluence page via the AgentCore Gateway.

    Args:
        tool_context: Strands tool context with invocation_state.
        page_id: Confluence page ID.
        label: Label name to add (e.g. "pending-review").

    Returns:
        Success or error message.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        return "Error: _mcp_client not found in shared state"

    logger.info("[INTEGRATION] atlassian_confluence_add_label: page_id=%s label=%s", page_id, label)
    try:
        result = call_tool(mcp_client, "atlassian___confluence_add_label", {"page_id": page_id, "label": label})
        content = _extract_text(result) or str(result)
        logger.info("[INTEGRATION] atlassian_confluence_add_label result: %s", content[:200])
        return content
    except Exception as exc:
        logger.error("[INTEGRATION] atlassian_confluence_add_label EXCEPTION: %s", exc, exc_info=True)
        return f"Error adding label: {exc}"


@tool(context=True)
def atlassian_confluence_get_labels(
    tool_context: dict,
    page_id: str,
) -> str:
    """Get all labels on a Confluence page via the AgentCore Gateway.

    Args:
        tool_context: Strands tool context with invocation_state.
        page_id: Confluence page ID.

    Returns:
        JSON string with labels list, or error message.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        return "Error: _mcp_client not found in shared state"

    logger.info("[INTEGRATION] atlassian_confluence_get_labels: page_id=%s", page_id)
    try:
        result = call_tool(mcp_client, "atlassian___confluence_get_labels", {"page_id": page_id})
        content = _extract_text(result) or str(result)
        logger.info("[INTEGRATION] atlassian_confluence_get_labels result: %s", content[:200])
        return content
    except Exception as exc:
        logger.error("[INTEGRATION] atlassian_confluence_get_labels EXCEPTION: %s", exc, exc_info=True)
        return f"Error getting labels: {exc}"


@tool(context=True)
def atlassian_jira_list_backlog_issues(
    tool_context: dict,
    board_id: int,
    project_key: str = "",
) -> str:
    """List issues in the backlog for a Jira board via the AgentCore Gateway.

    Args:
        tool_context: Strands tool context with invocation_state.
        board_id: Jira board ID.
        project_key: Optional project key to filter by.

    Returns:
        JSON string with issues list, or error message.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        return "Error: _mcp_client not found in shared state"

    args = {"board_id": board_id}
    if project_key:
        args["project_key"] = project_key

    logger.info("[INTEGRATION] atlassian_jira_list_backlog_issues: board=%s", board_id)
    try:
        result = call_tool(mcp_client, "atlassian___jira_list_backlog_issues", args)
        content = _extract_text(result) or str(result)
        return content
    except Exception as exc:
        return f"Error listing backlog: {exc}"


@tool(context=True)
def atlassian_jira_move_to_sprint(
    tool_context: dict,
    sprint_id: int,
    issue_keys: list,
) -> str:
    """Move issues to a sprint via the AgentCore Gateway.

    Args:
        tool_context: Strands tool context with invocation_state.
        sprint_id: The sprint ID.
        issue_keys: List of issue keys to move (e.g. ["PROJ-1", "PROJ-2"]).

    Returns:
        Success or error message.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")
    if mcp_client is None:
        return "Error: _mcp_client not found in shared state"

    logger.info("[INTEGRATION] atlassian_jira_move_to_sprint: sprint=%s issues=%s", sprint_id, issue_keys)
    try:
        result = call_tool(mcp_client, "atlassian___jira_move_to_sprint", {"sprint_id": sprint_id, "issue_keys": issue_keys})
        content = _extract_text(result) or str(result)
        return content
    except Exception as exc:
        return f"Error moving issues to sprint: {exc}"
