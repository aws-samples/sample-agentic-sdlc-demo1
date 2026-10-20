/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import type { ChatMessage } from "../../types/ws-messages";
import { relabelArtifactPaths } from "../../lib/displayLabels";

interface TextMessageProps {
  message: ChatMessage;
}

/**
 * Ensure paragraph breaks between distinct sentences/thoughts.
 * Detects sentence boundaries (period/colon followed by a capital letter
 * without an existing blank line) and inserts paragraph breaks.
 */
function ensureParagraphBreaks(text: string): string {
  // Insert \n\n before any capital letter that follows a period + optional space
  // but NOT inside tags like [PROGRESS_BANNER] and NOT for abbreviations like "e.g."
  let result = text;

  // Period followed by capital letter (with optional space) — new paragraph
  result = result.replace(/\.(\s*)([A-Z])/g, (match, space, letter) => {
    // Don't break if already has double newline
    if (space.includes("\n\n")) return match;
    return `.\n\n${letter}`;
  });

  // Colon followed by capital letter (with optional space) — new paragraph
  result = result.replace(/:(\s*)([A-Z])/g, (match, space, letter) => {
    if (space.includes("\n\n")) return match;
    // Don't break inside URLs (https:)
    if (match.startsWith("s:") || match.startsWith("p:")) return match;
    return `:\n\n${letter}`;
  });

  // Exclamation followed by capital letter — new paragraph
  result = result.replace(/!(\s*)([A-Z])/g, (match, space, letter) => {
    if (space.includes("\n\n")) return match;
    return `!\n\n${letter}`;
  });

  return result;
}

/**
 * Parse tagged content blocks from text.
 * Supports [PROGRESS_BANNER] and [STAGE_SUMMARY] tags.
 * Returns an array of typed segments for custom rendering.
 */
type Segment =
  | { type: "text"; content: string }
  | { type: "progress_banner"; title: string; stages: Array<{ icon: string; name: string; status: string }> }
  | { type: "stage_summary"; title: string; tableMarkdown: string };

function parseTaggedContent(text: string): Segment[] {
  const segments: Segment[] = [];

  // Combined regex for all tag types
  const tagRegex = /\[(PROGRESS_BANNER|STAGE_SUMMARY)\]\n?([\s\S]*?)\[\/\1\]/g;
  let lastIndex = 0;
  let match;

  while ((match = tagRegex.exec(text)) !== null) {
    // Text before the tag
    if (match.index > lastIndex) {
      const before = text.slice(lastIndex, match.index).trim();
      if (before) segments.push({ type: "text", content: before });
    }

    const tagType = match[1];
    const tagContent = match[2].trim();

    if (tagType === "PROGRESS_BANNER") {
      const lines = tagContent.split("\n").filter((l) => l.trim());
      const title = lines[0] || "📍 Workflow Progress";
      const stages = lines.slice(1).map((line) => {
        const trimmed = line.trim();
        const iconMatch = trimmed.match(/^([\u2705\u23ED\uFE0F\u{1F504}\u25B6\uFE0F\u{1F51C}][\uFE0F]?)\s*(.*)/u);
        if (iconMatch) {
          const icon = iconMatch[1];
          let rest = iconMatch[2];
          let status = "";
          if (rest.includes("← You are here")) {
            status = "current";
            rest = rest.replace("← You are here", "").trim();
          } else if (rest.includes("— REQUIRED")) {
            status = "required";
            rest = rest.replace("— REQUIRED", "").trim();
          } else if (icon === "✅") {
            status = "completed";
          } else if (icon === "⏭️" || icon === "⏭") {
            status = "skipped";
          } else {
            status = "pending";
          }
          return { icon, name: rest, status };
        }
        return { icon: "", name: trimmed, status: "pending" };
      });
      segments.push({ type: "progress_banner", title, stages });

    } else if (tagType === "STAGE_SUMMARY") {
      // First line is "title: ..." — extract it
      const lines = tagContent.split("\n");
      let title = "Stage Summary";
      let tableStart = 0;
      if (lines[0]?.trim().toLowerCase().startsWith("title:")) {
        title = lines[0].trim().slice(6).trim();
        tableStart = 1;
      }
      const tableMarkdown = lines.slice(tableStart).join("\n").trim();
      segments.push({ type: "stage_summary", title, tableMarkdown });
    }

    lastIndex = match.index + match[0].length;
  }

  // Remaining text after last tag
  if (lastIndex < text.length) {
    const remaining = text.slice(lastIndex).trim();
    if (remaining) segments.push({ type: "text", content: remaining });
  }

  if (segments.length === 0) {
    segments.push({ type: "text", content: text });
  }

  return segments;
}

