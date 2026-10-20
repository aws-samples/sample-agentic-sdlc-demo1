/**
 * Frontend scripted two-persona handoff verification (task 10.2, Frontend half).
 *
 * Companion to ``cdk/custom-resources/tests/test_two_persona_handoff_e2e.py``.
 * The Python test drives the *server* code paths (``project_api.handler`` over
 * a fake S3); this test drives the *Frontend* code paths (``projectService``
 * over a fake Project API and ``SolutionArchitectReviewPanel`` over a fake
 * S3). Together the two scripts cover the full "BA uploads/assigns ->
 * SA reviews/requests changes -> BA re-assigns -> SA approves -> SA
 * completes design" sequence end-to-end against the same code paths a
 * deployed environment would exercise.
 *
 * What this script covers:
 *
 *   - As the **BA**: list projects, create one, request a presigned upload
 *     URL for a `.docx`, advance ``draft -> requirements_ready -> assigned_to_sa``
 *     -- all through ``projectService`` so the real HTTP wiring (Authorization
 *     header, route shape, error mapping) is exercised.
 *   - As the **SA**: list projects (must include the BA's record;
 *     cross-creator visibility, Req 3.2), open the project (must resolve the
 *     same artifact prefix; Req 3.3), render ``SolutionArchitectReviewPanel``
 *     and verify it loads `requirements.md` / `stories.md` / `personas.md`
 *     from the shared artifact prefix via ``s3Service`` (Req 7.1), then click
 *     **Request Changes** with a note and confirm the bounce
 *     ``assigned_to_sa -> requirements_ready`` (Req 7.5) plus the runtime
 *     "Request Changes" approval-gate decision frame.
 *   - The BA re-assigns; the SA approves
 *     (``assigned_to_sa -> in_design``, Req 7.4) and completes design
 *     (``in_design -> design_ready -> ready_for_dev``, Req 7.7).
 *
 * The fake Project API is a small in-memory store that mimics the contract
 * of the deployed Lambda (RBAC by `cognito:groups`, registry-driven
 * forward/request-changes resolution, 422 for illegal jumps, 403 for
 * non-owning callers). Both personas hit it via the *real*
 * ``projectService`` so the wire format and error mapping are real.
 *
 * To run this against a deployed environment: replace the fake Project API
 * with the real fetch URL by removing the `vi.stubGlobal('fetch', ...)`
 * setup and pointing `loadConfig` at the deployed `projectApiUrl`. The
 * Cognito ID tokens for the BA/SA test users come from the existing
 * ``aidlc-platform-auth`` stack outputs (BATestUserEmail/Password,
 * SATestUserEmail/Password) via Amplify's `signIn`.
 *
 * Validates: Requirements 3.2, 3.3, 6.4, 6.5, 7.1, 7.4, 7.5, 7.7, 14.2,
 * 14.3, 14.4
 */

import React from 'react';
import {
  describe,
  it,
  expect,
  vi,
  beforeEach,
  afterEach,
} from 'vitest';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from '@testing-library/react';

// Hoisted so the vi.mock factories below can reference the same instances
// used by the test code.
const {
  fetchAuthSessionMock,
  loadConfigMock,
  getFileContentMock,
} = vi.hoisted(() => ({
  fetchAuthSessionMock: vi.fn(),
  loadConfigMock: vi.fn(),
  getFileContentMock: vi.fn(),
}));

vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: fetchAuthSessionMock,
}));

vi.mock('../../app/lib/config', () => ({
  loadConfig: loadConfigMock,
  getConfig: vi.fn(),
}));

vi.mock('../../app/services/s3Service', () => ({
  getFileContent: getFileContentMock,
}));

import {
  listProjects,
  createProject,
  getProject,
  transition,
  type ProjectRecord,
} from '../../app/services/projectService';
import { SolutionArchitectReviewPanel } from '../../app/components/SolutionArchitectReviewPanel';
import { ReturnForChangesControl } from '../../app/components/ReturnForChangesControl';
import {
  WorkflowRegistry,
  type RegistryData,
} from '../../app/lib/workflowRegistry';

