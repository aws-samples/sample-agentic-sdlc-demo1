"""Workflow Planning Agent for the AI-SDLC Multi-Agent System.

This agent loads prior context, assesses which stages should execute or skip,
generates an execution plan with a Mermaid flowchart, and handles user overrides.

Produces artifacts in aidlc-docs/inception/plans/:
- execution-plan.md

Updates SharedState execution_plan with stages_to_execute, stages_to_skip,
and per_unit_stages.

Has an approval gate with user override handling.
"""

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.file_io import write_artifact
from tools.questions import read_answers
from tools.validation import validate_mermaid

# Conditional stages that can be EXECUTE or SKIP
CONDITIONAL_STAGES = [
    "application_design",
    "units_generation",
    "functional_design",
    "nfr_requirements",
    "nfr_design",
    "infrastructure_design",
]

EXECUTION_PLAN_PATH = "aidlc-docs/inception/plans/execution-plan.md"


@tool(context=True)
def load_prior_context(tool_context: dict) -> dict:
    """Load all prior context from completed inception stages.

    Gathers reverse engineering artifacts, requirements, user stories,
    and their actual content to inform workflow planning decisions.

    Args:
        tool_context: Strands tool context with invocation_state.

    Returns:
        Dict with available context from prior stages, including artifact content.
    """
    from tools._compat import get_state
    from tools.file_ops import read_file

    state = get_state(tool_context)
    artifacts = state.get("artifacts", {})
    stage_progress = state.get("stage_progress", {})

    context = {
        "brownfield": state.get("brownfield", False),
        "request_analysis": state.get("request_analysis", {}),
        "depth_level": state.get("depth_level", "standard"),
        "completed_stages": [
            s for s, status in stage_progress.items() if status == "completed"
        ],
        "has_re_artifacts": bool(artifacts.get("reverse_engineering", {}).get("business_overview")),
        "has_requirements": bool(artifacts.get("requirements", {}).get("requirements_doc")),
        "has_user_stories": bool(artifacts.get("user_stories", {}).get("stories")),
    }

    # Load actual artifact content so the planning agent can make informed decisions
    artifact_content = {}

    # Load requirements document content. The per-session ``artifacts`` map is
    # NOT serialized into aidlc-state.md, so on a fresh session (notably the
    # SA's session after the BA→SA handoff) the pointer is absent even though
    # the BA already wrote the file. Fall back to the canonical path so the
    # file is read by its well-known location under the shared artifact prefix.
    req_path = artifacts.get("requirements", {}).get("requirements_doc", "")
    if not req_path:
        req_path = "aidlc-docs/inception/requirements/requirements.md"
    try:
        artifact_content["requirements"] = read_file(state, req_path)
    except (FileNotFoundError, RuntimeError):
        pass

    # Load user stories content (same canonical-path fallback as requirements).
    stories_path = artifacts.get("user_stories", {}).get("stories", "")
    if not stories_path:
        stories_path = "aidlc-docs/inception/user-stories/stories.md"
    try:
        artifact_content["user_stories"] = read_file(state, stories_path)
    except (FileNotFoundError, RuntimeError):
        pass

    # Load reverse engineering business overview
    re_overview_path = artifacts.get("reverse_engineering", {}).get("business_overview", "")
    if re_overview_path:
        try:
            artifact_content["reverse_engineering_overview"] = read_file(state, re_overview_path)
        except (FileNotFoundError, RuntimeError):
            pass

    context["artifact_content"] = artifact_content

    return context


