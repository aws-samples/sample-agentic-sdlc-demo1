/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
import { useState, useEffect, useCallback, useRef } from 'react';
import { Tree, NodeRendererProps } from 'react-arborist';
import {
  Folder,
  FolderOpen,
  FileText,
  ChevronRight,
  ChevronDown,
  RefreshCw,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { listFiles, buildFileTree, type FileTreeNode } from '../../services/s3Service';

interface FileBrowserProps {
  onFileSelect: (node: FileTreeNode) => void;
  projectPrefix?: string;
  refreshTrigger?: number;
  className?: string;
}

function Node({ node, style, dragHandle }: NodeRendererProps<FileTreeNode>) {
  const isFolder = node.data.type === 'folder';

  return (
    <div
      ref={dragHandle}
      style={style}
      className={`flex cursor-pointer items-center gap-1.5 rounded-sm px-2 py-1 text-sm select-none
        ${node.isSelected ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300' : 'text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800'}
      `}
      onClick={() => {
        if (isFolder) {
          node.toggle();
        } else {
          node.select();
        }
      }}
    >
      {isFolder ? (
        <>
          {node.isOpen ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-gray-400" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-gray-400" />
          )}
          {node.isOpen ? (
            <FolderOpen className="h-4 w-4 shrink-0 text-amber-500" />
          ) : (
            <Folder className="h-4 w-4 shrink-0 text-amber-500" />
          )}
        </>
      ) : (
        <>
          <span className="w-3.5" />
          <FileText className="h-4 w-4 shrink-0 text-gray-400 dark:text-gray-500" />
        </>
      )}
      <span className="truncate">{node.data.name}</span>
    </div>
  );
}

export function FileBrowser({ onFileSelect, projectPrefix, refreshTrigger, className }: FileBrowserProps) {
  const [tree, setTree] = useState<FileTreeNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [treeHeight, setTreeHeight] = useState(400);

  // Reset tree when project changes to avoid showing stale data
  useEffect(() => {
    setTree(null);
    setError(null);
  }, [projectPrefix]);

  const loadFiles = useCallback(async () => {
    if (!projectPrefix) return; // Don't load without a project scope
    setLoading(true);
    setError(null);
    try {
      const objects = await listFiles(`${projectPrefix}/`);
      const fileTree = buildFileTree(objects, projectPrefix);
      setTree(fileTree);
    } catch (err) {
      console.error('Failed to load files:', err);
      setError(err instanceof Error ? err.message : 'Failed to load files');
    } finally {
      setLoading(false);
    }
  }, [projectPrefix]);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  // Auto-refresh when new artifacts arrive (with 5s delay for S3 consistency)
  useEffect(() => {
    if (refreshTrigger && refreshTrigger > 0) {
      const timeout = setTimeout(loadFiles, 5000);
      return () => clearTimeout(timeout);
    }
  }, [refreshTrigger, loadFiles]);

  // Resize observer for tree height
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        // Subtract header height (~40px)
        setTreeHeight(Math.max(entry.contentRect.height - 40, 100));
      }
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const handleSelect = useCallback(
    (nodes: { data: FileTreeNode }[]) => {
      const selected = nodes[0];
      if (selected && selected.data.type === 'file') {
        onFileSelect(selected.data);
      }
    },
    [onFileSelect],
  );

  return (
    <div ref={containerRef} className={`flex h-full flex-col overflow-hidden ${className ?? ''}`}>
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-gray-200 px-3 py-2 dark:border-gray-700">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
          Files
        </h4>
        <button
          onClick={loadFiles}
          disabled={loading}
          aria-label="Refresh files"
          className="rounded-md p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 disabled:opacity-50 dark:hover:bg-gray-800 dark:hover:text-gray-300"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {loading && !tree && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
          </div>
        )}

        {error && (
          <div className="flex flex-col items-center gap-2 px-3 py-6 text-center">
            <AlertCircle className="h-5 w-5 text-red-400" />
            <p className="text-xs text-red-500">{error}</p>
          </div>
        )}

        {tree && !loading && tree.children?.length === 0 && (
          <div className="px-3 py-6 text-center text-xs text-gray-400">No files yet</div>
        )}

        {tree && tree.children && tree.children.length > 0 && (
          <Tree<FileTreeNode>
            data={tree.children}
            width="100%"
            height={treeHeight}
            indent={16}
            rowHeight={28}
            openByDefault={false}
            onSelect={handleSelect}
            disableDrag
            disableDrop
          >
            {Node}
          </Tree>
        )}
      </div>
    </div>
  );
}
