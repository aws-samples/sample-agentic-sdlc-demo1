"""Build and Test Agent for the AI-SDLC Multi-Agent System.

This agent generates comprehensive build and test instructions after all
units of work have completed code generation.

Produces artifacts in aidlc-docs/construction/build-and-test/:
- build-instructions.md
- unit-test-instructions.md
- integration-test-instructions.md
- build-and-test-summary.md
- performance-test-instructions.md (conditional)
- Additional test files based on requirements (contract, security, e2e)

Has an approval gate at completion.
"""

import posixpath

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.file_io import write_artifact

BUILD_TEST_DIR = "aidlc-docs/construction/build-and-test"

# Core instruction files always generated
CORE_ARTIFACTS = [
    "build-instructions.md",
    "unit-test-instructions.md",
    "integration-test-instructions.md",
    "build-and-test-summary.md",
]


@tool(context=True)
def generate_build_instructions(tool_context: dict, content: str) -> str:
    """Generate build-instructions.md via Gateway S3.

    Args:
        tool_context: Strands tool context with invocation_state.
        content: Markdown content for build instructions.

    Returns:
        Confirmation message with the file path.
    """
    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)
    rel_path = posixpath.join(BUILD_TEST_DIR, "build-instructions.md")
    write_file(state, rel_path, content)

    state.get("artifacts", {}).get("build_and_test", {})["build_instructions"] = rel_path
    return f"Build instructions generated at {rel_path}"


@tool(context=True)
def generate_test_instructions(
    tool_context: dict, test_type: str, content: str
) -> str:
    """Generate test instruction files based on test type via Gateway S3.

    Supports: unit-test, integration-test, performance-test, contract-test,
    security-test, e2e-test.

    Args:
        tool_context: Strands tool context with invocation_state.
        test_type: Type of test (e.g., "unit-test", "integration-test").
        content: Markdown content for the test instructions.

    Returns:
        Confirmation message with the file path.
    """
    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)
    filename = f"{test_type}-instructions.md"
    rel_path = posixpath.join(BUILD_TEST_DIR, filename)
    write_file(state, rel_path, content)

    # Update artifacts in SharedState
    bt_artifacts = state.get("artifacts", {}).get("build_and_test", {})
    key = test_type.replace("-", "_") + "_instructions"
    bt_artifacts[key] = rel_path

    return f"Test instructions generated at {rel_path}"


@tool(context=True)
def generate_test_summary(tool_context: dict, content: str) -> str:
    """Generate build-and-test-summary.md via Gateway S3.

    Args:
        tool_context: Strands tool context with invocation_state.
        content: Markdown content for the build and test summary.

    Returns:
        Confirmation message with the file path.
    """
    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)
    rel_path = posixpath.join(BUILD_TEST_DIR, "build-and-test-summary.md")
    write_file(state, rel_path, content)

    state.get("artifacts", {}).get("build_and_test", {})["build_and_test_summary"] = rel_path

    # Mark build_and_test as completed and persist state
    from tools.state import mark_stage_complete
    mark_stage_complete(state, "build_and_test")

    return f"Build and test summary generated at {rel_path}"


SYSTEM_PROMPT = """\
You are the Build and Test Agent for the AI-SDLC multi-agent system.
You generate comprehensive build and test instructions after all units complete.

Your responsibilities:
1. Use `generate_build_instructions` to create build-instructions.md with
   step-by-step build instructions for the project.
2. Use `generate_test_instructions` to create test instruction files:
   - Always generate: unit-test, integration-test
   - Conditional: performance-test (when performance requirements exist)
   - Based on requirements: contract-test, security-test, e2e-test
3. Use `generate_test_summary` to create build-and-test-summary.md with
   an overview of all build and test procedures.
4. Use `present_approval_gate` to present the instructions for approval.
5. Use `process_approval_response` to handle the user's response.

CONDITIONAL ARTIFACTS:
- performance-test-instructions.md: Only when performance requirements exist
  in the SharedState or requirements documents.
- contract-test-instructions.md: When API contracts are defined.
- security-test-instructions.md: When security requirements exist.
- e2e-test-instructions.md: When end-to-end testing is warranted.

After completion:
- Set stage_progress.build_and_test to "completed".
- STOP immediately. We will automatically move onto the next stage.
"""

TOOLS = [
    generate_build_instructions,
    generate_test_instructions,
    generate_test_summary,
    present_approval_gate,
    process_approval_response,
    write_artifact,
]


def create_build_and_test_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Build and Test Agent."""
    return create_stage_agent(
        stage_name="build_and_test",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
