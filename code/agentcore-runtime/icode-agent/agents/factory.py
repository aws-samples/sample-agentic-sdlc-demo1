"""Stage Agent factory for the AI-SDLC Multi-Agent System."""

from __future__ import annotations

from typing import TYPE_CHECKING

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models import BedrockModel

if TYPE_CHECKING:
    from bedrock_agentcore.memory import AgentCoreMemorySessionManager


def create_stage_agent(
    stage_name: str,
    system_prompt: str,
    tools: list[callable],
    model: BedrockModel,
    session_manager: AgentCoreMemorySessionManager | None = None,
    window_size: int = 100,
) -> Agent:
    """Create a Strands Agent for a specific SDLC stage.

    Args:
        stage_name: Identifier for the SDLC stage (e.g., "requirements_analysis").
        system_prompt: The system prompt defining the agent's behavior.
        tools: List of @tool-decorated callables the agent can invoke.
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for conversation
            history persistence via AgentCore Memory.
        window_size: Sliding window size for conversation history (default 100).
            Increase for agents with long multi-turn HITL conversations.

    Returns:
        A configured Strands Agent ready for execution.
    """
    kwargs = {
        "model": model,
        "tools": tools,
        "system_prompt": system_prompt,
        "agent_id": stage_name,
        "conversation_manager": SlidingWindowConversationManager(
            window_size=window_size,
            per_turn=True,  # Trim context before every model call to prevent timeouts
        ),
    }
    if session_manager is not None:
        kwargs["session_manager"] = session_manager
    return Agent(**kwargs)
