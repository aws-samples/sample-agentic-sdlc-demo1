"""Requirements Analysis Agent for the AI-SDLC Multi-Agent System.

This agent analyzes user requests to determine clarity, type, scope, complexity,
and depth level. It generates clarifying questions, collects answers, detects
ambiguities, generates requirements documents, and collects extension configuration.

Produces artifacts in aidlc-docs/inception/requirements/:
- requirement-verification-questions.md
- requirements.md

Has an approval gate at completion.
"""

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.extensions import load_extensions
from tools.file_io import write_artifact
from tools.questions import create_questions_file, detect_ambiguities, read_answers, record_answer

# Valid values for request analysis fields
CLARITY_VALUES = ("clear", "vague", "incomplete")
REQUEST_TYPES = ("new_feature", "bug_fix", "refactoring", "enhancement", "migration", "integration")
SCOPE_VALUES = ("single_file", "single_component", "multiple_components", "system_wide")
COMPLEXITY_VALUES = ("trivial", "simple", "moderate", "complex")
DEPTH_LEVELS = ("minimal", "standard", "comprehensive")

QUESTIONS_PATH = "aidlc-docs/inception/requirements/requirement-verification-questions.md"
REQUIREMENTS_PATH = "aidlc-docs/inception/requirements/requirements.md"
UPLOADED_PREFIX = "aidlc-docs/inception/requirements/uploaded/"

# Supported upload formats (design Section 7a, requirements 5.2-5.4).
_PASSTHROUGH_EXTS = {".md", ".txt"}
_EXTRACTOR_EXTS = {".pdf", ".docx"}


@tool(context=True)
def read_uploaded_requirements(tool_context: dict) -> dict:
    """Read any uploaded requirements documents for the current Project.

    Implements design Section 7c (Requirements 5.5-5.7): lists the project's
    ``aidlc-docs/inception/requirements/uploaded/`` prefix and returns the
    plain text of every document found there.

    * ``.md`` and ``.txt`` files are read directly via the Gateway file-ops
      tool (Requirement 5.2 — direct ingestion).
    * ``.pdf`` and ``.docx`` files are sent to the Gateway's
      ``extract_document_text`` tool, which parses them with ``pypdf`` /
      ``python-docx`` and writes the normalized plain text to
      ``.../uploaded/extracted/<filename>.txt`` (Requirement 5.3).
    * The ``extracted/`` subdirectory is skipped — those files are
      derivative outputs of this tool and re-reading them would
      double-count content.

    Args:
        tool_context: Strands tool context with ``invocation_state``.

    Returns:
        A dict with:
          - ``has_uploads`` (bool): True if at least one uploaded document
            was found and successfully ingested.
          - ``documents`` (list[dict]): one entry per ingested document with
            ``path`` (project-relative S3 key) and ``text`` (normalized
            plain text). Empty when no uploads are present.
          - ``errors`` (list[dict]): per-document failures captured during
            extraction, each ``{"path": ..., "error": ...}``. An empty list
            when extraction succeeded for every uploaded document.
    """
    import os as _os
    import posixpath as _posixpath

    from tools._compat import get_state
    from tools.file_ops import list_files, read_file
    from tools.gateway import call_tool

    state = get_state(tool_context)

    # Listing may legitimately return an empty list when no documents have
    # been uploaded; that is the "no upload present" branch (Req 5.7).
    try:
        keys = list_files(state, UPLOADED_PREFIX)
    except RuntimeError:
        keys = []

    documents: list[dict] = []
    errors: list[dict] = []

    for key in keys:
        # Normalise so prefix comparisons are slash-safe across S3 keys.
        norm = key.replace("\\", "/")
        # Skip the derived ``extracted/`` outputs and any non-file directory
        # placeholders S3 may surface when the prefix has subdirectories.
        rel = norm[len(UPLOADED_PREFIX):] if norm.startswith(UPLOADED_PREFIX) else norm
        if not rel or rel.endswith("/"):
            continue
        if rel.startswith("extracted/") or "/extracted/" in rel:
            continue

        ext = _os.path.splitext(rel)[1].lower()

        if ext in _PASSTHROUGH_EXTS:
            try:
                text = read_file(state, key)
            except (FileNotFoundError, RuntimeError) as exc:
                errors.append({"path": key, "error": str(exc)})
                continue
            documents.append({"path": key, "text": text})
            continue

        if ext in _EXTRACTOR_EXTS:
            # The Gateway extractor needs the absolute S3 key (``{session_id}/...``)
            # because it talks to S3 directly, not through the workspace-relative
            # file-ops layer.
            session_id = state.get("_session_id", "")
            absolute_key = (
                _posixpath.join(session_id, key) if session_id else key
            )
            mcp = state.get("_mcp_client")
            if mcp is None:
                errors.append({
                    "path": key,
                    "error": "MCP client not available for document extraction",
                })
                continue
            try:
                result = call_tool(
                    mcp, "extract_document_text", {"file_path": absolute_key}
                )
            except Exception as exc:  # noqa: BLE001 — surface gateway errors verbatim
                errors.append({"path": key, "error": str(exc)})
                continue

            is_error = (
                isinstance(result, dict)
                and (result.get("isError") or result.get("status") == "error")
            )
            text = _extract_gateway_text(result)
            if is_error or text is None:
                errors.append({
                    "path": key,
                    "error": text or "extract_document_text returned no content",
                })
                continue
            documents.append({"path": key, "text": text})
            continue

        # Unknown extension under uploaded/ — record it so callers can
        # surface a clear message; the Frontend already rejects unsupported
        # formats pre-upload (Req 5.4) so this is a defensive guard.
        errors.append({
            "path": key,
            "error": f"Unsupported document format: {ext or '(none)'}",
        })

    return {
        "has_uploads": len(documents) > 0,
        "documents": documents,
        "errors": errors,
    }


