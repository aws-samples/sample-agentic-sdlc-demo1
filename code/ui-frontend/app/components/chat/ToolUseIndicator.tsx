import { Wrench } from "lucide-react";
import type { ChatMessage } from "../../types/ws-messages";

interface ToolUseIndicatorProps {
  message: ChatMessage;
}

export function ToolUseIndicator({ message }: ToolUseIndicatorProps) {
  const toolName = message.content || message.metadata?.tool_name || "Unknown tool";

  return (
    <div className="flex items-center gap-2 rounded-md bg-gray-50 px-3 py-1.5 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-400">
      <Wrench className="h-3.5 w-3.5 shrink-0" />
      <span>{toolName}</span>
    </div>
  );
}
