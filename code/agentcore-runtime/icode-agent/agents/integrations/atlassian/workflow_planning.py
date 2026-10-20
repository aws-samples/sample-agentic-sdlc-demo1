"""Atlassian Workflow Planning Integration Agent for the AI-SDLC Multi-Agent System.

Reads the execution-plan.md S3 artifact, asks the user for sprint names,
creates sprints, lists backlog stories for the user to select, and moves
selected stories into the sprints.

All Atlassian tool calls go through the AgentCore Gateway via the MCPClient.
"""

from strands import Agent
from strands.models import BedrockModel

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.audit import log_audit_entry
from tools.human_input import human_input
from tools.integrations import (
    atlassian_ensure_jira_project,
    atlassian_jira_create_sprint,
    atlassian_jira_get_board,
    atlassian_jira_list_backlog_issues,
    atlassian_jira_move_to_sprint,
    disable_integration_for_session,
    log_integration_action,
    read_s3_artifact,
)

SYSTEM_PROMPT = """\
Atlassian Workflow Planning Integration Agent. Execute these steps IN ORDER without skipping.

STEP 0: Call atlassian_ensure_jira_project(project_name=state["project"]["selected_project"]). Save project_key. Call atlassian_jira_get_board(project_key). If board error/not found: call log_integration_action(outcome="skipped", action_type="jira_get_board") and jump to STEP 6.

STEP 1: MANDATORY — Call human_input(message="What would you like to name this sprint? Suggested: Sprint 1"). Wait for response.

STEP 2: Call atlassian_jira_create_sprint(board_id=<from STEP 0>, name=<user input>). Save sprint_id.

STEP 3: Call atlassian_jira_list_backlog_issues(board_id=<from STEP 0>). Then MANDATORY — call human_input with the numbered list of stories and ask: "Which stories go into sprint '<name>'? Enter numbers (e.g. 1,2,3) or 'all'." Wait for response.

STEP 4: Map user's numbers to issue keys. Call atlassian_jira_move_to_sprint(sprint_id=<from STEP 2>, issue_keys=<selected>). Confirm to user.

STEP 5: MANDATORY — Call human_input(message="Create another sprint? (yes/no)"). If yes, loop to STEP 1. If no, proceed to STEP 6.

STEP 6: Call log_integration_action(outcome="approved", action_type="jira_create_sprint"). Call log_audit_entry. Set state["stage_progress"]["atlassian_workflow_planning"]="completed". STOP.
"""

TOOLS = [
    human_input,
    read_s3_artifact,
    atlassian_ensure_jira_project,
    atlassian_jira_get_board,
    atlassian_jira_create_sprint,
    atlassian_jira_list_backlog_issues,
    atlassian_jira_move_to_sprint,
    log_audit_entry,
    log_integration_action,
    disable_integration_for_session,
]


def create_atlassian_workflow_planning_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Atlassian Workflow Planning Integration Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for the atlassian_workflow_planning stage.
    """
    return create_stage_agent(
        stage_name="atlassian_workflow_planning",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