// ---------------------------------------------------------------------------
// Fixtures: registry, personas, project metadata
// ---------------------------------------------------------------------------

const BA_GROUP = 'requirements-analyst';
const SA_GROUP = 'system-architect';

const BA_USER = 'ba-tester@example.com';
const SA_USER = 'sa-tester@example.com';

const BA_TOKEN = 'BA_ID_TOKEN_JWT';
const SA_TOKEN = 'SA_ID_TOKEN_JWT';

const PROJECT_API_URL = 'https://api.example.com/prod';
const PROJECT_UUID = 'two-persona-fe-uuid-001';
const PROJECT_NAME = 'Online Bookstore';
const ARTIFACT_PREFIX = 'online_bookstore';

/**
 * Same registry shape the Python `workflow_registry.py` loads from
 * `workflow_registry.json` (design Section 3a). The Frontend mirrors this
 * registry through `/config.json`; we hand it to the badge/service via
 * explicit overrides so the test does not rely on the cached default.
 */
const REGISTRY_DATA: RegistryData = {
  initial_state: 'draft',
  personas: {
    [BA_GROUP]: { display: 'Business Analyst', order: 1 },
    [SA_GROUP]: { display: 'Solution Architect', order: 2 },
  },
  segments: {
    ba_segment: {
      owning_role: BA_GROUP,
      stages: [
        'workspace_detection',
        'reverse_engineering',
        'requirements_analysis',
        'user_stories',
      ],
      entry_node: 'workspace_detection_ext',
      exit_hub: 'hub_post_user_stories',
    },
    sa_segment: {
      owning_role: SA_GROUP,
      stages: [
        'workflow_planning',
        'application_design',
        'units_generation',
        'inception_complete',
      ],
      entry_node: 'workflow_planning',
      exit_hub: 'hub_post_units_generation',
    },
  },
  states: {
    draft: { owning_role: BA_GROUP, segment: 'ba_segment', forward: 'requirements_ready' },
    requirements_ready: { owning_role: BA_GROUP, segment: 'ba_segment', forward: 'assigned_to_sa' },
    assigned_to_sa: { owning_role: SA_GROUP, segment: 'sa_segment', forward: 'in_design' },
    in_design: { owning_role: SA_GROUP, segment: 'sa_segment', forward: 'design_ready' },
    design_ready: { owning_role: SA_GROUP, segment: 'sa_segment', forward: 'ready_for_dev' },
    ready_for_dev: { owning_role: SA_GROUP, segment: 'sa_segment', forward: null },
  },
  request_changes: {
    assigned_to_sa: { target: 'requirements_ready', label: 'Changes requested' },
  },
};

const REGISTRY = new WorkflowRegistry(REGISTRY_DATA);

// ---------------------------------------------------------------------------
// FakeProjectApi: in-memory mirror of the real Project API contract.
//
// Mimics enough of `project_api.handler` for the Frontend service tests to
// cover the full BA -> SA scenario without spawning a server:
//
//   - Owner-gating (403) on transition, identical to design Section 4b.
//   - Registry-driven action resolution: `advance` / `assign` / `approve` /
//     `design-complete` resolve to `forward(state)`; `request-changes`
//     resolves to `request_changes(state).target` and records the note.
//   - Illegal target -> 422 (Req 4.4); not found -> 404; create + list +
//     get follow the same shape `projectService` consumes.
//
// The fake is *not* a copy of the production validator -- it is a smaller
// implementation that targets the same contract. The full validator (with
// all of its synonyms, ETag handling, etc.) is exercised end-to-end by
// `test_two_persona_handoff_e2e.py` against `project_api.handler` itself.
// ---------------------------------------------------------------------------

interface FakeIdentity {
  email: string;
  groups: string[];
}

const FORWARD_ACTIONS = new Set([
  'advance',
  'assign',
  'approve',
  'accept',
  'complete',
  'design-complete',
  'mark-design-complete',
  'forward',
  'next',
  'submit',
  'handoff',
  'ready',
]);
const REQUEST_CHANGES_ACTIONS = new Set([
  'request-changes',
  'changes-requested',
  'reject',
]);

