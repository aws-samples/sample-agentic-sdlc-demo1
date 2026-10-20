"""Graph Orchestrator for the AI-SDLC Multi-Agent System.

Builds the Strands GraphBuilder graph with 14 nodes (one per Stage_Agent),
conditional edges, and a cycle edge for the per-unit Construction loop.

The orchestrator:
- Creates all 14 agents using their respective factory functions
- Wires edges (including conditional transitions) based on SharedState
- Tracks per-unit loop progress (current_unit_index, completed_units)
- Persists state after each stage transition
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from strands.multiagent import GraphBuilder
from strands.multiagent.graph import Graph, GraphState
from strands.session import S3SessionManager

if TYPE_CHECKING:
    from bedrock_agentcore.memory import AgentCoreMemorySessionManager

logger = logging.getLogger(__name__)

# from agents.construction.build_and_test import create_build_and_test_agent
# from agents.construction.code_generation import create_code_generation_agent
# from agents.construction.functional_design import create_functional_design_agent
# from agents.construction.infrastructure_design import create_infrastructure_design_agent
from agents.construction.nfr_design import create_nfr_design_agent
from agents.construction.nfr_requirements import create_nfr_requirements_agent
from agents.inception.application_design import create_application_design_agent
from agents.inception.requirements_analysis import create_requirements_analysis_agent
from agents.inception.reverse_engineering import create_reverse_engineering_agent
from agents.inception.units_generation import create_units_generation_agent
from agents.inception.user_stories import create_user_stories_agent
from agents.inception.workflow_planning import create_workflow_planning_agent
from agents.extensions.inception_complete import create_inception_complete_agent
from agents.inception.workspace_detection import create_workspace_detection_agent
from agents.extensions.workspace_detection_ext import create_workspace_detection_ext_agent
from agents.extensions.application_design_ext import create_application_design_ext_agent
from agents.construction.construction_handoff import create_construction_handoff_agent
from agents.qa.test_planning import create_test_planning_agent
from agents.qa.test_execution import create_test_execution_agent
from agents.qa.qa_report import create_qa_report_agent
from agents.devops.deployment_planning import create_deployment_planning_agent
from agents.devops.release import create_release_agent
from agents.integrations.hub import create_integration_hub_node
from tools.state import _state_to_markdown
import workflow_registry


# ---------------------------------------------------------------------------
# Handoff_Gate factory (design Section 5b, Req 6.2 / task 5.2).
#
# Generalized gate node placed on every segment's exit_hub outbound edge for
# segments with a successor. When reached the gate:
#   1. marks ``state["handoff"]["pending"] = True``,
#   2. persists SharedState via the Gateway (parity with create_integration_
#      hub_node) so the collaboration metadata survives session restart, and
#   3. raises a Strands breakout interrupt with reason
#      ``{breakout: True, handoff: True, from_segment, message}``.
#
# The runner in ``main_ws.py::_run_graph_with_interrupts`` recognizes the
# ``handoff`` flag, emits a ``handoff_required`` WebSocket card, and halts
# the graph loop (Req 6.3); the next persona reconnects through a fresh
# WebSocket session after the Frontend records the transition through the
# Project API.
# ---------------------------------------------------------------------------


def create_handoff_gate_node(from_segment: str):
    """Return a non-LLM ``MultiAgentBase`` gate node for ``from_segment``.

    Modeled on :func:`agents.integrations.hub.create_integration_hub_node` —
    a lightweight node that runs in the graph between an upstream segment's
    ``exit_hub`` and the next segment's first stage. The returned object
    carries ``name = f"handoff_gate__{from_segment}"`` so its node id matches
    the convention asserted by ``tests/test_runtime_persona_logic.py`` and
    used by the runner's handoff branch.

    Args:
        from_segment: The upstream segment whose owning persona finished its
            work. Embedded in the interrupt ``reason`` so the runner can
            resolve the registry-derived ``from_role``/``to_role`` for the
            ``handoff_required`` card without any persona-specific branching.

    Returns:
        A :class:`MultiAgentBase` instance with ``name`` set to
        ``f"handoff_gate__{from_segment}"``.
    """
    from typing import Any

    from strands.interrupt import Interrupt, InterruptException
    from strands.multiagent.base import MultiAgentBase, MultiAgentResult

    from tools.state import _persist_via_gateway, _state_to_markdown

    gate_id = f"handoff_gate__{from_segment}"

    class HandoffGateNode(MultiAgentBase):
        """Non-LLM graph node that halts execution at a segment boundary."""

        async def invoke_async(
            self,
            task: Any,
            invocation_state: dict | None = None,
            **kwargs: Any,
        ) -> MultiAgentResult:
            state = invocation_state if invocation_state is not None else {}

            logger.info("[HANDOFF_GATE] Reached: %s", gate_id)

            # 1. Mark the SharedState mirror as pending so the runner and the
            #    serializer can both observe that the collaboration boundary
            #    was reached even if the breakout raises before persistence.
            handoff = state.setdefault("handoff", {})
            handoff["pending"] = True

            # 2. Persist state via the Gateway, parity with create_integration_
            #    hub_node — best-effort so a Gateway failure does not mask the
            #    breakout itself.
            mcp_client = state.get("_mcp_client")
            session_id = state.get("_session_id")
            if mcp_client is not None and session_id:
                try:
                    md_content = _state_to_markdown(state)
                    _persist_via_gateway(mcp_client, str(session_id), md_content)
                    logger.info(
                        "[HANDOFF_GATE] State persisted at boundary: %s", gate_id
                    )
                except Exception:
                    logger.exception(
                        "[HANDOFF_GATE] State persistence failed at %s", gate_id
                    )

            # 3. Raise a Strands breakout interrupt. The runner reads
            #    reason["handoff"] / reason["from_segment"] to emit the
            #    handoff_required card and halt the graph loop (Req 6.3).
            message = (
                f"{from_segment} segment is finished. "
                "Ownership transfers to the next persona — assign the "
                "project through the Project API to continue."
            )
            interrupt = Interrupt(
                id=gate_id,
                name=gate_id,
                reason={
                    "breakout": True,
                    "handoff": True,
                    "from_segment": from_segment,
                    "message": message,
                },
            )
            raise InterruptException(interrupt)

    node = HandoffGateNode()
    node.name = gate_id
    return node


# ---------------------------------------------------------------------------
# Resume: S3SessionManager handles skipping completed stages structurally.
# The graph persists its execution state (completed nodes, next nodes to
# execute) to S3 after each node completes.  On resume, the session manager
# restores this state via Graph.deserialize_state(), which sets
# _resume_next_nodes so the graph starts from the first incomplete node.
# No prompt-level skip instructions or LLM compliance required.
# ---------------------------------------------------------------------------


# Human-in-the-loop system prompt addendum injected when interactive mode is on.
_STAGE_BANNER_ADDENDUM = """

