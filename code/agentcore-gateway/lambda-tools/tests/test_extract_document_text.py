"""Unit tests for the ``extract_document_text`` Gateway tool (task 6.1).

Exercises the real tool function against an in-memory fake S3 so the three
behavioral branches the design calls out (Section 7b, requirements 5.2-5.3) are
all covered end-to-end:

* ``.md`` / ``.txt`` -> body returned directly (no extraction needed).
* ``.pdf`` -> text extracted via the real ``pypdf`` from real PDF bytes.
* ``.docx`` -> text extracted via the real ``python-docx`` from real DOCX bytes.

The "expected" text on the binary paths is determined by writing a known string
into the source document with the same library a downstream agent would use,
then asserting the tool reads that string back. That makes the assertion
independent of how ``pypdf`` / ``python-docx`` happen to format the text on
extract (no mirror testing).

Validates: Requirements 5.2, 5.3
"""

import importlib
import io
import os
import sys

import pytest

# Make the lambda-tools modules (handler, shared, tools/) importable regardless
# of pytest's rootdir/cwd. Mirrors cdk/custom-resources/tests/test_project_api.py.
_LAMBDA_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _LAMBDA_TOOLS_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_TOOLS_DIR)
_TESTS_DIR = os.path.dirname(__file__)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

# ``shared.py`` requires BUCKET_NAME at import time; set before any imports.
os.environ.setdefault("BUCKET_NAME", "test-bucket")

from fake_s3 import FakeS3Client  # noqa: E402

import shared  # noqa: E402

# ``from tools import extract_document_text`` resolves to the function (it's
# re-exported by ``tools/__init__.py``); the submodule attribute is shadowed in
# the ``tools`` package namespace. Load the submodule directly via importlib so
# the fixture can monkeypatch its ``s3_client`` and ``BUCKET_NAME`` symbols.
extract_module = importlib.import_module("tools.extract_document_text")

BUCKET = "test-bucket"
UPLOADED_DIR = "online_bookstore/aidlc-docs/inception/requirements/uploaded"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def s3(monkeypatch):
    """Install a fresh fake S3 in both ``shared`` and the tool module.

    Both modules captured a reference to the real ``boto3`` S3 client at import
    time; we rebind both so the tool always sees the fake.
    """
    fake = FakeS3Client()
    monkeypatch.setattr(shared, "s3_client", fake)
    monkeypatch.setattr(extract_module, "s3_client", fake)
    monkeypatch.setattr(shared, "BUCKET_NAME", BUCKET)
    monkeypatch.setattr(extract_module, "BUCKET_NAME", BUCKET)
    return fake


# ---------------------------------------------------------------------------
# Helpers — produce real PDF / DOCX bytes carrying a known string
# ---------------------------------------------------------------------------


