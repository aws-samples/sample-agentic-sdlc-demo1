import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router';
import { useAuth } from '../context/AuthContext';
import { WebSocketProvider, useWebSocket } from '../context/WebSocketContext';
import { ChatPanel } from './chat/ChatPanel';
import { DocumentViewer } from './document/DocumentViewer';
import { FileBrowser } from './files/FileBrowser';
import { ProgressPanel } from './progress/ProgressPanel';
import { RequirementsUploadControl } from './RequirementsUploadControl';
import { SolutionArchitectReviewPanel } from './SolutionArchitectReviewPanel';
import { QADevOpsReviewPanel } from './QADevOpsReviewPanel';
import { ChangeRequestBanner } from './ChangeRequestBanner';
import { ReturnForChangesControl } from './ReturnForChangesControl';
import { RepoLinkControl } from './RepoLinkControl';
import { ConstructionBriefDownload } from './ConstructionBriefDownload';
import { getFileContent, getFileObjectUrl, isImageFile, type FileTreeNode } from '../services/s3Service';
import { getProject, type ProjectRecord } from '../services/projectService';
import type { ChatMessage } from '../types/ws-messages';
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from './ui/resizeable';
import { ArrowLeft, LogOut, MessageSquare, FileText, FolderTree } from 'lucide-react';

