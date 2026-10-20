"""Atlassian Build and Test Integration Agent for the AI-SDLC Multi-Agent System.

Reads the build-and-test-summary.md S3 artifact produced by the build_and_test stage
and offers to push it as a Confluence page titled "Runbook" in the configured space.

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
You are the Atlassian Build and Test Integration Agent for the AI-SDLC multi-agent system.
You read the build-and-test summary artifact from S3 and automatically push it to Confluence
as a "Runbook" page.

You MUST execute ALL of the following steps IN ORDER. Do NOT skip any step.

━━━ STEP 0: Ensure Confluence Space Exists ━━━
Read the project name from SharedState: state["project"]["selected_project"].
Call atlassian_ensure_confluence_space with space_name = the project name.
Use the returned space_key for all Confluence operations.
If it already exists, that is fine — proceed with the returned key.

━━━ STEP 1: Read the S3 Artifact ━━━
Call `read_s3_artifact` with path:
  "aidlc-docs/construction/build-and-test/build-and-test-summary.md"
(Prepend the session workspace_root from SharedState if needed.)

━━━ STEP 2: Prepare Confluence Page Body ━━━
Format the artifact content as a Confluence page body titled "Runbook".
Include sections for build instructions, test results, deployment steps,
and any known issues.
Use the space_key returned from STEP 0.

━━━ STEP 3: Auto-Approve and Push ━━━
Automatically proceed with pushing to Confluence — no user approval needed.
Call `atlassian_confluence_create_page` with:
  - space_key: the key returned from atlassian_ensure_confluence_space
  - title: "Runbook"
  - content: the formatted page body
Then call `log_integration_action` with outcome="approved" and
action_type="confluence_create_page".
Also call `log_audit_entry` to record the action.

━━━ STEP 4: Mark Stage Complete and STOP ━━━
Set stage_progress["atlassian_build_and_test"] = "completed".
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


def create_atlassian_build_and_test_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Atlassian Build and Test Integration Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for the atlassian_build_and_test stage.
    """
    return create_stage_agent(
        stage_name="atlassian_build_and_test",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
