"""Bug condition exploration tests for the UnboundLocalError in handle_message.

These tests confirm the structural bug exists in the UNFIXED code:
  - A redundant `import asyncio` inside handle_message (line ~407) causes Python
    to treat `asyncio` as a local variable for the entire function scope.
  - The reference at line ~394 (`asyncio.get_running_loop()`) fires before the
    local import is reached, raising UnboundLocalError on every invocation.

Test 1 (scope_isolation): Confirms Python scoping behavior independently.
  This test ALWAYS passes — it is a language-level invariant.

Test 2 (structural_inspection): Inspects main_ws.py source to confirm the
  structural bug condition exists: a local `import asyncio` inside handle_message
  AND a prior reference to `asyncio` before that import line.
  This test PASSES on unfixed code (bug condition is present) and FAILS after fix.

Validates: Requirements 1.1, 1.2
"""

import ast
import inspect
import textwrap
import types


# ---------------------------------------------------------------------------
# Test 1: Scope-isolation reproducer
# ---------------------------------------------------------------------------

def test_scope_isolation_unbound_local_error():
    """Confirm Python raises UnboundLocalError when a name is referenced before
    a local import of the same name within the same function scope.

    This is a language-level invariant and always passes regardless of the
    state of main_ws.py.

    Validates: Requirements 1.1, 1.2
    """
    # Build a minimal function that mirrors the bug pattern:
    #   1. reference `asyncio` before the local import
    #   2. then `import asyncio` inside the same function body
    source = textwrap.dedent("""\
        import asyncio as _asyncio

        def _buggy():
            # Reference asyncio before the local import — mirrors line 394
            loop = asyncio.get_running_loop()  # noqa: F821
            # Local import that shadows the module-level name — mirrors line 407
            import asyncio  # noqa: E402
            return loop
    """)

    namespace: dict = {}
    exec(compile(source, "<bug_reproducer>", "exec"), namespace)  # noqa: S102  # nosec B102 — exec used intentionally in test
    buggy_fn = namespace["_buggy"]

    raised = False
    try:
        buggy_fn()
    except UnboundLocalError as exc:
        raised = True
        assert "asyncio" in str(exc), (
            f"Expected UnboundLocalError mentioning 'asyncio', got: {exc}"
        )

    assert raised, (
        "Expected UnboundLocalError was NOT raised. "
        "Python scoping behavior may have changed — investigate."
    )


# ---------------------------------------------------------------------------
# Test 2: Structural inspection of main_ws.py
# ---------------------------------------------------------------------------

def _get_handle_message_source() -> str:
    """Return the source lines of handle_message from main_ws.py."""
    import importlib.util
    import os
    import sys

    # Locate main_ws.py relative to this test file
    tests_dir = os.path.dirname(__file__)
    main_ws_path = os.path.join(tests_dir, "..", "main_ws.py")
    main_ws_path = os.path.normpath(main_ws_path)

    with open(main_ws_path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_structural_bug_condition_exists():
    """Confirm the structural bug condition is present in the UNFIXED main_ws.py:

    1. A local `import asyncio` statement exists inside handle_message.
    2. A prior reference to `asyncio` (as an attribute access or name) exists
       BEFORE that import line within the same function body.

    This test PASSES on unfixed code (bug is present) and FAILS after the fix
    removes the redundant local import.

    Validates: Requirements 1.1, 1.2
    """
    source = _get_handle_message_source()
    tree = ast.parse(source)

    # Find the handle_message function definition
    handle_message_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_message":
            handle_message_node = node
            break

    assert handle_message_node is not None, (
        "Could not find `handle_message` async function in main_ws.py"
    )

    # Collect ALL asyncio-related nodes in handle_message (including nested scopes
    # since Python scoping applies to the whole function body):
    #   a) lines of any `import asyncio` statement
    #   b) lines of any `asyncio.*` attribute access
    local_import_lines: list[int] = []
    asyncio_reference_lines: list[int] = []

    for node in ast.walk(handle_message_node):
        # Detect `import asyncio` or `import asyncio as ...`
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "asyncio":
                    local_import_lines.append(node.lineno)

        # Detect `asyncio.something` (Attribute access where value is Name("asyncio"))
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "asyncio":
                asyncio_reference_lines.append(node.lineno)

    # Bug condition part 1: a local `import asyncio` must exist inside handle_message
    assert local_import_lines, (
        "BUG CONDITION NOT FOUND: No local `import asyncio` inside handle_message. "
        "The code may already be fixed — the redundant import has been removed."
    )

    local_import_line = min(local_import_lines)

    # Bug condition part 2: at least one `asyncio.*` reference must exist BEFORE
    # the local import line (this is what triggers UnboundLocalError at runtime)
    references_before_import = [
        line for line in asyncio_reference_lines if line < local_import_line
    ]

    assert references_before_import, (
        f"Found local `import asyncio` at line {local_import_line} inside "
        f"handle_message, but found no `asyncio.*` reference before it "
        f"(all references: {sorted(asyncio_reference_lines)}). "
        "Cannot confirm the UnboundLocalError trigger."
    )

    prior_reference_line = min(references_before_import)

    # Document the counterexample
    print(
        f"\nBug condition confirmed:\n"
        f"  - `asyncio` referenced at line {prior_reference_line} "
        f"(before local import)\n"
        f"  - local `import asyncio` at line {local_import_line}\n"
        f"  => UnboundLocalError will fire on every handle_message invocation."
    )
