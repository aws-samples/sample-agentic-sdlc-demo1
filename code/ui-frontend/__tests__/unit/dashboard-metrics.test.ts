/**
 * Unit tests for `dashboardMetrics` — the pure aggregation layer behind the
 * Maintenance Specialist operations dashboard. No React / network involved.
 */

import { describe, it, expect } from 'vitest';

import {
  resolvePersonaLabel,
  stageLabel,
  buildProjectRows,
  summarizeByPersona,
  summarizeTotals,
  lastActivityOf,
  KNOWN_ROLES,
} from '../../app/services/dashboardMetrics';
import { WorkflowRegistry, type RegistryData } from '../../app/lib/workflowRegistry';
import type { ProjectRecord } from '../../app/services/projectService';

const BA = 'requirements-analyst';
const SA = 'system-architect';

const REGISTRY = new WorkflowRegistry({
  initial_state: 'draft',
  personas: {
    [BA]: { display: 'Business Analyst', order: 1 },
    [SA]: { display: 'Solution Architect', order: 2 },
  },
  segments: {
    ba_segment: { owning_role: BA, stages: ['user_stories'], entry_node: 'x', exit_hub: 'y' },
    sa_segment: { owning_role: SA, stages: ['workflow_planning'], entry_node: 'a', exit_hub: 'b' },
  },
  states: {
    draft: { owning_role: BA, segment: 'ba_segment', forward: 'requirements_ready' },
    requirements_ready: { owning_role: BA, segment: 'ba_segment', forward: 'assigned_to_sa' },
    assigned_to_sa: { owning_role: SA, segment: 'sa_segment', forward: 'in_design' },
    in_design: { owning_role: SA, segment: 'sa_segment', forward: 'design_ready' },
    design_ready: { owning_role: SA, segment: 'sa_segment', forward: 'ready_for_dev' },
    ready_for_dev: { owning_role: SA, segment: 'sa_segment', forward: null },
  },
  request_changes: {
    assigned_to_sa: { target: 'requirements_ready', label: 'Changes requested' },
  },
} as RegistryData);

function rec(over: Partial<ProjectRecord>): ProjectRecord {
  return {
    project_uuid: 'u',
    project_id: 'p',
    name: 'P',
    description: '',
    type: 'development',
    created_at: '2026-01-01T00:00:00Z',
    created_by: 'ba@example.com',
    handoff_state: 'draft',
    owning_role: BA,
    artifact_prefix: 'p',
    assignment_history: [],
    ...over,
  };
}

describe('resolvePersonaLabel', () => {
  it('uses the registry display label for a registry persona', () => {
    expect(resolvePersonaLabel(BA, REGISTRY)).toBe('Business Analyst');
    expect(resolvePersonaLabel(SA, REGISTRY)).toBe('Solution Architect');
  });

  it('falls back to the known-role map for a non-registry persona', () => {
    // software-developer is a Cognito group but not in this 2-persona registry.
    expect(resolvePersonaLabel('software-developer', REGISTRY)).toBe('Software Developer');
    expect(resolvePersonaLabel('maintenance-specialist', REGISTRY)).toBe('Maintenance Specialist');
  });

  it('returns Unassigned for empty and the raw value for a truly unknown role', () => {
    expect(resolvePersonaLabel('', REGISTRY)).toBe('Unassigned');
    expect(resolvePersonaLabel(null, REGISTRY)).toBe('Unassigned');
    expect(resolvePersonaLabel('brand-new-role', REGISTRY)).toBe('brand-new-role');
  });
});

describe('stageLabel', () => {
  it('maps known states to curated labels', () => {
    expect(stageLabel('assigned_to_sa')).toBe('Assigned to Solution Architect');
    expect(stageLabel('ready_for_dev')).toBe('Ready for development');
    expect(stageLabel('assigned_to_dev')).toBe('Assigned to Software Developer');
    expect(stageLabel('implementation_submitted')).toBe('Implementation submitted');
    expect(stageLabel('assigned_to_qa')).toBe('Assigned to QA Engineer');
    expect(stageLabel('assigned_to_devops')).toBe('Assigned to DevOps Engineer');
    expect(stageLabel('deployed')).toBe('Deployed');
    expect(stageLabel('draft')).toBe('Draft');
  });

  it('humanizes unknown states', () => {
    expect(stageLabel('some_new_stage')).toBe('Some New Stage');
    expect(stageLabel('')).toBe('Unknown');
  });
});

