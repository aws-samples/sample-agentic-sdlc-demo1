"""Questions and answers tools for the AI-SDLC Multi-Agent System.

Provides tools for creating structured question files with [Answer]: tags,
reading and parsing answers, and detecting ambiguities in responses.
"""

import posixpath
import re

from strands.tools import tool

# Words/phrases that indicate vague or ambiguous answers
_VAGUE_INDICATORS = [
    "maybe", "perhaps", "not sure", "depends", "it depends",
    "i think", "possibly", "probably", "don't know", "uncertain",
    "tbd", "to be determined", "n/a", "unclear",
]


def _format_question(index: int, question: dict, total: int = 0) -> str:
    """Format a single question with numbered options and [Answer]: tag.

    Every question MUST have at least one option. If the caller provides
    no options, a default set of generic options is used so the user always
    has numbered choices to pick from.

    Args:
        index: 1-based question number.
        question: Dict with 'text' and optional 'options' list.
        total: Total number of questions (used in the header).

    Returns:
        Formatted markdown string for the question.
    """
    if total > 0:
        header = f"## Question {index} of {total}"
    else:
        header = f"## Question {index}"
    lines = [header, question.get("text", "")]

    options = question.get("options", [])
    if not options:
        # Fallback: if the agent didn't provide options, create a minimal set
        # so the user always sees numbered choices.
        options = ["Yes", "No", "Other — please describe"]

    for i, opt in enumerate(options, 1):
        lines.append(f"{i}. {opt}")

    # Only add "Other" if the last option isn't already an "Other" variant
    last_opt_lower = options[-1].lower().strip() if options else ""
    if not last_opt_lower.startswith("other"):
        lines.append("X) Other — please describe")

    lines.append("[Answer]: ")
    return "\n".join(lines)


def _parse_answers(content: str) -> list[dict]:
    """Parse answers from a question file's content.

    Args:
        content: Full text content of the question file.

    Returns:
        List of dicts with 'question_number', 'answer', and 'answered' keys.
    """
    answers = []
    # Split by question sections (handles both "Question N" and "Question N of M")
    sections = re.split(r"## Question (\d+)(?:\s+of\s+\d+)?", content)

    # sections[0] is before first question, then alternating: number, body
    i = 1
    while i < len(sections) - 1:
        q_num = int(sections[i])
        body = sections[i + 1]

        # Find [Answer]: tag — grab everything on the same line after the colon
        answer_match = re.search(r"\[Answer\]:\s*(.*)", body)
        answer_text = ""
        if answer_match:
            answer_text = answer_match.group(1).strip()

        answers.append({
            "question_number": q_num,
            "answer": answer_text,
            "answered": bool(answer_text),
        })
        i += 2

    return answers


@tool(context=True)
def create_questions_file(tool_context: dict, path: str, questions: list[dict]) -> str:
    """Create a markdown question file with [Answer]: tags via Gateway S3.

    Each question can have optional multiple-choice options (A, B, C, D, etc.)
    with an X) Other option for custom responses.

    Args:
        tool_context: Strands tool context with invocation_state.
        path: File path to write the questions file (relative).
        questions: List of dicts, each with 'text' and optional 'options' list.

    Returns:
        Confirmation message with the file path and question count.
    """
    # Derive stage name from path
    stage_name = posixpath.basename(posixpath.dirname(path)).replace("-", " ").title()
    if not stage_name:
        stage_name = "Questions"

    parts = [f"# {stage_name} Questions"]
    total = len(questions)
    for i, q in enumerate(questions, 1):
        parts.append("")  # blank line before each question header
        parts.append(_format_question(i, q, total=total))

    content = "\n".join(parts) + "\n"

    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)
    write_file(state, path, content)

    return f"Created questions file at {path} with {len(questions)} questions"


@tool(context=True)
def read_answers(tool_context: dict, path: str) -> list[dict]:
    """Read and parse answers from a question file via Gateway S3.

    Checks that all [Answer]: tags are filled before allowing progression.

    Args:
        tool_context: Strands tool context with invocation_state.
        path: Path to the question file (relative).

    Returns:
        List of dicts with question_number, answer, and answered status.

    Raises:
        FileNotFoundError: If the question file does not exist.
    """
    from tools._compat import get_state
    from tools.file_ops import read_file

    state = get_state(tool_context)
    content = read_file(state, path)

    return _parse_answers(content)


def _write_answer_to_file(state: dict, path: str, question_number: int, answer: str) -> None:
    """Write an answer into the [Answer]: tag for a specific question in S3.

    Reads the file from S3, finds the [Answer]: line for the given question
    number, replaces it with the user's response, and writes back.

    Args:
        state: SharedState dict (must contain ``_mcp_client``, ``_session_id``).
        path: Relative path to the question file.
        question_number: 1-based question number to update.
        answer: The user's answer text to insert.
    """
    from tools.file_ops import read_file, write_file

    content = read_file(state, path)

    lines = content.split("\n")
    in_target_question = False
    answer_replaced = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped == f"## Question {question_number}" or stripped.startswith(f"## Question {question_number} of "):
            in_target_question = True
            continue

        if in_target_question and re.match(r"## Question \d+", stripped):
            break

        if in_target_question and not answer_replaced and line.startswith("[Answer]:"):
            lines[idx] = f"[Answer]: {answer}"
            answer_replaced = True
            break

    write_file(state, path, "\n".join(lines))


@tool(context=True)
def record_answer(tool_context: dict, path: str, question_number: int, answer: str) -> str:
    """Record a user's answer into the question file at the [Answer]: tag via Gateway S3.

    Call this after collecting an answer from the user (e.g. via ask_human)
    to persist the response into the question markdown file.

    Args:
        tool_context: Strands tool context with invocation_state.
        path: Path to the question file (relative).
        question_number: 1-based question number to update.
        answer: The user's answer text.

    Returns:
        Confirmation message.
    """
    from tools._compat import get_state
    from tools.file_ops import file_exists

    state = get_state(tool_context)

    if not file_exists(state, path):
        return f"Error: Question file not found: {path}"

    _write_answer_to_file(state, path, question_number, answer)
    return f"Recorded answer for Question {question_number} in {path}"


@tool(context=True)
def detect_ambiguities(tool_context: dict, answers: list[dict]) -> list[dict]:
    """Analyze answers for vagueness, contradictions, and missing details.

    Args:
        tool_context: Strands tool context with invocation_state.
        answers: List of answer dicts from read_answers.

    Returns:
        List of dicts describing detected ambiguities, each with
        'question_number', 'issue', and 'type' keys.
    """
    ambiguities = []

    for ans in answers:
        q_num = ans.get("question_number", 0)
        answer_text = ans.get("answer", "").lower().strip()

        # Check unanswered
        if not ans.get("answered", False):
            ambiguities.append({
                "question_number": q_num,
                "issue": "Question not answered",
                "type": "unanswered",
            })
            continue

        # Check for vague indicators
        for indicator in _VAGUE_INDICATORS:
            if indicator in answer_text:
                ambiguities.append({
                    "question_number": q_num,
                    "issue": f"Vague response detected: '{indicator}'",
                    "type": "vague",
                })
                break

        # Check for very short answers (likely insufficient detail)
        # Single digits (1-9) and single letters (a-x) are valid option picks
        if len(answer_text) < 3 and not answer_text.isdigit() and answer_text not in ("a", "b", "c", "d", "x"):
            ambiguities.append({
                "question_number": q_num,
                "issue": "Answer too short — may lack sufficient detail",
                "type": "insufficient",
            })

    return ambiguities
