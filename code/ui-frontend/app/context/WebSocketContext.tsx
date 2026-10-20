import React, {
  createContext,
  useContext,
  useState,
  useRef,
  useCallback,
  useEffect,
} from 'react';
import { useAuth } from './AuthContext';
import { getConfig } from '../lib/config';
import type { WSMessage, ChatMessage, ArtifactFile } from '../types/ws-messages';
import { isImageFile } from '../services/s3Service';

// --- Exported helpers (for testing) ---

export function buildWebSocketUrl(region: string, runtimeArn: string): string {
  return `wss://bedrock-agentcore.${region}.amazonaws.com/runtimes/${encodeURIComponent(runtimeArn)}/ws`;
}

export function createInitMessage(projectSlug: string, request: string, projectName?: string, projectUuid?: string): string {
  return JSON.stringify({
    project_id: projectSlug,
    project_name: projectName || projectSlug,
    project_uuid: projectUuid || projectSlug,
    request,
  });
}

// --- Context type ---

export interface WebSocketContextType {
  status: 'disconnected' | 'connecting' | 'connected' | 'error';
  messages: ChatMessage[];
  currentStage: string | null;
  artifacts: ArtifactFile[];
  messageCount: number;
  sendMessage: (text: string) => void;
  connect: (projectId: string, request: string, projectName?: string) => void;
  disconnect: () => void;
  addArtifact: (filePath: string, content: string, objectUrl?: string | null) => void;
  removeArtifact: (filePath: string) => void;
}

const WebSocketContext = createContext<WebSocketContextType | undefined>(undefined);

// --- Internal helpers ---

let messageCounter = 0;
function nextId(): string {
  return `msg-${Date.now()}-${++messageCounter}`;
}

const MAX_RETRIES = 3;
const BASE_DELAY_MS = 1000;

function backoffDelay(attempt: number): number {
  return BASE_DELAY_MS * Math.pow(2, attempt); // 1s, 2s, 4s
}

export function wsMessageToChatMessage(msg: WSMessage): ChatMessage {
  const base = { id: nextId(), timestamp: Date.now(), content: '' };

  switch (msg.type) {
    case 'text_chunk':
      return { ...base, type: 'ai_text', content: msg.content };
    case 'stage_start':
      return { ...base, type: 'stage_start', content: msg.node_id, metadata: { node_id: msg.node_id } };
    case 'stage_complete':
      return { ...base, type: 'stage_complete', content: msg.node_id, metadata: { node_id: msg.node_id } };
    case 'tool_use':
      return { ...base, type: 'tool_use', content: msg.tool_name, metadata: { tool_name: msg.tool_name } };
    case 'thinking':
      return { ...base, type: 'thinking', content: msg.tool, metadata: { tool_name: msg.tool } };
    case 'approval_required':
      return {
        ...base,
        type: 'approval_required',
        content: msg.summary,
        metadata: { stage: msg.stage, summary: msg.summary, options: msg.options },
        resolved: false,
      };
    case 'human_input':
      return { ...base, type: 'human_input', content: msg.prompt, metadata: { prompt: msg.prompt }, resolved: false };
    case 'human_input_notify':
      return { ...base, type: 'human_input_notify', content: msg.message };
    case 'handoff_required':
      // Render the runtime's Handoff_Gate breakout as an actionable in-app
      // card (design Sections 5b / 9, Req 6.3 / 6.4 / 14.6). The card label
      // is derived in HandoffCard from the registry's display labels.
      return {
        ...base,
        type: 'handoff_required',
        content: msg.summary,
        metadata: {
          summary: msg.summary,
          project_uuid: msg.project_uuid,
          from_role: msg.from_role,
          to_role: msg.to_role,
        },
        resolved: false,
      };
    case 'artifact_updated':
      return { ...base, type: 'ai_text', content: `Artifact updated: ${msg.file_path}` };
    case 'workflow_complete':
      return { ...base, type: 'workflow_complete', content: 'Workflow complete' };
    case 'error':
      return { ...base, type: 'error', content: msg.message };
  }
}

// --- Provider ---

