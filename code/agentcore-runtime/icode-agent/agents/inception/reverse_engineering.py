"""Reverse Engineering Agent for the AI-SDLC Multi-Agent System.

This agent analyzes existing codebases (brownfield projects) and generates
comprehensive documentation artifacts. It only executes when brownfield=true
AND no reverse engineering artifacts exist yet.

Produces 8 documentation files in aidlc-docs/inception/reverse-engineering/:
- business-overview.md
- architecture.md
- code-structure.md
- api-documentation.md
- component-inventory.md
- technology-stack.md
- dependencies.md
- code-quality-assessment.md

Has an approval gate at completion with summary presentation.
"""

import os
import posixpath

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.file_io import write_artifact

# The 8 reverse engineering artifact definitions
RE_ARTIFACTS = {
    "business_overview": "business-overview.md",
    "architecture": "architecture.md",
    "code_structure": "code-structure.md",
    "api_documentation": "api-documentation.md",
    "component_inventory": "component-inventory.md",
    "technology_stack": "technology-stack.md",
    "dependencies": "dependencies.md",
    "code_quality_assessment": "code-quality-assessment.md",
}

RE_OUTPUT_DIR = "aidlc-docs/inception/reverse-engineering"

# Extensions to scan for source code analysis
_SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
    ".cpp", ".c", ".h", ".hpp", ".rb", ".php", ".cs", ".swift",
    ".kt", ".scala", ".clj", ".ex", ".exs", ".hs", ".ml",
}

_BUILD_FILES = {
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "Makefile", "CMakeLists.txt", "Cargo.toml", "pyproject.toml",
    "setup.py", "setup.cfg", "requirements.txt", "Gemfile",
    "composer.json", "go.mod", "build.sbt", "mix.exs",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
}

_CONFIG_FILES = {
    ".eslintrc", ".eslintrc.js", ".eslintrc.json", "tsconfig.json",
    ".prettierrc", ".babelrc", "webpack.config.js", "vite.config.ts",
    "jest.config.js", "pytest.ini", "tox.ini", ".flake8", "mypy.ini",
    ".editorconfig", ".gitignore", ".dockerignore",
}

_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "env", ".env", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", "target", ".idea", ".vscode", "aidlc-docs",
}

_LANGUAGE_MAP = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".jsx": "JavaScript", ".tsx": "TypeScript", ".java": "Java",
    ".go": "Go", ".rs": "Rust", ".cpp": "C++", ".c": "C",
    ".h": "C", ".hpp": "C++", ".rb": "Ruby", ".php": "PHP",
    ".cs": "C#", ".swift": "Swift", ".kt": "Kotlin",
    ".scala": "Scala", ".clj": "Clojure", ".ex": "Elixir",
    ".exs": "Elixir", ".hs": "Haskell", ".ml": "OCaml",
}

_FRAMEWORK_INDICATORS = {
    "package.json": ["react", "angular", "vue", "express", "next", "nest"],
    "requirements.txt": ["django", "flask", "fastapi", "tornado", "celery"],
    "pyproject.toml": ["django", "flask", "fastapi", "poetry"],
    "Gemfile": ["rails", "sinatra", "hanami"],
    "pom.xml": ["spring", "quarkus", "micronaut"],
    "build.gradle": ["spring", "android"],
    "Cargo.toml": ["actix", "rocket", "tokio", "axum"],
    "go.mod": ["gin", "echo", "fiber"],
    "composer.json": ["laravel", "symfony", "slim"],
}


def _should_execute(state: dict) -> bool:
    """Check if the Reverse Engineering Agent should execute.

    Only runs when brownfield=true AND no RE artifacts exist yet.

    Args:
        state: The SharedState dictionary.

    Returns:
        True if the agent should execute, False otherwise.
    """
    if not state.get("brownfield", False):
        return False

    artifacts = state.get("artifacts", {}).get("reverse_engineering", {})
    for key in RE_ARTIFACTS:
        if artifacts.get(key, ""):
            return False

    return True


def _detect_frameworks(state: dict) -> list[str]:
    """Detect frameworks by reading build/config file contents from S3."""
    from tools.file_ops import file_exists, read_file

    frameworks = []
    for build_file, indicators in _FRAMEWORK_INDICATORS.items():
        try:
            if file_exists(state, build_file):
                content = read_file(state, build_file).lower()
                for indicator in indicators:
                    if indicator in content:
                        frameworks.append(indicator)
        except Exception:
            continue
    return sorted(set(frameworks))


