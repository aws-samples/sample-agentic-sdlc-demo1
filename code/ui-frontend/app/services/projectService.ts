/**
 * Project API client — replaces the browser-local project list with the
 * shared, server-side Project Store (design Section 2d, Req 3.4).
 *
 * Every call:
 *   1. Reads the API base URL from `/config.json` (`projectApiUrl`, set by
 *      CDK at deploy time, design Section 2b).
 *   2. Attaches the Cognito **ID token** as the `Authorization: Bearer`
 *      header — the API Gateway Cognito authorizer extracts the caller's
 *      groups from the verified JWT and uses them for RBAC server-side
 *      (design Section 4b).
 *   3. Throws a {@link ProjectApiError} on a non-2xx response, carrying the
 *      HTTP status and the API's error code/message (e.g. `FORBIDDEN`,
 *      `ILLEGAL_TRANSITION`, `ETAG_MISMATCH`).
 *
 * The `permittedActions` helper is a *purely registry-derived* presentational
 * gate (design Section 4a, Req 2.1) — it does **not** authorize anything by
 * itself; the Project API re-checks ownership on every state-changing route
 * (design Section 4b, Req 2.2/2.3).
 */

import { fetchAuthSession } from 'aws-amplify/auth';

import { loadConfig } from '../lib/config';
import { getRegistry, type WorkflowRegistry } from '../lib/workflowRegistry';

// ---------------------------------------------------------------------------
// Project record — mirrors the JSON document persisted by the Project API
// (design Section 2a). Field names are snake_case to match the wire format.
// ---------------------------------------------------------------------------

/** A single entry in `assignment_history` (design Sections 2a / 3c, Req 4.6). */
export interface AssignmentHistoryEntry {
  /** Source handoff state, or `null` for the first ("created") entry. */
  from: string | null;
  /** Target handoff state. */
  to: string;
  /** Identity of the actor who performed the transition. */
  actor: string;
  /** ISO-8601 timestamp (seconds precision). */
  timestamp: string;
  /** Free-form note (e.g. the request-changes reason); may be `null`. */
  note: string | null;
}

/**
 * Server-side project record returned by the Project API. The shape matches
 * `_platform/projects/{project_uuid}.json` (design Section 2a).
 */
export interface ProjectRecord {
  /** Stable identifier; primary key for the record. */
  project_uuid: string;
  /** S3-artifact slug derived from the project name (design Section 2a). */
  project_id: string;
  /** Display name. */
  name: string;
  /** Free-form description. */
  description: string;
  /** Project category (e.g. "development"). */
  type: string;
  /** ISO-8601 creation timestamp. */
  created_at: string;
  /** Creating user (Cognito email/username). */
  created_by: string;
  /** Current handoff state (registry state name, e.g. "draft"). */
  handoff_state: string;
  /** Cognito group name that owns the project at its current state. */
  owning_role: string;
  /** S3 prefix the project's artifacts live under (equals `project_id`). */
  artifact_prefix: string;
  /** Append-only history of every transition applied to the record. */
  assignment_history: AssignmentHistoryEntry[];
  /**
   * Linked implementation repository (C7 Git round-trip). Present (as `null`
   * defaults) from creation; populated by {@link linkRepo} and the ingestion
   * pipeline. `repo_ingest_status` is one of
   * `not_linked` | `pending` | `ready` | `failed`.
   */
  repo_url?: string | null;
  repo_provider?: string | null;
  repo_branch?: string | null;
  repo_commit?: string | null;
  repo_ingest_status?: string | null;
  repo_last_synced_at?: string | null;
  repo_ingest_error?: string | null;
  /** QA test-runner status (C7): not_run | running | passed | failed. */
  qa_test_status?: string | null;
  qa_test_summary?: string | null;
  qa_test_last_run_at?: string | null;
}

