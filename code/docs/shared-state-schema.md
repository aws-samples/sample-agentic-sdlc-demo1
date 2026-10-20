# SharedState Schema Reference

SharedState is the central data structure that flows through all 13 agents in the SDLC graph. It is a plain Python dictionary initialized in `code/agentcore-runtime/icode-agent/shared_state.py` and passed as `invocation_state` to every Strands graph node.

## Top-Level Schema

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `project` | `dict` | See below | Project metadata |
| `brownfield` | `bool` | `False` | Whether existing source code was detected |
| `stage_progress` | `dict[str, str]` | All `"not_started"` | Status of each of the 13 stages |
| `current_stage` | `str` | `"workspace_detection"` | Currently executing stage identifier |
| `current_phase` | `str` | `"inception"` | Current phase: `"inception"` or `"construction"` |
| `artifacts` | `dict` | See below | File paths to generated artifacts, organized by stage |
| `units` | `dict` | See below | Unit_of_Work items for the construction loop |
| `execution_plan` | `dict` | See below | Which stages to execute or skip |
| `depth_level` | `str` | `"standard"` | Analysis depth: `"minimal"`, `"standard"`, or `"comprehensive"` |
| `extensions` | `dict` | See below | Extension rules and enabled/disabled status |
| `approval` | `dict` | See below | Current approval gate state |
| `audit_buffer` | `list` | `[]` | Append-only audit trail entries |
| `request_analysis` | `dict` | See below | Analysis of the user's initial request |
| `handoff` | `dict` | See below | Handoff state mirror — `handoff_state`, `owning_role`, `assignment_history`, plus `pending` flag set at segment boundaries |

### Private Keys (not serialized to markdown)

| Key | Type | Description |
|-----|------|-------------|
| `_mcp_client` | `MCPClient` | Gateway MCP client instance. Injected by `main_ws.py`. |
| `_session_id` | `str` | S3 key prefix (project ID or session ID). Injected by `main_ws.py`. |
| `_session_manager` | `AgentCoreMemorySessionManager` or `None` | Memory session manager. Injected by `main_ws.py`. |
| `_websocket` | `WebSocket` | Starlette WebSocket instance for artifact notifications. Injected by `main_ws.py`. |
| `_ws_mode` | `bool` | When `True`, the `human_input` tool uses Strands interrupts instead of CLI stdin. Injected by `main_ws.py`. |
| `_ws_event_loop` | `asyncio.AbstractEventLoop` | Event loop for scheduling async WebSocket sends from sync tool threads. Injected by `main_ws.py`. |

## Nested Schemas

### `project`

| Key | Type | Default | Description | Written By |
|-----|------|---------|-------------|------------|
| `type` | `str` | `"greenfield"` | `"greenfield"` or `"brownfield"` | workspace_detection |
| `start_date` | `str` | ISO 8601 UTC | Session start timestamp | `initialize_shared_state` |
| `workspace_root` | `str` | `""` | S3 prefix or local path for the project workspace | `main_ws.py` |
| `languages` | `list[str]` | `[]` | Detected programming languages (e.g., `["Python", "TypeScript"]`) | workspace_detection |
| `build_system` | `str` | `""` | Detected build system (e.g., `"npm"`, `"pip"`, `"cargo"`) | workspace_detection |
| `project_structure` | `str` | `"empty"` | `"empty"`, `"library"`, `"monolith"`, or `"microservices"` | workspace_detection |
| `selected_project` | `str` | `""` | Display name of the project | `main_ws.py` |
| `is_new_project` | `bool` | `True` | Whether this is a new project (no prior state) | `initialize_shared_state` |

### `stage_progress`

A dict mapping each of the 13 stage identifiers to a status string.

Valid statuses: `"not_started"`, `"in_progress"`, `"completed"`, `"skipped"`

```python
{
    "workspace_detection": "completed",
    "reverse_engineering": "skipped",
    "requirements_analysis": "completed",
    "user_stories": "skipped",
    "workflow_planning": "in_progress",
    "application_design": "not_started",
    "units_generation": "not_started",
    "functional_design": "not_started",
    "nfr_requirements": "not_started",
    "nfr_design": "not_started",
    "infrastructure_design": "not_started",
    "code_generation": "not_started",
    "build_and_test": "not_started",
}
```

Updated by `tools/state.py:mark_stage_complete()` after each stage finishes.

### `artifacts`

Organized by stage. Values are relative file paths (S3 keys under the session prefix).

