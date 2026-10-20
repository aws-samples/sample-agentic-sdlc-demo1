"""Workspace Detection Extension Agent for the AI-SDLC Multi-Agent System.

This agent runs as a lightweight pass-through hook immediately before
Workspace Detection. It performs no external/demo context loading and calls
no tools; it simply acknowledges the start of the workflow and hands off to
Workspace Detection. It is kept as an explicit graph node (the BA segment's
entry node) so the orchestration topology is unchanged.
"""

from strands import Agent
from strands.models import BedrockModel

from agents.factory import create_stage_agent


SYSTEM_PROMPT = """\
You are the Workspace Detection Extension Agent for the AI-SDLC multi-agent system.

You run as a brief pass-through step immediately before Workspace Detection.
You do NOT call any tools, you do NOT load any external project context, and
you do NOT require user approval. Do not present an approval gate.

Emit exactly the following summary, then allow the workflow to proceed:

[STAGE_SUMMARY]
title: Initializing Workspace
| Property | Value |
|---|---|
| **Status** | Ready |
[/STAGE_SUMMARY]

The workflow will now proceed to Workspace Detection.
"""

TOOLS: list = []


def create_workspace_detection_ext_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Workspace Detection Extension Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent that acts as a pass-through before
        Workspace Detection.
    """
    return create_stage_agent(
        stage_name="workspace_detection_ext",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
