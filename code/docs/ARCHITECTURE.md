# Agentic SDLC Transformation — Architecture

> **Status (audit pass):** The high-level component descriptions and the
> interaction-flow diagram are still accurate. The "Deployment Topology"
> and "Deployment Pipeline" sections below contain stale account/region
> values and an outdated deploy script reference (the current entrypoint
> is `yarn deploy-infrastructure` → `scripts/package-assets.sh` + `cdk
> deploy`, default region `us-east-1`). The graph is registry-derived;
> the active node count is variable because a `handoff_gate__{seg}` node
> is inserted on every segment boundary with a successor (currently the
> BA → SA boundary). For canonical workflow rules see
> `.kiro/steering/00-project-overview.md` and
> `.kiro/steering/20-workflow-registry.md`. For canonical test commands see
> `.kiro/steering/60-testing.md`. For the handoff message contract see
> `code/docs/websocket-message-protocol.md` (`handoff_required`).

## What Is This Project?

Agentic SDLC Transformation is a multi-persona, agent-driven collaboration platform for the full software development lifecycle. A Business Analyst, a Solution Architect, and a Software Developer work on the same project: the BA uploads requirements and produces user stories, then hands off to the SA who reviews the artifacts and produces design and architecture deliverables; the SA then hands off to the Developer, who receives the finished design packaged as a construction brief to take into an AI-assisted IDE. A read-only Maintenance Specialist persona provides a cross-project operations dashboard. The platform runs on AWS Bedrock AgentCore (Runtime, Gateway, Memory) with a Strands GraphBuilder orchestrator, a shared S3-backed Project Store, and a React web UI. Inspired by the AWS AI-DLC methodology.

Users sign in with a persona role, interact via the React web UI, answer clarifying questions (Human-in-the-Loop), and the system produces structured artifacts — requirements docs, user stories, application design, architecture — all stored in S3 and optionally synced to Atlassian Confluence/Jira. Internal directory names, container images, and stack identifiers (`icode-agent`, `AIDLCPlatformWorkshop`, `aidlc-docs/`, etc.) are preserved verbatim for compatibility.

---

## Folder Structure

```
iCode-repo/
├── cdk/                          # AWS CDK infrastructure (TypeScript)
│   ├── bin/                      # CDK app entrypoint
│   ├── lib/
│   │   ├── aidlc-platform.ts     # Root stack — wires all constructs
│   │   ├── aidlc-platform-agents.ts  # AgentCore Runtime, Gateway, Memory, Lambdas
│   │   ├── aidlc-platform-auth.ts    # Cognito User Pool + PKCE client
│   │   ├── aidlc-platform-ui.ts      # S3 + CloudFront for React UI
│   │   └── config.ts             # Deployment config (model IDs, names, etc.)
│   └── custom-resources/
│       ├── atlassian_mcp_proxy.py    # Atlassian REST proxy Lambda
│       ├── integration_config_api.py # Integration config CRUD Lambda
│       ├── build.py              # CodeBuild trigger custom resource
│       └── ...
│
├── code/
│   ├── agentcore-runtime/
│   │   └── icode-agent/          # The agent container (Python)
│   │       ├── main_ws.py        # WebSocket entrypoint (BedrockAgentCoreApp)
│   │       ├── orchestrator.py   # Strands GraphBuilder — 14-node SDLC graph
│   │       ├── shared_state.py   # SharedState schema + initializer
│   │       ├── agents/           # One agent per SDLC stage
│   │       │   ├── inception/    # workspace_detection, requirements, design, etc.
│   │       │   ├── construction/ # code_generation, build_and_test, etc.
│   │       │   ├── extensions/   # inception_complete, workspace_detection_ext
│   │       │   └── integrations/
│   │       │       └── atlassian/ # Per-stage Atlassian sync agents
│   │       ├── tools/
│   │       │   ├── gateway.py    # MCPClient wrapper — translates tool names
│   │       │   ├── integrations.py # Integration config loading + Atlassian @tools
│   │       │   ├── state.py      # S3 state load/persist via Gateway
│   │       │   ├── human_input.py # HITL tool (routes via WebSocket queue)
│   │       │   ├── audit.py      # Audit log tool
│   │       │   └── stream_adapter.py # WebSocket event serializer
│   │       ├── config/           # Bedrock model factory
│   │       └── Dockerfile        # ARM64 container image
│   │
│   └── ui-frontend/              # React + Vite frontend (TypeScript)
│       └── app/
│           ├── context/
│           │   ├── AuthContext.tsx      # Cognito auth (Amplify)
│           │   └── WebSocketContext.tsx # AgentCore WebSocket client
│           ├── components/
│           │   ├── landingPage.tsx      # Project list + gear icon
│           │   └── IntegrationConfigPanel.tsx # Atlassian toggle UI
│           └── lib/config.ts     # Runtime config loader (config.json from S3)
│
├── assets/                       # Deployment zip artifacts (built by deploy.sh)
│   ├── icode-agent.zip           # Agent source → uploaded to S3 → CodeBuild
│   ├── ui-frontend.zip           # Built React app → S3 → CloudFront
│   └── lambda-tools.zip          # File-ops Lambda
│
├── assetsSrc/                    # Source for assets (pre-build)
│   └── isv-agents.json           # ISV agent registry
│
├── deploy.sh                     # Full build + deploy pipeline
└── .kiro/specs/                  # Kiro spec files for features/bugfixes
```

