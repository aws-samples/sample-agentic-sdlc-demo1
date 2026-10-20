"""Test Planning Agent — QA Engineer persona (qa_segment, stage 1).

Reads the inception/design artifacts and produces a test plan with traceability
from units of work to test cases, written to ``aidlc-docs/qa/test-plan.md``.
Proceeds automatically (no approval gate); it performs no test execution — that
is the Test Execution stage.
"""

from strands import Agent, tool
from strands.models import BedrockModel

from agents.factory import create_stage_agent

# (label, canonical artifact path) folded into the test plan, in order.
_SOURCES = [
    ("Requirements", "aidlc-docs/inception/requirements/requirements.md"),
    ("User Stories", "aidlc-docs/inception/user-stories/stories.md"),
    ("Application Design", "aidlc-docs/inception/application-design/application-design.md"),
    ("Units of Work", "aidlc-docs/inception/application-design/unit-of-work.md"),
]
_OUTPUT_PATH = "aidlc-docs/qa/test-plan.md"

_PREAMBLE = """\
# Test Plan

> Produced by the QA Test Planning agent from the inception/design artifacts.
> Defines the test strategy and a traceability outline from each unit of work
> to its test cases. Execution results are recorded separately by the Test
> Execution stage.

"""


@tool(context=True)
def generate_test_plan(tool_context: dict) -> dict:
    """Assemble the test plan from the inception/design artifacts.

    Reads the canonical artifacts under the project's artifact prefix, writes a
    consolidated ``test-plan.md``, records the artifact, and marks the stage
    complete. Missing artifacts are skipped gracefully.
    """
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
            parts.append(f"\n\n---\n\n## Source: {label}\n\n{content.strip()}\n")

    write_file(state, _OUTPUT_PATH, "".join(parts))
    state.setdefault("artifacts", {}).setdefault("test_planning", {})[
        "test_plan"
    ] = _OUTPUT_PATH
    mark_stage_complete(state, "test_planning")
    return {"test_plan_path": _OUTPUT_PATH, "included_sources": included}


SYSTEM_PROMPT = """\
You are the Test Planning Agent for the AI-SDLC multi-agent system, the first
stage of the QA Engineer segment.

Steps:
1. Call `generate_test_plan` exactly once. It reads the requirements, user
   stories, application design and units of work and writes
   `aidlc-docs/qa/test-plan.md`.
2. Do NOT present an approval gate and do NOT ask the user any questions.

After the tool returns, summarize using the [STAGE_SUMMARY] tag:

[STAGE_SUMMARY]
title: Test Plan Ready ✅
| Property | Value |
|---|---|
| **Test plan** | aidlc-docs/qa/test-plan.md |
| **Sources included** | {count} |
| **Next** | Test Execution |
[/STAGE_SUMMARY]

After [/STAGE_SUMMARY], add one brief sentence about what happens next.
"""

TOOLS = [generate_test_plan]


def create_test_planning_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Test Planning Agent (QA segment)."""
    return create_stage_agent(
        stage_name="test_planning",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
