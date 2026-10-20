"""Integration Registry — central registry for ISV integration definitions.

Loaded once at module import time. Reads assetsSrc/isv-agents.json to populate
IntegrationDefinition objects and exposes get_agents_for_stage for hub nodes.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STAGE_HOOK_POINTS = {
    "post_requirements_analysis",
    "post_user_stories",
    "post_workflow_planning",
    "post_application_design",
    "post_units_generation",
    "post_functional_design",
    "post_code_generation",
    "post_build_and_test",
}

# ---------------------------------------------------------------------------
# Agent instance cache — keyed by (integration_id, stage_name)
# Agents are created once per process and reused across hub invocations.
# This avoids repeated object instantiation overhead.
# ---------------------------------------------------------------------------
_AGENT_CACHE: Dict[Tuple[str, str], object] = {}

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class IntegrationDefinition:
    id: str
    name: str
    description: str
    default_stages: List[str]
    agent_factories: Dict[str, Callable] = field(default_factory=dict)


# Module-level registry dict
INTEGRATIONS: Dict[str, IntegrationDefinition] = {}

# ---------------------------------------------------------------------------
# Registry operations
# ---------------------------------------------------------------------------

def register_integration(definition: IntegrationDefinition) -> None:
    """Add an IntegrationDefinition to the registry."""
    INTEGRATIONS[definition.id] = definition
    logger.info("Registered integration: %s", definition.id)


def _load_manifest() -> List[dict]:
    """Read and return the parsed isv-agents.json manifest.

    Raises RuntimeError (logged at CRITICAL) if the file is missing or invalid JSON.
    Logs a WARNING for any stage string not in VALID_STAGE_HOOK_POINTS.
    """
    manifest_path = (
        Path(__file__).parent.parent.parent / "isv-agents.json"
    )
    try:
        with open(manifest_path, "r") as f:
            entries = json.load(f)
    except FileNotFoundError:
        logger.critical("ISV agents manifest not found: %s", manifest_path)
        raise RuntimeError(f"ISV agents manifest not found: {manifest_path}")
    except json.JSONDecodeError as e:
        logger.critical("ISV agents manifest is not valid JSON: %s", e)
        raise RuntimeError(f"ISV agents manifest is not valid JSON: {e}")

    for entry in entries:
        for stage in entry.get("stages", []):
            if stage not in VALID_STAGE_HOOK_POINTS:
                logger.warning(
                    "Unrecognised stage hook point '%s' in manifest entry '%s'",
                    stage,
                    entry.get("id"),
                )

    return entries


def get_agents_for_stage(stage_name: str, state: dict, model) -> List[Tuple[str, object]]:
    """Return (integration_id, agent) tuples for all enabled integrations subscribed to stage_name.

    Agent instances are cached per (integration_id, stage_name) so the same Agent
    object is reused across hub invocations within the same container lifetime.
    """
    config = state.get("integrations", {}).get("config", {}).get("integrations", {})
    result = []
    for integration_id, definition in INTEGRATIONS.items():
        integration_config = config.get(integration_id, {})
        if integration_config.get("enabled") is not True:
            continue
        if stage_name not in definition.default_stages:
            continue
        factory = definition.agent_factories.get(stage_name)
        if factory is None:
            logger.warning(
                "No factory for stage '%s' in integration '%s'",
                stage_name,
                integration_id,
            )
            continue

        # Use cached agent if available, otherwise create and cache
        cache_key = (integration_id, stage_name)
        if cache_key not in _AGENT_CACHE:
            logger.info("Creating and caching agent for %s/%s", integration_id, stage_name)
            _AGENT_CACHE[cache_key] = factory(model)
        else:
            logger.info("Reusing cached agent for %s/%s", integration_id, stage_name)

        result.append((integration_id, _AGENT_CACHE[cache_key]))
    return result

# ---------------------------------------------------------------------------
# Atlassian factory imports
# ---------------------------------------------------------------------------

from agents.integrations.atlassian.requirements import create_atlassian_requirements_agent
from agents.integrations.atlassian.user_stories import create_atlassian_user_stories_agent
from agents.integrations.atlassian.workflow_planning import create_atlassian_workflow_planning_agent
from agents.integrations.atlassian.application_design import create_atlassian_application_design_agent
from agents.integrations.atlassian.units import create_atlassian_units_agent
from agents.integrations.atlassian.functional_design import create_atlassian_functional_design_agent
from agents.integrations.atlassian.code_generation import create_atlassian_code_generation_agent
from agents.integrations.atlassian.build_and_test import create_atlassian_build_and_test_agent

_ATLASSIAN_FACTORIES: Dict[str, Callable] = {
    "post_requirements_analysis": create_atlassian_requirements_agent,
    "post_user_stories": create_atlassian_user_stories_agent,
    "post_workflow_planning": create_atlassian_workflow_planning_agent,
    "post_application_design": create_atlassian_application_design_agent,
    "post_units_generation": create_atlassian_units_agent,
    "post_functional_design": create_atlassian_functional_design_agent,
    "post_code_generation": create_atlassian_code_generation_agent,
    "post_build_and_test": create_atlassian_build_and_test_agent,
}

# ---------------------------------------------------------------------------
# Auto-registration
# ---------------------------------------------------------------------------

def _register_all_integrations() -> None:
    """Load the manifest and register all allowed integrations."""
    entries = _load_manifest()
    for entry in entries:
        if not entry.get("allowed", False):
            continue
        integration_id = entry["id"]
        raw_stages = entry.get("stages", [])
        if not raw_stages:
            logger.warning(
                "Manifest entry '%s' has no 'stages' field — registering with empty default_stages",
                integration_id,
            )
        valid_stages = [s for s in raw_stages if s in VALID_STAGE_HOOK_POINTS]

        if integration_id == "atlassian":
            factories = _ATLASSIAN_FACTORIES
        else:
            factories = {}

        definition = IntegrationDefinition(
            id=integration_id,
            name=entry.get("name", integration_id),
            description=entry.get("description", ""),
            default_stages=valid_stages,
            agent_factories=factories,
        )
        register_integration(definition)


_register_all_integrations()
