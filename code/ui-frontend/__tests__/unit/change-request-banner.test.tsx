/**
 * Unit tests for the change-request banner (Part A of the SA→BA feedback fix).
 *
 * Verifies the registry-driven note extractor and that the banner renders the
 * SA's note only to the persona who now owns the bounced-back project.
 */
import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

import { ChangeRequestBanner } from '../../app/components/ChangeRequestBanner';
import { latestChangeRequest } from '../../app/components/HandoffStatusBadge';
import { WorkflowRegistry, type RegistryData } from '../../app/lib/workflowRegistry';
import type {
  ProjectRecord,
  AssignmentHistoryEntry,
} from '../../app/services/projectService';

const REGISTRY_DATA: RegistryData = {
  initial_state: 'draft',
  personas: {
    'requirements-analyst': { display: 'Business Analyst', order: 1 },
    'system-architect': { display: 'Solution Architect', order: 2 },
  },
  segments: {
    ba_segment: {
      owning_role: 'requirements-analyst',
      stages: ['requirements_analysis'],
      entry_node: 'requirements_analysis',
      exit_hub: 'hub_a',
    },
    sa_segment: {
      owning_role: 'system-architect',
      stages: ['application_design'],
      entry_node: 'application_design',
      exit_hub: 'hub_b',
    },
  },
  states: {
    draft: { owning_role: 'requirements-analyst', segment: 'ba_segment', forward: 'requirements_ready' },
    requirements_ready: { owning_role: 'requirements-analyst', segment: 'ba_segment', forward: 'assigned_to_sa' },
    assigned_to_sa: { owning_role: 'system-architect', segment: 'sa_segment', forward: 'ready_for_dev' },
    ready_for_dev: { owning_role: 'system-architect', segment: 'sa_segment', forward: null },
  },
  request_changes: {
    assigned_to_sa: { target: 'requirements_ready', label: 'Requirements changes requested' },
  },
};

const registry = new WorkflowRegistry(REGISTRY_DATA);

function entry(
  from: string | null,
  to: string,
  note: string | null = null,
  actor = 'sa-tester@example.com',
): AssignmentHistoryEntry {
  return { from, to, actor, timestamp: '2026-06-16T18:31:55Z', note };
}

function makeRecord(
  overrides: Partial<Pick<ProjectRecord, 'handoff_state' | 'owning_role' | 'assignment_history'>> = {},
): Pick<ProjectRecord, 'handoff_state' | 'owning_role' | 'assignment_history'> {
  return {
    handoff_state: 'requirements_ready',
    owning_role: 'requirements-analyst',
    assignment_history: [],
    ...overrides,
  };
}

afterEach(cleanup);

describe('latestChangeRequest', () => {
  it('extracts the note + label for a request-changes bounce into the current state', () => {
    const record = makeRecord({
      assignment_history: [
        entry(null, 'draft', 'created'),
        entry('assigned_to_sa', 'requirements_ready', 'Please check the UI requirements once again.'),
      ],
    });
    const info = latestChangeRequest(record, registry);
    expect(info).not.toBeNull();
    expect(info!.label).toBe('Requirements changes requested');
    expect(info!.note).toBe('Please check the UI requirements once again.');
    expect(info!.actor).toBe('sa-tester@example.com');
  });

  it('returns null when the latest entry is a forward move, not a bounce', () => {
    const record = makeRecord({
      handoff_state: 'assigned_to_sa',
      owning_role: 'system-architect',
      assignment_history: [entry('requirements_ready', 'assigned_to_sa')],
    });
    expect(latestChangeRequest(record, registry)).toBeNull();
  });
});

describe('ChangeRequestBanner', () => {
  const bounced = makeRecord({
    assignment_history: [
      entry('assigned_to_sa', 'requirements_ready', 'Please check the UI requirements once again.'),
    ],
  });

  it('shows the note to the owning persona (the BA)', () => {
    render(
      <ChangeRequestBanner record={bounced} userGroups={['requirements-analyst']} registry={registry} />,
    );
    expect(screen.getByTestId('change-request-banner')).toBeInTheDocument();
    expect(screen.getByText('Requirements changes requested')).toBeInTheDocument();
    expect(screen.getByText('Please check the UI requirements once again.')).toBeInTheDocument();
  });

  it('does not show to a non-owner (the SA who requested the changes)', () => {
    render(
      <ChangeRequestBanner record={bounced} userGroups={['system-architect']} registry={registry} />,
    );
    expect(screen.queryByTestId('change-request-banner')).not.toBeInTheDocument();
  });

  it('renders nothing when there is no recent bounce', () => {
    const fwd = makeRecord({
      handoff_state: 'assigned_to_sa',
      owning_role: 'system-architect',
      assignment_history: [entry('requirements_ready', 'assigned_to_sa')],
    });
    render(<ChangeRequestBanner record={fwd} userGroups={['system-architect']} registry={registry} />);
    expect(screen.queryByTestId('change-request-banner')).not.toBeInTheDocument();
  });
});