def _extract_gateway_text(result) -> str | None:
    """Extract the text payload from a Gateway tool response envelope."""
    if isinstance(result, dict):
        content_list = result.get("content", [])
        if isinstance(content_list, list):
            for item in content_list:
                if isinstance(item, dict) and "text" in item:
                    return item["text"]
        if isinstance(result.get("content"), str):
            return result["content"]
    if isinstance(result, str):
        return result
    return None


@tool(context=True)
def analyze_request(tool_context: dict, user_request: str) -> dict:
    """Analyze the user request to determine clarity, type, scope, complexity, and depth level.

    Updates SharedState request_analysis and depth_level fields.

    Args:
        tool_context: Strands tool context with invocation_state.
        user_request: The original user request string.

    Returns:
        Dict with clarity, request_type, scope, complexity, and depth_level.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    request_lower = user_request.lower().strip()
    word_count = len(request_lower.split())

    # Determine clarity
    if word_count < 5:
        clarity = "incomplete"
    elif any(w in request_lower for w in ["maybe", "not sure", "somehow", "something"]):
        clarity = "vague"
    else:
        clarity = "clear"

    # Determine request type
    if any(w in request_lower for w in ["bug", "fix", "broken", "error", "crash"]):
        request_type = "bug_fix"
    elif any(w in request_lower for w in ["refactor", "clean", "restructure", "reorganize"]):
        request_type = "refactoring"
    elif any(w in request_lower for w in ["migrate", "migration", "upgrade", "port"]):
        request_type = "migration"
    elif any(w in request_lower for w in ["integrate", "integration", "connect", "api"]):
        request_type = "integration"
    elif any(w in request_lower for w in ["improve", "enhance", "optimize", "add"]):
        request_type = "enhancement"
    else:
        request_type = "new_feature"

    # Determine scope
    if any(w in request_lower for w in ["system", "entire", "whole", "all", "full"]):
        scope = "system_wide"
    elif any(w in request_lower for w in ["multiple", "several", "components", "modules"]):
        scope = "multiple_components"
    elif any(w in request_lower for w in ["single", "one file", "small"]):
        scope = "single_file"
    else:
        scope = "single_component"

    # Determine complexity
    if word_count < 10 and scope == "single_file":
        complexity = "trivial"
    elif scope == "system_wide" or word_count > 100:
        complexity = "complex"
    elif scope == "multiple_components" or word_count > 50:
        complexity = "moderate"
    else:
        complexity = "simple"

    # Determine depth level
    if complexity in ("trivial", "simple"):
        depth_level = "minimal"
    elif complexity == "complex":
        depth_level = "comprehensive"
    else:
        depth_level = "standard"

    # Update SharedState
    state["request_analysis"] = {
        "clarity": clarity,
        "request_type": request_type,
        "scope": scope,
        "complexity": complexity,
    }
    state["depth_level"] = depth_level

    return {
        "clarity": clarity,
        "request_type": request_type,
        "scope": scope,
        "complexity": complexity,
        "depth_level": depth_level,
    }


@tool(context=True)
def generate_requirements(tool_context: dict, requirements_content: str) -> str:
    """Generate the requirements.md document in aidlc-docs/inception/requirements/.

    Writes via the Gateway to S3.

    Args:
        tool_context: Strands tool context with invocation_state.
        requirements_content: The markdown content for the requirements document.

    Returns:
        Confirmation message with the file path.
    """
    from tools._compat import get_state
    from tools.file_ops import write_file

    state = get_state(tool_context)
    write_file(state, REQUIREMENTS_PATH, requirements_content)

    # Update artifacts in SharedState
    artifacts = state.get("artifacts", {}).get("requirements", {})
    artifacts["requirements_doc"] = REQUIREMENTS_PATH
    state.get("artifacts", {})["requirements"] = artifacts

    # Mark requirements_analysis as completed and persist state
    from tools.state import mark_stage_complete
    mark_stage_complete(state, "requirements_analysis")

    return f"Requirements document generated at {REQUIREMENTS_PATH}"


@tool(context=True)
def collect_extension_config(tool_context: dict, extension_decisions: dict) -> str:
    """Collect extension applicability and record enabled/disabled status in SharedState.

    Args:
        tool_context: Strands tool context with invocation_state.
        extension_decisions: Dict mapping extension names to bool (enabled/disabled).

    Returns:
        Confirmation message with the number of extensions configured.
    """
    from tools._compat import get_state
    state = get_state(tool_context)
    extensions = state.get("extensions", {})
    extensions["enabled_status"] = extension_decisions

    return f"Configured {len(extension_decisions)} extensions"


SYSTEM_PROMPT = """\
You are the Requirements Analysis Agent for the AI-SDLC multi-agent system.
You analyze user requests and generate comprehensive requirements documents.

