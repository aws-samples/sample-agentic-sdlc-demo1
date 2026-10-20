# Error Handling and Logging Guide

This document describes the error handling patterns, logging conventions, and audit trail mechanisms used across the project.

## Gateway Error Format

All Gateway tool calls return responses in MCP content format. Errors are indicated by the `isError` flag.

**Success:**
```json
{"content": [{"type": "text", "text": "File written: path/to/file.md"}]}
```

**Error:**
```json
{"content": [{"type": "text", "text": "File not found: path/to/file.md"}], "isError": true}
```

### Detecting Errors

The `tools/file_ops.py` module provides helper functions used throughout the agent codebase:

- `_raise_on_error(result, context_msg)` — Raises `RuntimeError` when `result["isError"]` is truthy or `result["status"] == "error"`. Extracts error text from the `content` array.
- `_is_not_found(result)` — Returns `True` when the error text contains "not found", "NoSuchKey", or "404". Used to map Gateway errors to Python `FileNotFoundError`.
- `_extract_text(result)` — Extracts the text string from a Gateway response's `content` array.

Source: `code/agentcore-runtime/icode-agent/tools/file_ops.py`, lines 40–75

### Error Handling in Tool Functions

Tools follow a consistent pattern:

```python
from tools.file_ops import read_file, write_file

# read_file raises FileNotFoundError for missing files, RuntimeError for other errors
try:
    content = read_file(state, "path/to/file.md")
except FileNotFoundError:
    return "File does not exist"
except RuntimeError as e:
    return f"Gateway error: {e}"
```

The `file_io.py` module (`write_artifact`, `read_artifact`, `scan_directory`) uses the same `_raise_on_gateway_error` pattern with context messages for debugging.

## Lambda Handler Error Pattern

The Lambda handler (`code/agentcore-gateway/lambda-tools/handler.py`) uses a dispatch pattern with explicit error returns:

```python
def _read_file(tool_input):
    file_path = tool_input.get("file_path")
    if not file_path:
        return _error("Missing required field: file_path")
    try:
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=file_path)
        content = response["Body"].read().decode("utf-8")
        return _success(content)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return _error(f"File not found: {file_path}")
        return _error(f"Failed to read file: {e}")
```

Conventions:
- Validate required fields first, return `_error(...)` immediately if missing
- Catch `ClientError` from boto3 and map to meaningful error messages
- Distinguish "not found" errors from other failures for proper upstream handling
- Unknown tool names return `_error(f"Unknown tool: {tool_name}")`

## WebSocket Error Handling

### Server Side (`main_ws.py`)

The WebSocket handler wraps the entire session in a try/except block:

```python
try:
    # ... session lifecycle ...
except Exception:
    logger.exception("WebSocket handler error for session %s", session_id)
    if shared_state is not None and mcp_client is not None:
        _persist_state(mcp_client, storage_id, shared_state)
finally:
    if mcp_client is not None:
        mcp_client.__exit__(None, None, None)
    await websocket.close()
```

Key behaviors:
- State is always persisted on error (if MCP client is available)
- MCP client is cleaned up in the `finally` block
- WebSocket is closed gracefully
- Graph execution errors send `{"type": "error", "message": "Workflow execution failed"}` to the client
- Individual graph execution failures are caught per-invocation, allowing the session to continue

#### Handoff Interrupts vs. Workflow Failures

A handoff at a segment boundary (`handoff_gate__{seg}`, design Section 5b) is
**not** a workflow failure. The gate node calls
`raise InterruptException(...)` with a reason payload of
`{breakout: True, handoff: True, from_segment, message}`.

`_run_graph_with_interrupts` in `main_ws.py` handles this through a single
`_handle_handoff_interrupt` helper, reachable via two channels:

1. **Result-event channel** — Strands yields a final
   `{"result": GraphResult(status=INTERRUPTED, interrupts=[...])}` event.
   Detected via `graph_result.status == Status.INTERRUPTED` or
   `graph._interrupt_state.activated`.
2. **Raised-exception channel** — Strands lets `InterruptException` propagate
   out of `stream_async`. Caught around the inner `async for` loop. If the
   interrupt's reason carries `handoff: True`, it is routed through the same
   helper; otherwise the exception is re-raised so the outer
   `except Exception` surfaces it as `Workflow execution failed: ...` exactly
   as it does for any other error.

Both channels produce the same observable sequence: one `handoff_required`
message (see `docs/websocket-message-protocol.md`), `shared_state["handoff"]["pending"] = True`,
SharedState persisted via the Gateway, graph loop halted without resuming.
The next persona reconnects through a fresh WebSocket session after the
Frontend records the transition through the Project API.

Tests live in `code/agentcore-runtime/icode-agent/tests/test_ws_handoff_breakout.py`
and cover both channels plus the negative case (a non-handoff
`InterruptException` must still propagate to the outer error handler).

Source: `code/agentcore-runtime/icode-agent/main_ws.py`, lines 200–350

### Client Side (`WebSocketContext.tsx`)

The frontend handles WebSocket errors with reconnection:

- `ws.onerror` → sets status to `"error"`
- `ws.onclose` (unintentional) → retries up to 3 times with exponential backoff (1s, 2s, 4s)
- Each retry attempt shows an error message in the chat: "Connection lost. Reconnecting in Xs..."
- After 3 failed retries: "Connection failed after 3 retries. Please try again."
- Intentional close (user navigates away) → no retry

Source: `code/ui-frontend/app/context/WebSocketContext.tsx`, lines 140–170

### Human Input Timeout

The `human_input` tool in WebSocket mode waits up to 600 seconds (10 minutes) for a user response. On timeout:

```python
except TimeoutError:
    return {"toolUseId": tool_use_id, "status": "error",
            "content": [{"text": "Timed out waiting for user response"}]}
```

