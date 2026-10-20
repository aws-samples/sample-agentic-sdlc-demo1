"""Human-in-the-loop input tools for interactive CLI and WebSocket sessions.

Uses Strands' native interrupt system for WebSocket mode: when the agent
needs user input, it raises an interrupt that pauses the Graph execution
and returns control to the WebSocket handler in main_ws.py.  The handler
sends the prompt to the client, waits for the response, and resumes the
Graph with the user's answer.

In CLI mode, delegates to the original strands_tools handoff_to_user.

See: https://strandsagents.com/docs/user-guide/concepts/interrupts/
"""

import logging
from html import escape as _html_escape
from typing import Any

from strands import tool
from strands.types.tools import ToolContext
from strands_tools import handoff_to_user as _original_module

logger = logging.getLogger(__name__)

# Commands that trigger a graceful exit
_EXIT_COMMANDS = {"exit", "quit", "/bye", "/exit", "/quit"}


def _sanitize_message(message: str) -> str:
    """Escape XML-special characters so prompt_toolkit doesn't choke."""
    return _html_escape(message, quote=False)


@tool(name="human_input", context=True)
def human_input(
    tool_context: ToolContext,
    message: str,
    breakout_of_loop: bool = False,
) -> str:
    """Pause execution and ask the user a question.  Returns the user's response.

    Use this tool whenever you need ANY input, confirmation, or decision
    from the user.  Set breakout_of_loop=True only when you want to
    completely stop the agent and hand control back to the user (e.g. at
    the very end of a workflow).

    Args:
        tool_context: Strands tool context (injected by framework).
        message: The question or prompt to display to the user.
        breakout_of_loop: If True, hand off control entirely to the user
            (end of workflow).  If False, collect input and continue.

    Returns:
        The user's response text.
    """
    state = tool_context.invocation_state or {}

    # --- WebSocket mode: use Strands interrupts ---
    if state.get("_ws_mode"):
        if breakout_of_loop:
            # For breakout, raise an interrupt with a flag so main_ws.py
            # knows to just display the message without expecting a response.
            tool_context.interrupt(
                "human_input_breakout",
                reason={"message": message, "breakout": True},
            )
            return f"Agent handoff completed. Message displayed to user: {message}"

        # Interactive mode: raise an interrupt.  The Graph pauses here.
        # main_ws.py receives the interrupt, sends the prompt over WebSocket,
        # waits for the user's response, and resumes the Graph.
        # On the second invocation tool_context.interrupt() returns the
        # user's response directly.
        user_response = tool_context.interrupt(
            "human_input_request",
            reason={"message": message, "breakout": False},
        )

        logger.info("Interrupt-based human_input response received")

        # Check for exit commands
        if isinstance(user_response, str) and user_response.strip().lower() in _EXIT_COMMANDS:
            state["stop_event_loop"] = True
            logger.info("User requested graceful exit via '%s'", user_response.strip())
            return "User requested to exit the session. Stopping gracefully."

        return f"User response received: {user_response}"

    # --- CLI mode: delegate to original handoff_to_user ---
    safe_message = _sanitize_message(message)
    tool_use = getattr(tool_context, "tool_use", {}) or {}
    tool_dict: dict[str, Any] = {
        "toolUseId": tool_use.get("toolUseId", "cli"),
        "input": {"message": safe_message, "breakout_of_loop": breakout_of_loop},
    }
    result = _original_module.handoff_to_user(tool_dict)

    # Check for exit commands in CLI mode
    if result.get("status") == "success":
        content = result.get("content", [])
        if content:
            text = content[0].get("text", "")
            prefix = "User response received: "
            if text.startswith(prefix):
                user_text = text[len(prefix):].strip().lower()
                if user_text in _EXIT_COMMANDS:
                    state["stop_event_loop"] = True
                    logger.info(
                        "User requested graceful exit via '%s'",
                        user_text,
                    )
                    return "User requested to exit the session. Stopping gracefully."
            return text

    # Fallback: extract whatever text the original tool returned
    fallback_content = result.get("content", [{}])
    if fallback_content:
        return fallback_content[0].get("text", "No response")
    return "No response"


__all__ = ["human_input"]
