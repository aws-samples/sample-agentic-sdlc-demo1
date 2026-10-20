"""Workspace Detection Agent for the AI-SDLC Multi-Agent System.

This agent scans the workspace for source code files, build files, and project
structure indicators. It sets the brownfield flag, checks for existing state
files for session resumption, and creates an initial state file if none exists.

This agent proceeds automatically without an approval gate.
"""

import os

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.state import _state_to_markdown

# Source code file extensions to detect
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
    ".cpp", ".c", ".h", ".hpp", ".rb", ".php", ".cs", ".swift",
    ".kt", ".scala", ".clj", ".ex", ".exs", ".hs", ".ml",
}

# Build / project configuration files
BUILD_FILES = {
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "Makefile", "CMakeLists.txt", "Cargo.toml", "pyproject.toml",
    "setup.py", "setup.cfg", "requirements.txt", "Gemfile",
    "composer.json", "go.mod", "build.sbt", "mix.exs",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
}

# Directories to skip during scanning
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "env", ".env", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", "target", ".idea", ".vscode", "aidlc-docs",
}

# Map of build file -> build system name
BUILD_SYSTEM_MAP = {
    "package.json": "npm",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "Makefile": "make",
    "CMakeLists.txt": "cmake",
    "Cargo.toml": "cargo",
    "pyproject.toml": "pyproject",
    "setup.py": "setuptools",
    "requirements.txt": "pip",
    "Gemfile": "bundler",
    "composer.json": "composer",
    "go.mod": "go",
    "build.sbt": "sbt",
    "mix.exs": "mix",
}

# Map of extension -> language name
LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".jsx": "JavaScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C",
    ".hpp": "C++",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".clj": "Clojure",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".hs": "Haskell",
    ".ml": "OCaml",
}


@tool(context=True)
def scan_workspace(tool_context: dict) -> dict:
    """Scan the workspace for source code files, build files, and project structure.

    In AgentCore mode (``_mcp_client`` present in shared state), files live
    in S3 via the Gateway.  A Gateway ``list_files`` call is used instead of
    a local ``os.walk``.  If no files are found the project is greenfield.

    In local mode, walks the local filesystem at ``workspace_root``.

    Args:
        tool_context: Strands tool context with invocation_state.

    Returns:
        A dict with keys: brownfield, languages, build_system, project_structure,
        source_file_count, workspace_root.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    workspace_root = state.get("project", {}).get("workspace_root", ".")

    # AgentCore mode: files are in S3 via Gateway
    return _scan_via_gateway(state, state.get("_mcp_client"), workspace_root)

def _scan_via_gateway(state, mcp_client, workspace_root):
    """Scan the S3 workspace via the MCP Gateway list_files tool."""
    from tools.gateway import call_tool

    detected_languages = set()
    detected_build_files = set()
    source_file_count = 0

    try:
        result = call_tool(mcp_client, "list_files", {"prefix": workspace_root})
        # Extract file list from the MCP result
        content = result.get("content", [])
        files_text = ""
        for item in content:
            if isinstance(item, dict):
                files_text += item.get("text", "")
            elif hasattr(item, "text"):
                files_text += item.text

        for line in files_text.splitlines():
            fname = line.strip().rsplit("/", 1)[-1] if "/" in line.strip() else line.strip()
            if not fname:
                continue
            _, ext = os.path.splitext(fname)
            if ext in SOURCE_EXTENSIONS:
                source_file_count += 1
                lang = LANGUAGE_MAP.get(ext)
                if lang:
                    detected_languages.add(lang)
            if fname in BUILD_FILES:
                detected_build_files.add(fname)
    except Exception:
        # If listing fails, treat as greenfield
        pass

    build_system = ""
    for bf in detected_build_files:
        if bf in BUILD_SYSTEM_MAP:
            build_system = BUILD_SYSTEM_MAP[bf]
            break

    brownfield = source_file_count > 0
    languages = sorted(detected_languages)

    if source_file_count == 0:
        project_structure = "empty"
    elif source_file_count > 20:
        project_structure = "monolith"
    else:
        project_structure = "library"

    state["brownfield"] = brownfield
    if not brownfield:
        state.get("stage_progress", {})["reverse_engineering"] = "skipped"
    project = state.get("project", {})
    project["type"] = "brownfield" if brownfield else "greenfield"
    project["languages"] = languages
    project["build_system"] = build_system
    project["project_structure"] = project_structure

    return {
        "brownfield": brownfield,
        "languages": languages,
        "build_system": build_system,
        "project_structure": project_structure,
        "source_file_count": source_file_count,
        "workspace_root": workspace_root,
    }


@tool(context=True)
def check_aidlc_state(tool_context: dict) -> dict:
    """Check for an existing aidlc-state.md file.

    In AgentCore mode, checks S3 via the Gateway.
    In local mode, checks the local filesystem.

    Args:
        tool_context: Strands tool context with invocation_state.

    Returns:
        A dict with 'exists' (bool) and optionally 'path' (str).
    """
    from tools._compat import get_state
    state = get_state(tool_context)

    # AgentCore mode: state is managed by main_ws.py via S3
    # The state was already loaded in _load_or_init_state
    session_id = state.get("_session_id", "")
    stage_progress = state.get("stage_progress", {})
    has_progress = any(
        v != "not_started" for v in stage_progress.values()
    )
    if has_progress:
        return {"exists": True, "path": f"s3://{session_id}/aidlc-state.md"}
    return {"exists": False}


@tool(context=True)
def create_state_file(tool_context: dict) -> str:
    """Create the initial aidlc-state.md with project information.

    In AgentCore mode, persists to S3 via the Gateway.
    In local mode, writes to the local filesystem.

    Args:
        tool_context: Strands tool context with invocation_state.

    Returns:
        Confirmation message with the file path created.
    """
    from tools._compat import get_state
    state = get_state(tool_context)

    from tools.state import mark_stage_complete
    mark_stage_complete(state, "workspace_detection")

    md_content = _state_to_markdown(state)

    # Persist via Gateway to S3
    from tools.state import _persist_via_gateway
    session_id = state.get("_session_id", "")
    _persist_via_gateway(state["_mcp_client"], session_id, md_content)
    return f"State file persisted to S3 under {session_id}"


SYSTEM_PROMPT = """\
You are the Workspace Detection Agent for the AI-SDLC multi-agent system.
You run immediately after Project Selection and your job is to analyze the workspace.

