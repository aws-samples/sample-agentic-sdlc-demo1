/**
 * Unit tests for the Solution Architect review experience (task 7.4; design
 * Section 8, Req 7.1, 7.3, 7.4, 7.5, 7.7).
 *
 * Covers the surface's three responsibilities:
 *   1. On `assigned_to_sa` the panel loads `requirements.md`, `stories.md`,
 *      and `personas.md` from the shared artifact prefix via `s3Service`
 *      (Req 7.1).
 *   2. The Approve / Request Changes buttons forward the runtime decision
 *      over the WebSocket *and* persist the handoff transition through the
 *      Project API:
 *        - Approve  -> assigned_to_sa -> in_design       (Req 7.4)
 *        - Request  -> assigned_to_sa -> requirements_ready,
 *                      with the SA's note recorded in history (Req 7.5).
 *   3. On `in_design` / `design_ready` the design-complete button advances
 *      the project through both forwards in one click — covering the chain
 *      `in_design -> design_ready -> ready_for_dev` (Req 7.7).
 *
 * Also asserts the Business Analyst session is never shown the SA panel
 * (presentational gating is purely additive — the Project API enforces
 * ownership server-side regardless).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';

const { transitionMock, getFileContentMock } = vi.hoisted(() => ({
  transitionMock: vi.fn(),
  getFileContentMock: vi.fn(),
}));

vi.mock('../../app/services/projectService', async () => {
  const actual =
    await vi.importActual<typeof import('../../app/services/projectService')>(
      '../../app/services/projectService',
    );
  return {
    ...actual,
    transition: transitionMock,
  };
});

vi.mock('../../app/services/s3Service', () => ({
  getFileContent: getFileContentMock,
}));

import { SolutionArchitectReviewPanel } from '../../app/components/SolutionArchitectReviewPanel';
import { ProjectApiError, type ProjectRecord } from '../../app/services/projectService';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeRecord(overrides: Partial<ProjectRecord> = {}): ProjectRecord {
  return {
    project_uuid: 'uuid-001',
    project_id: 'sample',
    name: 'Sample',
    description: '',
    type: 'development',
    created_at: '2024-01-01T00:00:00Z',
    created_by: 'ba@example.com',
    handoff_state: 'assigned_to_sa',
    owning_role: 'system-architect',
    artifact_prefix: 'sample',
    assignment_history: [
      {
        from: 'requirements_ready',
        to: 'assigned_to_sa',
        actor: 'ba@example.com',
        timestamp: '2024-01-01T00:01:00Z',
        note: null,
      },
    ],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Visibility gating
// ---------------------------------------------------------------------------

describe('SolutionArchitectReviewPanel — visibility gating', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getFileContentMock.mockResolvedValue('# placeholder');
  });

  afterEach(() => cleanup());

  it('renders nothing when the user is not in the system-architect group', () => {
    /**
     * BA viewers must never see the SA panel; presentational gating is the
     * first line of defense and the Project API re-checks ownership on
     * every transition (Req 2.3).
     */
    const { container } = render(
      <SolutionArchitectReviewPanel
        record={makeRecord()}
        userGroups={['requirements-analyst']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing on a state outside the SA-actionable set', () => {
    const { container } = render(
      <SolutionArchitectReviewPanel
        record={makeRecord({
          handoff_state: 'requirements_ready',
          owning_role: 'requirements-analyst',
        })}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders the review variant when an SA opens an assigned_to_sa project', async () => {
    render(
      <SolutionArchitectReviewPanel
        record={makeRecord()}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );
    expect(screen.getByText(/Review Business Analyst artifacts/)).toBeTruthy();
    // Approve exists on the review variant. (The handoff "Return for changes"
    // bounce now lives in the separate ReturnForChangesControl.)
    expect(screen.getByRole('button', { name: /Approve & continue/i })).toBeTruthy();
  });

  it('renders the design-complete variant on in_design', () => {
    render(
      <SolutionArchitectReviewPanel
        record={makeRecord({ handoff_state: 'in_design' })}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );
    expect(screen.getByText(/Design completion/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Mark design ready/i })).toBeTruthy();
  });

  it('renders the design-complete variant on design_ready with a different label', () => {
    render(
      <SolutionArchitectReviewPanel
        record={makeRecord({ handoff_state: 'design_ready' })}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /Mark ready for development/i })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Artifact loading (Req 7.1)
// ---------------------------------------------------------------------------

describe('SolutionArchitectReviewPanel — BA artifact loading', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => cleanup());

  it('loads requirements.md, stories.md, and personas.md from the artifact prefix', async () => {
    /**
     * The S3 keys are resolved against `record.artifact_prefix` so both
     * personas operate on the same artifact tree (Req 3.3) and the SA reads
     * the BA's outputs unchanged (Req 7.1).
     */
    getFileContentMock.mockImplementation(async (key: string) => `content of ${key}`);

    render(
      <SolutionArchitectReviewPanel
        record={makeRecord({ artifact_prefix: 'project_alpha' })}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(getFileContentMock).toHaveBeenCalledTimes(3);
    });

    const requested = getFileContentMock.mock.calls.map((args) => args[0]);
    expect(requested).toEqual([
      'project_alpha/aidlc-docs/inception/requirements/requirements.md',
      'project_alpha/aidlc-docs/inception/user-stories/stories.md',
      'project_alpha/aidlc-docs/inception/user-stories/personas.md',
    ]);
  });

  it('renders the active artifact body once content arrives', async () => {
    getFileContentMock.mockImplementation(async (key: string) => {
      if (key.endsWith('requirements.md')) return '# Sample Requirements';
      return '# Other artifact';
    });

    render(
      <SolutionArchitectReviewPanel
        record={makeRecord()}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );

    // The first artifact (Requirements) is the default active tab so its
    // content surfaces in the active artifact slot.
    const active = await screen.findByTestId('sa-review-active-artifact');
    await waitFor(() => {
      expect(within(active).getByText('Sample Requirements')).toBeTruthy();
    });
  });

  it('switching tabs reveals the next artifact body', async () => {
    getFileContentMock.mockImplementation(async (key: string) => {
      if (key.endsWith('stories.md')) return '# Stories Doc';
      return '# Other artifact';
    });

    render(
      <SolutionArchitectReviewPanel
        record={makeRecord()}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'User stories' }));
    const active = screen.getByTestId('sa-review-active-artifact');
    await waitFor(() => {
      expect(within(active).getByText('Stories Doc')).toBeTruthy();
    });
  });

  it('surfaces a per-artifact error and keeps the panel actionable', async () => {
    getFileContentMock.mockImplementation(async (key: string) => {
      if (key.endsWith('personas.md')) {
        throw new Error('S3 access denied');
      }
      return '# OK';
    });

    render(
      <SolutionArchitectReviewPanel
        record={makeRecord()}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Personas' }));
    const active = await screen.findByTestId('sa-review-active-artifact');
    await waitFor(() => {
      expect(within(active).getByText(/Failed to load Personas/)).toBeTruthy();
      expect(within(active).getByText(/S3 access denied/)).toBeTruthy();
    });
    // The Approve button is still actionable so the SA can still proceed if
    // they have read the necessary artifacts.
    expect(
      (screen.getByRole('button', { name: /Approve & continue/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Approve / Request Changes — runtime + Project API split
// ---------------------------------------------------------------------------

describe('SolutionArchitectReviewPanel — Approval Gate decisions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getFileContentMock.mockResolvedValue('# placeholder');
  });

  afterEach(() => cleanup());

  it('Approve forwards the runtime decision and persists assigned_to_sa -> in_design', async () => {
    /**
     * Req 7.4 / Req 10.6: the Approval Gate decision goes to the agent
     * graph over the WebSocket, while the *handoff* transition is the sole
     * responsibility of the Project API. This test asserts both happen.
     */
    const onSendMessage = vi.fn();
    const onRecordUpdated = vi.fn();
    transitionMock.mockResolvedValueOnce(makeRecord({ handoff_state: 'in_design' }));

    render(
      <SolutionArchitectReviewPanel
        record={makeRecord()}
        userGroups={['system-architect']}
        onSendMessage={onSendMessage}
        onRecordUpdated={onRecordUpdated}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Approve & continue/i }));

    // Runtime decision forwarded to the agent.
    expect(onSendMessage).toHaveBeenCalledWith('Approve & Continue');

    // Project API transition called with the `approve` action verb.
    await waitFor(() => {
      expect(transitionMock).toHaveBeenCalledTimes(1);
    });
    expect(transitionMock).toHaveBeenCalledWith('uuid-001', { action: 'approve' });

    // Updated record propagated to the parent.
    await waitFor(() => {
      expect(onRecordUpdated).toHaveBeenCalledWith(
        expect.objectContaining({ handoff_state: 'in_design' }),
      );
    });
  });

  it('Request Changes records the SA note and persists assigned_to_sa -> requirements_ready', async () => {
    // Moved to ReturnForChangesControl: the handoff bounce is now a clean
    // Project API call (no agent "Request Changes" WS text). See
    // change-request control tests.
  });

  it('surfaces a Project API error inline and keeps the panel actionable', async () => {
    /**
     * A `ProjectApiError` (HTTP 403, 422, 409, ...) must surface inline
     * without removing the panel so the SA has a path forward — parity
     * with the assign card's error UX.
     */
    transitionMock.mockRejectedValueOnce(
      new ProjectApiError(403, 'Caller is not the owning role for this state', 'FORBIDDEN'),
    );

    render(
      <SolutionArchitectReviewPanel
        record={makeRecord()}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Approve & continue/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/Caller is not the owning role for this state/),
      ).toBeTruthy();
    });
    // Buttons remain enabled so the SA can retry.
    expect(
      (screen.getByRole('button', { name: /Approve & continue/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Design completion (Req 7.7)
// ---------------------------------------------------------------------------

describe('SolutionArchitectReviewPanel — design completion forwards', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => cleanup());

  it('on in_design issues both forwards: in_design -> design_ready -> ready_for_dev', async () => {
    /**
     * Req 7.7 chains `in_design -> design_ready -> ready_for_dev`. The panel
     * issues the immediate forward and, when the API returns `design_ready`,
     * issues a second `design-complete` to land on `ready_for_dev` without a
     * second click.
     */
    transitionMock
      .mockResolvedValueOnce(makeRecord({ handoff_state: 'design_ready' }))
      .mockResolvedValueOnce(makeRecord({ handoff_state: 'ready_for_dev' }));

    render(
      <SolutionArchitectReviewPanel
        record={makeRecord({ handoff_state: 'in_design' })}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Mark design ready/i }));

    await waitFor(() => {
      expect(transitionMock).toHaveBeenCalledTimes(2);
    });
    expect(transitionMock.mock.calls[0]).toEqual(['uuid-001', { action: 'design-complete' }]);
    expect(transitionMock.mock.calls[1]).toEqual(['uuid-001', { action: 'design-complete' }]);
  });

  it('on design_ready issues exactly one design-complete forward', async () => {
    transitionMock.mockResolvedValueOnce(makeRecord({ handoff_state: 'ready_for_dev' }));

    render(
      <SolutionArchitectReviewPanel
        record={makeRecord({ handoff_state: 'design_ready' })}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Mark ready for development/i }));

    await waitFor(() => {
      expect(transitionMock).toHaveBeenCalledTimes(1);
    });
    expect(transitionMock).toHaveBeenCalledWith('uuid-001', { action: 'design-complete' });
  });

  it('aborts the second forward when the first design-complete fails', async () => {
    /**
     * If the first transition fails the panel must not blindly fire the
     * second forward — that would always be illegal anyway, but the panel
     * also surfaces the original error so the SA can react.
     */
    transitionMock.mockRejectedValueOnce(
      new ProjectApiError(409, 'ETag mismatch', 'ETAG_MISMATCH'),
    );

    render(
      <SolutionArchitectReviewPanel
        record={makeRecord({ handoff_state: 'in_design' })}
        userGroups={['system-architect']}
        onSendMessage={vi.fn()}
        onRecordUpdated={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Mark design ready/i }));

    await waitFor(() => {
      expect(screen.getByText(/ETag mismatch/)).toBeTruthy();
    });
    expect(transitionMock).toHaveBeenCalledTimes(1);
  });
});
