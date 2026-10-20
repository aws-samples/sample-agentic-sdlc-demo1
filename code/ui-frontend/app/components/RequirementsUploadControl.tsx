/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
/**
 * RequirementsUploadControl — Business Analyst requirements document upload.
 *
 * Renders a small upload control in the BA project workspace that accepts
 * Markdown / plain text / PDF / DOCX requirements documents (the
 * `Supported_Upload_Format` set, design Section 7a / Req 5.1, 5.4) and:
 *
 *   1. Validates the file's format **client-side** by file extension. Any
 *      unsupported format is rejected inline with a format-specific error
 *      naming the rejected extension and the allowed set (Req 5.4).
 *   2. On accept, asks the Project API for a short-lived presigned PUT URL
 *      (`POST /projects/{projectId}/upload-url`) and PUTs the file body
 *      directly to that URL (Req 5.1). This keeps the browser's standing S3
 *      permissions read-only — the presigned URL is the only write path
 *      (design Section 7a).
 *
 * Visibility/RBAC is enforced at the call site (only mounted in the BA
 * workspace) and reaffirmed server-side by the Project API, which 403s
 * upload-url requests from non-owning roles.
 */

import { useCallback, useRef, useState } from 'react';
import { Upload, Loader2, AlertCircle, CheckCircle2, X } from 'lucide-react';
import { Button } from './ui/button';
import { useAuth } from '../context/AuthContext';
import { loadConfig } from '../lib/config';

// ---- Supported formats -----------------------------------------------------

/**
 * The Supported_Upload_Format set from Req 5.4: Markdown / plain text / PDF /
 * DOCX. Extensions are stored lower-case with the leading dot so the validator
 * can compare without further normalization.
 */
export const SUPPORTED_UPLOAD_EXTENSIONS = ['.md', '.txt', '.pdf', '.docx'] as const;

export type SupportedUploadExtension = (typeof SUPPORTED_UPLOAD_EXTENSIONS)[number];

const ACCEPT_ATTRIBUTE = SUPPORTED_UPLOAD_EXTENSIONS.join(',');

// ---- Validation result -----------------------------------------------------

interface ValidationOk {
  ok: true;
  extension: SupportedUploadExtension;
}

interface ValidationFail {
  ok: false;
  /** Format-specific error message (Req 5.4): names the rejected extension. */
  error: string;
}

/**
 * Validate `filename` against {@link SUPPORTED_UPLOAD_EXTENSIONS}.
 *
 * Comparison is case-insensitive and uses only the trailing extension; this is
 * the same signal browsers use for the `accept` attribute and is the most
 * reliable cross-platform indicator of format. The MIME type provided by the
 * browser is intentionally not used as the primary check because browsers
 * disagree (e.g. `.md` is variously `text/markdown`, `text/plain`, or `''`).
 *
 * Returns a discriminated union so callers get either the matched extension or
 * a format-specific error message naming both the rejected and allowed formats.
 *
 * Exported for direct unit testing.
 */
export function validateUploadFilename(filename: string): ValidationOk | ValidationFail {
  const lower = filename.toLowerCase();
  const dot = lower.lastIndexOf('.');
  const ext = dot >= 0 ? lower.slice(dot) : '';

  for (const allowed of SUPPORTED_UPLOAD_EXTENSIONS) {
    if (ext === allowed) {
      return { ok: true, extension: allowed };
    }
  }

  const rejected = ext === '' ? '(no extension)' : ext;
  return {
    ok: false,
    error:
      `Unsupported file format: ${rejected}. ` +
      `Allowed formats: ${SUPPORTED_UPLOAD_EXTENSIONS.join(', ')}.`,
  };
}

// ---- Component -------------------------------------------------------------

type UploadState =
  | { kind: 'idle' }
  | { kind: 'selected'; file: File }
  | { kind: 'uploading'; file: File }
  | { kind: 'success'; filename: string }
  | { kind: 'error'; message: string };

interface RequirementsUploadControlProps {
  /** Project UUID (the URL-route `:projectId`, matches the Project API). */
  projectId: string;
  /**
   * Optional callback invoked after a successful PUT. Receives the S3 key the
   * file was uploaded to (returned by the presigned-URL response).
   */
  onUploaded?: (key: string) => void;
}

async function getProjectApiBaseUrl(): Promise<string> {
  const config = await loadConfig();
  return (config.projectApiUrl ?? '').replace(/\/$/, '');
}

