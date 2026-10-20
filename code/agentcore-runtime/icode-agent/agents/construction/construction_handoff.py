"""Construction Handoff Agent — Developer persona (dev_segment).

The single stage in the Developer segment. After the Solution Architect hands
the project off (``ready_for_dev`` → ``assigned_to_dev``), this agent runs in
the Developer's session and assembles a **construction kickoff package** from
the inception artifacts so the developer can take it straight into an
AI-assisted IDE (Kiro / Claude Code / Cursor):

  * ``aidlc-docs/construction/construction-brief.md`` — one consolidated
    document: a ready-to-paste coding-agent prompt preamble followed by the
    requirements, user stories, personas, application design, units of work,
    and execution plan.
  * ``aidlc-docs/construction/tasks/<unit>.md`` — one task file per Unit_of_Work
    (best-effort, parsed from ``unit-of-work.md``) pointing back at the brief.

It performs no in-platform code generation — construction happens in the
external IDE. The agent proceeds automatically (no approval gate).
"""

import posixpath

from strands import Agent, tool
from strands.models import BedrockModel

from agents.factory import create_stage_agent

# Inception artifacts to fold into the brief, in presentation order.
# (label, canonical path relative to the project artifact prefix)
_BRIEF_SOURCES = [
    ("Requirements", "aidlc-docs/inception/requirements/requirements.md"),
    ("User Stories", "aidlc-docs/inception/user-stories/stories.md"),
    ("Personas", "aidlc-docs/inception/user-stories/personas.md"),
    ("Application Design", "aidlc-docs/inception/application-design/application-design.md"),
    ("Components", "aidlc-docs/inception/application-design/components.md"),
    ("Component Methods", "aidlc-docs/inception/application-design/component-methods.md"),
    ("Services", "aidlc-docs/inception/application-design/services.md"),
    ("Component Dependencies", "aidlc-docs/inception/application-design/component-dependency.md"),
    ("Units of Work", "aidlc-docs/inception/application-design/unit-of-work.md"),
    ("Unit Dependencies", "aidlc-docs/inception/application-design/unit-of-work-dependency.md"),
    ("Unit / Story Map", "aidlc-docs/inception/application-design/unit-of-work-story-map.md"),
    ("Execution Plan", "aidlc-docs/inception/plans/execution-plan.md"),
]

_UNIT_OF_WORK_PATH = "aidlc-docs/inception/application-design/unit-of-work.md"
_BRIEF_PATH = "aidlc-docs/construction/construction-brief.md"
_TASKS_DIR = "aidlc-docs/construction/tasks"

_CODING_AGENT_PREAMBLE = """\
# Construction Brief

> **For an AI coding agent (Kiro / Claude Code / Cursor).** You are implementing
> the project described below. The inception and design phases are complete; the
> requirements, user stories, application design, and units of work are the
> authoritative specification. Build the implementation, infrastructure (AWS),
> and tests that satisfy them. Target AWS for all cloud services. Work unit by
> unit (see "Units of Work" and the per-unit task files under
> `aidlc-docs/construction/tasks/`). Ask for clarification only where the spec
> is genuinely ambiguous.

"""


def _slugify_unit(name: str) -> str:
    """Filesystem/S3-safe slug for a unit name."""
    import re

    slug = name.strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^a-z0-9_-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "unit"


def _split_units(unit_of_work_md: str) -> list[tuple[str, str]]:
    """Best-effort split of unit-of-work.md into (unit_name, section) pairs.

    Splits on the most common heading level present (``## `` preferred, then
    ``### ``). Returns an empty list when no headings are found.
    """
    import re

    for level in ("## ", "### "):
        pattern = re.compile(rf"^{re.escape(level)}(.+)$", re.MULTILINE)
        matches = list(pattern.finditer(unit_of_work_md))
        if len(matches) >= 1:
            units: list[tuple[str, str]] = []
            for i, m in enumerate(matches):
                name = m.group(1).strip()
                start = m.start()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(unit_of_work_md)
                units.append((name, unit_of_work_md[start:end].strip()))
            return units
    return []


