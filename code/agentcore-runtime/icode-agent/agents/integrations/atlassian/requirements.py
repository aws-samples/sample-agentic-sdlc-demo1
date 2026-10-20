"""Atlassian Requirements Integration Agent for the AI-SDLC Multi-Agent System.

Reads the requirements.md S3 artifact produced by the requirements_analysis stage
and pushes it as a Confluence page titled "Requirements" in the configured
Confluence space. Adds a "pending-review" label and waits for user approval
in Confluence (verified by checking for an "approved" label).

All Atlassian tool calls go through the AgentCore Gateway via the MCPClient.
"""

from strands import Agent
from strands.models import BedrockModel

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.audit import log_audit_entry
from tools.human_input import human_input
from tools.integrations import (
    atlassian_confluence_add_label,
    atlassian_confluence_create_page,
    atlassian_confluence_get_labels,
    atlassian_ensure_confluence_space,
    atlassian_ensure_jira_project,
    disable_integration_for_session,
    log_integration_action,
    read_s3_artifact,
)

SYSTEM_PROMPT = """\
Atlassian Requirements Integration Agent. Execute these steps IN ORDER without skipping.

STEP 0: Call atlassian_ensure_confluence_space(space_name=state["project"]["selected_project"]) and atlassian_ensure_jira_project(project_name=same). Save the space_key.

STEP 1: Call read_s3_artifact(path="{workspace_root}/aidlc-docs/inception/requirements/requirements.md"). Use workspace_root from state["project"]["workspace_root"].

STEP 2: Call atlassian_confluence_create_page(space_key=<from STEP 0>, title="Requirements", content=<artifact content>). Save the page_id from the response.

STEP 3: Call atlassian_confluence_add_label(page_id=<from STEP 2>, label="pending-review").

STEP 4: CALL present_approval_gate NOW — this is mandatory, do not skip.
  stage="Atlassian Requirements Review"
  summary="📄 Requirements published to Confluence (pending-review). Review the page, add the 'approved' label in Confluence, then select Approve."
  options=["Approve — I have reviewed and added the 'approved' label", "Request changes — I want to modify the requirements"]

STEP 5: Call process_approval_response with the user's choice. Then call atlassian_confluence_get_labels(page_id=<from STEP 2>). If "approved" label missing, tell user and loop to STEP 4. If user chose "Request changes", ask what to change, update the page, loop to STEP 4.

STEP 6: Call log_integration_action(outcome="approved", action_type="confluence_create_page"). Call log_audit_entry. Set state["stage_progress"]["atlassian_requirements"]="completed". STOP.
"""

TOOLS = [
    human_input,
    present_approval_gate,
    process_approval_response,
    read_s3_artifact,
    atlassian_ensure_confluence_space,
    atlassian_ensure_jira_project,
    atlassian_confluence_create_page,
    atlassian_confluence_add_label,
    atlassian_confluence_get_labels,
    log_audit_entry,
    log_integration_action,
    disable_integration_for_session,
]


def create_atlassian_requirements_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Atlassian Requirements Integration Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for the atlassian_requirements stage.
    """
    return create_stage_agent(
        stage_name="atlassian_requirements",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
