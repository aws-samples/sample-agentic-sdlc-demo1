# QA Engineer Walkthrough

As the QA Engineer, you ensure the quality of the implemented code. You receive a project with ingested source code from the developer's repository, along with the full design artifacts, and produce test plans, execute testing, and write QA reports. You can also run actual tests using a CodeBuild test runner.

**Your persona:** `qa-engineer`
**You own the project while it's in:** `assigned_to_qa`, `in_qa`, and `qa_passed`
**You receive from:** the Software Developer (via automated repository ingestion)
**You hand off to:** the DevOps Engineer

---

## Before you start

Sign in as a QA Engineer (see [Getting Started](getting-started.md)). On the project list, look for a project with an **Assigned to you** badge — that's a project the developer has linked and submitted through the Git round-trip.

---

## Step 1 — Review the ingested code and design artifacts

Open the assigned project. You have access to:

- **The ingested source code** — located under the project's `source/` folder, synchronized from the developer's GitHub/GitLab repository.
- **All design artifacts** — requirements, user stories, personas, NFR requirements, NFR design, application design, and units of work.
- **The construction brief** — the consolidated package the developer used to implement the code.

Use the file browser and document viewer to familiarize yourself with both the intended design and the actual implementation.

---

## Step 2 — Run the QA workflow stages

The chat greets you and the agent works through the QA segment:

| Stage | What it does | Runs when |
| --- | --- | --- |
| **Test Planning** | Produces a comprehensive test plan with traceability from requirements and user stories to test cases. Reviews the application design to identify testing scenarios. | Always |
| **Test Execution** | Analyzes the ingested source code and produces a test execution assessment. Can trigger actual test runs via CodeBuild if tests exist in the repository. | Always |
| **QA Report** | Creates a final QA report summarizing test results, coverage analysis, and quality assessment. Provides recommendation for advancement or requesting changes. | Always |

Key points about the QA stages:

- **Test Planning** runs even before looking at the code — it's based on the requirements and design, ensuring comprehensive test coverage.
- **Test Execution** can trigger a **CodeBuild test runner** if you click **Run tests** during this stage. The runner executes any tests found in the ingested repository and writes results to `aidlc-docs/qa/test-results.md`.
- **QA Report** synthesizes all testing information into a final quality assessment.

---

## Step 3 — Use the Run tests control (optional)

If the ingested repository contains tests (unit tests, integration tests, etc.), you can execute them:

1. During the Test Execution stage, a **Run tests** control appears above the chat.
2. Click it to trigger a CodeBuild job that runs the repository's test suite.
3. The results are captured and written to `aidlc-docs/qa/test-results.md`.
4. The test execution agent incorporates these real results into its assessment.

This gives you both automated test results and the agent's broader quality analysis.

---

## Step 4 — Advance through QA states

As QA work progresses, you'll see forward actions in the QA review panel:

1. **Start QA** — advances `assigned_to_qa → in_qa` when you begin active testing.
2. **Mark QA passed** — advances `in_qa → qa_passed` when testing is complete and the code meets quality standards.

Review each generated artifact (test plan, test results if applicable, QA report) in the document viewer before advancing.

---

## Step 5 — Hand off to DevOps or bounce back

From `qa_passed`, you have two options:

### Advance to DevOps
Click **Assign to DevOps Engineer** to pass the project to the DevOps stage. This advances the project `qa_passed → assigned_to_devops` and the DevOps Engineer takes over for deployment planning.

### Request changes from the developer
If testing reveals issues that require code changes, you can bounce the project back to the developer. This returns the project to `assigned_to_dev` and flips ownership back to the Software Developer. Include a note explaining what needs to be fixed.

The developer will then:
1. Make the necessary code changes in their external IDE
2. Push updates to their repository 
3. Re-link and resubmit (which triggers fresh ingestion)
4. Return the project to you for re-testing

---

## Handling a bounce from DevOps

If the DevOps Engineer encounters issues during deployment planning, they can send the project back to you. When this happens, the project returns to `assigned_to_qa` and ownership flips back to you. Review the DevOps note, address any QA-related concerns, and reassign when ready.

---

## Tips

- **Start with test planning before looking at code.** This ensures you're testing against the requirements, not just what was implemented.
- **Use the real test runner when available.** Automated test results provide concrete evidence of code quality.
- **Cross-reference design and implementation.** Look for gaps between what was designed and what was built.
- **Be specific when bouncing back to the developer.** Clear feedback helps them fix issues in one iteration.
- **Check the repository commit and ingestion status.** Make sure you're testing the latest version of the code.

---

## Next

- [DevOps Engineer Walkthrough](walkthrough-devops-engineer.md) — what happens after you hand off.
- [Software Developer Walkthrough](walkthrough-software-developer.md) — what happens if you bounce it back.
- [Handoffs and Change Requests](handoffs-and-change-requests.md) — the QA→Dev and DevOps→QA bounce mechanics.
- [Artifacts and Files](artifacts-and-files.md) — what the QA stages produce and where they're stored.