IMPORTANT — Stage Progress Banner (MANDATORY):
At the VERY START of your execution — before calling any tools or doing any
work — you MUST display a stage progress banner to the user. This banner
helps the user understand where they are in the workflow and what is coming.

Build the banner by reading `stage_progress` from SharedState. For each stage,
show its status using these icons:
- ✅ = completed
- ⏭️ = skipped
- 🔄 = in_progress (this is YOUR stage — mark it with "← You are here")
- ▶️ = REQUIRED (upcoming stage that will execute)
- 🔜 = not_started (upcoming, status not yet determined)

For stages that are in the execution plan's `stages_to_execute` list and have
not yet started, use ▶️ and label them **REQUIRED** (not "EXECUTE").

Only include stages that are in the execution plan (stages_to_execute) or
already completed/skipped. Do NOT show stages that were skipped by the
workflow planner unless they are marked "skipped" in stage_progress.

CRITICAL — You MUST wrap the progress banner in [PROGRESS_BANNER] tags.
The frontend uses these tags to render the banner with special styling.

Format the banner EXACTLY like this:

[PROGRESS_BANNER]
📍 Workflow Progress
✅ Workspace Detection
✅ Requirements Analysis
🔄 Workflow Planning ← You are here
▶️ Application Design — REQUIRED
▶️ Units Generation — REQUIRED
▶️ Code Generation — REQUIRED
▶️ Build & Test — REQUIRED
[/PROGRESS_BANNER]

RULES for the [PROGRESS_BANNER] tags:
- The FIRST line after [PROGRESS_BANNER] MUST be "📍 Workflow Progress"
- Each subsequent line is ONE stage with its status icon
- The [/PROGRESS_BANNER] tag MUST be on its own line
- Do NOT put any other content inside the tags
- Do NOT use markdown formatting (no **, no ---, no headers) inside the tags

After the banner, add a brief one-line description of what THIS stage will do,
e.g., "I'll now capture your functional and non-functional requirements through
a series of clarifying questions."

IMPORTANT — Text Formatting Between Tool Calls:
When you write status updates or descriptions between tool calls, ALWAYS
separate each distinct thought or status message with a blank line (double
newline). This ensures proper paragraph rendering in the chat UI.

CRITICAL — Be Concise Between Tool Calls:
Do NOT narrate every step you're about to take. The user can see the progress
banner and the tool calls. Keep status messages SHORT — one sentence maximum.
Do NOT say things like "I'll now capture your requirements" AND "Now I'll
generate questions" AND "Let me collect your answers" — that's three messages
saying the same thing. Pick ONE brief message per phase of work.

BAD (too chatty):
  "I'll now capture your functional and non-functional requirements through
  a series of clarifying questions. The request is complex and system-wide —
  a comprehensive set of questions is warranted. Now I'll generate a
  comprehensive set of clarifying questions. Questions file created. Let me
  now collect your answers one at a time."

GOOD (concise):
  "I'll now gather your requirements through a series of clarifying questions."

Then proceed with your stage's work (tool calls, questions, etc.).

IMPORTANT — Stage Completion Summary (MANDATORY):
When your stage work is complete (BEFORE the approval gate), you MUST present
a summary of what was accomplished using the [STAGE_SUMMARY] tag. The frontend
uses this tag to render a styled summary card with a table.

Format your completion summary EXACTLY like this:

[STAGE_SUMMARY]
title: {Your Stage Name} Complete ✅
| Property | Value |
|---|---|
| **{Key 1}** | {Value 1} |
| **{Key 2}** | {Value 2} |
| **{Key 3}** | {Value 3} |
[/STAGE_SUMMARY]

RULES for [STAGE_SUMMARY]:
- The first line after [STAGE_SUMMARY] MUST be "title: {stage name} Complete ✅"
- The rest MUST be a markdown table with Property and Value columns
- Include 4-8 rows summarizing the key outputs/decisions of your stage
- Keep values concise — one line per row
- Do NOT put any text outside the table inside the tags
- After [/STAGE_SUMMARY], add ONE brief sentence about what happens next

Examples of good summary tables per stage:
- Requirements Analysis: clarity, type, scope, complexity, # functional reqs, # non-functional reqs
- Workflow Planning: # stages required, execution sequence, rationale
- Application Design: architecture style, # services, key patterns, storage approach
- Units Generation: # units, # user stories, implementation phases
- Functional Design: # specifications, patterns used, unit name
- Code Generation: # files generated, language, framework, unit name
- Build & Test: build status, test results, coverage

CRITICAL — Scope Boundary (MANDATORY):
You are responsible for ONE stage only. After your approval gate is approved
and you have marked your stage as completed, you MUST STOP. Do NOT:
- Display a progress banner showing later stages as ✅ completed
- Start working on the next stage's tasks
- Call tools that belong to a different stage
- Generate code, design artifacts, or plans for stages after yours
After your stage is complete and approved, we will automatically move onto
the next stage. Your job ends when your stage is marked complete and the
approval gate is resolved.

