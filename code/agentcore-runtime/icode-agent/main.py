"""Entry point for the AI-SDLC Multi-Agent System.

Provides the main(user_request) function that initializes SharedState,
builds the SDLC graph, and executes it starting at workspace_detection.

Supports session resumption: if an existing aidlc-docs/aidlc-state.md
is found, the persisted state is loaded and execution resumes from the
last completed stage.

Streaming variants:
- ``main_async``: Uses ``graph.stream_async()`` for real-time streaming.
- ``main_sync``: Uses ``callback_handler`` for synchronous CLI environments.
"""

import os

from config.model import create_model
from orchestrator import build_sdlc_graph, persist_state
from shared_state import initialize_shared_state, validate_shared_state
from tools.state import _markdown_to_state
from tools.streaming import create_cli_callback_handler, process_stream_event


# Ordered stages for determining resume point.
_STAGE_ORDER = [
    "workspace_detection",
    "reverse_engineering",
    "requirements_analysis",
    "user_stories",
    "workflow_planning",
    "application_design",
    "units_generation",
    "inception_complete",
    # "functional_design",
    # "nfr_requirements",
    # "nfr_design",
    # "infrastructure_design",
    # "code_generation",
    # "build_and_test",
]


def _find_resume_stage(stage_progress: dict, execution_plan: dict | None = None, brownfield: bool = False) -> str:
    """Determine which stage to resume from based on stage_progress.

    Walks the stage order and returns the first stage that is not
    'completed' or 'skipped'. Stages that were never reached because
    graph edge conditions routed around them (e.g. reverse_engineering
    in greenfield, user_stories when not in plan) are treated as
    implicitly skipped.

    Args:
        stage_progress: Dict mapping stage names to status strings.
        execution_plan: The execution_plan dict from SharedState (optional).
        brownfield: Whether the project is brownfield.
    """
    stages_to_execute = (execution_plan or {}).get("stages_to_execute", [])

    for stage in _STAGE_ORDER:
        status = stage_progress.get(stage, "not_started")
        if status in ("completed", "skipped"):
            continue

        # Stages that are implicitly skipped by graph edge conditions:
        # - reverse_engineering: only runs for brownfield projects
        # - user_stories: only runs if in stages_to_execute
        if stage == "reverse_engineering" and not brownfield:
            continue
        if stage == "user_stories" and stages_to_execute and "user_stories" not in stages_to_execute:
            continue

        # Conditional stages that are not in the execution plan are skipped
        if stage in (
            "application_design", "units_generation",
            # "functional_design",
            # "nfr_requirements", "nfr_design", "infrastructure_design",
        ) and stages_to_execute and stage not in stages_to_execute:
            continue

        return stage

    return _STAGE_ORDER[-1]


def _resume_from_state(shared_state: dict) -> str:
    """Extract resume stage from SharedState using full context."""
    return _find_resume_stage(
        shared_state.get("stage_progress", {}),
        execution_plan=shared_state.get("execution_plan"),
        brownfield=shared_state.get("brownfield", False),
    )


