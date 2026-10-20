/**
 * Unit tests for ReturnForChangesControl — the generic, registry-driven
 * persona handoff bounce (clean Project API call, no agent revise-loop WS
 * text), covering both SA→BA and Dev→SA directions.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

const { transitionMock } = vi.hoisted(() => ({ transitionMock: vi.fn() }));

vi.mock('../../app/services/projectService', async () => {
  const actual =
    await vi.importActual<typeof import('../../app/services/projectService')>(
      '../../app/services/projectService',
    );
  return { ...actual, transition: transitionMock };
});

import { ReturnForChangesControl } from '../../app/components/ReturnForChangesControl';
import { WorkflowRegistry, type RegistryData } from '../../app/lib/workflowRegistry';
import type { ProjectRecord } from '../../app/services/projectService';

const REGISTRY_DATA: RegistryData = {
  initial_state: 'draft',
  personas: {
    'requirements-analyst': { display: 'Business Analyst', order: 1 },
    'system-architect': { display: 'Solution Architect', order: 2 },
    'software-developer': { display: 'Software Developer', order: 3 },
  },
  segments: {
    ba_segment: { owning_role: 'requirements-analyst', stages: ['requirements_analysis'], entry_node: 'requirements_analysis', exit_hub: 'h1' },
    sa_segment: { owning_role: 'system-architect', stages: ['application_design'], entry_node: 'application_design', exit_hub: 'h2' },
    dev_segment: { owning_role: 'software-developer', stages: ['construction_handoff'], entry_node: 'construction_handoff', exit_hub: 'h3' },
  },
  states: {
    draft: { owning_role: 'requirements-analyst', segment: 'ba_segment', forward: 'requirements_ready' },
    requirements_ready: { owning_role: 'requirements-analyst', segment: 'ba_segment', forward: 'assigned_to_sa' },
    assigned_to_sa: { owning_role: 'system-architect', segment: 'sa_segment', forward: 'ready_for_dev' },
    ready_for_dev: { owning_role: 'system-architect', segment: 'sa_segment', forward: 'assigned_to_dev' },
    assigned_to_dev: { owning_role: 'software-developer', segment: 'dev_segment', forward: 'development_complete' },
    development_complete: { owning_role: 'software-developer', segment: 'dev_segment', forward: null },
  },
  request_changes: {
    assigned_to_sa: { target: 'requirements_ready', label: 'Requirements changes requested' },
    assigned_to_dev: { target: 'design_ready', label: 'Design changes requested' },
  },
};
// design_ready exists only as a request_changes target; add it so owningRole resolves.
(REGISTRY_DATA.states as Record<string, unknown>)['design_ready'] = {
  owning_role: 'system-architect',
  segment: 'sa_segment',
  forward: 'ready_for_dev',
};

const registry = new WorkflowRegistry(REGISTRY_DATA);

function makeRecord(overrides: Partial<ProjectRecord> = {}): ProjectRecord {
  return {
    project_uuid: 'uuid-001',
    project_id: 'sample',
    name: 'Sample',
    description: '',
    type: 'development',
    created_at: '2026-01-01T00:00:00Z',
    created_by: 'x@example.com',
    handoff_state: 'assigned_to_sa',
    owning_role: 'system-architect',
    artifact_prefix: 'sample',
    assignment_history: [],
    ...overrides,
  } as ProjectRecord;
}

beforeEach(() => transitionMock.mockReset());
afterEach(cleanup);

describe('ReturnForChangesControl', () => {
  it('shows the SA a "Return to Business Analyst" action at assigned_to_sa', () => {
    render(
      <ReturnForChangesControl
        record={makeRecord()}
        userGroups={['system-architect']}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(screen.getByRole('button', { name: /Return to Business Analyst for changes/i })).toBeTruthy();
  });

  it('shows the Developer a "Return to Solution Architect" action at assigned_to_dev', () => {
    render(
      <ReturnForChangesControl
        record={makeRecord({ handoff_state: 'assigned_to_dev', owning_role: 'software-developer' })}
        userGroups={['software-developer']}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(screen.getByRole('button', { name: /Return to Solution Architect for changes/i })).toBeTruthy();
  });

  it('does a clean Project API bounce with the note and never sends agent WS text', async () => {
    const onRecordUpdated = vi.fn();
    transitionMock.mockResolvedValueOnce(
      makeRecord({ handoff_state: 'requirements_ready', owning_role: 'requirements-analyst' }),
    );
    render(
      <ReturnForChangesControl
        record={makeRecord()}
        userGroups={['system-architect']}
        onRecordUpdated={onRecordUpdated}
        registry={registry}
      />,
    );
    // Expand the note, type, submit.
    fireEvent.click(screen.getByRole('button', { name: /Return to Business Analyst for changes/i }));
    fireEvent.change(screen.getByLabelText(/Note for the Business Analyst/i), {
      target: { value: 'Tighten the UI requirements.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Return to Business Analyst$/i }));

    await waitFor(() => {
      expect(transitionMock).toHaveBeenCalledWith('uuid-001', {
        action: 'request-changes',
        note: 'Tighten the UI requirements.',
      });
    });
    expect(onRecordUpdated).toHaveBeenCalled();
    // The control has no WebSocket channel by design — it cannot trigger the
    // agent's in-stage revise loop. (No onSendMessage prop exists.)
  });

  it('omits the note field when left blank', async () => {
    transitionMock.mockResolvedValueOnce(makeRecord({ handoff_state: 'requirements_ready' }));
    render(
      <ReturnForChangesControl
        record={makeRecord()}
        userGroups={['system-architect']}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Return to Business Analyst for changes/i }));
    fireEvent.click(screen.getByRole('button', { name: /^Return to Business Analyst$/i }));
    await waitFor(() => {
      expect(transitionMock).toHaveBeenCalledWith('uuid-001', { action: 'request-changes' });
    });
  });

  it('renders nothing for a non-owner', () => {
    const { container } = render(
      <ReturnForChangesControl
        record={makeRecord()}
        userGroups={['requirements-analyst']}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing on a state without a request_changes edge', () => {
    const { container } = render(
      <ReturnForChangesControl
        record={makeRecord({ handoff_state: 'requirements_ready', owning_role: 'requirements-analyst' })}
        userGroups={['requirements-analyst']}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('hides cleanly when the same mount re-renders into a no-edge state (no hooks-order error)', () => {
    const { rerender } = render(
      <ReturnForChangesControl
        record={makeRecord()}
        userGroups={['system-architect']}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(screen.queryByTestId('return-for-changes')).toBeTruthy();
    // After a bounce the record updates to requirements_ready (no request_changes
    // edge). Re-rendering the SAME instance must not throw a Rules-of-Hooks error.
    rerender(
      <ReturnForChangesControl
        record={makeRecord({ handoff_state: 'requirements_ready', owning_role: 'requirements-analyst' })}
        userGroups={['system-architect']}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(screen.queryByTestId('return-for-changes')).toBeNull();
  });

  it('shows again at a re-assigned assigned_to_sa with prior bounce history (multi-bounce allowed)', () => {
    const record = makeRecord({
      assignment_history: [
        { from: 'assigned_to_sa', to: 'requirements_ready', actor: 'sa@example.com', timestamp: 't1', note: 'fix UI' },
        { from: 'requirements_ready', to: 'assigned_to_sa', actor: 'ba@example.com', timestamp: 't2', note: null },
      ],
    });
    render(
      <ReturnForChangesControl
        record={record}
        userGroups={['system-architect']}
        onRecordUpdated={vi.fn()}
        registry={registry}
      />,
    );
    expect(
      screen.getByRole('button', { name: /Return to Business Analyst for changes/i }),
    ).toBeTruthy();
  });
});