When communicating stage completion to the user, NEVER mention "graph
orchestrator", "graph routing", or any internal system terminology. Instead
say something like: "✅ {Stage Name} is complete and approved. We will now
move onto the {Next Stage Name} stage."
"""

_AUDIT_ADDENDUM = """

IMPORTANT — Audit Logging (MANDATORY):
You have access to the `log_audit_entry` tool. You MUST use this tool to
maintain a complete, append-only audit trail of all interactions. Log entries
for EVERY one of the following events:

1. **Stage Start**: When you begin working on a stage, log with
   entry_type="stage_transition" and content describing the stage being entered.
2. **User Input**: Every time you receive input from the user, log with
   entry_type="user_input" and content containing the FULL verbatim user input.
   NEVER summarize user input — log it exactly as received.
3. **AI Response**: After producing a significant response (e.g., presenting
   requirements, asking questions, showing results), log with
   entry_type="ai_response" and a brief summary of what you communicated.
4. **Tool Calls**: After calling any tool, log with entry_type="tool_call"
   and content describing the tool name and what it did.
5. **Approval Gates**: When presenting an approval gate, log with
   entry_type="approval_prompt" and the summary of what is being approved.
6. **Approval Decisions**: When the user approves or requests changes, log
   with entry_type="approval_decision" and the user's choice.

The `stage` parameter should always be your current SDLC stage name
(e.g., "Requirements Analysis", "Workspace Detection", etc.).

CRITICAL: Audit logging is NON-OPTIONAL. Every agent interaction must be
logged. Failure to log is a compliance violation. Call `log_audit_entry`
proactively — do not wait to be asked.
"""

_PRIOR_PHASE_CONTEXT_ADDENDUM = """

CRITICAL CONSTRAINT — AWS Only:
All infrastructure, deployment, and cloud service decisions MUST target AWS
(Amazon Web Services) exclusively. Do NOT offer, suggest, or reference other
cloud providers (Azure, GCP, etc.). When presenting options for hosting,
compute, storage, databases, auth, CDN, messaging, or any cloud service,
only include AWS services.

IMPORTANT — Prior Phase Decisions (DO NOT RE-ASK):
Before generating your own clarifying questions or making any decisions, you
MUST load ALL available context from prior phases. This includes both the Q&A
answers AND the generated artifacts.

STEP A — Load Q&A answers by calling `read_answers` with these paths
(skip any that return a not-found error):
- aidlc-docs/inception/requirements/requirement-verification-questions.md
  (Requirements Analysis: target audience, tech stack, database, auth, platform)
- aidlc-docs/inception/application-design/design-questions.md
  (Application Design: architecture style, storage model, auth model, real-time needs)

STEP B — Read the generated artifact files to understand what prior agents
produced (skip any that return a not-found error):
- aidlc-docs/inception/requirements/requirements.md (full requirements document)
- aidlc-docs/inception/user-stories/stories.md (generated user stories)
- aidlc-docs/inception/user-stories/personas.md (user personas)
- aidlc-docs/inception/application-design/components.md (component definitions)
- aidlc-docs/inception/application-design/services.md (service layer definitions)
- aidlc-docs/inception/application-design/component-dependency.md (dependency relationships)
- aidlc-docs/inception/plans/execution-plan.md (the execution plan)

Treat ALL prior answers and artifact content as ESTABLISHED CONSTRAINTS.
You MUST NOT re-ask questions that were already answered in a prior phase.
For example:
- If the user already chose "MongoDB" for the database, do not ask about database choice again.
- If the user already chose "React" for the frontend, do not ask about frontend framework again.
- If the user already chose "Serverless" architecture, do not ask about architecture style again.

Your questions should go DEEPER into implementation details that BUILD ON these
prior decisions, not repeat them. Reference the prior answers and artifact
content explicitly when generating your own artifacts and designs.
"""


_HUMAN_INPUT_ADDENDUM = """

IMPORTANT — Human-in-the-Loop Interaction:
You have access to the `human_input` tool. You MUST use this tool whenever
you need ANY input, confirmation, or decision from the user.
- Call `human_input(message=..., breakout_of_loop=False)` to pause and wait
  for user input. The user's response is returned to you.
- NEVER assume the user's answer. ALWAYS call the tool and wait for their response.
- Do NOT proceed to the next question until the current one is answered.
- When you need answers to clarifying questions, ask them ONE AT A TIME via
  `human_input` with `breakout_of_loop=False`.
- Only use `breakout_of_loop=True` when you want to completely stop the agent
  and hand control back to the user (e.g. at the very end of a workflow).
- If the user types "exit", "quit", or "/bye", the session will stop automatically.
  Do NOT try to continue after receiving an exit command.

ABSOLUTE RULE — NEVER Proceed Without User Input:
This is the single most important rule in the entire system. If you ask the
user a question, present options, or request any form of input, you MUST:
1. Call `human_input` IMMEDIATELY after presenting the question.
2. WAIT for the user's response before doing ANYTHING else.
3. NEVER generate content, call tools, or proceed to the next step until
   you have received the user's actual response from `human_input`.

VIOLATION: Asking a question and then continuing to work without calling
`human_input` is a CRITICAL FAILURE. This breaks the entire interactive
workflow and makes the system unusable. There are ZERO exceptions to this rule.

CRITICAL — NEVER Self-Answer Questions:
If you present a question, options, or a "ready to proceed?" prompt to the
user, you MUST call `human_input` to collect their response BEFORE taking
any action. You are STRICTLY FORBIDDEN from:
- Asking a question and then answering it yourself
- Presenting options and then choosing one on the user's behalf
- Saying "Are you ready?" and then immediately proceeding as if they said yes
- Writing "I'll start X now" after a question without waiting for the user
- Generating design artifacts, code, or documents before collecting all answers

