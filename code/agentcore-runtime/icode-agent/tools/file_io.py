"""File I/O tools for the AI-SDLC Multi-Agent System.

Provides tools for reading, writing, and scanning files with path validation
to enforce the aidlc-docs/ directory structure convention.

Operations are routed through the MCP Gateway to S3 via an MCPClient stored
in ``invocation_state["_mcp_client"]``.  The session ID (used as the S3 key
prefix) is read from ``invocation_state["_session_id"]``.
"""

import fnmatch
import os
import posixpath

from strands.tools import tool

from tools._compat import get_state
from tools.gateway import call_tool

# File extensions considered documentation artifacts
_DOC_EXTENSIONS = {".md", ".txt", ".rst", ".adoc"}


def _is_documentation_path(path: str) -> bool:
    """Check if a path is a documentation artifact based on extension."""
    _, ext = os.path.splitext(path)
    return ext.lower() in _DOC_EXTENSIONS


def _validate_write_path(path: str) -> None:
    """Validate that documentation goes under aidlc-docs/ and code does not.

    Raises:
        ValueError: If a documentation file is written outside aidlc-docs/
                    or a code file is written inside aidlc-docs/.
    """
    normalized = os.path.normpath(path)
    under_aidlc = normalized.startswith("aidlc-docs" + os.sep) or normalized.startswith("aidlc-docs/")

    if _is_documentation_path(path) and not under_aidlc:
        raise ValueError(
            f"Documentation artifacts must be placed under aidlc-docs/: {path}"
        )

    if not _is_documentation_path(path) and under_aidlc:
        raise ValueError(
            f"Application code must not be placed under aidlc-docs/: {path}"
        )


def _build_s3_key(session_id: str, path: str) -> str:
    """Construct an S3 key from a session ID and relative path.

    Documentation artifacts (.md, .txt, .rst, .adoc) are stored under
    ``{session_id}/aidlc-docs/{relative_path}``.  Non-documentation files
    are stored under ``{session_id}/{relative_path}``.

    The *path* is normalised to forward-slash separators so that S3 keys
    are consistent regardless of the host OS.
    """
    # Normalise to forward slashes for S3 key consistency
    normalized = posixpath.normpath(path.replace(os.sep, "/"))

    if _is_documentation_path(path):
        # Strip a leading "aidlc-docs/" if the caller already included it
        prefix = "aidlc-docs/"
        if normalized.startswith(prefix):
            relative = normalized[len(prefix):]
        else:
            relative = normalized
        return f"{session_id}/aidlc-docs/{relative}"
    else:
        return f"{session_id}/{normalized}"


def _raise_on_gateway_error(result: dict, context_msg: str = "") -> None:
    """Raise ``RuntimeError`` when the Gateway returns an error response.

    Args:
        result: The parsed response dict from the MCP Gateway.
        context_msg: Optional extra context to include in the exception.

    Raises:
        RuntimeError: If ``result`` contains an error indicator.
    """
    is_error = (
        (isinstance(result, dict) and result.get("isError"))
        or (isinstance(result, dict) and result.get("status") == "error")
    )
    if is_error:
        error_text = ""
        # Try to extract error text from content list
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


@tool(context=True)
def write_artifact(tool_context, path: str, content: str) -> str:
    """Write content via Gateway write_file tool.

    1. Validates path using existing _validate_write_path logic.
    2. Builds S3 key using _build_s3_key.
    3. Creates parent folder prefix via Gateway create_folder.
    4. Writes content via Gateway write_file.
    5. Raises exception if Gateway returns isError.

    Args:
        tool_context: Strands tool context with invocation_state.
        path: Relative file path to write to.
        content: Content to write to the file.

    Returns:
        A confirmation message with the file path written.
    """
    _validate_write_path(path)

    state = get_state(tool_context)
    mcp_client = state["_mcp_client"]
    session_id = state["_session_id"]

    s3_key = _build_s3_key(session_id, path)

    # Create parent folder prefix if the key contains a directory separator
    parent = posixpath.dirname(s3_key)
    if parent:
        folder_result = call_tool(mcp_client, "create_folder", {"path": parent + "/"})
        _raise_on_gateway_error(folder_result, f"create_folder for {parent}")

    # Write the file content
    write_result = call_tool(mcp_client, "write_file", {"path": s3_key, "content": content})
    _raise_on_gateway_error(write_result, f"write_file for {s3_key}")

    return f"Successfully wrote {len(content)} bytes to {path}"


