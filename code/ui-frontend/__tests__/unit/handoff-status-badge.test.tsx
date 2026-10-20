/**
 * Unit tests for the `HandoffStatusBadge` (task 7.3; design Section 10,
 * Req 14.1–14.7).
 *
 * Covers indicator derivation across `(handoff_state, owning_role,
 * userGroups, history)` and the per-badge error boundary that isolates a
 * single project's badge failure from the rest of the list (Req 14.7).
 */

import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';

import {
  HandoffStatusBadge,
  deriveIndicator,
} from '../../app/components/HandoffStatusBadge';
import { WorkflowRegistry, type RegistryData } from '../../app/lib/workflowRegistry';
import type {
  ProjectRecord,
  AssignmentHistoryEntry,
} from '../../app/services/projectService';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const BA_SA_REGISTRY_DATA: RegistryData = {
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

const baSaRegistry = new WorkflowRegistry(BA_SA_REGISTRY_DATA);

/** Build a Project-record-shaped object that satisfies the badge's typed slice. */
function makeRecord(
  overrides: Partial<
    Pick<ProjectRecord, 'handoff_state' | 'owning_role' | 'assignment_history'>
  > = {},
): Pick<ProjectRecord, 'handoff_state' | 'owning_role' | 'assignment_history'> {
  return {
    handoff_state: 'draft',
    owning_role: 'requirements-analyst',
    assignment_history: [],
    ...overrides,
  };
}

/** Convenience: build a single assignment history entry. */
function entry(
  from: string | null,
  to: string,
  note: string | null = null,
): AssignmentHistoryEntry {
  return {
    from,
    to,
    actor: 'someone@example.com',
    timestamp: '2024-01-01T00:00:00Z',
    note,
  };
}

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// deriveIndicator — pure logic
// ---------------------------------------------------------------------------

describe('deriveIndicator', () => {
  it('returns "Assigned to you" when the caller is in the owning role (Req 14.2)', () => {
    const record = makeRecord({
      handoff_state: 'draft',
      owning_role: 'requirements-analyst',
    });
    expect(
      deriveIndicator(record, ['requirements-analyst'], baSaRegistry),
    ).toEqual({ kind: 'assigned-to-you', label: 'Assigned to you' });
  });

  it('returns "Awaiting Solution Architect" for a BA whose project is requirements_ready (Req 14.3)', () => {
    // BA-side view of a project that has finished BA work and is waiting for
    // the BA to assign it to the SA. The badge derives the next role's display
    // label through the registry rather than hardcoding "Solution Architect".
    const record = makeRecord({
      handoff_state: 'requirements_ready',
      owning_role: 'requirements-analyst',
    });
    expect(
      deriveIndicator(record, ['system-architect'], baSaRegistry),
    ).toEqual({ kind: 'awaiting', label: 'Awaiting Solution Architect' });
  });

  it('returns "Awaiting Solution Architect" for a BA on assigned_to_sa state (Req 14.3)', () => {
    // BA looking at a project that is now owned by the SA. Caller is BA only.
    const record = makeRecord({
      handoff_state: 'assigned_to_sa',
      owning_role: 'system-architect',
    });
    expect(
      deriveIndicator(record, ['requirements-analyst'], baSaRegistry),
    ).toEqual({ kind: 'awaiting', label: 'Awaiting Solution Architect' });
  });

  it('returns "Changes requested" when the latest history entry is the request-changes bounce (Req 14.4)', () => {
    // After SA selects request-changes, state returns to requirements_ready
    // and the BA sees "Changes requested" instead of "Awaiting …".
    const record = makeRecord({
      handoff_state: 'requirements_ready',
      owning_role: 'requirements-analyst',
      assignment_history: [
        entry(null, 'draft'),
        entry('draft', 'requirements_ready'),
        entry('requirements_ready', 'assigned_to_sa'),
        // The bounce: from assigned_to_sa back into requirements_ready.
        entry('assigned_to_sa', 'requirements_ready', 'Please add NFRs'),
      ],
    });
    // Even though the BA owns the state, "Assigned to you" still wins for the
    // BA — Req 14.2's "for ANY role, present or future" is the highest
    // precedence rule. The "Changes requested" badge applies to OTHER viewers.
    expect(
      deriveIndicator(record, ['requirements-analyst'], baSaRegistry),
    ).toEqual({ kind: 'assigned-to-you', label: 'Assigned to you' });

    // For a non-owner caller (e.g. an SA viewing the same project), the
    // "Changes requested" indicator is shown over "Awaiting …".
    expect(
      deriveIndicator(record, ['system-architect'], baSaRegistry),
    ).toEqual({ kind: 'changes-requested', label: 'Changes requested' });
  });

  it('does NOT show "Changes requested" when the latest history entry is a forward step', () => {
    // Same `requirements_ready` state but reached forward from `draft`, not
    // via a bounce. The badge must not report "Changes requested".
    const record = makeRecord({
      handoff_state: 'requirements_ready',
      owning_role: 'requirements-analyst',
      assignment_history: [
        entry(null, 'draft'),
        entry('draft', 'requirements_ready'),
      ],
    });
    expect(
      deriveIndicator(record, ['system-architect'], baSaRegistry),
    ).toEqual({ kind: 'awaiting', label: 'Awaiting Solution Architect' });
  });

  it('returns null on a terminal state when the caller is not the owning role', () => {
    // ready_for_dev has no `forward`, so no "Awaiting …" badge. Caller is
    // not the owner, so no "Assigned to you" either.
    const record = makeRecord({
      handoff_state: 'ready_for_dev',
      owning_role: 'system-architect',
    });
    expect(
      deriveIndicator(record, ['requirements-analyst'], baSaRegistry),
    ).toBeNull();
  });

  it('returns null when an empty userGroups list also has no other applicable indicator', () => {
    // Without groups the caller can never own a state, so the only path to a
    // badge is "Awaiting …". On the terminal state that path is closed too.
    const record = makeRecord({
      handoff_state: 'ready_for_dev',
      owning_role: 'system-architect',
    });
    expect(deriveIndicator(record, [], baSaRegistry)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

describe('HandoffStatusBadge — rendering', () => {
  it('renders the "Assigned to you" badge for the owning role', () => {
    render(
      <HandoffStatusBadge
        record={makeRecord({
          handoff_state: 'draft',
          owning_role: 'requirements-analyst',
        })}
        userGroups={['requirements-analyst']}
        registry={baSaRegistry}
      />,
    );
    expect(screen.getByText('Assigned to you')).toBeInTheDocument();
    // The data-kind attribute is the discriminator the surrounding list can
    // use to style or filter the badge if needed.
    expect(screen.getByText('Assigned to you').closest('span')).toHaveAttribute(
      'data-kind',
      'assigned-to-you',
    );
  });

  it('renders "Awaiting Solution Architect" with the registry-derived label', () => {
    render(
      <HandoffStatusBadge
        record={makeRecord({
          handoff_state: 'assigned_to_sa',
          owning_role: 'system-architect',
        })}
        userGroups={['requirements-analyst']}
        registry={baSaRegistry}
      />,
    );
    expect(screen.getByText('Awaiting Solution Architect')).toBeInTheDocument();
  });

  it('renders nothing when no indicator applies (terminal state, non-owner)', () => {
    const { container } = render(
      <HandoffStatusBadge
        record={makeRecord({
          handoff_state: 'ready_for_dev',
          owning_role: 'system-architect',
        })}
        userGroups={['requirements-analyst']}
        registry={baSaRegistry}
      />,
    );
    expect(container.textContent).toBe('');
  });

  it('isolates a derivation failure: renders nothing instead of throwing (Req 14.7)', () => {
    // A malformed record references a state that does not exist in the
    // registry. WorkflowRegistry.forward / owningRole both throw on unknown
    // states; the badge's local error boundary must swallow the failure so
    // the surrounding list keeps rendering.
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const malformed = makeRecord({
      handoff_state: 'not_a_real_state',
      owning_role: 'requirements-analyst',
    });
    const { container } = render(
      <HandoffStatusBadge
        record={malformed}
        userGroups={['system-architect']}
        registry={baSaRegistry}
      />,
    );
    expect(container.textContent).toBe('');
    expect(consoleErrorSpy).toHaveBeenCalled();
    consoleErrorSpy.mockRestore();
  });

  it('cross-project isolation: a malformed badge never blocks neighbouring badges (Req 14.7, Property 10)', () => {
    /**
     * Property 10 / Req 14.7 isolation: rendering a list of badges where
     * one project's record is malformed must not prevent the other
     * projects' badges (or the surrounding list shell) from rendering.
     *
     * This exercises the per-badge BadgeErrorBoundary at the granularity
     * the project list actually uses — one boundary per record — so a
     * failing badge cannot escape its own project entry.
     */
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    // Viewer is the SA. With this viewer, none of these records short-circuit
    // through the early "Assigned to you" branch (which fires on owning-role
    // match before any registry lookup), so the malformed record actually
    // reaches `registry.forward()` and triggers the failure path we need
    // the per-badge error boundary to swallow.
    const records = [
      // BA-owned draft. SA viewer -> "Awaiting Business Analyst" via
      // forward(draft) = requirements_ready, owned by requirements-analyst.
      makeRecord({
        handoff_state: 'draft',
        owning_role: 'requirements-analyst',
      }),
      // Malformed: state not in the registry; forward()/owningRole() throw.
      makeRecord({
        handoff_state: 'not_a_real_state',
        owning_role: 'requirements-analyst',
      }),
      // SA-owned assigned_to_sa. SA viewer -> "Assigned to you".
      makeRecord({
        handoff_state: 'assigned_to_sa',
        owning_role: 'system-architect',
      }),
    ];

    // Mirror the project-list shell: a wrapping element with one entry per
    // project, so we can assert each entry independently.
    render(
      <ul data-testid="project-list">
        {records.map((rec, i) => (
          <li key={i} data-testid={`entry-${i}`}>
            <span>name-{i}</span>
            <HandoffStatusBadge
              record={rec}
              userGroups={['system-architect']}
              registry={baSaRegistry}
            />
          </li>
        ))}
      </ul>,
    );

    // The list shell itself rendered.
    expect(screen.getByTestId('project-list')).toBeInTheDocument();

    // Each project entry rendered, including the malformed one (Req 14.7
    // — the affected entry still renders without the badge).
    for (let i = 0; i < records.length; i++) {
      const entry = screen.getByTestId(`entry-${i}`);
      expect(entry).toBeInTheDocument();
      expect(entry.textContent).toContain(`name-${i}`);
    }

    // The healthy badges rendered with their derived labels in the right
    // entries (scoped lookup, not screen-wide, so we are sure each label
    // came from its own project entry's badge).
    const entry0 = screen.getByTestId('entry-0');
    expect(within(entry0).getByText('Awaiting Business Analyst')).toBeInTheDocument();
    const entry2 = screen.getByTestId('entry-2');
    expect(within(entry2).getByText('Assigned to you')).toBeInTheDocument();

    // The malformed entry rendered nothing for the badge slot — no badge
    // text and no other label leaked into it.
    const malformedEntry = screen.getByTestId('entry-1');
    expect(malformedEntry.querySelector('[data-kind]')).toBeNull();

    // The boundary recorded the failure but did not rethrow.
    expect(consoleErrorSpy).toHaveBeenCalled();
    consoleErrorSpy.mockRestore();
  });
});
