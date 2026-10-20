# Architecture

This page explains how the platform is put together — the major components, how a request flows through them, and how the workflow is governed. It is written for a mixed audience: architects and engineers who want the shape of the system, and non-engineers who want to understand what happens when they click a button.

For source-level depth, see the engineering docs in [`code/docs/`](../../code/docs/) — especially [`ARCHITECTURE.md`](../../code/docs/ARCHITECTURE.md), [`ai-agent-architecture.md`](../../code/docs/ai-agent-architecture.md), and [`security-model.md`](../../code/docs/security-model.md).

---

## The big picture

```text
                         ┌──────────────────────────────────────────────┐
   Browser (React UI)    │                   AWS                         │
   ┌───────────────┐     │                                              │
   │ Project list  │     │   ┌───────────────┐     ┌──────────────────┐ │
   │ Workspace     │◄────┼──►│  Project API  │◄───►│   S3 (state +    │ │
   │  - chat       │ HTTP │   │ (API GW +     │     │   artifacts)     │ │
   │  - files      │     │   │  Lambda)      │     └──────────────────┘ │
   │  - documents  │     │   └───────────────┘              ▲           │
   │  - review     │     │                                  │           │
   └──────┬────────┘     │   ┌───────────────────────────┐  │           │
          │ WebSocket    │   │  AgentCore Runtime        │  │ read/write│
          └──────────────┼──►│  (Strands orchestrator +  │──┘           │
                         │   │   stage agents)           │              │
   Sign in (Cognito) ────┼──►└──────────┬────────────────┘              │
                         │              │ MCP tools                     │
                         │   ┌──────────▼────────────┐                  │
                         │   │  AgentCore Gateway     │                  │
                         │   │  (MCP) ─► Lambda tools │──► S3, diagrams, │
                         │   └────────────────────────┘    doc extract  │
                         │   ┌────────────────────────┐                 │
                         │   │  AgentCore Memory       │                 │
                         │   └────────────────────────┘                 │
                         └──────────────────────────────────────────────┘
```

Two paths reach the backend:

1. **The Project API path (HTTP).** The React app calls a REST API for everything about a project's *record*: listing projects, creating one, reading it, advancing its handoff state, and minting short-lived upload URLs. This is the authoritative source of truth for who owns a project and what state it's in.
2. **The runtime path (WebSocket).** When you open a project workspace, the browser opens a WebSocket to the AgentCore Runtime. That's where the AI agents actually run — asking questions, generating artifacts, and streaming progress back into the chat.

Both paths read and write the same Amazon S3 bucket, which is the central store for project state and every generated artifact.

---

## Components

### Frontend (React + Vite + TypeScript)

Hosted on Amazon S3 and served through CloudFront. It provides:

- The **project list** (landing page) — every project, with a status badge showing where each one stands.
- The **project workspace** — a chat panel in the center, a file browser and progress panel on one side, and a document viewer on the other.
- Persona-specific controls — the BA's **requirements upload** control, the SA's **review panel** (approve / request changes), the Developer's **link repo & submit** control, the QA's **Run tests** control and review panel, the DevOps review panel, the **handoff card**, and the Maintenance Specialist's **operations dashboard**.

The UI reads a mirror of the workflow registry so it knows which actions to show for your persona and current state. It never decides what's *allowed* — it only decides what to *display*; the server enforces the rules.

### Project API (API Gateway + Lambda)

A Cognito-authorized REST API backed by S3. It owns the project record at `_platform/projects/{project_uuid}.json` and is the **authoritative RBAC and state-machine layer**:

- `GET /projects` — lists projects (visibility is broad by design; every persona can *see* all projects).
- `POST /projects` — creates a project (seeds the initial `draft` / Business-Analyst state).
- `GET /projects/{id}` — reads one project record.
- `POST /projects/{id}/transition` and `/reassign` — apply a handoff transition.
- `POST /projects/{id}/upload-url` — mints a short-lived (about 300 seconds) presigned S3 upload URL for the BA's requirements document.
- `POST /projects/{id}/link-repo` — links a Git repository to the project and triggers ingestion (Developer-only).
- `DELETE /projects/{id}` — deletes a project and its artifacts.

Every transition is checked two ways: the caller must own the project's current state (**HTTP 403** otherwise), and the requested transition must be legal in the state machine (**HTTP 422** otherwise). Writes are ETag-conditional, so two people acting at once can't corrupt a record.

### AgentCore Runtime (the agents)

The runtime is a container on Amazon Bedrock AgentCore. Inside it, a **Strands `GraphBuilder` orchestrator** wires the stage agents into a graph. Each node is a specialist agent for one stage (requirements analysis, user stories, NFR requirements, NFR design, application design, test planning, test execution, deployment planning, release, and so on). Conditional edges decide which stages run based on the project's scope and complexity, determined during workflow planning.

Between segments (BA → SA, SA → Developer, Developer → QA, QA → DevOps) the graph inserts a **handoff gate**. When the workflow reaches a gate, the current persona's session stops there, the state is persisted, and the UI shows a handoff card. The next persona reconnects in a fresh session and picks up from the next segment.

