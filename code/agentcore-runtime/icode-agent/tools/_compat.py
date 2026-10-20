"""Compatibility helper for Strands ToolContext.

Strands SDK v1.29+ passes a ToolContext dataclass object (with attributes
tool_use, agent, invocation_state) rather than a plain dict. This helper
extracts invocation_state regardless of whether the caller is the real SDK
or a test passing a dict/MagicMock.
"""

import posixpath


def get_state(tool_context) -> dict:
    """Extract invocation_state from a ToolContext, dict, or mock.

    Tries attribute access first (real ToolContext), then dict-style
    .get() (test dicts and MagicMock with .get lambda), and falls back
    to an empty dict.

    Args:
        tool_context: A Strands ToolContext object, a plain dict, or a mock.

    Returns:
        The invocation_state dictionary.
    """
    # 1. Attribute access (real ToolContext dataclass)
    if hasattr(tool_context, "invocation_state") and not callable(getattr(tool_context, "invocation_state", None)):
        return tool_context.invocation_state

    # 2. Dict-style access (test helpers that pass plain dicts)
    if isinstance(tool_context, dict):
        return tool_context.get("invocation_state", {})

    # 3. MagicMock with .get lambda (property-based tests)
    if hasattr(tool_context, "get") and callable(tool_context.get):
        return tool_context.get("invocation_state", {})

    return {}


def get_workspace_root(tool_context) -> str:
    """Return the workspace root directory from SharedState.

    Falls back to ``"."`` when the value is not set (e.g. in unit tests).

    Args:
        tool_context: A Strands ToolContext, dict, or mock.

    Returns:
        The workspace root path (S3 prefix in AgentCore mode).
    """
    state = get_state(tool_context)
    return state.get("project", {}).get("workspace_root", "") or "."


def resolve_workspace_path(tool_context, relative_path: str) -> str:
    """Join *relative_path* with the workspace root using forward slashes.

    In AgentCore mode the workspace root is an S3 prefix (e.g. ``"CYOA"``),
    so we use ``posixpath.join`` to produce S3-compatible keys.

    Args:
        tool_context: A Strands ToolContext, dict, or mock.
        relative_path: Path relative to the workspace root
                       (e.g. ``"aidlc-docs/inception/plans/execution-plan.md"``).

    Returns:
        The resolved path using forward slashes.
    """
    root = get_workspace_root(tool_context)
    return posixpath.join(root, relative_path)
