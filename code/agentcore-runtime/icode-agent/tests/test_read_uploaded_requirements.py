"""Unit tests for the ``read_uploaded_requirements`` Requirements_Analysis tool.

Covers task 6.2 (design Section 7c, Requirements 5.5-5.7):

* No upload present -> ``has_uploads`` is False, ``documents`` is empty (Req 5.7
  fallback path: the agent prompt then asks the full standard question set).
* ``.md`` and ``.txt`` uploads are returned directly via the file-ops layer
  (Req 5.2).
* ``.pdf`` and ``.docx`` uploads are routed through the Gateway's
  ``extract_document_text`` tool, with the absolute S3 key
  (``{session_id}/...``) so the extractor can fetch the source object
  (Req 5.3).
* The derived ``extracted/`` outputs are filtered out of the listing so the
  same content is not double-counted on re-runs.
* Per-document failures land in ``errors`` without aborting the whole call,
  so a single corrupted upload cannot block ingestion of healthy ones.

The runtime container's heavy SDK dependencies (``strands``, ``mcp``,
``bedrock_agentcore``) are not installed in the unit-test environment; they
are stubbed via ``sys.modules`` BEFORE importing the agent module, mirroring
the pattern used by ``tests/test_state_collaboration_roundtrip.py``.

Validates: Requirements 5.5, 5.6, 5.7
"""

import os
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Path / import setup
# ---------------------------------------------------------------------------

_ICODE_AGENT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ICODE_AGENT_DIR not in sys.path:
    sys.path.insert(0, _ICODE_AGENT_DIR)


def _ensure_stub_module(name: str) -> types.ModuleType:
    """Return ``sys.modules[name]``, creating a bare stub module if absent."""
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
    return sys.modules[name]


def _tool_passthrough(*args, **kwargs):
    """A no-op @tool decorator usable as both ``@tool`` and ``@tool(context=True)``."""
    if args and callable(args[0]) and not kwargs:
        return args[0]

    def _decorator(fn):
        return fn

    return _decorator


def _install_strands_stubs() -> None:
    strands_root = _ensure_stub_module("strands")
    strands_root.tool = _tool_passthrough

    class _AgentStub:  # noqa: D401 — stub
        pass

    strands_root.Agent = _AgentStub

    strands_tools = _ensure_stub_module("strands.tools")
    strands_tools.tool = _tool_passthrough
    strands_root.tools = strands_tools

    strands_mcp = _ensure_stub_module("strands.tools.mcp")
    strands_mcp.MCPClient = type("MCPClient", (), {})

    strands_models = _ensure_stub_module("strands.models")
    strands_models.BedrockModel = type("BedrockModel", (), {})

    strands_agent = _ensure_stub_module("strands.agent")
    strands_agent_cm = _ensure_stub_module("strands.agent.conversation_manager")
    strands_agent_cm.SlidingWindowConversationManager = type(
        "SlidingWindowConversationManager", (), {}
    )
    strands_agent.conversation_manager = strands_agent_cm


_install_strands_stubs()

from agents.inception.requirements_analysis import (  # noqa: E402
    UPLOADED_PREFIX,
    read_uploaded_requirements,
)


# ---------------------------------------------------------------------------
# Stubs for tools.file_ops.list_files / read_file and tools.gateway.call_tool
# ---------------------------------------------------------------------------


class _StubFileOps:
    """Captures list_files / read_file calls and serves canned responses.

    The agent module imports ``tools.file_ops`` lazily inside the tool
    function, so the test monkeypatches the *module* attributes rather than
    the imported names — that way the lazy import in the tool resolves to the
    stubs.
    """

    def __init__(self, listing: list[str], file_contents: dict[str, str]):
        self._listing = listing
        self._files = file_contents
        self.list_calls: list[str] = []
        self.read_calls: list[str] = []

    def list_files(self, state, prefix):
        self.list_calls.append(prefix)
        return list(self._listing)

    def read_file(self, state, path):
        self.read_calls.append(path)
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]


class _StubGateway:
    """Captures ``call_tool`` calls and serves canned ``extract_document_text``
    responses keyed by the absolute S3 key the agent passes in."""

    def __init__(self, responses: dict[str, dict]):
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, mcp_client, tool_name, args):
        self.calls.append((tool_name, dict(args)))
        path = args.get("file_path")
        if path in self._responses:
            return self._responses[path]
        return {
            "isError": True,
            "content": [{"text": f"unexpected key: {path}"}],
        }


