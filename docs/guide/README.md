# Agentic SDLC Transformation — User Documentation

Welcome. This is the user-facing guide set for the Agentic SDLC Transformation platform — a multi-persona, agent-driven collaboration platform for the full software development lifecycle (inception, design, construction, QA, deployment, and operations), built on AWS.

These docs are written to be **shared with users**: product owners, business analysts, solution architects, developers, and operations. They focus on understanding and *using* the platform. If you're deploying or extending it, jump to the [engineering docs](#for-builders-and-operators).

---

## Start here

| If you want to… | Read |
| --- | --- |
| Understand what the platform is and who it's for | [Overview](overview.md) |
| See how it's built on AWS | [Architecture](architecture.md) |
| Sign in and find your way around | [Getting Started](getting-started.md) |

---

## Walkthroughs by role

Pick the guide for your persona:

- [Business Analyst](walkthrough-business-analyst.md) — create a project, produce requirements and user stories, hand off to the architect.
- [Solution Architect](walkthrough-solution-architect.md) — review the requirements, produce the design and units of work, hand off to the developer.
- [Software Developer](walkthrough-software-developer.md) — receive the construction brief, code in an AI-assisted IDE, and link the repository.
- [QA Engineer](walkthrough-qa-engineer.md) — test the ingested code with test planning, execution, and reporting.
- [DevOps Engineer](walkthrough-devops-engineer.md) — deploy the application with deployment planning and release management.
- [Maintenance Dashboard](walkthrough-maintenance-dashboard.md) — the read-only cross-project operations view.

---

## Cross-cutting topics

- [Handoffs and Change Requests](handoffs-and-change-requests.md) — the full state machine, forward moves, bounces, and how the rules are enforced and audited.
- [Artifacts and Files](artifacts-and-files.md) — what the platform generates, where it's stored, and the `agentic-sdlc` vs `aidlc-docs` naming.
- [FAQ and Glossary](faq-and-glossary.md) — quick answers and a reference for the terms used throughout.

---

## The workflow in one picture

```text
Business Analyst        Solution Architect              Software Developer              QA Engineer                 DevOps Engineer
──────────────── ──────────────────────────── ──────────────────────────── ──────────────────────────── ─────────────────────────
create ─► requirements ─► review ─► design ─► construction ─► link repo & ─► test plan ─► test execution ─► deployment ─► release ─► deployed
         + stories        (approve/          + NFR       brief     submit for QA     + results   + reporting    planning   + runbook
                           request changes)                            ⬇                                                       
                                                                  ingestion                                                    
                                                               (Git round-trip)                                               
```

Every arrow is a governed step: an AI agent does the work, a human approves, and the platform records the transition. Work only moves to the next persona through an explicit handoff.

---

## For builders and operators

This guide set intentionally stays at the user level. For deployment, configuration, and internals, see:

- [Project README](../../README.md) — the top-level project overview and quick start.
- [User Guide](../user-guide.md) — step-by-step deploy, login, and end-to-end walkthrough.
- [`code/docs/`](../../code/docs/) — 30+ deep technical docs, including:
  - [`ARCHITECTURE.md`](../../code/docs/ARCHITECTURE.md) and [`ai-agent-architecture.md`](../../code/docs/ai-agent-architecture.md)
  - [`aidlc-stages-reference.md`](../../code/docs/aidlc-stages-reference.md) — every stage in detail
  - [`security-model.md`](../../code/docs/security-model.md), [`infrastructure-overview.md`](../../code/docs/infrastructure-overview.md), [`deployment-guide.md`](../../code/docs/deployment-guide.md)
  - [`frontend-overview.md`](../../code/docs/frontend-overview.md), [`websocket-message-protocol.md`](../../code/docs/websocket-message-protocol.md), [`shared-state-schema.md`](../../code/docs/shared-state-schema.md)
