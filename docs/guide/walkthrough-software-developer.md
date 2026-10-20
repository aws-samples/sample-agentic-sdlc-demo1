# Software Developer Walkthrough

As the Software Developer, you're the bridge from design to tested code. You receive a finished design packaged as a **construction brief**, take it into an AI-assisted IDE to generate the actual implementation, then link your repository back to the platform so QA and DevOps can operate on the real code.

**Your persona:** `software-developer`
**You own the project while it's in:** `assigned_to_dev` and `implementation_submitted`
**You receive from:** the Solution Architect
**You hand off to:** the QA Engineer (via repository ingestion)

> Important: the platform does **not** generate application code itself. Its job is to produce a construction-ready brief and then ingest your repository for the downstream QA and DevOps stages. Code generation happens in your external AI-assisted IDE. This is by design.

---

## Before you start

Sign in as a developer (see [Getting Started](getting-started.md)). On the project list, look for a project the SA has assigned to you.

---

## Step 1 — Let the construction handoff run

Open the assigned project. The `construction_handoff` agent runs automatically. It reads all the inception and design artifacts and assembles a **construction kickoff package**:

- **`construction-brief.md`** — a single consolidated document that folds together the requirements, user stories, design, and units of work, with a ready-to-paste coding-agent prompt at the top.
- **Per-unit task files** — one task file per unit of work, so you can drive construction unit by unit.

These are written under the project's artifact folder (shown as **`agentic-sdlc`** in the file browser). See [Artifacts and Files](artifacts-and-files.md) for exact locations.

---

## Step 2 — Review and take the brief into your IDE

1. Open **`construction-brief.md`** in the document viewer, or download it from the file browser.
2. Skim it end to end. It's designed to be a self-contained prompt: everything an AI coding assistant needs to start building, without re-reading the whole project.
3. If the platform was deployed with a linked IDE, an **Open IDE** button is available — click it. Otherwise, open your own environment.
4. Paste the brief (or the relevant per-unit task file) into your AI-assisted IDE — [Kiro](https://kiro.dev), Claude Code, Cursor, or any environment you prefer — and generate the implementation, infrastructure, and tests there.

Work unit by unit using the per-unit task files if the project was decomposed into multiple units. Each task file scopes one buildable piece.

---

## Step 3 — Link your repository and submit for QA

After implementing the code in your external IDE and pushing it to GitHub or GitLab:

1. In the platform workspace, you'll see a **Link repo & submit for QA** control above the chat.
2. Paste your repository URL (e.g., `https://github.com/username/project-repo`).
3. Optionally specify a branch (defaults to `main`).
4. For private repositories, provide a personal access token that will be securely stored.
5. Click **Submit for QA**.

What happens behind the scenes:
- The platform stores your repository metadata and triggers a **CodeBuild ingestion** job.
- CodeBuild clones your repository and syncs the source code into S3 under the project's prefix.
- Once ingestion completes, the project automatically advances to `assigned_to_qa` and the QA Engineer takes over.

## Step 4 — Bounce back, or finish

You have two ways to close out your ownership:

- **Request design changes.** If the design has a gap or a problem you hit during construction, send the project back to the SA. This returns it to `design_ready` and flips ownership to the Solution Architect, who revises the design and reassigns it to you.
- **Complete the Git round-trip.** When you successfully link your repository and the ingestion completes, the project automatically advances to the QA Engineer. Your direct ownership ends here, but your code continues through the QA and DevOps stages.

---

## What the brief contains

The construction brief is assembled from the upstream artifacts, so it reflects the whole project:

- The **requirements** (functional and non-functional).
- The **user stories** and **personas**.
- The **application design** — components, services, dependencies — and the **AWS architecture diagram**.
- The **units of work** and their dependency and story maps.
- A **prompt preamble** written so you can paste it straight into a coding agent.

Because it's generated from the same artifacts the SA approved, the brief stays consistent with the design. If you find it's missing something, that's usually a signal to bounce the project back to the SA rather than to patch around it.

---

## Tips

- **Start from the brief, not the raw artifacts.** The brief already consolidates everything; you rarely need to open the individual design files unless you want detail on a specific unit.
- **Use per-unit task files for parallel or incremental work.** They're scoped so you (or a coding agent) can build one unit at a time.
- **Bounce early if the design is wrong.** It's cheaper to fix a design gap with the SA than to work around it in code.
- **Ensure your repository is complete before linking.** The QA Engineer will be working with whatever is in your repository at the time of ingestion.
- **Use clear commit messages.** The ingested commit SHA is recorded and visible to QA and DevOps.

---

## Next

- [QA Engineer Walkthrough](walkthrough-qa-engineer.md) — what happens after you submit for QA.
- [Handoffs and Change Requests](handoffs-and-change-requests.md) — how the bounce back to the SA works.
- [Artifacts and Files](artifacts-and-files.md) — where the brief and task files live.
- [Solution Architect Walkthrough](walkthrough-solution-architect.md) — what happens if you bounce it back.