def _ok_text(text: str) -> dict:
    """Mirror the Gateway's success envelope."""
    return {"content": [{"text": text}]}


def _err(text: str) -> dict:
    return {"isError": True, "content": [{"text": text}]}


def _patch_io(monkeypatch, ops: _StubFileOps, gateway: _StubGateway) -> None:
    import agents.inception.requirements_analysis as ra
    import tools.file_ops as file_ops_mod
    import tools.gateway as gateway_mod

    monkeypatch.setattr(file_ops_mod, "list_files", ops.list_files)
    monkeypatch.setattr(file_ops_mod, "read_file", ops.read_file)
    monkeypatch.setattr(gateway_mod, "call_tool", gateway)
    # Defensive: if anything in the agent module captured these at module load
    # time it will still see the stubs.
    for attr in ("list_files", "read_file"):
        if hasattr(ra, attr):
            monkeypatch.setattr(ra, attr, getattr(ops, attr))


def _state(session_id: str = "online_bookstore") -> dict:
    """Build a tool_context-shaped dict the @tool wrapper unwraps via _compat.get_state."""
    return {
        "invocation_state": {
            "_session_id": session_id,
            # ``read_uploaded_requirements`` only needs a truthy MCP client to
            # decide it can call the Gateway; the StubGateway swallows it.
            "_mcp_client": object(),
        }
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_upload_returns_empty_no_uploads_flag(monkeypatch):
    """When the uploaded/ prefix is empty the tool reports has_uploads=False
    and an empty documents list, which the agent prompt uses to fall through
    to the FULL standard question set (Req 5.7).
    """
    ops = _StubFileOps(listing=[], file_contents={})
    gateway = _StubGateway(responses={})
    _patch_io(monkeypatch, ops, gateway)

    result = read_uploaded_requirements(_state())

    assert result == {"has_uploads": False, "documents": [], "errors": []}
    assert ops.list_calls == [UPLOADED_PREFIX]
    assert ops.read_calls == []
    assert gateway.calls == []


def test_md_and_txt_uploads_are_read_directly(monkeypatch):
    """``.md`` / ``.txt`` are passthrough formats per Req 5.2 — content is
    returned via the file-ops layer without invoking the extractor."""
    md_key = f"{UPLOADED_PREFIX}requirements.md"
    txt_key = f"{UPLOADED_PREFIX}notes.txt"
    ops = _StubFileOps(
        listing=[md_key, txt_key],
        file_contents={
            md_key: "# Requirements\n- must support login\n",
            txt_key: "additional notes\n",
        },
    )
    gateway = _StubGateway(responses={})
    _patch_io(monkeypatch, ops, gateway)

    result = read_uploaded_requirements(_state())

    assert result["has_uploads"] is True
    assert result["errors"] == []
    paths = sorted(d["path"] for d in result["documents"])
    assert paths == sorted([md_key, txt_key])
    by_path = {d["path"]: d["text"] for d in result["documents"]}
    assert by_path[md_key].startswith("# Requirements")
    assert by_path[txt_key] == "additional notes\n"
    # Pass-through formats MUST NOT call the extractor.
    assert gateway.calls == []
    # Both files were read via the file-ops read_file helper.
    assert sorted(ops.read_calls) == sorted([md_key, txt_key])


def test_pdf_and_docx_route_through_extract_document_text(monkeypatch):
    """``.pdf`` / ``.docx`` are sent to the Gateway extractor with the
    *absolute* S3 key (``{session_id}/...``) — the extractor opens the object
    directly and would fail on a workspace-relative path (Req 5.3)."""
    pdf_key = f"{UPLOADED_PREFIX}spec.pdf"
    docx_key = f"{UPLOADED_PREFIX}brief.docx"
    abs_pdf = f"online_bookstore/{pdf_key}"
    abs_docx = f"online_bookstore/{docx_key}"
    ops = _StubFileOps(listing=[pdf_key, docx_key], file_contents={})
    gateway = _StubGateway(
        responses={
            abs_pdf: _ok_text("EXTRACTED PDF TEXT"),
            abs_docx: _ok_text("EXTRACTED DOCX TEXT"),
        }
    )
    _patch_io(monkeypatch, ops, gateway)

    result = read_uploaded_requirements(_state(session_id="online_bookstore"))

    assert result["has_uploads"] is True
    assert result["errors"] == []
    by_path = {d["path"]: d["text"] for d in result["documents"]}
    assert by_path[pdf_key] == "EXTRACTED PDF TEXT"
    assert by_path[docx_key] == "EXTRACTED DOCX TEXT"

    # The agent must call extract_document_text with the absolute S3 key for
    # each non-passthrough format. Order is not asserted because S3 listing
    # order is not guaranteed; compare as a multiset of (name, file_path) pairs.
    seen = sorted((name, args["file_path"]) for name, args in gateway.calls)
    expected = sorted([
        ("extract_document_text", abs_pdf),
        ("extract_document_text", abs_docx),
    ])
    assert seen == expected
    # No passthrough reads for binary formats.
    assert ops.read_calls == []


def test_extracted_subdirectory_is_filtered_out(monkeypatch):
    """The extractor writes ``.../uploaded/extracted/<file>.txt`` next to the
    source. The agent's listing must skip that subdirectory so re-running
    ingestion never double-counts a previously extracted document."""
    pdf_key = f"{UPLOADED_PREFIX}spec.pdf"
    extracted_key = f"{UPLOADED_PREFIX}extracted/spec.txt"
    abs_pdf = f"online_bookstore/{pdf_key}"
    ops = _StubFileOps(listing=[pdf_key, extracted_key], file_contents={})
    gateway = _StubGateway(responses={abs_pdf: _ok_text("PDF BODY")})
    _patch_io(monkeypatch, ops, gateway)

    result = read_uploaded_requirements(_state())

    assert result["has_uploads"] is True
    assert [d["path"] for d in result["documents"]] == [pdf_key]
    # The extracted/ entry must not produce a passthrough read or a redundant
    # extraction call.
    assert ops.read_calls == []
    assert [c for c in gateway.calls] == [
        ("extract_document_text", {"file_path": abs_pdf})
    ]


def test_extractor_failure_lands_in_errors_without_breaking_others(monkeypatch):
    """A single failed document must not block ingestion of the others —
    failures are recorded under ``errors`` and successful documents are still
    returned. This makes the "happy" requirements baseline available even
    when one upload is corrupt."""
    md_key = f"{UPLOADED_PREFIX}good.md"
    bad_pdf = f"{UPLOADED_PREFIX}bad.pdf"
    abs_bad_pdf = f"online_bookstore/{bad_pdf}"
    ops = _StubFileOps(
        listing=[md_key, bad_pdf],
        file_contents={md_key: "# Good doc\n"},
    )
    gateway = _StubGateway(responses={abs_bad_pdf: _err("Failed to parse PDF")})
    _patch_io(monkeypatch, ops, gateway)

    result = read_uploaded_requirements(_state())

    assert result["has_uploads"] is True  # the good file still ingested
    assert [d["path"] for d in result["documents"]] == [md_key]
    assert len(result["errors"]) == 1
    assert result["errors"][0]["path"] == bad_pdf
    assert "Failed to parse PDF" in result["errors"][0]["error"]


def test_unsupported_extension_is_recorded_as_error(monkeypatch):
    """Defensive guard: even though the Frontend rejects unsupported formats
    pre-upload (Req 5.4), if a stray file slips into ``uploaded/`` the agent
    records it as an error rather than silently ignoring it. This keeps the
    operator aware of unexpected files in the project prefix."""
    bad_key = f"{UPLOADED_PREFIX}rogue.exe"
    ops = _StubFileOps(listing=[bad_key], file_contents={})
    gateway = _StubGateway(responses={})
    _patch_io(monkeypatch, ops, gateway)

    result = read_uploaded_requirements(_state())

    assert result["has_uploads"] is False
    assert result["documents"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0]["path"] == bad_key
    assert "Unsupported document format" in result["errors"][0]["error"]
    # Neither read nor extract should have been attempted.
    assert ops.read_calls == []
    assert gateway.calls == []


def test_listing_runtime_error_is_treated_as_no_uploads(monkeypatch):
    """If the Gateway list_files call raises (e.g. transient network error),
    the tool falls through to the no-upload branch so the agent can still
    fall back to the FULL standard question set rather than crashing."""

    def _boom(state, prefix):
        raise RuntimeError("gateway down")

    import tools.file_ops as file_ops_mod
    monkeypatch.setattr(file_ops_mod, "list_files", _boom)

    # read_file / call_tool should never be called when listing fails.
    sentinel = _StubFileOps(listing=[], file_contents={})

    def _read_file_should_not_be_called(state, path):  # pragma: no cover
        raise AssertionError(f"read_file unexpectedly called with {path}")

    monkeypatch.setattr(file_ops_mod, "read_file", _read_file_should_not_be_called)
    import tools.gateway as gateway_mod

    def _call_tool_should_not_be_called(*a, **kw):  # pragma: no cover
        raise AssertionError("call_tool unexpectedly called")

    monkeypatch.setattr(gateway_mod, "call_tool", _call_tool_should_not_be_called)

    result = read_uploaded_requirements(_state())

    assert result == {"has_uploads": False, "documents": [], "errors": []}


def test_missing_mcp_client_records_error_for_extractor_formats(monkeypatch):
    """If the Gateway client is absent from shared state (a runtime
    misconfiguration), extractor formats are recorded under errors instead of
    raising — passthrough formats can still succeed."""
    md_key = f"{UPLOADED_PREFIX}good.md"
    pdf_key = f"{UPLOADED_PREFIX}spec.pdf"
    ops = _StubFileOps(
        listing=[md_key, pdf_key],
        file_contents={md_key: "# good\n"},
    )
    gateway = _StubGateway(responses={})
    _patch_io(monkeypatch, ops, gateway)

    state_no_mcp = {
        "invocation_state": {
            "_session_id": "online_bookstore",
            # No "_mcp_client" — simulate misconfigured runtime.
        }
    }
    result = read_uploaded_requirements(state_no_mcp)

    # Passthrough succeeds.
    assert any(d["path"] == md_key for d in result["documents"])
    # Extractor format failed clearly.
    assert any(
        e["path"] == pdf_key and "MCP client" in e["error"]
        for e in result["errors"]
    )
    # The Gateway must NOT have been called for the .pdf when the client
    # was absent.
    assert gateway.calls == []


def test_gateway_tool_map_translates_extract_document_text():
    """The runtime's ``tools.gateway`` translates the internal
    ``extract_document_text`` name to the Gateway-namespaced name and remaps
    the ``path`` argument to ``file_path``. This is the wire contract between
    the agent and the Lambda dispatcher (handler.py)."""
    import tools.gateway as gateway_mod

    gw_name, key_remap = gateway_mod._TOOL_MAP["extract_document_text"]
    assert gw_name == "file-operations___extract_document_text"
    # The passthrough already uses ``file_path``, but we also accept the
    # internal ``path`` alias for symmetry with read_file/write_file.
    assert key_remap.get("path") == "file_path"



# ---------------------------------------------------------------------------
# SYSTEM_PROMPT and TOOLS wiring
# ---------------------------------------------------------------------------


def test_step_zero_is_present_in_system_prompt():
    """STEP 0 must appear before STEP 1 and explicitly direct the agent to
    call ``read_uploaded_requirements`` first. STEP 5 (generate_requirements)
    must remain unchanged in name (Req: STEP 5 unchanged per task 6.2)."""
    from agents.inception.requirements_analysis import SYSTEM_PROMPT

    step0_idx = SYSTEM_PROMPT.find("STEP 0:")
    step1_idx = SYSTEM_PROMPT.find("STEP 1:")
    step5_idx = SYSTEM_PROMPT.find("STEP 5: Generate Requirements Document")
    assert step0_idx != -1, "SYSTEM_PROMPT must include STEP 0"
    assert step1_idx != -1, "SYSTEM_PROMPT must still include STEP 1"
    assert step0_idx < step1_idx, "STEP 0 must appear before STEP 1"
    assert step5_idx != -1, (
        "STEP 5 (Generate Requirements Document) must remain — task 6.2 "
        "preserves the existing generate_requirements step verbatim."
    )

    step0_block = SYSTEM_PROMPT[step0_idx:step1_idx]
    assert "read_uploaded_requirements" in step0_block, (
        "STEP 0 must instruct the agent to call read_uploaded_requirements"
    )
    # STEP 0 must teach BOTH branches of Req 5.6 / 5.7: gap-only when present,
    # full set when absent.
    assert "has_uploads" in step0_block
    assert "gap" in step0_block.lower() or "gaps" in step0_block.lower()
    assert "FULL" in step0_block.upper() or "full standard" in step0_block.lower()


def test_read_uploaded_requirements_is_registered_as_tool():
    """The new tool must be part of the agent's TOOLS list so the LLM is
    actually permitted to invoke it."""
    from agents.inception.requirements_analysis import (
        TOOLS,
        read_uploaded_requirements,
    )

    assert read_uploaded_requirements in TOOLS