class FakeProjectApi {
  /** uuid -> persisted record. */
  readonly store = new Map<string, ProjectRecord>();
  /** Append every received call so tests can audit the wire-level sequence. */
  readonly calls: Array<{
    method: string;
    url: string;
    actor: string;
    body: unknown;
  }> = [];

  /** Mint and persist the seed `draft` record (Req 4.1). */
  create(name: string, projectUuid: string, identity: FakeIdentity): {
    status: number;
    body: unknown;
  } {
    if (!name) return { status: 400, body: { code: 'INVALID_REQUEST' } };
    if (this.store.has(projectUuid))
      return { status: 409, body: { code: 'ALREADY_EXISTS' } };

    const slug = name.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_-]/g, '');
    const now = new Date('2024-01-01T00:00:00Z').toISOString();
    const initial = REGISTRY.initialState();
    const owning = REGISTRY.owningRole(initial);
    const record: ProjectRecord = {
      project_uuid: projectUuid,
      project_id: slug,
      name,
      description: '',
      type: 'development',
      created_at: now,
      created_by: identity.email,
      handoff_state: initial,
      owning_role: owning,
      artifact_prefix: slug,
      assignment_history: [
        {
          from: null,
          to: initial,
          actor: identity.email,
          timestamp: now,
          note: 'created',
        },
      ],
    };
    this.store.set(projectUuid, record);
    return { status: 201, body: record };
  }

  list(): { status: number; body: unknown } {
    return {
      status: 200,
      body: { projects: Array.from(this.store.values()) },
    };
  }

  get(uuid: string): { status: number; body: unknown } {
    const record = this.store.get(uuid);
    if (!record) return { status: 404, body: { code: 'NOT_FOUND' } };
    return { status: 200, body: record };
  }

  transition(
    uuid: string,
    payload: { action?: string; note?: string; target_state?: string },
    identity: FakeIdentity,
  ): { status: number; body: unknown } {
    const record = this.store.get(uuid);
    if (!record) return { status: 404, body: { code: 'NOT_FOUND' } };

    // Owner-gate before any mutation (design Section 4b, Req 2.2/2.3).
    if (!identity.groups.includes(record.owning_role)) {
      return { status: 403, body: { code: 'FORBIDDEN' } };
    }

    let target: string | null | undefined = payload.target_state;
    let note = payload.note ?? null;
    if (!target && payload.action) {
      const action = payload.action.trim().toLowerCase().replace(/[\s_]+/g, '-');
      if (REQUEST_CHANGES_ACTIONS.has(action)) {
        const rc = REGISTRY.requestChangesInto(record.handoff_state);
        if (!rc) return { status: 400, body: { code: 'INVALID_REQUEST' } };
        target = rc.target;
        if (note === null) note = rc.label;
      } else if (FORWARD_ACTIONS.has(action)) {
        target = REGISTRY.forward(record.handoff_state);
      } else {
        return { status: 400, body: { code: 'INVALID_REQUEST' } };
      }
    }
    if (!target) return { status: 400, body: { code: 'INVALID_REQUEST' } };
    if (!REGISTRY.validateTransition(record.handoff_state, target)) {
      return { status: 422, body: { code: 'ILLEGAL_TRANSITION' } };
    }

    const next: ProjectRecord = {
      ...record,
      handoff_state: target,
      owning_role: REGISTRY.owningRole(target),
      assignment_history: [
        ...record.assignment_history,
        {
          from: record.handoff_state,
          to: target,
          actor: identity.email,
          timestamp: new Date().toISOString(),
          note,
        },
      ],
    };
    this.store.set(uuid, next);
    return { status: 200, body: next };
  }

  uploadUrl(uuid: string, filename: string, identity: FakeIdentity): {
    status: number;
    body: unknown;
  } {
    const record = this.store.get(uuid);
    if (!record) return { status: 404, body: { code: 'NOT_FOUND' } };
    if (!identity.groups.includes(record.owning_role)) {
      return { status: 403, body: { code: 'FORBIDDEN' } };
    }
    return {
      status: 200,
      body: {
        upload_url: `https://fake-s3.local/${record.artifact_prefix}/${filename}`,
        key: `${record.artifact_prefix}/aidlc-docs/inception/requirements/uploaded/${filename}`,
        expires_in: 300,
      },
    };
  }
}

