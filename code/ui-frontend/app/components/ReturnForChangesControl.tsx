/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
/**
 * Return-for-changes control — the persona **handoff bounce** (cross-persona),
 * distinct from the agent's in-chat "Request Changes" approval gate (which is
 * in-stage iteration that keeps the project with the current persona).
 *
 * Registry-driven and generic: it appears whenever the signed-in user owns a
 * state that has a `request_changes` edge (SA at `assigned_to_sa` → BA, or
 * Developer at `assigned_to_dev` → SA). It performs a **clean** Project API
 * bounce with an optional note and does NOT send the agent the "Request
 * Changes" text — so the bounced-back persona's session is re-opened (handled
 * runtime-side) without dragging the current persona into the agent's
 * regenerate-questions loop.
 */
import { useState, useCallback } from 'react';
import { AlertCircle, CornerUpLeft } from 'lucide-react';

import {
  ProjectApiError,
  transition,
  type ProjectRecord,
} from '../services/projectService';
import { getRegistry, type WorkflowRegistry } from '../lib/workflowRegistry';

export interface ReturnForChangesControlProps {
  record: ProjectRecord;
  /** Cognito group names from the signed-in user's session. */
  userGroups: string[];
  onRecordUpdated: (record: ProjectRecord) => void;
  /** Optional registry override (tests pass an explicit chain). */
  registry?: WorkflowRegistry;
}

export function ReturnForChangesControl({
  record,
  userGroups,
  onRecordUpdated,
  registry,
}: ReturnForChangesControlProps) {
  const [showNote, setShowNote] = useState(false);
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Resolve visibility + label WITHOUT early-returning before the hooks below
  // (Rules of Hooks: every hook must run on every render). The conditional
  // return happens after `useCallback`.
  let targetLabel = '';
  let canReturn = false;
  try {
    const reg = registry ?? getRegistry();
    const rc = reg.requestChangesInto(record.handoff_state);
    // Only the current owner may return the project, and only when the
    // current state actually has a request-changes edge. This recomputes on
    // every render, so a re-assigned owner sees the control again (multi-bounce
    // is allowed).
    if (rc && userGroups.includes(record.owning_role)) {
      targetLabel = reg.displayLabel(reg.owningRole(rc.target));
      canReturn = true;
    }
  } catch {
    canReturn = false;
  }

  const submit = useCallback(async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const trimmed = note.trim();
      // Clean handoff bounce — Project API only. No WebSocket "Request Changes"
      // text, so the current persona's agent session is NOT pushed into the
      // in-stage regenerate loop.
      const updated = await transition(record.project_uuid, {
        action: 'request-changes',
        ...(trimmed ? { note: trimmed } : {}),
      });
      onRecordUpdated(updated);
      setNote('');
      setShowNote(false);
    } catch (err) {
      setError(
        err instanceof ProjectApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : 'Failed to return the project',
      );
    } finally {
      setSubmitting(false);
    }
  }, [submitting, note, record.project_uuid, onRecordUpdated]);

  // Conditional render AFTER all hooks have run.
  if (!canReturn) return null;

  return (
    <section
      data-testid="return-for-changes"
      aria-label="Return for changes"
      className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950"
    >
      {!showNote ? (
        <button
          type="button"
          onClick={() => setShowNote(true)}
          className="inline-flex items-center gap-1.5 self-start rounded-full border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-700 transition-colors hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-900 dark:text-amber-200"
        >
          <CornerUpLeft className="h-3.5 w-3.5" />
          Return to {targetLabel} for changes
        </button>
      ) : (
        <div className="flex flex-col gap-2">
          <label
            htmlFor="return-changes-note"
            className="text-xs font-medium text-amber-800 dark:text-amber-200"
          >
            Note for the {targetLabel} (optional)
          </label>
          <textarea
            id="return-changes-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            placeholder="What needs to change?"
            className="rounded-md border border-amber-300 bg-white px-2 py-1 text-xs text-gray-900 dark:border-amber-700 dark:bg-gray-800 dark:text-gray-100"
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={submit}
              disabled={submitting}
              className="inline-flex items-center gap-1.5 rounded-full bg-amber-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <CornerUpLeft className="h-3.5 w-3.5" />
              {submitting ? 'Returning…' : `Return to ${targetLabel}`}
            </button>
            <button
              type="button"
              onClick={() => {
                setShowNote(false);
                setNote('');
                setError(null);
              }}
              disabled={submitting}
              className="rounded-full border border-gray-300 bg-white px-4 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && (
        <p className="flex items-center gap-1 text-xs text-red-600 dark:text-red-400">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      )}
    </section>
  );
}
