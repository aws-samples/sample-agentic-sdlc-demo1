import { AlertCircle } from "lucide-react";
import type { ChatMessage } from "../../types/ws-messages";

interface ErrorMessageProps {
  message: ChatMessage;
}

export function ErrorMessage({ message }: ErrorMessageProps) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{message.content}</span>
    </div>
  );
}
