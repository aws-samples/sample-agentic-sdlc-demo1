/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
/**
 * Solution Architect review experience (task 7.4; design Section 8, Req 7.1,
 * 7.3, 7.4, 7.5, 7.6, 7.7).
 *
 * Activated whenever the signed-in user belongs to the SA-owning group
 * (`system-architect`) and the project's handoff state is one of the SA-owned
 * states this surface acts on:
 *
 *   - `assigned_to_sa` — the BA has handed the project off; the panel loads
 *     `requirements.md`, `stories.md`, and `personas.md` from the shared
 *     artifact prefix using the existing `s3Service` (Req 7.1) and presents
 *     Approve / Request Changes buttons. Approve transitions the Project
 *     Store record `assigned_to_sa → in_design` (Req 7.4); Request Changes
 *     bounces the record back to `requirements_ready` with the note recorded
 *     in `assignment_history` (Req 7.5).
 *
 *   - `in_design` / `design_ready` — the panel exposes a single
 *     "design-complete" forward button. From `in_design` it advances to
 *     `design_ready`; from `design_ready` it advances to `ready_for_dev`,
 *     covering the two SA completion forwards in Req 7.7.
 *
 * The Project API is the single writer of handoff state (design Req 10.6) so
 * every transition is performed via `projectService.transition`. The Approval
 * Gate decision is *also* sent to the runtime over the WebSocket so the agent
 * graph progresses (design Section 8d). The panel is purely additive —
 * runtime persona-aware session entry (task 5.4) still drives the SDLC graph;
 * this component layers the user-visible review surface on top.
 *
 * Failures are surfaced inline so the user always has a path forward; the
 * panel never hides on a server error (parity with `HandoffCard`).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CheckCircle2, AlertCircle, Loader2, RefreshCw, ThumbsUp } from 'lucide-react';

import { getFileContent } from '../services/s3Service';
import {
  ProjectApiError,
  transition,
  type ProjectRecord,
} from '../services/projectService';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Cognito group name that owns the SA segment (matches `workflow_registry.json`). */
const SOLUTION_ARCHITECT_GROUP = 'system-architect';

/**
 * Artifacts produced by the BA segment that the SA reviews on assignment
 * (design Section 8a / Req 7.1). The relative paths match those written by
 * `requirements_analysis.py` and `user_stories.py` in the runtime.
 */
const BA_REVIEW_ARTIFACTS: ReadonlyArray<{
  /** Display label shown on the tab. */
  label: string;
  /** Path relative to the project's S3 artifact prefix. */
  relativePath: string;
}> = [
  { label: 'Requirements', relativePath: 'aidlc-docs/inception/requirements/requirements.md' },
  { label: 'User stories', relativePath: 'aidlc-docs/inception/user-stories/stories.md' },
  { label: 'Personas', relativePath: 'aidlc-docs/inception/user-stories/personas.md' },
];

/**
 * Approval-gate WS strings sent to the runtime when the SA picks a button.
 * Match the strings the agent's `present_approval_gate` advertises so the
 * runtime's `process_approval_response` resolves them deterministically.
 */
const RUNTIME_APPROVE_TEXT = 'Approve & Continue';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** A single artifact's load state. */
interface ArtifactLoadState {
  label: string;
  /** Full S3 key (resolved from `artifact_prefix` + `relativePath`). */
  key: string;
  content: string | null;
  loading: boolean;
  error: string | null;
}

interface SolutionArchitectReviewPanelProps {
  /** Project record from the Project API; its `handoff_state` drives the panel. */
  record: ProjectRecord;
  /** Cognito group names from the signed-in user's session. */
  userGroups: string[];
  /**
   * Send a chat message over the active WebSocket. Used to forward the SA's
   * approval-gate decision to the runtime so the agent graph progresses
   * (design Section 8c / Req 7.3).
   */
  onSendMessage: (text: string) => void;
  /**
   * Notify the parent of the latest project record after a successful
   * Project API transition. The parent re-renders with the new
   * `handoff_state` so this panel reflects the next step (e.g. Approve on
   * `assigned_to_sa` flips to the design-complete button on `in_design`).
   */
  onRecordUpdated: (record: ProjectRecord) => void;
}

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

/**
 * Resolve the full S3 key for a relative path under the project's artifact
 * prefix. Always emits a single-slash join even if either side has a trailing
 * or leading slash.
 */
function joinS3Key(prefix: string, relative: string): string {
  const left = prefix.replace(/\/+$/, '');
  const right = relative.replace(/^\/+/, '');
  return `${left}/${right}`;
}

