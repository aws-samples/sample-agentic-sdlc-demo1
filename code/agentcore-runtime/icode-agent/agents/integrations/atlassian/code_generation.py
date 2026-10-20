"""Atlassian Code Generation Integration Agent for the AI-SDLC Multi-Agent System.

Transitions the corresponding Jira tasks from "In Progress" to "Done" after the
code_generation stage completes. Reads Jira task IDs from SharedState.

No S3 artifact read — this agent works entirely with Jira task state transitions.
All Atlassian tool calls go through the AgentCore Gateway via the MCPClient.
"""

from strands import Agent
from strands.models import BedrockModel

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.audit import log_audit_entry
from tools.integrations import (
    atlassian_ensure_jira_project,
    atlassian_jira_transition_issue,
    disable_integration_for_session,
    log_integration_action,
    read_s3_artifact,
)

SYSTEM_PROMPT = """\
You are the Atlassian Code Generation Integration Agent for the AI-SDLC multi-agent system.
You automatically transition the corresponding Jira tasks from "In Progress" to "Done"
after code generation completes for the current unit.

You MUST execute ALL of the following steps IN ORDER. Do NOT skip any step.

━━━ STEP 0: Ensure Jira Project Exists ━━━
Read the project name from SharedState: state["project"]["selected_project"].
Call atlassian_ensure_jira_project with project_name = the project name.
Use the returned project key for all Jira operations.

━━━ STEP 1: Identify Jira Task IDs ━━━
Read the Jira task IDs for the current unit from SharedState. Check:
  - stage_progress for any stored Jira issue keys
  - integrations.action_log for previously created Jira issues
  - The current unit name from execution_plan or stage_progress
Use the project key returned from STEP 0 as the jira_project_key.

━━━ STEP 2: Auto-Approve and Transition ━━━
Automatically proceed with transitioning Jira tasks — no user approval needed.
For each Jira task, call `atlassian_jira_transition_issue` with:
  - issue_key: the task key
  - transition_name: "Done"
Then call `log_integration_action` with outcome="approved" and
action_type="jira_transition_issue".
Also call `log_audit_entry` to record the action.

━━━ STEP 3: Mark Stage Complete and STOP ━━━
Set stage_progress["atlassian_code_generation"] = "completed".
STOP immediately. The orchestrator will route to the next stage.
"""

TOOLS = [
    read_s3_artifact,
    atlassian_ensure_jira_project,
    atlassian_jira_transition_issue,
    log_audit_entry,
    log_integration_action,
    disable_integration_for_session,
]


def create_atlassian_code_generation_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Atlassian Code Generation Integration Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for the atlassian_code_generation stage.
    """
    return create_stage_agent(
        stage_name="atlassian_code_generation",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
