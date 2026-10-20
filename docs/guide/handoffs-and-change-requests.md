# Handoffs and Change Requests

Every project moves through a fixed set of states, and work passes between personas through governed handoffs. This page explains the state machine, what each state means, how forward moves and "bounces" work, and how the platform keeps it all honest.

---

## The state machine

```text
draft ─► requirements_ready ─► assigned_to_sa ─► in_design ─► design_ready ─► ready_for_dev ─► assigned_to_dev ─► implementation_submitted ─► assigned_to_qa ─► in_qa ─► qa_passed ─► assigned_to_devops ─► in_deployment ─► deployed
 (BA)         (BA)                 (SA)            (SA)          (SA)            (SA)              (Dev)               (Dev)                    (QA)        (QA)      (QA)           (DevOps)           (DevOps)      (DevOps)

                                     │                                                              │                                                              │                            │
                                     └─ request changes ─► requirements_ready                       └─ request changes ─► design_ready                          └─ request changes ─► assigned_to_dev   └─ request changes ─► assigned_to_qa
                                        (SA sends back to BA)                                         (Dev sends back to SA)                                     (QA sends back to Dev)            (DevOps sends back to QA)
```

Each state has exactly one **owning persona**. Only that persona can advance the project out of that state.

| State | Owner | What it means |
| --- | --- | --- |
| `draft` | Business Analyst | Project just created. The BA can upload requirements and start the inception stages. |
| `requirements_ready` | Business Analyst | Requirements and user stories are generated. The BA assigns the project to the SA. |
| `assigned_to_sa` | Solution Architect | The SA reviews the BA's artifacts and chooses **Approve** or **Request changes**. |
| `in_design` | Solution Architect | The SA is producing NFR requirements, NFR design, application design and units of work. |
| `design_ready` | Solution Architect | Design work is complete; the SA marks it ready for development. |
| `ready_for_dev` | Solution Architect | Design is final. The SA hands off to the Software Developer. |
| `assigned_to_dev` | Software Developer | The construction brief is assembled. The developer codes in an external IDE, links their repository, and submits for QA. |
| `implementation_submitted` | Software Developer | Repository is linked; CodeBuild ingestion clones and syncs the code, then advances to QA. |
| `assigned_to_qa` | QA Engineer | QA reviews test plan, can run tests via CodeBuild, and produces QA reports. |
| `in_qa` | QA Engineer | QA testing and assessment in progress. |
| `qa_passed` | QA Engineer | QA testing complete and passed. Ready for DevOps handoff. |
| `assigned_to_devops` | DevOps Engineer | DevOps produces deployment plans and release runbooks. |
| `in_deployment` | DevOps Engineer | Deployment planning and release management in progress. |
| `deployed` | DevOps Engineer | Terminal state. Application is deployed to production. |

---

## Forward moves

Forward moves are the normal path from idea to deployed application. Each is triggered by the owning persona clicking the relevant action:

- BA → **Assign to Solution Architect** (`requirements_ready → assigned_to_sa`)
- SA → **Approve & continue** (`assigned_to_sa → in_design`)
- SA → **Mark design ready** (`in_design → design_ready`)
- SA → **Mark ready for development** (`design_ready → ready_for_dev`)
- SA → **Assign to Software Developer** (`ready_for_dev → assigned_to_dev`)
- Dev → **Link repo & submit for QA** (`assigned_to_dev → implementation_submitted`)
- System → Auto-advance after ingestion (`implementation_submitted → assigned_to_qa`)
- QA → **Start QA** (`assigned_to_qa → in_qa`)
- QA → **Mark QA passed** (`in_qa → qa_passed`)
- QA → **Assign to DevOps Engineer** (`qa_passed → assigned_to_devops`)
- DevOps → **Start deployment** (`assigned_to_devops → in_deployment`)
- DevOps → **Mark deployed** (`in_deployment → deployed`)

## Bounces (request changes)

Four bounces are supported. They send a project *backward* to the previous persona so they can fix something:

- **SA → BA:** `assigned_to_sa → requirements_ready`. The SA requests changes to the requirements. Ownership returns to the BA.
- **Dev → SA:** `assigned_to_dev → design_ready`. The developer requests changes to the design. Ownership returns to the SA.
- **QA → Dev:** `in_qa → assigned_to_dev`. The QA engineer requests changes to the implementation. Ownership returns to the developer.
- **DevOps → QA:** `in_deployment → assigned_to_qa`. The DevOps engineer requests changes from QA. Ownership returns to the QA engineer.

When you request changes, you can attach a **note** explaining what needs to change. The note is recorded in the project's history and visible to the persona receiving the bounce.

There's no cap on bounces — a project can go back and forth as many times as quality requires.

---

## How a handoff actually happens

Handoffs are more than a status change; they're a clean boundary between AI sessions.

1. As you work, the AI agent for your segment runs stage by stage.
2. When your segment finishes, the workflow reaches a **handoff gate**. Your session stops there and the current state is saved.
3. The chat shows a **handoff card** with a button (for example, **Assign to Solution Architect**).
4. Clicking it records the transition and flips ownership. Your WebSocket session ends.
5. The next persona opens the project and reconnects in a **fresh session** that picks up from the next segment.

This "session per segment" design is why each persona gets a clean, focused conversation rather than one endless thread.

---

## How the rules are enforced

The platform doesn't rely on the UI to keep things legal — it enforces the rules on the server.

- **Ownership (who can act).** The Project API checks the caller's verified identity (their Cognito group) against the project's owning role. A non-owner attempting a transition is rejected with **HTTP 403**. This is why you only see action buttons on projects assigned to you — and why you can't force a transition by other means.
- **Legality (what's allowed).** Every requested transition is validated against the state machine. An illegal transition (say, jumping straight from `draft` to `ready_for_dev`) is rejected with **HTTP 422**, and the project record is left unchanged.
- **Concurrency.** Project records are written with an ETag condition, so two people acting on the same project at the same time can't clobber each other's change.

The legal transitions live in a single **workflow registry** that the runtime, the Project API, and the frontend all read from. Because the rules aren't duplicated across those layers, the state machine stays coherent.

---

## The audit trail

Every transition is appended to the project's **assignment history**, recorded in the authoritative project record (`_platform/projects/{project_uuid}.json`). Each entry captures who acted, when, and — for bounces — the note that was attached. That gives you a complete, reviewable record of how a project moved from idea to construction-ready, including every round trip.

---

## Status badges you'll see

On the project list, each project carries a badge reflecting its state and your relationship to it — for example **Awaiting Solution Architect**, **Assigned to you**, or **Changes requested**. Use the badge to spot what's waiting on you.

---

## Next

- [Business Analyst Walkthrough](walkthrough-business-analyst.md)
- [Solution Architect Walkthrough](walkthrough-solution-architect.md)
- [Software Developer Walkthrough](walkthrough-software-developer.md)
- [Architecture](architecture.md#governance-one-registry-enforced-everywhere) — how the registry drives all this.