```python
{
    "reverse_engineering": {
        "business_overview": "",       # aidlc-docs/inception/reverse-engineering/business-overview.md
        "architecture": "",
        "code_structure": "",
        "api_documentation": "",
        "component_inventory": "",
        "technology_stack": "",
        "dependencies": "",
        "code_quality_assessment": "",
    },
    "requirements": {
        "questions_file": "",          # aidlc-docs/inception/requirements/requirement-verification-questions.md
        "requirements_doc": "",        # aidlc-docs/inception/requirements/requirements.md
    },
    "user_stories": {
        "stories": "",                 # aidlc-docs/inception/user-stories/stories.md
        "personas": "",                # aidlc-docs/inception/user-stories/personas.md
    },
    "workflow_planning": {
        "execution_plan": "",          # aidlc-docs/inception/plans/execution-plan.md
    },
    "application_design": {
        "components": "",              # aidlc-docs/inception/application-design/components.md
        "component_methods": "",
        "services": "",
        "component_dependency": "",
        "application_design": "",
    },
    "units_generation": {
        "unit_of_work": "",            # aidlc-docs/inception/application-design/unit-of-work.md
        "unit_dependency": "",
        "unit_story_map": "",
    },
    "construction": {
        # Dynamic: keyed by unit name
        # "auth-service": {
        #     "functional_design": {"business-logic-model.md": "aidlc-docs/construction/auth-service/functional-design/business-logic-model.md"},
        #     "nfr_requirements": {...},
        #     "nfr_design": {...},
        #     "infrastructure_design": {...},
        #     "code_generation_plan": "aidlc-docs/construction/plans/auth-service-code-generation-plan.md",
        # }
    },
    "build_and_test": {
        "build_instructions": "",      # aidlc-docs/construction/build-and-test/build-instructions.md
        "unit_test_instructions": "",
        "integration_test_instructions": "",
        "performance_test_instructions": "",
        "build_and_test_summary": "",
    },
}
```

### `units`

Tracks the per-unit construction loop.

| Key | Type | Default | Description | Written By |
|-----|------|---------|-------------|------------|
| `items` | `list[dict]` | `[]` | Ordered list of Unit_of_Work dicts | units_generation |
| `current_unit_index` | `int` | `0` | Index of the unit currently being processed | `orchestrator.advance_unit_loop` |
| `completed_units` | `list[str]` | `[]` | Names of units that have finished code generation | `orchestrator.advance_unit_loop` |

Each item in `units.items`:

```python
{
    "name": "auth-service",              # kebab-case identifier
    "display_name": "Auth Service",      # human-readable name
    "boundaries": "User authentication and authorization",
    "responsibilities": ["JWT token management", "Password hashing"],
    "stories": ["US-001", "US-002"],     # mapped user story IDs
    "dependencies": ["database-service"],# other unit names
    "design_stages_completed": [],       # tracks which design stages finished for this unit
    "code_generation_completed": False,
}
```

### `execution_plan`

Controls which stages the graph routes to.

| Key | Type | Default | Description | Written By |
|-----|------|---------|-------------|------------|
| `stages_to_execute` | `list[str]` | `[]` | Stage names that will run (always includes `code_generation`, `build_and_test`) | workflow_planning (`assess_stages`) |
| `stages_to_skip` | `dict[str, str]` | `{}` | `{stage_name: rationale}` for skipped stages | workflow_planning (`assess_stages`) |
| `per_unit_stages` | `list[str]` | `[]` | Design stages that run once per unit in the construction loop | workflow_planning (`assess_stages`) |

### `request_analysis`

| Key | Type | Valid Values | Written By |
|-----|------|-------------|------------|
| `clarity` | `str` | `"clear"`, `"vague"`, `"incomplete"` | requirements_analysis (`analyze_request`) |
| `request_type` | `str` | `"new_feature"`, `"bug_fix"`, `"refactoring"`, `"enhancement"`, `"migration"`, `"integration"` | requirements_analysis |
| `scope` | `str` | `"single_file"`, `"single_component"`, `"multiple_components"`, `"system_wide"` | requirements_analysis |
| `complexity` | `str` | `"trivial"`, `"simple"`, `"moderate"`, `"complex"` | requirements_analysis |

> **Persisted across sessions.** `request_analysis` (and `depth_level`) are serialized into `aidlc-state.md` (`## Request Analysis` section) and parsed back on restore. This matters at the BA → SA handoff: Workflow Planning's `assess_stages` derives which design stages are REQUIRED vs SKIP from `scope`/`complexity`, so if these were lost on the SA's fresh session the planner would wrongly skip Application Design / Units Generation.

### `handoff`

Mirrors the authoritative Project Store record (`_platform/projects/{uuid}.json`)
so the runtime carries collaboration metadata across session restarts. Seeded
in `initialize_shared_state` from the workflow registry's `initial_state` and
its owning role (design Section 6a).

