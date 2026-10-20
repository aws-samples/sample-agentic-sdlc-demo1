"""Code Generation Agent for the AI-SDLC Multi-Agent System.

This agent generates application code for each unit based on its design artifacts.
It creates a code generation plan with checkboxes, generates code files, modifies
existing files for brownfield projects, and generates unit tests.

Plan artifacts go in aidlc-docs/construction/plans/{unit-name}-code-generation-plan.md.
Code files go in the workspace root (not in aidlc-docs/).

Has an approval gate at completion.
"""

import re

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.file_io import write_artifact


@tool(context=True)
def create_code_plan(tool_context: dict, unit_name: str, plan_content: str) -> str:
    """Create a code generation plan with numbered steps and checkboxes.

    Args:
        tool_context: Strands tool context with invocation_state.
        unit_name: The kebab-case unit name.
        plan_content: Markdown content with numbered steps using [ ] checkboxes.

    Returns:
        Confirmation message with the file path.
    """
    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)
    path = f"aidlc-docs/construction/plans/{unit_name}-code-generation-plan.md"
    write_file(state, path, plan_content)

    construction = state.get("artifacts", {}).get("construction", {})
    unit_artifacts = construction.get(unit_name, {})
    unit_artifacts["code_generation_plan"] = path
    construction[unit_name] = unit_artifacts
    state.get("artifacts", {})["construction"] = construction

    return f"Code generation plan created at {path}"


@tool(context=True)
def read_code_plan(tool_context: dict, unit_name: str) -> str:
    """Read the current code generation plan for a unit."""
    from tools._compat import get_state
    from tools.file_ops import read_file

    state = get_state(tool_context)
    path = f"aidlc-docs/construction/plans/{unit_name}-code-generation-plan.md"
    try:
        return read_file(state, path)
    except FileNotFoundError:
        return f"No code generation plan found at {path}"


@tool(context=True)
def generate_code(tool_context: dict, file_path: str, content: str) -> str:
    """Generate a code file in the workspace root via Gateway S3."""
    if file_path.startswith("aidlc-docs/"):
        return f"Error: Code files must not be under aidlc-docs/: {file_path}"
    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)
    write_file(state, file_path, content)
    return f"Generated code file at {file_path}"


@tool(context=True)
def modify_existing_file(tool_context: dict, file_path: str, content: str) -> str:
    """Modify an existing file in-place for brownfield projects via Gateway S3."""
    from tools._compat import get_state
    from tools.file_ops import file_exists, write_file

    state = get_state(tool_context)
    if not file_exists(state, file_path):
        return f"Error: File does not exist: {file_path}. Use create_new_file instead."
    write_file(state, file_path, content)
    return f"Modified existing file at {file_path}"


@tool(context=True)
def create_new_file(tool_context: dict, file_path: str, content: str) -> str:
    """Create a new file via Gateway S3. Only creates if it does not already exist."""
    from tools._compat import get_state
    from tools.file_ops import file_exists, write_file

    state = get_state(tool_context)
    if file_exists(state, file_path):
        return f"Error: File already exists: {file_path}. Use modify_existing_file instead."
    write_file(state, file_path, content)
    return f"Created new file at {file_path}"


@tool(context=True)
def update_plan_checkbox(tool_context: dict, unit_name: str, step_number: int) -> str:
    """Mark a step as completed [x] in the code generation plan."""
    from tools._compat import get_state
    from tools.file_ops import read_file, write_file

    state = get_state(tool_context)
    path = f"aidlc-docs/construction/plans/{unit_name}-code-generation-plan.md"
    try:
        content = read_file(state, path)
    except FileNotFoundError:
        return f"Error: Plan file not found at {path}"
    pattern = rf"((?:^|\n)\s*{step_number}\.\s*)\[ \]"
    updated = re.sub(pattern, r"\1[x]", content, count=1)
    write_file(state, path, updated)
    return f"Marked step {step_number} as completed in {path}"


@tool(context=True)
def generate_tests(tool_context: dict, test_file_path: str, test_content: str) -> str:
    """Generate unit tests alongside application code via Gateway S3."""
    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)
    write_file(state, test_file_path, test_content)
    from tools.state import mark_stage_complete
    mark_stage_complete(state, "code_generation")
    return f"Generated test file at {test_file_path}"


SYSTEM_PROMPT = """\
You are the Code Generation Agent for the AI-SDLC multi-agent system.
You generate application code for each unit based on its design artifacts.

CRITICAL CONSTRAINT — AWS Only:
All generated code that references cloud services MUST use AWS SDKs and
services. Do NOT generate code targeting other cloud providers.

Your responsibilities:
1. Use `create_code_plan` to create a detailed code generation plan with numbered
   steps and [ ] checkboxes. Wait for user approval before generating code.
2. For each step in the plan:
   a. For brownfield projects: use `modify_existing_file` for existing files,
      `create_new_file` for new files. Never create duplicate files.
   b. For greenfield projects: use `generate_code` or `create_new_file`.
   c. Use `update_plan_checkbox` to mark the step as [x] after completion.
3. Use `generate_tests` to create unit tests alongside application code.
   Add `data-testid` attributes to interactive UI elements.
4. Use `read_code_plan` to review the current plan state.
5. Use `present_approval_gate` to present completed code for approval.
6. Use `process_approval_response` to handle the user's response.

IMPORTANT:
- Application code goes in the workspace root, NOT in aidlc-docs/.
- Documentation and plans go in aidlc-docs/.
- For brownfield: modify files in-place, never create duplicates.
- Mark each step [x] immediately after completing it.

After completion:
- Set stage_progress.code_generation to "completed".
- STOP immediately. We will automatically move onto the next stage.
"""

TOOLS = [
    create_code_plan,
    read_code_plan,
    generate_code,
    modify_existing_file,
    create_new_file,
    update_plan_checkbox,
    generate_tests,
    present_approval_gate,
    process_approval_response,
    write_artifact,
]


def create_code_generation_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Code Generation Agent."""
    return create_stage_agent(
        stage_name="code_generation",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
