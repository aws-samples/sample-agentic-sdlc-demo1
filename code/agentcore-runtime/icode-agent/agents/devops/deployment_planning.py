"""Deployment Planning Agent — DevOps Engineer persona (devops_segment, stage 1).

Reads the application design and QA report and produces an AWS deployment plan
at ``aidlc-docs/devops/deployment-plan.md`` (target environments, infrastructure
outline, rollout/rollback strategy). Proceeds automatically (no approval gate).
"""

from strands import Agent, tool
from strands.models import BedrockModel

from agents.factory import create_stage_agent

_SOURCES = [
    ("Application Design", "aidlc-docs/inception/application-design/application-design.md"),
    ("Units of Work", "aidlc-docs/inception/application-design/unit-of-work.md"),
    ("QA Report", "aidlc-docs/qa/qa-report.md"),
]
_OUTPUT_PATH = "aidlc-docs/devops/deployment-plan.md"

_PREAMBLE = """\
# Deployment Plan

> Produced by the DevOps Deployment Planning agent. Outlines the AWS target
> environments, infrastructure, and the rollout/rollback strategy derived from
> the design and QA sign-off. Refine with concrete resource sizing before a
> production release.

"""


@tool(context=True)
def generate_deployment_plan(tool_context: dict) -> dict:
    """Assemble the deployment plan from the design + QA artifacts; mark complete."""
    from tools._compat import get_state
    from tools.file_ops import read_file, write_file
    from tools.state import mark_stage_complete

    state = get_state(tool_context)

    included: list[str] = []
    parts: list[str] = [_PREAMBLE]
    for label, path in _SOURCES:
        try:
            content = read_file(state, path)
        except (FileNotFoundError, RuntimeError):
            continue
        if content and content.strip():
            included.append(label)
            parts.append(f"\n\n---\n\n## {label}\n\n{content.strip()}\n")

    write_file(state, _OUTPUT_PATH, "".join(parts))
    state.setdefault("artifacts", {}).setdefault("deployment_planning", {})[
        "deployment_plan"
    ] = _OUTPUT_PATH
    mark_stage_complete(state, "deployment_planning")
    return {"deployment_plan_path": _OUTPUT_PATH, "included_sources": included}


SYSTEM_PROMPT = """\
You are the Deployment Planning Agent for the AI-SDLC multi-agent system, the
first stage of the DevOps Engineer segment.

Steps:
1. Call `generate_deployment_plan` exactly once. It reads the application
   design, units of work and QA report and writes
   `aidlc-docs/devops/deployment-plan.md`.
2. Do NOT present an approval gate and do NOT ask the user any questions.

After the tool returns, summarize using the [STAGE_SUMMARY] tag:

[STAGE_SUMMARY]
title: Deployment Plan Ready ✅
| Property | Value |
|---|---|
| **Deployment plan** | aidlc-docs/devops/deployment-plan.md |
| **Sources included** | {count} |
| **Next** | Release |
[/STAGE_SUMMARY]

After [/STAGE_SUMMARY], add one brief sentence about what happens next.
"""

TOOLS = [generate_deployment_plan]


def create_deployment_planning_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Deployment Planning Agent (DevOps segment)."""
    return create_stage_agent(
        stage_name="deployment_planning",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
