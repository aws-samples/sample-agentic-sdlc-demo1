/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
/**
 * Handoff status badge — purely presentational indicator rendered next to
 * each project on the project list (design Section 10, Req 14.1–14.7).
 *
 * The label is derived from `(handoff_state, owning_role, userGroups)`
 * through the registry — never hardcoded — so the same component covers BA,
 * SA, and any future persona automatically (Req 14.2/14.3/14.4):
 *
 *   1. "Assigned to you"        — when the caller's groups own the current state.
 *   2. "Changes requested"      — when the latest `assignment_history` entry
 *                                  is a request-changes bounce, derived from
 *                                  the registry's `request_changes` label.
 *                                  Takes precedence over the "Awaiting …"
 *                                  label (Req 14.4).
 *   3. "Awaiting <next role>"   — derived from `forward(state)` and the next
 *                                  owning role's display label (Req 14.3).
 *
 * The badge is wrapped in a local error boundary so a failed lookup or render
 * for a single project never blocks the list or that project's entry from
 * appearing (Req 14.7). Indicators refresh on the next `GET /projects` call;
 * there is no push channel for the list (Req 14.5).
 */

import React from 'react';
import { CheckCircle2, Clock, AlertCircle } from 'lucide-react';

import {
  getRegistry,
  type WorkflowRegistry,
} from '../lib/workflowRegistry';
import type {
  ProjectRecord,
  AssignmentHistoryEntry,
} from '../services/projectService';

// ---------------------------------------------------------------------------
// Indicator derivation
// ---------------------------------------------------------------------------

/** The kinds of indicator the badge can render. */
export type HandoffIndicatorKind = 'assigned-to-you' | 'changes-requested' | 'awaiting';

export interface HandoffIndicator {
  /** Discriminator used to pick the visual style. */
  kind: HandoffIndicatorKind;
  /** Final display label (e.g. "Awaiting Solution Architect"). */
  label: string;
}

/**
 * Look up the registry's request-changes descriptor that produced the most
 * recent `assignment_history` entry, or `null` when the latest entry is not a
 * request-changes bounce.
 *
 * The bounce is identified by the registry: the request-changes entry indexed
 * by `last.from` must declare `last.to` as its target. This is positional (it
 * inspects `from`/`to` rather than `note`) so a user-typed note never alters
 * which badge appears.
 *
 * Returning the descriptor (rather than just a boolean) lets the caller use
 * the registry-supplied label directly — the label lives on the *source* state
 * of the bounce (e.g. `request_changes.assigned_to_sa.label`), not on the
 * current state, so we cannot recover it from `record.handoff_state` alone.
 */
function lastHistoryRequestChanges(
  record: Pick<ProjectRecord, 'handoff_state' | 'assignment_history'>,
  registry: WorkflowRegistry,
) {
  const history = record.assignment_history;
  if (!history || history.length === 0) return null;

  const last: AssignmentHistoryEntry = history[history.length - 1];
  if (last.to !== record.handoff_state || last.from == null) return null;

  // The bounce is identified by the registry: the request-changes entry for
  // `last.from` must target `last.to` (== current state).
  const rc = registry.requestChangesInto(last.from);
  if (rc == null || rc.target !== last.to) return null;
  return rc;
}

/** Details of the most recent request-changes bounce, including the note. */
export interface ChangeRequestInfo {
  /** Registry-supplied label for the bounce (e.g. "Requirements changes requested"). */
  label: string;
  /** The free-form note the requester typed (may be `null`). */
  note: string | null;
  /** Identity of the actor who requested the changes. */
  actor: string;
  /** Source state of the bounce (e.g. `assigned_to_sa`). */
  from: string;
}

/**
 * Return details (incl. the typed note) of the most recent request-changes
 * bounce when the latest `assignment_history` entry is the registry-defined
 * request-changes edge into the current `handoff_state`; otherwise `null`.
 *
 * Reuses the same positional, registry-driven detection as the status badge,
 * so it stays correct for any persona chain. Unlike the badge, this surfaces
 * the human-readable `note` for display (e.g. the change-request banner shown
 * to the persona who now owns the bounced-back work).
 */
export function latestChangeRequest(
  record: Pick<ProjectRecord, 'handoff_state' | 'assignment_history'>,
  registry: WorkflowRegistry = getRegistry(),
): ChangeRequestInfo | null {
  const history = record.assignment_history;
  if (!history || history.length === 0) return null;
  const last: AssignmentHistoryEntry = history[history.length - 1];
  if (last.to !== record.handoff_state || last.from == null) return null;
  const rc = registry.requestChangesInto(last.from);
  if (rc == null || rc.target !== last.to) return null;
  return { label: rc.label, note: last.note, actor: last.actor, from: last.from };
}

/**
 * Compute the indicator for a project, or `null` when no badge applies
 * (terminal state with no forward, or no other case fires).
 *
 * Pure: same `(record, userGroups, registry)` always yields the same result.
 *
 * Precedence (design Section 10):
 *   1. "Assigned to you"     — caller's groups include `record.owning_role` (Req 14.2).
 *   2. "Changes requested"   — most recent history entry is the registry-defined
 *                              request-changes bounce. Label comes from the
 *                              registry's `request_changes[last.from].label`,
 *                              not the current state, because the bounce
 *                              descriptor lives on the *source* state. Takes
 *                              precedence over "Awaiting …" (Req 14.4).
 *   3. "Awaiting <next role>" — derived from `forward(state)` and the next
 *                                owning role's display label, so non-owner
 *                                viewers see who the project is heading to
 *                                regardless of which role they themselves are
 *                                (Req 14.3).
 */
