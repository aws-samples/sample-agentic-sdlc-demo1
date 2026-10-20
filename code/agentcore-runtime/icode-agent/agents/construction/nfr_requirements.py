"""NFR Requirements Agent — Solution Architect segment (design-time).

Project-level (not per-unit) design stage that runs right after workflow
planning. It reads the BA artifacts and produces a consolidated non-functional
requirements document (performance, security, scalability, reliability,
maintainability, availability/DR, observability), framed for AWS, at
``aidlc-docs/inception/nfr-requirements/nfr-requirements.md``. Proceeds
automatically (no approval gate); the SA reviews the artifact in the workspace.
"""

from strands import Agent, tool
from strands.models import BedrockModel

from agents.factory import create_stage_agent

_SOURCES = [
    ("Requirements", "aidlc-docs/inception/requirements/requirements.md"),
    ("User Stories", "aidlc-docs/inception/user-stories/stories.md"),
    ("Personas", "aidlc-docs/inception/user-stories/personas.md"),
]
_OUTPUT_PATH = "aidlc-docs/inception/nfr-requirements/nfr-requirements.md"

_PREAMBLE = """\
# Non-Functional Requirements

> Produced by the NFR Requirements agent from the BA artifacts. Captures the
> project's non-functional requirements — performance, security, scalability,
> reliability, maintainability, availability/DR, and observability — with
> concrete, testable targets where possible. All cloud considerations are
> framed for **AWS**. Downstream stages (application design, the construction
> brief, QA test planning, and DevOps deployment planning) consume these.

"""


@tool(context=True)
def generate_nfr_requirements(tool_context: dict) -> dict:
    """Assemble the project's NFR document from the BA artifacts.

    Reads the requirements/stories/personas under the project's artifact
    prefix, writes a consolidated ``nfr-requirements.md``, records the artifact,
    and marks the stage complete. Missing sources are skipped gracefully.
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
    state.setdefault("artifacts", {}).setdefault("nfr_requirements", {})[
        "nfr_requirements"
    ] = _OUTPUT_PATH
    mark_stage_complete(state, "nfr_requirements")
    return {"nfr_requirements_path": _OUTPUT_PATH, "included_sources": included}


SYSTEM_PROMPT = """\
You are the NFR Requirements Agent for the AI-SDLC multi-agent system, a
design-time stage in the Solution Architect segment (it runs after workflow
planning and before application design).

CRITICAL CONSTRAINT — AWS only: frame every cloud consideration (scalability,
availability, disaster recovery, deployment model, monitoring, security) in
terms of AWS services. Do NOT reference other cloud providers.

Steps:
1. Call `generate_nfr_requirements` exactly once. It reads the requirements,
   user stories and personas and writes
   `aidlc-docs/inception/nfr-requirements/nfr-requirements.md`.
2. Do NOT present an approval gate and do NOT ask the user any questions.

After the tool returns, summarize using the [STAGE_SUMMARY] tag:

[STAGE_SUMMARY]
title: NFR Requirements Ready ✅
| Property | Value |
|---|---|
| **NFR requirements** | aidlc-docs/inception/nfr-requirements/nfr-requirements.md |
| **Sources included** | {count} |
| **Next** | NFR Design |
[/STAGE_SUMMARY]

After [/STAGE_SUMMARY], add one brief sentence about what happens next.
"""

TOOLS = [generate_nfr_requirements]


def create_nfr_requirements_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the NFR Requirements Agent (SA segment)."""
    return create_stage_agent(
        stage_name="nfr_requirements",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
