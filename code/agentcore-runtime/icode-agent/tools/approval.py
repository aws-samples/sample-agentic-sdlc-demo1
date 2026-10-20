"""Approval gate tools for the AI-SDLC Multi-Agent System.

Provides tools to present approval gates to users and process their responses.
Approval gates pause workflow execution until the user explicitly approves.
"""

from strands.tools import tool

# Standard approval options
DEFAULT_OPTIONS = ["Request Changes", "Approve & Continue"]


@tool(context=True)
def present_approval_gate(
    tool_context: dict, stage: str, summary: str, options: list[str]
) -> str:
    """Present an approval gate to the user and set awaiting_approval flag.

    Builds a context-aware guided message that ends with a clear question
    and numbered options so the user always knows exactly what to do next.

    Sets `awaiting_approval=true` in SharedState and records the gate details.
    The orchestrator should halt until the user provides a response.

    Args:
        tool_context: Strands tool context with invocation_state.
        stage: The stage presenting the approval gate.
        summary: Completion summary to present to the user.
        options: List of options for the user (e.g., ["Request Changes", "Approve & Continue"]).

    Returns:
        Formatted approval gate presentation string with guided question.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    approval = state.get("approval", {})

    # Set approval state
    approval["awaiting_approval"] = True
    approval["current_gate_stage"] = stage
    approval["summary"] = summary
    approval["options"] = options if options else DEFAULT_OPTIONS
    approval["user_response"] = ""

    if "approval" not in state:
        state["approval"] = approval

    effective_options = approval["options"]

    # Ensure we always have at least the default options (Request Changes + Approve & Continue)
    # The agent sometimes passes an incomplete list.
    default_set = {o.lower() for o in DEFAULT_OPTIONS}
    effective_set = {o.lower() for o in effective_options}
    if not default_set.issubset(effective_set):
        # Merge: keep any extra options the agent added, but ensure defaults are present
        merged = list(DEFAULT_OPTIONS)
        for opt in effective_options:
            if opt.lower() not in default_set:
                merged.append(opt)
        effective_options = merged
        approval["options"] = effective_options

    # --- Build context-aware guidance ---
    # Check for skipped stages to provide specific guidance
    exec_plan = state.get("execution_plan", {})
    skipped = exec_plan.get("stages_to_skip", {})
    skipped_count = len(skipped)

    guidance_lines = []
    if skipped_count > 0:
        guidance_lines.append(
            f"I have skipped **{skipped_count}** stage(s) based on the project's "
            f"scope and complexity. If you disagree with any of the skip rationales "
            f"or would like to add skipped stages back into the workflow, please "
            f"select the appropriate option below."
        )
    else:
        guidance_lines.append(
            "All stages are included in the execution plan. Please review "
            "the plan above and choose how to proceed."
        )

    # --- Build the formatted gate ---
    lines = [
        f"## ✅ Approval Gate — {stage}",
        "",
        summary,
        "",
    ]

    # Add contextual guidance
    lines.extend(guidance_lines)
    lines.append("")

    # Add option descriptions so the user understands each choice
    # Use the stage name to make descriptions context-specific
    stage_lower = stage.lower().replace("_", " ")
    lines.append("**Please choose:**")
    option_descriptions = {
        "Request Changes": f"I'll incorporate your feedback and regenerate the {stage_lower}.",
        "Approve & Continue": f"The {stage_lower} looks good — proceed to the next stage.",
        "Add Skipped Stages": "Add one or more of the skipped stages back into the workflow.",
    }
    for i, opt in enumerate(effective_options, 1):
        desc = option_descriptions.get(opt, "")
        if desc:
            lines.append(f"{i}. **{opt}** — {desc}")
        else:
            lines.append(f"{i}. **{opt}**")

    # Build the instruction line dynamically from the options
    option_hints = " or ".join(
        f"`{i}` or `{opt}`" for i, opt in enumerate(effective_options, 1)
    )
    lines.append("")
    lines.append(f"Type {option_hints}.")

    formatted_gate = "\n".join(lines)

    # Store the formatted gate text so the agent can pass it to human_input
    approval["formatted_gate"] = formatted_gate

    return formatted_gate


@tool(context=True)
def process_approval_response(tool_context: dict, response: str) -> dict:
    """Process the user's approval response and update state.

    Handles three cases:
    - Approve: clears awaiting_approval, returns approved=True
    - Request Changes: keeps awaiting_approval, returns approved=False with feedback
    - Unrecognized: returns approved=False with re-prompt message

    Args:
        tool_context: Strands tool context with invocation_state.
        response: The user's response string.

    Returns:
        Dict with 'approved' (bool), 'action' (str), and optional 'feedback' (str).
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    approval = state.get("approval", {})
    normalized = response.strip().lower()

    # Record the response
    approval["user_response"] = response

    # Check for approval
    if _is_approval(normalized):
        approval["awaiting_approval"] = False
        return {
            "approved": True,
            "action": "continue",
            "feedback": "",
        }

    # Check for change request
    if _is_change_request(normalized):
        # Keep awaiting_approval=True so the stage re-executes
        approval["awaiting_approval"] = True
        return {
            "approved": False,
            "action": "request_changes",
            "feedback": response,
        }

    # Unrecognized input
    options = approval.get("options", DEFAULT_OPTIONS)
    options_str = ", ".join(f"'{o}'" for o in options)
    return {
        "approved": False,
        "action": "unrecognized",
        "feedback": f"Unrecognized response. Please choose one of: {options_str}",
    }


def _is_approval(normalized: str) -> bool:
    """Check if the response indicates approval."""
    approval_phrases = [
        "approve", "approve & continue", "approve and continue",
        "continue", "yes", "ok", "lgtm", "looks good",
        "2",  # Option number for "Approve & Continue"
    ]
    return any(phrase in normalized for phrase in approval_phrases)


def _is_change_request(normalized: str) -> bool:
    """Check if the response indicates a change request."""
    change_phrases = [
        "request changes", "change", "revise", "redo",
        "modify", "update", "fix",
        "1",  # Option number for "Request Changes"
    ]
    return any(phrase in normalized for phrase in change_phrases)