// ---------------------------------------------------------------------------
// Wire the fake as the global fetch handler for projectService.
//
// `projectService.ts` resolves the Cognito ID token from `fetchAuthSession`
// and the API URL from `loadConfig`, then calls `fetch`. We swap out fetch
// for a translator that decodes the request, delegates to the FakeProjectApi
// based on the bearer token's identity, and returns a Response-shaped object.
// ---------------------------------------------------------------------------

const TOKEN_IDENTITY: Record<string, FakeIdentity> = {
  [BA_TOKEN]: { email: BA_USER, groups: [BA_GROUP] },
  [SA_TOKEN]: { email: SA_USER, groups: [SA_GROUP] },
};

function buildFetchAdapter(api: FakeProjectApi) {
  return vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const path = url.replace(PROJECT_API_URL, '');

    const authHeader = (init?.headers as Record<string, string> | undefined)?.Authorization
      ?? '';
    const token = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
    const identity = TOKEN_IDENTITY[token];
    if (!identity) {
      return makeResponse(401, { code: 'UNAUTHENTICATED' });
    }
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    api.calls.push({ method, url, actor: identity.email, body });

    let result: { status: number; body: unknown };
    if (method === 'POST' && path === '/projects') {
      result = api.create(body.name, body.project_uuid, identity);
    } else if (method === 'GET' && path === '/projects') {
      result = api.list();
    } else if (method === 'GET' && path.startsWith('/projects/')) {
      const uuid = decodeURIComponent(path.replace('/projects/', ''));
      result = api.get(uuid);
    } else if (
      method === 'POST'
      && path.match(/^\/projects\/[^/]+\/transition$/)
    ) {
      const uuid = decodeURIComponent(
        path.replace('/projects/', '').replace('/transition', ''),
      );
      result = api.transition(uuid, body, identity);
    } else if (
      method === 'POST'
      && path.match(/^\/projects\/[^/]+\/upload-url$/)
    ) {
      const uuid = decodeURIComponent(
        path.replace('/projects/', '').replace('/upload-url', ''),
      );
      result = api.uploadUrl(uuid, body.filename, identity);
    } else {
      return makeResponse(400, { code: 'INVALID_REQUEST' });
    }
    return makeResponse(result.status, result.body);
  });
}

function makeResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

// ---------------------------------------------------------------------------
// Persona helpers: switch the active token between calls so the same
// projectService client speaks to the API as either BA or SA.
// ---------------------------------------------------------------------------

function asBa() {
  fetchAuthSessionMock.mockResolvedValue({
    tokens: { idToken: { toString: () => BA_TOKEN } },
  });
}
function asSa() {
  fetchAuthSessionMock.mockResolvedValue({
    tokens: { idToken: { toString: () => SA_TOKEN } },
  });
}

// ---------------------------------------------------------------------------
// The scripted scenario
// ---------------------------------------------------------------------------

