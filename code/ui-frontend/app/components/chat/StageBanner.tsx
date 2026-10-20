import { Loader2, CheckCircle2 } from "lucide-react";
import type { ChatMessage } from "../../types/ws-messages";

interface StageBannerProps {
  message: ChatMessage;
}

const STAGE_DISPLAY_NAMES: Record<string, string> = {
  hub_post_requirements_analysis: "Checking ISV tool integrations",
  hub_post_user_stories: "Checking ISV tool integrations",
  hub_post_workflow_planning: "Checking ISV tool integrations",
  hub_post_application_design: "Checking ISV tool integrations",
  hub_post_units_generation: "Checking ISV tool integrations",
  hub_post_functional_design: "Checking ISV tool integrations",
  hub_post_code_generation: "Checking ISV tool integrations",
  hub_post_build_and_test: "Checking ISV tool integrations",
};

function formatStageName(nodeId: string): string {
  if (STAGE_DISPLAY_NAMES[nodeId]) return STAGE_DISPLAY_NAMES[nodeId];
  return nodeId.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function StageBanner({ message }: StageBannerProps) {
  const nodeId = message.content || message.metadata?.node_id || "Stage";
  const stageName = formatStageName(nodeId);
  const isComplete = message.type === "stage_complete";

  return (
    <div className="flex items-center gap-2 rounded-lg bg-gray-50 px-4 py-2 text-sm text-gray-700 dark:bg-gray-800 dark:text-gray-300">
      {isComplete ? (
        <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" />
      ) : (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-blue-500" />
      )}
      <span>{stageName}</span>
    </div>
  );
}
