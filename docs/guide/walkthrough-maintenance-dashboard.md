# Maintenance Dashboard Walkthrough

The Maintenance Specialist is a read-only oversight role. It owns no phase of the workflow and performs no transitions. Instead, it gets a cross-project operations dashboard — a single view of every project, where it stands, and who's holding it.

**Your persona:** `maintenance-specialist`
**You own:** nothing (read-only)
**What you see:** a cross-project operations dashboard

---

## Before you start

Sign in as the Maintenance Specialist (see [Getting Started](getting-started.md)). You won't see create, assign, approve, or upload controls anywhere — that's expected. Your surface is the dashboard.

---

## Opening the dashboard

1. After signing in, look at the top-right of the project list. A **Dashboard** button appears — it's shown only to the `maintenance-specialist` group.
2. Click it to open the operations dashboard (`/dashboard`).

If a non-maintenance persona navigates to the dashboard URL directly, they get an "unavailable" message. The route is gated to your group.

---

## What the dashboard shows

The dashboard is a cross-project rollup built from the same project list the rest of the platform uses. For **every** project it shows:

- **Current stage** — a human-readable state, for example "Assigned to Solution Architect," "In QA," or "Deployed," covering all six personas in the lifecycle.
- **Assigned persona** — which role currently owns the project.
- **Repository** — the linked repository status, ingest status, and latest commit for projects that have reached the Git round-trip phase.
- **Per-persona workload** — how many projects each persona is holding across all six roles.
- **By-stage rollup** — how projects are distributed across the states in the full lifecycle.

This gives you an at-a-glance answer to "where is everything, and who's the bottleneck?" without touching any project.

---

## What you can and can't do

- **Can:** view every project's status and the aggregate rollups; use this to spot stalled work, uneven workloads, or projects waiting too long in one state.
- **Can't:** create, assign, approve, request changes, upload, or delete. The dashboard performs no transitions. It's purely observational.

This separation is intentional — operations oversight shouldn't be able to accidentally move someone else's work.

---

## Why visibility is broad but actions are gated

Across the whole platform, every persona can *see* every project (the project list is not owner-filtered), but only the owning persona can *act* on a project. The Maintenance Specialist takes that model to its natural end: full visibility, zero mutation. It reads the same broad project listing the other personas use, and simply presents it as an operations view.

---

## Next

- [Handoffs and Change Requests](handoffs-and-change-requests.md) — what each stage on the dashboard means.
- [Overview](overview.md) — how the personas fit together.
