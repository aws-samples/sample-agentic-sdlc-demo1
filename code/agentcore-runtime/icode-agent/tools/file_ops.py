"""Unified file I/O abstraction for the AI-SDLC Multi-Agent System.

All agent file operations (read, write, list, exists) go through this module.
Operations are routed to S3 via the MCP Gateway using ``tools.gateway``.

The ``_mcp_client`` and ``_session_id`` are extracted from the shared state
(invocation_state) that every tool receives via ``tool_context``.

Usage from any agent tool::

    from tools.file_ops import read_file, write_file, file_exists, list_files

    content = read_file(state, "aidlc-docs/inception/requirements/requirements.md")
    write_file(state, "aidlc-docs/inception/plans/execution-plan.md", md_text)
    exists  = file_exists(state, "aidlc-docs/aidlc-state.md")
    files   = list_files(state, "aidlc-docs/inception/")
"""

import posixpath

from tools.gateway import call_tool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_mcp_client(state: dict):
    """Extract the MCP client from shared state."""
    client = state.get("_mcp_client")
    if client is None:
        raise RuntimeError("_mcp_client not found in shared state")
    return client


def _get_session_id(state: dict) -> str:
    """Extract the session/storage ID from shared state."""
    sid = state.get("_session_id", "")
    if not sid:
        raise RuntimeError("_session_id not found in shared state")
    return sid


def _build_s3_key(session_id: str, relative_path: str) -> str:
    """Build an S3 key from session ID and a relative path.

    Normalises to forward slashes and strips leading ``./``. Rejects any path
    that would escape the per-session prefix (path traversal) or that is
    absolute, so a caller-supplied ``relative_path`` can never reach objects
    outside ``{session_id}/`` (e.g. ``"../../secret.txt"`` or ``"/etc/passwd"``).

    Raises:
        ValueError: if ``relative_path`` escapes the session directory.
    """
    normalized = posixpath.normpath(relative_path.replace("\\", "/"))

    # After normalization, any remaining leading ``..`` (or a ``/../`` segment,
    # or a leading ``/``) means the path resolves outside ``{session_id}/``.
    # ``posixpath.normpath`` collapses interior ``..`` (e.g. ``a/../b`` -> ``b``),
    # so a surviving ``..`` is an actual escape attempt.
    if (
        normalized == ".."
        or normalized.startswith("../")
        or normalized.startswith("/")
        or "/../" in normalized
    ):
        raise ValueError(
            f"Unsafe path (path traversal detected): {relative_path!r}"
        )

    if normalized == ".":
        return session_id + "/"
    return f"{session_id}/{normalized}"


def _raise_on_error(result, context_msg: str = "") -> None:
    """Raise ``RuntimeError`` when the Gateway returns an error response."""
    is_error = (
        (isinstance(result, dict) and result.get("isError"))
        or (isinstance(result, dict) and result.get("status") == "error")
    )
    if is_error:
        error_text = _extract_text(result) or str(result)
        prefix = f"{context_msg}: " if context_msg else ""
        raise RuntimeError(f"{prefix}{error_text}")


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


def _is_not_found(result) -> bool:
    """Return True if the Gateway result is a not-found error."""
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

def write_file(state: dict, relative_path: str, content: str) -> str:
    """Write *content* to S3 via the Gateway.

    Creates the parent folder prefix automatically.
    If a WebSocket stream adapter is available in shared state, sends
    an ``artifact_updated`` notification so the Document Viewer can
    fetch and display the file.

    Args:
        state: SharedState dict (must contain ``_mcp_client``, ``_session_id``).
        relative_path: Path relative to the workspace root
                       (e.g. ``"aidlc-docs/inception/plans/execution-plan.md"``).
        content: File content to write.

    Returns:
        Confirmation message.
    """
    mcp = _get_mcp_client(state)
    sid = _get_session_id(state)
    s3_key = _build_s3_key(sid, relative_path)

    # Ensure parent folder prefix exists
    parent = posixpath.dirname(s3_key)
    if parent:
        folder_result = call_tool(mcp, "create_folder", {"path": parent + "/"})
        _raise_on_error(folder_result, f"create_folder for {parent}")

    write_result = call_tool(mcp, "write_file", {"path": s3_key, "content": content})
    _raise_on_error(write_result, f"write_file for {s3_key}")

    # Notify the frontend Document Viewer about the new/updated file
    _notify_artifact_updated(state, s3_key)

    return f"Wrote {len(content)} bytes to {relative_path}"