---

## Components

### 1. React Frontend (`code/ui-frontend`)
- Built with Vite + React + TypeScript
- Deployed as a static site to S3, served via CloudFront
- Auth: AWS Amplify → Cognito (PKCE flow)
  - ID token used for API Gateway (Cognito authorizer)
  - Access token used for AgentCore WebSocket auth
- WebSocket: connects to AgentCore Runtime using `base64UrlBearerAuthorization` protocol header
- Sends an init message `{project_id, project_name, project_uuid, request}` on connect
- Receives streaming events: `text_chunk`, `human_input`, `approval_required`, `artifact_updated`, `workflow_complete`
- `IntegrationConfigPanel.tsx`: toggle UI for enabling Atlassian per-project

### 2. AgentCore Runtime (`code/agentcore-runtime/icode-agent`)
- Python container, built for ARM64 via CodeBuild, stored in ECR
- Runs as an AWS Bedrock AgentCore Runtime
- Entrypoint: `main_ws.py` — `@app.websocket` handler
  - Authenticates to Gateway via OAuth (client credentials)
  - Loads/initializes SharedState from S3
  - Loads integration config from S3
  - Builds the Strands Graph (`orchestrator.py`)
  - Runs graph with `stream_async`, forwarding events over WebSocket
  - HITL: `asyncio.Queue` + `StreamAdapter` — single receive loop routes responses
- `orchestrator.py`: Strands `GraphBuilder` graph, registry-derived. The
  active configuration covers the inception + design segments
  (`workspace_detection_ext` → `workspace_detection` → optional
  `reverse_engineering` → `requirements_analysis` → `user_stories` →
  `workflow_planning` → `application_design` → `units_generation` →
  `inception_complete`). A registry-derived `handoff_gate__{seg}` node is
  inserted on every segment boundary with a successor — currently
  `handoff_gate__ba_segment` between `hub_post_user_stories` and the SA
  segment's entry node. The construction stages
  (`functional_design` … `build_and_test`) exist as agent code but are
  commented out in `shared_state.py` and `orchestrator.py`; do not assume
  they run. Atlassian integration agents run after each completed stage
  hub, conditional on per-project config in S3.
- Conditional edges based on `execution_plan.stages_to_execute` in
  SharedState plus the registry-derived skip set computed for the active
  persona's segment.

