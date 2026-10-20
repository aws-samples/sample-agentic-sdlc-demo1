"""Integration Hub — factory for hub nodes used in the SDLC graph.

Each hub node discovers and runs all enabled integration agents for a given
stage, providing fail-soft error isolation and state persistence.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any

from strands.multiagent.base import MultiAgentBase, MultiAgentResult
from strands.types.event_loop import Metrics, Usage
from strands.multiagent.graph import Status

from config.model import create_model
from agents.integrations.registry import get_agents_for_stage
from tools.state import _persist_via_gateway, _state_to_markdown

logger = logging.getLogger(__name__)


def create_integration_hub_node(stage_name: str) -> MultiAgentBase:
    """Return a MultiAgentBase hub node for the given stage_name.

    The returned object is suitable for builder.add_node() — Strands GraphBuilder
    requires Agent or MultiAgentBase; plain functions are not supported.
    """

    class IntegrationHubNode(MultiAgentBase):
        """Hub node that runs all enabled integration agents for a stage."""

        async def invoke_async(
            self,
            task: Any,
            invocation_state: dict | None = None,
            **kwargs: Any,
        ) -> MultiAgentResult:
            start_time = time.time()

            # Extract shared state from invocation_state
            # main_ws.py passes shared_state directly as invocation_state
            state = invocation_state if invocation_state is not None else {}

            # 1. Log entry
            logger.info("[INTEGRATION_HUB] Entering: %s", stage_name)
            # Also print to stderr for visibility in all log levels
            import sys
            print(f"[INTEGRATION_HUB] Entering: {stage_name}", file=sys.stderr, flush=True)

            # Debug: log what's in the integration config
            integrations_config = state.get("integrations", {}).get("config", {})
            logger.info("[INTEGRATION_HUB] integrations.config keys: %s", list(integrations_config.keys()) if isinstance(integrations_config, dict) else integrations_config)
            print(f"[INTEGRATION_HUB] integrations.config keys: {list(integrations_config.keys()) if isinstance(integrations_config, dict) else integrations_config}", file=sys.stderr, flush=True)

            # 2. Create model
            model = create_model()

            # 3. Discover agents
            agents = get_agents_for_stage(stage_name, state, model)
            logger.info("[INTEGRATION_HUB] get_agents_for_stage returned %d agents for %s", len(agents), stage_name)
            print(f"[INTEGRATION_HUB] get_agents_for_stage returned {len(agents)} agents for {stage_name}", file=sys.stderr, flush=True)

            # 4. If no agents, skip
            if not agents:
                logger.info("[INTEGRATION_HUB] No integrations enabled for %s", stage_name)
                state.setdefault("stage_progress", {})[f"hub_{stage_name}"] = "skipped"
                return MultiAgentResult(
                    status=Status.COMPLETED,
                    execution_time=round((time.time() - start_time) * 1000),
                )

            # 5. Log count
            logger.info("[INTEGRATION_HUB] Running %d integrations for %s", len(agents), stage_name)

            # Inject HITL tools into each agent — the orchestrator injects these
            # for graph nodes, but hub-created agents need them added here.
            hitl_tools = []
            ws_bridge = state.get("_ws_human_input")
            if ws_bridge is not None:
                from tools import human_input as human_input_tool
                from tools.approval import present_approval_gate, process_approval_response
                hitl_tools = [human_input_tool, present_approval_gate, process_approval_response]

            # 6. Execute agents sequentially
            for integration_id, agent in agents:
                # Inject HITL tools if in interactive (WebSocket) mode
                if hitl_tools:
                    agent.tool_registry.process_tools(hitl_tools)
                logger.info("[INTEGRATION_HUB] Starting: %s", integration_id)
                try:
                    # Use invoke_async directly since we're already in an async context.
                    # Calling agent(...) uses run_async which creates a new thread with
                    # asyncio.run() — this deadlocks when the Atlassian agent calls
                    # human_input (which needs to schedule on the main event loop).
                    result = await agent.invoke_async(
                        f"Execute the {integration_id} integration for stage: {stage_name}. "
                        f"Project name: {state.get('project', {}).get('selected_project', '')}. "
                        f"Workspace root: {state.get('project', {}).get('workspace_root', '')}.",
                        invocation_state=state,
                    )
                    logger.info("[INTEGRATION_HUB] Completed: %s", integration_id)
                except Exception as exc:
                    logger.error(
                        "[INTEGRATION_HUB] %s FAILED: %s",
                        integration_id,
                        exc,
                        exc_info=True,
                    )
                    # Append error record
                    state.setdefault("integrations", {}).setdefault("errors", []).append({
                        "stage": stage_name,
                        "integration": integration_id,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    })
                    # Write audit entry directly to buffer (no tool context needed)
                    try:
                        audit_buffer = state.setdefault("audit_buffer", [])
                        audit_buffer.append({
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "stage": stage_name,
                            "entry_type": "integration_error",
                            "content": f"{integration_id} failed: {exc}",
                        })
                    except Exception:
                        pass
                    # Continue to next agent (fail-soft)

            # 7. Mark completed
            state.setdefault("stage_progress", {})[f"hub_{stage_name}"] = "completed"

            # 8. Persist state if mcp_client and session_id present
            mcp_client = state.get("_mcp_client")
            session_id = state.get("_session_id")
            if mcp_client and session_id:
                try:
                    md_content = _state_to_markdown(state)
                    _persist_via_gateway(mcp_client, session_id, md_content)
                    logger.info("[INTEGRATION_HUB] State persisted after %s", stage_name)
                except Exception as exc:
                    logger.error(
                        "[INTEGRATION_HUB] State persistence failed after %s: %s",
                        stage_name,
                        exc,
                    )

            # 9. Log exit
            logger.info("[INTEGRATION_HUB] Completed hub: %s", stage_name)

            return MultiAgentResult(
                status=Status.COMPLETED,
                execution_time=round((time.time() - start_time) * 1000),
            )

    node = IntegrationHubNode()
    node.name = f"integration_hub_{stage_name}"
    return node