function useIsMobile(breakpoint = 768): boolean {
  const [isMobile, setIsMobile] = useState( // nosemgrep: react-props-in-state — initial value from prop, updates handled via effects
    typeof window !== 'undefined' ? window.innerWidth < breakpoint : false,
  );

  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${breakpoint - 1}px)`);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    setIsMobile(mql.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, [breakpoint]);

  return isMobile;
}

function StatusDot({ status }: { status: 'disconnected' | 'connecting' | 'connected' | 'error' }) {
  const colors: Record<typeof status, string> = {
    connected: 'bg-green-500',
    connecting: 'bg-yellow-500',
    error: 'bg-red-500',
    disconnected: 'bg-gray-400',
  };
  const labels: Record<typeof status, string> = {
    connected: 'Connected',
    connecting: 'Connecting\u2026',
    error: 'Error',
    disconnected: 'Disconnected',
  };

  return (
    <span className="flex items-center gap-1.5 text-xs text-gray-500" aria-label={`Connection status: ${labels[status]}`}>
      <span className={`inline-block h-2 w-2 rounded-full ${colors[status]}`} />
      {labels[status]}
    </span>
  );
}

function TopNavBar({
  projectName,
  status,
  userName,
  userRole,
  onBack,
  onSignOut,
}: {
  projectName: string;
  status: 'disconnected' | 'connecting' | 'connected' | 'error';
  userName?: string;
  userRole?: string;
  onBack: () => void;
  onSignOut: () => void;
}) {
  return (
    <header className="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-3 dark:border-gray-700 dark:bg-gray-900">
      <div className="flex items-center gap-3">
        <button
          onClick={onBack}
          aria-label="Back to projects"
          className="rounded-md p-1.5 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <span className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Agentic SDLC Transformation
        </span>
        <span className="text-gray-300 dark:text-gray-600" aria-hidden="true">
          /
        </span>
        <h1 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          {projectName}
        </h1>
        <StatusDot status={status} />
      </div>
      <div className="flex items-center gap-4">
        {userRole && (
          <div className="text-right" aria-label={`Signed in as ${userRole}`}>
            {userName && (
              <p className="text-sm font-medium leading-tight text-gray-900 dark:text-gray-100">
                {userName}
              </p>
            )}
            <p className="text-xs leading-tight text-gray-500 dark:text-gray-400">
              {userRole}
            </p>
          </div>
        )}
        <button
          onClick={onSignOut}
          aria-label="Sign out"
          className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
        >
          <LogOut className="h-3.5 w-3.5" />
          Sign Out
        </button>
      </div>
    </header>
  );
}

/**
 * Render the central chat region together with the Solution Architect
 * review panel (task 7.4). The SA panel sits above the chat so that on
 * `assigned_to_sa` the BA artifacts and approve / request-changes actions
 * are immediately visible, and on `in_design`/`design_ready` the design
 * completion control is reachable without leaving the conversation flow.
 *
 * The panel renders nothing for non-SA viewers or when the project is in a
 * state outside the SA-actionable set, so a Business Analyst session is
 * unaffected.
 */
function ChatRegion({
  projectName,
  messages,
  currentStage,
  sendMessage,
  isComplete,
  projectRecord,
  userGroups,
  onRecordUpdated,
}: {
  projectName: string;
  messages: ChatMessage[];
  currentStage: string | null;
  sendMessage: (text: string) => void;
  isComplete: boolean;
  projectRecord: ProjectRecord | null;
  userGroups: string[];
  onRecordUpdated: (record: ProjectRecord) => void;
}) {
  return (
    <div className="flex h-full flex-col">
      {projectRecord && (
        <div className="flex flex-col gap-2 border-b border-gray-200 p-2 dark:border-gray-700">
          <ChangeRequestBanner record={projectRecord} userGroups={userGroups} />
          <SolutionArchitectReviewPanel
            record={projectRecord}
            userGroups={userGroups}
            onSendMessage={sendMessage}
            onRecordUpdated={onRecordUpdated}
          />
          <QADevOpsReviewPanel
            record={projectRecord}
            userGroups={userGroups}
            onRecordUpdated={onRecordUpdated}
          />
          <ConstructionBriefDownload
            record={projectRecord}
            userGroups={userGroups}
          />
          <RepoLinkControl
            record={projectRecord}
            userGroups={userGroups}
            onRecordUpdated={onRecordUpdated}
          />
          <ReturnForChangesControl
            record={projectRecord}
            userGroups={userGroups}
            onRecordUpdated={onRecordUpdated}
          />
        </div>
      )}
      <div className="flex-1 min-h-0">
        <ChatPanel
          projectName={projectName}
          messages={messages}
          currentStage={currentStage}
          onSendMessage={sendMessage}
          isComplete={isComplete}
        />
      </div>
    </div>
  );
}

function WorkspaceContent() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const { user, signOut } = useAuth();
  const {
    status,
    messages,
    currentStage,
    artifacts,
    messageCount,
    sendMessage,
    connect,
    disconnect,
    addArtifact,
    removeArtifact,
  } = useWebSocket();

  const isMobile = useIsMobile();
  const [activePanel, setActivePanel] = useState<'chat' | 'files' | 'document'>('chat');
  const [activeFilePath, setActiveFilePath] = useState<string | null>(null);
  // Track the full project record so persona-specific surfaces (the
  // Solution Architect review panel, task 7.4) can react to handoff state
  // changes immediately after a Project API transition without a re-fetch.
  // Display name is derived from the record so the placeholder ("Project")
  // is preserved until the API call resolves.
  const [projectRecord, setProjectRecord] = useState<ProjectRecord | null>(null);
  const projectName = projectRecord?.name ?? 'Project';

  // Show the requirements upload control only in the Business Analyst
  // workspace (Req 5.1). The Cognito group `requirements-analyst` is the
  // owning role for the BA workflow segment in `workflow_registry.json`. The
  // server-side Project API also re-checks ownership on the upload-url route,
  // so this is purely presentational gating.
  const isBusinessAnalyst = !!user?.groups?.includes('requirements-analyst');

  // Derive the S3 prefix slug from the project record's `artifact_prefix` so
  // it stays in sync with the Project Store. Falling back to the slugified
  // display name keeps the workspace functional during the brief window
  // between mount and the `getProject` resolution.
  const projectSlug = projectRecord?.artifact_prefix
    ?? (projectName !== 'Project'
      ? projectName.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_\-]/g, '')
      : undefined);

  // Load the project record from the shared Project Store (design Section 2d,
  // Req 3.4). Replaces the prior `localStorage.getItem('projects_${user.sub}')`
  // lookup so a Solution Architect signing in as a different user resolves the
  // same project (and therefore the same artifact prefix, Req 3.3) as the
  // Business Analyst who created it.
  useEffect(() => {
    if (!user || !projectId) return;
    let cancelled = false;
    getProject(projectId)
      .then((record) => {
        if (cancelled) return;
        setProjectRecord(record);
      })
      .catch(() => {
        // Leave the placeholder name in place — the WebSocket connection
        // below is gated on `projectName !== 'Project'`, so a missing record
        // simply prevents the workspace from connecting until the user
        // navigates back to the project list.
      });
    return () => {
      cancelled = true;
    };
  }, [user, projectId]);

  // Connect to WebSocket once project name is loaded
  useEffect(() => {
    if (projectId && projectName !== 'Project') {
      connect(projectId, 'Start workflow', projectName);
    }
    return () => {
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, projectName]);

  // Auto-select first artifact when artifacts change
  useEffect(() => {
    if (artifacts.length > 0 && !activeFilePath) {
      setActiveFilePath(artifacts[0].filePath);
    }
  }, [artifacts, activeFilePath]);

  const isComplete = messages.some((m) => m.type === 'workflow_complete');

  const handleBack = useCallback(() => {
    disconnect();
    navigate('/');
  }, [disconnect, navigate]);

  const handleSignOut = useCallback(async () => {
    disconnect();
    await signOut();
    navigate('/');
  }, [disconnect, signOut, navigate]);

  const handleSelectFile = useCallback((filePath: string) => {
    setActiveFilePath(filePath);
  }, []);

  const handleRetry = useCallback(
    (filePath: string) => {
      setActiveFilePath(filePath);
    },
    [],
  );

  const handleCloseFile = useCallback(
    (filePath: string) => {
      // If closing the active tab, switch to an adjacent one
      if (filePath === activeFilePath) {
        const idx = artifacts.findIndex((a) => a.filePath === filePath);
        const next = artifacts[idx + 1] ?? artifacts[idx - 1];
        setActiveFilePath(next?.filePath ?? null);
      }
      removeArtifact(filePath);
    },
    [activeFilePath, artifacts, removeArtifact],
  );

  // Handle file browser click: fetch content and add as artifact tab
  const handleFileBrowserSelect = useCallback(
    async (node: FileTreeNode) => {
      if (!node.key) return;

      // If already in artifacts, just select it
      const existing = artifacts.find((a) => a.filePath === node.key);
      if (existing) {
        setActiveFilePath(node.key);
        return;
      }

      // Add loading placeholder
      addArtifact(node.key, '');
      setActiveFilePath(node.key);

      try {
        if (isImageFile(node.key)) {
          const objectUrl = await getFileObjectUrl(node.key);
          addArtifact(node.key, '', objectUrl);
        } else {
          const content = await getFileContent(node.key);
          addArtifact(node.key, content);
        }
      } catch (err) {
        console.error('Failed to load file:', err);
      }
    },
    [artifacts, addArtifact],
  );

  if (isMobile) {
    return (
      <div className="flex h-screen flex-col bg-gray-50 dark:bg-gray-950">
        <TopNavBar
          projectName={projectName}
          status={status}
          userName={user?.username}
          userRole={user?.role}
          onBack={handleBack}
          onSignOut={handleSignOut}
        />
        {/* Mobile toggle — 3 tabs */}
        <div className="flex border-b border-gray-200 dark:border-gray-700">
          <button
            onClick={() => setActivePanel('chat')}
            className={`flex flex-1 items-center justify-center gap-1.5 py-2 text-sm font-medium transition-colors ${
              activePanel === 'chat'
                ? 'border-b-2 border-blue-600 text-blue-600 dark:border-blue-400 dark:text-blue-400'
                : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
            }`}
          >
            <MessageSquare className="h-4 w-4" />
            Chat
          </button>
          <button
            onClick={() => setActivePanel('files')}
            className={`flex flex-1 items-center justify-center gap-1.5 py-2 text-sm font-medium transition-colors ${
              activePanel === 'files'
                ? 'border-b-2 border-blue-600 text-blue-600 dark:border-blue-400 dark:text-blue-400'
                : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
            }`}
          >
            <FolderTree className="h-4 w-4" />
            Files
          </button>
          <button
            onClick={() => setActivePanel('document')}
            className={`flex flex-1 items-center justify-center gap-1.5 py-2 text-sm font-medium transition-colors ${
              activePanel === 'document'
                ? 'border-b-2 border-blue-600 text-blue-600 dark:border-blue-400 dark:text-blue-400'
                : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
            }`}
          >
            <FileText className="h-4 w-4" />
            Documents
          </button>
        </div>
        <div className="flex-1 overflow-hidden">
          {activePanel === 'chat' && (
            <ChatRegion
              projectName={projectName}
              messages={messages}
              currentStage={currentStage}
              sendMessage={sendMessage}
              isComplete={isComplete}
              projectRecord={projectRecord}
              userGroups={user?.groups ?? []}
              onRecordUpdated={setProjectRecord}
            />
          )}
          {activePanel === 'files' && (
            <div className="flex h-full flex-col">
              {isBusinessAnalyst && projectId && (
                <div className="border-b border-gray-200 p-2 dark:border-gray-700">
                  <RequirementsUploadControl projectId={projectId} />
                </div>
              )}
              <div className="flex-1 overflow-hidden">
                <FileBrowser onFileSelect={handleFileBrowserSelect} projectPrefix={projectSlug} refreshTrigger={messageCount} />
              </div>
              <div className="h-1/3 border-t border-gray-200 dark:border-gray-700">
                <ProgressPanel refreshTrigger={messageCount} projectPrefix={projectSlug} />
              </div>
            </div>
          )}
          {activePanel === 'document' && (
            <DocumentViewer
              artifacts={artifacts}
              activeFilePath={activeFilePath}
              onSelectFile={handleSelectFile}
              onRetry={handleRetry}
              onCloseFile={handleCloseFile}
            />
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col bg-gray-50 dark:bg-gray-950">
      <TopNavBar
        projectName={projectName}
        status={status}
        userName={user?.username}
        userRole={user?.role}
        onBack={handleBack}
        onSignOut={handleSignOut}
      />
      <ResizablePanelGroup direction="horizontal" className="flex-1">
        {/* Left sidebar: File Browser + Progress Panel */}
        <ResizablePanel defaultSize={20} minSize={15} maxSize={35}>
          <ResizablePanelGroup direction="vertical">
            <ResizablePanel defaultSize={60} minSize={30}>
              <div className="flex h-full flex-col">
                {isBusinessAnalyst && projectId && (
                  <div className="border-b border-gray-200 p-2 dark:border-gray-700">
                    <RequirementsUploadControl projectId={projectId} />
                  </div>
                )}
                <div className="flex-1 overflow-hidden">
                  <FileBrowser onFileSelect={handleFileBrowserSelect} projectPrefix={projectSlug} refreshTrigger={messageCount} />
                </div>
              </div>
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={40} minSize={20}>
              <ProgressPanel refreshTrigger={messageCount} projectPrefix={projectSlug} />
            </ResizablePanel>
          </ResizablePanelGroup>
        </ResizablePanel>
        <ResizableHandle withHandle />

        {/* Center: Chat Panel */}
        <ResizablePanel defaultSize={40} minSize={25}>
          <ChatRegion
            projectName={projectName}
            messages={messages}
            currentStage={currentStage}
            sendMessage={sendMessage}
            isComplete={isComplete}
            projectRecord={projectRecord}
            userGroups={user?.groups ?? []}
            onRecordUpdated={setProjectRecord}
          />
        </ResizablePanel>
        <ResizableHandle withHandle />

        {/* Right: Document Viewer */}
        <ResizablePanel defaultSize={40} minSize={25}>
          <DocumentViewer
            artifacts={artifacts}
            activeFilePath={activeFilePath}
            onSelectFile={handleSelectFile}
            onRetry={handleRetry}
            onCloseFile={handleCloseFile}
          />
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  );
}

export function ProjectWorkspace() {
  return (
    <WebSocketProvider>
      <WorkspaceContent />
    </WebSocketProvider>
  );
}