CRITICAL CONSTRAINT — AWS Only:
All infrastructure, deployment, and cloud service decisions MUST target AWS
(Amazon Web Services) exclusively. Do NOT offer or suggest other cloud
providers (Azure, GCP, etc.) as options. When generating questions about
deployment, hosting, infrastructure, or cloud services, all options MUST be
AWS services (e.g. Lambda, ECS, EC2, S3, DynamoDB, RDS, CloudFront, API
Gateway, Cognito, etc.). Treat AWS as the only cloud platform.

You MUST execute ALL of the following steps IN ORDER. Do NOT skip any step.
Do NOT move to the next step until the current step is fully complete.

━━━ STEP 0: Read Any Uploaded Requirements Document ━━━
BEFORE generating any clarifying questions, call `read_uploaded_requirements`
to ingest any document the Business Analyst has uploaded under the project's
`aidlc-docs/inception/requirements/uploaded/` prefix. The tool returns a dict
with `has_uploads`, `documents`, and `errors`.

— If `has_uploads` is true, treat the concatenated text of every document in
  `documents` as the AUTHORITATIVE REQUIREMENTS BASELINE for this project.
  In STEP 2 you MUST generate clarifying questions LIMITED to information
  that is genuinely absent from or ambiguous in the uploaded baseline.
  Do NOT re-ask questions whose answers are already plainly stated in the
  uploaded text. The number of questions is bounded by the number of real
  gaps/ambiguities — generate fewer questions when the upload is thorough,
  or zero questions when it leaves no material gap.