| Key | Type | Default | Description | Written By |
|-----|------|---------|-------------|------------|
| `handoff_state` | `str` | `workflow_registry.initial_state()` (e.g. `"draft"`) | Current handoff state from the registry's state machine. | `main_ws.py` (sync from Project Store on session entry); `tools/state.py` |
| `owning_role` | `str` | `workflow_registry.owning_role(initial_state)` | Cognito group that owns the current state. Server-side RBAC re-checks this on every request. | Same as above. |
| `assignment_history` | `list[dict]` | `[]` | Append-only log of every transition. Each entry: `{from, to, actor, timestamp, note?}`. | Project API (authoritative); mirrored on session entry. |
| `pending` | `bool` | absent | Set to `True` by `handoff_gate__{seg}` immediately before the gate raises `InterruptException`, so a session restart can detect that a handoff fired even if the next persona has not yet called the Project API. Read by `main_ws.py` and the persistence helper. | `orchestrator.create_handoff_gate_node` and `main_ws._handle_handoff_interrupt`. |

The Project API is the authority for `handoff_state` / `owning_role` /
`assignment_history`; the runtime treats this section as a read-mostly
mirror except for `pending`, which is purely a local marker. The frontend
reads the same fields off the Project Store record (not off SharedState) to
render the handoff status badge.

### `extensions`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `loaded_rules` | `list[dict]` | `[]` | Extension rule dicts parsed from `.md` files |
| `enabled_status` | `dict[str, bool]` | `{}` | `{extension_name: True/False}` |

### `approval`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `awaiting_approval` | `bool` | `False` | Whether an approval gate is currently active |
| `current_gate_stage` | `str` | `""` | Stage name presenting the gate |
| `summary` | `str` | `""` | Summary text shown to the user |
| `options` | `list[str]` | `[]` | Available options (e.g., `["Request Changes", "Approve & Continue"]`) |
| `user_response` | `str` | `""` | The user's response text |

## State Lifecycle

1. **Initialization**: `initialize_shared_state(user_request)` creates a fresh dict with all defaults (`shared_state.py`, line 37).
2. **Runtime injection**: `main_ws.py` injects `_mcp_client`, `_session_id`, `_session_manager`, `_websocket`, `_ws_mode`, and `_ws_event_loop`.
3. **Stage transitions**: Each agent reads/writes its relevant keys. `mark_stage_complete(state, stage_name)` updates `stage_progress`, `current_stage`, `current_phase`, and persists to S3.
4. **Persistence**: `_state_to_markdown(state)` serializes to markdown. `_persist_via_gateway(mcp_client, session_id, md_content)` writes to `{session_id}/aidlc-docs/aidlc-state.md` in S3.
5. **Resumption**: `_load_via_gateway` reads the markdown from S3. `_markdown_to_state(content)` parses it back into a dict. `_find_resume_stage` determines where to continue.

## Validation

`validate_shared_state(state)` (`shared_state.py`, line 130) checks:
- All top-level keys exist with correct types
- `project` section has all required keys with correct types
- `stage_progress` has all 13 stages as string values
- `units` has `items` (list), `current_unit_index` (int), `completed_units` (list)
- `execution_plan` has `stages_to_execute` (list), `stages_to_skip` (dict), `per_unit_stages` (list)
- `extensions` has `loaded_rules` (list), `enabled_status` (dict)
- `approval` has all 5 keys with correct types
- `request_analysis` has all 4 keys as strings
- `artifacts` has all required sub-sections as dicts

Returns `True` if valid, `False` otherwise. Used by `main_ws.py` to decide whether to resume or reinitialize.

## Artifact Path Conventions

| Phase | Path Pattern | Example |
|-------|-------------|---------|
| Inception | `aidlc-docs/inception/{stage-name}/{filename}.md` | `aidlc-docs/inception/requirements/requirements.md` |
| Construction (per-unit) | `aidlc-docs/construction/{unit-name}/{stage-name}/{filename}.md` | `aidlc-docs/construction/auth-service/functional-design/business-logic-model.md` |
| Construction (plans) | `aidlc-docs/construction/plans/{unit-name}-{plan-type}.md` | `aidlc-docs/construction/plans/auth-service-code-generation-plan.md` |
| Construction (build) | `aidlc-docs/construction/build-and-test/{filename}.md` | `aidlc-docs/construction/build-and-test/build-instructions.md` |
| State file | `aidlc-docs/aidlc-state.md` | `my-project/aidlc-docs/aidlc-state.md` |
| Application code | `{path}` (workspace root, NOT under aidlc-docs) | `src/auth/handler.py` |

All paths are prefixed with `{session_id}/` when stored in S3 (e.g., `my-project/aidlc-docs/inception/requirements/requirements.md`).