The runtime also enforces persona access based on the caller's Cognito groups. Because of how tokens propagate through AgentCore, the runtime's check is defense-in-depth — the Project API remains the authoritative gate.

### AgentCore Gateway (tools over MCP)

Agents don't touch AWS services directly. They call tools through the AgentCore Gateway using the **Model Context Protocol (MCP)**. The Gateway fronts Lambda-backed tools for:

- **File operations** on S3 — create folder, read, write, list, delete.
- **Presigned URLs** for uploads.
- **Document text extraction** — `.md`/`.txt` pass through, `.pdf` via `pypdf`, `.docx` via `python-docx`.
- **AWS architecture diagram generation.**
- Optional **Atlassian** (Jira / Confluence) integration.

### AgentCore Memory

Provides conversation memory (summarization plus semantic recall) so long sessions stay coherent, and so an interrupted session can resume without losing context.

### Storage (Amazon S3)

One bucket holds two things:

- **The project record** at `_platform/projects/{project_uuid}.json` — the authoritative handoff state and assignment history, written by the Project API.
- **The artifacts** under `{project_id}/aidlc-docs/...` — every generated document, plus a `aidlc-state.md` mirror of the runtime's shared state for resume durability.

See [Artifacts and Files](artifacts-and-files.md) for the full layout.

---

## How a project moves forward: the request flow

1. You sign in through Cognito (PKCE flow) and open a project.
2. The browser opens a **WebSocket** to the AgentCore Runtime and the orchestrator starts the stage agents for your persona's segment.
3. Agents call tools over **MCP** through the Gateway, which invokes Lambda targets (S3 file ops, diagram generation, document extraction).
4. Generated artifacts are written to **S3** and stream back to the UI, appearing in the file browser and document viewer.
5. At a **segment boundary**, the handoff gate fires: your session ends with a handoff card, and the Project API records the transition atomically. The next persona reconnects in a fresh session.

---

## Governance: one registry, enforced everywhere

The heart of the platform's predictability is a single **workflow registry** that defines personas, segments, states, and the legal transitions between them. It exists in three synchronized copies (the runtime, the Project API Lambda, and a frontend mirror) that are kept consistent by the build.

- The **runtime** uses it to build the orchestration graph and place handoff gates.
- The **Project API** uses it to decide whether a requested transition is legal.
- The **frontend** uses it to decide which buttons to show.

Because personas, states, and transitions are never hardcoded in three different places, the workflow stays coherent and is safe to extend.

---

## Security model (summary)

- **Authentication** is Amazon Cognito. The browser uses the PKCE flow; the agent-to-Gateway connection uses client credentials.
- **Persona** is chosen at sign-up, stored as a `custom:role` attribute, and mapped to a Cognito group by a post-confirmation Lambda trigger.
- **Authorization** for project actions is enforced by the Project API from the caller's verified Cognito groups — non-owners get 403, illegal transitions get 422.
- **Secrets** (per-project Atlassian credentials, the Cognito client secret) live in AWS Secrets Manager and are referenced by name, never logged.
- **Uploads** use short-lived presigned URLs constrained to a safe key prefix.

The full treatment is in [`code/docs/security-model.md`](../../code/docs/security-model.md).

---

## Infrastructure and deployment

Everything is defined as AWS CDK v2 (TypeScript) in the `cdk/` directory and deployed as a single stack named **`AIDLCPlatformWorkshop`**. The stack provisions the Cognito user pool, the S3 buckets, the CloudFront distribution, the AgentCore Runtime / Gateway / Memory, the Lambda tools, and the Project API. The agent container is built for ARM64 by AWS CodeBuild.

Deployment, prerequisites, and account-guard behavior are covered in the [User Guide](../user-guide.md) and [`code/docs/deployment-guide.md`](../../code/docs/deployment-guide.md).

---

## Extensibility

Two extension points are designed in:

- **Add a persona or workflow segment** by editing the workflow registry (persona, segment, states, and any request-changes edge), then syncing the three copies. The orchestrator derives its graph and handoff gates from the registry, so no orchestrator edit is needed to wire the state machine. (Producing artifacts for a new segment still requires implementing that segment's stage agents.)
- **Add a tool** by adding a Lambda target behind the Gateway and exposing it over MCP. See [`code/docs/guide-creating-new-tools.md`](../../code/docs/guide-creating-new-tools.md).

---

## Related deep-dive docs

- [`code/docs/ARCHITECTURE.md`](../../code/docs/ARCHITECTURE.md) — full architecture reference
- [`code/docs/ai-agent-architecture.md`](../../code/docs/ai-agent-architecture.md) — the orchestrator and agents
- [`code/docs/aidlc-stages-reference.md`](../../code/docs/aidlc-stages-reference.md) — every stage in detail
- [`code/docs/shared-state-schema.md`](../../code/docs/shared-state-schema.md) — the runtime's shared state