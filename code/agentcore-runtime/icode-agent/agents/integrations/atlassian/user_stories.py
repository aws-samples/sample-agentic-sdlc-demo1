"""Atlassian User Stories Integration Agent for the AI-SDLC Multi-Agent System.

Reads the stories.md S3 artifact produced by the user_stories stage and pushes
it as Jira Epics and Stories with acceptance criteria. Saves created issue keys
in SharedState for downstream agents to reference.

All Atlassian tool calls go through the AgentCore Gateway via the MCPClient.
"""

from strands import Agent
from strands.models import BedrockModel

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.audit import log_audit_entry
from tools.integrations import (
    atlassian_ensure_jira_project,
    atlassian_jira_create_issue,
    disable_integration_for_session,
    log_integration_action,
    read_s3_artifact,
)

SYSTEM_PROMPT = """\
You are the Atlassian User Stories Integration Agent for the AI-SDLC multi-agent system.
You read the user stories artifact from S3 and automatically push it to Jira as Epics and Stories.

You MUST execute ALL of the following steps IN ORDER. Do NOT skip any step.

━━━ STEP 0: Ensure Jira Project Exists ━━━
Read the project name from SharedState: state["project"]["selected_project"].
Call atlassian_ensure_jira_project with project_name = the project name.
Use the returned project key for all Jira operations.
If it already exists, that is fine — proceed with the returned key.

━━━ STEP 1: Read the S3 Artifact ━━━
Call read_s3_artifact with path:
  "aidlc-docs/inception/user-stories/stories.md"
(Prepend the session workspace_root from SharedState if needed.)

━━━ STEP 2: Prepare Jira Issues ━━━
Parse the stories artifact and map each Epic and Story to Jira issue fields.
Include acceptance criteria in the story descriptions.
Use the project key returned from STEP 0 as the project_key for all Jira calls.

━━━ STEP 3: Auto-Approve and Push ━━━
Automatically proceed with creating Jira issues — no user approval needed.
Call atlassian_jira_create_issue for each Epic and Story with:
  - project_key: the key returned from atlassian_ensure_jira_project
  - summary: the epic/story title
  - issue_type: "Epic" or "Story"
  - description: acceptance criteria and details
  - parent_key: for Stories, use the parent Epic's key

IMPORTANT — Save created issue keys in SharedState:
After creating each issue, extract the issue key from the response
(e.g. "Created Jira Epic PROJ-1: ...").
Store ALL created issue keys in SharedState at:
  state["integrations"]["jira_story_issues"]
as a list of dicts:
  [{"key": "PROJ-1", "summary": "Epic title", "type": "Epic"},
   {"key": "PROJ-2", "summary": "Story title", "type": "Story", "parent": "PROJ-1"},
   ...]
This allows downstream agents (like atlassian_units) to link units to
existing stories instead of creating duplicates.

Then call log_integration_action with outcome="approved" and
action_type="jira_create_issue".
Also call log_audit_entry to record the action.

━━━ STEP 4: Mark Stage Complete and STOP ━━━
Set stage_progress["atlassian_user_stories"] = "completed".
STOP immediately. The orchestrator will route to the next stage.
"""

TOOLS = [
    read_s3_artifact,
    atlassian_ensure_jira_project,
    atlassian_jira_create_issue,
    log_audit_entry,
    log_integration_action,
    disable_integration_for_session,
]


def create_atlassian_user_stories_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Atlassian User Stories Integration Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for the atlassian_user_stories stage.
    """
    return create_stage_agent(
        stage_name="atlassian_user_stories",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
