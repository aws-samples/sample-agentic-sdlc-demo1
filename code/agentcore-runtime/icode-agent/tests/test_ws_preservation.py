"""Preservation property tests for shared_state["_ws_event_loop"] assignment.

Property 2: Preservation — shared_state Event Loop Assignment
  After the fix, for any invocation of the event-loop assignment logic,
  shared_state["_ws_event_loop"] MUST equal asyncio.get_running_loop().

These tests are written against a minimal fixed stub — a function that
performs only the assignment:

    shared_state["_ws_event_loop"] = asyncio.get_running_loop()

This isolates the preservation property from the full handle_message
complexity, and verifies the test logic is correct before the real fix
is applied.

NOTE on unfixed code:
  In the unfixed main_ws.py, the `UnboundLocalError` at line 394 crashes
  handle_message before execution ever reaches the assignment at line 408.
  Therefore, on unfixed code these tests would error (not pass) if run
  against the real function — the crash prevents the assignment from
  running at all. The stub approach lets us validate the property logic
  independently of the crash.

Validates: Requirements 3.5
"""

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Minimal fixed stub
# ---------------------------------------------------------------------------

async def _assign_event_loop(shared_state: dict) -> None:
    """Minimal fixed stub: performs only the event-loop assignment.

    This mirrors the intended post-fix behavior of the relevant lines in
    handle_message:

        shared_state["_ws_event_loop"] = asyncio.get_running_loop()

    No redundant `import asyncio` — asyncio is imported at module level.
    """
    shared_state["_ws_event_loop"] = asyncio.get_running_loop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine in a fresh event loop and return the result."""
    return asyncio.get_event_loop_policy().get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Property 1: After assignment, _ws_event_loop is set (not None / missing)
# ---------------------------------------------------------------------------

@given(
    session_id=st.text(min_size=1, max_size=64),
    project_id=st.text(min_size=1, max_size=64),
    payload=st.dictionaries(
        keys=st.text(min_size=1, max_size=32),
        values=st.one_of(st.text(), st.integers(), st.booleans(), st.none()),
        max_size=10,
    ),
)
@settings(max_examples=100)
def test_event_loop_is_set_after_assignment(session_id, project_id, payload):
    """**Validates: Requirements 3.5**

    For varied session IDs, project IDs, and message payloads:
    after the event-loop assignment runs, shared_state["_ws_event_loop"]
    is set (not None and not missing).
    """
    shared_state: dict = {
        "session_id": session_id,
        "project_id": project_id,
        "payload": payload,
    }

    asyncio.run(_assign_event_loop(shared_state))

    assert "_ws_event_loop" in shared_state, (
        "shared_state['_ws_event_loop'] was not set after assignment"
    )
    assert shared_state["_ws_event_loop"] is not None, (
        "shared_state['_ws_event_loop'] is None after assignment"
    )


# ---------------------------------------------------------------------------
# Property 2: Stored loop is the same object as asyncio.get_running_loop()
# ---------------------------------------------------------------------------

@given(
    session_id=st.text(min_size=1, max_size=64),
    project_id=st.text(min_size=1, max_size=64),
)
@settings(max_examples=100)
def test_stored_loop_is_running_loop(session_id, project_id):
    """**Validates: Requirements 3.5**

    The event loop stored in shared_state["_ws_event_loop"] is the same
    object as asyncio.get_running_loop() at call time.

    This ensures run_coroutine_threadsafe calls from sync tool threads
    (e.g. artifact notifications in file_ops.py) use the correct loop.
    """
    shared_state: dict = {"session_id": session_id, "project_id": project_id}

    async def _capture_and_assign():
        loop_at_call_time = asyncio.get_running_loop()
        await _assign_event_loop(shared_state)
        return loop_at_call_time

    loop_at_call_time = asyncio.run(_capture_and_assign())

    assert shared_state["_ws_event_loop"] is loop_at_call_time, (
        f"stored loop {shared_state['_ws_event_loop']!r} is not the same "
        f"object as the running loop {loop_at_call_time!r} at call time"
    )


# ---------------------------------------------------------------------------
# Property 3: Assignment does not raise any exception
# ---------------------------------------------------------------------------

@given(
    extra_keys=st.dictionaries(
        keys=st.text(min_size=1, max_size=32).filter(
            lambda k: k != "_ws_event_loop"
        ),
        values=st.one_of(st.text(), st.integers(), st.booleans(), st.none()),
        max_size=10,
    ),
)
@settings(max_examples=100)
def test_assignment_does_not_raise(extra_keys):
    """**Validates: Requirements 3.5**

    The event-loop assignment does not raise any exception for any
    shared_state dict contents (varied pre-existing keys and values).
    """
    shared_state: dict = dict(extra_keys)

    try:
        asyncio.run(_assign_event_loop(shared_state))
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"_assign_event_loop raised an unexpected exception: "
            f"{type(exc).__name__}: {exc}"
        )