function ProgressBanner({ title, stages }: {
  title: string;
  stages: Array<{ icon: string; name: string; status: string }>;
}) {
  return (
    <div className="rounded-xl bg-gradient-to-r from-indigo-50 to-blue-50 border border-indigo-100 p-4 dark:from-indigo-950 dark:to-blue-950 dark:border-indigo-800">
      <div className="text-lg font-bold text-indigo-900 dark:text-indigo-100 mb-3 flex items-center gap-2">
        {title}
      </div>
      <div className="space-y-1.5">
        {stages.map((stage, i) => {
          let textClass = "text-sm text-gray-600 dark:text-gray-400";
          let fontWeight = "font-normal";

          if (stage.status === "current") {
            textClass = "text-base text-indigo-700 dark:text-indigo-300";
            fontWeight = "font-semibold";
          } else if (stage.status === "completed") {
            textClass = "text-sm text-green-700 dark:text-green-400";
          } else if (stage.status === "skipped") {
            textClass = "text-sm text-gray-400 dark:text-gray-500 line-through";
          } else if (stage.status === "required") {
            textClass = "text-sm text-gray-500 dark:text-gray-400";
          }

          return (
            <div key={i} className={`flex items-center gap-2 ${textClass} ${fontWeight}`}>
              <span className="w-5 text-center shrink-0">{stage.icon}</span>
              <span>{stage.name}</span>
              {stage.status === "current" && (
                <span className="text-xs bg-indigo-100 dark:bg-indigo-800 text-indigo-600 dark:text-indigo-300 px-2 py-0.5 rounded-full ml-1">
                  You are here
                </span>
              )}
              {stage.status === "required" && (
                <span className="text-xs text-gray-400 dark:text-gray-500 ml-1">
                  REQUIRED
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StageSummary({ title, tableMarkdown }: { title: string; tableMarkdown: string }) {
  return (
    <div className="rounded-xl bg-gradient-to-r from-emerald-50 to-teal-50 border border-emerald-100 p-4 dark:from-emerald-950 dark:to-teal-950 dark:border-emerald-800">
      <div className="text-lg font-bold text-emerald-900 dark:text-emerald-100 mb-3">
        {title}
      </div>
      <div className="prose prose-sm dark:prose-invert max-w-none [&_table]:w-full [&_th]:text-left [&_th]:text-base [&_th]:font-bold [&_th]:py-2 [&_td]:py-1.5 [&_td:first-child]:font-semibold [&_td:first-child]:text-gray-700 [&_td:first-child]:dark:text-gray-300 [&_td:last-child]:text-gray-600 [&_td:last-child]:dark:text-gray-400 [&_tr]:border-b [&_tr]:border-emerald-100 [&_tr]:dark:border-emerald-800">
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
          {tableMarkdown}
        </ReactMarkdown>
      </div>
    </div>
  );
}

export function TextMessage({ message }: TextMessageProps) {
  const isUser = message.type === "user";

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-blue-600 px-4 py-2 text-white">
          <p className="whitespace-pre-wrap text-sm">{message.content}</p>
        </div>
      </div>
    );
  }

  // Relabel the artifact folder for display (aidlc-docs → agentic-sdlc),
  // then preprocess paragraph breaks and parse tagged content. Display-only:
  // covers both the prose segments and the [STAGE_SUMMARY] table.
  const displayContent = relabelArtifactPaths(message.content);
  const processedContent = ensureParagraphBreaks(displayContent);
  const segments = parseTaggedContent(processedContent);

  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] rounded-2xl rounded-bl-sm bg-gray-100 px-4 py-2 text-gray-900 dark:bg-gray-800 dark:text-gray-100">
        <div className="space-y-3">
          {segments.map((segment, i) => {
            if (segment.type === "progress_banner") {
              return (
                <ProgressBanner
                  key={i}
                  title={segment.title}
                  stages={segment.stages}
                />
              );
            }
            if (segment.type === "stage_summary") {
              return (
                <StageSummary
                  key={i}
                  title={segment.title}
                  tableMarkdown={segment.tableMarkdown}
                />
              );
            }
            return (
              <div key={i} className="prose prose-sm dark:prose-invert max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
                  {segment.content}
                </ReactMarkdown>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