export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const { accessToken, user, signOut } = useAuth();
  const [status, setStatus] = useState<WebSocketContextType['status']>('disconnected');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [currentStage, setCurrentStage] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactFile[]>([]);
  const [messageCount, setMessageCount] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectArgsRef = useRef<{ projectId: string; request: string } | null>(null);
  const intentionalCloseRef = useRef(false);

  // --- Accumulate text_chunk into current AI message ---
  const handleTextChunk = useCallback((content: string) => {
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.type === 'ai_text' && !last.content.startsWith('Artifact updated:')) {
        // Accumulate into existing AI message bubble
        const updated = { ...last, content: last.content + content };
        return [...prev.slice(0, -1), updated];
      }
      // Start a new AI message bubble
      return [...prev, { id: nextId(), type: 'ai_text' as const, content, timestamp: Date.now() }];
    });
  }, []);

  // --- Fetch artifact content via presigned S3 URL ---
  const fetchArtifact = useCallback(async (filePath: string, presignedUrl?: string) => {
    setArtifacts((prev) => {
      const exists = prev.find((a) => a.filePath === filePath);
      if (exists) {
        return prev.map((a) => (a.filePath === filePath ? { ...a, loading: true, error: null } : a));
      }
      return [...prev, { filePath, content: null, objectUrl: null, loading: true, error: null }];
    });

    try {
      if (!presignedUrl) throw new Error('No presigned URL available for this artifact');

      const res = await fetch(presignedUrl);
      if (!res.ok) throw new Error(`Failed to fetch: ${res.status}`);

      if (isImageFile(filePath)) {
        const blob = await res.blob();
        const objectUrl = URL.createObjectURL(blob);
        setArtifacts((prev) =>
          prev.map((a) => (a.filePath === filePath ? { ...a, objectUrl, loading: false } : a)),
        );
      } else {
        const content = await res.text();
        setArtifacts((prev) =>
          prev.map((a) => (a.filePath === filePath ? { ...a, content, loading: false } : a)),
        );
      }
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : 'Unknown error';
      setArtifacts((prev) =>
        prev.map((a) => (a.filePath === filePath ? { ...a, loading: false, error: errorMsg } : a)),
      );
    }
  }, []);

  // --- Handle incoming WebSocket message ---
  const handleMessage = useCallback(
    (event: MessageEvent) => {
      let msg: WSMessage;
      try {
        msg = JSON.parse(event.data) as WSMessage;
      } catch {
        // Malformed JSON — skip silently per design doc
        return;
      }

      setMessageCount((c) => c + 1);

      // Update current stage tracking
      if (msg.type === 'stage_start') {
        setCurrentStage(msg.node_id);
      }

      // Accumulate text_chunk into current bubble
      if (msg.type === 'text_chunk') {
        handleTextChunk(msg.content);
        return;
      }

      // Trigger artifact fetch — filter out internal files the user shouldn't see
      if (msg.type === 'artifact_updated') {
        const fp = msg.file_path.toLowerCase();
        const isInternal =
          fp.endsWith('/audit.md') ||
          fp.includes('audit.md') ||
          fp.includes('requirement-verification-questions.md') ||
          fp.includes('questions.md');
        if (!isInternal) {
          fetchArtifact(msg.file_path, msg.presigned_url);
        }
        return; // Never show artifact_updated as a chat message
      }

      // Skip tool_use messages — too noisy, agent text already describes tools
      if (msg.type === 'tool_use') {
        return;
      }

      // Replace previous thinking indicator instead of accumulating them
      if (msg.type === 'thinking') {
        const chatMsg = wsMessageToChatMessage(msg);
        setMessages((prev) => {
          // Remove the last thinking message if present
          const filtered = prev.filter(
            (m, i) => !(m.type === 'thinking' && i === prev.length - 1)
          );
          return [...filtered, chatMsg];
        });
        return;
      }

      // Detect approval-style human_input prompts and convert to approval_required
      // so they render as clickable buttons instead of a text input
      if (msg.type === 'human_input') {
        const prompt = msg.prompt || '';
        const hasApproveOption = /approve\s*&?\s*continue/i.test(prompt);
        const hasRequestChanges = /request\s*changes/i.test(prompt);
        if (hasApproveOption && hasRequestChanges) {
          // Extract the summary (everything before "Please choose" or "**Please choose**")
          const summaryMatch = prompt.split(/\*?\*?Please choose:?\*?\*?/i);
          const summary = (summaryMatch[0] || prompt).trim();

          // Build options from the detected patterns
          const options: string[] = [];
          const changesMatch = prompt.match(/\*?\*?Request Changes\*?\*?\s*[—–-]\s*([^\n*]+)/i);
          const approveMatch = prompt.match(/\*?\*?Approve\s*&?\s*Continue\*?\*?\s*[—–-]\s*([^\n*]+)/i);
          if (changesMatch) options.push('Request Changes');
          if (approveMatch) options.push('Approve & Continue');
          if (options.length === 0) options.push('Request Changes', 'Approve & Continue');

          const chatMsg: ChatMessage = {
            id: nextId(),
            type: 'approval_required',
            content: summary,
            timestamp: Date.now(),
            metadata: { stage: 'Approval', summary, options },
            resolved: false,
          };
          setMessages((prev) => [...prev, chatMsg]);
          return;
        }
      }

      // Dispatch all other message types as new ChatMessage entries
      const chatMsg = wsMessageToChatMessage(msg);
      setMessages((prev) => [...prev, chatMsg]);
    },
    [handleTextChunk, fetchArtifact],
  );

  // --- Open WebSocket connection ---
  const openConnection = useCallback(
    (projectId: string, request: string, projectName?: string) => {
      const region = getConfig().awsRegion;
      const runtimeArn = getConfig().runtimeArn;
      const token = accessToken;

      if (!token) {
        setStatus('error');
        setMessages((prev) => [
          ...prev,
          { id: nextId(), type: 'error', content: 'No access token available. Please sign in.', timestamp: Date.now() },
        ]);
        return;
      }

      setStatus('connecting');
      const url = buildWebSocketUrl(region, runtimeArn);

      // Build human-readable session ID: project_name + email
      // AgentCore Memory requires: [a-zA-Z0-9][a-zA-Z0-9-_]*
      // So replace @ . and other invalid chars with underscores
      // e.g. "online_bookstore_pabetts_amazon_com"
      const namePart = (projectName || projectId)
        .toLowerCase()
        .replace(/\s+/g, '_')
        .replace(/[^a-z0-9_\-]/g, '');
      const emailPart = (user?.email ?? user?.username ?? 'anonymous')
        .replace(/[@.]/g, '_')
        .replace(/[^a-zA-Z0-9_\-]/g, '');
      const sessionId = `${namePart}_${emailPart}`;

      // Append session ID as query parameter per AgentCore docs
      const wsUrl = `${url}?X-Amzn-Bedrock-AgentCore-Runtime-Session-Id=${encodeURIComponent(sessionId)}`;

      // Browser WebSocket API doesn't support custom headers.
      // AgentCore accepts the bearer token via Sec-WebSocket-Protocol header
      // using base64url encoding with the "base64UrlBearerAuthorization" sentinel.
      const base64url = btoa(token)
        .replace(/\+/g, '-')
        .replace(/\//g, '_')
        .replace(/=/g, '');
      const ws = new WebSocket(wsUrl, [
        `base64UrlBearerAuthorization.${base64url}`,
        'base64UrlBearerAuthorization',
      ]);

      // Track whether the socket ever successfully opened.
      // If onerror+onclose fire without onopen, the handshake failed
      // (typically a 403 from an expired token).
      let didOpen = false;

      ws.onopen = () => {
        didOpen = true;
        setStatus('connected');
        retryCountRef.current = 0;
        // Send slugified project name as project_id (used as S3 prefix)
        // and the display name as project_name
        // and the original UUID as project_uuid (used for credential lookup)
        ws.send(createInitMessage(namePart, request, projectName, projectId));
      };

      ws.onmessage = handleMessage;

      ws.onerror = () => {
        // onerror alone doesn't tell us much; onclose follows with details
        if (!didOpen) {
          setStatus('error');
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (intentionalCloseRef.current) {
          setStatus('disconnected');
          return;
        }

        // If the socket never opened, the handshake failed (likely 403 —
        // expired/invalid token). Sign the user out and redirect to
        // sign-in instead of retrying with the same stale token.
        if (!didOpen) {
          setStatus('error');
          setMessages((prev) => [
            ...prev,
            {
              id: nextId(),
              type: 'error',
              content: 'Session expired. Redirecting to sign in…',
              timestamp: Date.now(),
            },
          ]);
          // Give the user a moment to see the message, then sign out
          setTimeout(() => {
            signOut().catch(() => {});
          }, 1500);
          return;
        }

        // Socket was open and then closed — attempt reconnection
        if (retryCountRef.current < MAX_RETRIES) {
          const delay = backoffDelay(retryCountRef.current);
          retryCountRef.current += 1;
          setStatus('connecting');
          setMessages((prev) => [
            ...prev,
            {
              id: nextId(),
              type: 'error',
              content: `Connection lost. Reconnecting in ${delay / 1000}s... (attempt ${retryCountRef.current}/${MAX_RETRIES})`,
              timestamp: Date.now(),
            },
          ]);
          retryTimerRef.current = setTimeout(() => {
            openConnection(projectId, request);
          }, delay);
        } else {
          setStatus('error');
          setMessages((prev) => [
            ...prev,
            {
              id: nextId(),
              type: 'error',
              content: 'Connection failed after 3 retries. Please try again.',
              timestamp: Date.now(),
            },
          ]);
        }
      };

      wsRef.current = ws;
    },
    [accessToken, user, handleMessage, signOut],
  );

  // --- Public API ---

  const connect = useCallback(
    (projectId: string, request: string, projectName?: string) => {
      // Clean up any existing connection
      intentionalCloseRef.current = true;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }

      // Reset state for new session
      intentionalCloseRef.current = false;
      retryCountRef.current = 0;
      connectArgsRef.current = { projectId, request };
      setMessages([]);
      setCurrentStage(null);
      setArtifacts([]);

      openConnection(projectId, request, projectName);
    },
    [openConnection],
  );

  const disconnect = useCallback(() => {
    intentionalCloseRef.current = true;
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setStatus('disconnected');
  }, []);

  const sendMessage = useCallback(
    (text: string) => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        // Add user message to chat
        setMessages((prev) => [
          ...prev,
          { id: nextId(), type: 'user', content: text, timestamp: Date.now() },
        ]);
        wsRef.current.send(text);
      }
    },
    [],
  );

  // Add an artifact from external sources (e.g., file browser click)
  const addArtifact = useCallback((filePath: string, content: string, objectUrl?: string | null) => {
    setArtifacts((prev) => {
      const exists = prev.find((a) => a.filePath === filePath);
      if (exists) {
        if (exists.objectUrl && exists.objectUrl !== objectUrl) URL.revokeObjectURL(exists.objectUrl);
        return prev.map((a) => (a.filePath === filePath ? { ...a, content, objectUrl: objectUrl ?? null, loading: false, error: null } : a));
      }
      return [...prev, { filePath, content, objectUrl: objectUrl ?? null, loading: false, error: null }];
    });
  }, []);

  const removeArtifact = useCallback((filePath: string) => {
    setArtifacts((prev) => {
      const toRemove = prev.find((a) => a.filePath === filePath);
      if (toRemove?.objectUrl) URL.revokeObjectURL(toRemove.objectUrl);
      return prev.filter((a) => a.filePath !== filePath);
    });
  }, []);

  // Clean close on unmount
  useEffect(() => {
    return () => {
      intentionalCloseRef.current = true;
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  return (
    <WebSocketContext.Provider
      value={{ status, messages, currentStage, artifacts, messageCount, sendMessage, connect, disconnect, addArtifact, removeArtifact }}
    >
      {children}
    </WebSocketContext.Provider>
  );
}

export function useWebSocket(): WebSocketContextType {
  const context = useContext(WebSocketContext);
  if (context === undefined) {
    throw new Error('useWebSocket must be used within a WebSocketProvider');
  }
  return context;
}