@tool(context=True)
def analyze_codebase(tool_context: dict) -> dict:
    """Scan the workspace and return a comprehensive codebase analysis.

    Reads workspace_root from SharedState, scans for source files, analyzes
    structure, identifies patterns, languages, frameworks, and dependencies.

    Args:
        tool_context: Strands tool context with invocation_state.

    Returns:
        A dict with analysis results including file_count, languages,
        frameworks, patterns, directory_structure, build_files, config_files,
        and source_files_by_language.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    workspace_root = state.get("project", {}).get("workspace_root", ".")

    if not _should_execute(state):
        return {
            "skipped": True,
            "reason": "brownfield=false or RE artifacts already exist",
        }

    # AgentCore mode: scan via Gateway list_files
    from tools.file_ops import list_files, read_file

    languages = {}
    source_files = []
    build_files_found = []
    config_files_found = []
    dir_structure = []
    file_count = 0

    try:
        all_files = list_files(state, "")
    except Exception:
        all_files = []

    for key in all_files:
        # key is relative to session prefix, e.g. "src/main.py"
        fname = key.rsplit("/", 1)[-1] if "/" in key else key
        if not fname:
            continue

        # Track directory structure
        if "/" in key:
            dir_part = key.rsplit("/", 1)[0]
            parts = dir_part.split("/")
            # Skip known noise directories
            if any(p in _SKIP_DIRS for p in parts):
                continue
            if dir_part not in dir_structure:
                dir_structure.append(dir_part)

        _, ext = os.path.splitext(fname)
        if ext in _SOURCE_EXTENSIONS:
            file_count += 1
            lang = _LANGUAGE_MAP.get(ext, "Unknown")
            languages.setdefault(lang, 0)
            languages[lang] += 1
            source_files.append(key)

        if fname in _BUILD_FILES:
            build_files_found.append(key)

        if fname in _CONFIG_FILES:
            config_files_found.append(key)

    frameworks = _detect_frameworks(state)

    # Identify patterns from directory structure
    patterns = []
    dir_names_lower = [d.rsplit("/", 1)[-1].lower() for d in dir_structure]
    if any(d in dir_names_lower for d in ["src", "lib", "pkg"]):
        patterns.append("standard-layout")
    if any(d in dir_names_lower for d in ["test", "tests", "spec", "specs"]):
        patterns.append("has-tests")
    if any(d in dir_names_lower for d in ["api", "routes", "controllers"]):
        patterns.append("api-layer")
    if any(d in dir_names_lower for d in ["models", "entities", "domain"]):
        patterns.append("domain-layer")
    if any(d in dir_names_lower for d in ["services", "service"]):
        patterns.append("service-layer")
    if any(d in dir_names_lower for d in ["components", "views", "pages"]):
        patterns.append("ui-layer")
    if any(d in dir_names_lower for d in ["utils", "helpers", "common"]):
        patterns.append("utility-layer")
    if any(d in dir_names_lower for d in ["config", "conf", "settings"]):
        patterns.append("config-layer")

    return {
        "skipped": False,
        "file_count": file_count,
        "total_lines": 0,  # Line counting not available via S3
        "languages": languages,
        "frameworks": frameworks,
        "patterns": patterns,
        "directory_structure": sorted(dir_structure),
        "build_files": sorted(build_files_found),
        "config_files": sorted(config_files_found),
        "source_files": sorted(source_files),
    }


@tool(context=True)
def generate_re_artifacts(tool_context: dict, analysis: dict) -> dict:
    """Generate all 8 reverse engineering documentation artifacts.

    Takes the analysis results from analyze_codebase and generates markdown
    documentation files in aidlc-docs/inception/reverse-engineering/.
    Updates SharedState artifacts.reverse_engineering with file paths.

    Args:
        tool_context: Strands tool context with invocation_state.
        analysis: The analysis dict returned by analyze_codebase.

    Returns:
        A dict mapping artifact keys to their file paths.
    """
    from tools._compat import get_state
    state = get_state(tool_context)

    if not _should_execute(state):
        return {"skipped": True, "reason": "Conditions not met for RE execution"}

    if analysis.get("skipped", False):
        return {"skipped": True, "reason": "Analysis was skipped"}

    languages = analysis.get("languages", {})
    frameworks = analysis.get("frameworks", [])
    patterns = analysis.get("patterns", [])
    file_count = analysis.get("file_count", 0)
    total_lines = analysis.get("total_lines", 0)
    dir_structure = analysis.get("directory_structure", [])
    build_files = analysis.get("build_files", [])
    config_files = analysis.get("config_files", [])
    source_files = analysis.get("source_files", [])

    lang_summary = ", ".join(
        f"{lang} ({count} files)" for lang, count in sorted(languages.items())
    )
    framework_summary = ", ".join(frameworks) if frameworks else "None detected"
    pattern_summary = ", ".join(patterns) if patterns else "None detected"

    artifact_contents = _build_artifact_contents(
        lang_summary=lang_summary,
        framework_summary=framework_summary,
        pattern_summary=pattern_summary,
        file_count=file_count,
        total_lines=total_lines,
        languages=languages,
        frameworks=frameworks,
        patterns=patterns,
        dir_structure=dir_structure,
        build_files=build_files,
        config_files=config_files,
        source_files=source_files,
    )

    generated_paths = {}
    re_artifacts = state.get("artifacts", {}).get("reverse_engineering", {})

    for key, filename in RE_ARTIFACTS.items():
        rel_path = posixpath.join(RE_OUTPUT_DIR, filename)
        content = artifact_contents.get(key, f"# {filename}\n\nNo content generated.\n")

        from tools.file_ops import write_file
        write_file(state, rel_path, content)

        generated_paths[key] = rel_path
        re_artifacts[key] = rel_path

    # Update stage progress
    from tools.state import mark_stage_complete
    mark_stage_complete(state, "reverse_engineering")
    state["current_stage"] = "reverse_engineering"

    return generated_paths


def _build_artifact_contents(
    lang_summary, framework_summary, pattern_summary,
    file_count, total_lines, languages, frameworks, patterns,
    dir_structure, build_files, config_files, source_files,
) -> dict:
    """Build the markdown content for each of the 8 RE artifacts."""
    dir_tree = "\n".join(f"  - {d}" for d in dir_structure[:50]) if dir_structure else "  (empty)"
    build_list = "\n".join(f"  - {f}" for f in build_files) if build_files else "  None detected"
    config_list = "\n".join(f"  - {f}" for f in config_files) if config_files else "  None detected"
    source_sample = "\n".join(f"  - {f}" for f in source_files[:30]) if source_files else "  None found"

    contents = {}

    contents["business_overview"] = (
        f"# Business Overview\n\n"
        f"## Project Summary\n\n"
        f"This is a brownfield project with {file_count} source files "
        f"totaling approximately {total_lines} lines of code.\n\n"
        f"## Languages\n\n{lang_summary}\n\n"
        f"## Frameworks\n\n{framework_summary}\n\n"
        f"## Identified Patterns\n\n{pattern_summary}\n\n"
        f"## Notes\n\n"
        f"This document was auto-generated by the Reverse Engineering Agent. "
        f"Review and refine as needed.\n"
    )

    contents["architecture"] = (
        f"# Architecture\n\n"
        f"## Overview\n\n"
        f"The codebase contains {file_count} source files across "
        f"{len(languages)} language(s).\n\n"
        f"## Architectural Patterns\n\n{pattern_summary}\n\n"
        f"## Directory Structure\n\n{dir_tree}\n\n"
        f"## Frameworks\n\n{framework_summary}\n\n"
        f"## Notes\n\n"
        f"Architecture details should be refined with deeper analysis.\n"
    )

    contents["code_structure"] = (
        f"# Code Structure\n\n"
        f"## File Statistics\n\n"
        f"- **Total source files**: {file_count}\n"
        f"- **Total lines of code**: {total_lines}\n"
        f"- **Languages**: {lang_summary}\n\n"
        f"## Directory Layout\n\n{dir_tree}\n\n"
        f"## Source Files (sample)\n\n{source_sample}\n"
    )

    contents["api_documentation"] = (
        "# API Documentation\n\n"
        "## Detected API Patterns\n\n"
        + ("API layer detected in directory structure.\n\n"
           if "api-layer" in patterns
           else "No explicit API layer detected in directory structure.\n\n")
        + f"## Frameworks\n\n{framework_summary}\n\n"
        "## Notes\n\n"
        "API endpoints and contracts should be documented through deeper code analysis.\n"
    )

    contents["component_inventory"] = (
        "# Component Inventory\n\n"
        "## Detected Layers\n\n"
        + "\n".join(f"- {p}" for p in patterns if p.endswith("-layer"))
        + ("\n\n" if any(p.endswith("-layer") for p in patterns) else "No layers detected.\n\n")
        + "## Source File Count by Language\n\n"
        + "\n".join(f"- **{lang}**: {count} files" for lang, count in sorted(languages.items()))
        + "\n\n"
        "## Notes\n\n"
        "Individual components should be catalogued through deeper analysis.\n"
    )

    lang_list = "\n".join(f"- {lang}" for lang in sorted(languages.keys())) if languages else "- None detected"
    fw_list = "\n".join(f"- {fw}" for fw in frameworks) if frameworks else "- None detected"

    contents["technology_stack"] = (
        f"# Technology Stack\n\n"
        f"## Languages\n\n{lang_list}\n\n"
        f"## Frameworks\n\n{fw_list}\n\n"
        f"## Build Files\n\n{build_list}\n\n"
        f"## Configuration Files\n\n{config_list}\n\n"
        f"## Notes\n\n"
        f"Version details should be extracted from build/config files.\n"
    )

    contents["dependencies"] = (
        f"# Dependencies\n\n"
        f"## Build Files\n\n{build_list}\n\n"
        f"## Frameworks Detected\n\n{framework_summary}\n\n"
        f"## Notes\n\n"
        f"Dependency versions and transitive dependencies should be extracted "
        f"from build files for a complete inventory.\n"
    )

    has_tests = "has-tests" in patterns
    contents["code_quality_assessment"] = (
        f"# Code Quality Assessment\n\n"
        f"## Metrics\n\n"
        f"- **Source files**: {file_count}\n"
        f"- **Lines of code**: {total_lines}\n"
        f"- **Languages**: {len(languages)}\n"
        f"- **Test coverage**: {'Tests directory detected' if has_tests else 'No tests directory detected'}\n\n"
        f"## Configuration Files\n\n{config_list}\n\n"
        f"## Observations\n\n"
        + ("- Linting/formatting configuration detected\n" if config_files else "")
        + ("- Test infrastructure present\n" if has_tests else "- No test infrastructure detected\n")
        + "\n## Notes\n\n"
        "A deeper static analysis is recommended for comprehensive quality assessment.\n"
    )

    return contents


SYSTEM_PROMPT = """\
You are the Reverse Engineering Agent for the AI-SDLC multi-agent system.
You analyze existing codebases (brownfield projects) and generate comprehensive
documentation artifacts.

