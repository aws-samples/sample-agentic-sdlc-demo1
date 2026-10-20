"""QA Report Agent — QA Engineer persona (qa_segment, stage 3).

Consolidates the test plan and test results into a QA sign-off report at
``aidlc-docs/qa/qa-report.md`` — the artifact the QA Engineer reviews before
handing the project off to DevOps (or bouncing it back to the developer via a
request-changes transition). Proceeds automatically (no approval gate); the
pass/fail decision and any bounce are driven through the Project API by the
persona.
"""

from strands import Agent, tool
from strands.models import BedrockModel

from agents.factory import create_stage_agent

_SOURCES = [
    ("Test Plan", "aidlc-docs/qa/test-plan.md"),
    ("Test Results", "aidlc-docs/qa/test-results.md"),
]
_OUTPUT_PATH = "aidlc-docs/qa/qa-report.md"

_PREAMBLE = """\
# QA Report

> Produced by the QA Report agent. Consolidates the test plan and results into a
> sign-off summary. The QA Engineer approves (hand off to DevOps) or requests
> changes (bounce back to the developer) through the platform handoff controls.

"""


@tool(context=True)
def generate_qa_report(tool_context: dict) -> dict:
    """Consolidate the QA artifacts into a sign-off report and mark stage complete."""
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
    state.setdefault("artifacts", {}).setdefault("qa_report", {})[
        "qa_report"
    ] = _OUTPUT_PATH
    mark_stage_complete(state, "qa_report")
    return {"qa_report_path": _OUTPUT_PATH, "included_sources": included}


SYSTEM_PROMPT = """\
You are the QA Report Agent for the AI-SDLC multi-agent system, the final stage
of the QA Engineer segment.

Steps:
1. Call `generate_qa_report` exactly once. It reads the QA test plan and results
   and writes `aidlc-docs/qa/qa-report.md`.
2. Do NOT present an approval gate and do NOT ask the user any questions.

After the tool returns, summarize using the [STAGE_SUMMARY] tag:

[STAGE_SUMMARY]
title: QA Report Ready ✅
| Property | Value |
|---|---|
| **QA report** | aidlc-docs/qa/qa-report.md |
| **Sources included** | {count} |
| **Next** | Hand off to DevOps (or request changes back to the developer) |
[/STAGE_SUMMARY]

After [/STAGE_SUMMARY], add one brief sentence about what happens next.
"""

TOOLS = [generate_qa_report]


def create_qa_report_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the QA Report Agent (QA segment)."""
    return create_stage_agent(
        stage_name="qa_report",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
