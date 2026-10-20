# DevOps Engineer Walkthrough

As the DevOps Engineer, you handle the deployment and release of the tested application. You receive a project that has passed QA testing, along with the full design artifacts and tested source code, and produce deployment plans and release runbooks to bring the application to production.

**Your persona:** `devops-engineer`
**You own the project while it's in:** `assigned_to_devops` and `in_deployment`
**You receive from:** the QA Engineer
**You hand off to:** deployed state (terminal)

---

## Before you start

Sign in as a DevOps Engineer (see [Getting Started](getting-started.md)). On the project list, look for a project with an **Assigned to you** badge — that's a project the QA Engineer has tested and approved for deployment.

---

## Step 1 — Review the tested code and artifacts

Open the assigned project. You have access to:

- **The tested source code** — located under the project's `source/` folder, the same code that passed QA testing.
- **QA artifacts** — test plan, test results, and QA report showing the code has been validated.
- **Design artifacts** — requirements, NFR requirements, NFR design, application design, AWS architecture diagram, and units of work.
- **Technology stack information** — details about the chosen technologies and architectural patterns.

Use the file browser and document viewer to understand the application's architecture, infrastructure requirements, and quality status.

---

## Step 2 — Run the DevOps workflow stages

The chat greets you and the agent works through the DevOps segment:

| Stage | What it does | Runs when |
| --- | --- | --- |
| **Deployment Planning** | Produces a comprehensive deployment plan covering infrastructure requirements, environment setup, CI/CD pipelines, monitoring, and deployment strategies. References the NFR design and AWS architecture diagram. | Always |
| **Release** | Creates release runbooks, deployment procedures, rollback plans, and operational documentation. May include infrastructure-as-code generation and deployment automation setup. | Always |

Key points about the DevOps stages:

- **Deployment Planning** leverages the SA's NFR design and AWS architecture diagram to create concrete infrastructure and deployment plans.
- **Release** focuses on the operational aspects — how to deploy, monitor, and maintain the application in production.
- Both stages consider the specific technology stack and architectural decisions made during the design phase.

---

## Step 3 — Advance through deployment states

As DevOps work progresses, you'll see forward actions in the DevOps review panel:

1. **Start deployment** — advances `assigned_to_devops → in_deployment` when you begin deployment planning and preparation.
2. **Mark deployed** — advances `in_deployment → deployed` when the application is successfully deployed to production.

Review each generated artifact (deployment plan, release runbook) in the document viewer before advancing.

---

## Step 4 — Complete deployment or bounce back

From `in_deployment`, you have two options:

### Mark as deployed (terminal state)
Click **Mark deployed** to complete the project lifecycle. This advances the project to the `deployed` state, which is terminal. The project has successfully moved through all six personas from initial idea to production deployment.

### Request changes from QA
If deployment reveals issues that require re-testing or QA attention, you can bounce the project back to the QA Engineer. This returns the project to `assigned_to_qa` and flips ownership back to the QA Engineer. Include a note explaining what QA concerns need to be addressed.

Common reasons for bouncing back to QA include:
- Infrastructure compatibility issues discovered during deployment planning
- Security concerns that require additional testing
- Performance requirements that need validation
- Missing test coverage for deployment-critical functionality

---

## Step 5 — Monitor via Maintenance Dashboard

Once deployed, the project appears in the **Maintenance Specialist's operations dashboard** with a "Deployed" status. While the formal workflow is complete, the Maintenance Specialist can track the operational status, repository information, and overall project health across all deployed applications.

---

## Tips

- **Leverage the AWS architecture diagram.** The SA's architecture design should guide your infrastructure and deployment planning.
- **Reference NFR requirements.** Performance, scalability, and security requirements from the SA inform your deployment strategy.
- **Check QA test results.** Understanding what was tested helps you plan monitoring and operational validation.
- **Plan for rollback.** Always include rollback procedures in your release runbook.
- **Consider the technology stack.** Tailor your deployment approach to the specific technologies chosen during design.
- **Use infrastructure-as-code.** Generate CloudFormation, CDK, or Terraform templates when appropriate.

---

## Next

- [Maintenance Dashboard](walkthrough-maintenance-dashboard.md) — the operations view of deployed applications.
- [QA Engineer Walkthrough](walkthrough-qa-engineer.md) — what happens if you bounce it back.
- [Handoffs and Change Requests](handoffs-and-change-requests.md) — the DevOps→QA bounce mechanics.
- [Artifacts and Files](artifacts-and-files.md) — what the DevOps stages produce and where they're stored.