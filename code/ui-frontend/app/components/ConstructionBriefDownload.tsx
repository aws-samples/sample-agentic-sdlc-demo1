/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
/**
 * Construction-brief download — Developer convenience control.
 *
 * Shows for the owning Software Developer and downloads the consolidated
 * construction brief (`aidlc-docs/construction/construction-brief.md`) produced
 * by the `construction_handoff` agent, so the developer can drop it straight
 * into an AI-assisted IDE (Kiro / Claude Code / Cursor) for the coding phase.
 * Reads via the existing `s3Service` and triggers a client-side file download.
 */
import { useCallback, useState } from 'react';
import { Download, AlertCircle } from 'lucide-react';

import { getFileContent } from '../services/s3Service';
import type { ProjectRecord } from '../services/projectService';

const DEVELOPER_GROUP = 'software-developer';
const BRIEF_RELATIVE_PATH = 'aidlc-docs/construction/construction-brief.md';
const BRIEF_FILENAME = 'construction-brief.md';

export interface ConstructionBriefDownloadProps {
  record: ProjectRecord;
  /** Cognito group names from the signed-in user's session. */
  userGroups: string[];
}

function joinS3Key(prefix: string, relative: string): string {
  return `${prefix.replace(/\/+$/, '')}/${relative.replace(/^\/+/, '')}`;
}

export function ConstructionBriefDownload({
  record,
  userGroups,
}: ConstructionBriefDownloadProps) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Presentational gate: the developer who currently owns the project.
  const isDeveloper =
    userGroups.includes(record.owning_role) && record.owning_role === DEVELOPER_GROUP;

  const handleDownload = useCallback(async () => {
    if (downloading) return;
    setDownloading(true);
    setError(null);
    try {
      const key = joinS3Key(record.artifact_prefix, BRIEF_RELATIVE_PATH);
      const content = await getFileContent(key);
      const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = BRIEF_FILENAME;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
    } catch (err) {
      const raw = err instanceof Error ? err.message : '';
      const notGenerated =
        (err instanceof Error && err.name === 'NoSuchKey') ||
        /specified key does not exist|no such key|not found/i.test(raw);
      setError(
        notGenerated
          ? 'The construction brief has not been generated yet. Open this project’s chat as the Software Developer to generate it, then try downloading again.'
          : raw || 'Failed to download the construction brief',
      );
    } finally {
      setDownloading(false);
    }
  }, [downloading, record.artifact_prefix]);

  if (!isDeveloper) return null;

  return (
    <section
      data-testid="construction-brief-download"
      aria-label="Download construction brief"
      className="flex flex-col gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm dark:border-emerald-800 dark:bg-emerald-950"
    >
      <p className="text-xs text-emerald-800 dark:text-emerald-200">
        Download your construction brief and open it in your AI-assisted IDE
        (Kiro / Claude Code / Cursor) to begin coding. The brief is generated the
        first time you open this project's chat as the Software Developer.
      </p>
      <button
        type="button"
        onClick={handleDownload}
        disabled={downloading}
        className="inline-flex w-fit items-center gap-1.5 rounded-full bg-emerald-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Download className="h-3.5 w-3.5" />
        {downloading ? 'Preparing…' : 'Download construction brief'}
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