@tool(context=True)
def assess_stages(tool_context: dict) -> dict:
    """Determine REQUIRED or SKIP for each conditional stage with rationale.

    IMPORTANT: This function writes the decisions directly into
    ``state["execution_plan"]["stages_to_execute"]`` and
    ``state["execution_plan"]["stages_to_skip"]`` so the graph routing
    conditions can read them.  The always-on stages (``code_generation``
    and ``build_and_test``) are included automatically.

    Args:
        tool_context: Strands tool context with invocation_state.

    Returns:
        Dict mapping stage names to {decision: "REQUIRED"|"SKIP", rationale: str}.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    request_analysis = state.get("request_analysis", {})
    complexity = request_analysis.get("complexity", "simple")
    scope = request_analysis.get("scope", "single_component")

    decisions = {}

    # Application Design: needed for multi-component or complex projects
    if scope in ("multiple_components", "system_wide") or complexity in ("moderate", "complex"):
        decisions["application_design"] = {
            "decision": "REQUIRED",
            "rationale": f"Scope ({scope}) and complexity ({complexity}) require application design",
        }
    else:
        decisions["application_design"] = {
            "decision": "SKIP",
            "rationale": f"Scope ({scope}) is small enough to skip application design",
        }

    # Units Generation: needed when multiple components exist
    if scope in ("multiple_components", "system_wide"):
        decisions["units_generation"] = {
            "decision": "REQUIRED",
            "rationale": f"Scope ({scope}) requires decomposition into units",
        }
    else:
        decisions["units_generation"] = {
            "decision": "SKIP",
            "rationale": f"Scope ({scope}) does not require unit decomposition",
        }

    # Functional Design: execute for moderate+ complexity
    if complexity in ("moderate", "complex"):
        decisions["functional_design"] = {
            "decision": "REQUIRED",
            "rationale": f"Complexity ({complexity}) warrants functional design",
        }
    else:
        decisions["functional_design"] = {
            "decision": "SKIP",
            "rationale": f"Complexity ({complexity}) is low enough to skip functional design",
        }

    # NFR Requirements: execute for complex or system-wide
    if complexity == "complex" or scope == "system_wide":
        decisions["nfr_requirements"] = {
            "decision": "REQUIRED",
            "rationale": "Complex or system-wide scope requires NFR analysis",
        }
    else:
        decisions["nfr_requirements"] = {
            "decision": "SKIP",
            "rationale": "Scope and complexity do not require dedicated NFR analysis",
        }

    # NFR Design: only if NFR Requirements is required
    if decisions["nfr_requirements"]["decision"] == "REQUIRED":
        decisions["nfr_design"] = {
            "decision": "REQUIRED",
            "rationale": "NFR requirements exist, design patterns needed",
        }
    else:
        decisions["nfr_design"] = {
            "decision": "SKIP",
            "rationale": "No NFR requirements to design for",
        }

    # Infrastructure Design: execute for complex or system-wide
    if complexity == "complex" or scope == "system_wide":
        decisions["infrastructure_design"] = {
            "decision": "REQUIRED",
            "rationale": "Complex scope requires infrastructure design",
        }
    else:
        decisions["infrastructure_design"] = {
            "decision": "SKIP",
            "rationale": "Scope does not require dedicated infrastructure design",
        }

    # ── Persist decisions into SharedState so graph conditions can read them ──
    exec_plan = state.get("execution_plan", {})

    stages_to_execute = []
    stages_to_skip = {}
    per_unit_stages = []

    for stage_name, info in decisions.items():
        if info["decision"] == "REQUIRED":
            stages_to_execute.append(stage_name)
            # Track per-unit stages (run once per unit in the construction loop)
            if stage_name in (
                "functional_design", "nfr_requirements",
                "nfr_design", "code_generation",
            ):
                per_unit_stages.append(stage_name)
        else:
            stages_to_skip[stage_name] = info["rationale"]

    # Always-on stages that are never conditional
    for always_on in ("code_generation", "build_and_test"):
        if always_on not in stages_to_execute:
            stages_to_execute.append(always_on)

    exec_plan["stages_to_execute"] = stages_to_execute
    exec_plan["stages_to_skip"] = stages_to_skip
    exec_plan["per_unit_stages"] = per_unit_stages
    state["execution_plan"] = exec_plan

    # Mark skipped stages in stage_progress so the state file reflects them.
    # Include user_stories if it's not in the execute list — the graph
    # condition will skip it, but nobody else marks it.
    sp = state.get("stage_progress", {})
    for stage_name in stages_to_skip:
        sp[stage_name] = "skipped"
    if "user_stories" not in stages_to_execute and sp.get("user_stories") != "completed":
        sp["user_stories"] = "skipped"

    return decisions


@tool(context=True)
def generate_execution_plan(tool_context: dict, plan_content: str) -> str:
    """Write the execution plan to aidlc-docs/inception/plans/execution-plan.md.

    Also validates that SharedState execution_plan.stages_to_execute has been
    populated (by ``assess_stages``).  If it is empty, this is a critical error
    — the graph conditions will not route to any downstream stage.

    Args:
        tool_context: Strands tool context with invocation_state.
        plan_content: The markdown content for the execution plan.

    Returns:
        Confirmation message with the file path.
    """
    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)

    # Safety net: stages_to_execute MUST be populated before we finalise
    exec_plan = state.get("execution_plan", {})
    if not exec_plan.get("stages_to_execute"):
        return (
            "ERROR: execution_plan.stages_to_execute is empty. "
            "You MUST call assess_stages before generate_execution_plan "
            "so the graph knows which stages to route to."
        )

    write_file(state, EXECUTION_PLAN_PATH, plan_content)

    # Update artifacts
    state.get("artifacts", {}).get("workflow_planning", {})["execution_plan"] = EXECUTION_PLAN_PATH

    # Mark workflow_planning as completed and persist state
    from tools.state import mark_stage_complete
    mark_stage_complete(state, "workflow_planning")

    return f"Execution plan generated at {EXECUTION_PLAN_PATH}"


MERMAID_DIAGRAM_PATH = "aidlc-docs/inception/plans/execution-plan-diagram.md"


@tool(context=True)
def generate_mermaid_diagram(tool_context: dict, mermaid_content: str) -> dict:
    """Create a Mermaid flowchart for the execution plan, validate it, and persist to S3.

    Writes the diagram to aidlc-docs/inception/plans/execution-plan-diagram.md
    wrapped in a Mermaid code block so it renders in any Markdown viewer.
    Automatically prepends a theme init directive for a white background
    with high-contrast colors.

    Args:
        tool_context: Strands tool context with invocation_state.
        mermaid_content: The Mermaid diagram content.

    Returns:
        Dict with validation result, file path, and fallback text.
    """
    # Inject white-background theme if the agent didn't include one
    stripped = mermaid_content.strip()
    if not stripped.startswith("%%{init:"):
        mermaid_content = (
            "%%{init: {'theme': 'base', 'themeVariables': {"
            "'primaryColor': '#4a90d9', 'primaryTextColor': '#000000', "
            "'primaryBorderColor': '#2c5f8a', 'lineColor': '#333333', "
            "'secondaryColor': '#f0f4fa', 'tertiaryColor': '#ffffff', "
            "'background': '#ffffff', 'mainBkg': '#ffffff', "
            "'nodeBorder': '#2c5f8a', 'clusterBkg': '#f8f9fa', "
            "'titleColor': '#000000', 'edgeLabelBackground': '#ffffff'"
            "}}}%%\n"
            + stripped
        )

    validation = validate_mermaid.__wrapped__(tool_context, mermaid_content)

    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)

    file_content = (
        "# Execution Plan — Mermaid Diagram\n\n"
        "```mermaid\n"
        f"{mermaid_content}\n"
        "```\n"
    )
    fallback = validation.get("fallback", "")
    if fallback:
        file_content += f"\n## Text Fallback\n\n```\n{fallback}\n```\n"

    write_file(state, MERMAID_DIAGRAM_PATH, file_content)

    return {
        "mermaid": mermaid_content,
        "valid": validation.get("valid", False),
        "errors": validation.get("errors", []),
        "fallback": fallback,
        "saved_to": MERMAID_DIAGRAM_PATH,
    }


SYSTEM_PROMPT = """\
You are the Workflow Planning Agent for the AI-SDLC multi-agent system.
You create an execution plan showing which stages will run and in what order.

