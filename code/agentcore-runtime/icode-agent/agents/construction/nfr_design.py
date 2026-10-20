"""NFR Design Agent — Solution Architect segment (design-time).

Project-level design stage that runs right after NFR Requirements. It turns the
non-functional requirements into AWS design patterns and logical components at
``aidlc-docs/inception/nfr-design/nfr-design.md``, so the subsequent application
design, construction brief, and DevOps deployment plan can build on them.
Proceeds automatically (no approval gate).
"""

from strands import Agent, tool
from strands.models import BedrockModel

from agents.factory import create_stage_agent

_SOURCES = [
    ("NFR Requirements", "aidlc-docs/inception/nfr-requirements/nfr-requirements.md"),
    ("Requirements", "aidlc-docs/inception/requirements/requirements.md"),
]
_OUTPUT_PATH = "aidlc-docs/inception/nfr-design/nfr-design.md"

_PREAMBLE = """\
# NFR Design

> Produced by the NFR Design agent. Translates the non-functional requirements
> into concrete **AWS** design patterns and logical components (e.g. auto
> scaling, multi-AZ, caching, queueing, encryption, IAM least-privilege,
> observability). Refine with specific resource choices during application
> design and deployment planning.

"""


@tool(context=True)
def generate_nfr_design(tool_context: dict) -> dict:
    """Assemble the NFR design document from the NFR requirements; mark complete."""
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
    state.setdefault("artifacts", {}).setdefault("nfr_design", {})[
        "nfr_design"
    ] = _OUTPUT_PATH
    mark_stage_complete(state, "nfr_design")
    return {"nfr_design_path": _OUTPUT_PATH, "included_sources": included}


SYSTEM_PROMPT = """\
You are the NFR Design Agent for the AI-SDLC multi-agent system, a design-time
stage in the Solution Architect segment (it runs after NFR Requirements and
before application design).

CRITICAL CONSTRAINT — AWS only: every NFR design pattern MUST leverage AWS
services and capabilities. Do NOT reference other cloud providers.

Steps:
1. Call `generate_nfr_design` exactly once. It reads
   `aidlc-docs/inception/nfr-requirements/nfr-requirements.md` and writes
   `aidlc-docs/inception/nfr-design/nfr-design.md`.
2. Do NOT present an approval gate and do NOT ask the user any questions.

After the tool returns, summarize using the [STAGE_SUMMARY] tag:

[STAGE_SUMMARY]
title: NFR Design Ready ✅
| Property | Value |
|---|---|
| **NFR design** | aidlc-docs/inception/nfr-design/nfr-design.md |
| **Sources included** | {count} |
| **Next** | Application Design |
[/STAGE_SUMMARY]

After [/STAGE_SUMMARY], add one brief sentence about what happens next.
"""

TOOLS = [generate_nfr_design]


def create_nfr_design_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the NFR Design Agent (SA segment)."""
    return create_stage_agent(
        stage_name="nfr_design",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