If you write a question or present choices in your text output, the VERY NEXT
thing you do MUST be a `human_input` tool call to collect the user's answer.
No exceptions. Proceeding without the user's explicit response is a critical
violation of the human-in-the-loop protocol.

MANDATORY — Standard Question Format (ALL Agents):
When presenting clarifying questions to the user (via `human_input`), you
MUST use EXACTLY this markdown format for EVERY question:

📚 **{Stage Name} — Question {X} of {Y}**

{Question text}

**Options:**
1. {First option}
2. {Second option}
3. {Third option}
...
X) Other — please describe

Please type the number of your choice, or type `X` and describe your response.

CRITICAL FORMATTING RULES FOR QUESTIONS:
- Options MUST be numbered starting from 1 (NOT letters like A, B, C, D)
- Each option MUST be on its own line with a number followed by a period and space
- The last option MUST always be "X) Other — please describe" (using X, not a number)
- Include a blank line between the question text and "**Options:**"
- Include a blank line between the last option and the instruction line
- The stage name should be your current stage (e.g., "Application Design")
- NEVER use letter-based options (A, B, C, D) — ALWAYS use numbers (1, 2, 3, 4)
- Ask questions ONE AT A TIME. After each question, call `human_input` and WAIT.
- After receiving each answer, call `record_answer` before asking the next question.

CRITICAL — Approval Gate → human_input Handoff (MANDATORY PROCEDURE):
When you call `present_approval_gate`, it returns a formatted message that
ALREADY contains the summary, guidance, numbered options with descriptions,
and a closing instruction line. You MUST follow this EXACT procedure:

1. Call `present_approval_gate(stage=..., summary=..., options=...)`.
2. The tool returns a formatted string. This string contains everything the
   user needs to see, including the "**Please choose:**" section with numbered
   options and the "Type ..." instruction.
3. Your VERY NEXT action MUST be to call `human_input`. The `message` parameter
   MUST contain the **Please choose:** section and all numbered options from
   the `present_approval_gate` return value. You may prepend your own context
   (tables, summaries) ABOVE the options, but the message MUST END with the
   options block. Specifically, the last lines of your `human_input` message
   MUST be:
   - The "**Please choose:**" heading
   - Each numbered option with its description
   - The "Type ..." instruction line
4. Do NOT replace, rewrite, summarize, or omit the options. Do NOT substitute
   "Your response:" for the options. Do NOT end the message with a table or
   summary — the options MUST be the final content.
5. After receiving the user's response from `human_input`, call
   `process_approval_response` with their answer.