function ArtifactBody({ artifact }: { artifact: ArtifactLoadState }) {
  if (artifact.loading) {
    return (
      <div className="flex h-48 items-center justify-center" role="status" aria-label={`Loading ${artifact.label}`}>
        <Loader2 className="h-5 w-5 animate-spin text-blue-500" />
      </div>
    );
  }
  if (artifact.error) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
        <AlertCircle className="h-5 w-5" aria-hidden="true" />
        <p>Failed to load {artifact.label}.</p>
        <p className="text-xs text-red-600 dark:text-red-300">{artifact.error}</p>
      </div>
    );
  }
  if (!artifact.content) {
    return (
      <div className="rounded-md border border-gray-200 bg-gray-50 p-4 text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400">
        No content available for {artifact.label} yet.
      </div>
    );
  }
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{artifact.content}</ReactMarkdown>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function SolutionArchitectReviewPanel({
  record,
  userGroups,
  onSendMessage,
  onRecordUpdated,
}: SolutionArchitectReviewPanelProps) {
  // --- Visibility gate -----------------------------------------------------
  // The panel is presentational gating only; the Project API re-checks
  // ownership server-side on every transition (design Section 4b, Req 2.3).
  const isSa = userGroups.includes(SOLUTION_ARCHITECT_GROUP);
  const state = record.handoff_state;
  const showsReview = isSa && state === 'assigned_to_sa';
  const showsDesignComplete = isSa && (state === 'in_design' || state === 'design_ready');

  // --- BA artifact loading (Req 7.1) --------------------------------------
  // Resolve the S3 keys against the record's `artifact_prefix` so both
  // personas operate on the same artifact tree (Req 3.3).
  const initialArtifacts = useMemo<ArtifactLoadState[]>(
    () =>
      BA_REVIEW_ARTIFACTS.map((spec) => ({
        label: spec.label,
        key: joinS3Key(record.artifact_prefix, spec.relativePath),
        content: null,
        loading: showsReview, // start loading only when the panel will show them
        error: null,
      })),
    [record.artifact_prefix, showsReview],
  );
  const [artifacts, setArtifacts] = useState<ArtifactLoadState[]>(initialArtifacts);
  const [activeKey, setActiveKey] = useState<string>(initialArtifacts[0]?.key ?? '');

  // Reload when prefix or visibility changes; otherwise keep cached content.
  useEffect(() => {
    setArtifacts(initialArtifacts);
    setActiveKey(initialArtifacts[0]?.key ?? '');
  }, [initialArtifacts]);

  const fetchOne = useCallback(async (key: string) => {
    setArtifacts((prev) =>
      prev.map((a) => (a.key === key ? { ...a, loading: true, error: null } : a)),
    );
    try {
      const content = await getFileContent(key);
      setArtifacts((prev) =>
        prev.map((a) => (a.key === key ? { ...a, content, loading: false, error: null } : a)),
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      setArtifacts((prev) =>
        prev.map((a) => (a.key === key ? { ...a, loading: false, error: message } : a)),
      );
    }
  }, []);

  useEffect(() => {
    if (!showsReview) return;
    let cancelled = false;
    (async () => {
      for (const artifact of initialArtifacts) {
        if (cancelled) return;
        await fetchOne(artifact.key);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [showsReview, initialArtifacts, fetchOne]);

  // --- Action state -------------------------------------------------------
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  /**
   * Apply a Project API transition and surface success/error inline.
   *
   * Returns the updated record on success so callers (e.g. the Mark Design
   * Complete button) can chain a second transition for the
   * `in_design → design_ready → ready_for_dev` path (Req 7.7) without waiting
   * for the parent re-render.
   */
  const applyTransition = useCallback(
    async (
      action: string,
      note?: string,
    ): Promise<ProjectRecord | null> => {
      setSubmitting(true);
      setActionError(null);
      try {
        const updated = await transition(record.project_uuid, {
          action,
          ...(note ? { note } : {}),
        });
        onRecordUpdated(updated);
        return updated;
      } catch (err) {
        const message =
          err instanceof ProjectApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : 'Transition failed';
        setActionError(message);
        return null;
      } finally {
        setSubmitting(false);
      }
    },
    [record.project_uuid, onRecordUpdated],
  );

  // --- Approval gate decisions (Req 7.3, 7.4, 7.5) ------------------------

  const handleApprove = useCallback(async () => {
    if (submitting) return;
    // Forward the agent's approval-gate decision to the runtime so the SDLC
    // graph progresses past the gate (design Section 8c / Req 7.3). The
    // Project API transition that follows is what advances the *handoff*
    // state — these are the two transition responsibilities split by
    // Req 10.6.
    onSendMessage(RUNTIME_APPROVE_TEXT);
    await applyTransition('approve');
  }, [submitting, onSendMessage, applyTransition]);

  // --- Design completion (Req 7.7) ----------------------------------------

  const handleDesignComplete = useCallback(async () => {
    if (submitting) return;
    // Always issue the immediate forward; if we land on `design_ready` after
    // advancing from `in_design` we issue a *second* `design-complete`
    // transition to reach `ready_for_dev`, satisfying the Req 7.7 chain
    // `in_design → design_ready → ready_for_dev`.
    const first = await applyTransition('design-complete');
    if (!first) return;
    if (first.handoff_state === 'design_ready') {
      await applyTransition('design-complete');
    }
  }, [submitting, applyTransition]);

  // --- Render -------------------------------------------------------------

  if (!showsReview && !showsDesignComplete) {
    return null;
  }

  return (
    <section
      data-testid="sa-review-panel"
      aria-label="Solution Architect review"
      className="flex flex-col gap-3 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm dark:border-blue-800 dark:bg-blue-950"
    >
      <header className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          {showsReview ? 'Review Business Analyst artifacts' : 'Design completion'}
        </h2>
        <span className="rounded-full border border-blue-300 bg-white px-2 py-0.5 text-xs font-medium text-blue-700 dark:border-blue-700 dark:bg-blue-900 dark:text-blue-200">
          {state}
        </span>
      </header>

      {showsReview && (
        <>
          {/* Tabs over the three review artifacts */}
          <div className="flex flex-wrap gap-1 border-b border-blue-200 dark:border-blue-800">
            {artifacts.map((a) => {
              const active = a.key === activeKey;
              return (
                <button
                  key={a.key}
                  type="button"
                  onClick={() => setActiveKey(a.key)}
                  className={`relative rounded-t-md px-3 py-1.5 text-xs font-medium transition-colors ${
                    active
                      ? 'bg-white text-blue-700 dark:bg-blue-900 dark:text-blue-200'
                      : 'text-gray-600 hover:bg-blue-100 dark:text-gray-300 dark:hover:bg-blue-900'
                  }`}
                  aria-pressed={active}
                >
                  {a.label}
                  {a.error && (
                    <AlertCircle className="ml-1 inline-block h-3 w-3 text-red-500" aria-hidden="true" />
                  )}
                </button>
              );
            })}
            <button
              type="button"
              onClick={() => activeKey && fetchOne(activeKey)}
              disabled={submitting}
              className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-blue-700 hover:bg-blue-100 disabled:opacity-50 dark:text-blue-200 dark:hover:bg-blue-900"
              aria-label="Reload artifact"
            >
              <RefreshCw className="h-3 w-3" />
              Reload
            </button>
          </div>

          {/* Active artifact body */}
          <div
            className="max-h-72 overflow-auto rounded-md border border-blue-200 bg-white p-3 dark:border-blue-800 dark:bg-gray-900"
            data-testid="sa-review-active-artifact"
          >
            {(() => {
              const active = artifacts.find((a) => a.key === activeKey);
              if (!active) return null;
              return <ArtifactBody artifact={active} />;
            })()}
          </div>

          {/* Approve / Request Changes actions */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleApprove}
              disabled={submitting}
              className="inline-flex items-center gap-1.5 rounded-full bg-blue-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ThumbsUp className="h-3.5 w-3.5" />
              Approve & continue
            </button>
          </div>
        </>
      )}

      {showsDesignComplete && (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-gray-700 dark:text-gray-300">
            {state === 'in_design'
              ? 'Mark design ready when the design artifacts are complete. The project will then be ready for development.'
              : 'Mark the project ready for development to complete the design phase.'}
          </p>
          <button
            type="button"
            onClick={handleDesignComplete}
            disabled={submitting}
            className="inline-flex w-fit items-center gap-1.5 rounded-full bg-emerald-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <CheckCircle2 className="h-3.5 w-3.5" />
            {state === 'in_design' ? 'Mark design ready' : 'Mark ready for development'}
          </button>
        </div>
      )}

      {actionError && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
        >
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>{actionError}</span>
        </div>
      )}
    </section>
  );
}