### 3. AgentCore Gateway
- AWS Bedrock AgentCore Gateway (MCP protocol)
- Cognito-authenticated (access token required)
- Two Lambda targets registered:
  - `file-operations` → `ToolsLambda` (S3 read/write/list/presign)
  - `atlassian` → `AtlassianMcpProxyLambda` (Confluence + Jira REST)
- Agent calls tools via `MCPClient` (streamable HTTP) with Bearer token

### 4. AgentCore Memory
- AWS Bedrock AgentCore Memory
- Strategies: summarization + semantic
- Session manager injected into agent at runtime
- Provides short-term and long-term memory across sessions

### 5. File Operations Lambda (`assets/lambda-tools.zip`)
- Python Lambda registered as Gateway target `file-operations`
- Reads/writes/lists/deletes objects in the Project S3 Bucket
- Generates presigned GET URLs for artifact delivery to the UI

### 6. Atlassian MCP Proxy Lambda (`cdk/custom-resources/atlassian_mcp_proxy.py`)
- Python Lambda registered as Gateway target `atlassian`
- Translates MCP tool calls into Atlassian REST API calls
- Credentials resolved per-project from Secrets Manager: `aidlc/projects/{project_uuid}/atlassian`
- Falls back to global `ATLASSIAN_SECRET_ARN` env var
- Supports: `ensure_jira_project`, `ensure_confluence_space`, `confluence_create_page`, `jira_create_issue`, `jira_transition_issue`, `jira_get_board`, `jira_create_sprint`, and more
- `cloud_id` read from secret (or auto-discovered via Atlassian API)

### 7. Integration Config API Lambda (`cdk/custom-resources/integration_config_api.py`)
- Python Lambda behind API Gateway (Cognito-authorized)
- Routes:
  - `GET /projects/{projectId}/integrations` — read config from S3
  - `PUT /projects/{projectId}/integrations/{integrationId}` — write config to S3 + upsert secret
  - `GET /projects/{projectId}/integrations/{integrationId}/status` — check status
- Stores config at `{projectId}/integrations/config.json` in Project S3 Bucket
- Stores Atlassian credentials in Secrets Manager at `aidlc/projects/{projectId}/atlassian`

### 8. Cognito User Pool (`cdk/lib/aidlc-platform-auth.ts`)
- User Pool with groups mapping to SDLC roles (requirements-analyst, system-architect, etc.)
- Two clients:
  - Standard app client (client credentials — used by agent for Gateway OAuth)
  - PKCE client (used by React UI for user login)
- Token endpoint used by agent's `OAuthProvider` to get Gateway access tokens

### 9. Project S3 Bucket
- Stores all project artifacts under `{project_slug}/` prefix
- Structure per project:
  ```
  {project_slug}/
  ├── aidlc-docs/aidlc-state.md          # Serialized SharedState
  ├── aidlc-docs/audit.md                # Audit log
  ├── aidlc-docs/inception/requirements/ # Requirements artifacts
  ├── aidlc-docs/inception/application-design/
  ├── aidlc-docs/construction/plans/
  └── integrations/config.json           # Integration config
  ```

### 10. VSCode IDE (EC2)
- Developer IDE running in the VPC
- Bootstrap script installs tools, clones labs, mounts S3

---

## Deployment Topology

