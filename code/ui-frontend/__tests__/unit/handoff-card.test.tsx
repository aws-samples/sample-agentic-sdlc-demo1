/**
 * Unit tests for the `handoff_required` WS message type and the assign card
 * (task 7.2; design Sections 5b / 9, Req 6.3, 6.4, 14.6).
 *
 * Covers:
 *   - `wsMessageToChatMessage` (via `WebSocketContext`'s exported helper) maps
 *     a `handoff_required` WS payload to a ChatMessage carrying the project
 *     identifier and roles in metadata so the assign card can render it.
 *   - `HandoffCard` renders the registry-derived label
 *     ("Assign to {next role display}") for the `to_role` from the message,
 *     and calls `projectService.transition(uuid, { action: "assign" })` on
 *     click — the only authorized server-side mutation path (Req 6.4).
 *   - Surface error handling: a server-side rejection (e.g. `FORBIDDEN`)
 *     surfaces inline without the card disappearing.
 *   - Resilience: a registry lookup failure for `to_role` falls back to the
 *     raw role name so the user always has a usable button.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

const { transitionMock, getProjectMock, getRegistryMock } = vi.hoisted(() => ({
  transitionMock: vi.fn(),
  getProjectMock: vi.fn(),
  getRegistryMock: vi.fn(),
}));

vi.mock('../../app/services/projectService', async () => {
  // Keep ProjectApiError as a real class so `instanceof` checks still work.
  const actual =
    await vi.importActual<typeof import('../../app/services/projectService')>(
      '../../app/services/projectService',
    );
  return {
    ...actual,
    transition: transitionMock,
    getProject: getProjectMock,
  };
});

vi.mock('../../app/lib/workflowRegistry', async () => {
  const actual =
    await vi.importActual<typeof import('../../app/lib/workflowRegistry')>(
      '../../app/lib/workflowRegistry',
    );
  return {
    ...actual,
    getRegistry: getRegistryMock,
  };
});

import { HandoffCard } from '../../app/components/chat/HandoffCard';
import { ProjectApiError } from '../../app/services/projectService';
import { wsMessageToChatMessage } from '../../app/context/WebSocketContext';
import type { ChatMessage } from '../../app/types/ws-messages';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeHandoffMessage(
  overrides: Partial<ChatMessage['metadata']> = {},
): ChatMessage {
  return {
    id: 'msg-1',
    type: 'handoff_required',
    content: 'User stories are ready. Assign to Solution Architect?',
    timestamp: 1,
    metadata: {
      summary: 'User stories are ready. Assign to Solution Architect?',
      project_uuid: 'uuid-001',
      from_role: 'requirements-analyst',
      to_role: 'system-architect',
      ...overrides,
    },
    resolved: false,
  };
}

/**
 * The configured registry's forward chain (mirrors `workflow_registry.json`).
 * Used by the card to step forward until ownership reaches `toRole`.
 */
const FORWARD_CHAIN: Record<string, string | null> = {
  draft: 'requirements_ready',
  requirements_ready: 'assigned_to_sa',
  assigned_to_sa: 'in_design',
  in_design: 'design_ready',
  design_ready: 'ready_for_dev',
  ready_for_dev: null,
};

