# AI-DLC Stages Reference

A complete reference for every phase and stage in the AI-Driven Development Life Cycle (AI-DLC).

Each elaboration stage is AI-driven with a human approval gate before the workflow advances (the final Inception Complete stage is the exception — it just delivers a completion summary and ends). Stages marked **conditional** are included or skipped at runtime based on the project's scope and complexity, determined during Workflow Planning.

---

## End-to-End Flow

```
INCEPTION PHASE
  └─ Workspace Detection
  └─ Reverse Engineering          (brownfield only)
  └─ Requirements Analysis
  └─ User Stories                 (conditional)
  └─ Workflow Planning
  └─ Application Design           (conditional)
  └─ Units Generation             (conditional)
  └─ Inception Complete

CONSTRUCTION PHASE  ← repeats per unit of work
  └─ Functional Design            (conditional)
  └─ NFR Requirements             (conditional)
  └─ NFR Design                   (conditional)
  └─ Infrastructure Design        (conditional)
  └─ Code Generation              (always)
  ↺  (loop back for next unit)
  └─ Build & Test                 (always, once after all units)

OPERATIONS PHASE
  └─ Deploy & Monitor
```

---

## Phase 1: Inception

Transforms a business intent into a validated, structured set of artifacts — requirements, user stories, architecture designs, and a decomposed unit plan — ready to hand off to Construction. AI drives the elaboration; humans validate and approve at every step.

---

### Stage 1 — Workspace Detection

**Runs:** Always — first stage in every session

Scans the workspace to understand the starting point. Detects whether the project is **greenfield** (no existing code) or **brownfield** (existing codebase). Checks for a prior `aidlc-state.md` to resume an interrupted session. Populates project context — tech stack, existing files, project structure — into shared state so all downstream agents have accurate context.

**Outputs**
- `aidlc-docs/aidlc-state.md` — project state file (created or resumed)
- SharedState populated with `brownfield` flag, `workspace_root`, and project context

---

### Stage 2 — Reverse Engineering

**Runs:** Brownfield projects only — skipped for greenfield

Analyzes the existing codebase to produce human-readable models before any new development begins. Generates static models (components, responsibilities, relationships) and dynamic models (how components interact for key use cases). Gives AI accurate context about the existing system so it doesn't generate conflicting or duplicate code.

**Outputs** in `aidlc-docs/inception/reverse-engineering/`
- `business-overview.md` — high-level description of the existing system
- `architecture.md` — current architecture documentation
- `api-specifications.md` — existing API contracts
- `interaction-diagrams.md` — component interaction flows

---

### Stage 3 — Requirements Analysis

**Runs:** Always

The core elaboration stage. AI analyzes the user's intent, asks clarifying questions to resolve ambiguity, and produces a structured requirements document. Determines the project's **scope** (single component, multiple components, system-wide) and **complexity** (simple, moderate, complex) — these two values drive all downstream conditional routing decisions.

**Outputs** in `aidlc-docs/inception/requirements/`
- `requirements.md` — functional and non-functional requirements
- `requirement-verification-questions.md` — clarification Q&A log

---

### Stage 4 — User Stories

**Runs:** Conditional — requires 2 or more of the following factors:
- User-facing feature or enhancement
- Multi-persona system
- Complex business logic
- Cross-team scope

Generates user stories that act as contracts between the team and AI for what needs to be built. Stories follow the **INVEST** criteria (Independent, Negotiable, Valuable, Estimable, Small, Testable) and include acceptance criteria. Also produces user personas to ground the stories in real user needs.

**Outputs** in `aidlc-docs/inception/user-stories/`
- `stories.md` — user stories with acceptance criteria
- `personas.md` — user personas
- `story-plan.md` — story generation plan

---

### Stage 5 — Workflow Planning

**Runs:** Always

Decides which downstream stages are **REQUIRED** or **SKIP** based on the scope and complexity assessed in Requirements Analysis. Produces an execution plan with a Mermaid flowchart so the team can see exactly what will run and why. Users can override stage decisions at the approval gate before the workflow proceeds.

**Routing decisions made here**