EXAMPLE of correct approval gate flow:
  Step 1: result = present_approval_gate(stage="Workflow Planning", summary="...", options=["Request Changes", "Approve & Continue"])
  Step 2: human_input(message="<your context tables/summary>\\n\\n**Please choose:**\\n1. **Request Changes** — I'll incorporate your feedback and regenerate the plan.\\n2. **Approve & Continue** — The plan looks good — proceed to the next stage.\\n\\nType `1` or `Request Changes`, or `2` or `Approve & Continue`.")
  Step 3: process_approval_response(response=<user's answer>)
  Step 4: If approved → log the approval via log_audit_entry, then STOP.

VIOLATION EXAMPLES (NEVER do these):
  ❌ Ending human_input message with "Your response:" instead of the options
  ❌ Ending human_input message with a table and no options
  ❌ Rewriting the options in your own words instead of using the formatted ones
  ❌ Skipping human_input entirely after present_approval_gate
  ❌ Continuing to work on the NEXT stage after approval
  ❌ Using letter options (A, B, C, D) instead of numbers (1, 2, 3, 4)
  ❌ Asking multiple questions at once instead of one at a time
  ❌ Proceeding to generate artifacts before all questions are answered

CRITICAL — Every `human_input` Message MUST End With a Clear Question:
The `message` you pass to `human_input` is the ONLY thing the user sees.
You MUST NEVER send a message that leaves the user without a clear, explicit
question to answer. Every single `human_input` call MUST satisfy ALL of these:

1. The message MUST end with a specific, actionable question — not just
   "Your response:" or a bare prompt. The user must know exactly what they
   are being asked.
2. When there are discrete choices, you MUST list them as numbered options
   at the end of the message, followed by a one-line instruction like
   "Type `1` or `Request Changes`, or `2` or `Approve & Continue`."
3. NEVER end a message with only a table, status summary, or informational
   content. Information alone is NOT a question. Always follow information
   with the actual question and options.
4. For open-ended questions (e.g., "Describe your preferred tech stack"),
   still end with a clear prompt like: "Please describe your preference below."

CRITICAL — Answer Persistence:
After collecting EACH answer from the user via `human_input`, you MUST call
`record_answer(path=<question_file_path>, question_number=<N>, answer=<response>)`
to persist the answer into the question markdown file. This ensures:
- Answers are saved to disk for session continuity and resumption.
- The `read_answers` tool can find them later for ambiguity detection.
- If the session is interrupted, answers are not lost.
"""


# All 13 node names in the graph
NODE_NAMES = [
    "workspace_detection_ext",
    "workspace_detection",
    "reverse_engineering",
    "requirements_analysis",
    "user_stories",
    "workflow_planning",
    "nfr_requirements",
    "nfr_design",
    "application_design",
    "application_design_ext",
    "units_generation",
    "inception_complete",
    "construction_handoff",
    "test_planning",
    "test_execution",
    "qa_report",
    "deployment_planning",
    "release",
    # "functional_design",
    # "infrastructure_design",
    # "code_generation",
    # "build_and_test",
]

# Maps stage_progress keys to the graph node IDs they own.
# Extension nodes are implicitly completed with their parent stage.
# Must be updated when new extension agents are added.
STAGE_TO_NODE_IDS = {
    "workspace_detection": ("workspace_detection_ext", "workspace_detection"),
    "reverse_engineering": ("reverse_engineering",),
    "requirements_analysis": ("requirements_analysis",),
    "user_stories": ("user_stories",),
    "workflow_planning": ("workflow_planning",),
    "nfr_requirements": ("nfr_requirements",),
    "nfr_design": ("nfr_design",),
    "application_design": ("application_design", "application_design_ext"),
    "units_generation": ("units_generation",),
    "inception_complete": ("inception_complete",),
    "construction_handoff": ("construction_handoff",),
    "test_planning": ("test_planning",),
    "test_execution": ("test_execution",),
    "qa_report": ("qa_report",),
    "deployment_planning": ("deployment_planning",),
    "release": ("release",),
}


# ---------------------------------------------------------------------------
# Registry-derived segment partition (design Section 5a, Req 8.3).
#
# The segment->stages partition is NOT hardcoded as BA_SEGMENT_STAGES /
# SA_SEGMENT_STAGES.  It is read from the shared Persona/Workflow Registry,
# ordered by persona order, and expanded to graph node IDs via the existing
# STAGE_TO_NODE_IDS mapping (so extension nodes follow their parent stage).
#
# Because the partition is derived, adding a third segment to the registry
# automatically yields a third partition here with no orchestrator edit (the
# extensibility contract in design Section 5d). Tasks 5.2 (handoff gates) and
# 5.4 (persona-aware session entry) consume SEGMENTS / SEGMENT_NODE_IDS /
# segment_node_ids() rather than naming BA or SA directly.
# ---------------------------------------------------------------------------

# Ordered {segment: [stage names]} partition, ordered by persona.order.
SEGMENTS: dict[str, list[str]] = workflow_registry.segments()


def segment_node_ids(seg: str) -> set[str]:
    """Return the set of graph node IDs owned by segment ``seg``.

    Each of the segment's registry stages is expanded to its graph node IDs via
    :data:`STAGE_TO_NODE_IDS` (extension nodes follow their parent stage). A
    stage with no entry in ``STAGE_TO_NODE_IDS`` contributes no node IDs.
    Pure/derived from the registry + ``STAGE_TO_NODE_IDS`` so a new segment in
    the registry needs no code change here.
    """
    node_ids: set[str] = set()
    for stage in SEGMENTS.get(seg, ()):
        node_ids.update(STAGE_TO_NODE_IDS.get(stage, ()))
    return node_ids


# Precomputed {segment: set(node_ids)} mapping derived from the registry.
SEGMENT_NODE_IDS: dict[str, set[str]] = {
    seg: segment_node_ids(seg) for seg in SEGMENTS
}


def persist_state(shared_state: dict) -> None:
    """Persist SharedState to S3 via the MCP Gateway.

    Called after each stage transition to ensure state durability.
    Uses ``tools.state._persist_via_gateway`` when ``_mcp_client`` and
    ``_session_id`` are available in the shared state.

    Args:
        shared_state: The current SharedState dictionary.
    """
    from tools.state import _persist_via_gateway

    mcp_client = shared_state.get("_mcp_client")
    session_id = shared_state.get("_session_id")

    if mcp_client is not None and session_id:
        md_content = _state_to_markdown(shared_state)
        _persist_via_gateway(mcp_client, session_id, md_content)
    else:
        logger.warning(
            "persist_state: _mcp_client or _session_id missing; skipping persistence"
        )


def advance_unit_loop(shared_state: dict) -> bool:
    """Advance the per-unit loop after Code Generation completes for a unit.

    Increments current_unit_index, adds the completed unit to completed_units.

    Args:
        shared_state: The current SharedState dictionary.

    Returns:
        True if all units are done (should transition to Build & Test),
        False if more units remain (should cycle back to Functional Design).
    """
    units = shared_state.get("units", {})
    items = units.get("items", [])
    current_idx = units.get("current_unit_index", 0)

    if current_idx < len(items):
        unit_name = items[current_idx].get("name", f"unit-{current_idx}")
        completed = units.get("completed_units", [])
        if unit_name not in completed:
            completed.append(unit_name)
        units["completed_units"] = completed

    # Advance index
    units["current_unit_index"] = current_idx + 1

    # Check if all units are done
    all_done = units["current_unit_index"] >= len(items)
    return all_done


# --- Condition functions ---
# Each condition captures shared_state via closure so it can read
# the latest values at edge-evaluation time.


def _make_brownfield_condition(shared_state: dict):
    """Condition: workspace_detection -> reverse_engineering.

    Only transition to RE when brownfield=true and no RE artifacts exist.
    """
    def condition(graph_state: GraphState) -> bool:
        if not shared_state.get("brownfield", False):
            return False
        artifacts = shared_state.get("artifacts", {}).get("reverse_engineering", {})
        for val in artifacts.values():
            if val:
                return False
        return True

    return condition


def _make_not_brownfield_condition(shared_state: dict):
    """Condition: workspace_detection -> requirements_analysis (skip RE).

    Transition directly when brownfield=false or RE artifacts already exist.
    """
    def condition(graph_state: GraphState) -> bool:
        if not shared_state.get("brownfield", False):
            return True
        artifacts = shared_state.get("artifacts", {}).get("reverse_engineering", {})
        has_artifacts = any(val for val in artifacts.values())
        return has_artifacts

    return condition



def _make_stage_in_plan_condition(shared_state: dict, stage_name: str):
    """Generic condition: execute stage only if it's in the execution plan."""
    def condition(graph_state: GraphState) -> bool:
        stages = shared_state.get("execution_plan", {}).get("stages_to_execute", [])
        return stage_name in stages

    return condition


def _make_stage_not_in_plan_condition(shared_state: dict, stage_name: str):
    """Generic condition: skip stage if it's not in the execution plan."""
    def condition(graph_state: GraphState) -> bool:
        stages = shared_state.get("execution_plan", {}).get("stages_to_execute", [])
        return stage_name not in stages

    return condition


def _make_first_design_stage_condition(shared_state: dict, target: str, higher_priority: list[str]):
    """Route to *target* only when it is in the plan AND none of the
    *higher_priority* stages are in the plan.

    This prevents the graph from fanning out to multiple design stages
    simultaneously when all of them are in the execution plan.
    """
    def condition(graph_state: GraphState) -> bool:
        stages = shared_state.get("execution_plan", {}).get("stages_to_execute", [])
        if target not in stages:
            return False
        # Only route here if no higher-priority stage is also in the plan
        return all(s not in stages for s in higher_priority)

    return condition


def _make_no_design_stages_condition(shared_state: dict, stage_names: list[str]):
    """Route to code_generation when NONE of the listed design stages are in the plan."""
    def condition(graph_state: GraphState) -> bool:
        stages = shared_state.get("execution_plan", {}).get("stages_to_execute", [])
        return all(s not in stages for s in stage_names)

    return condition


def _make_cycle_back_condition(shared_state: dict):
    """Condition: code_generation -> functional_design (cycle back for next unit).

    Returns True when there are more units to process.
    """
    def condition(graph_state: GraphState) -> bool:
        units = shared_state.get("units", {})
        items = units.get("items", [])
        completed = units.get("completed_units", [])
        return len(completed) < len(items)

    return condition


def _make_all_units_done_condition(shared_state: dict):
    """Condition: code_generation -> build_and_test.

    Returns True when all units have completed Code Generation,
    or when there are no units defined (skip straight to build_and_test).
    """
    def condition(graph_state: GraphState) -> bool:
        units = shared_state.get("units", {})
        items = units.get("items", [])
        completed = units.get("completed_units", [])
        # If no units were defined, proceed to build_and_test
        if len(items) == 0:
            return True
        return len(completed) >= len(items)

    return condition


def build_sdlc_graph(
    shared_state: dict,
    model=None,
    interactive: bool = False,
    session_manager: AgentCoreMemorySessionManager | None = None,
    graph_session_manager: S3SessionManager | None = None,
    skip_completed: set[str] | None = None,
) -> Graph:
    """Construct the SDLC workflow graph with all 14 nodes and edges.

    Args:
        shared_state: The initialized SharedState dictionary. Condition
            functions capture this reference so they read live values.
        model: A BedrockModel instance. If None, one is created via
            create_model().
        interactive: If True, inject human-in-the-loop tools (ask_human,
            ask_human_choice) into every agent and append interaction
            instructions to their system prompts.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence via AgentCore Memory.
            When provided, it is passed to each Stage_Agent via the
            factory.
        graph_session_manager: Optional S3SessionManager for graph-level
            session persistence. When provided, the graph persists its
            execution state (completed nodes, next nodes) to S3 after
            each node. On resume, the graph restores this state and
            skips completed nodes structurally.
        skip_completed: Optional set of stage names already completed.
            When provided, nodes for these stages are excluded from
            the graph and the entry point is set to the first
            remaining node.

    Returns:
        A built Strands Graph ready for execution.
    """
    if model is None:
        from config.model import create_model
        model = create_model()

    # Always load audit tool — audit logging is mandatory for all agents
    from tools.audit import log_audit_entry
    audit_tools = [log_audit_entry]

    # Optionally load human-in-the-loop tools
    hitl_tools = []
    if interactive:
        from tools.human_input import human_input
        from tools.questions import record_answer
        hitl_tools = [human_input, record_answer]

    # --- Create all agents ---
    agents = {
        "workspace_detection_ext": create_workspace_detection_ext_agent(model),
        "workspace_detection": create_workspace_detection_agent(model),
        "reverse_engineering": create_reverse_engineering_agent(model),
        "requirements_analysis": create_requirements_analysis_agent(model),
        "user_stories": create_user_stories_agent(model),
        "workflow_planning": create_workflow_planning_agent(model),
        "nfr_requirements": create_nfr_requirements_agent(model),
        "nfr_design": create_nfr_design_agent(model),
        "application_design": create_application_design_agent(model),
        "application_design_ext": create_application_design_ext_agent(model),
        "units_generation": create_units_generation_agent(model),
        "inception_complete": create_inception_complete_agent(model),
        "construction_handoff": create_construction_handoff_agent(model),
        "test_planning": create_test_planning_agent(model),
        "test_execution": create_test_execution_agent(model),
        "qa_report": create_qa_report_agent(model),
        "deployment_planning": create_deployment_planning_agent(model),
        "release": create_release_agent(model),
        # "functional_design": create_functional_design_agent(model),
        # "infrastructure_design": create_infrastructure_design_agent(model),
        # "code_generation": create_code_generation_agent(model),
        # "build_and_test": create_build_and_test_agent(model),
    }

    # Determine which nodes to exclude from the graph entirely.
    _skip_ids: set[str] = set()
    for stage in (skip_completed or ()):
        _skip_ids.update(STAGE_TO_NODE_IDS.get(stage, ()))

    # Inject stage banner and audit tool into remaining agents
    for node_name, agent in agents.items():
        if node_name in _skip_ids:
            continue
        agent.tool_registry.process_tools(audit_tools)
        if agent.system_prompt:
            agent.system_prompt += _STAGE_BANNER_ADDENDUM
            agent.system_prompt += _AUDIT_ADDENDUM

            # Inject prior-phase context into all agents except the
            # extension agent itself (which is the one that populates it).
            if node_name != "workspace_detection_ext":
                agent.system_prompt += _PRIOR_PHASE_CONTEXT_ADDENDUM


    # Inject human-in-the-loop tools and prompt addendum
    if interactive and hitl_tools:
        for node_name, agent in agents.items():
            if node_name in _skip_ids:
                continue  # excluded from graph
            agent.tool_registry.process_tools(hitl_tools)
            if agent.system_prompt:
                agent.system_prompt += _HUMAN_INPUT_ADDENDUM

    builder = GraphBuilder()

    # Set execution limits to prevent infinite loops in the per-unit cycle.
    # 14 stages + up to 10 units × 6 construction stages = ~74 max executions.
    builder.set_max_node_executions(105)

    # --- S3SessionManager for structural resume ---
    if graph_session_manager is not None:
        builder.set_session_manager(graph_session_manager)

    # --- Add nodes (exclude completed stages) ---
    for node_name, agent in agents.items():
        if node_name in _skip_ids:
            continue
        builder.add_node(agent, node_name)

    # --- Add hub nodes (inception + developer/QA segment-boundary hubs) ---
    INCEPTION_HUB_STAGES = [
        "post_requirements_analysis",
        "post_user_stories",
        "post_workflow_planning",
        "post_application_design",
        "post_units_generation",
        "post_inception_complete",
        # Segment-boundary exit hubs whose outbound edge is rewired through a
        # handoff gate (dev->qa and qa->devops boundaries).
        "post_construction_handoff",
        "post_qa_report",
    ]
    for stage in INCEPTION_HUB_STAGES:
        builder.add_node(create_integration_hub_node(stage), f"hub_{stage}")

    # --- Add Handoff_Gate nodes (design Section 5b, Req 6.2 / task 5.2) ---
    #
    # For every segment with a successor in persona order, add a non-LLM gate
    # node ``handoff_gate__{seg}`` whose outbound edge will replace the exit
    # hub → next-segment-entry boundary edge below. Wholly registry-derived:
    # adding a third segment to the registry yields a third gate without a
    # code change here.
    #
    # When the active persona's session has the upstream segment fully
    # excluded (its ``entry_node`` is in ``_skip_ids``), the gate would be
    # unreachable in that session — the gate's id is added to ``_skip_ids``
    # so the registry-derived boundary edges below are silently dropped by
    # ``_edge``. This keeps the gate out of the next persona's graph view
    # without any persona-specific branching.
    for seg in workflow_registry.ordered_segments():
        if workflow_registry.successor_segment(seg) is None:
            continue
        gate_id = f"handoff_gate__{seg}"
        seg_entry = workflow_registry.entry_node(seg)
        if seg_entry in _skip_ids:
            # Upstream segment is fully excluded for this session — leave
            # the gate out of the graph and ensure boundary edges to it
            # are silently dropped via _edge's skip check.
            _skip_ids.add(gate_id)
            continue
        builder.add_node(create_handoff_gate_node(seg), gate_id)

    # --- Set entry point (first non-skipped node in execution order) ---
    _entry = next(
        (n for n in NODE_NAMES if n not in _skip_ids),
        "workspace_detection_ext",
    )
    builder.set_entry_point(_entry)

    # Helper: silently skip edges involving excluded nodes.
    def _edge(from_n, to_n, condition=None):
        if from_n in _skip_ids or to_n in _skip_ids:
            logger.debug("Skipping edge %s -> %s (node excluded)", from_n, to_n)
            return
        builder.add_edge(from_n, to_n, condition=condition)

    # --- Add edges (skipping excluded nodes) ---

    # Workspace Detection Ext -> Workspace Detection (unconditional)
    _edge("workspace_detection_ext", "workspace_detection")

    # Workspace Detection -> Reverse Engineering (conditional: brownfield)
    _edge(
        "workspace_detection",
        "reverse_engineering",
        condition=_make_brownfield_condition(shared_state),
    )

    # Workspace Detection -> Requirements Analysis (skip RE when not brownfield)
    _edge(
        "workspace_detection",
        "requirements_analysis",
        condition=_make_not_brownfield_condition(shared_state),
    )

    # Reverse Engineering -> Requirements Analysis (unconditional)
    _edge("reverse_engineering", "requirements_analysis")

    # --- Integration Hub edges (using _edge to respect skip logic) ---
    # requirements_analysis → hub → user_stories (always)
    _edge("requirements_analysis", "hub_post_requirements_analysis")
    _edge("hub_post_requirements_analysis", "user_stories")

    # user_stories → hub → handoff_gate → workflow_planning (BA→SA boundary)
    #
    # Every segment with a successor (per ``ordered_segments()``) gets a
    # ``handoff_gate__{seg}`` inserted on its ``exit_hub`` outbound edge so
    # the graph halts at the boundary instead of falling through into the
    # next segment's first stage in the same session (design Section 5b /
    # Req 6.2). The wiring is registry-derived: BA→SA is the only configured
    # boundary today, but a third segment automatically gets its own gate
    # with no code change here.
    _edge("user_stories", "hub_post_user_stories")
    for seg in workflow_registry.ordered_segments():
        successor = workflow_registry.successor_segment(seg)
        if successor is None:
            continue
        exit_hub_id = workflow_registry.exit_hub(seg)
        gate_id = f"handoff_gate__{seg}"
        next_entry = workflow_registry.entry_node(successor)
        # Replace the exit-hub → next-segment-entry-node edge with two edges
        # routed through the gate. _edge silently drops boundary edges when
        # any endpoint is in _skip_ids (e.g. the SA-side build, where the BA
        # stages and the gate are all excluded).
        _edge(exit_hub_id, gate_id)
        _edge(gate_id, next_entry)

    # workflow_planning → hub → application_design / units_generation / inception_complete
    # workflow_planning → nfr_requirements → nfr_design → hub_post_workflow_planning.
    # The NFR stages are project-level SA design-time stages that always run
    # between workflow planning and the (conditional) design routing, so the NFR
    # documents inform application design, the construction brief, QA and DevOps.
    _edge("workflow_planning", "nfr_requirements")
    _edge("nfr_requirements", "nfr_design")
    _edge("nfr_design", "hub_post_workflow_planning")
    _edge(
        "hub_post_workflow_planning", "application_design",
        condition=_make_stage_in_plan_condition(shared_state, "application_design"),
    )
    _edge(
        "hub_post_workflow_planning", "units_generation",
        condition=_make_first_design_stage_condition(
            shared_state, "units_generation", ["application_design"],
        ),
    )
    _edge(
        "hub_post_workflow_planning", "inception_complete",
        condition=_make_no_design_stages_condition(
            shared_state, ["application_design", "units_generation"],
        ),
    )

    # application_design → application_design_ext → hub → units_generation / inception_complete
    _edge("application_design", "application_design_ext")
    _edge("application_design_ext", "hub_post_application_design")
    _edge(
        "hub_post_application_design", "units_generation",
        condition=_make_stage_in_plan_condition(shared_state, "units_generation"),
    )
    _edge(
        "hub_post_application_design", "inception_complete",
        condition=_make_stage_not_in_plan_condition(shared_state, "units_generation"),
    )

    # units_generation → hub → inception_complete
    _edge("units_generation", "hub_post_units_generation")
    _edge("hub_post_units_generation", "inception_complete")

    # inception_complete → hub_post_inception_complete (sa_segment's terminal
    # exit hub). The registry-derived handoff-gate loop above wires this hub
    # → handoff_gate__sa_segment → construction_handoff (the dev_segment entry),
    # so the SA's session halts at the SA→Dev boundary and emits an
    # "Assign to Software Developer" handoff card (symmetric to BA→SA).
    _edge("inception_complete", "hub_post_inception_complete")

    # --- Developer → QA → DevOps segment wiring -------------------------------
    # dev_segment exit: construction_handoff routes to its exit hub; the
    # registry-derived gate loop above wires hub_post_construction_handoff →
    # handoff_gate__dev_segment → test_planning (the qa_segment entry), so the
    # developer's session halts at the Dev→QA boundary.
    _edge("construction_handoff", "hub_post_construction_handoff")

    # qa_segment intra-segment chain, then exit hub. The gate loop wires
    # hub_post_qa_report → handoff_gate__qa_segment → deployment_planning
    # (the devops_segment entry), halting the QA session at the QA→DevOps
    # boundary.
    _edge("test_planning", "test_execution")
    _edge("test_execution", "qa_report")
    _edge("qa_report", "hub_post_qa_report")

    # devops_segment intra-segment chain. `release` is the terminal leaf of the
    # whole graph (devops_segment has no successor → no handoff gate).
    _edge("deployment_planning", "release")

    # # Application Design -> Functional Design (skip units gen)
    # builder.add_edge(
    #     "application_design",
    #     "functional_design",
    #     condition=_make_first_design_stage_condition(
    #         shared_state, "functional_design", ["units_generation"],
    #     ),
    # )

    # # Application Design -> Code Generation (skip units gen + functional design)
    # builder.add_edge(
    #     "application_design",
    #     "code_generation",
    #     condition=_make_no_design_stages_condition(
    #         shared_state, ["units_generation", "functional_design"],
    #     ),
    # )

    # # Units Generation -> Functional Design (if functional_design in plan)
    # builder.add_edge(
    #     "units_generation",
    #     "functional_design",
    #     condition=_make_stage_in_plan_condition(shared_state, "functional_design"),
    # )

    # # Units Generation -> Code Generation (skip functional design)
    # builder.add_edge(
    #     "units_generation",
    #     "code_generation",
    #     condition=_make_stage_not_in_plan_condition(shared_state, "functional_design"),
    # )

    # # --- Construction per-unit edges ---

    # # Functional Design -> NFR Requirements (conditional)
    # builder.add_edge(
    #     "functional_design",
    #     "nfr_requirements",
    #     condition=_make_stage_in_plan_condition(shared_state, "nfr_requirements"),
    # )

    # # Functional Design -> Code Generation (skip NFR stages)
    # builder.add_edge(
    #     "functional_design",
    #     "code_generation",
    #     condition=_make_stage_not_in_plan_condition(shared_state, "nfr_requirements"),
    # )

    # # NFR Requirements -> NFR Design (conditional)
    # builder.add_edge(
    #     "nfr_requirements",
    #     "nfr_design",
    #     condition=_make_stage_in_plan_condition(shared_state, "nfr_design"),
    # )

    # # NFR Requirements -> Code Generation (skip NFR Design)
    # builder.add_edge(
    #     "nfr_requirements",
    #     "code_generation",
    #     condition=_make_stage_not_in_plan_condition(shared_state, "nfr_design"),
    # )

    # # NFR Design -> Infrastructure Design (conditional)
    # builder.add_edge(
    #     "nfr_design",
    #     "infrastructure_design",
    #     condition=_make_stage_in_plan_condition(shared_state, "infrastructure_design"),
    # )

    # # NFR Design -> Code Generation (skip infra design — fallback)
    # builder.add_edge(
    #     "nfr_design",
    #     "code_generation",
    #     condition=_make_stage_not_in_plan_condition(shared_state, "infrastructure_design"),
    # )

    # # Infrastructure Design -> Code Generation (unconditional)
    # builder.add_edge("infrastructure_design", "code_generation")

    # # --- Cycle edge: per-unit loop ---

    # # Code Generation -> Functional Design (cycle back for next unit)
    # builder.add_edge(
    #     "code_generation",
    #     "functional_design",
    #     condition=_make_cycle_back_condition(shared_state),
    # )

    # # Code Generation -> Build & Test (all units done)
    # builder.add_edge(
    #     "code_generation",
    #     "build_and_test",
    #     condition=_make_all_units_done_condition(shared_state),
    # )

    # Build the graph
    graph = builder.build()
    return graph
