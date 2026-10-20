/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
import { useState, useEffect, useCallback } from 'react';
import { ChevronRight, ChevronDown, Loader2, ExternalLink } from 'lucide-react';
import { listFiles, getFileContent } from '../../services/s3Service';
import { parseAidlcState, type AidlcPhase, type AidlcProgress, type AidlcStep } from '../../services/aidlcParser';
import { getConfig } from '../../lib/config';

interface ProgressPanelProps {
  refreshTrigger: number;
  projectPrefix?: string;
  onProgressLoad?: (progress: AidlcProgress | null) => void;
  className?: string;
}

const STATUS_CONFIG: Record<AidlcStep['status'], { icon: string; color: string }> = {
  completed: { icon: '\u2713', color: 'text-green-600 dark:text-green-400' },
  'in-progress': { icon: '\u25CF', color: 'text-blue-600 dark:text-blue-400' },
  pending: { icon: '\u25CB', color: 'text-gray-400 dark:text-gray-500' },
  skipped: { icon: '\u2298', color: 'text-gray-300 dark:text-gray-600' },
};

function isInceptionComplete(progress: AidlcProgress): boolean {
  const inceptionPhase = progress.phases.find(
    (p) => p.name.toUpperCase() === 'INCEPTION',
  );
  if (!inceptionPhase || inceptionPhase.steps.length === 0) return false;
  const activeSteps = inceptionPhase.steps.filter((s) => s.status !== 'skipped');
  return activeSteps.length > 0 && activeSteps.every((s) => s.status === 'completed');
}

function PhaseSection({ phase }: { phase: AidlcPhase }) {
  const [expanded, setExpanded] = useState(true);

  const completedCount = phase.steps.filter((s) => s.status === 'completed').length;
  const activeCount = phase.steps.filter((s) => s.status !== 'skipped').length;
  const hasInProgress = phase.steps.some((s) => s.status === 'in-progress');
  const allDone = activeCount > 0 && completedCount === activeCount;

  let phaseStatus: 'completed' | 'in-progress' | 'pending' = 'pending';
  if (allDone) phaseStatus = 'completed';
  else if (completedCount > 0 || hasInProgress) phaseStatus = 'in-progress';

  const phaseColors = {
    completed: 'text-green-700 dark:text-green-400',
    'in-progress': 'text-blue-700 dark:text-blue-400',
    pending: 'text-gray-500 dark:text-gray-400',
  };

  return (
    <div className="border-b border-gray-100 last:border-b-0 dark:border-gray-800">
      <button
        onClick={() => setExpanded(!expanded)}
        className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium transition-colors hover:bg-gray-50 dark:hover:bg-gray-800/50 ${phaseColors[phaseStatus]}`}
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0" />
        )}
        <span className="flex-1 truncate">{phase.name}</span>
        {phaseStatus === 'completed' && (
          <span className="rounded-full bg-green-100 px-1.5 py-0.5 text-[10px] font-semibold text-green-700 dark:bg-green-900/30 dark:text-green-400">
            {'\u2713'}
          </span>
        )}
        {phaseStatus === 'in-progress' && (
          <span className="rounded-full bg-blue-100 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
            {completedCount}/{activeCount}
          </span>
        )}
      </button>
      {expanded && (
        <div className="space-y-0.5 pb-2 pl-6 pr-3">
          {phase.steps.map((step, i) => {
            const { icon, color } = STATUS_CONFIG[step.status];
            return (
              <div key={i} className="flex items-start gap-2 py-0.5 text-xs">
                <span className={`shrink-0 font-mono ${color}`}>{icon}</span>
                <span className="text-gray-600 dark:text-gray-400">{step.name}</span>
                {step.status === 'skipped' && (
                  <span className="text-[10px] italic text-gray-300 dark:text-gray-600">skipped</span>
                )}
                {step.status === 'in-progress' && step.detail && (
                  <span className="text-[10px] italic text-blue-500">{step.detail}</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function OpenIdeButton() {
  const config = getConfig();
  if (!config.ideUrl) return null;

  return (
    <div className="border-t border-gray-200 px-3 py-3 dark:border-gray-700">
      <a
        href={config.ideUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="flex w-full items-center justify-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-blue-700"
      >
        <ExternalLink className="h-3.5 w-3.5" />
        Open IDE
      </a>
      <p className="mt-1.5 text-center text-[10px] text-gray-400 dark:text-gray-500">
        Start the construction phase
      </p>
    </div>
  );
}

export function ProgressPanel({ refreshTrigger, projectPrefix, onProgressLoad, className }: ProgressPanelProps) {
  const [progress, setProgress] = useState<AidlcProgress | null>(null);
  const [loading, setLoading] = useState(false);

  // Reset progress when project changes
  useEffect(() => {
    setProgress(null);
  }, [projectPrefix]);

  const loadProgress = useCallback(async () => {
    if (!projectPrefix) return; // Don't load without a project scope
    setLoading(true);
    try {
      const objects = await listFiles(`${projectPrefix}/`);
      const stateFile = objects.find((o) => o.Key.endsWith('/aidlc-state.md'));
      if (!stateFile) {
        setProgress(null);
        onProgressLoad?.(null);
        return;
      }
      const content = await getFileContent(stateFile.Key);
      const parsed = parseAidlcState(content);
      setProgress(parsed);
      onProgressLoad?.(parsed);
    } catch (err) {
      console.error('Failed to load progress:', err);
      setProgress(null);
      onProgressLoad?.(null);
    } finally {
      setLoading(false);
    }
  }, [projectPrefix, onProgressLoad]);

  // Initial load
  useEffect(() => {
    loadProgress();
  }, [loadProgress]);

  // Refresh when agent sends a message (with 10s delay to allow backend processing)
  useEffect(() => {
    if (refreshTrigger > 0) {
      const timeout = setTimeout(loadProgress, 5000);
      return () => clearTimeout(timeout);
    }
  }, [refreshTrigger, loadProgress]);

  // Auto-refresh every 30 seconds
  useEffect(() => {
    const interval = setInterval(loadProgress, 30000);
    return () => clearInterval(interval);
  }, [loadProgress]);

  const showIdeButton = progress != null && isInceptionComplete(progress);

  return (
    <div className={`flex h-full flex-col overflow-hidden ${className ?? ''}`}>
      {/* Header */}
      <div className="shrink-0 border-b border-gray-200 px-3 py-2 dark:border-gray-700">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
          Progress
        </h4>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {loading && !progress && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
          </div>
        )}

        {!loading && !progress && (
          <div className="flex flex-col items-center gap-2 px-3 py-6 text-center">
            <span className="text-2xl">📋</span>
            <p className="text-xs text-gray-400">Start a conversation to begin your project.</p>
          </div>
        )}

        {progress && (
          <div>
            {progress.phases.map((phase, i) => (
              <PhaseSection key={i} phase={phase} />
            ))}
          </div>
        )}
      </div>

      {/* Open IDE button — shown when inception phase is complete */}
      {showIdeButton && <OpenIdeButton />}
    </div>
  );
}
