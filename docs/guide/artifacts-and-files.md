# Artifacts and Files

Everything the platform produces is a file in Amazon S3, visible in the project's file browser and readable in the document viewer. This page explains what gets generated, where it lives, and one naming detail worth knowing.

---

## A naming note: `agentic-sdlc` vs `aidlc-docs`

In the file browser, chat, and document viewer, the project's artifact folder is labeled **`agentic-sdlc`**. Under the hood, the actual S3 folder is named **`aidlc-docs/`**. This is a **display-only relabel** — the real storage keys never change.

Why it matters:

- When you're clicking around the UI, you'll see **`agentic-sdlc`**.
- When you (or a script, or a support engineer) look at the actual S3 keys, you'll see **`aidlc-docs/`**.
- The paths in the tables below are the **real S3 keys**. Mentally swap the top folder to `agentic-sdlc` when reading the UI.

---

## Two kinds of stored data

Per project, the platform stores two distinct things in the same S3 bucket:

1. **The project record** — one JSON file that is the authoritative source for handoff state and assignment history. Written and owned by the Project API.
2. **The artifacts** — all the generated documents, plus a state mirror. Written by the runtime agents.

| Key | Owner | Contents |
| --- | --- | --- |
| `_platform/projects/{project_uuid}.json` | Project API | Authoritative project record: current handoff state, owning role, and full assignment history. |
| `{project_id}/aidlc-docs/aidlc-state.md` | Runtime | A Markdown mirror of the runtime's shared state (including handoff), used for resume durability. |

---

## Artifacts by stage

Artifacts accumulate as the project moves through the stages. Here's what each stage writes, in order.

### Business Analyst segment

| Key | Stage | Contents |
| --- | --- | --- |
| `{project_id}/aidlc-docs/inception/requirements/uploaded/{file}` | BA upload | The original requirements document you uploaded. |
| `{project_id}/aidlc-docs/inception/requirements/uploaded/extracted/{file}.txt` | Document extraction | Plain-text extraction from an uploaded `.pdf` / `.docx`. |
| `{project_id}/aidlc-docs/inception/requirements/requirements.md` | Requirements Analysis | The functional and non-functional requirements. |
| `{project_id}/aidlc-docs/inception/requirements/requirement-verification-questions.md` | Requirements Analysis | The clarifying-question log. |
| `{project_id}/aidlc-docs/inception/reverse-engineering/*.md` | Reverse Engineering (brownfield) | Models of an existing codebase (business overview, architecture, APIs, interactions). |
| `{project_id}/aidlc-docs/inception/user-stories/stories.md` | User Stories | User stories with acceptance criteria. |
| `{project_id}/aidlc-docs/inception/user-stories/personas.md` | User Stories | User personas. |

### Solution Architect segment

| Key | Stage | Contents |
| --- | --- | --- |
| `{project_id}/aidlc-docs/inception/plans/execution-plan.md` | Workflow Planning | Which design stages will run, and why. |
| `{project_id}/aidlc-docs/inception/plans/execution-plan-diagram.md` | Workflow Planning | A flowchart of the planned workflow. |
| `{project_id}/aidlc-docs/inception/nfr-requirements/nfr-requirements.md` | NFR Requirements | Non-functional requirements covering performance, scalability, security, and operational concerns. |
| `{project_id}/aidlc-docs/inception/nfr-design/nfr-design.md` | NFR Design | Non-functional design addressing NFR requirements with architectural patterns and solutions. |
| `{project_id}/aidlc-docs/inception/application-design/*.md` | Application Design | Components, methods, services, dependencies, and the consolidated design (plus an AWS architecture diagram). |
| `{project_id}/aidlc-docs/inception/application-design/unit-of-work.md` | Units Generation | The units of work, with boundaries and responsibilities. |
| `{project_id}/aidlc-docs/inception/application-design/unit-of-work-dependency.md` | Units Generation | Dependencies between units. |
| `{project_id}/aidlc-docs/inception/application-design/unit-of-work-story-map.md` | Units Generation | Mapping of user stories to units. |

### Software Developer segment

| Key | Stage | Contents |
| --- | --- | --- |
| `{project_id}/aidlc-docs/construction/construction-brief.md` | Construction Handoff | The consolidated, ready-to-paste construction brief. |
| `{project_id}/aidlc-docs/construction/tasks/*` | Construction Handoff | One task file per unit of work. |
| `{project_id}/source/*` | Repository Ingestion | The ingested source code from the linked GitHub/GitLab repository. |

### QA Engineer segment

| Key | Stage | Contents |
| --- | --- | --- |
| `{project_id}/aidlc-docs/qa/test-plan.md` | Test Planning | Test planning document with traceability from requirements to test cases. |
| `{project_id}/aidlc-docs/qa/test-results.md` | Test Execution | Test execution results (written by CodeBuild test runner when "Run tests" is used). |
| `{project_id}/aidlc-docs/qa/qa-report.md` | QA Report | Final QA assessment and report on the tested code. |

### DevOps Engineer segment

| Key | Stage | Contents |
| --- | --- | --- |
| `{project_id}/aidlc-docs/devops/deployment-plan.md` | Deployment Planning | Deployment plan covering infrastructure, environments, and deployment strategies. |
| `{project_id}/aidlc-docs/devops/release-runbook.md` | Release | Release runbook and procedures for production deployment. |

> The exact file set varies by project. Optional stages (user stories, application design, units generation) only run when the project's scope and complexity call for them, so smaller projects produce fewer artifacts. See [`code/docs/aidlc-stages-reference.md`](../../code/docs/aidlc-stages-reference.md) for the full per-stage output list and the conditions that trigger each stage.

---

## Working with artifacts in the UI

- **File browser.** Shows everything under the project (as the `agentic-sdlc` folder). Click a file to open it.
- **Document viewer.** Renders Markdown inline, so you can read requirements, stories, design, and the construction brief without leaving the browser.
- **Streaming updates.** As agents write files, they appear in the browser in near real time — you don't need to refresh.

---

## Uploads

When a BA uploads a requirements document, the browser requests a short-lived presigned upload URL from the Project API and uploads the file straight to S3, under `{project_id}/aidlc-docs/inception/requirements/uploaded/`. The URL is valid for only about 300 seconds and is constrained to that upload path. PDFs and Word documents are then extracted to plain text automatically.

---

## Next

- [Business Analyst Walkthrough](walkthrough-business-analyst.md) — produces the requirements and stories artifacts.
- [Software Developer Walkthrough](walkthrough-software-developer.md) — consumes the construction brief.
- [`code/docs/shared-state-schema.md`](../../code/docs/shared-state-schema.md) — the runtime state that backs `aidlc-state.md`.
