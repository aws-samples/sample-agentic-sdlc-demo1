"""Integration test for session resume. Set AIDLC_WS_URL and AIDLC_TOKEN."""
import asyncio
import json
import os
import time
import uuid

import pytest

WS_URL = os.environ.get("AIDLC_WS_URL", "")
TOKEN = os.environ.get("AIDLC_TOKEN", "")
MSG_TIMEOUT, WORKFLOW_TIMEOUT = 30, 600

pytestmark = pytest.mark.skipif(
    not WS_URL or not TOKEN, reason="Need AIDLC_WS_URL+AIDLC_TOKEN"
)


class WorkflowError(Exception):
    pass


class WSClient:
    def __init__(self, url, token, sid):
        self.url, self.token, self.session_id = url, token, sid
        self._ws = None
        self.messages = []
        self.stage_events = []

    async def connect(self):
        import websockets
        u = f"{self.url}?X-Amzn-Bedrock-AgentCore-Runtime-Session-Id={self.session_id}"
        self._ws = await websockets.connect(
            u,
            subprotocols=[
                f"base64UrlBearerAuthorization.{self.token}",
                "base64UrlBearerAuthorization",
            ],
            ping_interval=30, ping_timeout=120, close_timeout=5,
        )
        print(f"  [WS] Connected {self.session_id}")

    async def disconnect(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
            print("  [WS] Disconnected")

    async def send(self, d):
        print(f"  >>> {d[:120]}")
        await self._ws.send(d)

    async def send_json(self, o):
        await self.send(json.dumps(o))

    async def recv(self, timeout=MSG_TIMEOUT):
        raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        m = json.loads(raw)
        self.messages.append(m)
        t = m.get("type", "")
        if t == "stage_start":
            self.stage_events.append(m)
            print(f"  <<< stage_start: {m['node_id']}")
        elif t == "stage_complete":
            self.stage_events.append(m)
            print(f"  <<< stage_complete: {m.get('node_id', '?')}")
        elif t == "human_input":
            print(f"  <<< human_input: {m.get('prompt', '')[:80]}...")
        elif t == "approval_required":
            print(f"  <<< approval: {m.get('stage', '')}")
        elif t == "error":
            print(f"  <<< ERROR: {m.get('message', '')}")
            raise WorkflowError(m.get("message", "?"))
        elif t == "human_input_notify":
            print(f"  <<< notify: {m.get('message', '')[:80]}")
        elif t == "artifact_updated":
            pass
        elif t != "text_chunk":
            print(f"  <<< {t}")
        return m

    async def wait_for(self, pred, timeout=MSG_TIMEOUT, label=""):
        dl = time.time() + timeout
        while time.time() < dl:
            m = await self.recv(timeout=max(0.1, dl - time.time()))
            if pred(m):
                return m
        raise TimeoutError(f"Timeout: {label}")

    async def wait_approval(self, timeout=MSG_TIMEOUT):
        return await self.wait_for(
            lambda m: m.get("type") == "approval_required", timeout, "approval"
        )

    async def wait_human(self, timeout=MSG_TIMEOUT):
        return await self.wait_for(
            lambda m: m.get("type") == "human_input", timeout, "human_input"
        )

    def stages(self):
        return [e["node_id"] for e in self.stage_events if e["type"] == "stage_start"]


def _pid():
    return f"test_{uuid.uuid4().hex[:8]}"


async def _fresh(c, pid, name):
    """Send init message and handle Session Recovery or Project Confirmation."""
    await c.send_json(
        {"project_id": pid, "project_name": name, "request": "Start workflow"}
    )
    m = await c.wait_approval(timeout=MSG_TIMEOUT)
    if m["stage"] == "Session Recovery":
        print("  Clearing old session...")
        await c.send("Start Fresh \u2014 delete everything and restart")
        dl = time.time() + MSG_TIMEOUT
        while time.time() < dl:
            i = await c.recv(timeout=max(0.1, dl - time.time()))
            if i.get("type") == "stage_start":
                return
            if i.get("type") == "approval_required":
                await c.send(f"Yes \u2014 let's build {name}")
                return
        raise TimeoutError("Timeout after reset")
    elif m["stage"] == "Project Confirmation":
        await c.send(f"Yes \u2014 let's build {name}")


async def _drive_past_workflow_planning(c):
    """Drive the workflow through requirements + workflow_planning.

    Auto-answers all requirements questions with "1" and approves all gates.
    Returns once we see a stage_start AFTER workflow_planning's approval gate
    has been approved, meaning requirements_analysis and workflow_planning are
    both marked completed in stage_progress on S3.
    """
    print("  Driving past Workflow Planning...")
    wp_started = False
    wp_approved = False
    dl = time.time() + WORKFLOW_TIMEOUT
    while time.time() < dl:
        try:
            m = await c.recv(timeout=min(MSG_TIMEOUT, max(0.1, dl - time.time())))
        except (TimeoutError, asyncio.TimeoutError):
            print(f"  timeout, stages={c.stages()}")
            continue

        t = m.get("type", "")

        if t == "stage_start" and m["node_id"] == "workflow_planning":
            wp_started = True
            print("  Reached workflow_planning")

        # Once workflow_planning has been approved, any subsequent stage_start
        # means we've moved past it — time to disconnect.
        if t == "stage_start" and wp_approved:
            print(f"  Post-WP stage started: {m['node_id']} — ready to disconnect")
            return

        if t == "human_input":
            prompt = m.get("prompt", "")
            if "Approve & Continue" in prompt or "Please choose" in prompt:
                await c.send("Approve & Continue")
                if wp_started:
                    wp_approved = True
                    print("  Approved workflow_planning gate")
            else:
                await c.send("1")

        if t == "workflow_complete":
            raise WorkflowError(
                "Workflow completed before we could disconnect! "
                f"stages={c.stages()}"
            )

    raise TimeoutError(f"Never got past workflow_planning. stages={c.stages()}")


@pytest.mark.asyncio
async def test_resume_does_not_restart_from_scratch():
    """Drive through requirements + workflow_planning, disconnect, resume.

    After resume, the S3SessionManager should restore graph state so
    completed stages are skipped structurally. The key assertion: no
    human_input from requirements_analysis should appear after resume.
    """
    pid, name = _pid(), "Resume App"
    sid = f"{pid}_testuser_example_com"
    c1 = WSClient(WS_URL, TOKEN, sid)
    try:
        print(f"\n  pid={pid}")
        await c1.connect()
        await _fresh(c1, pid, name)

        # Drive through requirements_analysis and workflow_planning
        await _drive_past_workflow_planning(c1)
        print(f"  S1 stages: {c1.stages()}")

        # Disconnect — workspace_detection, requirements_analysis, and
        # workflow_planning are all completed in stage_progress.
        print("\n  Disconnect...")
        await c1.disconnect()
        await asyncio.sleep(3)

        # Reconnect and resume
        print("\n  Reconnect...")
        c2 = WSClient(WS_URL, TOKEN, sid)
        await c2.connect()
        await c2.send_json(
            {"project_id": pid, "project_name": name, "request": "Start workflow"}
        )
        r = await c2.wait_approval(timeout=MSG_TIMEOUT)
        assert r["stage"] == "Session Recovery", (
            f"Expected Session Recovery, got {r['stage']}"
        )
        await c2.send("Resume \u2014 continue where I left off")

        # Collect events after resume.
        # Skipped nodes still emit stage_start (the Graph fires it before
        # the hook), but they should NOT produce human_input events.
        print("\n  Checking resume behaviour...")
        resume_stages = []
        first_human_input = None
        dl = time.time() + 120
        while time.time() < dl:
            try:
                m = await c2.recv(timeout=15)
                t = m.get("type", "")
                if t == "stage_start":
                    resume_stages.append(m["node_id"])
                if t == "human_input":
                    current_stage = resume_stages[-1] if resume_stages else "unknown"
                    first_human_input = {
                        "stage": current_stage,
                        "prompt": m.get("prompt", ""),
                    }
                    break
                if t == "workflow_complete":
                    break
            except WorkflowError as e:
                pytest.fail(f"Crash on resume: {e}")
            except (TimeoutError, asyncio.TimeoutError):
                break

        await c2.disconnect()

        print(f"\n  Resume stages: {resume_stages}")
        if first_human_input:
            print(f"  First human_input stage: {first_human_input['stage']}")
            print(f"  Prompt preview: {first_human_input['prompt'][:100]}")
        else:
            print("  No human_input received")

        # ASSERTION: requirements_analysis must NOT have produced a human_input
        if first_human_input:
            prompt = first_human_input["prompt"]
            stage = first_human_input["stage"]
            assert not (
                stage == "requirements_analysis"
                or ("Requirements Analysis" in prompt and "Question" in prompt)
            ), (
                f"Resume re-asked requirements questions! "
                f"stage={stage}, prompt={prompt[:120]}"
            )
            # Also check workflow_planning wasn't re-asked
            assert not (
                stage == "workflow_planning"
                and "execution plan" in prompt.lower()
                and "Approve" in prompt
            ), (
                f"Resume re-ran workflow_planning! "
                f"stage={stage}, prompt={prompt[:120]}"
            )
        else:
            print("  No human_input received — skip appears to have worked")

    except WorkflowError as e:
        pytest.fail(f"Error: {e}")
    finally:
        await c1.disconnect()


@pytest.mark.asyncio
async def test_resume_does_not_crash():
    """Resume after disconnecting during requirements_analysis.

    Verifies that selecting 'Resume' does not crash the runtime.
    """
    pid, name = _pid() + "_c", "Crash App"
    sid = f"{pid}_testuser_example_com"
    c1 = WSClient(WS_URL, TOKEN, sid)
    try:
        print(f"\n  pid={pid}")
        await c1.connect()
        await _fresh(c1, pid, name)

        # Wait for the first human_input (requirements question)
        q = await c1.wait_human(timeout=WORKFLOW_TIMEOUT)
        print(f"  Q: {q.get('prompt', '')[:60]}...")
        await c1.send("1")

        # Disconnect mid-requirements
        print("\n  Disconnect...")
        await c1.disconnect()
        await asyncio.sleep(3)

        # Reconnect and resume
        print("\n  Reconnect...")
        c2 = WSClient(WS_URL, TOKEN, sid)
        await c2.connect()
        await c2.send_json(
            {"project_id": pid, "project_name": name, "request": "Start workflow"}
        )
        r = await c2.wait_approval(timeout=MSG_TIMEOUT)
        assert r["stage"] == "Session Recovery", (
            f"Expected Session Recovery, got {r['stage']}"
        )
        await c2.send("Resume \u2014 continue where I left off")

        # Just verify it doesn't crash — any non-error response is fine
        try:
            m = await c2.recv(timeout=15)
            assert m.get("type") != "error", f"Crash on resume: {m}"
            print(f"  First: type={m.get('type')}")
        except (TimeoutError, asyncio.TimeoutError):
            print("  No response 15s (ok)")

        await c2.disconnect()
    except WorkflowError as e:
        pytest.fail(f"Error: {e}")
    finally:
        await c1.disconnect()