| Stage | REQUIRED when |
|---|---|
| Application Design | scope is multi-component/system-wide OR complexity is moderate/complex |
| Units Generation | scope is multi-component or system-wide |
| Functional Design | complexity is moderate or complex |
| NFR Requirements | complexity is complex OR scope is system-wide |
| NFR Design | NFR Requirements is REQUIRED |
| Infrastructure Design | complexity is complex OR scope is system-wide |
| Code Generation | Always |
| Build & Test | Always |

**Outputs** in `aidlc-docs/inception/plans/`
- `execution-plan.md` — stage decisions with rationale
- `execution-plan-diagram.md` — Mermaid flowchart of the planned workflow

---

### Stage 6 — Application Design

**Runs:** Conditional — multi-component/system-wide scope OR moderate/complex complexity

Designs the high-level component architecture before any code is written. AI asks design clarification questions, resolves ambiguities, then produces five interconnected design artifacts. These become the authoritative reference for all Construction phase agents.

**Outputs** in `aidlc-docs/inception/application-design/`
- `components.md` — component definitions and responsibilities
- `component-methods.md` — method signatures for each component
- `services.md` — service layer definitions
- `component-dependency.md` — dependency relationships between components
- `application-design.md` — consolidated design overview

---

### Stage 7 — Units Generation

**Runs:** Conditional — multi-component or system-wide scope

Decomposes the system into loosely coupled **Units of Work** — self-contained work items analogous to DDD Subdomains or Scrum Epics. Each unit has clear boundaries, responsibilities, and story mappings, enabling parallel or sequential construction. The ordered unit list is registered in shared state to drive the Construction per-unit loop.

**Outputs** in `aidlc-docs/inception/application-design/`
- `unit-of-work.md` — detailed unit descriptions with boundaries and responsibilities
- `unit-of-work-dependency.md` — dependency relationships between units
- `unit-of-work-story-map.md` — mapping of user stories to units

---

### Stage 8 — Inception Complete

**Runs:** Always — final stage of Inception

Marks the end of the Inception phase. Delivers a completion summary recapping the generated artifacts, then the SA hands the project off to the Software Developer (the **"Assign to Software Developer"** card / `ready_for_dev → assigned_to_dev`). It does **not** present an approval gate or a "review vs. complete" choice.

---

## Developer Handoff

### Construction Handoff *(Software Developer)*

**Runs:** Always — the single stage of the Developer segment, after `assigned_to_dev`.

The Software Developer's session runs the `construction_handoff` agent, which reads the inception/design artifacts and assembles a **construction kickoff package**: a consolidated `aidlc-docs/construction/construction-brief.md` (with a ready-to-paste coding-agent prompt preamble) plus one task file per Unit_of_Work under `aidlc-docs/construction/tasks/`. The developer takes these into an AI-assisted IDE (Kiro / Claude Code / Cursor) for coding. The developer may bounce the project back to the SA for design changes (`assigned_to_dev → design_ready`), or mark it `development_complete`. No in-platform code generation runs — construction happens in the IDE.

---

## Phase 2: Construction (in-platform, disabled)

> The in-platform per-unit construction stages below exist as agent code but are **disabled**. With the Developer handoff above, construction is performed in the external IDE using the construction brief.

Implements each Unit of Work through a repeating per-unit loop. AI proposes designs and generates code; humans validate at each step. The loop runs until all units are complete, then Build & Test runs once across the full project.

---

### Stage 9 — Functional Design *(per unit)*

**Runs:** Conditional — moderate or complex complexity, once per unit

Models the business logic for the current unit independently of infrastructure concerns — the DDD domain model step. AI asks clarification questions, then produces business logic models, domain entities, and business rules. For units with a UI component, frontend component definitions are also generated.

**Outputs** in `aidlc-docs/construction/{unit-name}/functional-design/`
- `business-logic-model.md` — domain model for the unit
- `business-rules.md` — business rules and constraints
- `domain-entities.md` — entities, aggregates, value objects
- `frontend-components.md` — UI component definitions *(if applicable)*

---

### Stage 10 — NFR Requirements *(per unit)*

**Runs:** Conditional — complex complexity OR system-wide scope, once per unit

Identifies and documents the non-functional requirements relevant to the current unit. Assesses six NFR categories and generates requirements alongside technology stack decisions.

**NFR categories assessed**

| Category | When assessed |
|---|---|
| Security | Always |
| Maintainability | Always |
| Performance | Moderate or complex complexity |
| Scalability | Multi-component or system-wide scope |
| Reliability | Moderate or complex complexity |
| Usability | New feature or enhancement |