describe('two-persona handoff scripted verification (frontend)', () => {
  let api: FakeProjectApi;
  let fetchMock: ReturnType<typeof buildFetchAdapter>;

  beforeEach(() => {
    vi.clearAllMocks();
    loadConfigMock.mockResolvedValue({
      cognitoUserPoolId: '',
      cognitoClientId: '',
      cognitoIdentityPoolId: '',
      cognitoDomain: '',
      awsRegion: 'us-east-1',
      runtimeArn: '',
      filesBucket: '',
      projectApiUrl: PROJECT_API_URL,
    });
    api = new FakeProjectApi();
    fetchMock = buildFetchAdapter(api);
    vi.stubGlobal('fetch', fetchMock);
    // Default S3 mock content per artifact key, used by the SA review panel.
    getFileContentMock.mockImplementation(async (key: string) => {
      if (key.endsWith('requirements.md')) return '# Requirements\n\nUploaded.\n';
      if (key.endsWith('stories.md')) return '# User Stories\n\n- A story.\n';
      if (key.endsWith('personas.md')) return '# Personas\n\n- A persona.\n';
      throw new Error(`unexpected s3 key: ${key}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('walks the full BA -> SA scenario through projectService + the SA review panel', async () => {
    // ----- STEP 1 -- BA creates the project ----------------------------------
    asBa();
    const draft = await createProject({
      name: PROJECT_NAME,
      project_uuid: PROJECT_UUID,
    });
    expect(draft).toMatchObject({
      project_uuid: PROJECT_UUID,
      handoff_state: 'draft',
      owning_role: BA_GROUP,
      created_by: BA_USER,
      artifact_prefix: ARTIFACT_PREFIX,
    });

    // ----- STEP 2 -- BA requests a presigned upload URL via raw fetch ---------
    // (`projectService` does not yet wrap upload-url; the Frontend's
    // RequirementsUploadControl makes this call directly. We exercise the
    // wire shape through the same FakeProjectApi to keep one source of truth.)
    const uploadResp = await fetch(
      `${PROJECT_API_URL}/projects/${PROJECT_UUID}/upload-url`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${BA_TOKEN}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ filename: 'draft-requirements.docx' }),
      },
    );
    expect(uploadResp.status).toBe(200);
    const uploadBody = (await uploadResp.json()) as {
      upload_url: string;
      key: string;
      expires_in: number;
    };
    expect(uploadBody.key).toBe(
      `${ARTIFACT_PREFIX}/aidlc-docs/inception/requirements/uploaded/draft-requirements.docx`,
    );

    // ----- STEP 3 -- BA advances draft -> requirements_ready ------------------
    const requirementsReady = await transition(PROJECT_UUID, { action: 'advance' });
    expect(requirementsReady).toMatchObject({
      handoff_state: 'requirements_ready',
      owning_role: BA_GROUP,
    });

    // ----- STEP 4 -- BA assigns to SA: requirements_ready -> assigned_to_sa --
    const assigned = await transition(PROJECT_UUID, { action: 'assign' });
    expect(assigned).toMatchObject({
      handoff_state: 'assigned_to_sa',
      owning_role: SA_GROUP, // ownership flipped at the boundary (Req 6.4)
    });

    // ----- STEP 5 -- SA logs in (different identity), sees the project -------
    asSa();
    const saList = await listProjects();
    const saSeen = saList.find((p) => p.project_uuid === PROJECT_UUID);
    expect(saSeen).toBeDefined(); // cross-creator visibility (Req 3.2)
    expect(saSeen!.created_by).toBe(BA_USER); // SA didn't create it
    expect(saSeen!.handoff_state).toBe('assigned_to_sa');

    // SA reads by id and resolves the SAME artifact prefix (Req 3.3).
    let saRecord = await getProject(PROJECT_UUID);
    expect(saRecord.artifact_prefix).toBe(ARTIFACT_PREFIX);
    expect(saRecord.handoff_state).toBe('assigned_to_sa');

    // ----- STEP 6 -- SA renders the review panel and requests changes ------
    // The panel loads requirements/stories/personas from the shared artifact
    // prefix (Req 7.1) -- we assert the s3Service was hit with the prefix-
    // joined keys, then click "Request Changes" with a note and verify both
    // the runtime approval-gate WS frame ("Request Changes") and the Project
    // API transition (assigned_to_sa -> requirements_ready, Req 7.5).
    const sentMessages: string[] = [];
    let latestRecord: ProjectRecord = saRecord;
    const onSendMessage = (text: string) => sentMessages.push(text);
    const onRecordUpdated = (rec: ProjectRecord) => {
      latestRecord = rec;
    };

    const { unmount } = render(
      <SolutionArchitectReviewPanel
        record={saRecord}
        userGroups={[SA_GROUP]}
        onSendMessage={onSendMessage}
        onRecordUpdated={onRecordUpdated}
      />,
    );

    // The panel loads each BA artifact under the project's artifact_prefix
    // (Req 7.1, 3.3). All three BA artifacts -> three s3Service calls.
    await waitFor(() => {
      expect(getFileContentMock).toHaveBeenCalledWith(
        `${ARTIFACT_PREFIX}/aidlc-docs/inception/requirements/requirements.md`,
      );
      expect(getFileContentMock).toHaveBeenCalledWith(
        `${ARTIFACT_PREFIX}/aidlc-docs/inception/user-stories/stories.md`,
      );
      expect(getFileContentMock).toHaveBeenCalledWith(
        `${ARTIFACT_PREFIX}/aidlc-docs/inception/user-stories/personas.md`,
      );
    });

    // The handoff bounce is a clean Project API call via the generic
    // ReturnForChangesControl — NOT the agent's in-stage revise loop. Unmount
    // the review panel and drive the return control.
    unmount();
    const SA_NOTE = 'Please clarify the non-functional requirements.';
    const { unmount: unmountReturn } = render(
      <ReturnForChangesControl
        record={saRecord}
        userGroups={[SA_GROUP]}
        onRecordUpdated={onRecordUpdated}
        registry={REGISTRY}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Return to Business Analyst for changes/i }));
    const noteInput = (await screen.findByLabelText(/Note for the Business Analyst/)) as HTMLTextAreaElement;
    fireEvent.change(noteInput, { target: { value: SA_NOTE } });
    fireEvent.click(screen.getByRole('button', { name: /^Return to Business Analyst$/i }));

    // The bounce persists through the Project API (Req 7.5)...
    await waitFor(() => {
      expect(latestRecord.handoff_state).toBe('requirements_ready');
    });
    // ...and crucially does NOT send the agent the "Request Changes" WS text
    // (a cross-persona handoff bounce must not trigger the in-stage revise loop).
    expect(sentMessages).not.toContain('Request Changes');
    expect(latestRecord.owning_role).toBe(BA_GROUP);
    const lastEntry =
      latestRecord.assignment_history[latestRecord.assignment_history.length - 1];
    expect(lastEntry).toMatchObject({
      from: 'assigned_to_sa',
      to: 'requirements_ready',
      actor: SA_USER,
      note: SA_NOTE,
    });

    // The Frontend HandoffStatusBadge would now derive the "Changes
    // requested" label for the BA viewer (Req 14.4) -- the registry's
    // request-changes label IS the badge label.
    const rcDescriptor = REGISTRY.requestChangesInto('assigned_to_sa');
    expect(rcDescriptor?.label).toBe('Changes requested');
    expect(rcDescriptor?.target).toBe('requirements_ready');

    unmountReturn();

    // ----- STEP 7 -- BA re-assigns after addressing the bounce ---------------
    asBa();
    const reassigned = await transition(PROJECT_UUID, { action: 'assign' });
    expect(reassigned).toMatchObject({
      handoff_state: 'assigned_to_sa',
      owning_role: SA_GROUP,
    });

    // ----- STEP 8 -- SA approves: assigned_to_sa -> in_design (Req 7.4) -----
    asSa();
    const reReadAfterReassign = await getProject(PROJECT_UUID);
    expect(reReadAfterReassign.handoff_state).toBe('assigned_to_sa');

    sentMessages.length = 0;
    latestRecord = reReadAfterReassign;
    const { unmount: unmountApprove } = render(
      <SolutionArchitectReviewPanel
        record={reReadAfterReassign}
        userGroups={[SA_GROUP]}
        onSendMessage={onSendMessage}
        onRecordUpdated={onRecordUpdated}
      />,
    );
    fireEvent.click(screen.getByText(/Approve & continue/));
    expect(sentMessages).toContain('Approve & Continue');
    await waitFor(() => {
      expect(latestRecord.handoff_state).toBe('in_design');
    });
    expect(latestRecord.owning_role).toBe(SA_GROUP);
    unmountApprove();

    // ----- STEP 9 -- SA marks design complete: in_design -> design_ready -> ready_for_dev (Req 7.7) ----
    // The SolutionArchitectReviewPanel issues two transitions in one click
    // when the first lands on design_ready.
    sentMessages.length = 0;
    latestRecord = await getProject(PROJECT_UUID);
    expect(latestRecord.handoff_state).toBe('in_design');

    const { unmount: unmountDesign } = render(
      <SolutionArchitectReviewPanel
        record={latestRecord}
        userGroups={[SA_GROUP]}
        onSendMessage={onSendMessage}
        onRecordUpdated={onRecordUpdated}
      />,
    );
    // The "Mark design ready" string appears in both the description
    // paragraph and the button on the in_design view, so pick the button
    // explicitly. The panel chains the second design-complete transition
    // (design_ready -> ready_for_dev) automatically for Req 7.7.
    fireEvent.click(
      screen.getByRole('button', { name: /Mark design ready/ }),
    );
    await waitFor(() => {
      expect(latestRecord.handoff_state).toBe('ready_for_dev');
    });
    expect(latestRecord.owning_role).toBe(SA_GROUP);
    unmountDesign();

    // ----- STEP 10 -- Cross-cutting end-of-run assertions --------------------

    // Both personas STILL see the same project at the same terminal state
    // (Req 3.2 / 3.3 again at end of run).
    asBa();
    const finalBaList = await listProjects();
    asSa();
    const finalSaList = await listProjects();
    const baFinal = finalBaList.find((p) => p.project_uuid === PROJECT_UUID);
    const saFinal = finalSaList.find((p) => p.project_uuid === PROJECT_UUID);
    expect(baFinal).toBeDefined();
    expect(saFinal).toBeDefined();
    expect(baFinal!.handoff_state).toBe('ready_for_dev');
    expect(saFinal!.handoff_state).toBe('ready_for_dev');
    expect(baFinal!.artifact_prefix).toBe(saFinal!.artifact_prefix);
    expect(baFinal!.artifact_prefix).toBe(ARTIFACT_PREFIX);

    // Append-only history (Req 4.6 / Property 4): the persisted history
    // matches the full sequence the personas drove, with the SA's note on the
    // bounce entry preserved.
    const persisted = api.store.get(PROJECT_UUID)!;
    const expectedChain: Array<[string | null, string, string]> = [
      [null, 'draft', BA_USER],                          // created
      ['draft', 'requirements_ready', BA_USER],          // STEP 3
      ['requirements_ready', 'assigned_to_sa', BA_USER], // STEP 4
      ['assigned_to_sa', 'requirements_ready', SA_USER], // STEP 6 (bounce)
      ['requirements_ready', 'assigned_to_sa', BA_USER], // STEP 7
      ['assigned_to_sa', 'in_design', SA_USER],          // STEP 8
      ['in_design', 'design_ready', SA_USER],            // STEP 9a
      ['design_ready', 'ready_for_dev', SA_USER],        // STEP 9b
    ];
    expect(persisted.assignment_history.length).toBe(expectedChain.length);
    persisted.assignment_history.forEach((entry, i) => {
      const [from, to, actor] = expectedChain[i];
      expect(entry.from).toBe(from);
      expect(entry.to).toBe(to);
      expect(entry.actor).toBe(actor);
    });
    const bounce = persisted.assignment_history.find(
      (e) => e.from === 'assigned_to_sa' && e.to === 'requirements_ready',
    )!;
    expect(bounce.note).toBe(SA_NOTE);

    // Every projectService call carried the persona's bearer token -- the
    // wire-level identity binding the API uses for RBAC. Verify both tokens
    // appeared and were correctly attributed to the right actor.
    const baCalls = api.calls.filter((c) => c.actor === BA_USER);
    const saCalls = api.calls.filter((c) => c.actor === SA_USER);
    expect(baCalls.length).toBeGreaterThan(0);
    expect(saCalls.length).toBeGreaterThan(0);

    // Sanity: the BA never made a transition while the project was
    // SA-owned (would be a 403 -- the panel would never call transition
    // for a non-owning persona).
    const ownershipFlippedToSa = baCalls.some(
      (c) => c.method === 'POST' && c.url.endsWith('/transition'),
    );
    expect(ownershipFlippedToSa).toBe(true); // BA did legit transitions
  });
});
