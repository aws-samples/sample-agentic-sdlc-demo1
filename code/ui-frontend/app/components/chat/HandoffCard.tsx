/**
 * Handoff card — actionable in-app card rendered when the runtime emits a
 * `handoff_required` WebSocket message at a Handoff_Gate (design Sections
 * 5b / 9, Req 6.3, 6.4).
 *
 * The card label is derived from the registry's `displayLabel(to_role)` so
 * "Assign to {next role display}" generalizes to any persona boundary, not
 * just BA → SA (design Section 9, Req 14.6 / 14.2). On click, the card calls
 * `projectService.transition(uuid, { action: "assign" })` to advance the
 * project record to the next owning persona; the Project API re-checks
 * ownership server-side (design Section 4b, Req 6.4).
 *
 * The card is rendered defensively: if the registry lookup fails for any
 * reason (unknown role, missing config), the card still renders with a
 * sensible default label so the user always has a path forward.
 */

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { ArrowRightCircle } from "lucide-react";

import type { ChatMessage } from "../../types/ws-messages";
import {
  getProject,
  transition,
  ProjectApiError,
} from "../../services/projectService";
import { getRegistry } from "../../lib/workflowRegistry";

interface HandoffCardProps {
  message: ChatMessage;
}

/**
 * Resolve the display label for a Cognito group name through the registry.
 * Falls back to the raw role on any registry failure so the button always
 * carries a usable label.
 */
function resolveDisplayLabel(role: string | undefined): string {
  if (!role) return "next role";
  try {
    return getRegistry().displayLabel(role);
  } catch {
    return role;
  }
}

/**
 * Safety cap on the number of forward transitions a single handoff click may
 * apply. The configured BA segment spans two BA-owned states
 * (`draft` -> `requirements_ready` -> `assigned_to_sa`), so two forward steps
 * are needed to reach the SA from a fresh `draft` project. The cap protects
 * against a misconfigured registry where `forward` never reaches `toRole`.
 */
const MAX_HANDOFF_STEPS = 6;

export function HandoffCard({ message }: HandoffCardProps) {
  const [resolved, setResolved] = useState(message.resolved ?? false); // nosemgrep: react-props-in-state — initial value from prop, updates handled via state
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const projectUuid = message.metadata?.project_uuid ?? "";
  const fromRole = message.metadata?.from_role;
  const toRole = message.metadata?.to_role;
  const summary = message.metadata?.summary || message.content || "";

  const toRoleLabel = resolveDisplayLabel(toRole);
  const buttonLabel = `Assign to ${toRoleLabel}`;

  const fromRoleLabel = resolveDisplayLabel(fromRole);
  const headingLabel = `Handoff: ${fromRoleLabel} → ${toRoleLabel}`;

  const handleClick = async () => {
    if (resolved || submitting || !projectUuid) return;
    setSubmitting(true);
    setError(null);
    try {
      // Advance the project to the *next owning persona* (design Section 9 —
      // the card's job is to transfer ownership across the segment boundary,
      // not to take a single fixed step). A freshly-created project sits at
      // `draft`, and nothing else advances it; from `draft` a single forward
      // step only reaches `requirements_ready` (still BA-owned), so the SA
      // would never receive the project. We therefore step forward through
      // the registry until ownership lands on `toRole`.
      //
      // Each step is a distinct, registry-validated Project API transition.
      // The caller (BA) owns every intermediate BA-segment state, so the
      // server-side owner gate authorizes each step until ownership flips to
      // the SA, at which point the loop stops.
      if (toRole) {
        const registry = getRegistry();
        let record = await getProject(projectUuid);
        let steps = 0;
        while (record.owning_role !== toRole && steps < MAX_HANDOFF_STEPS) {
          const next = registry.forward(record.handoff_state);
          if (!next) break; // terminal state; nothing further to advance
          record = await transition(projectUuid, {
            action: "assign",
            target_state: next,
          });
          steps += 1;
        }
        if (record.owning_role !== toRole) {
          throw new ProjectApiError(
            0,
            `Could not advance the project to ${toRoleLabel}.`,
            "HANDOFF_INCOMPLETE",
          );
        }
      } else {
        // No target role context (registry resolution failed upstream) —
        // fall back to a single forward step so the user still has a path
        // forward, matching the card's documented defensive behavior.
        await transition(projectUuid, { action: "assign" });
      }
      setResolved(true);
    } catch (err) {
      const apiErr = err instanceof ProjectApiError ? err : null;
      setError(
        apiErr
          ? `Assignment failed: ${apiErr.message}`
          : "Assignment failed. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-950">
      <div className="mb-2 flex items-center gap-2">
        <ArrowRightCircle className="h-4 w-4 text-amber-700 dark:text-amber-300" />
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          {headingLabel}
        </h3>
      </div>
      {summary && (
        <div className="prose prose-sm dark:prose-invert mb-3 max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
            {summary}
          </ReactMarkdown>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={resolved || submitting || !projectUuid}
          onClick={handleClick}
          className="rounded-full bg-amber-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Assigning…" : resolved ? "Assigned" : buttonLabel}
        </button>
        {error && (
          <span className="text-xs text-red-700 dark:text-red-300">{error}</span>
        )}
      </div>
    </div>
  );
}