**Outputs** in `aidlc-docs/construction/{unit-name}/nfr-requirements/`
- `nfr-requirements.md` — non-functional requirements
- `tech-stack-decisions.md` — technology stack decisions with rationale

---

### Stage 11 — NFR Design *(per unit)*

**Runs:** Conditional — only when NFR Requirements ran, once per unit

Translates NFR requirements into concrete architectural design patterns. Selects and documents patterns such as CQRS, Circuit Breakers, event-driven design, and caching strategies. Produces Architecture Decision Records (ADRs) for team validation.

**Outputs** in `aidlc-docs/construction/{unit-name}/nfr-design/`
- `nfr-design.md` — design patterns and architectural decisions
- ADRs for each significant design choice

---

### Stage 12 — Infrastructure Design *(per unit)*

**Runs:** Conditional — complex complexity OR system-wide scope, once per unit

Maps the unit's components to specific AWS services and defines the deployment architecture. Produces infrastructure mapping and deployment configuration artifacts that feed directly into code generation.

**Outputs** in `aidlc-docs/construction/{unit-name}/infrastructure-design/`
- `infrastructure-mapping.md` — component-to-AWS-service mapping
- `deployment-architecture.md` — deployment architecture and configuration

---

### Stage 13 — Code Generation *(per unit)*

**Runs:** Always — once per unit

Generates application code for the current unit based on all prior design artifacts. First creates a numbered, checkbox-driven code generation plan and waits for human approval before writing any code. Handles both greenfield (new files) and brownfield (in-place modification of existing files). Generates unit tests alongside application code.

**Steps within this stage**
1. Create code generation plan with numbered steps and `[ ]` checkboxes
2. Human approves the plan
3. For each step: generate/modify code file, mark step `[x]`
4. Generate unit tests
5. Human approval gate — then loop back for next unit, or advance to Build & Test

**Outputs**
- Application code in workspace root (never in `aidlc-docs/`)
- `aidlc-docs/construction/plans/{unit-name}-code-generation-plan.md`
- Unit test files alongside application code

---

### Stage 14 — Build & Test

**Runs:** Always — once after all units complete

Generates comprehensive build and test instructions covering the full project. Core artifacts are always produced; additional test types are generated conditionally based on requirements and NFR artifacts from earlier stages.

**Core outputs** in `aidlc-docs/construction/build-and-test/`
- `build-instructions.md` — step-by-step build instructions
- `unit-test-instructions.md` — unit testing procedures
- `integration-test-instructions.md` — integration testing procedures
- `build-and-test-summary.md` — overview of all build and test procedures

**Conditional outputs**
- `performance-test-instructions.md` — when performance requirements exist
- `contract-test-instructions.md` — when API contracts are defined
- `security-test-instructions.md` — when security requirements exist
- `e2e-test-instructions.md` — when end-to-end testing is warranted

---

## Phase 3: Operations

Manages deployment, observability, and ongoing maintenance. AI analyzes telemetry, detects anomalies, and proposes remediations; humans validate and approve actions.

---

### Stage 15 — Deploy & Monitor

**Runs:** Always

Packages units into deployment artifacts (container images, Lambda functions, IaC stacks). Monitors metrics, logs, and traces post-deployment. Integrates with incident runbooks to propose scaling, tuning, or fault isolation actions when SLA violations are predicted or detected.

---

## Cross-Cutting Behaviors

These behaviors apply to every stage in the workflow.

| Behavior | Description |
|---|---|
| **Resume support** | Every stage checks `stage_progress` in shared state. If already `"completed"`, it skips immediately — sessions can be interrupted and resumed safely |
| **Audit logging** | Every stage logs all actions to `aidlc-docs/audit.md` |
| **Human approval gates** | Every stage presents its outputs and waits for explicit human approval before the workflow advances |
| **Atlassian sync** | After each major stage, an integration agent syncs artifacts to Confluence pages and Jira issues |
| **Ambiguity resolution** | Stages that ask clarifying questions detect vague or contradictory answers and resolve them before proceeding |
| **Conditional routing** | The workflow graph is dynamic — stage execution is determined at runtime by Workflow Planning based on scope and complexity |
