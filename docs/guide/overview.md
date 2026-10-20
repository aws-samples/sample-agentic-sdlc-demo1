# Overview

Agentic SDLC Transformation is a multi-persona, agent-driven collaboration platform for the full software development lifecycle — inception, design, construction, QA, deployment, and operations. It runs on AWS and lets specialized AI personas do the heavy lifting while humans stay in control of every decision that matters.

Instead of one chatbot trying to do everything, the platform models the way real delivery teams work: distinct roles own distinct phases, and work moves forward through explicit, governed handoffs.

> New here? Read this page, then jump to [Getting Started](getting-started.md) to sign in and run your first project.

---

## What it does

You bring a rough idea ("an event RSVP and check-in app," "an internal helpdesk," and so on). The platform walks it through a governed pipeline and produces a consistent, reviewable set of artifacts:

- **Requirements** — functional and non-functional, derived from your intent and any documents you upload.
- **User stories and personas** — contracts for what gets built, following INVEST criteria.
- **A workflow plan** — which design stages will run, and why, based on the project's scope and complexity.
- **Application design** — components, services, dependencies, and an AWS architecture diagram.
- **Units of work** — the system decomposed into loosely coupled, buildable pieces.
- **A construction brief** — a consolidated, ready-to-paste package a developer takes into an AI-assisted IDE to write the actual code.
- **Git round-trip** — the developer links their repository, and the platform ingests the code for QA and DevOps.
- **Test plan and results** — test planning, execution assessment, and QA reports on the ingested code.
- **Deployment plan and runbook** — deployment planning and release management for production deployment.

At each meaningful step, an AI agent drafts the work, asks clarifying questions, and then pauses at a human approval gate. Nothing advances until a person approves it.

---

## Who it's for

- **Product owners and business analysts** who want to turn intent into structured, reviewable requirements quickly.
- **Solution architects** who want a fast, standardized path from requirements to an AWS-targeted design.
- **Software developers** who want a clean, complete brief to hand to an AI coding assistant.
- **Engineering leaders and operations** who want cross-project visibility into where work stands.

If you are evaluating how agentic AI can accelerate cloud-native delivery without giving up human oversight, this platform is a working reference.

---

## The six personas

Each person signs in with a persona role chosen at sign-up. The UI only shows the actions your role is allowed to take.

| Persona | Owns | What they do |
| --- | --- | --- |
| **Business Analyst (BA)** | Inception / requirements | Creates the project, optionally uploads a requirements document, and works with the agent to produce requirements, user stories, and personas. Then assigns the project to the Solution Architect. |
| **Solution Architect (SA)** | Design | Reviews the BA's artifacts and either approves or requests changes. On approval, produces the NFR requirements, NFR design, application design, AWS architecture diagram, and units of work. Then assigns the project to the Software Developer. |
| **Software Developer (Dev)** | Construction handoff | Receives a packaged construction brief plus per-unit task files and takes them into an AI-assisted IDE to write the code. Links the repository and submits for QA. Can bounce the project back to the SA if the design needs changes. |
| **QA Engineer** | Testing | Receives the ingested code and produces test plans, runs test execution, and writes QA reports. Can run a CodeBuild test runner on the actual code. Advances to DevOps or bounces back to the developer. |
| **DevOps Engineer** | Deployment | Receives the tested code and produces deployment plans and release runbooks. Manages the deployment process through to the deployed state. Can bounce back to QA if issues are found. |
| **Maintenance Specialist** | (read-only) | Owns no phase. Sees a cross-project operations dashboard showing every project's current stage and assigned persona, repository status, and workload rollups across all six personas. |

The platform ships with these six personas configured. Adding a persona is a configuration change, not a code change — see [Architecture](architecture.md#extensibility).

---

## The lifecycle at a glance

A project moves forward through a fixed sequence of states. Each state has exactly one owning persona, and only that persona can advance it.

```text
draft ─► requirements_ready ─► assigned_to_sa ─► in_design ─► design_ready ─► ready_for_dev ─► assigned_to_dev ─► implementation_submitted ─► assigned_to_qa ─► in_qa ─► qa_passed ─► assigned_to_devops ─► in_deployment ─► deployed
 (BA)         (BA)                 (SA)            (SA)          (SA)            (SA)              (Dev)               (Dev)                    (QA)        (QA)      (QA)           (DevOps)           (DevOps)      (DevOps)

                                     │                                                              │                                                              │                            │
                                     └─ request changes ─► requirements_ready                       └─ request changes ─► design_ready                          └─ request changes ─► assigned_to_dev   └─ request changes ─► assigned_to_qa
                                        (SA sends back to BA)                                         (Dev sends back to SA)                                     (QA sends back to Dev)            (DevOps sends back to QA)
```

- **Forward** moves are the normal path from idea to deployed application.
- **Request-changes** moves are the supported "bounces": SA→BA, Dev→SA, QA→Dev, and DevOps→QA.

The full meaning of each state, and how transitions are enforced and audited, is covered in [Handoffs and Change Requests](handoffs-and-change-requests.md).

---

## Two principles behind the design

**AI-powered execution with human oversight.** Agents create detailed plans, actively ask clarifying questions, and defer the decisions that require business context to a human. You approve or redirect at every gate. This mirrors the AWS [AI-Driven Development Lifecycle (AI-DLC)](https://aws.amazon.com/blogs/devops/ai-driven-development-life-cycle/) methodology the platform is based on.

**Governed collaboration, not a free-for-all.** Roles, permissions, and legal state transitions are defined in one place (a workflow registry) and enforced server-side. A persona can only act on projects assigned to it, and only in ways the state machine allows. This keeps a multi-agent, multi-person workflow predictable and auditable.

---

## What it does not do

Setting expectations honestly:

- **It does not generate application code inside the platform.** The construction stage produces a construction brief that the developer takes to an external AI-assisted IDE (Kiro, Claude Code, Cursor, and so on). Code generation happens there, then the developer links their repository back into the platform for QA and DevOps stages.
- **The deeper construction stages exist but are disabled.** Agent code for functional design, infrastructure design, code generation, and build/test lives in the repository but is switched off in the running workflow. The Git round-trip pattern is used instead.
- **Real test execution uses a CodeBuild runner.** The QA Engineer can trigger actual test runs on the ingested code, but the test execution stage primarily produces test assessment documentation.

---

## Where to go next

- [Getting Started](getting-started.md) — deploy access, sign in, and orient yourself in the workspace.
- [Business Analyst Walkthrough](walkthrough-business-analyst.md) — create a project and produce requirements and stories.
- [Solution Architect Walkthrough](walkthrough-solution-architect.md) — review, design, and hand off.
- [Software Developer Walkthrough](walkthrough-software-developer.md) — turn the brief into code.
- [Maintenance Dashboard](walkthrough-maintenance-dashboard.md) — the read-only operations view.
- [Architecture](architecture.md) — how the pieces fit together on AWS.
