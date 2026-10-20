"""Inception Complete Agent for the AI-SDLC Multi-Agent System.

This is the final node in the Inception phase. It congratulates the user on
completing all inception stages and points them to the Construction phase.
It does not present an approval gate — the inception phase simply completes.
"""

from strands import Agent, tool
from strands.models import BedrockModel

from agents.factory import create_stage_agent


@tool(context=True)
def mark_inception_complete(tool_context: dict) -> str:
    """Mark the inception phase as complete in the state file.

    Returns:
        Confirmation message.
    """
    from tools._compat import get_state
    from tools.state import mark_stage_complete

    state = get_state(tool_context)
    mark_stage_complete(state, "inception_complete")
    return "Inception phase marked as complete in the state file."

SYSTEM_PROMPT = """\
You are the Inception Complete Agent for the AI-SDLC multi-agent system.
You are the FINAL stage of the Inception phase.

Your job is to:
1. Congratulate the user on completing the entire Inception phase.
2. Provide a brief recap of what was accomplished (workspace detection,
   requirements analysis, workflow planning, application design, units generation).
3. Confirm that all design artifacts, requirements, and unit decompositions
   are ready and saved in the project workspace.
4. Tell the user the project is now ready to hand off to the **Software
   Developer**: the Solution Architect can assign it to the Developer (via the
   "Assign to Software Developer" action), who will receive a packaged
   construction brief to take into an AI-assisted IDE (e.g. Kiro) for coding.

IMPORTANT — Your message MUST:
- Be warm, celebratory, and professional.
- Clearly state that the Inception and design phases are now COMPLETE.
- Make it clear the design artifacts are ready and the next step is the
  handoff to the Software Developer.

DO NOT present an approval gate, and DO NOT ask the user to choose between
options. There is no further decision to make here.

To finish: call `mark_inception_complete` to record completion in the state
file, then deliver your final congratulations/next-steps message. The workflow
will then halt at the Software Developer handoff — do NOT call other tools.
"""

TOOLS = [
    mark_inception_complete,
]


def create_inception_complete_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Inception Complete Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for inception completion.
    """
    return create_stage_agent(
        stage_name="inception_complete",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