Your responsibilities:
1. Use `load_prior_context` to gather all context from completed inception stages
   (reverse engineering, requirements, user stories). This tool returns both
   metadata AND the actual content of generated artifacts. You MUST read and
   consider the artifact_content in the response — it contains the full
   requirements document, user stories, and reverse engineering overview that
   should inform your stage assessment decisions.
2. Use `assess_stages` to determine EXECUTE or SKIP for each conditional stage:
   - Application Design
   - Units Generation
   - Functional Design
   - NFR Requirements
   - NFR Design
   - Infrastructure Design
   Each decision must have a documented rationale.
3. Use `generate_mermaid_diagram` to create a Mermaid flowchart visualization.
   The tool automatically applies a white-background theme with high-contrast
   colors. Do NOT include your own %%{init:}%% directive — just provide the
   raw flowchart content. If you apply custom styles to nodes, use dark text
   on light backgrounds (e.g. fill:#e8f0fe,stroke:#2c5f8a,color:#000000).
   Use `validate_mermaid` to ensure syntax is correct.
4. Use `generate_execution_plan` to write the execution plan to
   aidlc-docs/inception/plans/execution-plan.md including the Mermaid diagram.
5. Update SharedState execution_plan with:
   - stages_to_execute: list of stage names that will run
   - stages_to_skip: dict of {stage_name: rationale} for skipped stages
   - per_unit_stages: list of design stages that run per unit
6. Use `present_approval_gate` to present the plan with options:
   - Request Changes
   - Approve & Continue
   - Add Skipped Stages (if any stages were skipped)
7. Use `process_approval_response` to handle the user's response.

If the user requests stage inclusion/exclusion overrides, update the plan accordingly.

After completion:
- Set stage_progress.workflow_planning to "completed".
- STOP immediately. Do NOT proceed to Application Design or any other stage.
  We will automatically move onto the next stage.
"""

TOOLS = [
    load_prior_context,
    assess_stages,
    generate_execution_plan,
    generate_mermaid_diagram,
    validate_mermaid,
    read_answers,
    present_approval_gate,
    process_approval_response,
    write_artifact,
]


def create_workflow_planning_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Workflow Planning Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for workflow planning.
    """
    return create_stage_agent(
        stage_name="workflow_planning",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
