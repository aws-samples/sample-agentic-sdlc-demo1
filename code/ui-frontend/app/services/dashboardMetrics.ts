/**
 * Pure aggregation helpers for the maintenance-specialist admin dashboard.
 *
 * The dashboard is a read-only view over the shared Project Store: it lists
 * every project (cross-creator, via `projectService.listProjects`) and shows,
 * for each, the persona it is currently assigned to and the stage it is in
 * (e.g. "Assigned to Business Analyst", "Assigned to Solution Architect", and
 * — as future roles/segments are added to the registry — those too).
 *
 * Everything here is a pure function of `(records, registry)` so it can be
 * unit-tested without React, Amplify, or the network. Persona labels are
 * resolved through the workflow registry first (single source of truth) and
 * fall back to the known Cognito role display names, then the raw role, so a
 * future owning role that isn't yet in the registry still renders sensibly.
 */

import { getRegistry, type WorkflowRegistry } from '../lib/workflowRegistry';
import type { ProjectRecord } from './projectService';

/**
 * Display names for the six standard Cognito persona groups. Mirrors
 * `COGNITO_GROUPS` in `AuthContext` but is duplicated here intentionally so
 * this module stays pure (no Amplify import chain) and unit-testable.
 */
export const ROLE_DISPLAY: Record<string, string> = {
  'requirements-analyst': 'Business Analyst',
  'system-architect': 'Solution Architect',
  'software-developer': 'Software Developer',
  'qa-engineer': 'QA Engineer',
  'devops-engineer': 'DevOps Engineer',
  'maintenance-specialist': 'Maintenance Specialist',
};

/** All persona roles to surface in the persona summary, in workflow order. */
export const KNOWN_ROLES: readonly string[] = [
  'requirements-analyst',
  'system-architect',
  'software-developer',
  'qa-engineer',
  'devops-engineer',
  'maintenance-specialist',
];

/**
 * Resolve a Cognito group/owning-role into a human display label.
 * Registry first (authoritative), then the known-role map, then the raw value.
 */
export function resolvePersonaLabel(
  role: string | null | undefined,
  registry: WorkflowRegistry = getRegistry(),
): string {
  if (!role) return 'Unassigned';
  try {
    return registry.displayLabel(role);
  } catch {
    // not a registry persona (e.g. a future role) — fall back below
  }
  return ROLE_DISPLAY[role] ?? role;
}

/**
 * Humanize a handoff_state into a stage label. Known states get curated
 * labels; anything else is title-cased from its identifier so new states
 * render readably without a code change.
 */
const STAGE_LABELS: Record<string, string> = {
  draft: 'Draft',
  requirements_ready: 'Requirements ready',
  assigned_to_sa: 'Assigned to Solution Architect',
  in_design: 'In design',
  design_ready: 'Design ready',
  ready_for_dev: 'Ready for development',
  assigned_to_dev: 'Assigned to Software Developer',
  implementation_submitted: 'Implementation submitted',
  assigned_to_qa: 'Assigned to QA Engineer',
  in_qa: 'In QA',
  qa_passed: 'QA passed',
  assigned_to_devops: 'Assigned to DevOps Engineer',
  in_deployment: 'In deployment',
  deployed: 'Deployed',
};

export function stageLabel(state: string | null | undefined): string {
  if (!state) return 'Unknown';
  if (STAGE_LABELS[state]) return STAGE_LABELS[state];
  return state
    .split('_')
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

/** A flattened, presentation-ready row for the projects table. */
export interface DashboardProjectRow {
  projectUuid: string;
  name: string;
  createdBy: string;
  /** Raw handoff state (registry state name). */
  state: string;
  /** Humanized stage label. */
  stage: string;
  /** Current owning Cognito role. */
  ownerRole: string;
  /** Display label for the owning role ("Assigned to {label}"). */
  assignedTo: string;
  /** ISO timestamp of the most recent activity (last history entry or created_at). */
  lastActivity: string;
  /** Number of transitions recorded (history length). */
  transitions: number;
  /** Linked repository URL (C7 Git round-trip), or null when unlinked. */
  repoUrl: string | null;
  /** Ingest status: not_linked | pending | ready | failed. */
  repoStatus: string;
  /** Ingested commit SHA, or null. */
  repoCommit: string | null;
  /** Ingest failure reason, or null. */
  repoError: string | null;
}

/** Return the ISO timestamp of the latest activity for a record. */
export function lastActivityOf(record: ProjectRecord): string {
  const history = record.assignment_history ?? [];
  const last = history.length > 0 ? history[history.length - 1] : null;
  return last?.timestamp ?? record.created_at ?? '';
}

/** Flatten a project record into a dashboard row. */
export function toProjectRow(
  record: ProjectRecord,
  registry: WorkflowRegistry = getRegistry(),
): DashboardProjectRow {
  return {
    projectUuid: record.project_uuid,
    name: record.name,
    createdBy: record.created_by,
    state: record.handoff_state,
    stage: stageLabel(record.handoff_state),
    ownerRole: record.owning_role,
    assignedTo: resolvePersonaLabel(record.owning_role, registry),
    lastActivity: lastActivityOf(record),
    transitions: (record.assignment_history ?? []).length,
    repoUrl: record.repo_url ?? null,
    repoStatus: record.repo_ingest_status ?? 'not_linked',
    repoCommit: record.repo_commit ?? null,
    repoError: record.repo_ingest_error ?? null,
  };
}

/**
 * Build the sorted projects table (most-recently-active first).
 */
export function buildProjectRows(
  records: ProjectRecord[],
  registry: WorkflowRegistry = getRegistry(),
): DashboardProjectRow[] {
  return records
    .map((r) => toProjectRow(r, registry))
    .sort((a, b) => b.lastActivity.localeCompare(a.lastActivity));
}

/** Count of projects currently in each handoff state. */
export function countByState(records: ProjectRecord[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const r of records) {
    const s = r.handoff_state || 'unknown';
    counts[s] = (counts[s] ?? 0) + 1;
  }
  return counts;
}

/** One persona's workload summary line. */
export interface PersonaSummary {
  role: string;
  label: string;
  /** Projects currently owned (assigned to) this persona. */
  owned: number;
}

/**
 * Per-persona workload across all known roles (roles with no projects show 0),
 * plus any owning role present in the data that isn't a known role (defensive).
 * Ordered by {@link KNOWN_ROLES}, then any extras.
 */
export function summarizeByPersona(
  records: ProjectRecord[],
  registry: WorkflowRegistry = getRegistry(),
): PersonaSummary[] {
  const owned: Record<string, number> = {};
  for (const r of records) {
    const role = r.owning_role || 'unassigned';
    owned[role] = (owned[role] ?? 0) + 1;
  }

  const ordered = [...KNOWN_ROLES];
  for (const role of Object.keys(owned)) {
    if (!ordered.includes(role)) ordered.push(role);
  }

  return ordered.map((role) => ({
    role,
    label: resolvePersonaLabel(role, registry),
    owned: owned[role] ?? 0,
  }));
}

/** Top-line totals for the dashboard header. */
export interface DashboardTotals {
  totalProjects: number;
  byState: Record<string, number>;
}

export function summarizeTotals(records: ProjectRecord[]): DashboardTotals {
  return {
    totalProjects: records.length,
    byState: countByState(records),
  };
}
