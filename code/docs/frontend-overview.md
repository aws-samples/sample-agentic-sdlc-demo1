# Frontend Overview

This document describes the React SPA that serves as the user interface for Agentic SDLC Transformation. It covers the framework, layout, pages, components, data flow, and authentication.

## Technology Stack

| Concern | Library | Version |
|---------|---------|---------|
| Framework | React | 18.3.1 |
| Build tool | Vite | 6.3.5 |
| Routing | react-router | 7.13.0 |
| Styling | Tailwind CSS v4 | 4.1.12 |
| Auth | aws-amplify | 6.16.3 |
| UI primitives | Radix UI (dialog, select, alert-dialog, label, slot) | Various |
| Icons | lucide-react | 0.487.0 |
| Markdown | react-markdown + remark-gfm + remark-breaks | 10.1.0 |
| Diagrams | mermaid | 11.13.0 |
| Resizable panels | react-resizable-panels | 2.1.7 |
| State management | React Context (AuthContext, WebSocketContext) | Built-in |

Source: `code/ui-frontend/package.json`

## Application Entry Point

`code/ui-frontend/app/main.tsx` loads runtime configuration from `/config.json` (deployed by CDK) or falls back to `VITE_*` environment variables for local development. It then configures AWS Amplify for Cognito auth and renders the `<App>` component.

## Routing

Defined in `code/ui-frontend/app/routes.tsx`:

| Path | Component | Auth Required | Description |
|------|-----------|---------------|-------------|
| `/` | `LandingPage` | No (shows welcome) / Yes (shows projects) | Landing page with sign-in prompt or project grid |
| `/sign-in` | `SignInPage` | No | Email/password sign-in form |
| `/sign-up` | `SignUpPage` | No | Registration form with role selection |
| `/project/:projectId` | `ProjectWorkspace` | Yes | Split-pane workspace with chat and document viewer |
| `/dashboard` | `AdminDashboard` | Yes (`maintenance-specialist`) | Read-only cross-project operations dashboard |
| `*` | Redirect to `/` | — | Catch-all redirect |

## Pages

### Landing Page (`code/ui-frontend/app/components/landingPage.tsx`)

**Unauthenticated view:**
- Centered hero section with "Agentic SDLC Transformation" title and tagline
- Single "Sign In" button navigating to `/sign-in`
- Gradient background (blue to indigo)

**Authenticated view:**
- Top header bar with username, role badge, and "Sign Out" button
- A **Dashboard** button (next to Sign Out) shown only when the signed-in user is in the `maintenance-specialist` group, navigating to `/dashboard`
- "Your Projects" heading with "Create Project" button
- Project grid (responsive: 1 column mobile, 2 medium, 3 large)
- Each project is a card showing name, description, type badge, and creation date
- Hover reveals a delete button (trash icon) in the top-right corner
- Empty state shows a folder icon with "No projects yet" message and CTA button
- Delete confirmation via AlertDialog (Radix UI)

**Data flow:**
- Projects are stored in `localStorage` keyed by `projects_{user.sub}`
- No backend API for project CRUD — purely client-side persistence
- Clicking a project card navigates to `/project/{projectId}`

**Interactive components:**
- "Create Project" button → opens `CreateProjectDialog`
- Project card → navigates to workspace
- Delete button → opens confirmation dialog
- "Sign Out" button → calls `amplifySignOut`, clears state

### Operations Dashboard (`code/ui-frontend/app/components/AdminDashboard.tsx`)

Read-only cross-project view for the **Maintenance Specialist** persona (route `/dashboard`).

