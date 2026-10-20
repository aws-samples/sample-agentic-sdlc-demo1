/**
 * Parses aidlc-state.md markdown content into structured progress data.
 */

export interface AidlcStep {
  name: string;
  status: 'completed' | 'in-progress' | 'pending' | 'skipped';
  detail: string;
}

export interface AidlcPhase {
  name: string;
  steps: AidlcStep[];
}

export interface AidlcProgress {
  phases: AidlcPhase[];
  currentStage: string | null;
  projectName: string | null;
}

export function parseAidlcState(markdown: string): AidlcProgress | null {
  if (!markdown) return null;

  const lines = markdown.split('\n');
  const phases: AidlcPhase[] = [];
  let currentPhase: AidlcPhase | null = null;
  let currentStage: string | null = null;
  let projectName: string | null = null;

  for (const line of lines) {
    const trimmed = line.trim();

    // Extract project name
    const nameMatch = trimmed.match(/^\*?\*?Project Name\*?\*?:\s*(.+)/);
    if (nameMatch) {
      projectName = nameMatch[1].trim();
    }

    // Extract current stage
    const stageMatch = trimmed.match(/^\*?\*?Current Stage\*?\*?:\s*(.+)/);
    if (stageMatch) {
      currentStage = stageMatch[1].trim();
    }

    // Phase headers: ### INCEPTION PHASE, ### CONSTRUCTION PHASE, etc.
    const phaseMatch = trimmed.match(/^###\s+(.+?)\s+PHASE/i);
    if (phaseMatch) {
      currentPhase = { name: phaseMatch[1].trim(), steps: [] };
      phases.push(currentPhase);
      continue;
    }

    // Step items: - [x] Name or - [ ] Name
    if (currentPhase && trimmed.match(/^-\s+\[/)) {
      const checked = trimmed.match(/^-\s+\[x\]/i) !== null;
      const inProgressMark = trimmed.match(/^-\s+\[~\]/) !== null;
      const textAfterCheckbox = trimmed.replace(/^-\s+\[.?\]\s*/, '');

      let status: AidlcStep['status'];
      let detail = '';

      if (checked) {
        status = 'completed';
      } else if (inProgressMark) {
        status = 'in-progress';
      } else if (/SKIP/i.test(textAfterCheckbox)) {
        status = 'skipped';
        const skipMatch = textAfterCheckbox.match(/SKIP\s*\(([^)]+)\)/i);
        if (skipMatch) detail = skipMatch[1];
      } else if (/IN\s*PROGRESS/i.test(textAfterCheckbox)) {
        status = 'in-progress';
        const progressMatch = textAfterCheckbox.match(/IN\s*PROGRESS\s*\(([^)]+)\)/i);
        if (progressMatch) detail = progressMatch[1];
      } else if (/PLACEHOLDER/i.test(textAfterCheckbox)) {
        status = 'pending';
      } else {
        status = 'pending';
      }

      // Extract step name (before any status annotation)
      const name = textAfterCheckbox
        .replace(
          /\s*[-–]\s*(COMPLETED|SKIP(\s*\([^)]*\))?|IN\s*PROGRESS(\s*\([^)]*\))?|PENDING|PLACEHOLDER).*/i,
          '',
        )
        .replace(/\s*\(COMPLETED\)/i, '')
        .trim();

      currentPhase.steps.push({ name, status, detail });
    }
  }

  if (phases.length === 0) return null;

  return { phases, currentStage, projectName };
}
