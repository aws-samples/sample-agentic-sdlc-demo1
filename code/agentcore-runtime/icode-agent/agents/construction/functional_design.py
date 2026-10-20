"""Functional Design Agent for the AI-SDLC Multi-Agent System.

This agent generates functional design artifacts for a specific unit of work,
including business logic models, business rules, and domain entities.

Produces artifacts in aidlc-docs/construction/{unit-name}/functional-design/.

Has an approval gate at completion.
"""

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.file_io import write_artifact
from tools.questions import create_questions_file, detect_ambiguities, read_answers, record_answer


@tool(context=True)
def load_unit_context(tool_context: dict) -> dict:
    """Load context for the current unit of work from SharedState.

    Args:
        tool_context: Strands tool context with invocation_state.

    Returns:
        Dict with current unit info, requirements, and prior design context.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    units = state.get("units", {})
    items = units.get("items", [])
    index = units.get("current_unit_index", 0)

    current_unit = items[index] if index < len(items) else {}

    return {
        "unit": current_unit,
        "unit_index": index,
        "total_units": len(items),
        "request_analysis": state.get("request_analysis", {}),
        "depth_level": state.get("depth_level", "standard"),
        "brownfield": state.get("brownfield", False),
    }


@tool(context=True)
def generate_functional_artifacts(
    tool_context: dict, unit_name: str, artifacts_content: dict
) -> dict:
    """Generate functional design artifacts for a unit via Gateway S3.

    Args:
        tool_context: Strands tool context with invocation_state.
        unit_name: The kebab-case unit name.
        artifacts_content: Dict mapping filename to markdown content.

    Returns:
        Dict mapping filenames to their written paths.
    """
    import posixpath

    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)
    output_dir = f"aidlc-docs/construction/{unit_name}/functional-design"

    generated_paths = {}
    for filename, content in artifacts_content.items():
        rel_path = posixpath.join(output_dir, filename)
        write_file(state, rel_path, content)
        generated_paths[filename] = rel_path

    # Update construction artifacts in SharedState
    construction = state.get("artifacts", {}).get("construction", {})
    unit_artifacts = construction.get(unit_name, {})
    unit_artifacts["functional_design"] = generated_paths
    construction[unit_name] = unit_artifacts
    state.get("artifacts", {})["construction"] = construction

    # Mark functional_design as completed and persist state
    from tools.state import mark_stage_complete
    mark_stage_complete(state, "functional_design")

    return generated_paths


SYSTEM_PROMPT = """\
You are the Functional Design Agent for the AI-SDLC multi-agent system.
You generate functional design artifacts for the current unit of work.

CRITICAL CONSTRAINT — AWS Only:
All cloud service references in functional designs MUST use AWS services.
Do NOT reference other cloud providers.

Your responsibilities:
0. FIRST — Load prior phase decisions by calling `read_answers` with these paths
   (skip any that return a not-found error):
   - aidlc-docs/inception/requirements/requirement-verification-questions.md
     (Requirements Analysis: tech stack, database, auth, platform)
   - aidlc-docs/inception/application-design/design-questions.md
     (Application Design: architecture style, storage model, components)
   Use these as established constraints — do NOT re-ask questions already answered.
   Your design questions should focus on unit-specific implementation details.
1. Use `load_unit_context` to get the current unit's information and context.
2. CHECK FOR EXISTING QUESTIONS (Resume Support):
   Call `read_answers` with the questions file path for this unit
   (aidlc-docs/construction/{unit-name}/functional-design/design-questions.md)
   to check whether a questions file already exists from a previous session.
   — If it returns a not-found error: the file does not exist.
     Use `create_questions_file` to generate design clarification questions
     that do NOT overlap with Requirements Analysis or Application Design answers.
   — If it succeeds: the file already exists. Do NOT call `create_questions_file`.
     Examine the returned list to identify answered vs. unanswered questions.
3. COLLECT ANSWERS (Resume-Aware):
   If the questions file already existed, first replay every previously
   answered question as a brief summary for the user's context:
     ✅ **Question {X} of {Y}**: {Question text}
     ↳ Your previous answer: {answer}
   Then continue from the FIRST unanswered question using `human_input`,
   calling `record_answer` after each response. If ALL questions are already
   answered, skip directly to ambiguity detection.
   Do NOT re-ask questions that already have answers.
   Use `detect_ambiguities` to check for vague or contradictory responses.
   Resolve ambiguities before proceeding.
4. Use `generate_functional_artifacts` to create design files in
   aidlc-docs/construction/{unit-name}/functional-design/:
   - business-logic-model.md
   - business-rules.md
   - domain-entities.md
   - frontend-components.md (if applicable)
5. Use `present_approval_gate` to present the design for user approval.
6. Use `process_approval_response` to handle the user's response.

If the user requests changes, incorporate feedback and regenerate.

After completion:
- Set stage_progress.functional_design to "completed".
- STOP immediately. We will automatically move onto the next stage.
"""

TOOLS = [
    load_unit_context,
    create_questions_file,
    read_answers,
    record_answer,
    detect_ambiguities,
    generate_functional_artifacts,
    present_approval_gate,
    process_approval_response,
    write_artifact,
]


def create_functional_design_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Functional Design Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for functional design.
    """
    return create_stage_agent(
        stage_name="functional_design",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