def _make_pdf_bytes(text: str) -> bytes:
    """Build a minimal one-page PDF whose visible body equals ``text``.

    Uses a hand-rolled PDF structure rather than a heavy dependency, so the
    test exercises the real ``pypdf`` parser without pulling in ReportLab.
    Only ASCII text without parentheses is supported (sufficient for tests).
    """
    if any(ch in text for ch in "()\\\n"):
        raise ValueError("test PDF builder supports plain ASCII without () or newlines")

    objects = []

    def _add(obj_bytes: bytes) -> int:
        objects.append(obj_bytes)
        return len(objects)

    # Object 1: catalog
    catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
    _add(catalog)
    # Object 2: pages
    pages = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    _add(pages)
    # Object 3: page
    page = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )
    _add(page)
    # Object 4: content stream
    stream_text = (
        b"BT /F1 12 Tf 72 720 Td ("
        + text.encode("ascii")
        + b") Tj ET"
    )
    content_obj = b"<< /Length " + str(len(stream_text)).encode("ascii") + b" >>\nstream\n" + stream_text + b"\nendstream"
    _add(content_obj)
    # Object 5: font
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    _add(font)

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{i} 0 obj\n".encode("ascii"))
        buf.write(obj)
        buf.write(b"\nendobj\n")
    xref_offset = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.write(b"trailer\n")
    buf.write(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii"))
    buf.write(b"startxref\n")
    buf.write(f"{xref_offset}\n".encode("ascii"))
    buf.write(b"%%EOF\n")
    return buf.getvalue()


def _make_docx_bytes(paragraphs):
    """Build a real DOCX document with the given paragraphs, return bytes."""
    from docx import Document  # local import — only used by .docx tests

    document = Document()
    for p in paragraphs:
        document.add_paragraph(p)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# .md / .txt passthrough (Req 5.2)
# ---------------------------------------------------------------------------


def test_md_returns_body_directly_and_writes_extracted(s3):
    """``.md`` -> success, body returned as-is, extracted/.txt mirror written."""
    key = f"{UPLOADED_DIR}/spec.md"
    body = b"# Heading\n\nA paragraph with detail."
    s3.seed(BUCKET, key, body)

    result = extract_module.extract_document_text({"file_path": key})

    assert "isError" not in result, result
    assert result["content"][0]["type"] == "text"
    assert result["content"][0]["text"] == body.decode("utf-8")

    # The normalized text is also written under .../uploaded/extracted/.
    extracted_key = f"{UPLOADED_DIR}/extracted/spec.txt"
    assert s3.raw_body(BUCKET, extracted_key) == body


def test_txt_returns_body_directly_and_writes_extracted(s3):
    """``.txt`` is treated identically to ``.md`` — passthrough."""
    key = f"{UPLOADED_DIR}/notes.txt"
    body = b"plain text requirements"
    s3.seed(BUCKET, key, body)

    result = extract_module.extract_document_text({"file_path": key})

    assert "isError" not in result, result
    assert result["content"][0]["text"] == "plain text requirements"
    assert s3.raw_body(BUCKET, f"{UPLOADED_DIR}/extracted/notes.txt") == body


def test_passthrough_falls_back_to_lossy_decode_on_invalid_utf8(s3):
    """Non-UTF-8 ``.md`` bytes don't 500 — they decode with replacement."""
    key = f"{UPLOADED_DIR}/legacy.md"
    body = b"\xff\xfeRequirement\xff"  # invalid UTF-8 sequences
    s3.seed(BUCKET, key, body)

    result = extract_module.extract_document_text({"file_path": key})

    assert "isError" not in result, result
    assert "Requirement" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# .pdf extraction via real pypdf (Req 5.3)
# ---------------------------------------------------------------------------


def test_pdf_extraction_returns_text_and_writes_extracted(s3):
    pytest.importorskip("pypdf")

    key = f"{UPLOADED_DIR}/requirements.pdf"
    needle = "OnlineBookstoreRequirement"
    pdf_bytes = _make_pdf_bytes(needle)
    s3.seed(BUCKET, key, pdf_bytes)

    result = extract_module.extract_document_text({"file_path": key})

    assert "isError" not in result, result
    text = result["content"][0]["text"]
    # The unique string we put in the PDF survives the round-trip.
    assert needle in text

    extracted_key = f"{UPLOADED_DIR}/extracted/requirements.txt"
    extracted_body = s3.raw_body(BUCKET, extracted_key)
    assert extracted_body is not None
    assert needle in extracted_body.decode("utf-8")


# ---------------------------------------------------------------------------
# .docx extraction via real python-docx (Req 5.3)
# ---------------------------------------------------------------------------


def test_docx_extraction_returns_text_and_writes_extracted(s3):
    pytest.importorskip("docx", reason="python-docx required")

    key = f"{UPLOADED_DIR}/requirements.docx"
    paragraphs = [
        "First requirement: support search.",
        "Second requirement: support checkout.",
    ]
    docx_bytes = _make_docx_bytes(paragraphs)
    s3.seed(BUCKET, key, docx_bytes)

    result = extract_module.extract_document_text({"file_path": key})

    assert "isError" not in result, result
    text = result["content"][0]["text"]
    for p in paragraphs:
        assert p in text

    extracted_key = f"{UPLOADED_DIR}/extracted/requirements.txt"
    extracted_body = s3.raw_body(BUCKET, extracted_key)
    assert extracted_body is not None
    decoded = extracted_body.decode("utf-8")
    for p in paragraphs:
        assert p in decoded


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_missing_file_path_returns_error(s3):
    result = extract_module.extract_document_text({})
    assert result.get("isError") is True
    assert "file_path" in result["content"][0]["text"]


def test_unsupported_extension_returns_error(s3):
    key = f"{UPLOADED_DIR}/diagram.png"
    s3.seed(BUCKET, key, b"\x89PNG")
    result = extract_module.extract_document_text({"file_path": key})
    assert result.get("isError") is True
    assert ".png" in result["content"][0]["text"]


def test_no_extension_returns_error(s3):
    key = f"{UPLOADED_DIR}/README"
    s3.seed(BUCKET, key, b"hello")
    result = extract_module.extract_document_text({"file_path": key})
    assert result.get("isError") is True


def test_path_outside_uploaded_directory_rejected(s3):
    """Source key must live directly under an ``.../uploaded/`` directory."""
    key = "online_bookstore/aidlc-docs/inception/requirements/spec.md"
    s3.seed(BUCKET, key, b"# spec")
    result = extract_module.extract_document_text({"file_path": key})
    assert result.get("isError") is True
    assert "uploaded" in result["content"][0]["text"]


def test_missing_object_returns_404_message(s3):
    key = f"{UPLOADED_DIR}/nope.md"
    result = extract_module.extract_document_text({"file_path": key})
    assert result.get("isError") is True
    assert "not found" in result["content"][0]["text"].lower()


def test_pdf_extraction_failure_is_reported(s3):
    """Corrupt PDF bytes -> error response, no extracted file written."""
    pytest.importorskip("pypdf")

    key = f"{UPLOADED_DIR}/bad.pdf"
    s3.seed(BUCKET, key, b"not really a pdf")
    result = extract_module.extract_document_text({"file_path": key})

    assert result.get("isError") is True
    assert s3.raw_body(BUCKET, f"{UPLOADED_DIR}/extracted/bad.txt") is None


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def test_handler_dispatches_to_extract_document_text(s3):
    """The Lambda handler routes the namespaced tool name to our function."""
    import handler as lambda_handler

    key = f"{UPLOADED_DIR}/dispatch.md"
    body = b"dispatch body"
    s3.seed(BUCKET, key, body)

    class _Ctx:
        class client_context:
            custom = {"bedrockAgentCoreToolName": "file-operations___extract_document_text"}

    response = lambda_handler.handler({"file_path": key}, _Ctx())

    assert "isError" not in response, response
    assert response["content"][0]["text"] == body.decode("utf-8")