/** Input accepted by {@link createProject}. */
export interface CreateProjectInput {
  /** Required, human-readable project name. */
  name: string;
  /** Free-form description (defaults to ""). */
  description?: string;
  /** Project category (defaults to "development"). */
  type?: string;
  /**
   * Optional client-supplied UUID. When omitted the server mints one. The
   * Frontend uses this to keep the URL route stable across the round-trip.
   */
  project_uuid?: string;
}

/** Body accepted by {@link transition}. */
export interface TransitionAction {
  /**
   * High-level action verb resolved by the Project API through the registry
   * (e.g. "assign", "approve", "request-changes", "design-complete").
   */
  action: string;
  /** Optional free-form note attached to the history entry. */
  note?: string;
  /**
   * Optional explicit target state. When supplied it overrides `action`
   * resolution (matched by the API's `_resolve_target_state`).
   */
  target_state?: string;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/** Thrown when the Project API returns a non-2xx response. */
export class ProjectApiError extends Error {
  /** HTTP status code. */
  readonly status: number;
  /** Server-side error code, when the body included one. */
  readonly code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = 'ProjectApiError';
    this.status = status;
    this.code = code;
  }
}

// ---------------------------------------------------------------------------
// HTTP plumbing
// ---------------------------------------------------------------------------

/** Resolve the Project API base URL with no trailing slash. */
async function getProjectApiBaseUrl(): Promise<string> {
  const config = await loadConfig();
  const url = config.projectApiUrl ?? '';
  if (!url) {
    throw new ProjectApiError(
      0,
      'Project API URL is not configured (set `projectApiUrl` in /config.json).',
      'CONFIG_MISSING',
    );
  }
  return url.replace(/\/$/, '');
}

/**
 * Return the Cognito ID token from the active Amplify session.
 *
 * The ID token (not the access token) is sent as the `Authorization: Bearer`
 * header because the API Gateway Cognito authorizer extracts the caller's
 * `cognito:groups` from the verified ID token's claims (design Section 4b).
 */
async function getIdToken(): Promise<string> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString();
  if (!token) {
    throw new ProjectApiError(
      401,
      'No Cognito ID token available. Sign in before calling the Project API.',
      'UNAUTHENTICATED',
    );
  }
  return token;
}

interface RequestOptions {
  method: 'GET' | 'POST' | 'DELETE';
  path: string;
  body?: unknown;
}

/**
 * Issue a request to the Project API.
 *
 * The ID token is attached on every call so the server-side authorizer can
 * verify the caller's identity and group membership (design Section 4b). On a
 * non-2xx response a {@link ProjectApiError} is thrown carrying the HTTP
 * status and the API's structured error code/message when available.
 */
async function request<T>({ method, path, body }: RequestOptions): Promise<T> {
  const [baseUrl, idToken] = await Promise.all([getProjectApiBaseUrl(), getIdToken()]);

  const init: RequestInit = {
    method,
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${idToken}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
  };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }

  const res = await fetch(`${baseUrl}${path}`, init);

  if (!res.ok) {
    let serverMessage: string | undefined;
    let serverCode: string | undefined;
    try {
      const errBody = (await res.json()) as { error?: string; code?: string };
      serverMessage = errBody.error;
      serverCode = errBody.code;
    } catch {
      // No / non-JSON body — fall through to the generic message below.
    }
    throw new ProjectApiError(
      res.status,
      serverMessage ?? `Project API request failed (HTTP ${res.status})`,
      serverCode,
    );
  }

  // 204 No Content support: callers asking for `void` will get `undefined`.
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * List every project visible to the caller.
 *
 * Listing is broad on purpose (design Section 2c, Req 3.2): both personas see
 * cross-creator projects so a project never disappears from a collaborator's
 * view. Action permissions are gated by `owning_role` on the server, not by
 * visibility.
 */
export async function listProjects(): Promise<ProjectRecord[]> {
  const result = await request<{ projects: ProjectRecord[] }>({
    method: 'GET',
    path: '/projects',
  });
  return result.projects ?? [];
}

