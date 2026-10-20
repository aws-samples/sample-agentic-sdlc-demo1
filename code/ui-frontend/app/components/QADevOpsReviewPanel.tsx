/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
/**
 * QA / DevOps review panel — the read + forward surface for the QA Engineer
 * and DevOps Engineer personas (six-persona lifecycle).
 *
 * Activated when the signed-in user owns the project's current state and that
 * owning role is `qa-engineer` or `devops-engineer`. It loads the artifacts the
 * persona acts on (QA: test plan / results / report; DevOps: deployment plan /
 * release runbook) from the shared artifact prefix via `s3Service`, and renders
 * a single registry-derived **forward** button that advances the intra-segment
 * handoff state (e.g. `assigned_to_qa → in_qa → qa_passed`,
 * `assigned_to_devops → in_deployment → deployed`).
 *
 * Request-changes bounces (QA→Dev, DevOps→QA) are handled by the existing
 * `ReturnForChangesControl`, so this panel intentionally does not duplicate
 * them. The Project API remains the single writer of handoff state.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AlertCircle, ArrowRightCircle, Loader2, RefreshCw } from 'lucide-react';

import { getFileContent } from '../services/s3Service';
import {
  ProjectApiError,
  permittedActions,
  runTests,
  transition,
  type ProjectRecord,
} from '../services/projectService';
import { getRegistry, type WorkflowRegistry } from '../lib/workflowRegistry';

const QA_GROUP = 'qa-engineer';
const DEVOPS_GROUP = 'devops-engineer';

interface ArtifactSpec {
  label: string;
  relativePath: string;
}

const QA_ARTIFACTS: ReadonlyArray<ArtifactSpec> = [
  { label: 'Test plan', relativePath: 'aidlc-docs/qa/test-plan.md' },
  { label: 'Test results', relativePath: 'aidlc-docs/qa/test-results.md' },
  { label: 'QA report', relativePath: 'aidlc-docs/qa/qa-report.md' },
];

const DEVOPS_ARTIFACTS: ReadonlyArray<ArtifactSpec> = [
  { label: 'Deployment plan', relativePath: 'aidlc-docs/devops/deployment-plan.md' },
  { label: 'Release runbook', relativePath: 'aidlc-docs/devops/release-runbook.md' },
];

interface ArtifactLoadState {
  label: string;
  key: string;
  content: string | null;
  loading: boolean;
  error: string | null;
  /** True when the artifact key does not exist yet (stage not run). */
  notGenerated: boolean;
}

export interface QADevOpsReviewPanelProps {
  record: ProjectRecord;
  userGroups: string[];
  onRecordUpdated: (record: ProjectRecord) => void;
  /** Optional registry override (tests pass an explicit chain). */
  registry?: WorkflowRegistry;
}

function joinS3Key(prefix: string, relative: string): string {
  return `${prefix.replace(/\/+$/, '')}/${relative.replace(/^\/+/, '')}`;
}

/** True when a fetch error means the artifact key does not exist yet. */
function isNotGeneratedError(err: unknown): boolean {
  const raw = err instanceof Error ? err.message : '';
  return (
    (err instanceof Error && err.name === 'NoSuchKey') ||
    /specified key does not exist|no such key|not found/i.test(raw)
  );
}

