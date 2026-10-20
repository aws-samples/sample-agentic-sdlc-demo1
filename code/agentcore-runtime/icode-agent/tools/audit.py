"""Audit logging tool for the AI-SDLC Multi-Agent System.

Provides append-only audit trail logging with ISO 8601 timestamps.
Entries are buffered in SharedState's audit_buffer and flushed to
aidlc-docs/audit.md via the Gateway S3.
"""

from datetime import datetime, timezone

from strands.tools import tool


def _format_audit_entry(timestamp: str, stage: str, entry_type: str, content: str) -> str:
    """Format a single audit entry as markdown."""
    return (
        f"## [{timestamp}] {stage} — {entry_type}\n"
        f"{content}\n"
        f"**Action**: {entry_type}\n"
    )


def _flush_buffer_to_s3(audit_buffer: list, state: dict) -> int:
    """Flush in-memory audit buffer entries to S3 via the Gateway.

    Appends all buffered entries to the audit file and clears the buffer.

    Args:
        audit_buffer: List of audit entry dicts from SharedState.
        state: SharedState dict (must contain ``_mcp_client``, ``_session_id``).

    Returns:
        Number of entries flushed.
    """
    if not audit_buffer:
        return 0

    from tools.file_ops import append_file

    entries_text = ""
    for entry in audit_buffer:
        entries_text += _format_audit_entry(
            entry["timestamp"],
            entry["stage"],
            entry["entry_type"],
            entry["content"],
        )
        entries_text += "\n"

    append_file(state, "aidlc-docs/audit.md", entries_text)

    count = len(audit_buffer)
    audit_buffer.clear()
    return count


@tool(context=True)
def log_audit_entry(tool_context: dict, stage: str, entry_type: str, content: str) -> str:
    """Append an ISO 8601 timestamped audit entry to aidlc-docs/audit.md via Gateway S3.

    The entry is first added to the in-memory audit_buffer in SharedState,
    then the buffer is flushed to S3. This ensures append-only behavior.

    Args:
        tool_context: Strands tool context with invocation_state.
        stage: The SDLC stage name (e.g., "Requirements Analysis").
        entry_type: Type of entry (user_input, ai_response, approval_prompt, stage_transition).
        content: The raw content to log (never summarized for user_input).

    Returns:
        Confirmation message with the timestamp of the logged entry.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    timestamp = datetime.now(timezone.utc).isoformat()

    entry = {
        "timestamp": timestamp,
        "stage": stage,
        "entry_type": entry_type,
        "content": content,
    }

    # Add to in-memory buffer
    audit_buffer = state.get("audit_buffer", [])
    if "audit_buffer" not in state:
        state["audit_buffer"] = audit_buffer
    audit_buffer.append(entry)

    # Flush buffer to S3
    flushed = _flush_buffer_to_s3(audit_buffer, state)

    return f"Logged audit entry at {timestamp} ({flushed} entries flushed)"
