/* nosemgrep: react-props-spreading — Radix UI wrapper, prop spreading is intentional */
/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FileText, Loader2, AlertCircle, RefreshCw, X } from "lucide-react";
import type { ArtifactFile } from "../../types/ws-messages";
import { MermaidDiagram } from "./MermaidDiagram";
import { isImageFile } from "../../services/s3Service";
import { relabelArtifactPaths } from "../../lib/displayLabels";

interface DocumentViewerProps {
  artifacts: ArtifactFile[];
  activeFilePath: string | null;
  onSelectFile: (filePath: string) => void;
  onRetry: (filePath: string) => void;
  onCloseFile?: (filePath: string) => void;
}

function getFileName(filePath: string): string {
  const segments = filePath.split("/");
  return segments[segments.length - 1] || filePath;
}

function isMarkdownFile(filePath: string): boolean {
  return filePath.toLowerCase().endsWith(".md");
}

export function DocumentViewer({
  artifacts,
  activeFilePath,
  onSelectFile,
  onRetry,
  onCloseFile,
}: DocumentViewerProps) {
  if (artifacts.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-gray-400">
        <FileText className="mb-3 h-12 w-12" />
        <p className="text-sm">
          Documents will appear here when the agent generates artifacts
        </p>
      </div>
    );
  }

  const activeArtifact = artifacts.find((a) => a.filePath === activeFilePath);

  return (
    <div className="flex h-full flex-col">
      {/* Tab bar */}
      <div className="flex border-b border-gray-200 dark:border-gray-700 overflow-x-auto">
        {artifacts.map((artifact) => (
          <div
            key={artifact.filePath}
            className={`group flex shrink-0 items-center gap-1 border-b-2 transition-colors ${
              artifact.filePath === activeFilePath
                ? "border-blue-600 dark:border-blue-400"
                : "border-transparent"
            }`}
          >
            <button
              onClick={() => onSelectFile(artifact.filePath)}
              className={`py-2 pl-4 pr-1 text-sm font-medium transition-colors ${
                artifact.filePath === activeFilePath
                  ? "text-blue-600 dark:text-blue-400"
                  : "text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
              }`}
            >
              {getFileName(artifact.filePath)}
            </button>
            {onCloseFile && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onCloseFile(artifact.filePath);
                }}
                aria-label={`Close ${getFileName(artifact.filePath)}`}
                className="mr-1 rounded p-0.5 text-gray-400 opacity-0 transition-opacity hover:bg-gray-200 hover:text-gray-600 group-hover:opacity-100 dark:hover:bg-gray-700 dark:hover:text-gray-300"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        ))}
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-auto p-4">
        {activeArtifact ? (
          <ArtifactContent artifact={activeArtifact} onRetry={onRetry} />
        ) : (
          <div className="flex h-full items-center justify-center text-gray-400">
            <p className="text-sm">Select a file to view</p>
          </div>
        )}
      </div>
    </div>
  );
}

function ArtifactContent({
  artifact,
  onRetry,
}: {
  artifact: ArtifactFile;
  onRetry: (filePath: string) => void;
}) {
  if (artifact.loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-blue-500" />
      </div>
    );
  }

  if (artifact.error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-red-500">
        <AlertCircle className="h-8 w-8" />
        <p className="text-sm text-center">
          Failed to load <span className="font-mono">{artifact.filePath}</span>
        </p>
        <p className="text-xs text-gray-500">{artifact.error}</p>
        <button
          onClick={() => onRetry(artifact.filePath)}
          className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-red-50 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-100 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/30"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Retry
        </button>
      </div>
    );
  }

  if ((artifact.content === null || artifact.content === "") && !artifact.objectUrl) {
    return (
      <div className="flex h-full items-center justify-center text-gray-400">
        <p className="text-sm">This file is empty</p>
      </div>
    );
  }

  // Image files — render with <img> using the object URL
  if (isImageFile(artifact.filePath) && artifact.objectUrl) {
    return (
      <div className="flex h-full items-center justify-center p-4">
        <img
          src={artifact.objectUrl}
          alt={getFileName(artifact.filePath)}
          className="max-h-full max-w-full object-contain rounded"
        />
      </div>
    );
  }

  if (isMarkdownFile(artifact.filePath)) {
    return (
      <div className="prose prose-slate dark:prose-invert max-w-none
        prose-headings:font-semibold prose-headings:tracking-tight
        prose-h1:text-2xl prose-h1:border-b prose-h1:border-gray-200 prose-h1:pb-2 prose-h1:mb-4
        prose-h2:text-xl prose-h2:mt-8 prose-h2:mb-3
        prose-h3:text-lg prose-h3:mt-6 prose-h3:mb-2
        prose-p:leading-relaxed
        prose-table:border-collapse prose-table:w-full prose-table:text-sm
        prose-th:bg-gray-50 prose-th:px-4 prose-th:py-2 prose-th:text-left prose-th:font-semibold prose-th:border prose-th:border-gray-200
        prose-td:px-4 prose-td:py-2 prose-td:border prose-td:border-gray-200
        prose-li:my-0.5
        prose-hr:my-6
        dark:prose-th:bg-gray-800 dark:prose-th:border-gray-700
        dark:prose-td:border-gray-700
        dark:prose-h1:border-gray-700
      ">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            code({ className, children, ...props }) {
              const match = /language-(\w+)/.exec(className || "");
              const lang = match ? match[1] : "";
              const codeString = String(children).replace(/\n$/, "");

              if (lang === "mermaid") {
                return <MermaidDiagram chart={codeString} />;
              }

              return (
                <code className={className} {...props}>
                  {children}
                </code>
              );
            },
          }}
        >
          {relabelArtifactPaths(artifact.content)}
        </ReactMarkdown>
      </div>
    );
  }

  return (
    <pre className="whitespace-pre-wrap rounded-md bg-gray-50 p-4 text-sm text-gray-800 dark:bg-gray-900 dark:text-gray-200">
      {artifact.content}
    </pre>
  );
}
