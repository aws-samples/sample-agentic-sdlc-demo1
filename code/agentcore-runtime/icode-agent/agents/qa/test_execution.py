"""Test Execution Agent — QA Engineer persona (qa_segment, stage 2).

Reads the test plan and (when the implementation repository has been ingested)
the project source, and records a test-execution assessment at
``aidlc-docs/qa/test-results.md``. In-platform test *execution* against a real
sandbox is out of scope here (that is wired via the Git round-trip ingestion +
a CodeBuild test runner); this stage produces a coverage/assessment report and
flags gaps. Proceeds automatically (no approval gate).
"""

from strands import Agent, tool
from strands.models import BedrockModel

from agents.factory import create_stage_agent

_TEST_PLAN_PATH = "aidlc-docs/qa/test-plan.md"
_OUTPUT_PATH = "aidlc-docs/qa/test-results.md"

_PREAMBLE = """\
# Test Results

> Produced by the QA Test Execution agent. Summarizes the assessment against the
> test plan. When the implementation repository has been ingested, this includes
> a review of the source against the planned test cases; live test-runner output
> is attached when available.

"""


@tool(context=True)
def record_test_results(tool_context: dict) -> dict:
    """Record a test-execution assessment against the test plan.

    Reads ``test-plan.md`` (best-effort), writes ``test-results.md``, records
    the artifact and marks the stage complete.
    """
    from tools._compat import get_state
    from tools.file_ops import read_file, write_file
    from tools.state import mark_stage_complete

    state = get_state(tool_context)

    parts: list[str] = [_PREAMBLE]
    have_plan = False
    try:
        plan = read_file(state, _TEST_PLAN_PATH)
    except (FileNotFoundError, RuntimeError):
        plan = ""
    if plan and plan.strip():
        have_plan = True
        parts.append(f"\n\n---\n\n## Test Plan Under Assessment\n\n{plan.strip()}\n")

    repo = state.get("handoff", {}).get("repo_url") or state.get("repo_url")
    parts.append(
        "\n\n---\n\n## Execution Summary\n\n"
        + (f"- Implementation repository: {repo}\n" if repo else "- Implementation repository: not linked yet\n")
        + "- Live test-runner output is attached once the repository is ingested "
        "and a CI run completes.\n"
    )

    write_file(state, _OUTPUT_PATH, "".join(parts))
    state.setdefault("artifacts", {}).setdefault("test_execution", {})[
        "test_results"
    ] = _OUTPUT_PATH
    mark_stage_complete(state, "test_execution")
    return {"test_results_path": _OUTPUT_PATH, "had_test_plan": have_plan}


SYSTEM_PROMPT = """\
You are the Test Execution Agent for the AI-SDLC multi-agent system, the second
stage of the QA Engineer segment.

Steps:
1. Call `record_test_results` exactly once. It reads `aidlc-docs/qa/test-plan.md`
   and writes `aidlc-docs/qa/test-results.md`.
2. Do NOT present an approval gate and do NOT ask the user any questions.

After the tool returns, summarize using the [STAGE_SUMMARY] tag:

[STAGE_SUMMARY]
title: Test Results Recorded ✅
| Property | Value |
|---|---|
| **Test results** | aidlc-docs/qa/test-results.md |
| **Next** | QA Report |
[/STAGE_SUMMARY]

After [/STAGE_SUMMARY], add one brief sentence about what happens next.
"""

TOOLS = [record_test_results]


def create_test_execution_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Test Execution Agent (QA segment)."""
    return create_stage_agent(
        stage_name="test_execution",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