CONDITIONAL EXECUTION:
- You ONLY execute when brownfield=true AND no reverse engineering artifacts exist yet.
- If brownfield=false or artifacts already exist, report that you are skipping and return.

Your responsibilities:
1. Use `analyze_codebase` to scan the workspace and gather comprehensive analysis
   of the existing codebase including languages, frameworks, patterns, and structure.
2. Use `generate_re_artifacts` with the analysis results to generate all 8 documentation
   files in aidlc-docs/inception/reverse-engineering/:
   - business-overview.md
   - architecture.md
   - code-structure.md
   - api-documentation.md
   - component-inventory.md
   - technology-stack.md
   - dependencies.md
   - code-quality-assessment.md
3. After generating all artifacts, use `present_approval_gate` to present a completion
   summary to the user and wait for approval.
4. If the user requests changes, incorporate feedback and regenerate affected artifacts,
   then re-present the approval gate.
5. If the user approves, use `process_approval_response` to finalize and proceed.

IMPORTANT:
- Always check the conditional execution result from analyze_codebase first.
- If skipped, set stage_progress.reverse_engineering to "skipped" and return immediately.
- Use write_artifact for any additional file writes needed.
- Set stage_progress.reverse_engineering to "completed" after successful execution.

Execute tools in this order:
1. analyze_codebase
2. generate_re_artifacts (with analysis results)
3. present_approval_gate (with summary of generated artifacts)
4. process_approval_response (after user responds)
"""

TOOLS = [
    analyze_codebase,
    generate_re_artifacts,
    write_artifact,
    present_approval_gate,
    process_approval_response,
]


def create_reverse_engineering_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Reverse Engineering Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for reverse engineering analysis.
    """
    return create_stage_agent(
        stage_name="reverse_engineering",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