@tool(context=True)
def assemble_construction_brief(tool_context: dict) -> dict:
    """Assemble the construction kickoff package from the inception artifacts.

    Reads the canonical inception/design artifacts under the project's shared
    artifact prefix, writes a consolidated ``construction-brief.md`` (with a
    coding-agent prompt preamble), and writes one best-effort task file per
    Unit_of_Work. Missing artifacts are skipped gracefully.

    Returns a summary dict: the brief path, the source labels that were
    included, and the per-unit task files written.
    """
    from tools._compat import get_state
    from tools.file_ops import read_file, write_file

    state = get_state(tool_context)

    included: list[str] = []
    parts: list[str] = [_CODING_AGENT_PREAMBLE]

    for label, path in _BRIEF_SOURCES:
        try:
            content = read_file(state, path)
        except (FileNotFoundError, RuntimeError):
            continue
        if not content or not content.strip():
            continue
        included.append(label)
        parts.append(f"\n\n---\n\n## {label}\n\n{content.strip()}\n")

    brief = "".join(parts)
    write_file(state, _BRIEF_PATH, brief)

    # Per-unit task files (best-effort, parsed from unit-of-work.md).
    task_files: list[str] = []
    try:
        uow = read_file(state, _UNIT_OF_WORK_PATH)
    except (FileNotFoundError, RuntimeError):
        uow = ""
    if uow.strip():
        for name, section in _split_units(uow):
            slug = _slugify_unit(name)
            task_path = posixpath.join(_TASKS_DIR, f"{slug}.md")
            task_md = (
                f"# Task: {name}\n\n"
                f"> Implement this unit of work. Full project context is in "
                f"`{_BRIEF_PATH}`.\n\n"
                f"{section}\n"
            )
            try:
                write_file(state, task_path, task_md)
                task_files.append(task_path)
            except RuntimeError:
                continue

    # Record artifact + mark the stage complete.
    state.setdefault("artifacts", {}).setdefault("construction_handoff", {})[
        "construction_brief"
    ] = _BRIEF_PATH

    from tools.state import mark_stage_complete

    mark_stage_complete(state, "construction_handoff")

    return {
        "brief_path": _BRIEF_PATH,
        "included_sources": included,
        "task_files": task_files,
    }


SYSTEM_PROMPT = """\
You are the Construction Handoff Agent for the AI-SDLC multi-agent system, the
final step for the Software Developer persona.

The inception and design phases are complete. Your job is to package everything
the developer needs to start coding in an AI-assisted IDE — you do NOT generate
code yourself.

Steps:
1. Call `assemble_construction_brief` exactly once. It reads the inception
   artifacts (requirements, user stories, personas, application design, units
   of work, execution plan) and writes:
   - `aidlc-docs/construction/construction-brief.md` (one consolidated brief
     with a coding-agent prompt preamble), and
   - one task file per unit under `aidlc-docs/construction/tasks/`.
2. Do NOT present an approval gate and do NOT ask the user to choose options.

After the tool returns, summarize using the [STAGE_SUMMARY] tag, listing the
brief path, how many sources were folded in, and how many per-unit task files
were written, and telling the developer they can now open their AI-assisted IDE
(or download the bundle) to begin construction:

[STAGE_SUMMARY]
title: Construction Package Ready ✅
| Property | Value |
|---|---|
| **Brief** | aidlc-docs/construction/construction-brief.md |
| **Sources included** | {count} |
| **Per-unit task files** | {count} |
| **Next** | Open your AI-assisted IDE (or download the bundle) to start coding |
[/STAGE_SUMMARY]

This is the END of the workflow. Do NOT proceed to any other stage.
"""

TOOLS = [assemble_construction_brief]


def create_construction_handoff_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Construction Handoff Agent (Developer segment).

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for the construction handoff.
    """
    return create_stage_agent(
        stage_name="construction_handoff",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