— If `has_uploads` is false (no upload present), proceed to STEP 2 with the
  FULL standard requirements question set described there.

— If `errors` is non-empty, mention each failed file briefly to the user and
  proceed using the documents that were ingested successfully (treat the
  failed files as if they were not uploaded).

In either branch, ALWAYS continue with STEP 1 next.

━━━ STEP 1: Analyze the Request ━━━
Call `analyze_request` with the user's original request to determine clarity,
request_type, scope, complexity, and depth_level.

━━━ STEP 2: Check for Existing Questions (Resume Support) ━━━
FIRST, call `read_answers` with path
aidlc-docs/inception/requirements/requirement-verification-questions.md
to check whether a questions file already exists from a previous session.

— If `read_answers` returns a not-found error: the file does not exist.
  You MUST generate a new set of clarifying questions following the rules
  established by STEP 0:

  • If STEP 0 reported `has_uploads = true`: generate clarifying questions
    LIMITED to information missing from or ambiguous in the uploaded
    baseline. Skip every topic in the standard list below that the upload
    already answers unambiguously. Generate as many or as few questions as
    the real gaps require — generating zero questions is acceptable when
    the upload covers everything (proceed straight to STEP 4 in that case).

  • If STEP 0 reported `has_uploads = false`: generate a FOCUSED set of the
    **4-5 most decision-critical questions** for this specific project. Do NOT
    ask one question per topic below — treat the list as a menu and pick only
    the few highest-impact items whose answers most change the design. Prefer
    questions that disambiguate scope, core features, and the primary
    technical/architectural choices. Topics to choose from:
  - Target audience and users
  - Core features and functionality
  - Technology preferences (language, framework)
  - Data storage and persistence needs (AWS services: DynamoDB, RDS, S3, etc.)
  - User interface expectations
  - Authentication and authorization needs (AWS Cognito, IAM, etc.)
  - Performance and scalability expectations
  - AWS deployment preferences (Lambda, ECS, EC2, Amplify, etc.)
  - Integration with external services or AWS services
  - Any constraints or limitations

  CRITICAL — QUESTION FORMAT: Every question you generate MUST include meaningful
  multiple-choice options in the 'options' list. Do NOT generate free-text questions.
  For technology-related questions, provide concrete options (e.g. specific languages,
  frameworks, platforms). Each question dict must have both 'text' and 'options' keys,
  where 'options' is a non-empty list of strings. The tool will automatically append
  an "Other" option, so do not include it yourself.

  Call `create_questions_file` with the path
  aidlc-docs/inception/requirements/requirement-verification-questions.md
  Pass your list of questions. Then proceed to STEP 3. (If you generated
  zero questions because the upload left no material gap, you may skip
  `create_questions_file` and STEP 3 entirely and proceed to STEP 4.)

— If `read_answers` succeeds: the file already exists from a prior session.
  Do NOT call `create_questions_file` — the existing file must be preserved.
  Examine the returned list to identify which questions have `answered: true`
  and which have `answered: false`. Then proceed to STEP 3.

━━━ STEP 3: Collect Answers ONE AT A TIME (Resume-Aware) ━━━
If the questions file already existed (resume path), FIRST replay every
previously answered question as a summary so the user has context:

For each question where `answered` is true, output a brief recap:

✅ **Question {X} of {Y}**: {Question text}
↳ Your previous answer: {answer}