def _load_existing_state(workspace_root: str) -> dict | None:
    """Attempt to load persisted state from aidlc-docs/aidlc-state.md.

    Args:
        workspace_root: Absolute path to the project workspace directory.

    Returns:
        A SharedState dictionary if the state file exists and is parseable,
        or None if no state file is found.
    """
    state_path = os.path.join(workspace_root, "aidlc-docs", "aidlc-state.md")
    if not os.path.isfile(state_path):
        return None

    with open(state_path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        return None

    return _markdown_to_state(content)


def _init_state(user_request: str, project_name: str, workspace_root: str) -> dict:
    """Create a fresh SharedState with project info pre-populated."""
    shared_state = initialize_shared_state(user_request)
    shared_state["project"]["workspace_root"] = workspace_root
    shared_state["project"]["selected_project"] = project_name
    return shared_state


def main(
    user_request: str,
    project_name: str = "default",
    workspace_root: str = ".",
) -> object:
    """Run the AI-SDLC multi-agent workflow.

    Args:
        user_request: The user's initial request describing what to build.
        project_name: Display name for the project.
        workspace_root: Absolute path to the project workspace.

    Returns:
        The result from graph execution.
    """
    model = create_model()
    existing_state = _load_existing_state(workspace_root)

    if existing_state is not None and validate_shared_state(existing_state):
        shared_state = existing_state
        if not shared_state["project"].get("workspace_root"):
            shared_state["project"]["workspace_root"] = workspace_root
        resume_stage = _resume_from_state(shared_state)
        shared_state["current_stage"] = resume_stage
    else:
        shared_state = _init_state(user_request, project_name, workspace_root)

    graph = build_sdlc_graph(shared_state, model=model)
    result = graph(user_request, invocation_state=shared_state)
    persist_state(shared_state)
    return result


async def main_async(
    user_request: str,
    project_name: str = "default",
    workspace_root: str = ".",
) -> None:
    """Run the AI-SDLC workflow with async streaming output."""
    model = create_model()
    existing_state = _load_existing_state(workspace_root)

    if existing_state is not None and validate_shared_state(existing_state):
        shared_state = existing_state
        if not shared_state["project"].get("workspace_root"):
            shared_state["project"]["workspace_root"] = workspace_root
        resume_stage = _resume_from_state(shared_state)
        shared_state["current_stage"] = resume_stage
    else:
        shared_state = _init_state(user_request, project_name, workspace_root)

    graph = build_sdlc_graph(shared_state, model=model)
    async for event in graph.stream_async(user_request, invocation_state=shared_state):
        process_stream_event(event)
    persist_state(shared_state)


def main_sync(
    user_request: str,
    project_name: str = "default",
    workspace_root: str = ".",
) -> object:
    """Run the AI-SDLC workflow with synchronous callback-based streaming."""
    model = create_model()
    existing_state = _load_existing_state(workspace_root)

    if existing_state is not None and validate_shared_state(existing_state):
        shared_state = existing_state
        if not shared_state["project"].get("workspace_root"):
            shared_state["project"]["workspace_root"] = workspace_root
        resume_stage = _resume_from_state(shared_state)
        shared_state["current_stage"] = resume_stage
    else:
        shared_state = _init_state(user_request, project_name, workspace_root)

    graph = build_sdlc_graph(shared_state, model=model)
    graph.callback_handler = create_cli_callback_handler()
    result = graph(user_request, invocation_state=shared_state)
    persist_state(shared_state)
    return result


def main_interactive(
    user_request: str,
    project_name: str = "default",
    workspace_root: str = ".",
    max_retries: int = 5,
) -> object:
    """Run the AI-SDLC workflow in interactive human-in-the-loop mode.

    Includes automatic retry with session resumption when the Bedrock
    connection times out.
    """
    import time

    attempt = 0
    shared_state = None
    result = None

    while attempt <= max_retries:
        model = create_model()

        if shared_state is None:
            existing_state = _load_existing_state(workspace_root)

            if existing_state is not None and validate_shared_state(existing_state):
                shared_state = existing_state
                if not shared_state["project"].get("workspace_root"):
                    shared_state["project"]["workspace_root"] = workspace_root
                resume_stage = _resume_from_state(shared_state)
                shared_state["current_stage"] = resume_stage
                print(f"\n📂 Resuming session from stage: {resume_stage}")
            else:
                shared_state = _init_state(user_request, project_name, workspace_root)
                print("\n🆕 Starting new session")
        else:
            persist_state(shared_state)
            resume_stage = _resume_from_state(shared_state)
            shared_state["current_stage"] = resume_stage
            print(f"\n🔄 Reconnecting and resuming from stage: {resume_stage}")

        try:
            graph = build_sdlc_graph(shared_state, model=model, interactive=True)
            result = graph(user_request, invocation_state=shared_state)
            persist_state(shared_state)
            return result

        except KeyboardInterrupt:
            print("\n\n⏹️  Session interrupted by user.")
            persist_state(shared_state)
            print("💾 State saved. Run again to resume.")
            raise

        except Exception as e:
            error_name = type(e).__name__
            persist_state(shared_state)
            attempt += 1

            if attempt > max_retries:
                print(f"\n❌ Failed after {max_retries} retries: {error_name}: {e}")
                print("💾 State saved. Run again to resume from where you left off.")
                raise

            wait = min(2 ** attempt, 30)
            print(f"\n⚠️  Connection lost ({error_name}). State saved.")
            print(f"   Retrying in {wait}s (attempt {attempt}/{max_retries})...")
            time.sleep(wait)

    return result