IMPORTANT: The workspace path has ALREADY been configured in shared state
(`project.workspace_root`). You MUST use that path — do NOT ask the user for a
directory path. Do NOT use `human_input` to ask for a workspace path.

Your responsibilities:
1. Use `scan_workspace` to detect source code files, build files, and project structure
   at the workspace_root path already stored in shared state.
   This determines whether the project is greenfield (no existing code) or brownfield (existing code).
2. Use `check_aidlc_state` to check if an existing state file exists.
   If it exists, session resumption is possible — the orchestrator will load the persisted state.
3. If no state file exists, use `create_state_file` to create the initial state file with
   the project information gathered from workspace scanning.

IMPORTANT: You proceed automatically without requiring user approval. Do NOT present an
approval gate. Do NOT ask the user any questions. Simply execute your tools, update the
shared state, and return.

After completing your work, present the results using the [STAGE_SUMMARY] tag.
The frontend uses this tag to render a styled summary card.

Format your completion output EXACTLY like this:

[STAGE_SUMMARY]
title: Workspace Detection Complete ✅
| Property | Value |
|---|---|
| **Project Name** | {project name from shared state} |
| **Workspace Root** | {workspace_root value} |
| **Project Type** | {Greenfield or Brownfield} |
| **Languages Detected** | {languages or None} |
| **Build System** | {build system or None} |
| **Source Files** | {count} |
| **State File** | {Created or Already exists} |
[/STAGE_SUMMARY]

The workflow will now proceed to Requirements Analysis.

RULES for [STAGE_SUMMARY]:
- The first line after [STAGE_SUMMARY] MUST be "title: {your title}"
- The rest is a markdown table with Property and Value columns
- Keep the table clean — no extra text inside the tags
- After [/STAGE_SUMMARY], add ONE brief sentence about what happens next

Execute all three tools in sequence: scan_workspace, check_aidlc_state, then
create_state_file (only if no state file exists).
"""

TOOLS = [scan_workspace, check_aidlc_state, create_state_file]


def create_workspace_detection_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Workspace Detection Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for workspace detection.
    """
    return create_stage_agent(
        stage_name="workspace_detection",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
