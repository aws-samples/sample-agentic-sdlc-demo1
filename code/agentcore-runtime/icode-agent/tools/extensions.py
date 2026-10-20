"""Extension loading and compliance evaluation tools for the AI-SDLC Multi-Agent System.

Provides tools to scan an extensions directory for .md rule files, parse them
into ExtensionRule schema, and evaluate compliance against stage artifacts.

In AgentCore mode, extensions are loaded from S3 via the Gateway.
"""

import posixpath
import re

from strands.tools import tool


def _parse_extension_content(name: str, content: str, file_path: str) -> dict:
    """Parse extension content into an ExtensionRule dict.

    Args:
        name: Extension name (filename without extension).
        content: The markdown content of the extension file.
        file_path: The relative path to the extension file.

    Returns:
        ExtensionRule dict.
    """
    # Extract applicable stages
    applicable_stages = []
    match = re.search(r"[Aa]pplicable [Ss]tages?:\s*(.+)", content)
    if match:
        applicable_stages = [s.strip() for s in match.group(1).split(",") if s.strip()]

    # Check for applicability question
    has_question = bool(re.search(r"\?", content))

    return {
        "name": name,
        "file_path": file_path,
        "content": content,
        "applicable_stages": applicable_stages,
        "has_applicability_question": has_question,
        "enabled": True,
    }


@tool(context=True)
def load_extensions(tool_context: dict, extensions_dir: str) -> list[dict]:
    """Scan extensions directory for .md files via Gateway S3 and parse into ExtensionRule schema.

    Args:
        tool_context: Strands tool context with invocation_state.
        extensions_dir: Relative path to the extensions directory.

    Returns:
        List of ExtensionRule dicts.
    """
    from tools._compat import get_state
    from tools.file_ops import list_files, read_file

    state = get_state(tool_context)

    try:
        all_files = list_files(state, extensions_dir)
    except Exception:
        return []

    rules = []
    for rel_path in sorted(all_files):
        filename = posixpath.basename(rel_path)
        if not filename.endswith(".md"):
            continue
        name = filename[:-3]  # strip .md
        try:
            content = read_file(state, rel_path)
            rule = _parse_extension_content(name, content, rel_path)
            rules.append(rule)
        except Exception:
            continue

    # Store in SharedState if available
    if "extensions" in state:
        state["extensions"]["loaded_rules"] = rules

    return rules


@tool(context=True)
def evaluate_extension_compliance(
    tool_context: dict, stage: str, artifacts: list[str]
) -> dict:
    """Evaluate extension rule compliance for a stage's artifacts.

    Checks each loaded extension's enabled/disabled status and applicability
    to the current stage. Produces a summary with compliant/non-compliant/N/A
    status per extension.

    Args:
        tool_context: Strands tool context with invocation_state.
        stage: The current stage name.
        artifacts: List of artifact file paths produced by the stage.

    Returns:
        Dict with 'results' (list of per-extension evaluations) and
        'all_compliant' (bool).
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    extensions = state.get("extensions", {})
    loaded_rules = extensions.get("loaded_rules", [])
    enabled_status = extensions.get("enabled_status", {})

    results = []
    all_compliant = True

    for rule in loaded_rules:
        ext_name = rule["name"]

        # Check if extension is disabled
        is_enabled = enabled_status.get(ext_name, rule.get("enabled", True))
        if not is_enabled:
            results.append({
                "extension": ext_name,
                "status": "skipped",
                "reason": "Extension is disabled",
            })
            continue

        # Check if extension applies to this stage
        applicable = rule.get("applicable_stages", [])
        if applicable and stage not in applicable:
            results.append({
                "extension": ext_name,
                "status": "N/A",
                "reason": f"Not applicable to stage '{stage}'",
            })
            continue

        # Extension is enabled and applicable — evaluate compliance
        # Basic compliance check: verify artifacts exist
        if artifacts:
            results.append({
                "extension": ext_name,
                "status": "compliant",
                "reason": f"Stage produced {len(artifacts)} artifacts",
            })
        else:
            results.append({
                "extension": ext_name,
                "status": "non-compliant",
                "reason": "No artifacts produced for compliance check",
            })
            all_compliant = False

    return {"results": results, "all_compliant": all_compliant}