/**
 * Create a new project. Server seeds the initial handoff state from the
 * registry's `initial_state` and seeds an `assignment_history` "created"
 * entry (design Section 3a / Req 4.1).
 */
export async function createProject(input: CreateProjectInput): Promise<ProjectRecord> {
  return request<ProjectRecord>({
    method: 'POST',
    path: '/projects',
    body: {
      name: input.name,
      description: input.description ?? '',
      type: input.type ?? 'development',
      ...(input.project_uuid ? { project_uuid: input.project_uuid } : {}),
    },
  });
}

/**
 * Read a single project record. Returns the same artifact prefix to both
 * personas for the same `project_uuid` (design Section 2a, Req 3.3).
 */
export async function getProject(projectUuid: string): Promise<ProjectRecord> {
  return request<ProjectRecord>({
    method: 'GET',
    path: `/projects/${encodeURIComponent(projectUuid)}`,
  });
}

/**
 * Apply a handoff transition.
 *
 * The Project API resolves the request's `action` through the registry and
 * validates the resulting target with `validate_transition` (design Sections
 * 3b/3c, Req 4.3-4.6). On illegal transitions the server returns HTTP 422 and
 * the record is left unchanged; on a concurrent-write conflict it returns
 * HTTP 409 — both surface here as {@link ProjectApiError}.
 */
export async function transition(
  projectUuid: string,
  action: TransitionAction,
): Promise<ProjectRecord> {
  return request<ProjectRecord>({
    method: 'POST',
    path: `/projects/${encodeURIComponent(projectUuid)}/transition`,
    body: action,
  });
}

/**
 * Delete a project. Server enforces owner-gating before removing the record
 * (design Section 4b).
 */
export async function deleteProject(projectUuid: string): Promise<void> {
  await request<unknown>({
    method: 'DELETE',
    path: `/projects/${encodeURIComponent(projectUuid)}`,
  });
}

/** Input accepted by {@link linkRepo}. */
export interface LinkRepoInput {
  /** https URL to a supported Git host (github.com / gitlab.com), owner/repo. */
  repo_url: string;
  /** Branch to ingest (defaults to `main` server-side when omitted). */
  branch?: string;
  /**
   * Optional access token for a **private** repository. Sent once to the
   * Project API, which stores it in Secrets Manager (never persisted on the
   * project record, never returned). Omit for public repos.
   */
  git_token?: string;
}

/**
 * Link the developer's implementation repository to a project (C7 Git
 * round-trip). Owner-gated server-side to the current owning role (the
 * Software Developer at `assigned_to_dev`); the URL is validated against an
 * allowlist of public Git hosts. Records repo metadata (`repo_ingest_status`
 * becomes `pending`); ingestion itself is triggered when the developer submits
 * (the transition into `implementation_submitted`). A supplied `git_token` is
 * stored in Secrets Manager for private-repo cloning and never echoed back.
 */
export async function linkRepo(
  projectUuid: string,
  input: LinkRepoInput,
): Promise<ProjectRecord> {
  return request<ProjectRecord>({
    method: 'POST',
    path: `/projects/${encodeURIComponent(projectUuid)}/link-repo`,
    body: {
      repo_url: input.repo_url,
      ...(input.branch ? { branch: input.branch } : {}),
      ...(input.git_token ? { git_token: input.git_token } : {}),
    },
  });
}

/**
 * Start the QA test-runner for a project (C7). Owner-gated server-side to the
 * QA Engineer. Marks the record `qa_test_status="running"` and kicks off the
 * CodeBuild test job, which reports pass/fail back asynchronously.
 */
export async function runTests(projectUuid: string): Promise<ProjectRecord> {
  return request<ProjectRecord>({
    method: 'POST',
    path: `/projects/${encodeURIComponent(projectUuid)}/run-tests`,
  });
}

// ---------------------------------------------------------------------------
// permittedActions — registry-derived presentational gate (design Section 4a)
// ---------------------------------------------------------------------------