def _notify_artifact_updated(state: dict, s3_key: str) -> None:
    """Send an artifact_updated WebSocket message with a presigned URL.

    Generates a short-lived S3 presigned GET URL via the Gateway's
    ``get_presigned_url`` tool and includes it in the WebSocket message
    so the frontend can fetch the file directly without an unauthenticated
    Lambda function URL.

    This is a best-effort notification — failures are silently ignored
    so file writes are never blocked by WebSocket issues.
    """
    import asyncio
    import json as _json

    loop = state.get("_ws_event_loop")
    if not loop:
        return

    websocket = state.get("_websocket")
    if not websocket:
        return

    # Generate a presigned URL for the file (best-effort)
    presigned_url = None
    try:
        mcp = _get_mcp_client(state)
        result = call_tool(mcp, "get_presigned_url", {"path": s3_key})
        url_text = _extract_text(result)
        if url_text and not (isinstance(result, dict) and result.get("isError")):
            presigned_url = url_text
    except Exception:
        pass  # Fall back to no URL — frontend will show error gracefully

    payload = {"type": "artifact_updated", "file_path": s3_key}
    if presigned_url:
        payload["presigned_url"] = presigned_url

    msg = _json.dumps(payload)
    try:
        future = asyncio.run_coroutine_threadsafe(
            websocket.send_text(msg), loop
        )
        future.result(timeout=5)
    except Exception:
        pass  # Best-effort — don't block file writes


def read_file(state: dict, relative_path: str) -> str:
    """Read a file from S3 via the Gateway.

    Args:
        state: SharedState dict.
        relative_path: Path relative to the workspace root.

    Returns:
        The file content as a string.

    Raises:
        FileNotFoundError: If the file does not exist.
        RuntimeError: For other Gateway errors.
    """
    mcp = _get_mcp_client(state)
    sid = _get_session_id(state)
    s3_key = _build_s3_key(sid, relative_path)

    result = call_tool(mcp, "read_file", {"path": s3_key})

    if _is_not_found(result):
        raise FileNotFoundError(f"File not found: {relative_path}")

    _raise_on_error(result, f"read_file for {s3_key}")

    text = _extract_text(result)
    if text is not None:
        return text
    return str(result)


def file_exists(state: dict, relative_path: str) -> bool:
    """Check whether a file exists in S3 via the Gateway.

    Uses ``read_file`` and catches ``FileNotFoundError``.

    Args:
        state: SharedState dict.
        relative_path: Path relative to the workspace root.

    Returns:
        True if the file exists, False otherwise.
    """
    try:
        read_file(state, relative_path)
        return True
    except FileNotFoundError:
        return False


def list_files(state: dict, prefix: str) -> list[str]:
    """List files under *prefix* in S3 via the Gateway.

    Args:
        state: SharedState dict.
        prefix: Directory prefix relative to workspace root
                (e.g. ``"aidlc-docs/inception/"``).

    Returns:
        Sorted list of S3 keys (with session prefix stripped).
    """
    import json as _json

    mcp = _get_mcp_client(state)
    sid = _get_session_id(state)
    s3_prefix = _build_s3_key(sid, prefix)

    result = call_tool(mcp, "list_files", {"prefix": s3_prefix})
    _raise_on_error(result, f"list_files for {s3_prefix}")

    keys: list[str] = []
    if isinstance(result, dict):
        content_list = result.get("content", [])
        if isinstance(content_list, list):
            for item in content_list:
                if isinstance(item, dict) and "text" in item:
                    try:
                        parsed = _json.loads(item["text"])
                        if isinstance(parsed, list):
                            keys = parsed
                    except (_json.JSONDecodeError, TypeError):
                        # Treat as newline-separated list
                        keys = [
                            line.strip()
                            for line in item["text"].splitlines()
                            if line.strip()
                        ]
                    break
    elif isinstance(result, list):
        keys = result

    # Strip session prefix
    stripped = []
    session_prefix = f"{sid}/"
    for key in keys:
        if isinstance(key, str):
            if key.startswith(session_prefix):
                stripped.append(key[len(session_prefix):])
            else:
                stripped.append(key)

    return sorted(stripped)


def append_file(state: dict, relative_path: str, content: str) -> str:
    """Append *content* to an existing file in S3.

    Reads the current content, appends, and writes back.

    Args:
        state: SharedState dict.
        relative_path: Path relative to the workspace root.
        content: Content to append.

    Returns:
        Confirmation message.
    """
    try:
        existing = read_file(state, relative_path)
    except FileNotFoundError:
        existing = ""

    new_content = existing + content
    return write_file(state, relative_path, new_content)