- **Access:** gated to the `maintenance-specialist` Cognito group. A non-maintenance user that lands on `/dashboard` sees a "Dashboard unavailable" message and never fetches data. (Presentational gate; `GET /projects` is already broadly readable.)
- **Content:** an all-projects table (name, created-by, **stage**, **assigned persona**, #transitions, last activity), a per-persona workload summary, and a by-stage rollup.
- **Aggregation:** pure helpers in `app/services/dashboardMetrics.ts` (`buildProjectRows`, `summarizeByPersona`, `summarizeTotals`, `resolvePersonaLabel`, `stageLabel`) resolve the assigned persona via the workflow registry (with a known-role fallback so future personas render) and humanize the handoff state.
- **Data source:** `projectService.listProjects()` — no new backend; read-only (performs no transitions).

### Sign In Page (`code/ui-frontend/app/components/SignInPage.tsx`)

- Centered card on gradient background
- Email and password input fields
- "Sign In" submit button
- Error message display (red text below inputs)
- "Don't have an account? Sign Up" link

**Data flow:** Calls `amplifySignIn` from aws-amplify. On success, `buildCognitoUser()` extracts user info from the ID token (sub, username, email, groups, role) and stores it in AuthContext. Navigates to `/`.

### Sign Up Page (`code/ui-frontend/app/components/SignUpPage.tsx`)

- Centered card on gradient background
- Username, email, password input fields
- Role dropdown (Select component) with 6 SDLC roles:
  - Requirements Analyst, System Architect, Software Developer, QA Engineer, DevOps Engineer, Maintenance Specialist
- "Sign Up" submit button
- Error handling for duplicate emails

**Data flow:** Calls `amplifySignUp` with email as username and preferred_username as display name. Auto-sign-in is disabled — user is redirected to `/sign-in` after registration. The Cognito auto-confirm Lambda trigger skips email verification.

### Project Workspace (`code/ui-frontend/app/components/ProjectWorkspace.tsx`)

This is the main working view. It wraps content in a `WebSocketProvider`.

**Layout:**
- Top navigation bar: back arrow, project name, connection status dot (green/yellow/red/gray), the signed-in user's name + persona label, and a "Sign Out" button
- Below the nav bar: a horizontally resizable split pane (50/50 default, 25% minimum each side)
  - Left panel: ChatPanel
  - Right panel: DocumentViewer
- On mobile (< 768px): tab-based toggle between Chat and Documents instead of split pane

**Connection lifecycle:**
1. Component mounts → loads project name from localStorage
2. Calls `connect(projectId, "Start workflow", projectName)` on the WebSocketContext
3. WebSocket opens → sends init message with `project_id`, `project_name`, `request`
4. Receives streaming messages → dispatched to chat and artifact state
5. On unmount → calls `disconnect()`

**Interactive components:**
- Back arrow → disconnects WebSocket, navigates to `/`
- Chat/Documents tab toggle (mobile only)
- Resizable panel handle (desktop)

## Chat Panel (`code/ui-frontend/app/components/chat/ChatPanel.tsx`)

The chat panel displays the conversation between the user and the agent system.

**Layout:**
- Header: project name + current stage badge (blue pill)
- Scrollable message list (auto-scrolls unless user scrolled up)
- Input bar: upload button (placeholder), text input, send button, thumbs up/down buttons (placeholder)

**Message types rendered:**

| Message Type | Component | Visual |
|-------------|-----------|--------|
| `user` | `TextMessage` | Right-aligned bubble |
| `ai_text` | `TextMessage` | Left-aligned bubble with markdown rendering |
| `stage_start` / `stage_complete` | `StageBanner` | Colored banner with stage name |
| `approval_required` | `ApprovalGate` | Blue card with markdown summary + clickable option buttons |
| `human_input` | `HumanInputPrompt` | Yellow card with markdown prompt + text input + send button |
| `human_input_notify` | `NotificationMessage` | Subtle notification text |
| `error` | `ErrorMessage` | Red error card |
| `workflow_complete` | `CompletionBanner` | Green banner with checkmark |
| `tool_use` | (hidden) | Not rendered — agent text already describes tool usage |

**Text accumulation:** Consecutive `text_chunk` WebSocket messages are accumulated into a single `ai_text` ChatMessage bubble rather than creating separate bubbles per chunk (`WebSocketContext.tsx`, `handleTextChunk`).

**Approval gate detection:** The WebSocket context detects `human_input` prompts that contain both "Approve & Continue" and "Request Changes" patterns and converts them to `approval_required` messages so they render as clickable buttons instead of a text input (`WebSocketContext.tsx`, line 120).

## Document Viewer (`code/ui-frontend/app/components/document/DocumentViewer.tsx`)

The document viewer displays artifacts generated by the agent during the SDLC workflow.

**Layout:**
- Tab bar (when multiple artifacts exist): horizontal scrollable tabs showing filenames
- Content area: renders the selected artifact

**Content rendering:**
- Markdown files (`.md`): rendered with `ReactMarkdown` + `remarkGfm`, styled with Tailwind Typography (`prose` classes). Mermaid code blocks are rendered as interactive diagrams via the `MermaidDiagram` component.
- Other files: rendered as preformatted text in a code block.

**States:**
- Empty: "Documents will appear here when the agent generates artifacts" with file icon
- Loading: spinning loader
- Error: red error card with file path, error message, and "Retry" button
- Content: rendered markdown or preformatted text

**Data flow:**
- Artifacts arrive via `artifact_updated` WebSocket messages containing a file path and presigned S3 URL
- The WebSocket context fetches artifact content via the presigned URL (`fetchArtifact` in `WebSocketContext.tsx`)
- Internal files (audit logs, question files) are filtered out and not displayed
- The first artifact is auto-selected when it arrives

## File Browser (`code/ui-frontend/app/components/files/FileBrowser.tsx`)

A `react-arborist` tree of the project's S3 artifacts, built by `buildFileTree` in `code/ui-frontend/app/services/s3Service.ts` from a `listFiles` call scoped to the project prefix. Selecting a file opens it in the Document Viewer (fetched by its real S3 key).

**Display-only folder relabel:** `buildFileTree` applies `displaySegment` so the artifact root folder, stored in S3 as `aidlc-docs/`, is **shown as `agentic-sdlc`** in the tree. This is a label-only transform — the underlying S3 keys, `.md` file names (including `aidlc-state.md`), agent write paths, and everything fetched are unchanged. Only folder labels are remapped; file names are kept verbatim.

The relabel is centralized in `code/ui-frontend/app/lib/displayLabels.ts` (`FOLDER_DISPLAY_NAMES`, `displaySegment`, and `relabelArtifactPaths`) — the single source of truth. It is applied to three display surfaces:
- the **file browser tree** (`s3Service.buildFileTree` → `displaySegment`),
- **assistant chat text** (`TextMessage.tsx` → `relabelArtifactPaths`, covering agent narration and the `[STAGE_SUMMARY]` table), and
- the **in-app document viewer** markdown (`DocumentViewer.tsx` → `relabelArtifactPaths`, so e.g. the construction brief reads `agentic-sdlc/...`).

To change or add a display label, edit `FOLDER_DISPLAY_NAMES` (covered by `__tests__/unit/display-labels.test.ts` and `__tests__/unit/file-tree-display.test.ts`).

> Note: the relabel is cosmetic and the real S3 keys remain `aidlc-docs/`. A few surfaces intentionally still show the real key: the **raw downloaded file** content (the relabel is display-time only), the Document Viewer **error path** (`{artifact.filePath}`), and the **SA review panel** artifact render. Do not rename the stored prefix to match — see `.kiro/steering/10-stable-identifiers.md`.

## Authentication Flow

Managed by `AuthContext` (`code/ui-frontend/app/context/AuthContext.tsx`):

1. On app mount, `buildCognitoUser()` checks for an existing Amplify session
2. If a valid session exists, extracts user info from the Cognito ID token (sub, username, email, groups) and stores the access token
3. `signIn` calls `amplifySignIn` then refreshes user state
4. `signUp` calls `amplifySignUp` (auto-sign-in disabled)
5. `signOut` calls `amplifySignOut` and clears all state

The access token is used for WebSocket authentication via the `Sec-WebSocket-Protocol` header with base64url encoding (`WebSocketContext.tsx`, line 175).

Amplify is configured in `code/ui-frontend/app/lib/amplify.ts` using values from the runtime config (Cognito User Pool ID, PKCE Client ID, Cognito domain).

## WebSocket Communication

Managed by `WebSocketContext` (`code/ui-frontend/app/context/WebSocketContext.tsx`):

**Connection:**
- URL format: `wss://bedrock-agentcore.{region}.amazonaws.com/runtimes/{runtimeArn}/ws`
- Auth: Bearer token via `Sec-WebSocket-Protocol: base64UrlBearerAuthorization.{base64url-token}`
- Session ID: `{project_name}_{user_email}` (sanitized to `[a-zA-Z0-9_-]`)

**Reconnection:** Up to 3 retries with exponential backoff (1s, 2s, 4s). Error messages shown in chat on each retry.

**Message types received** (defined in `code/ui-frontend/app/types/ws-messages.ts`):
`text_chunk`, `stage_start`, `stage_complete`, `tool_use`, `approval_required`, `human_input`, `human_input_notify`, `artifact_updated`, `workflow_complete`, `error`

**Sending:** `sendMessage(text)` sends plain text over the WebSocket and adds a `user` type ChatMessage to the local state.

## State Management

The app uses two React Context providers — no external state library:

1. **AuthContext** — User authentication state, access token, sign-in/sign-up/sign-out functions. Wraps the entire app.
2. **WebSocketContext** — Connection status, chat messages, current stage, artifacts, send/connect/disconnect functions. Wraps only the `ProjectWorkspace` component.

Project data (name, description, type) is stored in `localStorage` per user. There is no backend API for project CRUD.

## Responsive Design

- Mobile breakpoint: 768px (`useIsMobile` hook in `ProjectWorkspace.tsx`)
- Desktop: side-by-side resizable panels (chat + documents)
- Mobile: tab-based toggle between chat and documents
- All pages use responsive grid layouts (1/2/3 columns)
- Cards, forms, and dialogs are max-width constrained

## Accessibility Notes

- Buttons have `aria-label` attributes (back, sign out, send, upload, thumbs up/down)
- Connection status dot has `aria-label` with status text
- Form inputs have associated `<Label>` elements via `htmlFor`/`id`
- ErrorBoundary provides a reload button on crash
- Disabled states use `disabled:cursor-not-allowed disabled:opacity-50`
- Color contrast relies on Tailwind's default palette — manual WCAG audit recommended