@tool(context=True)
def read_artifact(tool_context, path: str) -> str:
    """Read content via Gateway read_file tool.

    Args:
        tool_context: Strands tool context with invocation_state.
        path: File path to read from.

    Returns:
        The file content as a string.

    Raises:
        FileNotFoundError: If the Gateway indicates the file was not found.
        RuntimeError: For other Gateway errors.
    """
    state = get_state(tool_context)
    mcp_client = state["_mcp_client"]
    session_id = state["_session_id"]

    s3_key = _build_s3_key(session_id, path)

    read_result = call_tool(mcp_client, "read_file", {"path": s3_key})

    # Check for error responses
    is_error = (
        (isinstance(read_result, dict) and read_result.get("isError"))
        or (isinstance(read_result, dict) and read_result.get("status") == "error")
    )
    if is_error:
        error_text = ""
        content_list = read_result.get("content", [])
        if isinstance(content_list, list):
            for item in content_list:
                if isinstance(item, dict) and "text" in item:
                    error_text = item["text"]
                    break
        if not error_text:
            error_text = str(read_result)
        # Map "not found" style errors to FileNotFoundError
        if "not found" in error_text.lower() or "nosuchkey" in error_text.lower() or "404" in error_text:
            raise FileNotFoundError(f"File not found: {path}")
        raise RuntimeError(f"read_file for {s3_key}: {error_text}")

    # Extract text content from the response
    if isinstance(read_result, dict):
        content_list = read_result.get("content", [])
        if isinstance(content_list, list):
            for item in content_list:
                if isinstance(item, dict) and "text" in item:
                    return item["text"]
        # Fallback: if there's a direct "content" string
        if isinstance(read_result.get("content"), str):
            return read_result["content"]

    # If the result is a plain string, return it directly
    if isinstance(read_result, str):
        return read_result

    return str(read_result)


@tool(context=True)
def scan_directory(tool_context, path: str, patterns: list[str]) -> list[str]:
    """List files via Gateway list_files tool with glob pattern filtering.

    Args:
        tool_context: Strands tool context with invocation_state.
        path: Directory path to scan (relative).
        patterns: List of glob patterns to match (e.g., ["*.md", "*.py"]).

    Returns:
        Sorted list of matching file paths relative to the scanned directory.
    """
    state = get_state(tool_context)
    mcp_client = state["_mcp_client"]
    session_id = state["_session_id"]

    # Build the S3 prefix from session_id and path
    normalized_path = posixpath.normpath(path.replace(os.sep, "/"))
    if normalized_path == ".":
        prefix = f"{session_id}/"
    else:
        prefix = f"{session_id}/{normalized_path}/"

    list_result = call_tool(mcp_client, "list_files", {"prefix": prefix})
    _raise_on_gateway_error(list_result, f"list_files for {prefix}")

    # Extract the list of keys from the response
    keys: list[str] = []
    if isinstance(list_result, dict):
        content_list = list_result.get("content", [])
        if isinstance(content_list, list):
            for item in content_list:
                if isinstance(item, dict) and "text" in item:
                    import json
                    try:
                        parsed = json.loads(item["text"])
                        if isinstance(parsed, list):
                            keys = parsed
                    except (json.JSONDecodeError, TypeError):
                        pass
                    break
    elif isinstance(list_result, list):
        keys = list_result

    # Strip the session prefix and filter against glob patterns
    matches = []
    for key in keys:
        # Strip the session_id prefix to get the relative path
        if key.startswith(f"{session_id}/"):
            relative = key[len(f"{session_id}/"):]
        else:
            relative = key

        # Get the filename for glob matching
        filename = posixpath.basename(relative)

        # Check if the file matches any of the provided patterns
        for pattern in patterns:
            if fnmatch.fnmatch(filename, pattern):
                matches.append(relative)
                break

    return sorted(set(matches))
