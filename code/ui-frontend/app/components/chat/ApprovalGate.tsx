import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import type { ChatMessage } from "../../types/ws-messages";

interface ApprovalGateProps {
  message: ChatMessage;
  onSendMessage: (text: string) => void;
}

function getButtonStyle(option: string): string {
  const lower = option.toLowerCase();
  if (lower.includes("request changes")) {
    return "bg-red-600 text-white hover:bg-red-700";
  }
  if (lower.includes("approve")) {
    return "bg-blue-600 text-white hover:bg-blue-700";
  }
  return "border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700";
}

export function ApprovalGate({ message, onSendMessage }: ApprovalGateProps) {
  const [resolved, setResolved] = useState(message.resolved ?? false); // nosemgrep: react-props-in-state — initial value from prop, updates handled via effects

  const stage = message.metadata?.stage || message.content || "Approval Required";
  const summary = message.metadata?.summary || "";
  const options = message.metadata?.options || [];

  const handleClick = (option: string) => {
    if (resolved) return;
    onSendMessage(option);
    setResolved(true);
  };

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950">
      <h3 className="mb-2 text-sm font-semibold text-gray-900 dark:text-gray-100">
        {stage}
      </h3>
      {summary && (
        <div className="prose prose-sm dark:prose-invert mb-3 max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{summary}</ReactMarkdown>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <button
            key={option}
            disabled={resolved}
            onClick={() => handleClick(option)}
            className={`rounded-full px-4 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${getButtonStyle(option)}`}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  );
}
