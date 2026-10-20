# Agentic SDLC Transformation — Frontend & Agent Architecture Guide

## 1. End-to-End User Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           BROWSER (React SPA)                               │
│                                                                             │
│  code/ui-frontend/app/main.tsx          ← Entry point, loads config.json    │
│  code/ui-frontend/app/lib/config.ts     ← Runtime config (Cognito, Lambda)  │
│  code/ui-frontend/app/lib/amplify.ts    ← Amplify Auth setup                │
│  code/ui-frontend/app/context/AuthContext.tsx  ← Sign in/up/out, tokens     │
│  code/ui-frontend/app/routes.tsx        ← React Router (/, /sign-in, etc.)  │
│                                                                             │
│  LANDING PAGE: app/components/landingPage.tsx                               │
│    ├── Shows welcome (unauth) or project grid (auth)                        │
│    ├── CreateProjectDialog.tsx → saves to localStorage                      │
│    ├── Delete project → removes from localStorage                           │
│    └── Click project card → navigates to /project/:projectId                │
│                                                                             │
│  PROJECT WORKSPACE: app/components/ProjectWorkspace.tsx                      │
│    ├── Wraps in WebSocketProvider                                           │
│    ├── Loads project name from localStorage                                 │
│    ├── Calls connect(projectId, "Start workflow", projectName)              │
│    ├── Left panel: ChatPanel.tsx                                            │
│    │     ├── TextMessage.tsx      (ai_text, user messages)                  │
│    │     ├── StageBanner.tsx      (stage_start, stage_complete)             │
│    │     ├── ApprovalGate.tsx     (approval_required → buttons)             │
│    │     ├── HumanInputPrompt.tsx (human_input → text input)                │
│    │     ├── NotificationMessage.tsx (human_input_notify)                   │
│    │     └── ErrorMessage.tsx     (error)                                   │
│    └── Right panel: DocumentViewer.tsx                                       │
│          └── Fetches files via presigned S3 URLs from artifact_updated msgs │
│                                                                             │
│  WEBSOCKET CONTEXT: app/context/WebSocketContext.tsx                         │
│    ├── Builds session ID: {project_slug}_{email_sanitized}                  │
│    ├── Connects: wss://bedrock-agentcore.../ws?Session-Id=...               │
│    ├── Auth: base64UrlBearerAuthorization subprotocol                       │
│    ├── Sends init: {project_id: slug, project_name: name, request: ...}     │
│    ├── Receives JSON frames → dispatches to message state                   │
│    │     text_chunk → accumulate into ai_text bubble                        │
│    │     stage_start/complete → StageBanner                                 │
│    │     human_input → HumanInputPrompt (or ApprovalGate if detected)       │
│    │     approval_required → ApprovalGate                                   │
│    │     artifact_updated → fetchArtifact (filtered: no audit/questions)    │
│    │     tool_use → IGNORED (not rendered)                                  │
│    │     workflow_complete → completion banner                               │
│    └── User responses sent as plain text WebSocket frames                   │
└─────────────────────────────────────────────────────────────────────────────┘
                              │
                    WebSocket │ wss://
                              │
┌─────────────────────────────────────────────────────────────────────────────┐
│                    AGENTCORE RUNTIME (Container)                             │
│                                                                             │
│  ENTRYPOINT: main_ws.py                                                     │
│    │                                                                        │
│    ├── 1. websocket.accept()                                                │
│    ├── 2. Receive init message → extract project_id, project_name           │
│    ├── 3. OAuth → get access_token for Gateway                              │
│    │      └── tools/oauth_provider.py                                       │
│    ├── 4. Create MCPClient with Bearer auth                                 │
│    │      └── connects to Gateway MCP endpoint                              │
│    ├── 5. Create AgentCoreMemorySessionManager                              │
│    │      └── uses session_id for memory isolation                          │
│    ├── 6. Load or init SharedState from S3                                  │
│    │      └── tools/state.py (_load_via_gateway, _markdown_to_state)        │
│    ├── 7. Inject runtime refs into SharedState                              │
│    │      (_mcp_client, _session_id, _websocket, etc.)                      │
│    ├── 8. Build SDLC Graph                                                  │
│    │      └── orchestrator.py (build_sdlc_graph)                            │
│    ├── 9. Create StreamAdapter                                              │
│    │      └── tools/stream_adapter.py                                       │
│    ├── 10. Inject WebSocket human_input bridge                              │
│    │                                                                        │
│    ├── 11. GREETING / RESUME FLOW                                           │
│    │      ├── has_progress? → "Session Recovery" approval gate               │
│    │      │     Resume → continue with loaded state                         │
│    │      │     Start Fresh → reinitialize state, rebuild graph             │
│    │      └── no progress? → "Project Confirmation" approval gate           │
│    │            Yes → run workflow                                           │
│    │            No → general conversation mode                              │
│    │                                                                        │
│    ├── 12. graph.stream_async(request, invocation_state=shared_state)       │
│    │      └── Events flow through StreamAdapter → WebSocket → Browser       │
│    │                                                                        │
│    └── 13. Follow-up message loop (websocket.iter_text)                     │
│                                                                             │
│  ORCHESTRATOR: orchestrator.py                                              │
│    └── build_sdlc_graph(shared_state, model, interactive)                   │
│          Creates 13 agent nodes with conditional edges:                      │
│                                                                             │
│          workspace_detection ──► reverse_engineering (if brownfield)         │
│                │                                                            │
│                └──► requirements_analysis ──► user_stories (conditional)     │
│                                    │                                        │
│                                    └──► workflow_planning                    │
│                                              │                              │
│                                    ┌─────────┼─────────┐                    │
│                                    ▼         ▼         ▼                    │
│                              application  units_gen  functional             │
│                              _design                 _design                │
│                                    │         │         │                    │
│                                    └─────────┼─────────┘                    │
│                                              ▼                              │
│                                    nfr_requirements → nfr_design            │
│                                              │                              │
│                                    infrastructure_design                    │
│                                              │                              │
│                                    code_generation ──► (loop per unit)      │
│                                              │                              │
│                                    build_and_test                           │
└─────────────────────────────────────────────────────────────────────────────┘
                              │
                    MCP/HTTPS │
                              │
