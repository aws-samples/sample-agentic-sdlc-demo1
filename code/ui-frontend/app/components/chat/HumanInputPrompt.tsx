import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import type { ChatMessage } from "../../types/ws-messages";

interface HumanInputPromptProps {
  message: ChatMessage;
}

/**
 * Preprocess the prompt to ensure options render as a proper markdown list.
 *
 * The agent sends prompts like:
 *   **Options:**\n1. Foo\n2. Bar\nX) Other
 *
 * Markdown requires a blank line before a numbered list for it to be
 * recognised as a list (especially with remark-breaks which turns \n
 * into <br>). This function inserts blank lines around the options
 * block so ReactMarkdown renders them as an ordered list.
 */
function ensureNumberedOptions(text: string): string {
  const lines = text.split("\n");
  const result: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // Insert blank line before the **Options:** header if previous line isn't blank
    if (
      trimmed.match(/^\*{0,2}Options:?\*{0,2}\s*$/i) &&
      result.length > 0 &&
      result[result.length - 1].trim() !== ""
    ) {
      result.push("");
    }

    result.push(lines[i]);

    // Insert blank line after the **Options:** header so the list starts cleanly
    if (trimmed.match(/^\*{0,2}Options:?\*{0,2}\s*$/i)) {
      result.push("");
    }

    // Insert blank line before "Please type..." instruction if previous line isn't blank
    if (
      (trimmed.toLowerCase().startsWith("please type") ||
        trimmed.toLowerCase().startsWith("please choose")) &&
      result.length > 1 &&
      result[result.length - 2].trim() !== ""
    ) {
      // Insert blank line before this line
      const lastLine = result.pop()!;
      result.push("");
      result.push(lastLine);
    }
  }

  return result.join("\n");
}

export function HumanInputPrompt({ message, onSendMessage }: HumanInputPromptProps) {
  const rawPrompt = message.metadata?.prompt || message.content || "";
  const prompt = ensureNumberedOptions(rawPrompt);

  return (
    <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-4 dark:border-yellow-800 dark:bg-yellow-950">
      <div className="prose prose-sm dark:prose-invert max-w-none">
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
          {prompt}
        </ReactMarkdown>
      </div>
    </div>
  );
}
