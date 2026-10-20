"""Lambda handler for AgentCore Gateway file operation tools.

The AgentCore Gateway invokes this Lambda with:
- event: a flat dict of the tool's inputSchema properties
  e.g. {"file_path": "some/key.md"}
- context.client_context.custom: metadata including
  bedrockAgentCoreToolName (prefixed with target name, e.g.
  "file-operations___read_file")

The handler strips the target prefix from the tool name and dispatches
to the appropriate function.
"""

from shared import error
from tools import (
    create_folder,
    read_file,
    write_file,
    list_files,
    delete_folder,
    get_presigned_url,
    extract_document_text,
)

# Delimiter used by the Gateway to prefix tool names with the target name.
_DELIMITER = "___"

_TOOL_DISPATCH = {
    "create_folder": create_folder,
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    "delete_folder": delete_folder,
    "get_presigned_url": get_presigned_url,
    "extract_document_text": extract_document_text,
}


def handler(event, context):
    """Lambda handler for AgentCore Gateway tool invocations.

    The Gateway sends:
    - event: flat dict of tool input properties
    - context.client_context.custom["bedrockAgentCoreToolName"]:
      the namespaced tool name (e.g. "file-operations___read_file")

    Also supports the legacy format where event contains toolName/toolInput
    for backward compatibility with direct invocations.
    """
    tool_name = None
    tool_input = event

    try:
        custom = context.client_context.custom
        if custom and "bedrockAgentCoreToolName" in custom:
            full_name = custom["bedrockAgentCoreToolName"]
            if _DELIMITER in full_name:
                tool_name = full_name[full_name.index(_DELIMITER) + len(_DELIMITER):]
            else:
                tool_name = full_name
    except (AttributeError, TypeError, KeyError):
        pass

    # Fallback: legacy format with toolName/toolInput in event body
    if not tool_name:
        tool_name = event.get("toolName")
        tool_input = event.get("toolInput", {})

    if not tool_name:
        return error("Missing required field: toolName")

    tool_fn = _TOOL_DISPATCH.get(tool_name)
    if not tool_fn:
        return error(f"Unknown tool: {tool_name}")

    return tool_fn(tool_input)
