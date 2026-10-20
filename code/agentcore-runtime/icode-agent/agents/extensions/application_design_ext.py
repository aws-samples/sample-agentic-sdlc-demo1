"""Application Design Diagram Extension Agent for the AI-SDLC Multi-Agent System.

This agent extends application design by generating an AWS architecture diagram
based on the design artifacts already in memory. It calls the diagram-generation
Lambda tool via the Gateway to produce a visual diagram stored in S3.
"""

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent


DESIGN_DIR = "aidlc-docs/inception/application-design"


@tool(context=True)
def generate_architecture_diagram(tool_context: dict, architecture_description: str) -> dict:
    """Generate an AWS architecture diagram from a natural language description.

    Calls the generate_aws_architecture_diagram Lambda tool via the Gateway.
    The diagram is stored at {workspace_root}/aidlc-docs/inception/application-design/
    where workspace_root comes from project.workspace_root in SharedState.

    Args:
        tool_context: Strands tool context with invocation_state.
        architecture_description: Natural language description of the AWS
            architecture derived from the design artifacts in memory.

    Returns:
        A dict with the diagram S3 path or an error message.
    """
    import uuid
    from strands.tools.mcp import MCPClient
    from tools._compat import get_state
    from tools.gateway import call_tool

    state = get_state(tool_context)
    mcp_client = state.get("_mcp_client")

    if not mcp_client:
        return {"error": "No MCP client available — cannot reach Gateway"}

    project = state.get("project", {})
    workspace_root = project.get("workspace_root", "")
    if not workspace_root:
        return {"error": "No project.workspace_root in state — cannot determine storage path"}

    folder_path = f"{workspace_root.rstrip('/')}/{DESIGN_DIR}"

    args = {
        "architecture_description": architecture_description,
        "folder_path": folder_path,
    }

    try:
        result = call_tool(mcp_client, "generate_aws_architecture_diagram", args)

        content_text = ""
        content_items = result.get("content", [])
        for item in content_items:
            if isinstance(item, dict):
                content_text += item.get("text", "")
            elif hasattr(item, "text"):
                content_text += item.text

        return {"success": True, "diagram_path": content_text}
    except Exception as e:
        return {"error": f"Failed to generate architecture diagram: {e}"}


SYSTEM_PROMPT = """\
You are the Application Design Diagram Extension Agent for the AI-SDLC multi-agent system.

Your role is to generate an AWS architecture diagram based on the application design
artifacts that were produced during the Application Design stage. The design content
(components, services, dependencies) is already available in your conversation memory —
you do NOT need to read files.

WORKFLOW:
1. Review the design information available in memory — components, services,
   dependencies, and the overall application design.
2. Synthesize a comprehensive natural language description of the AWS architecture
   that includes all services, components, data flows, and dependencies.
3. Call `generate_architecture_diagram` with that description. The diagram will
   be stored automatically at {workspace_root}/aidlc-docs/inception/application-design/.

You proceed automatically without requiring user approval.

After completion, summarize using the [STAGE_SUMMARY] tag:

[STAGE_SUMMARY]
title: Architecture Diagram Generated ✅
| Property | Value |
|---|---|
| **Source** | Application Design Artifacts (from memory) |
| **Diagram** | {diagram_path} |
| **Status** | {Generated or Failed} |
[/STAGE_SUMMARY]

If the design information is not available in memory, respond with:

[STAGE_SUMMARY]
title: Architecture Diagram Skipped
| Property | Value |
|---|---|
| **Reason** | No application design information found in memory |
[/STAGE_SUMMARY]
"""

TOOLS = [generate_architecture_diagram]


def create_application_design_ext_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Application Design Diagram Extension Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for architecture diagram generation.
    """
    return create_stage_agent(
        stage_name="application_design_ext",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
