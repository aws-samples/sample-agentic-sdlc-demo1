export type WSMessage =
  | { type: "text_chunk"; content: string }
  | { type: "stage_start"; node_id: string }
  | { type: "stage_complete"; node_id: string }
  | { type: "tool_use"; tool_name: string }
  | { type: "thinking"; tool: string }
  | { type: "approval_required"; stage: string; summary: string; options: string[] }
  | { type: "human_input"; prompt: string }
  | { type: "human_input_notify"; message: string }
  | {
      // Sent by the runtime when the SDLC graph reaches a Handoff_Gate
      // (design Sections 5b / 9, Req 6.3). The Frontend renders this as an
      // actionable in-app card whose label is derived from the registry's
      // display labels using `from_role` / `to_role` (Req 14.6, in-app only).
      type: "handoff_required";
      project_uuid: string;
      from_role: string;
      to_role: string;
      summary: string;
    }
  | { type: "artifact_updated"; file_path: string; presigned_url?: string }
  | { type: "workflow_complete" }
  | { type: "error"; message: string };

export interface ChatMessage {
  id: string;
  type:
    | "user"
    | "ai_text"
    | "stage_start"
    | "stage_complete"
    | "tool_use"
    | "thinking"
    | "approval_required"
    | "human_input"
    | "human_input_notify"
    | "handoff_required"
    | "error"
    | "workflow_complete";
  content: string;
  timestamp: number;
  metadata?: {
    node_id?: string;
    tool_name?: string;
    stage?: string;
    summary?: string;
    options?: string[];
    prompt?: string;
    /** Stable Project identifier carried from the WS handoff_required event. */
    project_uuid?: string;
    /** Cognito group of the segment that just halted (registry group name). */
    from_role?: string;
    /** Cognito group of the next-owning persona (registry group name). */
    to_role?: string;
  };
  resolved?: boolean;
}

export interface ArtifactFile {
  filePath: string;
  content: string | null;
  objectUrl: string | null;   // Blob URL for binary files (images)
  loading: boolean;
  error: string | null;
}