┌─────────────────────────────────────────────────────────────────────────────┐
│                    AGENTCORE GATEWAY (Lambda)                                │
│                                                                             │
│  code/agentcore-gateway/lambda-tools/handler.py                             │
│    ├── create_folder  → S3 put_object (folder prefix)                       │
│    ├── read_file      → S3 get_object                                       │
│    ├── write_file     → S3 put_object                                       │
│    ├── list_files     → S3 list_objects_v2                                  │
│    ├── get_presigned_url → S3 generate_presigned_url                        │
│    └── delete_folder  → S3 list + batch delete                              │
│                                                                             │
│  Invoked by the Gateway via MCP protocol (not directly by the frontend)     │
└─────────────────────────────────────────────────────────────────────────────┘
                              │
                         S3   │
                              │
┌─────────────────────────────────────────────────────────────────────────────┐
│                    S3 BUCKET (agentcore-files-...)                           │
│                                                                             │
│  /web/                       ← SPA static files (deployed by CDK)           │
│  /web/config.json            ← Runtime config (generated by CDK)            │
│  /{project_slug}/            ← Project workspace root                       │
│    └── aidlc-docs/                                                          │
│        ├── aidlc-state.md              ← SharedState (markdown)             │
│        ├── audit.md                    ← Audit log (filtered from UI)       │
│        └── inception/                                                       │
│            ├── requirements/                                                │
│            │   ├── requirement-verification-questions.md (filtered)          │
│            │   └── requirements.md     ← Shown in Document Viewer           │
│            ├── plans/                                                       │
│            │   └── execution-plan.md   ← Shown in Document Viewer           │
│            └── design/                                                      │
│                ├── application-design.md                                    │
│                ├── components.md                                            │
│                ├── component-methods.md                                     │
│                ├── services.md                                              │
│                └── component-dependency.md                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```


## 2. How Text Flows From Agent to Screen

```
AGENT (Python)                    WEBSOCKET                 FRONTEND (React)
─────────────                    ─────────                 ────────────────

LLM generates text               Strands emits             WebSocketContext.tsx
token by token                    "data" events             receives JSON frames
    │                                 │                          │
    │  "📍 "                          │                          │
    │  "**Workflow"                   │                          │
    │  " Progress**"                  │                          │
    │  "\n✅ Work"                    │                          │
    │  "space Detection"              │                          │
    │  "\n🔄 Req"                     │                          │
    │  "uirements..."                 │                          │
    ▼                                 ▼                          ▼
stream_adapter.py              multiagent_node_stream     handleTextChunk()
process_event()                unwraps inner "data"       ACCUMULATES into
sends text_chunk               sends to browser           single ai_text bubble
                                                               │
                                                               ▼
                                                          ChatPanel.tsx
                                                          MessageList maps
                                                          type → component
                                                               │
                                                               ▼
                                                          TextMessage.tsx
                                                          ReactMarkdown +
                                                          remarkGfm +
                                                          remarkBreaks
                                                               │
                                                               ▼
                                                          Tailwind CSS
                                                          "prose prose-sm"
                                                          class styles the
                                                          rendered HTML
