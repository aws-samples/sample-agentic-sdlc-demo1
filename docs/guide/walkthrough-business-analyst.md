# Business Analyst Walkthrough

As the Business Analyst (BA), you start the project. You turn a rough idea — optionally backed by a requirements document — into structured requirements, user stories, and personas, then hand the project to the Solution Architect.

**Your persona:** `requirements-analyst`
**You own the project while it's in:** `draft` and `requirements_ready`
**You hand off to:** the Solution Architect

---

## Before you start

Sign in as a BA (see [Getting Started](getting-started.md)). You'll know it worked when the project list shows a **Create Project** button and, inside a project, an **Upload requirements document** control appears above the chat.

Have your idea ready. A good starting idea is small enough to finish inception quickly but rich enough to exercise the workflow — for example an event RSVP and check-in app, an internal helpdesk/ticketing tool, or a document-upload-with-AI-summary service.

---

## Step 1 — Create the project

1. On the project list, click **Create Project**.
2. Enter a **name** (for example, `Online Bookstore`) and a short **description**.
3. Click **Create**.

The project opens in its workspace in the `draft` state, owned by you.

---

## Step 2 — (Optional) Upload a requirements document

If you already have requirements written down, upload them so the agent uses them as its baseline instead of asking you everything from scratch.

1. Use the **Upload requirements document** control above the chat.
2. Choose a `.md`, `.txt`, `.pdf`, or `.docx` file.

What happens behind the scenes:

- The browser checks the file extension, then requests a short-lived upload URL from the Project API and uploads the file directly to S3.
- PDFs and Word documents are converted to plain text automatically (via the Gateway's document-extraction tool). The extracted text becomes the agent's authoritative requirements baseline.

You can skip this step entirely and describe your idea in chat instead — the agent will ask a fuller set of clarifying questions.

---

## Step 3 — Run the inception stages

The chat greets you and asks whether to start. Confirm, and the agent works through the BA segment:

| Stage | What it does | Runs when |
| --- | --- | --- |
| **Workspace Detection** | Establishes the starting point (new vs. existing project) and sets up state. | Always |
| **Reverse Engineering** | Analyzes an existing codebase to model it before new work begins. | Existing (brownfield) projects only |
| **Requirements Analysis** | Asks clarifying questions and produces the requirements document. Also assesses scope and complexity, which drive later routing. | Always |
| **User Stories** | Produces user stories (INVEST criteria, acceptance criteria) and personas. | When the project is user-facing / multi-persona / complex |

A few things to expect while it runs:

- **If you uploaded a document,** the requirements stage reads it first and only asks about gaps and ambiguities. **If you didn't,** it asks the full clarifying-question set.
- **Questions come one at a time.** Answer each to shape the result. Be specific — the quality of the requirements tracks the quality of your answers.
- **Approval gates.** After a stage produces its output, the agent pauses and asks you to approve before moving on. Review the artifact in the document viewer, then approve (or ask for adjustments).

As artifacts are generated, they appear in the file browser (under the **`agentic-sdlc`** folder) and you can open them in the document viewer. See [Artifacts and Files](artifacts-and-files.md) for exactly what's produced and where it lives.

---

## Step 4 — Hand off to the Solution Architect

Once requirements and user stories are generated and approved, the workflow reaches the **handoff gate**. The chat shows a card:

> **Handoff: Business Analyst → Solution Architect**  ·  **[ Assign to Solution Architect ]**

Click **Assign to Solution Architect**. This:

- Advances the project `requirements_ready → assigned_to_sa`.
- Flips ownership to the Solution Architect.
- Ends your WebSocket session.

On the project list, the project now shows an **Awaiting Solution Architect** badge. Your part is done unless the SA sends it back.

---

## Handling a bounce (changes requested)

If the SA reviews your artifacts and clicks **Request changes**, ownership flips back to you and the project returns to `requirements_ready`. On the project list you'll see a **Changes requested** badge.

To address it:

1. Open the project. The SA's note (if they left one) is recorded in the project's history.
2. Reconnect to the chat, make the requested adjustments to the requirements or stories.
3. When you're satisfied, use the handoff card again to **reassign to the Solution Architect**.

There's no limit to how many times a project can bounce; every transition — including the SA's note — is recorded in the project's assignment history for audit. See [Handoffs and Change Requests](handoffs-and-change-requests.md).

---

## Tips

- **Front-load context.** Uploading a solid requirements document (or pasting a detailed prompt) dramatically shortens the question phase.
- **Answer precisely.** Vague answers lead the agent to ask follow-ups or make assumptions. If you don't know something, say so — the agent handles that gracefully.
- **Review before you approve.** Open each artifact in the document viewer before approving a gate. It's much cheaper to adjust now than after an SA bounce.

---

## Next

- [Solution Architect Walkthrough](walkthrough-solution-architect.md) — what happens after you hand off.
- [Handoffs and Change Requests](handoffs-and-change-requests.md) — the full state machine and bounce mechanics.
- [Artifacts and Files](artifacts-and-files.md) — what the BA stages produce and where.