```
AWS Account: <<account id >> / <<region>>
│
├── CloudFront (endpoint.cloudfront.net)
│   └── Origin: S3 Web Bucket (aidlc-ui-accountnumber-region)
│       └── React SPA (built by Vite, deployed by CDK BucketDeployment)
│
├── Cognito User Pool (region_poolname)
│   ├── PKCE Client (43nb04u7f2iqsinmhjqilmbcc) — UI login
│   └── App Client — agent OAuth (client credentials)
│
├── API Gateway (gateway.execute-api.region.amazonaws.com/prod)
│   └── Integration Config API Lambda
│       └── Reads/writes Project S3 Bucket + Secrets Manager
│
├── AgentCore Runtime (strands_agent-name)
│   └── Container: ECR icode-agent (ARM64, built by CodeBuild)
│       └── Connects to: AgentCore Gateway (MCP/streamable-HTTP)
│
├── AgentCore Gateway
│   ├── Target: file-operations → ToolsLambda → Project S3 Bucket
│   └── Target: atlassian → AtlassianMcpProxyLambda → Atlassian REST APIs
│
├── AgentCore Memory
│   └── Strategies: summarization + semantic
│
├── Project S3 Bucket (aidlc-platform-accountnumber-region)
│   └── All project artifacts + integration configs
│
├── Secrets Manager
│   ├── aidlc/projects/{uuid}/atlassian — per-project Atlassian credentials
│   └── Cognito client secret (for agent OAuth)
│
├── SSM Parameter Store (/aidlc/ prefix)
│   ├── bedrock/model-id
│   ├── memory/id
│   ├── gateway/endpoint
│   └── gateway/scope
│
├── ECR Repository (icode-agent)
│   └── Built by CodeBuild project (icode-agent-builder)
│       └── Source: assets/icode-agent.zip from Assets S3 Bucket
│
└── VPC (10.0.0.0/16)
    └── EC2 VSCode IDE (developer environment)
```

---

## Interaction Flow

```
User (Browser)
    │
    │  HTTPS login
    ▼
CloudFront → S3 (React SPA)
    │
    │  Cognito PKCE login
    ▼
Cognito User Pool
    │  returns ID token (for API GW) + Access token (for AgentCore WS)
    │
    ├──────────────────────────────────────────────────────────────────┐
    │  REST (ID token)                                                 │  WebSocket (Access token)
    ▼                                                                  ▼
API Gateway                                              AgentCore Runtime (WebSocket)
    │                                                         │
    ▼                                                         │  1. Receive init msg {project_id, project_uuid, request}
Integration Config Lambda                                     │  2. OAuth → Cognito → get Gateway access token
    │                                                         │  3. MCPClient.__enter__() → AgentCore Gateway
    ├── S3: read/write integrations/config.json               │  4. Load SharedState from S3 (via Gateway file-ops)
    └── Secrets Manager: upsert Atlassian credentials         │  5. Load integration config from S3
                                                              │  6. Build Strands Graph (14 nodes)
                                                              │  7. stream_async() → events → WebSocket
                                                              │
                                                              │  HITL loop:
                                                              │  ┌─────────────────────────────────┐
                                                              │  │ human_input tool called         │
                                                              │  │   → send WS msg to browser      │
                                                              │  │   → wait on asyncio.Queue       │
                                                              │  │ _hitl_receive_loop              │
                                                              │  │   → receive WS msg from browser │
                                                              │  │   → put_nowait to Queue         │
                                                              │  └─────────────────────────────────┘
                                                              │
                                                              ▼
                                                    AgentCore Gateway (MCP)
                                                         │
                                                         ├── file-operations tools
                                                         │       ▼
                                                         │   ToolsLambda → Project S3 Bucket
                                                         │   (read_file, write_file, list_files,
                                                         │    get_presigned_url, create_folder)
                                                         │
                                                         └── atlassian tools
                                                                 ▼
                                                         AtlassianMcpProxyLambda
                                                             │
                                                             ├── Secrets Manager (per-project creds)
                                                             └── Atlassian REST APIs
                                                                 ├── Confluence (create/update pages, spaces)
                                                                 └── Jira (projects, issues, sprints, boards)
```

---

## Deployment Pipeline

> Stale: the previous version of this doc described a `deploy.sh` script.
> The current entrypoint is `yarn deploy-infrastructure`, which runs:

1. `bash scripts/package-assets.sh` — packages the agent runtime source,
   the React UI build, the Lambda zips, and the project context bundle,
   and uploads them all to `s3://aidlc-assets-<account>-<region>/`. The
   bucket is created if missing. Prints the bucket name on stdout.
