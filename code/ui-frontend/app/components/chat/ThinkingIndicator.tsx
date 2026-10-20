import { Loader2 } from "lucide-react";

export const TOOL_LABELS: Record<string, string> = {
  analyze_request: "Analyzing request…",
  load_prior_context: "Loading prior context…",
  assess_stages: "Assessing workflow stages…",
  assess_story_need: "Evaluating user story needs…",
  generate_requirements: "Generating requirements…",
  generate_design_artifacts: "Generating design artifacts…",
  generate_execution_plan: "Generating execution plan…",
  generate_mermaid_diagram: "Creating diagram…",
  generate_stories: "Generating user stories…",
  generate_unit_artifacts: "Generating unit artifacts…",
  generate_functional_artifacts: "Generating functional design…",
  generate_nfr_requirements: "Generating NFR requirements…",
  generate_infra_artifacts: "Generating infrastructure design…",
  create_questions_file: "Preparing questions…",
  read_answers: "Reading prior answers…",
  detect_ambiguities: "Checking for ambiguities…",
  validate_mermaid: "Validating diagram…",
  scan_workspace: "Scanning workspace…",
  decompose_units: "Decomposing into units…",
  present_approval_gate: "Preparing for your review…",
  log_audit_entry: "Logging audit entry…",
  write_artifact: "Writing artifact…",
  create_code_plan: "Creating code plan…",
  generate_code: "Generating code…",
  generate_tests: "Generating tests…",
};

export function ThinkingIndicator({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2.5 px-4 py-1.5 text-sm text-blue-600 dark:text-blue-400">
      <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
      <span className="font-medium">{label}</span>
      <span className="flex gap-0.5">
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-blue-400 [animation-delay:0ms]" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-blue-400 [animation-delay:150ms]" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-blue-400 [animation-delay:300ms]" />
      </span>
    </div>
  );
}
