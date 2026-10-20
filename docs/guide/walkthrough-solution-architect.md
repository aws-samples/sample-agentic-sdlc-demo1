# Solution Architect Walkthrough

As the Solution Architect (SA), you're the quality gate between requirements and construction. You review the BA's work, decide whether it's ready, and — once approved — produce the design: application architecture, an AWS architecture diagram, and a decomposition into units of work. Then you hand the project to the Software Developer.

**Your persona:** `system-architect`
**You own the project while it's in:** `assigned_to_sa`, `in_design`, `design_ready`, and `ready_for_dev`
**You receive from:** the Business Analyst
**You hand off to:** the Software Developer

---

## Before you start

Sign in as an SA (see [Getting Started](getting-started.md)). On the project list, look for a project with an **Assigned to you** badge — that's a project the BA has passed to you.

---

## Step 1 — Review the BA's artifacts

Open the assigned project. Above the chat you'll see the **Solution Architect review panel**. It loads the BA's deliverables directly from the project's shared storage:

- `requirements.md` — the functional and non-functional requirements.
- `stories.md` — the user stories with acceptance criteria.
- `personas.md` — the user personas.

Read them carefully — click between the tabs. You're deciding one thing: **is this a solid enough foundation to design against?**

---

## Step 2 — Approve or request changes

The review panel gives you two choices.

### Approve & continue

If the requirements are sound, click **Approve & continue**. This:

- Advances the project `assigned_to_sa → in_design`.
- Forwards your approval to the runtime so the design agents pick up the work.

### Request changes

If something is missing, ambiguous, or wrong, click **Request changes**. A note field appears — use it to tell the BA specifically what needs to change. Submitting:

- Advances the project `assigned_to_sa → requirements_ready` (ownership flips back to the BA).
- Records your note in the project's assignment history.

The BA sees a **Changes requested** badge, addresses your feedback, and reassigns the project to you. You then review again. This can repeat as many times as needed.

---

## Step 3 — Produce the design

After you approve, the workflow proceeds through the design stages. The agent asks clarifying design questions and pauses at approval gates, just like the BA segment.

| Stage | What it does | Runs when |
| --- | --- | --- |
| **Workflow Planning** | Decides which downstream design stages are required or skipped, based on the scope and complexity assessed during requirements. Produces an execution plan with a flowchart. | Always |
| **NFR Requirements** | Produces non-functional requirements covering performance, scalability, security, and operational concerns. | Always |
| **NFR Design** | Develops the non-functional design addressing the NFR requirements with specific architectural patterns and solutions. | Always |
| **Application Design** | Designs the component architecture — components, methods, services, dependencies — plus an AWS architecture diagram. | Multi-component / system-wide scope, or moderate/complex complexity |
| **Units Generation** | Decomposes the system into loosely coupled units of work, with dependency and story maps. | Multi-component or system-wide scope |
| **Inception Complete** | Delivers a completion summary and prepares the developer handoff. | Always |

At the workflow-planning gate you can **override** which stages run before proceeding — useful if you know the project is simpler or more involved than the automatic assessment suggests.

As the design artifacts are generated they appear in the file browser and document viewer. See [Artifacts and Files](artifacts-and-files.md) for the full list.

---

## Step 4 — Advance through the design states

As design work completes, the review panel surfaces the next forward action for you:

1. When design work reaches completion (`in_design`), click **Mark design ready** to advance `in_design → design_ready`.
2. From `design_ready`, click **Mark ready for development** to advance `design_ready → ready_for_dev`.

At this point the document viewer shows the full set of generated artifacts: requirements, user stories, personas, the workflow plan, the NFR requirements, the NFR design, the application design, the AWS architecture diagram, and the units of work.

---

## Step 5 — Hand off to the Software Developer

From `ready_for_dev`, the chat shows a handoff card:

> **Handoff: Solution Architect → Software Developer**  ·  **[ Assign to Software Developer ]**

Click **Assign to Software Developer**. This advances the project `ready_for_dev → assigned_to_dev` and passes ownership to the developer. The developer's session then assembles the construction brief (see the [Software Developer Walkthrough](walkthrough-software-developer.md)).

---

## Handling a bounce from the developer

A developer can send a project back to you if the design needs revision. When they do, the project returns to `design_ready` and ownership flips back to you. Address the design change, then re-advance the project to `ready_for_dev` and reassign to the developer.

---

## Tips

- **Be decisive at review.** Requesting changes is cheap and keeps quality high — use the note field to be specific so the BA can fix things in one pass.
- **Use the planning override.** If you know the project's real scope, adjust the stage plan at the workflow-planning gate rather than letting every optional stage run.
- **Check the diagram.** The AWS architecture diagram is one of the most useful artifacts for the downstream developer — make sure it reflects your intent before you hand off.

---

## Next

- [Software Developer Walkthrough](walkthrough-software-developer.md) — what the developer receives.
- [Handoffs and Change Requests](handoffs-and-change-requests.md) — the full state machine.
- [Artifacts and Files](artifacts-and-files.md) — what the design stages produce.