Source: `code/agentcore-runtime/icode-agent/tools/human_input.py`, line 95

## Session Retry with Resumption

The `main_interactive` function (`main.py`, line 170) implements automatic retry for CLI sessions:

- Up to 5 retries with exponential backoff: 2s, 4s, 8s, 16s, 30s (capped)
- State is persisted before each retry
- On reconnect, `_find_resume_stage` determines where to continue
- `KeyboardInterrupt` saves state and exits cleanly
- After max retries, state is saved and the exception is re-raised

## OAuth Token Retry

`OAuthProvider.get_access_token()` (`tools/oauth_provider.py`, line 60) retries token requests:

- 3 attempts with exponential backoff: 1s, 2s
- Catches `requests.RequestException` and `KeyError`
- Raises `RuntimeError` after all attempts fail

## State Persistence Error Handling

`mark_stage_complete` and `persist_state` (`tools/state.py`) handle missing MCP client gracefully:

```python
if mcp_client is not None and session_id:
    md_content = _state_to_markdown(state)
    _persist_via_gateway(mcp_client, session_id, md_content)
else:
    logger.warning("mark_stage_complete: _mcp_client or _session_id missing; skipping persistence")
```

This allows agents to function in test environments without a Gateway connection.

## Artifact Notification Error Handling

`_notify_artifact_updated` in `tools/file_ops.py` is best-effort — failures are silently ignored so file writes are never blocked by WebSocket issues:

```python
try:
    future = asyncio.run_coroutine_threadsafe(websocket.send_text(msg), loop)
    future.result(timeout=5)
except Exception:
    pass  # Best-effort — don't block file writes
```

## Logging Conventions

### Python Logging

The agent uses Python's standard `logging` module. Each module creates its own logger:

```python
import logging
logger = logging.getLogger(__name__)
```

Log levels used:
- `logger.info(...)` — Session lifecycle events, state loading/saving
- `logger.warning(...)` — Graceful degradation (missing Memory ID, corrupted state, skipped persistence)
- `logger.error(...)` — Failed operations that don't crash the session
- `logger.exception(...)` — Unexpected errors with full traceback
- `logger.debug(...)` — Verbose details (MCP client cleanup, XML escaping)

### CloudWatch Log Groups

The CDK stack creates dedicated log groups for each AgentCore component:

| Log Group | Retention | Content |
|-----------|-----------|---------|
| `/aws/vendedlogs/bedrock-agentcore/runtime/APPLICATION_LOGS/{name}` | 30 days | Runtime container stdout/stderr |
| `/aws/vendedlogs/bedrock-agentcore/runtime/USAGE_LOGS/{name}` | 30 days | Runtime usage metrics |
| `/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/{name}` | 30 days | Gateway request/response logs |
| `/aws/vendedlogs/bedrock-agentcore/memory/APPLICATION_LOGS/{name}` | 30 days | Memory read/write operations |
| `/aws/lambda/agentcore-file-tools` | Default | Lambda function logs |
| `bedrock_model_invocation_loggrp` | 30 days | Bedrock model invocation logs |

### Querying Logs

```bash
# Runtime errors
aws logs filter-log-events \
  --log-group-name "/aws/vendedlogs/bedrock-agentcore/runtime/APPLICATION_LOGS/strands_agent" \
  --filter-pattern "ERROR"

# Lambda errors
aws logs filter-log-events \
  --log-group-name "/aws/lambda/agentcore-file-tools" \
  --filter-pattern "ERROR"

# Tail logs in real time
aws logs tail "/aws/vendedlogs/bedrock-agentcore/runtime/APPLICATION_LOGS/strands_agent" --follow
```

## Audit Trail

### Mandatory Audit Logging

Every agent is required to call `log_audit_entry` (`tools/audit.py`) for all interactions. The orchestrator injects the audit tool and instructions into every agent's system prompt.

Entry types:
- `stage_transition` — When entering a new stage
- `user_input` — Every user response (logged verbatim)
- `ai_response` — Significant agent outputs (summarized)
- `tool_call` — After each tool invocation
- `approval_prompt` — When presenting an approval gate
- `approval_decision` — User's approval/rejection choice

Entries are appended to `state["audit_buffer"]` (in-memory list) and serialized with the state markdown on persistence.

### Frontend Error Boundary

The `ErrorBoundary` component (`code/ui-frontend/app/components/ErrorBoundary.tsx`) catches React rendering errors:

- Logs error + component stack to `console.error`
- Displays a user-friendly error card with a "Reload" button
- Wraps `LandingPage` and `ProjectWorkspace` routes

## Error Response Summary

| Layer | Error Type | Handling |
|-------|-----------|----------|
| Lambda | Missing input field | Returns `_error("Missing required field: ...")` |
| Lambda | S3 NoSuchKey | Returns `_error("File not found: ...")` |
| Lambda | S3 ClientError | Returns `_error("Failed to ...: {error}")` |
| Lambda | Unknown tool | Returns `_error("Unknown tool: ...")` |
| Gateway wrapper | isError response | Raises `RuntimeError` or `FileNotFoundError` |
| Agent tool | File not found | Catches `FileNotFoundError`, returns user-friendly message |
| WebSocket | Connection drop | Persists state, retries with backoff |
| WebSocket | Handoff InterruptException (handoff=True) | Routed via `_handle_handoff_interrupt`; emits `handoff_required`, persists state, halts loop — **not** a failure |
| WebSocket | Graph execution failure | Persists state, sends error message to client |
| OAuth | Token request failure | Retries 3x with backoff, raises `RuntimeError` |
| Frontend | WebSocket close | Retries 3x with backoff, shows error in chat |
| Frontend | React crash | ErrorBoundary shows reload button |