export function deriveIndicator(
  record: Pick<ProjectRecord, 'handoff_state' | 'owning_role' | 'assignment_history'>,
  userGroups: string[],
  registry: WorkflowRegistry = getRegistry(),
): HandoffIndicator | null {
  // 14.2 — generic "Assigned to you" works for any current owning role.
  if (userGroups.includes(record.owning_role)) {
    return { kind: 'assigned-to-you', label: 'Assigned to you' };
  }

  // 14.4 — a request-changes bounce takes precedence over the "Awaiting …"
  // label. The descriptor (and therefore the label) is registry-driven so a
  // future chain can pick a different word without code changes.
  const bounce = lastHistoryRequestChanges(record, registry);
  if (bounce) {
    return { kind: 'changes-requested', label: bounce.label };
  }

  // 14.3 — "Awaiting <next owning role>" using the registry's forward state.
  // Shown to any non-owner viewer (the "Assigned to you" branch above already
  // claimed the owner), so a BA viewing an SA-owned project and an SA viewing
  // a BA-owned project both see who the project is heading to.
  const fwd = registry.forward(record.handoff_state);
  if (fwd) {
    const nextRole = registry.owningRole(fwd);
    return {
      kind: 'awaiting',
      label: `Awaiting ${registry.displayLabel(nextRole)}`,
    };
  }

  return null;
}

// ---------------------------------------------------------------------------
// Presentational badge
// ---------------------------------------------------------------------------

const KIND_STYLES: Record<HandoffIndicatorKind, { className: string; Icon: React.ElementType }> = {
  'assigned-to-you': {
    className: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    Icon: CheckCircle2,
  },
  'changes-requested': {
    className: 'border-amber-200 bg-amber-50 text-amber-700',
    Icon: AlertCircle,
  },
  awaiting: {
    className: 'border-blue-200 bg-blue-50 text-blue-700',
    Icon: Clock,
  },
};

interface BadgeViewProps {
  indicator: HandoffIndicator;
}

function BadgeView({ indicator }: BadgeViewProps) {
  const style = KIND_STYLES[indicator.kind];
  const Icon = style.Icon;
  return (
    <span
      data-kind={indicator.kind}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${style.className}`}
    >
      <Icon className="size-3 shrink-0" aria-hidden="true" />
      {indicator.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Per-badge error boundary (Req 14.7)
// ---------------------------------------------------------------------------

interface BadgeErrorBoundaryProps {
  children: React.ReactNode;
}

interface BadgeErrorBoundaryState {
  hasError: boolean;
}

/**
 * Local error boundary scoped to a single badge. Mirrors `ErrorBoundary.tsx`'s
 * shape but renders nothing on error so the surrounding project entry remains
 * visible (Req 14.7 — list and entry render without the badge if the badge
 * fails). A render failure for one project's badge never affects another's.
 */
class BadgeErrorBoundary extends React.Component<
  BadgeErrorBoundaryProps,
  BadgeErrorBoundaryState
> {
  constructor(props: BadgeErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): BadgeErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    // Logged but not surfaced; the project entry continues to render.
    console.error('HandoffStatusBadge failed to render:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) return null;
    return this.props.children;
  }
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export interface HandoffStatusBadgeProps {
  /** Project record from the Project API. */
  record: Pick<ProjectRecord, 'handoff_state' | 'owning_role' | 'assignment_history'>;
  /** Cognito group names from the signed-in user's session. */
  userGroups: string[];
  /**
   * Optional registry override. Defaults to the registry loaded from
   * `/config.json`; tests pass an explicit registry to exercise alternative
   * chains.
   */
  registry?: WorkflowRegistry;
}

/**
 * Render the handoff status badge for a project, or nothing when no
 * indicator applies (terminal state, or no current/next-role match).
 *
 * Renders inside a local error boundary so a single bad record never blocks
 * the surrounding list (Req 14.7).
 */
export function HandoffStatusBadge({ record, userGroups, registry }: HandoffStatusBadgeProps) {
  return (
    <BadgeErrorBoundary>
      <HandoffStatusBadgeInner record={record} userGroups={userGroups} registry={registry} />
    </BadgeErrorBoundary>
  );
}

/**
 * Inner derivation + render. Splitting it lets the error boundary above catch
 * synchronous derivation failures (e.g. a registry lookup throwing on an
 * unknown role) in addition to render-time failures.
 */
function HandoffStatusBadgeInner({ record, userGroups, registry }: HandoffStatusBadgeProps) {
  let indicator: HandoffIndicator | null;
  try {
    indicator = deriveIndicator(record, userGroups, registry);
  } catch (err) {
    console.error('HandoffStatusBadge derivation failed:', err);
    return null;
  }
  if (!indicator) return null;
  return <BadgeView indicator={indicator} />;
}
