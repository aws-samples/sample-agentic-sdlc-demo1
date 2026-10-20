import { Info } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import type { ChatMessage } from "../../types/ws-messages";

interface NotificationMessageProps {
  message: ChatMessage;
}

export function NotificationMessage({ message }: NotificationMessageProps) {
  return (
    <div className="flex items-start gap-2 rounded-lg bg-blue-50 px-4 py-3 text-sm text-blue-800 dark:bg-blue-950 dark:text-blue-200">
      <Info className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="prose prose-sm dark:prose-invert max-w-none">
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{message.content}</ReactMarkdown>
      </div>
    </div>
  );
}
