"""Units Generation Agent for the AI-SDLC Multi-Agent System.

This agent decomposes the system into manageable Unit_of_Work items with
defined boundaries, responsibilities, and story mappings.

Produces artifacts in aidlc-docs/inception/application-design/:
- unit-of-work.md
- unit-of-work-dependency.md
- unit-of-work-story-map.md

Updates SharedState with ordered list of Unit_of_Work items.

Has an approval gate at completion.
"""

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.file_io import write_artifact
from tools.questions import read_answers

UNITS_DIR = "aidlc-docs/inception/application-design"

UNIT_ARTIFACTS = {
    "unit_of_work": "unit-of-work.md",
    "unit_dependency": "unit-of-work-dependency.md",
    "unit_story_map": "unit-of-work-story-map.md",
}


@tool(context=True)
def decompose_units(tool_context: dict, units: list[dict]) -> list[dict]:
    """Break the system into Unit_of_Work items with boundaries and responsibilities.

    Each unit should have: name, display_name, boundaries, responsibilities,
    stories, and dependencies.

    Args:
        tool_context: Strands tool context with invocation_state.
        units: List of unit dicts with name, boundaries, responsibilities, stories.

    Returns:
        The validated list of Unit_of_Work items.
    """
    validated = []
    for unit in units:
        validated.append({
            "name": unit.get("name", ""),
            "display_name": unit.get("display_name", unit.get("name", "")),
            "boundaries": unit.get("boundaries", ""),
            "responsibilities": unit.get("responsibilities", []),
            "stories": unit.get("stories", []),
            "dependencies": unit.get("dependencies", []),
            "design_stages_completed": [],
            "code_generation_completed": False,
        })

    return validated


@tool(context=True)
def generate_unit_artifacts(
    tool_context: dict,
    unit_of_work_content: str,
    unit_dependency_content: str,
    unit_story_map_content: str,
) -> dict:
    """Create unit-of-work.md, unit-of-work-dependency.md, unit-of-work-story-map.md.

    Args:
        tool_context: Strands tool context with invocation_state.
        unit_of_work_content: Markdown content for unit-of-work.md.
        unit_dependency_content: Markdown content for unit-of-work-dependency.md.
        unit_story_map_content: Markdown content for unit-of-work-story-map.md.

    Returns:
        Dict mapping artifact keys to their file paths.
    """
    import posixpath

    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)

    contents = {
        "unit_of_work": unit_of_work_content,
        "unit_dependency": unit_dependency_content,
        "unit_story_map": unit_story_map_content,
    }

    generated_paths = {}
    ug_artifacts = state.get("artifacts", {}).get("units_generation", {})

    for key, filename in UNIT_ARTIFACTS.items():
        rel_path = posixpath.join(UNITS_DIR, filename)
        write_file(state, rel_path, contents[key])
        generated_paths[key] = rel_path
        ug_artifacts[key] = rel_path

    state.get("artifacts", {})["units_generation"] = ug_artifacts

    # Mark units_generation as completed and persist state
    from tools.state import mark_stage_complete
    mark_stage_complete(state, "units_generation")

    return generated_paths


@tool(context=True)
def update_unit_list(tool_context: dict, units: list[dict]) -> str:
    """Update SharedState units.items with the ordered list of Unit_of_Work items.

    Args:
        tool_context: Strands tool context with invocation_state.
        units: Ordered list of Unit_of_Work dicts.

    Returns:
        Confirmation message with the number of units registered.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    state_units = state.get("units", {})
    state_units["items"] = units
    state_units["current_unit_index"] = 0
    state_units["completed_units"] = []

    return f"Registered {len(units)} units of work in SharedState"


SYSTEM_PROMPT = """\
You are the Units Generation Agent for the AI-SDLC multi-agent system.
You decompose the system into manageable units of work for the Construction phase.

CONDITIONAL EXECUTION:
- You only execute when the execution plan includes Units Generation.
- If not in the execution plan, set stage_progress.units_generation to "skipped" and return.

Your responsibilities:
0. FIRST — Load prior phase context to inform your unit decomposition:
   a) Call `read_answers` with path
      aidlc-docs/inception/requirements/requirement-verification-questions.md
      to retrieve all decisions from Requirements Analysis.
   b) Call `read_answers` with path
      aidlc-docs/inception/application-design/design-questions.md
      to retrieve Application Design decisions (skip if not found).
   Use these prior decisions as the foundation for your unit decomposition.
   The components, services, and dependencies defined in Application Design
   should directly inform how you break the system into units.
1. Use `decompose_units` to break the system into Unit_of_Work items, each with:
   - name: kebab-case identifier
   - display_name: human-readable name
   - boundaries: description of unit boundaries
   - responsibilities: list of what this unit is responsible for
   - stories: list of story IDs mapped to this unit
   - dependencies: list of other unit names this depends on
2. Use `generate_unit_artifacts` to create documentation:
   - unit-of-work.md: Detailed unit descriptions
   - unit-of-work-dependency.md: Dependency relationships between units
   - unit-of-work-story-map.md: Mapping of stories to units
3. Use `update_unit_list` to register the ordered list of units in SharedState
   for the Construction per-unit loop.
4. Use `present_approval_gate` to present the unit decomposition for approval.
5. Use `process_approval_response` to handle the user's response.

If the user requests changes, incorporate feedback and regenerate.

After completion:
- Set stage_progress.units_generation to "completed".
- Ensure units.items is populated in SharedState.
- STOP immediately. We will automatically move onto the next stage.
"""

TOOLS = [
    decompose_units,
    generate_unit_artifacts,
    update_unit_list,
    read_answers,
    present_approval_gate,
    process_approval_response,
    write_artifact,
]


def create_units_generation_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Units Generation Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for units generation.
    """
    return create_stage_agent(
        stage_name="units_generation",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
