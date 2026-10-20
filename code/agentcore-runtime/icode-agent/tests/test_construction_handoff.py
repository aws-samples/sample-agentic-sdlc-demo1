"""Unit tests for the construction_handoff agent's pure helpers.

Covers the unit-of-work splitter and the unit slugifier used to produce the
per-unit task files. The Strands SDK is installed in the runtime venv, so the
module imports cleanly; these tests exercise only the pure parsing helpers.
"""

import os
import sys

_ICODE_AGENT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ICODE_AGENT_DIR not in sys.path:
    sys.path.insert(0, _ICODE_AGENT_DIR)

from agents.construction.construction_handoff import _slugify_unit, _split_units


def test_slugify_unit():
    assert _slugify_unit("Order Service") == "order-service"
    assert _slugify_unit("  Auth / Cognito  ") == "auth-cognito"
    assert _slugify_unit("") == "unit"


def test_split_units_h2():
    md = (
        "# Units of Work\n\n"
        "## Order Service\nHandles orders.\n\n"
        "## Catalog Service\nHandles catalog.\n"
    )
    units = _split_units(md)
    assert [n for n, _ in units] == ["Order Service", "Catalog Service"]
    assert "Handles orders." in units[0][1]
    assert units[1][1].startswith("## Catalog Service")


def test_split_units_falls_back_to_h3_when_no_h2():
    md = "### Unit A\nalpha\n\n### Unit B\nbeta\n"
    units = _split_units(md)
    assert [n for n, _ in units] == ["Unit A", "Unit B"]


def test_split_units_empty_when_no_headings():
    assert _split_units("just prose, no headings") == []
