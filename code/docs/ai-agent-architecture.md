# AI Agent Architecture

This document describes the multi-agent system that powers Agentic SDLC Transformation: its agents, tools, orchestration pattern, state management, and end-to-end flows.

## System Overview

The system implements a multi-persona, AI-driven Software Development Life Cycle using the [Strands Agent SDK](https://github.com/strands-agents/sdk-python). It consists of specialist Stage agents orchestrated as a directed graph with conditional edges, partitioned into persona-owned segments (Business Analyst → Solution Architect) with an explicit handoff between roles. Each agent handles one SDLC stage, produces markdown artifacts in S3, and transitions to the next stage based on shared state. Inspired by the AWS AI-DLC methodology.

The orchestration pattern is a **conditional directed graph** with a **per-unit construction loop**. The graph is built using Strands `GraphBuilder` (`code/agentcore-runtime/icode-agent/orchestrator.py`).

## Agents

### Inception Phase (7 agents)

#### 1. Workspace Detection Agent
- File: `code/agentcore-runtime/icode-agent/agents/inception/workspace_detection.py`
- Role: Scans the workspace to determine if the project is greenfield (no code) or brownfield (existing code). Detects languages, build systems, and project structure.
- Tools: `scan_workspace`, `check_aidlc_state`, `create_state_file`
- Approval gate: No (proceeds automatically)
- Outputs: Sets `brownfield`, `project.type`, `project.languages`, `project.build_system`, `project.project_structure` in SharedState.

#### 1b. Workspace Detection Extension Agent
- File: `code/agentcore-runtime/icode-agent/agents/extensions/workspace_detection_ext.py`
- Role: Lightweight pass-through hook that runs immediately before Workspace Detection (the BA segment's graph entry node). It loads no external/demo context and calls no tools.
- Tools: none
- Activation condition: Always runs as the first node; emits a short "Initializing Workspace" summary and hands off to Workspace Detection.
- Approval gate: No (proceeds automatically)
- Outputs: None.

#### 2. Reverse Engineering Agent
- File: `code/agentcore-runtime/icode-agent/agents/inception/reverse_engineering.py`
- Role: Analyzes existing codebases (brownfield only). Generates 8 documentation artifacts covering business overview, architecture, code structure, APIs, components, tech stack, dependencies, and code quality.
- Tools: `analyze_codebase`, `generate_re_artifacts`, `write_artifact`, `present_approval_gate`, `process_approval_response`
- Conditional: Only executes when `brownfield=true` AND no RE artifacts exist.
- Outputs: 8 markdown files in `aidlc-docs/inception/reverse-engineering/`.

#### 3. Requirements Analysis Agent
- File: `code/agentcore-runtime/icode-agent/agents/inception/requirements_analysis.py`
- Role: Analyzes the user request for clarity, type, scope, and complexity. Generates a **focused** set of clarifying questions (the 4–5 most decision-critical, or only gap-filling questions when a requirements document was uploaded), collects answers one-at-a-time via human input, detects ambiguities, and produces a requirements document.
- Tools: `analyze_request`, `create_questions_file`, `read_answers`, `record_answer`, `detect_ambiguities`, `generate_requirements`, `collect_extension_config`, `load_extensions`, `present_approval_gate`, `process_approval_response`, `write_artifact`
- Approval gate: Yes
- Outputs: `requirement-verification-questions.md`, `requirements.md` in `aidlc-docs/inception/requirements/`. Sets `request_analysis` and `depth_level` in SharedState.

#### 4. User Stories Agent
- File: `code/agentcore-runtime/icode-agent/agents/inception/user_stories.py`
- Role: Assesses whether user stories add value (multi-factor evaluation), then generates stories with INVEST criteria and acceptance criteria.
- Tools: `assess_story_need`, `create_story_plan`, `generate_stories`, `read_answers`, `record_answer`, `detect_ambiguities`, `present_approval_gate`, `process_approval_response`, `write_artifact`
- Conditional: Only executes when `execution_plan.stages_to_execute` includes `user_stories`.
- Outputs: `stories.md`, `personas.md` in `aidlc-docs/inception/user-stories/`.

#### 5. Workflow Planning Agent
- File: `code/agentcore-runtime/icode-agent/agents/inception/workflow_planning.py`
- Role: Determines which downstream stages should execute or skip based on request complexity and scope. Generates an execution plan with a Mermaid flowchart.
- Tools: `load_prior_context`, `assess_stages`, `generate_execution_plan`, `generate_mermaid_diagram`, `validate_mermaid`, `present_approval_gate`, `process_approval_response`, `write_artifact`
- Approval gate: Yes (with "Add Skipped Stages" option)
- Outputs: `execution-plan.md`, `execution-plan-diagram.md` in `aidlc-docs/inception/plans/`. Populates `execution_plan.stages_to_execute` and `execution_plan.stages_to_skip` in SharedState.

#### 6. Application Design Agent
- File: `code/agentcore-runtime/icode-agent/agents/inception/application_design.py`
- Role: Generates high-level component definitions, method signatures, service definitions, and dependency relationships.
- Tools: `create_questions_file`, `read_answers`, `record_answer`, `detect_ambiguities`, `generate_design_artifacts`, `present_approval_gate`, `process_approval_response`, `write_artifact`
- Conditional: Only executes when in `stages_to_execute`.
- Outputs: 5 files (`components.md`, `component-methods.md`, `services.md`, `component-dependency.md`, `application-design.md`) in `aidlc-docs/inception/application-design/`.

#### 7. Units Generation Agent
- File: `code/agentcore-runtime/icode-agent/agents/inception/units_generation.py`
- Role: Decomposes the system into manageable Unit_of_Work items with boundaries, responsibilities, and story mappings.
- Tools: `decompose_units`, `generate_unit_artifacts`, `update_unit_list`, `present_approval_gate`, `process_approval_response`, `write_artifact`
- Conditional: Only executes when in `stages_to_execute`.
- Outputs: `unit-of-work.md`, `unit-of-work-dependency.md`, `unit-of-work-story-map.md` in `aidlc-docs/inception/application-design/`. Populates `units.items` in SharedState.

### Developer Phase (1 agent)

#### Construction Handoff Agent
- File: `code/agentcore-runtime/icode-agent/agents/construction/construction_handoff.py`
- Persona/segment: Software Developer (`dev_segment`); the single stage in the Developer segment.
- Role: Runs in the Developer's session after the SA→Dev handoff (`assigned_to_dev`). Reads the inception/design artifacts and assembles a **construction kickoff package** for an external AI-assisted IDE — it does NOT generate code in-platform.
- Tools: `assemble_construction_brief`
- Approval gate: No (proceeds automatically)
- Outputs: `aidlc-docs/construction/construction-brief.md` (one consolidated brief with a coding-agent prompt preamble) and one task file per Unit_of_Work under `aidlc-docs/construction/tasks/`.

### Construction Phase (6 agents, disabled)

> The following per-unit construction agents exist under `agents/construction/` but are **disabled** (commented out in `orchestrator.py` / `shared_state.py`). Construction is performed in the external IDE using the construction brief above, not by these agents.

Construction agents run in a per-unit loop. For each unit of work, the graph cycles through Functional Design → NFR Requirements → NFR Design → Infrastructure Design → Code Generation, then advances to the next unit.

#### 8. Functional Design Agent
- File: `code/agentcore-runtime/icode-agent/agents/construction/functional_design.py`
- Role: Generates functional design artifacts (business logic models, business rules, domain entities) for the current unit.
- Tools: `load_unit_context`, `create_questions_file`, `read_answers`, `record_answer`, `detect_ambiguities`, `generate_functional_artifacts`, `present_approval_gate`, `process_approval_response`, `write_artifact`
- Outputs: Files in `aidlc-docs/construction/{unit-name}/functional-design/`.

#### 9. NFR Requirements Agent
- File: `code/agentcore-runtime/icode-agent/agents/construction/nfr_requirements.py`
- Role: Assesses non-functional requirement categories (performance, security, scalability, reliability, maintainability, usability) and generates NFR documents.
- Tools: `assess_nfr_needs`, `create_questions_file`, `read_answers`, `record_answer`, `generate_nfr_requirements`, `present_approval_gate`, `process_approval_response`, `write_artifact`
- Conditional: Only for complex or system-wide scope.
- Outputs: Files in `aidlc-docs/construction/{unit-name}/nfr-requirements/`.

#### 10. NFR Design Agent
- File: `code/agentcore-runtime/icode-agent/agents/construction/nfr_design.py`
- Role: Generates NFR design patterns and logical component artifacts.
- Tools: `generate_nfr_design_artifacts`, `present_approval_gate`, `process_approval_response`, `write_artifact`
- Conditional: Only when NFR Requirements is also required.
- Outputs: Files in `aidlc-docs/construction/{unit-name}/nfr-design/`.

#### 11. Infrastructure Design Agent
- File: `code/agentcore-runtime/icode-agent/agents/construction/infrastructure_design.py`
- Role: Generates infrastructure mapping and deployment architecture.
- Tools: `generate_infra_artifacts`, `present_approval_gate`, `process_approval_response`, `write_artifact`
- Conditional: Only for complex or system-wide scope.
- Outputs: Files in `aidlc-docs/construction/{unit-name}/infrastructure-design/`.

#### 12. Code Generation Agent
- File: `code/agentcore-runtime/icode-agent/agents/construction/code_generation.py`
- Role: Generates application code and unit tests for each unit. Creates a plan with checkboxes, generates files, and marks steps complete.
- Tools: `create_code_plan`, `read_code_plan`, `generate_code`, `modify_existing_file`, `create_new_file`, `update_plan_checkbox`, `generate_tests`, `present_approval_gate`, `process_approval_response`, `write_artifact`
- Outputs: Code files in workspace root, plans in `aidlc-docs/construction/plans/`.

#### 13. Build and Test Agent
- File: `code/agentcore-runtime/icode-agent/agents/construction/build_and_test.py`
- Role: Generates build instructions, test instructions (unit, integration, performance, contract, security, e2e), and a summary.
- Tools: `generate_build_instructions`, `generate_test_instructions`, `generate_test_summary`, `present_approval_gate`, `process_approval_response`, `write_artifact`
- Outputs: Files in `aidlc-docs/construction/build-and-test/`.

## Cross-Cutting Tools

These tools are injected into every agent by the orchestrator (`orchestrator.py`, lines 490–510):

- `log_audit_entry` (`tools/audit.py`) — Mandatory audit logging for all agent interactions.
- `human_input` (`tools/human_input.py`) — Human-in-the-loop input collection via WebSocket or CLI.
- `record_answer` (`tools/questions.py`) — Persists user answers to question files in S3.

## Gateway Tool Dispatch

The file operations Lambda (`code/agentcore-gateway/lambda-tools/`) uses a modular structure:

```
lambda-tools/
├── handler.py          # Lambda entry point and tool dispatch
├── shared.py           # S3 client, env vars, response helpers
└── tools/              # One file per tool function
    ├── __init__.py
    ├── create_folder.py
    ├── read_file.py
    ├── write_file.py
    ├── list_files.py
    ├── delete_folder.py
    ├── get_presigned_url.py
    └── extract_document_text.py
```

`shared.py` exports `BUCKET_NAME`, `s3_client`, `success()`, and `error()`. All tool modules import from `shared`. The `handler.py` entry point imports tool functions from the `tools` package and dispatches based on the tool name extracted from the Gateway metadata.

## State Management

### SharedState Dictionary

All agents share a single `SharedState` dictionary (`code/agentcore-runtime/icode-agent/shared_state.py`) passed as `invocation_state` through the Strands graph. Key sections:

| Section | Purpose |
|---------|---------|
| `project` | Project metadata (type, languages, build system, workspace root) |
| `brownfield` | Boolean flag for existing code detection |
| `stage_progress` | Status of all 13 stages (`not_started`, `in_progress`, `completed`, `skipped`) |
| `current_stage` / `current_phase` | Current execution position |
| `artifacts` | File paths to all generated artifacts, organized by stage |
| `units` | Unit_of_Work items, current index, completed units list |
| `execution_plan` | Which stages to execute/skip, per-unit stages |
| `request_analysis` | Clarity, type, scope, complexity of the user request |
| `depth_level` | `minimal`, `standard`, or `comprehensive` |
| `approval` | Current approval gate state |
| `extensions` | Loaded extension rules and enabled/disabled status |
| `audit_buffer` | Append-only audit trail |

### Persistence

State is persisted to S3 as `{session_id}/aidlc-docs/aidlc-state.md` via the MCP Gateway after every stage transition (`tools/state.py`, `mark_stage_complete` function). The markdown format is human-readable and supports session resumption — if a session is interrupted, the state file is loaded on reconnect and execution resumes from the last completed stage.

### Session Resumption

The `main.py` module (`_find_resume_stage` function, line 37) walks the stage order and returns the first stage that is not `completed` or `skipped`. The WebSocket entrypoint (`main_ws.py`, line 150) detects existing progress and offers the user a choice to resume or start fresh.

### Memory

AgentCore Memory (STM + LTM) is configured with summarization and semantic strategies. The `AgentCoreMemorySessionManager` is created per WebSocket session (`main_ws.py`, `_create_session_manager`). Note: session persistence via Memory is not passed to individual graph nodes because Strands Graph does not support session persistence on graph nodes (see `orchestrator.py`, line 485 comment).

## Prompt Strategy

### System Prompts

Each agent has a `SYSTEM_PROMPT` constant defined in its module file. Prompts are static Python strings, not loaded from S3. They define:
- The agent's role and responsibilities
- Step-by-step execution instructions
- Tool usage requirements
- Output format requirements (e.g., `[STAGE_SUMMARY]` tags)
- Scope boundaries (agents must stop after their stage)

### Orchestrator Injections

The orchestrator appends three addenda to every agent's system prompt (`orchestrator.py`, lines 50–250):
1. **Skip-If-Completed Check** — Prevents re-execution of completed stages on resume.
2. **Stage Banner Addendum** — Requires agents to display a `[PROGRESS_BANNER]` at the start and a `[STAGE_SUMMARY]` at the end.
3. **Audit Addendum** — Mandates `log_audit_entry` calls for all interactions.

In interactive mode, a fourth addendum is injected:
4. **Human Input Addendum** — Detailed rules for `human_input` tool usage, question formatting, approval gate handoff, and answer persistence.

## Orchestration Flow

### Graph Structure

The graph is built in `build_sdlc_graph()` (`orchestrator.py`, line 460). Entry point is `workspace_detection`. Edges are conditional — each edge has a condition function that reads SharedState at evaluation time.

```
workspace_detection
  ├─[brownfield]──→ reverse_engineering ──→ requirements_analysis
  └─[not brownfield]──→ requirements_analysis
                          ├─[user_stories in plan]──→ user_stories ──→ workflow_planning
                          └─[skip user_stories]──→ workflow_planning
                                                    ├──→ application_design
                                                    ├──→ units_generation
                                                    ├──→ functional_design
                                                    └──→ code_generation
                                                          (priority routing)

application_design ──→ units_generation ──→ functional_design
                                              ├──→ nfr_requirements ──→ nfr_design ──→ infrastructure_design ──→ code_generation
                                              └──→ code_generation (skip NFR/infra)

code_generation
  ├─[more units]──→ functional_design (cycle back)
  └─[all done]──→ build_and_test
```

### Per-Unit Construction Loop

When multiple units of work are defined, the graph cycles: `functional_design → ... → code_generation → functional_design` for each unit. The `advance_unit_loop` function (`orchestrator.py`, line 280) increments `current_unit_index` and checks if all units are complete.

Max node executions is set to 100 (`orchestrator.py`, line 500) to prevent infinite loops (14 stages + up to 10 units × 6 construction stages ≈ 74 max).

## Worked Examples

### Example 1: Simple Greenfield Project (Minimal Scope)

User request: "Build a simple to-do list CLI app in Python"

1. **workspace_detection** — `scan_workspace` finds no files → `brownfield=false`, `project_structure=empty`. Creates initial state file.
2. **requirements_analysis** — `analyze_request` → clarity=clear, type=new_feature, scope=single_component, complexity=simple, depth=minimal. Generates 5–6 questions, collects answers, produces `requirements.md`. Approval gate → user approves.
3. **workflow_planning** — `assess_stages` → all conditional stages SKIP (simple scope). `stages_to_execute = [code_generation, build_and_test]`. Generates execution plan. Approval gate → user approves.
4. **code_generation** — Creates code plan, generates Python files (`todo.py`, `test_todo.py`), marks steps complete. Approval gate → user approves.
5. **build_and_test** — Generates `build-instructions.md`, `unit-test-instructions.md`, `integration-test-instructions.md`, `build-and-test-summary.md`. Approval gate → user approves.

Total stages executed: 5. Skipped: reverse_engineering, user_stories, application_design, units_generation, functional_design, nfr_requirements, nfr_design, infrastructure_design.

### Example 2: Complex System-Wide Project

User request: "Build a full-stack e-commerce platform with user auth, product catalog, shopping cart, payment processing, and admin dashboard"

1. **workspace_detection** — Greenfield, empty workspace.
2. **requirements_analysis** — scope=system_wide, complexity=complex, depth=comprehensive. 10+ questions covering all aspects. Produces detailed requirements.
3. **user_stories** — `assess_story_need` → should_generate=true (user-facing, multi-persona, complex logic, cross-team). Generates stories and personas.
4. **workflow_planning** — All 6 conditional stages REQUIRED. Generates full execution plan with Mermaid diagram.
5. **application_design** — Generates 5 design artifacts (components, methods, services, dependencies, overview).
6. **units_generation** — Decomposes into units: `auth-service`, `product-catalog`, `shopping-cart`, `payment-processing`, `admin-dashboard`. Registers 5 units.
7. **functional_design** (unit 1: auth-service) — Business logic, rules, domain entities for auth.
8. **nfr_requirements** (unit 1) — Security, performance, reliability requirements.
9. **nfr_design** (unit 1) — Design patterns for NFRs.
10. **infrastructure_design** (unit 1) — Infrastructure mapping.
11. **code_generation** (unit 1) — Generates auth service code + tests.
12. **functional_design** (unit 2: product-catalog) — Cycle back for next unit.
13–26. Repeat steps 7–11 for remaining 4 units.
27. **build_and_test** — Comprehensive build/test instructions.

Total stages executed: ~27 (5 units × 5 construction stages + 7 inception stages).

### Example 3: Brownfield Enhancement

User request: "Add a REST API layer to the existing Python data processing pipeline"

1. **workspace_detection** — Finds Python files → `brownfield=true`, languages=[Python], build_system=pip.
2. **reverse_engineering** — `analyze_codebase` scans existing files. Generates 8 RE artifacts documenting the existing codebase.
3. **requirements_analysis** — type=enhancement, scope=multiple_components, complexity=moderate.
4. **workflow_planning** — application_design=REQUIRED, functional_design=REQUIRED, code_generation=REQUIRED, build_and_test=REQUIRED. Others skipped.
5. **application_design** — Designs API components alongside existing pipeline.
6. **functional_design** — API endpoint specifications, request/response models.
7. **code_generation** — Uses `modify_existing_file` for existing files, `create_new_file` for new API files.
8. **build_and_test** — Build and test instructions.

### Example 4: Session Interruption and Resume

1. User starts a complex project. Stages 1–5 complete successfully.
2. WebSocket connection drops during stage 6 (application_design).
3. State is persisted to S3 with `stage_progress.application_design = "in_progress"`.
4. User reconnects. `main_ws.py` loads state from S3, detects progress.
5. Frontend shows resume/restart dialog. User chooses "Resume".
6. `_find_resume_stage` returns `application_design`. Graph resumes from there.
7. The application_design agent checks its skip-if-completed instruction, finds status is `in_progress`, and re-executes.

### Example 5: Approval Gate Rejection

1. Workflow Planning agent presents execution plan with 3 stages skipped.
2. User selects "Add Skipped Stages" at the approval gate.
3. `process_approval_response` returns `approved=false, action=request_changes`.
4. Agent incorporates feedback, re-runs `assess_stages` with overrides, regenerates the plan.
5. New approval gate presented. User selects "Approve & Continue".
6. `process_approval_response` returns `approved=true`. Stage marked complete.

## Error Handling and Retry

- **Session-level retry**: `main_interactive` (`main.py`, line 170) retries up to 5 times with exponential backoff (2s, 4s, 8s, 16s, 30s) on connection timeouts. State is persisted before each retry.
- **WebSocket reconnection**: The frontend (`WebSocketContext.tsx`) retries up to 3 times with exponential backoff (1s, 2s, 4s) on connection loss.
- **OAuth token retry**: `OAuthProvider` (`tools/oauth_provider.py`) retries token requests up to 3 times with exponential backoff.
- **State persistence**: `persist_state` is called after every stage transition and on error. If `_mcp_client` is unavailable, persistence is skipped with a warning (graceful degradation).
- **Tool errors**: Gateway tool calls return `{"isError": true}` on failure. `file_ops.py` raises `RuntimeError` or `FileNotFoundError` which agents handle in their tool logic.
