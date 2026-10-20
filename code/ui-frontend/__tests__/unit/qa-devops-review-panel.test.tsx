/**
 * Unit tests for QADevOpsReviewPanel — the QA/DevOps read + intra-segment
 * forward surface. Cross-segment boundaries are handled by the handoff card,
 * so the forward button is suppressed at a segment's terminal state.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

const { transitionMock, getFileContentMock } = vi.hoisted(() => ({
  transitionMock: vi.fn(),
  getFileContentMock: vi.fn(),
}));

const { runTestsMock } = vi.hoisted(() => ({ runTestsMock: vi.fn() }));

vi.mock('../../app/services/projectService', async () => {
  const actual =
    await vi.importActual<typeof import('../../app/services/projectService')>(
      '../../app/services/projectService',
    );
  return { ...actual, transition: transitionMock, runTests: runTestsMock };
});

vi.mock('../../app/services/s3Service', () => ({
  getFileContent: getFileContentMock,
}));

import { QADevOpsReviewPanel } from '../../app/components/QADevOpsReviewPanel';
import { WorkflowRegistry, type RegistryData } from '../../app/lib/workflowRegistry';
import type { ProjectRecord } from '../../app/services/projectService';

const QA = 'qa-engineer';
const DEVOPS = 'devops-engineer';

const REGISTRY_DATA: RegistryData = {
  initial_state: 'assigned_to_qa',
  personas: {
    [QA]: { display: 'QA Engineer', order: 4 },
    [DEVOPS]: { display: 'DevOps Engineer', order: 5 },
  },
  segments: {
    qa_segment: { owning_role: QA, stages: ['test_planning'], entry_node: 'test_planning', exit_hub: 'h1' },
    devops_segment: { owning_role: DEVOPS, stages: ['deployment_planning'], entry_node: 'deployment_planning', exit_hub: 'h2' },
  },
  states: {
    assigned_to_qa: { owning_role: QA, segment: 'qa_segment', forward: 'in_qa' },
    in_qa: { owning_role: QA, segment: 'qa_segment', forward: 'qa_passed' },
    qa_passed: { owning_role: QA, segment: 'qa_segment', forward: 'assigned_to_devops' },
    assigned_to_devops: { owning_role: DEVOPS, segment: 'devops_segment', forward: 'in_deployment' },
    in_deployment: { owning_role: DEVOPS, segment: 'devops_segment', forward: 'deployed' },
    deployed: { owning_role: DEVOPS, segment: 'devops_segment', forward: null },
  },
  request_changes: {},
};
const registry = new WorkflowRegistry(REGISTRY_DATA);

function makeRecord(over: Partial<ProjectRecord> = {}): ProjectRecord {
  return {
    project_uuid: 'uuid-qa',
    project_id: 'sample',
    name: 'Sample',
    description: '',
    type: 'development',
    created_at: '2026-01-01T00:00:00Z',
    created_by: 'x@example.com',
    handoff_state: 'assigned_to_qa',
    owning_role: QA,
    artifact_prefix: 'sample',
    assignment_history: [],
    ...over,
  } as ProjectRecord;
}

beforeEach(() => {
  transitionMock.mockReset();
  getFileContentMock.mockReset();
  getFileContentMock.mockResolvedValue('# artifact');
  runTestsMock.mockReset();
});
afterEach(cleanup);

describe('QADevOpsReviewPanel', () => {
  it('shows the QA persona a review panel with an intra-segment forward button', async () => {
    transitionMock.mockResolvedValueOnce(makeRecord({ handoff_state: 'in_qa' }));
    render(
      <QADevOpsReviewPanel
        record={makeRecord()}
        userGroups={[QA]}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(screen.getByTestId('qa-devops-review-panel')).toBeTruthy();
    const btn = await screen.findByRole('button', { name: /Start QA/i });
    fireEvent.click(btn);
    await waitFor(() => {
      // assigned_to_qa is not in the curated forward-action map -> generic 'advance'.
      expect(transitionMock).toHaveBeenCalledWith('uuid-qa', { action: 'advance' });
    });
  });

  it('shows the DevOps persona a deployment review with a Start deployment button', async () => {
    render(
      <QADevOpsReviewPanel
        record={makeRecord({ handoff_state: 'assigned_to_devops', owning_role: DEVOPS })}
        userGroups={[DEVOPS]}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(screen.getByText('Deployment review')).toBeTruthy();
    expect(await screen.findByRole('button', { name: /Start deployment/i })).toBeTruthy();
  });

  it('renders nothing for a non-owner', () => {
    const { container } = render(
      <QADevOpsReviewPanel
        record={makeRecord()}
        userGroups={['software-developer']}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('suppresses the forward button at a cross-segment boundary (qa_passed → devops)', () => {
    render(
      <QADevOpsReviewPanel
        record={makeRecord({ handoff_state: 'qa_passed' })}
        userGroups={[QA]}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    // Panel still shows (QA owner), but no forward button — the qa→devops
    // boundary is handled by the handoff card.
    expect(screen.getByTestId('qa-devops-review-panel')).toBeTruthy();
    expect(
      screen.queryByRole('button', { name: /Start|Mark|Continue|Assign|Working/i }),
    ).toBeNull();
  });

  it('has no forward button at the terminal deployed state', () => {
    render(
      <QADevOpsReviewPanel
        record={makeRecord({ handoff_state: 'deployed', owning_role: DEVOPS })}
        userGroups={[DEVOPS]}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(screen.getByTestId('qa-devops-review-panel')).toBeTruthy();
    expect(
      screen.queryByRole('button', { name: /Start|Mark|Continue|Assign|Working/i }),
    ).toBeNull();
  });

  it('lets the QA persona run tests via the Project API', async () => {
    runTestsMock.mockResolvedValueOnce(makeRecord({ qa_test_status: 'running' }));
    render(
      <QADevOpsReviewPanel
        record={makeRecord()}
        userGroups={[QA]}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^Run tests$/i }));
    await waitFor(() => {
      expect(runTestsMock).toHaveBeenCalledWith('uuid-qa');
    });
  });

  it('shows a friendly "not generated yet" message when an artifact key is missing', async () => {
    const err = new Error('The specified key does not exist.');
    err.name = 'NoSuchKey';
    getFileContentMock.mockRejectedValue(err);
    render(
      <QADevOpsReviewPanel
        record={makeRecord({ handoff_state: 'assigned_to_devops', owning_role: DEVOPS })}
        userGroups={[DEVOPS]}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText(/hasn.t been generated yet/i)).toBeTruthy();
    });
    // The raw S3 error must not leak into the UI.
    expect(screen.queryByText(/specified key does not exist/i)).toBeNull();
  });
});
