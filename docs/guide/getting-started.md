# Getting Started

This page gets you from "I have access" to "I'm looking at a project workspace." It covers signing in, choosing a persona, and finding your way around the interface. If you're the person deploying the platform, the deployment steps live in the [User Guide](../user-guide.md); this page assumes the platform is already running and you have a URL and credentials.

---

## What you need

- The **platform URL** — a CloudFront address handed to you by whoever deployed it (the deploy prints it as `AIDLCPlatformURL`).
- **Sign-in credentials** — either a pre-provisioned test user or your own self-registered account. The demo persona logins are printed as stack outputs; the initial password is set via the `AIDLC_DEMO_USER_PASSWORD` environment variable at deploy time.
- A modern browser.

If you're running a demo or evaluation, the deploy provisions one test user per persona. Their emails and initial passwords are printed as stack outputs (`BATestUserEmail` / `BATestUserPassword`, `SATestUserEmail` / `SATestUserPassword`, `TestUserEmail` / `TestUserPassword` for the developer, `QATestUserEmail` / `QATestUserPassword`, `DevOpsTestUserEmail` / `DevOpsTestUserPassword`, and `MaintTestUserEmail` / `MaintTestUserPassword`).

---

## Signing in

1. Open the platform URL in your browser. You land on the project list.
2. Click **Sign In**. You're taken to the Cognito-hosted login page.
3. Enter your email and password.
4. The first time you sign in with a pre-provisioned test user, Cognito prompts you to set a new permanent password. Choose one that meets the pool's policy (mixed case, a digit, a symbol, at least 8 characters).
5. You're returned to the project list, now signed in as your persona.

Your persona determines what you see. The interface reads your Cognito group and only shows the actions your role can take, so a BA sees the upload control, an SA sees the review panel, and so on.

---

## Choosing a persona (self-signup)

The pre-provisioned test users are convenient for a first run. For real use, register your own account:

1. From the platform URL, click **Sign In**, then **Sign up** on the hosted page.
2. Provide an email and password, and choose a **persona role**.
3. Complete the email-verification step.
4. A post-confirmation step maps your chosen role to the matching group automatically. On your next sign-in, the correct persona surfaces appear.

The valid persona roles are:

| Role value | Persona | Has a UI surface today? |
| --- | --- | --- |
| `requirements-analyst` | Business Analyst | Yes |
| `system-architect` | Solution Architect | Yes |
| `software-developer` | Software Developer | Yes |
| `qa-engineer` | QA Engineer | Yes |
| `devops-engineer` | DevOps Engineer | Yes |
| `maintenance-specialist` | Maintenance Specialist | Yes (read-only dashboard) |

If you register with a role outside this set, you can still sign in, but you won't see any owner-gated actions until an administrator adds you to a group.

> Tip: to work as more than one persona (for example, to run a full BA → SA → Developer flow yourself), sign in as each persona in a separate browser profile or a private window. That keeps the sessions independent.

---

## Finding your way around the workspace

Open any project from the list and you land in its workspace. Three regions:

- **Chat (center).** This is your conversation with the AI agent for the current stage. The agent greets you, asks clarifying questions one at a time, and streams progress as it works. Approval prompts and handoff cards appear here.
- **Files and progress (one side).** A file browser showing everything the project has produced, and a progress panel showing which stages are done, in progress, or pending. The artifact folder is labeled **`agentic-sdlc`** here.
- **Document viewer (other side).** Click any Markdown artifact to read it rendered inline.

Persona-specific controls appear above the chat when relevant — the requirements upload control for a BA, the review panel for an SA.

### Reading the chat

The agent surfaces a few recurring signals in the chat:

- **Progress banners** tell you which stage is running.
- **Stage summaries** recap what a stage produced.
- **Clarifying questions** come one at a time — answer them to shape the output.
- **Approval gates** ask you to confirm before the workflow advances. Nothing moves forward until you approve.
- **Handoff cards** appear at a segment boundary with a button to pass the project to the next persona.

---

## The project list and status badges

The project list shows every project (visibility is broad on purpose), each with a status badge reflecting its current state — for example **Awaiting Solution Architect**, **Assigned to you**, or **Changes requested**. The badge is your at-a-glance signal for whether a project is waiting on you.

You can *see* every project, but you can only *act* on projects currently assigned to your persona. The relevant action button (assign, approve, request changes, and so on) only appears when it's your turn.

---

## Where to go next

Pick the walkthrough for your role:

- [Business Analyst Walkthrough](walkthrough-business-analyst.md)
- [Solution Architect Walkthrough](walkthrough-solution-architect.md)
- [Software Developer Walkthrough](walkthrough-software-developer.md)
- [QA Engineer Walkthrough](walkthrough-qa-engineer.md)
- [DevOps Engineer Walkthrough](walkthrough-devops-engineer.md)
- [Maintenance Dashboard](walkthrough-maintenance-dashboard.md)

Or read [Handoffs and Change Requests](handoffs-and-change-requests.md) to understand how work passes between roles.