function ArtifactBody({ artifact }: { artifact: ArtifactLoadState }) {
  if (artifact.loading) {
    return (
      <div className="flex h-40 items-center justify-center" role="status" aria-label={`Loading ${artifact.label}`}>
        <Loader2 className="h-5 w-5 animate-spin text-indigo-500" />
      </div>
    );
  }
  if (artifact.notGenerated) {
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
        {artifact.label} hasn’t been generated yet. Open this project’s chat as the
        owning persona to run the stage that produces it, then use Reload.
      </div>
    );
  }
  if (artifact.error) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
        Failed to load {artifact.label}: {artifact.error}
      </div>
    );
  }
  if (!artifact.content) {
    return (
      <div className="rounded-md border border-gray-200 bg-gray-50 p-3 text-xs text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400">
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

export function QADevOpsReviewPanel({
  record,
  userGroups,
  onRecordUpdated,
  registry,
}: QADevOpsReviewPanelProps) {
  const owningRole = record.owning_role;
  const isQa = userGroups.includes(QA_GROUP) && owningRole === QA_GROUP;
  const isDevops = userGroups.includes(DEVOPS_GROUP) && owningRole === DEVOPS_GROUP;
  const shows = isQa || isDevops;

  const specs = isQa ? QA_ARTIFACTS : DEVOPS_ARTIFACTS;

  const initialArtifacts = useMemo<ArtifactLoadState[]>(
    () =>
      specs.map((spec) => ({
        label: spec.label,
        key: joinS3Key(record.artifact_prefix, spec.relativePath),
        content: null,
        loading: shows,
        error: null,
        notGenerated: false,
      })),
    [specs, record.artifact_prefix, shows],
  );

  const [artifacts, setArtifacts] = useState<ArtifactLoadState[]>(initialArtifacts);
  const [activeKey, setActiveKey] = useState<string>(initialArtifacts[0]?.key ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    setArtifacts(initialArtifacts);
    setActiveKey(initialArtifacts[0]?.key ?? '');
  }, [initialArtifacts]);

  const fetchOne = useCallback(async (key: string) => {
    setArtifacts((prev) => prev.map((a) => (a.key === key ? { ...a, loading: true, error: null, notGenerated: false } : a)));
    try {
      const content = await getFileContent(key);
      setArtifacts((prev) => prev.map((a) => (a.key === key ? { ...a, content, loading: false, error: null, notGenerated: false } : a)));
    } catch (err) {
      const notGenerated = isNotGeneratedError(err);
      const message = err instanceof Error ? err.message : 'Unknown error';
      setArtifacts((prev) => prev.map((a) => (a.key === key ? { ...a, loading: false, notGenerated, error: notGenerated ? null : message } : a)));
    }
  }, []);

  useEffect(() => {
    if (!shows) return;
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
  }, [shows, initialArtifacts, fetchOne]);

  // Registry-derived forward action for the current state. Only INTRA-segment
  // forwards are surfaced here (e.g. assigned_to_qa → in_qa → qa_passed);
  // cross-segment boundaries (e.g. qa_passed → assigned_to_devops) are handled
  // by the handoff card (the gate emits `handoff_required`). Terminal states
  // (e.g. `deployed`) have no forward.
  const forwardAction = useMemo(() => {
    try {
      const reg = registry ?? getRegistry();
      const fwd = permittedActions(record, userGroups, reg).find((a) => a.kind === 'forward');
      if (!fwd) return null;
      if (reg.segmentOf(fwd.target) !== reg.segmentOf(record.handoff_state)) return null;
      return fwd;
    } catch {
      return null;
    }
  }, [record, userGroups, registry]);

  const handleForward = useCallback(async () => {
    if (submitting || !forwardAction) return;
    setSubmitting(true);
    setActionError(null);
    try {
      const updated = await transition(record.project_uuid, { action: forwardAction.action });
      onRecordUpdated(updated);
    } catch (err) {
      setActionError(
        err instanceof ProjectApiError ? err.message
          : err instanceof Error ? err.message
          : 'Transition failed',
      );
    } finally {
      setSubmitting(false);
    }
  }, [submitting, forwardAction, record.project_uuid, onRecordUpdated]);

  const handleRunTests = useCallback(async () => {
    if (submitting) return;
    setSubmitting(true);
    setActionError(null);
    try {
      const updated = await runTests(record.project_uuid);
      onRecordUpdated(updated);
    } catch (err) {
      setActionError(
        err instanceof ProjectApiError ? err.message
          : err instanceof Error ? err.message
          : 'Failed to start tests',
      );
    } finally {
      setSubmitting(false);
    }
  }, [submitting, record.project_uuid, onRecordUpdated]);

  if (!shows) return null;

  const heading = isQa ? 'QA review' : 'Deployment review';

  return (
    <section
      data-testid="qa-devops-review-panel"
      aria-label="QA and DevOps review"
      className="flex flex-col gap-3 rounded-lg border border-indigo-200 bg-indigo-50 p-4 text-sm dark:border-indigo-800 dark:bg-indigo-950"
    >
      <header className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{heading}</h2>
        <span className="rounded-full border border-indigo-300 bg-white px-2 py-0.5 text-xs font-medium text-indigo-700 dark:border-indigo-700 dark:bg-indigo-900 dark:text-indigo-200">
          {record.handoff_state}
        </span>
      </header>

      <div className="flex flex-wrap gap-1 border-b border-indigo-200 dark:border-indigo-800">
        {artifacts.map((a) => {
          const active = a.key === activeKey;
          return (
            <button
              key={a.key}
              type="button"
              onClick={() => setActiveKey(a.key)}
              className={`rounded-t-md px-3 py-1.5 text-xs font-medium transition-colors ${
                active
                  ? 'bg-white text-indigo-700 dark:bg-indigo-900 dark:text-indigo-200'
                  : 'text-gray-600 hover:bg-indigo-100 dark:text-gray-300 dark:hover:bg-indigo-900'
              }`}
              aria-pressed={active}
            >
              {a.label}
              {a.error && <AlertCircle className="ml-1 inline-block h-3 w-3 text-red-500" aria-hidden="true" />}
            </button>
          );
        })}
        <button
          type="button"
          onClick={() => activeKey && fetchOne(activeKey)}
          disabled={submitting}
          className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 dark:text-indigo-200 dark:hover:bg-indigo-900"
          aria-label="Reload artifact"
        >
          <RefreshCw className="h-3 w-3" />
          Reload
        </button>
      </div>

      <div className="max-h-72 overflow-auto rounded-md border border-indigo-200 bg-white p-3 dark:border-indigo-800 dark:bg-gray-900" data-testid="qa-devops-active-artifact">
        {(() => {
          const active = artifacts.find((a) => a.key === activeKey);
          return active ? <ArtifactBody artifact={active} /> : null;
        })()}
      </div>

      {isQa && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-indigo-200 bg-white px-3 py-2 dark:border-indigo-800 dark:bg-gray-900">
          <span className="text-xs text-gray-600 dark:text-gray-300">
            Tests: <strong>{record.qa_test_status ?? 'not_run'}</strong>
            {record.qa_test_summary ? ` — ${record.qa_test_summary}` : ''}
          </span>
          <button
            type="button"
            onClick={handleRunTests}
            disabled={submitting || record.qa_test_status === 'running'}
            className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-indigo-300 px-3 py-1 text-xs font-medium text-indigo-700 transition-colors hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-indigo-700 dark:text-indigo-200 dark:hover:bg-indigo-900"
          >
            {record.qa_test_status === 'running' ? 'Running tests…' : 'Run tests'}
          </button>
        </div>
      )}

      {forwardAction && (
        <button
          type="button"
          onClick={handleForward}
          disabled={submitting}
          className="inline-flex w-fit items-center gap-1.5 rounded-full bg-indigo-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <ArrowRightCircle className="h-3.5 w-3.5" />
          {submitting ? 'Working…' : forwardAction.label}
        </button>
      )}

      {actionError && (
        <div role="alert" className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>{actionError}</span>
        </div>
      )}
    </section>
  );
}
