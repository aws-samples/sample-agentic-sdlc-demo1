# FAQ and Glossary

Quick answers to common questions, plus a glossary of the terms you'll run into across the platform.

---

## Frequently asked questions

### Does the platform write my application code?

No. The active workflow covers inception, design, and the developer handoff. It produces a **construction brief** that you take into an external AI-assisted IDE (Kiro, Claude Code, Cursor) to generate the code. Once you've implemented the code, you link your repository back to the platform, which ingests it for the QA and DevOps stages. This split is intentional — the platform's job is to get you to a construction-ready state, then bridge your implemented code back into the QA and deployment phases.

### Why can I see projects I can't act on?

Visibility is broad on purpose: every persona can *see* every project, which makes cross-team awareness and the operations dashboard possible. But you can only *act* on projects currently assigned to your persona. The action buttons only appear when it's your turn, and the server rejects out-of-turn actions.

### Can I play more than one persona?

Yes, but sign in as each persona in a separate browser profile or private window so the sessions stay independent. This is the easiest way to run a full BA → SA → Developer flow yourself.

### How many times can a project bounce back and forth?

As many times as needed. There's no cap on request-changes bounces (SA → BA, Dev → SA, QA → Dev, and DevOps → QA). Every bounce, including the note, is recorded in the project's history.

### How does the Git round-trip work?

After implementing code in your external IDE, you link your GitHub/GitLab repository to the project. The platform triggers a CodeBuild job that clones your repository and syncs the source code into S3. Once ingestion completes, the project automatically advances to the QA Engineer, who can then review both your code and the design artifacts to perform testing and quality assurance.

### What happens if my repository is private?

You can provide a personal access token when linking your repository. The token is securely stored in AWS Secrets Manager and used by the ingestion process to access your private repository. The token is never logged or displayed.

### What file types can I upload as requirements?

`.md`, `.txt`, `.pdf`, and `.docx`. PDFs and Word documents are automatically extracted to plain text, which becomes the agent's authoritative requirements baseline.

### Why does the file browser say `agentic-sdlc` but the docs mention `aidlc-docs`?

They're the same folder. `agentic-sdlc` is a display-only label in the UI; the real S3 folder is `aidlc-docs/`. See [Artifacts and Files](artifacts-and-files.md#a-naming-note-agentic-sdlc-vs-aidlc-docs).

### The agent is asking a lot of questions. How do I speed that up?

Give it more context up front. If you're the BA, upload a detailed requirements document or paste a thorough description. When the agent already has the information, it only asks about gaps and ambiguities instead of the full question set.

### Why do some stages not run?

Several stages are conditional. Workflow planning assesses the project's scope and complexity and decides which design stages are needed. Smaller, simpler projects skip stages like user stories, application design, or units generation. As the SA, you can override these decisions at the workflow-planning gate.

### I signed up but don't see any persona controls.

The role you chose at sign-up must be one of the recognized values, and the post-confirmation step runs only after email verification. If you were confirmed some other way, an administrator may need to add you to the matching Cognito group. See [Getting Started](getting-started.md#choosing-a-persona-self-signup).

### Where's the deployment guide?

Deploying and tearing down the platform is covered in the [User Guide](../user-guide.md) and [`code/docs/deployment-guide.md`](../../code/docs/deployment-guide.md). This guide set focuses on *using* the platform, not standing it up.

### Is my work saved if a session is interrupted?

Yes. The runtime mirrors its state to S3 (`aidlc-state.md`) and uses AgentCore Memory, so an interrupted session can resume. The authoritative handoff state lives in the project record, independent of any live session.

---

## Glossary

**AI-DLC (AI-Driven Development Lifecycle)** — the AWS methodology this platform is based on: AI does the execution, humans keep oversight and approve at gates.

**AgentCore** — Amazon Bedrock AgentCore. The managed service that hosts the agent Runtime, the Gateway (tools), and Memory.

**Approval gate** — a pause where an agent presents its output and waits for a human to approve before the workflow advances.

**Artifact** — any file the platform generates (requirements, stories, design, diagrams, the construction brief). Stored in S3, visible in the file browser.

**Assignment history** — the recorded log of every transition on a project (who, when, and any change-request note), part of the project record.

**Bounce** — a request-changes transition that sends a project backward to the previous persona (SA → BA, or Dev → SA).

**Construction brief** — the consolidated, ready-to-paste package the developer takes into an AI-assisted IDE. Contains all requirements, design, and units of work in a single document.

**Git round-trip** — the process where a developer links their repository to the platform, triggering ingestion of the source code back into the platform for QA and DevOps stages.

**Handoff** — passing a project from one persona to the next, at a segment boundary. Ends the current AI session and starts a fresh one for the next persona.

**Handoff gate** — the point in the workflow graph where a segment ends and the handoff card appears.

**Ingestion** — the CodeBuild process that clones a linked repository and syncs the source code into S3 for downstream QA and DevOps consumption.

**MCP (Model Context Protocol)** — the protocol agents use to call tools through the Gateway.

**NFR (Non-Functional Requirements)** — performance, scalability, security, and operational requirements produced by the Solution Architect.

**Persona** — a role a user signs in as: Business Analyst, Solution Architect, Software Developer, QA Engineer, DevOps Engineer, or Maintenance Specialist. Determines what you can see and do.

**Project record** — the authoritative JSON file (`_platform/projects/{uuid}.json`) holding a project's handoff state and history. Owned by the Project API.

**Segment** — the portion of the lifecycle owned by one persona (the BA segment, the SA segment, the Developer segment).

**Stage** — a single step within a segment (for example, Requirements Analysis or Application Design), each run by a specialist agent.

**State** — where a project sits in the handoff state machine (`draft`, `requirements_ready`, `assigned_to_sa`, and so on).

**Unit of work** — a loosely coupled, independently buildable piece the system is decomposed into during Units Generation.

**Workflow registry** — the single source of truth defining personas, segments, states, and legal transitions. Read by the runtime, the Project API, and the frontend.

---

## Next

- [Overview](overview.md)
- [Handoffs and Change Requests](handoffs-and-change-requests.md)
- [Architecture](architecture.md)