/** Minimal registry stub returning display labels keyed on the group name. */
function fakeRegistry(displayMap: Record<string, string>) {
  return {
    displayLabel(role: string): string {
      if (Object.hasOwn(displayMap, role)) return displayMap[role];
      // Mirror WorkflowRegistry.displayLabel: throw on unknown roles so the
      // card's catch-and-fallback path can be exercised.
      throw new Error(`unknown persona role: '${role}'`);
    },
    forward(state: string): string | null {
      return FORWARD_CHAIN[state] ?? null;
    },
  } as unknown as ReturnType<typeof import('../../app/lib/workflowRegistry').getRegistry>;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('HandoffCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getRegistryMock.mockReturnValue(
      fakeRegistry({
        'requirements-analyst': 'Business Analyst',
        'system-architect': 'Solution Architect',
      }),
    );
  });

  afterEach(() => {
    cleanup();
  });

  it('renders the registry-derived assign label and the summary text', () => {
    /**
     * The card label is derived from the registry's display label for
     * `to_role` (design Section 9) so the BA → SA boundary shows
     * "Assign to Solution Architect" without hardcoding either role name.
     */
    render(<HandoffCard message={makeHandoffMessage()} />);

    expect(screen.getByRole('button', { name: 'Assign to Solution Architect' })).toBeTruthy();
    expect(screen.getByText(/User stories are ready/)).toBeTruthy();
    // Heading carries the registry-derived from/to labels.
    expect(screen.getByText('Handoff: Business Analyst → Solution Architect')).toBeTruthy();
  });

  it('advances the project forward until ownership reaches the SA on click', async () => {
    /**
     * The card's job is to transfer ownership to the next persona (design
     * Section 9), not to take a single fixed step. A fresh project sits at
     * `draft`, which is BA-owned; a single forward step only reaches
     * `requirements_ready` (still BA-owned). The card must therefore step
     * forward through the registry (draft → requirements_ready →
     * assigned_to_sa) until ownership lands on `to_role` (system-architect),
     * so the SA actually receives the project (Req 6.4). Each step is a
     * distinct, registry-validated Project API transition.
     */
    getProjectMock.mockResolvedValueOnce({
      project_uuid: 'uuid-001',
      handoff_state: 'draft',
      owning_role: 'requirements-analyst',
    });
    transitionMock
      .mockResolvedValueOnce({
        project_uuid: 'uuid-001',
        handoff_state: 'requirements_ready',
        owning_role: 'requirements-analyst',
      })
      .mockResolvedValueOnce({
        project_uuid: 'uuid-001',
        handoff_state: 'assigned_to_sa',
        owning_role: 'system-architect',
      });

    render(<HandoffCard message={makeHandoffMessage()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Assign to Solution Architect' }));

    await waitFor(() => {
      expect(transitionMock).toHaveBeenCalledTimes(2);
    });
    // Two explicit forward steps, each targeting the registry's next state.
    expect(transitionMock).toHaveBeenNthCalledWith(1, 'uuid-001', {
      action: 'assign',
      target_state: 'requirements_ready',
    });
    expect(transitionMock).toHaveBeenNthCalledWith(2, 'uuid-001', {
      action: 'assign',
      target_state: 'assigned_to_sa',
    });

    // After resolution the button is disabled and shows the resolved label
    // so a stray double-click never re-submits the same handoff.
    await waitFor(() => {
      const button = screen.getByRole('button', { name: 'Assigned' });
      expect((button as HTMLButtonElement).disabled).toBe(true);
    });
  });

  it('advances a single step when the project is already at requirements_ready', async () => {
    /**
     * When the project is already one BA-owned step from the boundary
     * (`requirements_ready`), a single forward transition reaches
     * `assigned_to_sa` and ownership flips to the SA — the loop stops after
     * one step.
     */
    getProjectMock.mockResolvedValueOnce({
      project_uuid: 'uuid-001',
      handoff_state: 'requirements_ready',
      owning_role: 'requirements-analyst',
    });
    transitionMock.mockResolvedValueOnce({
      project_uuid: 'uuid-001',
      handoff_state: 'assigned_to_sa',
      owning_role: 'system-architect',
    });

    render(<HandoffCard message={makeHandoffMessage()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Assign to Solution Architect' }));

    await waitFor(() => {
      expect(transitionMock).toHaveBeenCalledTimes(1);
    });
    expect(transitionMock).toHaveBeenCalledWith('uuid-001', {
      action: 'assign',
      target_state: 'assigned_to_sa',
    });
  });

  it('renders an inline error and keeps the card visible when transition fails', async () => {
    /**
     * A `ProjectApiError` (e.g. HTTP 403 FORBIDDEN, HTTP 422 ILLEGAL_TRANSITION)
     * surfaces inline without removing the card so the user can retry.
     */
    getProjectMock.mockResolvedValueOnce({
      project_uuid: 'uuid-001',
      handoff_state: 'draft',
      owning_role: 'requirements-analyst',
    });
    transitionMock.mockRejectedValueOnce(
      new ProjectApiError(403, 'Caller is not the owning role for this state', 'FORBIDDEN'),
    );

    render(<HandoffCard message={makeHandoffMessage()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Assign to Solution Architect' }));

    await waitFor(() => {
      expect(
        screen.getByText(/Assignment failed: Caller is not the owning role/),
      ).toBeTruthy();
    });
    // The card stays interactive so the user can retry.
    const button = screen.getByRole('button', { name: 'Assign to Solution Architect' });
    expect((button as HTMLButtonElement).disabled).toBe(false);
  });

  it('falls back to the raw role name when the registry has no display label', () => {
    /**
     * Req 14.7 / defensive rendering: if the registry lookup fails (unknown
     * persona, missing config) the card must still render with a usable
     * button label — never a thrown error or a missing button.
     */
    getRegistryMock.mockReturnValue(fakeRegistry({})); // every lookup throws

    render(
      <HandoffCard
        message={makeHandoffMessage({ to_role: 'software-developer' })}
      />,
    );
    expect(screen.getByRole('button', { name: 'Assign to software-developer' })).toBeTruthy();
  });

  it('disables the assign button when the message carries no project_uuid', () => {
    /**
     * Without a project identifier the Project API call cannot be addressed,
     * so the button is presented in a disabled state instead of attempting
     * a request that the API would reject anyway.
     */
    render(<HandoffCard message={makeHandoffMessage({ project_uuid: '' })} />);
    const button = screen.getByRole('button', { name: 'Assign to Solution Architect' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// WS message -> ChatMessage mapping (WebSocketContext.wsMessageToChatMessage)
// ---------------------------------------------------------------------------

describe('wsMessageToChatMessage — handoff_required mapping', () => {
  it('produces a handoff_required ChatMessage carrying the WS payload in metadata', () => {
    /**
     * The runtime emits the WS `handoff_required` message at a Handoff_Gate
     * (design Sections 5b / 9, Req 6.3). The Frontend's mapper places the
     * project identifier and roles in `metadata` so the assign card can
     * render the registry-derived label and call the Project API.
     */
    const chatMsg = wsMessageToChatMessage({
      type: 'handoff_required',
      project_uuid: 'uuid-001',
      from_role: 'requirements-analyst',
      to_role: 'system-architect',
      summary: 'User stories are ready. Assign to Solution Architect?',
    });

    expect(chatMsg.type).toBe('handoff_required');
    expect(chatMsg.content).toBe(
      'User stories are ready. Assign to Solution Architect?',
    );
    expect(chatMsg.metadata).toMatchObject({
      project_uuid: 'uuid-001',
      from_role: 'requirements-analyst',
      to_role: 'system-architect',
      summary: 'User stories are ready. Assign to Solution Architect?',
    });
    expect(chatMsg.resolved).toBe(false);
  });
});