/** A single action a user is presentationally allowed to take on a project. */
export interface PermittedAction {
  /** Action verb to send as the `action` field of {@link transition}. */
  action: string;
  /** Whether this is a forward step or a request-changes bounce. */
  kind: 'forward' | 'request-changes';
  /** Resolved target handoff state (from the registry). */
  target: string;
  /** UI label suggested for the action button. */
  label: string;
  /** When `true`, the UI should collect a free-form note before transitioning. */
  acceptsNote: boolean;
}

/**
 * Default forward action verbs by source state. The Project API's
 * `_FORWARD_ACTIONS` set already accepts each of these as a forward intent
 * (design Section 3 / `project_api.py`). Every value is a forward synonym so
 * the API never silently advances on an unknown action.
 *
 * Choices map to the user-facing intent at each boundary:
 *   - `requirements_ready` -> `assign`        (BA assigns to SA, Req 6.4)
 *   - `assigned_to_sa`     -> `approve`       (SA approves BA artifacts, Req 7.4)
 *   - `in_design` /
 *     `design_ready`       -> `design-complete` (SA marks design done, Req 7.7)
 *   - any other state with a forward edge falls back to the generic `advance`.
 */
const FORWARD_ACTION_BY_STATE: Record<string, string> = {
  requirements_ready: 'assign',
  assigned_to_sa: 'approve',
  in_design: 'design-complete',
  design_ready: 'design-complete',
};

/** Default forward-button label by source state, derived from the registry. */
function forwardLabel(currentState: string, registry: WorkflowRegistry): string {
  const target = registry.forward(currentState);
  if (!target) return 'Continue';
  switch (currentState) {
    case 'requirements_ready':
      return `Assign to ${registry.displayLabel(registry.owningRole(target))}`;
    case 'assigned_to_sa':
      return 'Approve';
    case 'in_design':
      return 'Mark design ready';
    case 'design_ready':
      return 'Mark ready for development';
    case 'assigned_to_qa':
      return 'Start QA';
    case 'in_qa':
      return 'Mark QA passed';
    case 'assigned_to_devops':
      return 'Start deployment';
    case 'in_deployment':
      return 'Mark deployed';
    default:
      return 'Continue';
  }
}

/** Default forward-action verb for `currentState`. Falls back to `advance`. */
function forwardAction(currentState: string): string {
  return FORWARD_ACTION_BY_STATE[currentState] ?? 'advance';
}

/**
 * Return the set of actions the caller's groups permit on a project.
 *
 * The set is derived purely from the registry (design Section 4a, Req 2.1):
 *   - When the caller's groups do not contain the project's `owning_role`,
 *     the result is empty (the caller is not the owner of the current state).
 *   - Otherwise the registry's `forward(state)` produces a forward action and
 *     `requestChangesInto(state)` produces a request-changes action when one
 *     is configured.
 *
 * This is presentational only; the Project API re-checks ownership on every
 * state-changing route (design Section 4b, Req 2.2/2.3).
 *
 * The default registry from `/config.json` is used unless `registry` is given
 * (tests pass an explicit registry to exercise alternative chains).
 */
export function permittedActions(
  record: Pick<ProjectRecord, 'handoff_state' | 'owning_role'>,
  userGroups: string[],
  registry: WorkflowRegistry = getRegistry(),
): PermittedAction[] {
  if (!userGroups.includes(record.owning_role)) {
    return [];
  }

  const actions: PermittedAction[] = [];

  const fwd = registry.forward(record.handoff_state);
  if (fwd != null) {
    actions.push({
      action: forwardAction(record.handoff_state),
      kind: 'forward',
      target: fwd,
      label: forwardLabel(record.handoff_state, registry),
      acceptsNote: false,
    });
  }

  const rc = registry.requestChangesInto(record.handoff_state);
  if (rc != null) {
    actions.push({
      action: 'request-changes',
      kind: 'request-changes',
      target: rc.target,
      label: rc.label,
      acceptsNote: true,
    });
  }

  return actions;
}
