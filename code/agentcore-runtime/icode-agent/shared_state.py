"""Shared state initialization and validation for the AI-SDLC Multi-Agent System."""

from datetime import datetime, timezone

import workflow_registry


# All 14 stages tracked in stage_progress
STAGE_NAMES = [
    "workspace_detection",
    "reverse_engineering",
    "requirements_analysis",
    "user_stories",
    "workflow_planning",
    "nfr_requirements",
    "nfr_design",
    "application_design",
    "units_generation",
    "inception_complete",
    "construction_handoff",
    "test_planning",
    "test_execution",
    "qa_report",
    "deployment_planning",
    "release",
    # "functional_design",
    # "nfr_requirements",
    # "nfr_design",
    # "infrastructure_design",
    # "code_generation",
    # "build_and_test",
]

# Human-readable display names for each stage (used in welcome/recovery messages).
STAGE_DISPLAY_NAMES = {
    "workspace_detection": "Workspace Detection",
    "reverse_engineering": "Reverse Engineering",
    "requirements_analysis": "Requirements Analysis",
    "user_stories": "User Stories",
    "workflow_planning": "Workflow Planning",
    "nfr_requirements": "NFR Requirements",
    "nfr_design": "NFR Design",
    "application_design": "Application Design",
    "units_generation": "Units Generation",
    "inception_complete": "Inception Complete",
    "construction_handoff": "Construction Handoff",
    "test_planning": "Test Planning",
    "test_execution": "Test Execution",
    "qa_report": "QA Report",
    "deployment_planning": "Deployment Planning",
    "release": "Release",
}

VALID_STAGE_STATUSES = {"not_started", "in_progress", "completed", "skipped"}
VALID_DEPTH_LEVELS = {"minimal", "standard", "comprehensive"}
VALID_PHASES = {"inception", "construction", "operations"}
VALID_PROJECT_TYPES = {"greenfield", "brownfield"}
VALID_PROJECT_STRUCTURES = {"monolith", "microservices", "library", "empty"}


