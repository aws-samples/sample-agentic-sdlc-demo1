"""Path-safety tests for tools.file_ops._build_s3_key.

Verifies the centralized S3-key builder rejects path-traversal / absolute
paths so a caller-supplied relative path can never escape the per-session
``{session_id}/`` prefix, while leaving legitimate workspace paths intact.
"""

import os
import sys

import pytest

_ICODE_AGENT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ICODE_AGENT_DIR not in sys.path:
    sys.path.insert(0, _ICODE_AGENT_DIR)

from tools.file_ops import _build_s3_key

SID = "proj-123"


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("aidlc-docs/inception/requirements/requirements.md",
         "proj-123/aidlc-docs/inception/requirements/requirements.md"),
        ("./aidlc-docs/x.md", "proj-123/aidlc-docs/x.md"),
        # Interior ".." that stays within the tree is fine (collapses safely).
        ("aidlc-docs/sub/../audit.md", "proj-123/aidlc-docs/audit.md"),
        ("aidlc-docs/aidlc-state.md", "proj-123/aidlc-docs/aidlc-state.md"),
        (".", "proj-123/"),
    ],
)
def test_build_s3_key_allows_safe_paths(rel, expected):
    assert _build_s3_key(SID, rel) == expected


@pytest.mark.parametrize(
    "rel",
    [
        "../../sensitive/file.txt",
        "../secret",
        "..",
        "aidlc-docs/../../escape.txt",
        "/etc/passwd",
        "a/b/../../../etc/passwd",
        "..\\..\\windows\\system32",  # backslashes normalized to forward slashes
    ],
)
def test_build_s3_key_rejects_traversal(rel):
    with pytest.raises(ValueError, match="path traversal"):
        _build_s3_key(SID, rel)