After replaying all answered questions, continue from the FIRST question
where `answered` is false. Ask it using `human_input` with EXACTLY this
markdown format:

📚 **{Stage Name} — Question {X} of {Y}**

{Question text}

**Options:**
1. {First option}
2. {Second option}
3. {Third option}
...
X) Other — please describe

Please type the number of your choice, or type `X` and describe your response.

CRITICAL FORMATTING RULES:
- Options MUST be numbered starting from 1 (not letters like A, B, C)
- Each option MUST be on its own line with a number followed by a period and space
- The last option MUST always be "X) Other — please describe" (using X, not a number)
- Include a blank line between the question text and "**Options:**"
- Include a blank line between the last option and the instruction line
- The stage name should be the current stage (e.g., "Requirements Analysis")

After receiving each answer, IMMEDIATELY call `record_answer` with the
file path, question number, and the user's response.
Only then move to the next question.

If ALL questions are already answered (full resume), skip directly to STEP 4.

DO NOT batch questions. Ask them ONE AT A TIME and wait for each response.
DO NOT re-ask questions that already have answers. DO NOT assume answers
for unanswered questions — you MUST collect real answers from the user.

━━━ STEP 4: Check for Ambiguities ━━━
Call `read_answers` to collect all answers, then call `detect_ambiguities`.
If ambiguities are found, ask follow-up questions to resolve them (using the
same one-at-a-time approach from Step 3).

━━━ STEP 5: Generate Requirements Document ━━━
Call `generate_requirements` to create the requirements document containing:
- Intent analysis summary
- Functional requirements (derived from the user's answers)
- Non-functional requirements
The document will be written to aidlc-docs/inception/requirements/requirements.md.

━━━ STEP 6: Extension Configuration ━━━
Call `load_extensions` to scan for extension rules, then call
`collect_extension_config` to record which extensions are enabled/disabled.

━━━ STEP 7: Present Requirements for Approval ━━━
Before asking for approval you MUST show the user what they are approving.
Do the following in order:

a) Read back the requirements document you just generated (the full content
   you passed to `generate_requirements`).
b) Present a clear, formatted overview to the user that includes:
   - A summary of the intent analysis (clarity, type, scope, complexity, depth)
   - ALL functional requirements (numbered list)
   - ALL non-functional requirements (numbered list)
   - Extension configuration summary (if any)
   Output this overview as your normal text response so the user can read it
   in the chat BEFORE you ask for approval.
c) AFTER the overview is visible, call `present_approval_gate` with the stage
   name "Requirements Analysis", a SHORT summary sentence, and the default
   options.
d) Then ask the user for their decision via `human_input` and call
   `process_approval_response` with their answer.
e) If the user requests changes, incorporate feedback and regenerate affected
   artifacts, then repeat from (a).

After completion:
- Set stage_progress.requirements_analysis to "completed".
- Ensure request_analysis and depth_level are set in SharedState.
- STOP immediately. We will automatically move onto the next stage.

CRITICAL: Steps 2 and 3 are MANDATORY. You must either generate new questions
OR resume from existing ones, and collect all remaining unanswered answers from
the user before generating requirements. Never skip the interactive
question-and-answer phase. Never re-ask a question that already has an answer.
"""

TOOLS = [
    analyze_request,
    read_uploaded_requirements,
    create_questions_file,
    read_answers,
    record_answer,
    detect_ambiguities,
    generate_requirements,
    collect_extension_config,
    load_extensions,
    present_approval_gate,
    process_approval_response,
    write_artifact,
]


def create_requirements_analysis_agent(model: BedrockModel, session_manager=None) -> Agent:
    """Create and return the Requirements Analysis Agent.

    Args:
        model: A configured BedrockModel instance for LLM inference.
        session_manager: Optional AgentCoreMemorySessionManager for
            conversation history persistence.

    Returns:
        A configured Strands Agent for requirements analysis.
    """
    return create_stage_agent(
        stage_name="requirements_analysis",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
