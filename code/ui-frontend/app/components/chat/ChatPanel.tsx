/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { Send, Upload, ThumbsUp, ThumbsDown, CheckCircle2, Loader2 } from "lucide-react";
import type { ChatMessage } from "../../types/ws-messages";
import { TextMessage } from "./TextMessage";
import { StageBanner } from "./StageBanner";
import { ToolUseIndicator } from "./ToolUseIndicator";
import { ThinkingIndicator, TOOL_LABELS } from "./ThinkingIndicator";
import { ApprovalGate } from "./ApprovalGate";
import { HumanInputPrompt } from "./HumanInputPrompt";
import { NotificationMessage } from "./NotificationMessage";
import { ErrorMessage } from "./ErrorMessage";
import { HandoffCard } from "./HandoffCard";

interface ChatPanelProps {
  projectName: string;
  messages: ChatMessage[];
  currentStage: string | null;
  onSendMessage: (text: string) => void;
  isComplete: boolean;
}

function ChatHeader({
  projectName,
  currentStage,
}: {
  projectName: string;
  currentStage: string | null;
}) {
  return (
    <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-gray-700">
      <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
        {projectName}
      </h2>
      {currentStage && (
        <span className="rounded-full bg-blue-100 px-3 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-900 dark:text-blue-300">
          {currentStage}
        </span>
      )}
    </div>
  );
}

function CompletionBanner() {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-green-50 px-4 py-3 text-sm font-medium text-green-800 dark:bg-green-950 dark:text-green-200">
      <CheckCircle2 className="h-5 w-5 shrink-0" />
      <span>Workflow complete</span>
    </div>
  );
}

function MessageList({
  messages,
  onSendMessage,
}: {
  messages: ChatMessage[];
  onSendMessage: (text: string) => void;
}) {
  return (
    <>
      {messages.map((msg) => {
        switch (msg.type) {
          case "user":
          case "ai_text":
            return <TextMessage key={msg.id} message={msg} />;
          case "stage_start":
            return null; // Handled by thinking indicator near input bar
          case "stage_complete":
            return <StageBanner key={msg.id} message={msg} />;
          case "tool_use":
          case "thinking":
            return null; // Thinking shown near input bar, not in chat
          case "approval_required":
            return (
              <ApprovalGate
                key={msg.id}
                message={msg}
                onSendMessage={onSendMessage}
              />
            );
          case "human_input":
            return (
              <HumanInputPrompt
                key={msg.id}
                message={msg}
              />
            );
          case "human_input_notify":
            return <NotificationMessage key={msg.id} message={msg} />;
          case "handoff_required":
            return <HandoffCard key={msg.id} message={msg} />;
          case "error":
            return <ErrorMessage key={msg.id} message={msg} />;
          case "workflow_complete":
            return <CompletionBanner key={msg.id} />;
          default:
            return null;
        }
      })}
    </>
  );
}

function InputBar({
  onSendMessage,
  disabled,
}: {
  onSendMessage: (text: string) => void;
  disabled: boolean;
}) {
  const [text, setText] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || disabled) return;
    onSendMessage(text.trim());
    setText("");
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="flex items-center gap-2 border-t border-gray-200 px-4 py-3 dark:border-gray-700"
    >
      <button
        type="button"
        disabled={disabled}
        aria-label="Upload file"
        className="shrink-0 rounded-md p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
      >
        <Upload className="h-4 w-4" />
      </button>
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={disabled}
        placeholder={disabled ? "Workflow complete" : "Type a message…"}
        className="min-w-0 flex-1 rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
      />
      <button
        type="submit"
        disabled={disabled || !text.trim()}
        aria-label="Send message"
        className="shrink-0 rounded-md bg-blue-600 p-2 text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Send className="h-4 w-4" />
      </button>
      <div className="flex shrink-0 gap-1">
        <button
          type="button"
          disabled={disabled}
          aria-label="Thumbs up"
          className="rounded-md p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
        >
          <ThumbsUp className="h-4 w-4" />
        </button>
        <button
          type="button"
          disabled={disabled}
          aria-label="Thumbs down"
          className="rounded-md p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
        >
          <ThumbsDown className="h-4 w-4" />
        </button>
      </div>
    </form>
  );
}

export function ChatPanel({
  projectName,
  messages,
  currentStage,
  onSendMessage,
  isComplete,
}: ChatPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const userScrolledUpRef = useRef(false);

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    // Consider "scrolled up" if more than 100px from the bottom
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    userScrolledUpRef.current = distanceFromBottom > 100;
  }, []);

  useEffect(() => {
    if (!userScrolledUpRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  // Derive thinking label: show only when the last relevant message is a
  // thinking event (agent is working). Hide when the last message is a
  // human_input, approval_required, handoff_required, or workflow_complete
  // (waiting for user).
  const thinkingLabel = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const t = messages[i].type;
      if (
        t === "human_input" ||
        t === "approval_required" ||
        t === "handoff_required" ||
        t === "workflow_complete"
      ) {
        return null; // waiting for user — hide thinking
      }
      if (t === "thinking") {
        const toolName = messages[i].content || messages[i].metadata?.tool_name || "";
        return TOOL_LABELS[toolName] || `Working on ${toolName.replace(/_/g, " ")}…`;
      }
      // text chunks and stage events don't affect thinking visibility — keep scanning
      if (t === "ai_text" || t === "stage_start" || t === "stage_complete") {
        continue;
      }
      // any other message type — stop scanning
      return null;
    }
    return null;
  }, [messages]);

  return (
    <div className="flex h-full flex-col">
      <ChatHeader projectName={projectName} currentStage={currentStage} />
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 space-y-3 overflow-y-auto p-4"
      >
        <MessageList messages={messages} onSendMessage={onSendMessage} />
        <div ref={bottomRef} />
      </div>
      {thinkingLabel && !isComplete && <ThinkingIndicator label={thinkingLabel} />}
      <InputBar onSendMessage={onSendMessage} disabled={isComplete} />
    </div>
  );
}
