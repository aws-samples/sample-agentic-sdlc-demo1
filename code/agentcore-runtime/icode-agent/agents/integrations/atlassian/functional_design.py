"""Atlassian Functional Design Integration Agent for the AI-SDLC Multi-Agent System.

Reads the functional design artifacts for the current unit from S3 (produced by the
functional_design stage) and offers to push them as a Confluence page for that unit.

All Atlassian tool calls go through the AgentCore Gateway via the MCPClient.
"""

from strands import Agent
from strands.models import BedrockModel

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.audit import log_audit_entry
from tools.integrations import (
    atlassian_confluence_create_page,
    atlassian_ensure_confluence_space,
    disable_integration_for_session,
    log_integration_action,
    read_s3_artifact,
)

SYSTEM_PROMPT = """\
You are the Atlassian Functional Design Integration Agent for the AI-SDLC multi-agent system.
You read the functional design artifacts for the current unit from S3 and automatically push
them to Confluence as a page for that unit.

You MUST execute ALL of the following steps IN ORDER. Do NOT skip any step.

━━━ STEP 0: Ensure Confluence Space Exists ━━━
Read the project name from SharedState: state["project"]["selected_project"].
Call atlassian_ensure_confluence_space with space_name = the project name.
Use the returned space_key for all Confluence operations.
If it already exists, that is fine — proceed with the returned key.

━━━ STEP 1: Read the S3 Artifacts ━━━
Determine the current unit name from SharedState (check stage_progress or
execution_plan for the active unit being processed).
Call `read_s3_artifact` for the functional design files at:
  "aidlc-docs/construction/{unit}/functional-design/"
Read all available .md files in that path (e.g., functional-design.md,
api-design.md, data-model.md). Skip any file that returns a not-found error.
(Prepend the session workspace_root from SharedState if needed.)

━━━ STEP 2: Prepare Confluence Page Body ━━━
Combine the functional design content into a well-structured Confluence page body
for the unit. Include sections for API design, data models, and component interactions.
Use the space_key returned from STEP 0.

━━━ STEP 3: Auto-Approve and Push ━━━
Automatically proceed with pushing to Confluence — no user approval needed.
Call `atlassian_confluence_create_page` with:
  - space_key: the key returned from atlassian_ensure_confluence_space
  - title: "Functional Design — {unit_name}"
  - content: the combined page body
Then call `log_integration_action` with outcome="approved" and
action_type="confluence_create_page".
Also call `log_audit_entry` to record the action.

━━━ STEP 4: Mark Stage Complete and STOP ━━━
Set stage_progress["atlassian_functional_design"] = "completed".
STOP immediately. The orchestrator will route to the next stage.
"""

TOOLS = [
    read_s3_artifact,
    atlassian_ensure_confluence_space,
    atlassian_confluence_create_page,
    log_audit_entry,
    log_integration_action,
    disable_integration_for_session,
]


def create_atlassian_functional_design_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Atlassian Functional Design Integration Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for the atlassian_functional_design stage.
    """
    return create_stage_agent(
        stage_name="atlassian_functional_design",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
