"""Content validation tools for the AI-SDLC Multi-Agent System.

Provides validation for Mermaid diagrams, ASCII diagrams, and markdown content
before writing to files.
"""

import re

from strands.tools import tool

# Characters permitted in ASCII diagrams
_ASCII_PERMITTED = set("+-|^v<> ")

# Known Mermaid diagram types
_MERMAID_TYPES = [
    "flowchart", "graph", "sequenceDiagram", "classDiagram",
    "stateDiagram", "erDiagram", "gantt", "pie", "gitgraph",
    "journey", "mindmap", "timeline",
]

# Characters that need escaping in Mermaid labels
_MERMAID_SPECIAL_CHARS = {
    "#": "#35;",
    "&": "#38;",
    "<": "#60;",
    ">": "#62;",
}


def _escape_mermaid_special_chars(content: str) -> str:
    """Escape special characters in Mermaid diagram text labels."""
    result = content
    for char, escape in _MERMAID_SPECIAL_CHARS.items():
        result = result.replace(char, escape)
    return result


def _generate_text_fallback(content: str) -> str:
    """Generate a text-based fallback description from Mermaid content."""
    lines = content.strip().split("\n")
    fallback_lines = ["Text-based diagram fallback:", ""]
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("```"):
            # Extract node relationships
            if "-->" in stripped or "---" in stripped or "==>" in stripped:
                fallback_lines.append(f"  {stripped}")
            elif any(stripped.startswith(t) for t in _MERMAID_TYPES):
                fallback_lines.append(f"  Diagram type: {stripped}")
            else:
                fallback_lines.append(f"  {stripped}")
    return "\n".join(fallback_lines)


@tool(context=True)
def validate_mermaid(tool_context: dict, content: str) -> dict:
    """Validate Mermaid diagram syntax with special character escaping.

    Checks that the content starts with a valid Mermaid diagram type keyword
    and has balanced brackets. Escapes special characters and generates a
    text-based fallback.

    Args:
        tool_context: Strands tool context with invocation_state.
        content: Mermaid diagram content to validate.

    Returns:
        Dict with keys: valid (bool), errors (list[str]), escaped (str), fallback (str).
    """
    errors = []
    stripped = content.strip()

    if not stripped:
        errors.append("Empty Mermaid content")
        return {"valid": False, "errors": errors, "escaped": "", "fallback": ""}

    # Check for valid diagram type
    # Skip %%{init:...}%% directives when looking for the diagram type keyword
    first_line = stripped.split("\n")[0].strip()
    type_line = first_line
    if first_line.startswith("%%{"):
        # Init directive — find the actual diagram type on a subsequent line
        for line in stripped.split("\n")[1:]:
            line = line.strip()
            if line and not line.startswith("%%"):
                type_line = line
                break
    has_valid_type = any(type_line.startswith(t) for t in _MERMAID_TYPES)
    if not has_valid_type:
        errors.append(f"Unknown Mermaid diagram type: '{type_line.split()[0] if type_line else ''}'")

    # Check balanced brackets
    for open_ch, close_ch in [("(", ")"), ("[", "]"), ("{", "}")]:
        if stripped.count(open_ch) != stripped.count(close_ch):
            errors.append(f"Unbalanced '{open_ch}{close_ch}' brackets")

    escaped = _escape_mermaid_special_chars(stripped)
    fallback = _generate_text_fallback(stripped)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "escaped": escaped,
        "fallback": fallback,
    }


@tool(context=True)
def validate_markdown(tool_context: dict, content: str) -> dict:
    """Validate markdown syntax correctness.

    Checks for unclosed code blocks, valid heading structure, and
    basic syntax issues.

    Args:
        tool_context: Strands tool context with invocation_state.
        content: Markdown content to validate.

    Returns:
        Dict with keys: valid (bool), errors (list[str]).
    """
    errors = []

    if not content.strip():
        errors.append("Empty markdown content")
        return {"valid": False, "errors": errors}

    # Check for unclosed code blocks (``` must appear in pairs)
    fence_count = content.count("```")
    if fence_count % 2 != 0:
        errors.append("Unclosed code block (odd number of ``` fences)")

    # Check heading structure (# must be followed by a space)
    for i, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        if re.match(r"^#{1,6}[^ #]", stripped):
            errors.append(f"Line {i}: Heading missing space after '#'")

    return {"valid": len(errors) == 0, "errors": errors}


@tool(context=True)
def validate_ascii_diagram(tool_context: dict, content: str) -> dict:
    """Validate ASCII diagram line widths and permitted characters.

    Checks that all lines use only permitted characters (+, -, |, ^, v, <, >, spaces)
    and flags lines with inconsistent widths.

    Args:
        tool_context: Strands tool context with invocation_state.
        content: ASCII diagram content to validate.

    Returns:
        Dict with keys: valid (bool), errors (list[str]).
    """
    errors = []

    if not content.strip():
        errors.append("Empty ASCII diagram content")
        return {"valid": False, "errors": errors}

    lines = content.split("\n")
    # Filter out empty lines for width comparison
    non_empty_lines = [(i + 1, line) for i, line in enumerate(lines) if line.strip()]

    if not non_empty_lines:
        errors.append("ASCII diagram has no non-empty lines")
        return {"valid": False, "errors": errors}

    # Check permitted characters
    for line_num, line in non_empty_lines:
        for ch in line:
            if ch not in _ASCII_PERMITTED:
                errors.append(
                    f"Line {line_num}: Invalid character '{ch}' "
                    f"(only +, -, |, ^, v, <, >, spaces allowed)"
                )
                break  # One error per line is enough

    # Check consistent line widths
    widths = [len(line) for _, line in non_empty_lines]
    if widths and len(set(widths)) > 1:
        errors.append(
            f"Inconsistent line widths: {sorted(set(widths))}"
        )

    return {"valid": len(errors) == 0, "errors": errors}
