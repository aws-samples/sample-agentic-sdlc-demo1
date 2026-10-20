"""Application Design Agent for the AI-SDLC Multi-Agent System.

This agent generates high-level component definitions, method signatures,
service definitions, and dependency relationships when new components are needed.

Produces 5 artifacts in aidlc-docs/inception/application-design/:
- components.md
- component-methods.md
- services.md
- component-dependency.md
- application-design.md

Has an approval gate at completion.
"""

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.file_io import write_artifact
from tools.questions import create_questions_file, detect_ambiguities, read_answers, record_answer

DESIGN_DIR = "aidlc-docs/inception/application-design"

DESIGN_ARTIFACTS = {
    "components": "components.md",
    "component_methods": "component-methods.md",
    "services": "services.md",
    "component_dependency": "component-dependency.md",
    "application_design": "application-design.md",
}


@tool(context=True)
def generate_design_artifacts(
    tool_context: dict,
    components_content: str,
    component_methods_content: str,
    services_content: str,
    component_dependency_content: str,
    application_design_content: str,
) -> dict:
    """Generate all 5 application design artifacts.

    Args:
        tool_context: Strands tool context with invocation_state.
        components_content: Markdown content for components.md.
        component_methods_content: Markdown content for component-methods.md.
        services_content: Markdown content for services.md.
        component_dependency_content: Markdown content for component-dependency.md.
        application_design_content: Markdown content for application-design.md.

    Returns:
        Dict mapping artifact keys to their file paths.
    """
    import posixpath

    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)

    contents = {
        "components": components_content,
        "component_methods": component_methods_content,
        "services": services_content,
        "component_dependency": component_dependency_content,
        "application_design": application_design_content,
    }

    generated_paths = {}
    ad_artifacts = state.get("artifacts", {}).get("application_design", {})

    for key, filename in DESIGN_ARTIFACTS.items():
        rel_path = posixpath.join(DESIGN_DIR, filename)
        write_file(state, rel_path, contents[key])
        generated_paths[key] = rel_path
        ad_artifacts[key] = rel_path

    state.get("artifacts", {})["application_design"] = ad_artifacts

    # Mark application_design as completed and persist state
    from tools.state import mark_stage_complete
    mark_stage_complete(state, "application_design")

    return generated_paths


SYSTEM_PROMPT = """\
You are the Application Design Agent for the AI-SDLC multi-agent system.
You design high-level components and service layers when new components are needed.

CRITICAL CONSTRAINT — AWS Only:
All infrastructure, deployment, and cloud service decisions MUST target AWS
(Amazon Web Services) exclusively. When designing components, services, and
architecture, use AWS services (Lambda, ECS, API Gateway, DynamoDB, RDS, S3,
Cognito, CloudFront, SQS, SNS, EventBridge, Step Functions, etc.). Do NOT
suggest or reference other cloud providers.

CONDITIONAL EXECUTION:
- You only execute when the execution plan includes Application Design.
- If not in the execution plan, set stage_progress.application_design to "skipped" and return.

Your responsibilities:
0. FIRST — Load prior phase decisions by calling `read_answers` with path
   aidlc-docs/inception/requirements/requirement-verification-questions.md
   to retrieve all decisions from Requirements Analysis (tech stack, database,
   auth model, platform, etc.). These are established constraints — do NOT
   re-ask any question that was already answered. Your design questions should
   go deeper into architecture and implementation details that build on these.
1. CHECK FOR EXISTING QUESTIONS (Resume Support):
   Call `read_answers` with path
   aidlc-docs/inception/application-design/design-questions.md
   to check whether a questions file already exists from a previous session.
   — If it returns a not-found error: the file does not exist.
     Use `create_questions_file` to generate **at most 3-4 high-impact**
     design clarification questions that do NOT overlap with Requirements
     Analysis answers. Ask only about architecture/implementation decisions
     that genuinely shape the design (e.g. component boundaries, storage
     model, integration/auth patterns); skip anything already established or
     low-impact. Fewer, sharper questions are strongly preferred.
   — If it succeeds: the file already exists. Do NOT call `create_questions_file`.
     Examine the returned list to identify answered vs. unanswered questions.
2. COLLECT ANSWERS (Resume-Aware):
   If the questions file already existed, first replay every previously
   answered question as a brief summary for the user's context:
     ✅ **Question {X} of {Y}**: {Question text}
     ↳ Your previous answer: {answer}
   Then continue from the FIRST unanswered question using `human_input`,
   calling `record_answer` after each response. If ALL questions are already
   answered, skip directly to ambiguity detection.
   Do NOT re-ask questions that already have answers.
   Use `detect_ambiguities` to check for vague or contradictory responses.
   Resolve all ambiguities before proceeding.
3. Use `generate_design_artifacts` to create all 5 design files in
   {workspace_root}/aidlc-docs/inception/application-design/ where {workspace_root} is the project.workspace_root value from shared state:
   - components.md: Component definitions and responsibilities
   - component-methods.md: Method signatures for each component
   - services.md: Service layer definitions
   - component-dependency.md: Dependency relationships between components
   - application-design.md: Consolidated design overview
4. Use `present_approval_gate` to present the design for user approval.
5. Use `process_approval_response` to handle the user's response.

If the user requests changes, incorporate feedback and regenerate affected artifacts.

After completion:
- Set stage_progress.application_design to "completed".
- STOP immediately. We will automatically move onto the next stage.
"""

TOOLS = [
    create_questions_file,
    read_answers,
    record_answer,
    detect_ambiguities,
    generate_design_artifacts,
    present_approval_gate,
    process_approval_response,
    write_artifact,
]


def create_application_design_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Application Design Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for application design.
    """
    return create_stage_agent(
        stage_name="application_design",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
