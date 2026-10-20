/**
 * Unit tests for RepoLinkControl — the Developer's C7 Git round-trip action:
 * link the pushed repo, then submit (advance to implementation_submitted).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

const { linkRepoMock, transitionMock } = vi.hoisted(() => ({
  linkRepoMock: vi.fn(),
  transitionMock: vi.fn(),
}));

vi.mock('../../app/services/projectService', async () => {
  const actual =
    await vi.importActual<typeof import('../../app/services/projectService')>(
      '../../app/services/projectService',
    );
  return { ...actual, linkRepo: linkRepoMock, transition: transitionMock };
});

import { RepoLinkControl } from '../../app/components/RepoLinkControl';
import { WorkflowRegistry, type RegistryData } from '../../app/lib/workflowRegistry';
import type { ProjectRecord } from '../../app/services/projectService';

const DEV = 'software-developer';
const QA = 'qa-engineer';

const REGISTRY_DATA: RegistryData = {
  initial_state: 'draft',
  personas: {
    [DEV]: { display: 'Software Developer', order: 3 },
    [QA]: { display: 'QA Engineer', order: 4 },
  },
  segments: {
    dev_segment: { owning_role: DEV, stages: ['construction_handoff'], entry_node: 'construction_handoff', exit_hub: 'h1' },
    qa_segment: { owning_role: QA, stages: ['test_planning'], entry_node: 'test_planning', exit_hub: 'h2' },
  },
  states: {
    assigned_to_dev: { owning_role: DEV, segment: 'dev_segment', forward: 'implementation_submitted' },
    implementation_submitted: { owning_role: DEV, segment: 'dev_segment', forward: 'assigned_to_qa' },
    assigned_to_qa: { owning_role: QA, segment: 'qa_segment', forward: null },
  },
  request_changes: {},
};
const registry = new WorkflowRegistry(REGISTRY_DATA);

function makeRecord(overrides: Partial<ProjectRecord> = {}): ProjectRecord {
  return {
    project_uuid: 'uuid-dev',
    project_id: 'sample',
    name: 'Sample',
    description: '',
    type: 'development',
    created_at: '2026-01-01T00:00:00Z',
    created_by: 'x@example.com',
    handoff_state: 'assigned_to_dev',
    owning_role: DEV,
    artifact_prefix: 'sample',
    assignment_history: [],
    ...overrides,
  } as ProjectRecord;
}

beforeEach(() => {
  linkRepoMock.mockReset();
  transitionMock.mockReset();
});
afterEach(cleanup);

describe('RepoLinkControl', () => {
  it('shows the developer a submit control at assigned_to_dev', () => {
    render(
      <RepoLinkControl
        record={makeRecord()}
        userGroups={[DEV]}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(screen.getByTestId('repo-link-control')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Link repo & submit for QA/i })).toBeTruthy();
  });

  it('renders nothing for a non-owner', () => {
    const { container } = render(
      <RepoLinkControl
        record={makeRecord()}
        userGroups={[QA]}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when the forward successor is not the submit state', () => {
    // At implementation_submitted the forward is assigned_to_qa (not the submit
    // state), so the control should not appear.
    const { container } = render(
      <RepoLinkControl
        record={makeRecord({ handoff_state: 'implementation_submitted' })}
        userGroups={[DEV]}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('rejects an unsupported URL client-side without calling the API', async () => {
    render(
      <RepoLinkControl
        record={makeRecord()}
        userGroups={[DEV]}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    fireEvent.change(screen.getByLabelText(/Repository URL/i), {
      target: { value: 'https://evil.example.com/x/y' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Link repo & submit for QA/i }));

    await waitFor(() => {
      expect(screen.getByText(/GitHub or GitLab repository/i)).toBeTruthy();
    });
    expect(linkRepoMock).not.toHaveBeenCalled();
    expect(transitionMock).not.toHaveBeenCalled();
  });

  it('links the repo then submits, and reports the updated record', async () => {
    const onRecordUpdated = vi.fn();
    linkRepoMock.mockResolvedValueOnce(makeRecord({ repo_ingest_status: 'pending' }));
    transitionMock.mockResolvedValueOnce(
      makeRecord({ handoff_state: 'implementation_submitted' }),
    );

    render(
      <RepoLinkControl
        record={makeRecord()}
        userGroups={[DEV]}
        onRecordUpdated={onRecordUpdated}
        registry={registry}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Repository URL/i), {
      target: { value: 'https://github.com/acme/widget' },
    });
    fireEvent.change(screen.getByLabelText(/Branch/i), { target: { value: 'release' } });
    fireEvent.click(screen.getByRole('button', { name: /Link repo & submit for QA/i }));

    await waitFor(() => {
      expect(linkRepoMock).toHaveBeenCalledWith('uuid-dev', {
        repo_url: 'https://github.com/acme/widget',
        branch: 'release',
      });
    });
    await waitFor(() => {
      expect(transitionMock).toHaveBeenCalledWith('uuid-dev', { action: 'submit' });
    });
    expect(onRecordUpdated).toHaveBeenCalled();
  });
});