describe('lastActivityOf', () => {
  it('returns the last history timestamp, or created_at when no history', () => {
    expect(lastActivityOf(rec({ created_at: '2026-01-01T00:00:00Z', assignment_history: [] }))).toBe(
      '2026-01-01T00:00:00Z',
    );
    expect(
      lastActivityOf(
        rec({
          assignment_history: [
            { from: null, to: 'draft', actor: 'ba', timestamp: '2026-02-01T00:00:00Z', note: null },
            { from: 'draft', to: 'requirements_ready', actor: 'ba', timestamp: '2026-03-01T00:00:00Z', note: null },
          ],
        }),
      ),
    ).toBe('2026-03-01T00:00:00Z');
  });
});

describe('buildProjectRows', () => {
  it('maps records to rows and sorts most-recently-active first', () => {
    const records = [
      rec({ project_uuid: 'a', name: 'Alpha', handoff_state: 'draft', owning_role: BA, created_at: '2026-01-01T00:00:00Z' }),
      rec({
        project_uuid: 'b',
        name: 'Beta',
        handoff_state: 'assigned_to_sa',
        owning_role: SA,
        assignment_history: [
          { from: null, to: 'draft', actor: 'ba', timestamp: '2026-05-01T00:00:00Z', note: null },
        ],
      }),
    ];
    const rows = buildProjectRows(records, REGISTRY);
    // Beta (May) sorts before Alpha (Jan).
    expect(rows.map((r) => r.name)).toEqual(['Beta', 'Alpha']);
    const beta = rows[0];
    expect(beta.stage).toBe('Assigned to Solution Architect');
    expect(beta.assignedTo).toBe('Solution Architect');
    expect(beta.transitions).toBe(1);
  });
});

describe('summarizeByPersona', () => {
  it('counts owned projects per role and includes every known role (0 when none)', () => {
    const records = [
      rec({ owning_role: BA }),
      rec({ owning_role: SA }),
      rec({ owning_role: SA }),
    ];
    const summary = summarizeByPersona(records, REGISTRY);
    // All six known roles present, in known order.
    expect(summary.map((s) => s.role)).toEqual([...KNOWN_ROLES]);
    const byRole = Object.fromEntries(summary.map((s) => [s.role, s.owned]));
    expect(byRole[BA]).toBe(1);
    expect(byRole[SA]).toBe(2);
    expect(byRole['software-developer']).toBe(0);
    // Labels resolved.
    expect(summary.find((s) => s.role === BA)!.label).toBe('Business Analyst');
  });
});

describe('summarizeTotals', () => {
  it('totals projects and counts by state', () => {
    const records = [
      rec({ handoff_state: 'draft' }),
      rec({ handoff_state: 'draft' }),
      rec({ handoff_state: 'in_design' }),
    ];
    const totals = summarizeTotals(records);
    expect(totals.totalProjects).toBe(3);
    expect(totals.byState).toEqual({ draft: 2, in_design: 1 });
  });
});

describe('toProjectRow repo fields (C7 dashboard column)', () => {
  it('defaults repo fields when unlinked and passes them through when set', () => {
    const unlinked = buildProjectRows([rec({})], REGISTRY)[0];
    expect(unlinked.repoStatus).toBe('not_linked');
    expect(unlinked.repoUrl).toBeNull();
    expect(unlinked.repoCommit).toBeNull();

    const linked = buildProjectRows(
      [
        rec({
          repo_url: 'https://github.com/acme/widget',
          repo_ingest_status: 'ready',
          repo_commit: 'abcdef1234567',
          repo_ingest_error: null,
        }),
      ],
      REGISTRY,
    )[0];
    expect(linked.repoStatus).toBe('ready');
    expect(linked.repoUrl).toBe('https://github.com/acme/widget');
    expect(linked.repoCommit).toBe('abcdef1234567');
    expect(linked.repoError).toBeNull();
  });
});
