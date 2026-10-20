"""User Stories Agent for the AI-SDLC Multi-Agent System.

This agent assesses whether user stories add value, creates a story plan,
and generates stories with INVEST criteria and acceptance criteria.
Conditional execution based on multi-factor assessment.

Produces artifacts in aidlc-docs/inception/user-stories/:
- stories.md
- personas.md

Has an approval gate at completion.
"""

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.file_io import write_artifact
from tools.questions import detect_ambiguities, read_answers, record_answer

STORIES_PATH = "aidlc-docs/inception/user-stories/stories.md"
PERSONAS_PATH = "aidlc-docs/inception/user-stories/personas.md"


@tool(context=True)
def assess_story_need(tool_context: dict) -> dict:
    """Multi-factor assessment to determine if user stories add value.

    Evaluates: user-facing features, multi-persona systems, complex business
    logic, and cross-team projects.

    Args:
        tool_context: Strands tool context with invocation_state.

    Returns:
        Dict with assessment factors and overall 'should_generate' bool.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    request_analysis = state.get("request_analysis", {})
    scope = request_analysis.get("scope", "single_component")
    complexity = request_analysis.get("complexity", "simple")
    request_type = request_analysis.get("request_type", "new_feature")

    user_facing = request_type in ("new_feature", "enhancement")
    multi_persona = scope in ("multiple_components", "system_wide")
    complex_logic = complexity in ("moderate", "complex")
    cross_team = scope == "system_wide"

    factors = {
        "user_facing_features": user_facing,
        "multi_persona": multi_persona,
        "complex_business_logic": complex_logic,
        "cross_team": cross_team,
    }

    should_generate = sum(factors.values()) >= 2

    return {
        "factors": factors,
        "should_generate": should_generate,
        "reason": "Multiple factors indicate user stories add value"
        if should_generate
        else "Insufficient factors to warrant user story generation",
    }


@tool(context=True)
def create_story_plan(tool_context: dict, plan_content: str) -> str:
    """Create a story generation plan with embedded questions.

    Args:
        tool_context: Strands tool context with invocation_state.
        plan_content: The markdown content for the story plan.

    Returns:
        Confirmation message.
    """
    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)
    rel_path = "aidlc-docs/inception/user-stories/story-plan.md"
    write_file(state, rel_path, plan_content)

    return f"Story plan created at {rel_path}"


@tool(context=True)
def generate_stories(tool_context: dict, stories_content: str, personas_content: str) -> dict:
    """Generate stories.md and personas.md in aidlc-docs/inception/user-stories/.

    Args:
        tool_context: Strands tool context with invocation_state.
        stories_content: Markdown content for user stories with INVEST criteria.
        personas_content: Markdown content for user personas.

    Returns:
        Dict with paths to generated files.
    """
    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)

    for rel_path, content in [(STORIES_PATH, stories_content), (PERSONAS_PATH, personas_content)]:
        write_file(state, rel_path, content)

    # Update artifacts in SharedState
    artifacts = state.get("artifacts", {}).get("user_stories", {})
    artifacts["stories"] = STORIES_PATH
    artifacts["personas"] = PERSONAS_PATH
    state.get("artifacts", {})["user_stories"] = artifacts

    # Mark user_stories as completed and persist state
    from tools.state import mark_stage_complete
    mark_stage_complete(state, "user_stories")

    return {"stories": STORIES_PATH, "personas": PERSONAS_PATH}


SYSTEM_PROMPT = """\
You are the User Stories Agent for the AI-SDLC multi-agent system.
You generate user stories with acceptance criteria when they add value.

CONDITIONAL EXECUTION:
- First use `assess_story_need` to determine if user stories should be generated.
- If the assessment says should_generate=false, set stage_progress.user_stories
  to "skipped" and return immediately.

Your responsibilities (when stories are needed):
0. FIRST — Load prior phase decisions by calling `read_answers` with path
   aidlc-docs/inception/requirements/requirement-verification-questions.md
   to retrieve all decisions from Requirements Analysis (target audience,
   core features, platform, etc.). Use these as context for persona creation
   and story generation — do NOT re-ask questions already answered.
1. Use `assess_story_need` to evaluate whether stories add value.
2. Use `create_story_plan` to create a story generation plan with embedded questions.
3. Use `read_answers` to collect user answers and `detect_ambiguities` to check
   for vague or contradictory responses. Resolve ambiguities before proceeding.
4. Use `generate_stories` to create:
   - stories.md with stories following INVEST criteria and acceptance criteria
   - personas.md with user personas
5. Use `present_approval_gate` to present generated stories for user approval.
6. Use `process_approval_response` to handle the user's response.

If the user requests changes, incorporate feedback and regenerate.

After completion:
- Set stage_progress.user_stories to "completed" (or "skipped" if not needed).
- STOP immediately. We will automatically move onto the next stage.
"""

TOOLS = [
    assess_story_need,
    create_story_plan,
    generate_stories,
    read_answers,
    record_answer,
    detect_ambiguities,
    present_approval_gate,
    process_approval_response,
    write_artifact,
]


def create_user_stories_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the User Stories Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for user story generation.
    """
    return create_stage_agent(
        stage_name="user_stories",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