2. `yarn workspace cdk cdk deploy --require-approval never -c assetsBucketName=<bucket>` —
   synthesizes and deploys the `AIDLCPlatformWorkshop` stack.

Default region is `us-east-1`. The account-mismatch guard
(`cdk/lib/utils/account-guard.ts`) halts a deploy that would otherwise
land in a different account from the pinned default; bypass it
intentionally with `-c account=<id>`.

The CodeBuild project rebuilds the ARM64 agent container from
`assets/icode-agent.zip` on every deploy. First container build is
~10–15 minutes; subsequent builds reuse cached layers and finish in
~5–10 minutes.

To rebuild the container only (without re-running the full CDK deploy),
re-run `yarn deploy-infrastructure` — `package-assets.sh` is idempotent
and will only re-upload zips whose contents changed.

---

## Key Design Decisions

- SharedState is a Python dict passed by reference through the Strands Graph. Non-serializable objects (`_mcp_client`, `_websocket`, `_ws_human_input`) are injected at runtime and stripped before S3 persistence.
- HITL uses a single `asyncio.Queue` per session. The graph runs in one coroutine, the receive loop in another — both via `asyncio.gather`. No concurrent consumers, no race conditions.
- Project identity uses two IDs: `project_slug` (slugified name, used as S3 prefix) and `project_uuid` (UI UUID, used for credential lookup in Secrets Manager).
- The Atlassian proxy Lambda bypasses `mcp.atlassian.com` (which blocks AWS Lambda IPs via Cloudflare) by calling Atlassian REST APIs directly.
- The Gateway translates MCP tool names: `file-operations___read_file`, `atlassian___confluence_create_page`, etc. `gateway.py` handles this translation transparently.


# Notes

┌─────────────────────────────────────────────────────────────────────────────┐
│  LOCAL REPO (iCode-repo/)                                                   │
│                                                                             │
│  code/ui-frontend/          → built by: npx vite build                     │
│  code/agentcore-runtime/    → packaged by: zip → icode-agent.zip           │
│  cdk/custom-resources/      → Lambda inline code (Python files)            │
│  assetsSrc/isv-agents.json  → static config file                           │
│  assets/                    → output zips (ui-frontend.zip, icode-agent.zip)│
└─────────────────────────────────────────────────────────────────────────────┘
                    │
                    │  deploy.sh → npx cdk deploy
                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  S3 ASSETS BUCKET (deployment assets)                                         │
│  ├── ui-frontend.zip    ← React SPA build                                  │
│  ├── icode-agent.zip    ← Python agent source                              │
│  └── lambda-tools.zip   ← file operations Lambda                           │
└─────────────────────────────────────────────────────────────────────────────┘
         │                    │
         │ CodeBuild           │ UnzipUi Lambda (custom resource)
         ▼                    ▼
┌──────────────────┐  ┌──────────────────────────────────────────────────────┐
│  ECR             │  │  S3 WEB BUCKET (aidlc-ui-*)                          │
│  icode-agent     │  │  web/                                                │
│  Docker image    │  │  ├── index.html  (React SPA)                         │
│  (ARM64)         │  │  ├── assets/     (JS/CSS bundles)                    │
└──────────────────┘  │  ├── config.json (runtime config — CDK generated)    │
         │            │  └── isv-agents.json (ISV config)                    │
         │            └──────────────────────────────────────────────────────┘
         │                           │
         ▼                           │ CloudFront serves
