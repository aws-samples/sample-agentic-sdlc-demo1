/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
/**
 * Change-request banner — surfaces the most recent "request changes" note to
 * the persona who now owns the bounced-back project (e.g. the BA after the SA
 * sends the requirements back).
 *
 * The note itself lives only on the Project Store handoff record
 * (`assignment_history[*].note`); without this banner it is never shown in the
 * workspace. Detection is registry-driven (same positional logic as the status
 * badge), so it covers any persona chain. Rendered only for the current owning
 * role — the person expected to act on the feedback. Dismissible per session.
 */
import { useState } from 'react';
import { AlertCircle, X } from 'lucide-react';

import { latestChangeRequest } from './HandoffStatusBadge';
import { getRegistry, type WorkflowRegistry } from '../lib/workflowRegistry';
import type { ProjectRecord } from '../services/projectService';

export interface ChangeRequestBannerProps {
  record: Pick<ProjectRecord, 'handoff_state' | 'owning_role' | 'assignment_history'>;
  /** Cognito group names from the signed-in user's session. */
  userGroups: string[];
  /** Optional registry override (tests pass an explicit chain). */
  registry?: WorkflowRegistry;
}

export function ChangeRequestBanner({ record, userGroups, registry }: ChangeRequestBannerProps) {
  const [dismissed, setDismissed] = useState(false);

  let info: ReturnType<typeof latestChangeRequest> = null;
  try {
    info = latestChangeRequest(record, registry ?? getRegistry());
  } catch {
    // Registry not loaded or lookup failed — never block the workspace.
    return null;
  }

  // Show only to the persona who now owns the bounced-back work.
  if (!info || dismissed || !userGroups.includes(record.owning_role)) return null;

  return (
    <div
      data-testid="change-request-banner"
      role="status"
      className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">{info.label}</p>
        {info.note ? (
          <p className="mt-0.5 whitespace-pre-wrap break-words">{info.note}</p>
        ) : (
          <p className="mt-0.5 italic opacity-80">No note was provided.</p>
        )}
        <p className="mt-1 text-xs opacity-70">Requested by {info.actor}</p>
      </div>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        aria-label="Dismiss change request"
        className="rounded p-0.5 text-amber-600 transition-colors hover:bg-amber-100 dark:text-amber-300 dark:hover:bg-amber-900"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
