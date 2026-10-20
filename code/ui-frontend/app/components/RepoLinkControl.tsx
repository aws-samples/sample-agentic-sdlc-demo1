/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
/**
 * Repo link + submit control — the Software Developer's C7 Git round-trip
 * action.
 *
 * Registry-driven: it appears for the owning developer when the current
 * state's forward successor is `implementation_submitted` (i.e. at
 * `assigned_to_dev`). After building from the construction brief in an external
 * IDE (e.g. Kiro) and pushing to GitHub/GitLab, the developer pastes the repo
 * URL; on submit the control (1) links the repo via the Project API (which
 * validates the URL against a host allowlist and owner-gates the caller) and
 * (2) advances the project to `implementation_submitted`, which triggers
 * server-side ingestion (clone → sync → QA).
 */
import { useState, useCallback } from 'react';
import { AlertCircle, GitBranch } from 'lucide-react';

import {
  ProjectApiError,
  linkRepo,
  transition,
  type ProjectRecord,
} from '../services/projectService';
import { getRegistry, type WorkflowRegistry } from '../lib/workflowRegistry';

/**
 * The handoff state a project enters when the developer submits the linked
 * implementation (C7). Mirrors the Project API's IMPLEMENTATION_SUBMITTED_STATE
 * so the control appears exactly at the pre-submit boundary regardless of what
 * that boundary state is named upstream.
 */
const SUBMIT_TARGET_STATE = 'implementation_submitted';

export interface RepoLinkControlProps {
  record: ProjectRecord;
  /** Cognito group names from the signed-in user's session. */
  userGroups: string[];
  onRecordUpdated: (record: ProjectRecord) => void;
  /** Optional registry override (tests pass an explicit chain). */
  registry?: WorkflowRegistry;
}

/** Light client-side check for quick feedback; the server is authoritative. */
function looksLikeSupportedRepoUrl(url: string): boolean {
  return /^https:\/\/(www\.)?(github|gitlab)\.com\/[^/\s]+\/[^/\s]+/.test(url.trim());
}

export function RepoLinkControl({
  record,
  userGroups,
  onRecordUpdated,
  registry,
}: RepoLinkControlProps) {
  const [repoUrl, setRepoUrl] = useState('');
  const [branch, setBranch] = useState('');
  const [gitToken, setGitToken] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Compute visibility WITHOUT early-returning before the hooks below
  // (Rules of Hooks). The conditional return happens after `useCallback`.
  let canSubmit = false;
  try {
    const reg = registry ?? getRegistry();
    if (
      userGroups.includes(record.owning_role) &&
      reg.forward(record.handoff_state) === SUBMIT_TARGET_STATE
    ) {
      canSubmit = true;
    }
  } catch {
    canSubmit = false;
  }

  const submit = useCallback(async () => {
    if (submitting) return;
    const url = repoUrl.trim();
    if (!looksLikeSupportedRepoUrl(url)) {
      setError('Enter an https URL to a GitHub or GitLab repository (owner/repo).');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      // 1) Link the repo (Project API validates host allowlist + owner gate).
      await linkRepo(record.project_uuid, {
        repo_url: url,
        ...(branch.trim() ? { branch: branch.trim() } : {}),
        ...(gitToken.trim() ? { git_token: gitToken.trim() } : {}),
      });
      // 2) Submit — advance into implementation_submitted, which triggers
      //    server-side ingestion. The submit transition is gated on the repo
      //    being linked, which step (1) just satisfied.
      const updated = await transition(record.project_uuid, { action: 'submit' });
      onRecordUpdated(updated);
    } catch (err) {
      setError(
        err instanceof ProjectApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : 'Failed to submit the implementation',
      );
    } finally {
      setSubmitting(false);
    }
  }, [submitting, repoUrl, branch, gitToken, record.project_uuid, onRecordUpdated]);

  if (!canSubmit) return null;

  return (
    <section
      data-testid="repo-link-control"
      aria-label="Submit implementation"
      className="flex flex-col gap-2 rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm dark:border-sky-800 dark:bg-sky-950"
    >
      <div className="flex items-center gap-1.5 text-xs font-medium text-sky-800 dark:text-sky-200">
        <GitBranch className="h-3.5 w-3.5" />
        Submit implementation for QA
      </div>
      <label htmlFor="repo-url" className="text-xs text-sky-800 dark:text-sky-200">
        Repository URL (GitHub or GitLab)
      </label>
      <input
        id="repo-url"
        type="url"
        value={repoUrl}
        onChange={(e) => setRepoUrl(e.target.value)}
        placeholder="https://github.com/your-org/your-repo"
        className="rounded-md border border-sky-300 bg-white px-2 py-1 text-xs text-gray-900 dark:border-sky-700 dark:bg-gray-800 dark:text-gray-100"
      />
      <label htmlFor="repo-branch" className="text-xs text-sky-800 dark:text-sky-200">
        Branch (optional, defaults to main)
      </label>
      <input
        id="repo-branch"
        type="text"
        value={branch}
        onChange={(e) => setBranch(e.target.value)}
        placeholder="main"
        className="rounded-md border border-sky-300 bg-white px-2 py-1 text-xs text-gray-900 dark:border-sky-700 dark:bg-gray-800 dark:text-gray-100"
      />
      <label htmlFor="repo-token" className="text-xs text-sky-800 dark:text-sky-200">
        Access token (private repos only, optional)
      </label>
      <input
        id="repo-token"
        type="password"
        autoComplete="off"
        value={gitToken}
        onChange={(e) => setGitToken(e.target.value)}
        placeholder="ghp_… / glpat-… (stored securely, never shown again)"
        className="rounded-md border border-sky-300 bg-white px-2 py-1 text-xs text-gray-900 dark:border-sky-700 dark:bg-gray-800 dark:text-gray-100"
      />
      <button
        type="button"
        onClick={submit}
        disabled={submitting}
        className="inline-flex items-center gap-1.5 self-start rounded-full bg-sky-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <GitBranch className="h-3.5 w-3.5" />
        {submitting ? 'Submitting…' : 'Link repo & submit for QA'}
      </button>
      {error && (
        <p className="flex items-center gap-1 text-xs text-red-600 dark:text-red-400">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      )}
    </section>
  );
}
