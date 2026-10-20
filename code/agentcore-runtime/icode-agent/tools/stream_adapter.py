"""WebSocket stream adapter for the AI-SDLC Multi-Agent System.

Translates Strands graph streaming events into structured JSON messages
sent over a Starlette WebSocket connection. This is the WebSocket
counterpart to the CLI-based :mod:`tools.streaming` module.
"""

import asyncio
import json


class StreamAdapter:
    """Translates Strands graph events to WebSocket JSON messages."""

    def __init__(self, websocket, hitl_response_queue: asyncio.Queue | None = None):
        """Initialize with a Starlette WebSocket connection.

        Args:
            websocket: A Starlette ``WebSocket`` instance.
            hitl_response_queue: Optional asyncio.Queue for HITL responses.
                When provided, send_human_input_request reads from the queue
                instead of calling websocket.receive_text() directly, avoiding
                the concurrent-receiver race condition.
        """
        self.websocket = websocket
        self._hitl_response_queue = hitl_response_queue
        self._last_tool_use_id = None  # Deduplicate tool_use events

    async def process_event(self, event: dict) -> None:
        """Process a single graph streaming event.

        Event type mapping:

        - ``data`` (text chunk) -> text_chunk
        - ``multiagent_node_start`` -> stage_start
        - ``multiagent_node_stop`` -> stage_complete
        - ``multiagent_node_stream`` -> unwrap inner event and forward
        - ``current_tool_use`` -> tool_use (deduplicated by toolUseId)

        Unknown event types are silently ignored.
        """
        # --- Multi-agent graph events ---
        if event.get("type") == "multiagent_node_start":
            self._last_tool_use_id = None
            await self.websocket.send_text(
                json.dumps({"type": "stage_start", "node_id": event["node_id"]})
            )
        elif event.get("type") == "multiagent_node_stop":
            self._last_tool_use_id = None
            await self.websocket.send_text(
                json.dumps({"type": "stage_complete", "node_id": event["node_id"]})
            )
        elif event.get("type") == "multiagent_node_stream":
            inner = event.get("event", {})
            if inner:
                await self.process_event(inner)
        # --- Agent-level events ---
        elif "data" in event:
            await self.websocket.send_text(
                json.dumps({"type": "text_chunk", "content": event["data"]})
            )
        # Forward tool_use events as lightweight "thinking" indicators so
        # the user sees progress while the agent is working.  Deduplicated
        # by toolUseId to avoid flooding the UI with repeated events for
        # the same tool invocation.
        elif "current_tool_use" in event:
            tool_use = event["current_tool_use"]
            tool_use_id = tool_use.get("toolUseId", "")
            tool_name = tool_use.get("name", "")
            if tool_use_id and tool_use_id != self._last_tool_use_id and tool_name:
                self._last_tool_use_id = tool_use_id
                await self.websocket.send_text(
                    json.dumps({
                        "type": "thinking",
                        "tool": tool_name,
                    })
                )

    async def send_approval_required(
        self, stage: str, summary: str, options: list[str]
    ) -> None:
        """Send approval gate message to client."""
        await self.websocket.send_text(
            json.dumps({
                "type": "approval_required",
                "stage": stage,
                "summary": summary,
                "options": options,
            })
        )

    async def send_human_input_request(self, prompt: str) -> str:
        """Send a human_input prompt and wait for the client's response.

        When a hitl_response_queue is available, reads from the queue to
        avoid the concurrent-receiver race condition with the main receive loop.
        """
        await self.websocket.send_text(
            json.dumps({"type": "human_input", "prompt": prompt})
        )
        if self._hitl_response_queue is not None:
            # Queue-based: single receive loop routes all messages here
            response = await self._hitl_response_queue.get()
        else:
            # Fallback: direct receive (only safe when no other coroutine is receiving)
            response = await self.websocket.receive_text()
        return response

    async def send_human_input_notification(self, message: str) -> None:
        """Send a human_input notification (no response expected)."""
        await self.websocket.send_text(
            json.dumps({"type": "human_input_notify", "message": message})
        )

    async def send_handoff_required(
        self,
        project_uuid: str,
        from_role: str,
        to_role: str,
        summary: str,
    ) -> None:
        """Send a ``handoff_required`` message to the client.

        Emitted when the SDLC graph reaches a Handoff_Gate (design Sections
        5b / 9, Req 6.3). The Frontend renders this as an actionable in-app
        card whose label is derived from the registry's display labels using
        ``from_role`` / ``to_role`` (no email / SNS — in-app only, Req 14.6).

        Args:
            project_uuid: Stable Project identifier; used by the Frontend
                to call ``projectService.transition(uuid, ...)``.
            from_role: Cognito group name of the segment that just halted.
            to_role: Cognito group name of the next-owning persona.
            summary: Human-readable handoff summary surfaced in the card.
        """
        await self.websocket.send_text(
            json.dumps({
                "type": "handoff_required",
                "project_uuid": project_uuid,
                "from_role": from_role,
                "to_role": to_role,
                "summary": summary,
            })
        )

    async def send_workflow_complete(self) -> None:
        """Send workflow completion message."""
        await self.websocket.send_text(json.dumps({"type": "workflow_complete"}))