┌──────────────────────────────────┐ ▼
│  BEDROCK AGENTCORE RUNTIME       │ ┌─────────────────────────────────────┐
│  (strands_agent-unbodHFnuB)      │ │  CLOUDFRONT (d27a9n7w5hah3l.*)      │
│                                  │ │  → serves SPA + config.json         │
│  Runs: icode-agent Docker image  │ │  → originPath: /web                 │
│  Entry: main_ws.py               │ └─────────────────────────────────────┘
│  Protocol: WebSocket             │              │
│  Auth: Cognito access token      │              │ Browser loads
│                                  │              ▼
│  Inside the container:           │ ┌─────────────────────────────────────┐
│  ├── main_ws.py (WS handler)     │ │  REACT SPA (browser)                │
│  ├── orchestrator.py (graph)     │ │  ├── AuthContext (Cognito/Amplify)   │
│  ├── agents/ (stage agents)      │ │  ├── WebSocketContext               │
│  └── tools/                      │ │  │   └── wss://bedrock-agentcore...  │
│      ├── human_input.py          │ │  ├── LandingPage (project cards)    │
│      ├── stream_adapter.py       │ │  └── IntegrationConfigPanel         │
│      └── gateway.py              │ │      └── calls Config API           │
└──────────────────────────────────┘ └─────────────────────────────────────┘
         │                                        │
         │ MCP over HTTP                          │ REST API calls
         ▼                                        ▼
┌──────────────────────────────────┐ ┌─────────────────────────────────────┐
│  AGENTCORE GATEWAY               │ │  API GATEWAY (5xv1jvanoh.*)         │
│  (MCP protocol)                  │ │  + Cognito authorizer (ID token)    │
│  Auth: Cognito machine client    │ │                                     │
│                                  │ │  Routes:                            │
│  Targets:                        │ │  GET  /projects/{id}/integrations   │
│  ├── file-operations Lambda      │ │  PUT  /projects/{id}/integrations/  │
│  └── atlassian-mcp-proxy Lambda  │ │       {integrationId}               │
└──────────────────────────────────┘ │  GET  /projects/{id}/integrations/  │
         │                           │       {integrationId}/status        │
         ▼                           └─────────────────────────────────────┘
┌──────────────────────────────────┐              │
│  LAMBDA: file-operations         │              ▼
│  (lambda-tools.zip)              │ ┌─────────────────────────────────────┐
│  → S3 read/write/list            │ │  LAMBDA: integration-config-api     │
│  → project files bucket          │ │  (integration_config_api.py)        │
└──────────────────────────────────┘ │  → reads/writes S3 project bucket   │
                                     │  → creates/updates Secrets Manager  │
┌──────────────────────────────────┐ └─────────────────────────────────────┘
│  LAMBDA: atlassian-mcp-proxy     │              │
│  (atlassian_mcp_proxy.py)        │              ▼
│  → per-project secret lookup     │ ┌─────────────────────────────────────┐
│  → Atlassian REST API calls      │ │  S3 PROJECT BUCKET (agentcore-files)│
└──────────────────────────────────┘ │  {project_id}/integrations/         │
         │                           │  config.json                        │
         ▼                           └─────────────────────────────────────┘
┌──────────────────────────────────┐              │
│  SECRETS MANAGER                 │◄─────────────┘
│  aidlc/projects/{id}/atlassian   │
│  aidlc/projects/{id}/{isv}       │
└──────────────────────────────────┘

## Execution Flow

USER (Browser)
    │
    │  1. Opens https://endpoint.cloudfront.net
    │     → loads React SPA + config.json from S3/CloudFront
    │
    │  2. Signs in via Cognito (PKCE flow)
    │     → gets ID token (for API calls) + access token (for WebSocket)
    │
    │  3. Clicks project card → ProjectWorkspace mounts
    │
    ▼
WebSocketContext.tsx
    │
    │  connect(projectId, "Build a software project called X")
    │  → builds WSS URL: wss://bedrock-agentcore.region.amazonaws.com
    │                     /runtimes/{runtimeArn}/ws?session-id=...
    │  → authenticates via Sec-WebSocket-Protocol: base64UrlBearerAuthorization.{accessToken}
    │
    ▼
