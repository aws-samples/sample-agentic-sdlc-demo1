"""Release Agent — DevOps Engineer persona (devops_segment, stage 2, terminal).

Reads the deployment plan and produces a release runbook at
``aidlc-docs/devops/release-runbook.md`` (pre-flight checks, deploy steps,
verification, rollback). The actual (guarded, human-approved) deploy execution
is triggered through the platform handoff controls / an external pipeline —
this stage produces the runbook and marks the lifecycle's final stage complete.
Proceeds automatically (no approval gate).
"""

from strands import Agent, tool
from strands.models import BedrockModel

from agents.factory import create_stage_agent

_DEPLOYMENT_PLAN_PATH = "aidlc-docs/devops/deployment-plan.md"
_OUTPUT_PATH = "aidlc-docs/devops/release-runbook.md"

_PREAMBLE = """\
# Release Runbook

> Produced by the DevOps Release agent from the deployment plan. Contains the
> pre-flight checklist, ordered deploy steps, post-deploy verification, and the
> rollback procedure. Executing the release is a guarded, human-approved action
> performed through the platform / an external pipeline.

"""


@tool(context=True)
def generate_release_runbook(tool_context: dict) -> dict:
    """Assemble the release runbook from the deployment plan; mark stage complete."""
    from tools._compat import get_state
    from tools.file_ops import read_file, write_file
    from tools.state import mark_stage_complete

    state = get_state(tool_context)

    parts: list[str] = [_PREAMBLE]
    had_plan = False
    try:
        plan = read_file(state, _DEPLOYMENT_PLAN_PATH)
    except (FileNotFoundError, RuntimeError):
        plan = ""
    if plan and plan.strip():
        had_plan = True
        parts.append(f"\n\n---\n\n## Deployment Plan\n\n{plan.strip()}\n")

    write_file(state, _OUTPUT_PATH, "".join(parts))
    state.setdefault("artifacts", {}).setdefault("release", {})[
        "release_runbook"
    ] = _OUTPUT_PATH
    mark_stage_complete(state, "release")
    return {"release_runbook_path": _OUTPUT_PATH, "had_deployment_plan": had_plan}


SYSTEM_PROMPT = """\
You are the Release Agent for the AI-SDLC multi-agent system, the final stage of
the DevOps Engineer segment and the end of the lifecycle.

Steps:
1. Call `generate_release_runbook` exactly once. It reads
   `aidlc-docs/devops/deployment-plan.md` and writes
   `aidlc-docs/devops/release-runbook.md`.
2. Do NOT present an approval gate and do NOT ask the user any questions.

After the tool returns, summarize using the [STAGE_SUMMARY] tag:

[STAGE_SUMMARY]
title: Release Runbook Ready ✅
| Property | Value |
|---|---|
| **Release runbook** | aidlc-docs/devops/release-runbook.md |
| **Next** | Execute the guarded release, then operate/monitor |
[/STAGE_SUMMARY]

This is the END of the workflow. Do NOT proceed to any other stage.
"""

TOOLS = [generate_release_runbook]


def create_release_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Release Agent (DevOps segment)."""
    return create_stage_agent(
        stage_name="release",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
