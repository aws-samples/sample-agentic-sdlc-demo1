"""Infrastructure Design Agent for the AI-SDLC Multi-Agent System.

This agent generates infrastructure mapping and deployment architecture
artifacts for a specific unit of work.

Produces artifacts in aidlc-docs/construction/{unit-name}/infrastructure-design/.

Has an approval gate at completion.
"""

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.file_io import write_artifact


@tool(context=True)
def generate_infra_artifacts(
    tool_context: dict, unit_name: str, artifacts_content: dict
) -> dict:
    """Generate infrastructure design artifacts for a unit via Gateway S3.

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
    output_dir = f"aidlc-docs/construction/{unit_name}/infrastructure-design"

    generated_paths = {}
    for filename, content in artifacts_content.items():
        rel_path = posixpath.join(output_dir, filename)
        write_file(state, rel_path, content)
        generated_paths[filename] = rel_path

    # Update construction artifacts in SharedState
    construction = state.get("artifacts", {}).get("construction", {})
    unit_artifacts = construction.get(unit_name, {})
    unit_artifacts["infrastructure_design"] = generated_paths
    construction[unit_name] = unit_artifacts
    state.get("artifacts", {})["construction"] = construction

    # Mark infrastructure_design as completed and persist state
    from tools.state import mark_stage_complete
    mark_stage_complete(state, "infrastructure_design")

    return generated_paths


SYSTEM_PROMPT = """\
You are the Infrastructure Design Agent for the AI-SDLC multi-agent system.
You generate infrastructure mapping and deployment architecture for the current unit.

CRITICAL CONSTRAINT — AWS Only:
All infrastructure and deployment designs MUST use AWS services exclusively.
Map components to specific AWS services (Lambda, ECS, EC2, API Gateway,
DynamoDB, RDS, S3, CloudFront, Cognito, SQS, SNS, EventBridge, Step
Functions, CDK, CloudFormation, etc.). Do NOT reference other cloud providers.

CONDITIONAL EXECUTION:
- You only execute when the execution plan includes Infrastructure Design.
- If not in the execution plan, set stage_progress.infrastructure_design to "skipped" and return.

Your responsibilities:
1. Use `generate_infra_artifacts` to create infrastructure files in
   aidlc-docs/construction/{unit-name}/infrastructure-design/:
   - infrastructure-mapping.md: Infrastructure component mapping
   - deployment-architecture.md: Deployment architecture and configuration
2. Use `present_approval_gate` to present the infrastructure design for approval.
3. Use `process_approval_response` to handle the user's response.

If the user requests changes, incorporate feedback and regenerate.

After completion:
- Set stage_progress.infrastructure_design to "completed".
- STOP immediately. We will automatically move onto the next stage.
"""

TOOLS = [
    generate_infra_artifacts,
    present_approval_gate,
    process_approval_response,
    write_artifact,
]


def create_infrastructure_design_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Infrastructure Design Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for infrastructure design.
    """
    return create_stage_agent(
        stage_name="infrastructure_design",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