export function RequirementsUploadControl({
  projectId,
  onUploaded,
}: RequirementsUploadControlProps) {
  const { idToken } = useAuth();
  const inputRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<UploadState>({ kind: 'idle' });

  const resetInput = useCallback(() => {
    if (inputRef.current) inputRef.current.value = '';
  }, []);

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0] ?? null;
      if (!file) {
        setState({ kind: 'idle' });
        return;
      }

      const validation = validateUploadFilename(file.name);
      if (!validation.ok) {
        setState({ kind: 'error', message: validation.error });
        resetInput();
        return;
      }

      setState({ kind: 'selected', file });
    },
    [resetInput],
  );

  const handleUpload = useCallback(async () => {
    if (state.kind !== 'selected') return;
    const { file } = state;

    setState({ kind: 'uploading', file });

    try {
      const apiUrl = await getProjectApiBaseUrl();
      if (!apiUrl) {
        throw new Error('Project API URL is not configured');
      }

      // 1) Request a presigned PUT URL from the Project API. The endpoint is
      //    owner-gated server-side so a non-owning caller cannot mint a write
      //    URL even if the control were rendered for them (Req 2.3, 5.1).
      const presignRes = await fetch(`${apiUrl}/projects/${projectId}/upload-url`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(idToken ? { Authorization: `Bearer ${idToken}` } : {}),
        },
        body: JSON.stringify({ filename: file.name }),
      });

      if (!presignRes.ok) {
        const data = await presignRes.json().catch(() => ({}));
        throw new Error(
          (data as { error?: string }).error ??
            `Failed to obtain upload URL (HTTP ${presignRes.status})`,
        );
      }

      const { upload_url: uploadUrl, key } = (await presignRes.json()) as {
        upload_url: string;
        key: string;
      };

      // 2) PUT the file body directly to the presigned URL (design Section
      //    7a). The Authorization header is intentionally NOT forwarded — the
      //    presigned URL already carries its own SigV4 query-string credential.
      const putRes = await fetch(uploadUrl, {
        method: 'PUT',
        body: file,
      });

      if (!putRes.ok) {
        throw new Error(`Upload failed (HTTP ${putRes.status})`);
      }

      setState({ kind: 'success', filename: file.name });
      resetInput();
      onUploaded?.(key);
    } catch (err) {
      setState({
        kind: 'error',
        message: err instanceof Error ? err.message : 'Upload failed',
      });
      resetInput();
    }
  }, [state, projectId, idToken, onUploaded, resetInput]);

  const handleClear = useCallback(() => {
    setState({ kind: 'idle' });
    resetInput();
  }, [resetInput]);

  const isUploading = state.kind === 'uploading';

  return (
    <div className="rounded-md border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-900">
      <div className="mb-2 flex items-center gap-1.5 text-sm font-medium text-gray-900 dark:text-gray-100">
        <Upload className="h-4 w-4 text-gray-500" aria-hidden="true" />
        Upload requirements document
      </div>
      <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">
        Allowed: {SUPPORTED_UPLOAD_EXTENSIONS.join(', ')}
      </p>

      <label className="block">
        <span className="sr-only">Choose a requirements document to upload</span>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_ATTRIBUTE}
          onChange={handleFileChange}
          disabled={isUploading}
          aria-label="Choose a requirements document to upload"
          className="block w-full text-xs text-gray-700 file:mr-2 file:rounded-md file:border-0 file:bg-blue-50 file:px-2 file:py-1 file:text-xs file:font-medium file:text-blue-700 hover:file:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-300 dark:file:bg-blue-900/40 dark:file:text-blue-200"
        />
      </label>

      {state.kind === 'selected' && (
        <div className="mt-2 flex items-center justify-between gap-2 text-xs text-gray-600 dark:text-gray-300">
          <span className="truncate" title={state.file.name}>
            {state.file.name}
          </span>
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              size="sm"
              onClick={handleUpload}
              disabled={isUploading || !idToken}
            >
              Upload
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleClear}
              aria-label="Clear selected file"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}

      {state.kind === 'uploading' && (
        <p
          role="status"
          aria-live="polite"
          className="mt-2 flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-300"
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Uploading {state.file.name}…
        </p>
      )}

      {state.kind === 'success' && (
        <p
          role="status"
          aria-live="polite"
          className="mt-2 flex items-center gap-1.5 text-xs text-green-700 dark:text-green-400"
        >
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
          Uploaded {state.filename}
        </p>
      )}

      {state.kind === 'error' && (
        <p
          role="alert"
          className="mt-2 flex items-start gap-1.5 text-xs text-red-700 dark:text-red-400"
        >
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>{state.message}</span>
        </p>
      )}
    </div>
  );
}