def initialize_shared_state(user_request: str) -> dict:
    """Return a fully initialized SharedState dictionary.

    Args:
        user_request: The user's initial request string.

    Returns:
        A SharedState dictionary with all required keys initialized to defaults.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Seed handoff defaults from the workflow registry so the initial
    # handoff_state / owning_role stay consistent with the registry's single
    # source of truth (design Section 6a).
    initial_handoff_state = workflow_registry.initial_state()
    initial_owning_role = workflow_registry.owning_role(initial_handoff_state)

    return {
        "project": {
            "type": "greenfield",
            "start_date": now,
            "workspace_root": "",
            "languages": [],
            "build_system": "",
            "project_structure": "empty",
            "selected_project": "",
            "is_new_project": True,
        },
        "brownfield": False,
        "stage_progress": {stage: "not_started" for stage in STAGE_NAMES},
        "current_stage": "workspace_detection",
        "current_phase": "inception",
        "artifacts": {
            "project_context": {
                "summary": "",
            },
            "reverse_engineering": {
                "business_overview": "",
                "architecture": "",
                "code_structure": "",
                "api_documentation": "",
                "component_inventory": "",
                "technology_stack": "",
                "dependencies": "",
                "code_quality_assessment": "",
            },
            "requirements": {
                "questions_file": "",
                "requirements_doc": "",
            },
            "user_stories": {
                "stories": "",
                "personas": "",
            },
            "workflow_planning": {
                "execution_plan": "",
            },
            "application_design": {
                "components": "",
                "component_methods": "",
                "services": "",
                "component_dependency": "",
                "application_design": "",
            },
            "units_generation": {
                "unit_of_work": "",
                "unit_dependency": "",
                "unit_story_map": "",
            },
            "construction": {},
            "build_and_test": {
                "build_instructions": "",
                "unit_test_instructions": "",
                "integration_test_instructions": "",
                "performance_test_instructions": "",
                "build_and_test_summary": "",
            },
        },
        "units": {
            "items": [],
            "current_unit_index": 0,
            "completed_units": [],
        },
        "execution_plan": {
            "stages_to_execute": [],
            "stages_to_skip": {},
            "per_unit_stages": [],
        },
        "depth_level": "standard",
        "extensions": {
            "loaded_rules": [],
            "enabled_status": {},
        },
        "approval": {
            "awaiting_approval": False,
            "current_gate_stage": "",
            "summary": "",
            "options": [],
            "user_response": "",
        },
        "audit_buffer": [],
        "project_context": "",
        "request_analysis": {
            "clarity": "",
            "request_type": "",
            "scope": "",
            "complexity": "",
        },
        "handoff": {
            "handoff_state": initial_handoff_state,
            "owning_role": initial_owning_role,
            "assignment_history": [],  # list of {from,to,actor,timestamp,note}
        },
    }


def validate_shared_state(state: dict) -> bool:
    """Validate that a SharedState dictionary has all required keys with correct types.

    Args:
        state: The dictionary to validate.

    Returns:
        True if the state is valid, False otherwise.
    """
    if not isinstance(state, dict):
        return False

    # Required top-level keys and their expected types
    top_level_schema = {
        "project": dict,
        "brownfield": bool,
        "stage_progress": dict,
        "current_stage": str,
        "current_phase": str,
        "artifacts": dict,
        "units": dict,
        "execution_plan": dict,
        "depth_level": str,
        "extensions": dict,
        "approval": dict,
        "audit_buffer": list,
        "request_analysis": dict,
        "handoff": dict,
    }

    # Check all top-level keys exist with correct types
    for key, expected_type in top_level_schema.items():
        if key not in state:
            return False
        if not isinstance(state[key], expected_type):
            return False

    # Validate project section
    project = state["project"]
    project_keys = {
        "type": str,
        "start_date": str,
        "workspace_root": str,
        "languages": list,
        "build_system": str,
        "project_structure": str,
        "selected_project": str,
        "is_new_project": bool,
    }
    for key, expected_type in project_keys.items():
        if key not in project:
            return False
        if not isinstance(project[key], expected_type):
            return False

    # Validate stage_progress has all 14 stages
    stage_progress = state["stage_progress"]
    for stage in STAGE_NAMES:
        if stage not in stage_progress:
            return False
        if not isinstance(stage_progress[stage], str):
            return False

    # Validate units section
    units = state["units"]
    units_keys = {"items": list, "current_unit_index": int, "completed_units": list}
    for key, expected_type in units_keys.items():
        if key not in units:
            return False
        if not isinstance(units[key], expected_type):
            return False

    # Validate execution_plan section
    exec_plan = state["execution_plan"]
    exec_plan_keys = {
        "stages_to_execute": list,
        "stages_to_skip": dict,
        "per_unit_stages": list,
    }
    for key, expected_type in exec_plan_keys.items():
        if key not in exec_plan:
            return False
        if not isinstance(exec_plan[key], expected_type):
            return False

    # Validate extensions section
    extensions = state["extensions"]
    extensions_keys = {"loaded_rules": list, "enabled_status": dict}
    for key, expected_type in extensions_keys.items():
        if key not in extensions:
            return False
        if not isinstance(extensions[key], expected_type):
            return False

    # Validate approval section
    approval = state["approval"]
    approval_keys = {
        "awaiting_approval": bool,
        "current_gate_stage": str,
        "summary": str,
        "options": list,
        "user_response": str,
    }
    for key, expected_type in approval_keys.items():
        if key not in approval:
            return False
        if not isinstance(approval[key], expected_type):
            return False

    # Validate request_analysis section
    request_analysis = state["request_analysis"]
    ra_keys = {"clarity": str, "request_type": str, "scope": str, "complexity": str}
    for key, expected_type in ra_keys.items():
        if key not in request_analysis:
            return False
        if not isinstance(request_analysis[key], expected_type):
            return False

    # Validate handoff section
    handoff = state["handoff"]
    handoff_keys = {
        "handoff_state": str,
        "owning_role": str,
        "assignment_history": list,
    }
    for key, expected_type in handoff_keys.items():
        if key not in handoff:
            return False
        if not isinstance(handoff[key], expected_type):
            return False

    # Validate artifacts section has required sub-sections
    artifacts = state["artifacts"]
    required_artifact_sections = [
        "project_context",
        "reverse_engineering",
        "requirements",
        "user_stories",
        "workflow_planning",
        "application_design",
        "units_generation",
        "construction",
        "build_and_test",
    ]
    for section in required_artifact_sections:
        if section not in artifacts:
            return False
        if not isinstance(artifacts[section], dict):
            return False

    return True