AgentCore Runtime (Docker container — main_ws.py)
    │
    │  handle_message(websocket, context)
    │
    │  ┌─ SETUP ──────────────────────────────────────────────────────┐
    │  │  1. Parse session_id from query params                       │
    │  │  2. Load MCP client (connects to AgentCore Gateway)          │
    │  │  3. Load shared_state from S3 (or initialize fresh)          │
    │  │  4. Create hitl_response_queue = asyncio.Queue()             │
    │  │  5. Create StreamAdapter(websocket, hitl_response_queue)     │
    │  │  6. Inject _ws_human_input bridge into shared_state          │
    │  └──────────────────────────────────────────────────────────────┘
    │
    │  ┌─ BRANCH: has_progress? ──────────────────────────────────────┐
    │  │                                                               │
    │  │  YES (resuming) → send approval gate "Resume or Start Fresh?" │
    │  │                 → receive response via websocket.receive_text │
    │  │                 → if "Start Fresh": reinitialize shared_state │
    │  │                                                               │
    │  │  NO (new project) → send greeting approval gate              │
    │  │                   → receive "Approve & Continue"             │
    │  └──────────────────────────────────────────────────────────────┘
    │
    │  asyncio.gather(
    │    _run_graph_fresh(request),      ← runs the SDLC graph
    │    _hitl_receive_loop_fresh()      ← routes WS messages to queue
    │  )
    │
    ├─────────────────────────────────────────────────────────────────
    │
    ▼  _run_graph_fresh
orchestrator.py → build_sdlc_graph(shared_state)
    │
    │  Creates Strands GraphBuilder with nodes:
    │  workspace_detection → requirements_analysis → user_stories
    │  → workflow_planning → application_design → units_generation
    │  → [atlassian_* stages] → inception_complete
    │
    │  Each node = a Strands Agent with:
    │  - system_prompt (stage instructions + HITL addendum + audit addendum)
    │  - tools: [human_input, record_answer, log_audit_entry, read_s3_artifact,
    │            present_approval_gate, process_approval_response, ...]
    │
    ▼  graph.stream_async(request, invocation_state=shared_state)
    │
    │  ┌─ STAGE EXECUTION (e.g. requirements_analysis) ──────────────┐
    │  │                                                               │
    │  │  Agent runs in asyncio.to_thread (worker thread)             │
    │  │                                                               │
    │  │  1. Agent displays progress banner (text_chunk events)       │
    │  │     → StreamAdapter.process_event() → websocket.send_text()  │
    │  │                                                               │
    │  │  2. Agent calls human_input(message="Question 1 of 10...")   │
    │  │     → human_input.py._handle_ws_mode()                       │
    │  │     → run_coroutine_threadsafe(_ws_send_fn(prompt), loop)    │
    │  │     → _ws_send_fn awaits StreamAdapter.send_human_input_request()
    │  │     → sends {"type":"human_input","prompt":"..."} to browser │
    │  │     → awaits hitl_response_queue.get()  ← BLOCKS HERE        │
    │  │                                                               │
    │  │  3. User types answer in browser → sends:                    │
    │  │     {"type":"human_input_response","response":"1"}           │
    │  │                                                               │
    │  │  4. _hitl_receive_loop_fresh() receives it                   │
    │  │     → hitl_response_queue.put_nowait("1")                    │
    │  │                                                               │
    │  │  5. hitl_response_queue.get() unblocks → returns "1"         │
    │  │     → human_input tool returns to agent                      │
    │  │                                                               │
    │  │  6. Agent calls record_answer() → saves to S3                │
    │  │  7. Agent calls human_input again for Question 2...          │
    │  │     (repeat steps 2-6 for all 10 questions)                  │
    │  │                                                               │
    │  │  8. Agent calls present_approval_gate() + human_input()      │
    │  │     → user approves → process_approval_response()            │
    │  │     → stage marked "completed" in shared_state               │
    │  │                                                               │
    │  └──────────────────────────────────────────────────────────────┘
    │
    │  Graph transitions to next node (user_stories, etc.)
    │  Repeat until all stages complete
    │
    ▼
StreamAdapter.send_workflow_complete()
    → {"type":"workflow_complete"} → browser shows completion
