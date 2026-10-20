"""Atlassian Units Integration Agent for the AI-SDLC Multi-Agent System.

Reads the unit-of-work.md and unit-of-work-story-map.md artifacts, creates
Jira Epics for each unit, links existing Stories to the correct Epic using
the story map, and optionally assigns issues to a sprint.

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
    atlassian_jira_create_issue,
    atlassian_jira_get_board,
    atlassian_jira_list_backlog_issues,
    atlassian_jira_move_to_sprint,
    atlassian_jira_create_sprint,
    disable_integration_for_session,
    log_integration_action,
    read_s3_artifact,
)

SYSTEM_PROMPT = """\
Atlassian Units Integration Agent. Execute these steps IN ORDER without skipping.

STEP 0: Call atlassian_ensure_jira_project(project_name=state["project"]["selected_project"]). Save project_key.

STEP 1: Call read_s3_artifact for (prepend workspace_root, skip if not found):
  - "{workspace_root}/aidlc-docs/inception/application-design/unit-of-work.md"
  - "{workspace_root}/aidlc-docs/inception/application-design/unit-of-work-story-map.md"
Also read state["integrations"]["jira_story_issues"] for existing story keys (list of {key, summary, type, parent}).

STEP 2: For each unit in unit-of-work.md: call atlassian_jira_create_issue(issue_type="Epic"). Use story map to find matching stories from jira_story_issues. For each match, call atlassian_jira_create_issue(issue_type="Task", parent_key=<epic key>). If no match, create Tasks from unit responsibilities. Track all created keys. Call log_integration_action(outcome="approved", action_type="jira_create_issue"). Call log_audit_entry.

STEP 3: MANDATORY — Call human_input(message="Created Epics and Tasks for each unit. Assign to a sprint? (yes/no)"). Wait for response. If no, jump to STEP 6.

STEP 4: Call atlassian_jira_get_board(project_key). Call atlassian_jira_list_backlog_issues(board_id). MANDATORY — call human_input with the issue list and ask: "Which sprint? Enter existing sprint name or new name to create one." Wait for response.

STEP 5: If new sprint name, call atlassian_jira_create_sprint(board_id, name). Call atlassian_jira_move_to_sprint(sprint_id, issue_keys). Confirm to user.

STEP 6: Set state["stage_progress"]["atlassian_units"]="completed". STOP.
"""

TOOLS = [
    human_input,
    read_s3_artifact,
    atlassian_ensure_jira_project,
    atlassian_jira_create_issue,
    atlassian_jira_get_board,
    atlassian_jira_list_backlog_issues,
    atlassian_jira_move_to_sprint,
    atlassian_jira_create_sprint,
    log_audit_entry,
    log_integration_action,
    disable_integration_for_session,
]


def create_atlassian_units_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Atlassian Units Integration Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for the atlassian_units stage.
    """
    return create_stage_agent(
        stage_name="atlassian_units",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
