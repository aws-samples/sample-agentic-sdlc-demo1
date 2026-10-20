"""Thin wrapper around MCPClient for Gateway tool calls.

The AgentCore Gateway namespaces tools by target name (e.g.
``file-operations___read_file``) and uses different parameter names
(``file_path`` instead of ``path``).  This module translates the
simple internal names used throughout the codebase into the actual
Gateway tool names and parameter keys.
"""

import uuid

from strands.tools.mcp import MCPClient

# Gateway target prefixes — must match the gatewayTargetName in CDK config.
_TARGET_PREFIX = "file-operations___"
_DIAGRAM_TARGET_PREFIX = "diagram-operations___"

# Maps internal tool names to (gateway_tool_name, param_key_remap).
# param_key_remap maps our internal key → Gateway key.
_TOOL_MAP = {
    "create_folder": (
        f"{_TARGET_PREFIX}create_folder",
        {"path": "folder_path"},
    ),
    "read_file": (
        f"{_TARGET_PREFIX}read_file",
        {"path": "file_path"},
    ),
    "write_file": (
        f"{_TARGET_PREFIX}write_file",
        {"path": "file_path"},
    ),
    "list_files": (
        f"{_TARGET_PREFIX}list_files",
        {},  # prefix and max_keys already match
    ),
    "get_presigned_url": (
        f"{_TARGET_PREFIX}get_presigned_url",
        {"path": "file_path"},
    ),
    "extract_document_text": (
        f"{_TARGET_PREFIX}extract_document_text",
        {"path": "file_path"},
    ),
    "generate_aws_architecture_diagram": (
        f"{_DIAGRAM_TARGET_PREFIX}generate_aws_architecture_diagram",
        {},  # architecture_description and folder_path already match
    ),
}


def call_tool(mcp_client, tool_name: str, args: dict):
    """Call a Gateway tool, translating names and params automatically.

    For real MCPClient instances, translates the internal tool name
    (e.g. ``read_file``) to the Gateway-namespaced name
    (``file-operations___read_file``) and remaps parameter keys.

    For test mocks (non-MCPClient), passes through unchanged.

    Args:
        mcp_client: An MCPClient instance or mock.
        tool_name: Internal tool name (e.g. "read_file").
        args: Arguments dict with internal key names.

    Returns:
        The tool result dict.
    """
    if isinstance(mcp_client, MCPClient):
        gw_name, key_remap = _TOOL_MAP.get(tool_name, (tool_name, {}))
        gw_args = {key_remap.get(k, k): v for k, v in args.items()}
        tool_use_id = str(uuid.uuid4())
        return mcp_client.call_tool_sync(tool_use_id, gw_name, gw_args)
    # Fallback for test mocks that define call_tool(name, args) directly
    return mcp_client.call_tool(tool_name, args)