```

### Bubble Accumulation Rule

All consecutive `text_chunk` messages merge into ONE bubble.
A new bubble starts only when a different message type arrives between text chunks.

```
[stage_start: requirements_analysis]     ← StageBanner component
[text_chunk: "📍 "]                      ┐
[text_chunk: "**Workflow Progress**\n"]   │
[text_chunk: "✅ Workspace Detection\n"] │ ALL ACCUMULATED
[text_chunk: "🔄 Requirements..."]       │ INTO ONE ai_text
[text_chunk: "I'll now capture..."]      │ BUBBLE
[text_chunk: "Now I'll generate..."]     │
[text_chunk: "Perfect! Now I'll..."]     ┘
[human_input: "📚 Question 1..."]        ← HumanInputPrompt component
```


## 3. Rendering & Formatting Layers

```
┌─────────────────────────────────────────────────────────────┐
│ LAYER 1: CONTENT (what text is generated)                    │
│                                                              │
│ WHO: The LLM inside each stage agent                         │
│ WHERE: agents/inception/*.py (system prompts)                │
│        agents/construction/*.py (system prompts)             │
│                                                              │
│ The system prompt tells the LLM what markdown to produce.    │
│ Emojis, bold text, headings, lists — all come from the LLM. │
│                                                              │
│ TO CHANGE: Edit the agent's SYSTEM_PROMPT in the .py file    │
│                                                              │
│ ⚠️ NOT 100% REPEATABLE — the LLM decides formatting each    │
│ time. You can instruct it, but it may vary slightly.         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ LAYER 2: TRANSPORT (how text is chunked and grouped)         │
│                                                              │
│ WHO: StreamAdapter + WebSocketContext                         │
│ WHERE:                                                       │
│   stream_adapter.py    → sends text_chunk JSON frames        │
│   WebSocketContext.tsx → handleTextChunk() accumulates        │
│                                                              │
│ RULE: All consecutive text_chunks merge into ONE bubble.     │
│ A new bubble starts only when a non-text message arrives.    │
│                                                              │
│ TO CHANGE bubble boundaries: Insert a different message      │
│ type between text chunks from the agent side.                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ LAYER 3: COMPONENT SELECTION (which React component renders) │
│                                                              │
│ WHO: ChatPanel.tsx → MessageList switch statement             │
│ WHERE: code/ui-frontend/app/components/chat/ChatPanel.tsx    │
│                                                              │
│   "ai_text"            → TextMessage.tsx                     │
│   "user"               → TextMessage.tsx                     │
│   "stage_start"        → StageBanner.tsx                     │
│   "stage_complete"     → StageBanner.tsx                     │
│   "approval_required"  → ApprovalGate.tsx                    │
│   "human_input"        → HumanInputPrompt.tsx                │
│   "human_input_notify" → NotificationMessage.tsx             │
│   "error"              → ErrorMessage.tsx                    │
│   "workflow_complete"  → CompletionBanner (inline)           │
│   "tool_use"           → null (hidden)                       │
│                                                              │
│ TO CHANGE: Edit the switch statement in MessageList          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ LAYER 4: MARKDOWN → HTML (how markdown becomes HTML)         │
│                                                              │
│ WHO: ReactMarkdown + remark plugins                          │
│ WHERE: Each component that renders markdown:                 │
│   TextMessage.tsx        (ai_text bubbles)                   │
│   HumanInputPrompt.tsx   (question prompts)                  │
│   ApprovalGate.tsx       (approval summaries)                │
│   NotificationMessage.tsx (notifications)                    │
│                                                              │
│ PLUGINS:                                                     │
│   remarkGfm    → tables, strikethrough, task lists           │
│   remarkBreaks → single \n becomes <br> (line breaks)        │
│                                                              │
│ TRANSFORMS:                                                  │
│   **bold**     → <strong>                                    │
│   # heading    → <h1>                                        │
│   ## heading   → <h2>                                        │
│   - list       → <ul><li>                                    │
│   1. list      → <ol><li>                                    │
│   | table |    → <table>                                     │
│   \n           → <br> (via remarkBreaks)                     │
│   emoji        → passed through as-is (Unicode)              │
│                                                              │
│ PREPROCESSORS (before ReactMarkdown):                        │
│   HumanInputPrompt.tsx → ensureNumberedOptions()             │
│     Detects option lines and adds 1. 2. 3. X) numbering     │
│   WebSocketContext.tsx → approval detection                   │
│     Converts approval-style human_input to ApprovalGate      │
│                                                              │
│ TO CHANGE: Edit ReactMarkdown props or add custom            │
│ components via the `components` prop                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ LAYER 5: CSS STYLING (font sizes, spacing, colors)           │
│                                                              │
│ WHO: Tailwind CSS "prose" classes + component classes         │
│ WHERE:                                                       │
│   TextMessage.tsx    → "prose prose-sm dark:prose-invert"     │
│   HumanInputPrompt  → "prose prose-sm dark:prose-invert"     │
│   ApprovalGate      → "prose prose-sm dark:prose-invert"     │
│   NotificationMsg   → "prose prose-sm dark:prose-invert"     │
│                                                              │
│ "prose prose-sm" applies Tailwind Typography defaults:        │
│   <p>       → font-size: 14px, margin: 1.14em 0             │
│   <strong>  → font-weight: 600                               │
│   <h1>      → font-size: ~30px (2.14em)                     │
│   <h2>      → font-size: ~20px (1.43em)                     │
│   <h3>      → font-size: ~16px (1.14em)                     │
│   <li>      → margin: 0.29em 0                               │
│   <table>   → full width with borders                        │
│   line-height: 1.71                                          │
│                                                              │
│ BUBBLE CONTAINERS:                                           │
│   AI bubble:     "bg-gray-100 rounded-2xl px-4 py-2"        │
│     → TextMessage.tsx (justify-start)                        │
│   User bubble:   "bg-blue-600 text-white rounded-2xl"        │
│     → TextMessage.tsx (justify-end)                          │
│   Human input:   "border-yellow-200 bg-yellow-50 p-4"        │
│     → HumanInputPrompt.tsx                                   │
│   Approval gate: "border-blue-200 bg-blue-50 p-4"            │
│     → ApprovalGate.tsx                                       │
│   Stage banner:  "bg-gray-50 px-4 py-2"                      │
│     → StageBanner.tsx                                        │
│   Error:         "border-red-200 bg-red-50"                   │
│     → ErrorMessage.tsx                                       │
│   Notification:  "bg-blue-50"                                 │
│     → NotificationMessage.tsx                                │
│                                                              │
│ TO CHANGE:                                                   │
│   Option A: Change "prose-sm" to "prose" or "prose-lg"       │
│   Option B: Add custom CSS in styles/theme.css               │
│   Option C: Add Tailwind classes to specific elements        │
│   Option D: Use ReactMarkdown `components` prop for          │
│             custom rendering of specific HTML elements       │
└─────────────────────────────────────────────────────────────┘
```


## 4. Quick Reference: What to Edit for Common Changes

| What you want to change | File to edit | Layer |
|---|---|---|
| Greeting/resume/restart flow | `main_ws.py` (section 11) | Agent |
| What events reach the browser | `stream_adapter.py` | Transport |
| Which files show in Document Viewer | `WebSocketContext.tsx` (artifact filter) | Transport |
| How files are fetched for viewer | `WebSocketContext.tsx` (fetchArtifact) | Transport |
| Which component renders a message type | `ChatPanel.tsx` (MessageList switch) | Component |
| How AI text bubbles look | `TextMessage.tsx` | Component + CSS |
| How question prompts look | `HumanInputPrompt.tsx` | Component + CSS |
| How approval gates look | `ApprovalGate.tsx` | Component + CSS |
| How stage banners look | `StageBanner.tsx` | Component + CSS |
| Question numbering (1/2/3/X) | `HumanInputPrompt.tsx` (ensureNumberedOptions) | Preprocessor |
| Approval detection (buttons vs text) | `WebSocketContext.tsx` (approval detection) | Preprocessor |
| Font sizes for all markdown | Tailwind `prose-sm` class in each component | CSS |
| Bubble colors/borders/padding | Tailwind classes in each component | CSS |
| Global theme/colors | `styles/theme.css` | CSS |
| Stage agent behavior/prompts | `agents/inception/*.py` or `agents/construction/*.py` | Agent |
| Graph structure/stage order | `orchestrator.py` | Agent |
| State persistence format | `tools/state.py` | Agent |
| S3 file operations | `tools/file_ops.py` | Agent |
| Gateway Lambda tools | `code/agentcore-gateway/lambda-tools/handler.py` | Infra |
| CDK infrastructure | `cdk/stack.ts` | Infra |


## 5. How to Give Clear Instructions for Changes

When asking for a visual change, specify:

1. **Which message type** — ai_text, human_input, approval_required, stage_start, etc.
2. **Which layer** — content (agent prompt), transport (bubble boundaries), component (React), or CSS (styling)
3. **What the current output looks like** — paste the text
4. **What you want it to look like** — describe or paste the desired output

### Example instruction format:

> "In the **ai_text** bubble rendered by **TextMessage.tsx**, when the text contains
> '📍 Workflow Progress', I want the font size to be 28px. The stage lines below it
> (✅, 🔄, 🔜) should be 18px. This is a **Layer 5 (CSS)** change — add custom
> rendering in TextMessage.tsx using ReactMarkdown's `components` prop."

### For content changes (what the agent says):

> "In **workspace_detection.py**, update the **SYSTEM_PROMPT** to include a markdown
> table for the workspace analysis results instead of a bullet list."

### For bubble boundary changes:

> "I want the progress banner and the description text to be in **separate bubbles**.
> This is a **Layer 2 (Transport)** change — the agent needs to send a separator
> between them, or the frontend needs to split the accumulated text."
