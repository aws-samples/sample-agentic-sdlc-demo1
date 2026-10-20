/**
 * Unit tests for `projectService.ts` (design Section 2d, Req 2.1, 3.4).
 *
 * Covers:
 *   - HTTP plumbing: every call attaches the Cognito ID token as
 *     `Authorization: Bearer ...` and routes to the right path under
 *     `projectApiUrl`.
 *   - `permittedActions`: registry-derived ownership gating (forward step,
 *     request-changes bounce, no-op for non-owners).
 *   - Error mapping: non-2xx responses surface as `ProjectApiError` carrying
 *     the API's structured `error`/`code` body.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Hoisted mocks: vitest hoists `vi.mock` above imports, so the symbols they
// reference must be set up via `vi.hoisted`.
const { fetchAuthSessionMock, loadConfigMock } = vi.hoisted(() => ({
  fetchAuthSessionMock: vi.fn(),
  loadConfigMock: vi.fn(),
}));

vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: fetchAuthSessionMock,
}));

vi.mock('../../app/lib/config', () => ({
  loadConfig: loadConfigMock,
  getConfig: vi.fn(),
}));

import {
  listProjects,
  createProject,
  getProject,
  transition,
  deleteProject,
  linkRepo,
  runTests,
  permittedActions,
  ProjectApiError,
  type ProjectRecord,
} from '../../app/services/projectService';
import { WorkflowRegistry, type RegistryData } from '../../app/lib/workflowRegistry';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const PROJECT_API_URL = 'https://api.example.com/prod';
const ID_TOKEN = 'id-token-jwt';

const SAMPLE_RECORD: ProjectRecord = {
  project_uuid: 'uuid-1',
  project_id: 'sample',
  name: 'Sample',
  description: '',
  type: 'development',
  created_at: '2024-01-01T00:00:00Z',
  created_by: 'ba@example.com',
  handoff_state: 'draft',
  owning_role: 'requirements-analyst',
  artifact_prefix: 'sample',
  assignment_history: [],
};

const BA_SA_REGISTRY: RegistryData = {
  initial_state: 'draft',
  personas: {
    'requirements-analyst': { display: 'Business Analyst', order: 1 },
    'system-architect': { display: 'Solution Architect', order: 2 },
  },
  segments: {
    ba_segment: {
      owning_role: 'requirements-analyst',
      stages: ['workspace_detection', 'requirements_analysis', 'user_stories'],
      entry_node: 'workspace_detection_ext',
      exit_hub: 'hub_post_user_stories',
    },
    sa_segment: {
      owning_role: 'system-architect',
      stages: ['workflow_planning', 'application_design', 'inception_complete'],
      entry_node: 'workflow_planning',
      exit_hub: 'hub_post_units_generation',
    },
  },
  states: {
    draft: {
      owning_role: 'requirements-analyst',
      segment: 'ba_segment',
      forward: 'requirements_ready',
    },
    requirements_ready: {
      owning_role: 'requirements-analyst',
      segment: 'ba_segment',
      forward: 'assigned_to_sa',
    },
    assigned_to_sa: {
      owning_role: 'system-architect',
      segment: 'sa_segment',
      forward: 'in_design',
    },
    in_design: {
      owning_role: 'system-architect',
      segment: 'sa_segment',
      forward: 'design_ready',
    },
    design_ready: {
      owning_role: 'system-architect',
      segment: 'sa_segment',
      forward: 'ready_for_dev',
    },
    ready_for_dev: {
      owning_role: 'system-architect',
      segment: 'sa_segment',
      forward: null,
    },
  },
  request_changes: {
    assigned_to_sa: { target: 'requirements_ready', label: 'Changes requested' },
  },
};

const baSaRegistry = new WorkflowRegistry(BA_SA_REGISTRY);

// ---------------------------------------------------------------------------
// HTTP plumbing
// ---------------------------------------------------------------------------

describe('projectService — HTTP plumbing', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

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
    fetchAuthSessionMock.mockResolvedValue({
      tokens: { idToken: { toString: () => ID_TOKEN } },
    });
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('listProjects GETs /projects with the ID token and returns the records', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ projects: [SAMPLE_RECORD] }),
    });

    const records = await listProjects();
    expect(records).toEqual([SAMPLE_RECORD]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${PROJECT_API_URL}/projects`);
    expect(init.method).toBe('GET');
    expect(init.headers).toMatchObject({
      Accept: 'application/json',
      Authorization: `Bearer ${ID_TOKEN}`,
    });
    // No body on GET.
    expect(init.body).toBeUndefined();
  });

  it('listProjects normalizes a missing `projects` field to an empty list', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    await expect(listProjects()).resolves.toEqual([]);
  });

  it('createProject POSTs /projects with the input body and ID token', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => SAMPLE_RECORD,
    });

    const out = await createProject({
      name: 'Sample',
      description: 'desc',
      type: 'research',
      project_uuid: 'uuid-1',
    });
    expect(out).toEqual(SAMPLE_RECORD);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${PROJECT_API_URL}/projects`);
    expect(init.method).toBe('POST');
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      Authorization: `Bearer ${ID_TOKEN}`,
    });
    expect(JSON.parse(init.body)).toEqual({
      name: 'Sample',
      description: 'desc',
      type: 'research',
      project_uuid: 'uuid-1',
    });
  });

  it('createProject defaults description and type when omitted', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => SAMPLE_RECORD,
    });

    await createProject({ name: 'Sample' });
    const init = fetchMock.mock.calls[0][1];
    const body = JSON.parse(init.body);
    expect(body).toEqual({
      name: 'Sample',
      description: '',
      type: 'development',
    });
    // No project_uuid field when not supplied.
    expect(body).not.toHaveProperty('project_uuid');
  });

  it('linkRepo POSTs /projects/{uuid}/link-repo with repo_url + branch and the ID token', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => SAMPLE_RECORD,
    });

    await linkRepo('uuid-1', { repo_url: 'https://github.com/acme/widget', branch: 'release' });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${PROJECT_API_URL}/projects/uuid-1/link-repo`);
    expect(init.method).toBe('POST');
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      Authorization: `Bearer ${ID_TOKEN}`,
    });
    expect(JSON.parse(init.body)).toEqual({
      repo_url: 'https://github.com/acme/widget',
      branch: 'release',
    });
  });

  it('linkRepo omits the branch when not supplied', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => SAMPLE_RECORD,
    });

    await linkRepo('uuid-1', { repo_url: 'https://gitlab.com/acme/widget' });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ repo_url: 'https://gitlab.com/acme/widget' });
    expect(body).not.toHaveProperty('branch');
  });

  it('runTests POSTs /projects/{uuid}/run-tests with the ID token and no body', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 202,
      json: async () => SAMPLE_RECORD,
    });

    await runTests('uuid-1');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${PROJECT_API_URL}/projects/uuid-1/run-tests`);
    expect(init.method).toBe('POST');
    expect(init.headers).toMatchObject({ Authorization: `Bearer ${ID_TOKEN}` });
    expect(init.body).toBeUndefined();
  });

  it('getProject GETs /projects/{uuid} with the ID token', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => SAMPLE_RECORD,
    });

    const out = await getProject('uuid-1');
    expect(out).toEqual(SAMPLE_RECORD);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${PROJECT_API_URL}/projects/uuid-1`);
    expect(init.method).toBe('GET');
    expect(init.headers).toMatchObject({ Authorization: `Bearer ${ID_TOKEN}` });
  });

  it('getProject percent-encodes the UUID path component', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => SAMPLE_RECORD,
    });
    await getProject('uuid with space');
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe(`${PROJECT_API_URL}/projects/uuid%20with%20space`);
  });

  it('transition POSTs the action body to /projects/{uuid}/transition', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ ...SAMPLE_RECORD, handoff_state: 'requirements_ready' }),
    });

    const out = await transition('uuid-1', { action: 'advance' });
    expect(out.handoff_state).toBe('requirements_ready');

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${PROJECT_API_URL}/projects/uuid-1/transition`);
    expect(init.method).toBe('POST');
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      Authorization: `Bearer ${ID_TOKEN}`,
    });
    expect(JSON.parse(init.body)).toEqual({ action: 'advance' });
  });

  it('deleteProject DELETEs /projects/{uuid} with the ID token', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ deleted: true }),
    });

    await deleteProject('uuid-1');

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${PROJECT_API_URL}/projects/uuid-1`);
    expect(init.method).toBe('DELETE');
    expect(init.headers).toMatchObject({ Authorization: `Bearer ${ID_TOKEN}` });
    expect(init.body).toBeUndefined();
  });

  it('strips a trailing slash from the configured projectApiUrl', async () => {
    loadConfigMock.mockResolvedValue({
      cognitoUserPoolId: '',
      cognitoClientId: '',
      cognitoIdentityPoolId: '',
      cognitoDomain: '',
      awsRegion: 'us-east-1',
      runtimeArn: '',
      filesBucket: '',
      projectApiUrl: `${PROJECT_API_URL}/`,
    });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ projects: [] }),
    });

    await listProjects();
    expect(fetchMock.mock.calls[0][0]).toBe(`${PROJECT_API_URL}/projects`);
  });

  it('throws ProjectApiError carrying the server status and code on a non-2xx response', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: async () => ({ error: 'Illegal transition: draft -> in_design', code: 'ILLEGAL_TRANSITION' }),
    });

    await expect(
      transition('uuid-1', { action: 'advance' }),
    ).rejects.toMatchObject({
      name: 'ProjectApiError',
      status: 422,
      code: 'ILLEGAL_TRANSITION',
      message: 'Illegal transition: draft -> in_design',
    });
  });

  it('falls back to a generic message when the error body is not JSON', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error('not json');
      },
    });

    let caught: unknown;
    try {
      await listProjects();
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(ProjectApiError);
    expect((caught as ProjectApiError).status).toBe(500);
    expect((caught as ProjectApiError).message).toContain('500');
  });

  it('throws a CONFIG_MISSING ProjectApiError when projectApiUrl is not set', async () => {
    loadConfigMock.mockResolvedValue({
      cognitoUserPoolId: '',
      cognitoClientId: '',
      cognitoIdentityPoolId: '',
      cognitoDomain: '',
      awsRegion: 'us-east-1',
      runtimeArn: '',
      filesBucket: '',
    });

    await expect(listProjects()).rejects.toMatchObject({
      name: 'ProjectApiError',
      code: 'CONFIG_MISSING',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('throws an UNAUTHENTICATED ProjectApiError when no ID token is present', async () => {
    fetchAuthSessionMock.mockResolvedValue({ tokens: undefined });

    await expect(listProjects()).rejects.toMatchObject({
      name: 'ProjectApiError',
      status: 401,
      code: 'UNAUTHENTICATED',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// permittedActions — registry-derived presentational gate (Req 2.1)
// ---------------------------------------------------------------------------

describe('permittedActions', () => {
  it('returns no actions when the user is not in the owning role', () => {
    const record = { handoff_state: 'draft', owning_role: 'requirements-analyst' };
    expect(permittedActions(record, ['system-architect'], baSaRegistry)).toEqual([]);
  });

  it('returns no actions when the user has no groups at all', () => {
    const record = { handoff_state: 'draft', owning_role: 'requirements-analyst' };
    expect(permittedActions(record, [], baSaRegistry)).toEqual([]);
  });

  it('exposes a single forward action for an owning role on a forward-only state', () => {
    const record = { handoff_state: 'draft', owning_role: 'requirements-analyst' };
    const actions = permittedActions(record, ['requirements-analyst'], baSaRegistry);
    expect(actions).toHaveLength(1);
    expect(actions[0]).toMatchObject({
      kind: 'forward',
      target: 'requirements_ready',
    });
  });

  it("uses 'assign' as the forward action and a labelled button on requirements_ready", () => {
    const record = {
      handoff_state: 'requirements_ready',
      owning_role: 'requirements-analyst',
    };
    const actions = permittedActions(record, ['requirements-analyst'], baSaRegistry);
    expect(actions).toHaveLength(1);
    expect(actions[0]).toMatchObject({
      action: 'assign',
      kind: 'forward',
      target: 'assigned_to_sa',
      label: 'Assign to Solution Architect',
    });
  });

  it('exposes BOTH a forward (approve) and a request-changes action on assigned_to_sa', () => {
    const record = {
      handoff_state: 'assigned_to_sa',
      owning_role: 'system-architect',
    };
    const actions = permittedActions(record, ['system-architect'], baSaRegistry);

    const byKind = Object.fromEntries(actions.map((a) => [a.kind, a]));
    expect(byKind.forward).toMatchObject({
      action: 'approve',
      target: 'in_design',
      label: 'Approve',
      acceptsNote: false,
    });
    expect(byKind['request-changes']).toMatchObject({
      action: 'request-changes',
      target: 'requirements_ready',
      label: 'Changes requested',
      acceptsNote: true,
    });
  });

  it('exposes only the design-complete forward on in_design and design_ready', () => {
    const inDesign = permittedActions(
      { handoff_state: 'in_design', owning_role: 'system-architect' },
      ['system-architect'],
      baSaRegistry,
    );
    expect(inDesign).toHaveLength(1);
    expect(inDesign[0]).toMatchObject({
      action: 'design-complete',
      target: 'design_ready',
    });

    const designReady = permittedActions(
      { handoff_state: 'design_ready', owning_role: 'system-architect' },
      ['system-architect'],
      baSaRegistry,
    );
    expect(designReady).toHaveLength(1);
    expect(designReady[0]).toMatchObject({
      action: 'design-complete',
      target: 'ready_for_dev',
    });
  });

  it('returns no actions on a terminal state with no forward and no request-changes', () => {
    const record = { handoff_state: 'ready_for_dev', owning_role: 'system-architect' };
    expect(permittedActions(record, ['system-architect'], baSaRegistry)).toEqual([]);
  });

  it("permittedActions matches registry-derived ownership for every state", () => {
    // For every state in the registry, the action set is non-empty IFF the
    // user is in that state's owning role (Req 2.1 — frontend gating mirrors
    // the registry's `owning_role(state)` at all times).
    for (const stateName of Object.keys(BA_SA_REGISTRY.states)) {
      const owningRole = BA_SA_REGISTRY.states[stateName].owning_role;
      const record = { handoff_state: stateName, owning_role: owningRole };

      // Owner sees actions iff the state has a forward edge or a request-changes edge.
      const hasForward = BA_SA_REGISTRY.states[stateName].forward !== null;
      const hasRequestChanges = stateName in BA_SA_REGISTRY.request_changes;
      const ownerActions = permittedActions(record, [owningRole], baSaRegistry);
      expect(ownerActions.length).toBe(
        Number(hasForward) + Number(hasRequestChanges),
      );

      // A non-owner is never offered an action, regardless of their groups.
      for (const otherRole of Object.keys(BA_SA_REGISTRY.personas)) {
        if (otherRole === owningRole) continue;
        const nonOwnerActions = permittedActions(record, [otherRole], baSaRegistry);
        expect(nonOwnerActions).toEqual([]);
      }
    }
  });
});
