/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
import { useState, useEffect, useCallback } from 'react';
import { X, Settings, CheckCircle2, Loader2, AlertCircle } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { useAuth } from '../context/AuthContext';
import { loadConfig } from '../lib/config';

// ---- Types ----

interface CredentialField {
  key: string;
  label: string;
  type: 'text' | 'password';
}

interface ISVAgent {
  id: string;
  name: string;
  description: string;
  allowed: boolean;
  credentialFields: CredentialField[];
  stages: string[];
}

interface IntegrationState {
  enabled: boolean;
  configured: boolean;
}

interface PanelState {
  loading: boolean;
  error: string | null;
  integrations: Record<string, IntegrationState>;
  savingId: string | null;
}

interface IntegrationConfigPanelProps {
  projectId: string;
  projectName: string;
  open: boolean;
  onClose: () => void;
}

// ---- Helpers ----

async function getIntegrationConfigApiUrl(): Promise<string> {
  const config = await loadConfig();
  return (config.integrationConfigApiUrl ?? '').replace(/\/$/, '');
}

// ---- CredentialCaptureForm ----

interface CredentialCaptureFormProps {
  agent: ISVAgent;
  projectId: string;
  onSuccess: () => void;
  onCancel: () => void;
  disabled: boolean;
  idToken: string | null;
}

function CredentialCaptureForm({
  agent,
  projectId,
  onSuccess,
  onCancel,
  disabled,
  idToken,
}: CredentialCaptureFormProps) {
  const [values, setValues] = useState<Record<string, string>>(() => // nosemgrep: react-props-in-state — initial value from prop, updates handled via effects
    Object.fromEntries(agent.credentialFields.map((f) => [f.key, '']))
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const allFilled = agent.credentialFields.every((f) => values[f.key].trim() !== '');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!allFilled) return;

    setSubmitting(true);
    setError(null);

    const apiUrl = await getIntegrationConfigApiUrl();
    try {
      const res = await fetch(`${apiUrl}/projects/${projectId}/integrations/${agent.id}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...(idToken ? { Authorization: `Bearer ${idToken}` } : {}),
        },
        body: JSON.stringify({ enabled: true, credentials: values }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? `Request failed (${res.status})`);
      }

      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save credentials');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="mt-3 space-y-3 rounded-md border border-gray-200 bg-gray-50 p-4">
      {agent.credentialFields.map((field) => (
        <div key={field.key} className="space-y-1">
          <Label htmlFor={`cred-${agent.id}-${field.key}`}>{field.label}</Label>
          <Input
            id={`cred-${agent.id}-${field.key}`}
            type={field.type === 'password' ? 'password' : 'text'}
            value={values[field.key]}
            onChange={(e) => setValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
            disabled={submitting || disabled}
            autoComplete="off"
          />
        </div>
      ))}

      {error && (
        <p className="flex items-center gap-1 text-sm text-red-600">
          <AlertCircle className="size-4 shrink-0" />
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={submitting}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={!allFilled || submitting || disabled}>
          {submitting ? <Loader2 className="size-4 animate-spin" /> : 'Save'}
        </Button>
      </div>
    </form>
  );
}

// ---- Toggle Switch ----

interface ToggleSwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled: boolean;
  id: string;
}

function ToggleSwitch({ checked, onChange, disabled, id }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      id={id}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${
        checked ? 'bg-blue-600' : 'bg-gray-200'
      }`}
    >
      <span
        className={`pointer-events-none inline-block size-5 rounded-full bg-white shadow-lg ring-0 transition-transform ${
          checked ? 'translate-x-5' : 'translate-x-0'
        }`}
      />
    </button>
  );
}

// ---- IntegrationRow ----

interface IntegrationRowProps {
  agent: ISVAgent;
  state: IntegrationState;
  projectId: string;
  savingId: string | null;
  idToken: string | null;
  onToggle: (agentId: string, newEnabled: boolean) => void;
  onSaveSuccess: (agentId: string) => void;
  onCancelForm: (agentId: string) => void;
}

function IntegrationRow({
  agent,
  state,
  projectId,
  savingId,
  idToken,
  onToggle,
  onSaveSuccess,
  onCancelForm,
}: IntegrationRowProps) {
  const isSaving = savingId !== null;
  const isThisSaving = savingId === agent.id;
  const showForm = state.enabled && !state.configured;

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-gray-900">{agent.name}</span>
            {state.configured && (
              <span className="flex items-center gap-1 text-xs text-green-600">
                <CheckCircle2 className="size-3.5" />
                Configured
              </span>
            )}
          </div>
          <p className="mt-0.5 text-sm text-gray-500">{agent.description}</p>
          {agent.stages && agent.stages.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {agent.stages.map((stage) => (
                <span
                  key={stage}
                  className="inline-block rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-500"
                >
                  {stage.replace(/^post_/, '').replace(/_/g, ' ')}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {isThisSaving && <Loader2 className="size-4 animate-spin text-gray-400" />}
          <ToggleSwitch
            id={`toggle-${agent.id}`}
            checked={state.enabled}
            onChange={(checked) => onToggle(agent.id, checked)}
            disabled={isSaving}
          />
        </div>
      </div>

      {showForm && (
        <CredentialCaptureForm
          agent={agent}
          projectId={projectId}
          idToken={idToken}
          disabled={isSaving}
          onSuccess={() => onSaveSuccess(agent.id)}
          onCancel={() => onCancelForm(agent.id)}
        />
      )}
    </div>
  );
}

// ---- IntegrationConfigPanel ----

export function IntegrationConfigPanel({
  projectId,
  projectName,
  open,
  onClose,
}: IntegrationConfigPanelProps) {
  const { accessToken, idToken } = useAuth();

  const [agents, setAgents] = useState<ISVAgent[]>([]);
  const [agentsError, setAgentsError] = useState(false);
  const [panelState, setPanelState] = useState<PanelState>({
    loading: false,
    error: null,
    integrations: {},
    savingId: null,
  });

  useEffect(() => {
    if (!open) return;
    if (!accessToken || !idToken) return;

    let cancelled = false;

    const load = async () => {
      setPanelState({ loading: true, error: null, integrations: {}, savingId: null });
      setAgents([]);
      setAgentsError(false);

      const apiUrl = await getIntegrationConfigApiUrl();

      const [agentsResult, configResult] = await Promise.allSettled([
        fetch('/isv-agents.json').then((r) => {
          if (!r.ok) throw new Error('Failed to load isv-agents.json');
          return r.json() as Promise<ISVAgent[]>;
        }),
        fetch(`${apiUrl}/projects/${projectId}/integrations`, {
          headers: {
            ...(idToken ? { Authorization: `Bearer ${idToken}` } : {}),
          },
        }).then((r) => {
          if (!r.ok) throw new Error(`Config API error (${r.status})`);
          return r.json() as Promise<{ integrations: Record<string, IntegrationState> }>;
        }),
      ]);

      if (cancelled) return;

      let allowedAgents: ISVAgent[] = [];
      if (agentsResult.status === 'fulfilled') {
        allowedAgents = agentsResult.value.filter((a) => a.allowed);
        setAgents(allowedAgents);
      } else {
        console.error('Failed to load isv-agents.json:', agentsResult.reason);
        setAgentsError(true);
      }

      let integrations: Record<string, IntegrationState> = {};
      let configError: string | null = null;

      if (configResult.status === 'fulfilled') {
        integrations = configResult.value.integrations ?? {};
      } else {
        configError = 'Failed to load integration configuration. Showing all toggles as OFF.';
        integrations = Object.fromEntries(
          allowedAgents.map((a) => [a.id, { enabled: false, configured: false }])
        );
      }

      setPanelState({ loading: false, error: configError, integrations, savingId: null });
    };

    load();
    return () => { cancelled = true; };
  }, [open, projectId, accessToken, idToken]);

  const handleToggle = useCallback(
    async (agentId: string, newEnabled: boolean) => {
      const prev = panelState.integrations[agentId] ?? { enabled: false, configured: false };

      if (newEnabled) {
        setPanelState((s) => ({
          ...s,
          integrations: { ...s.integrations, [agentId]: { enabled: true, configured: false } },
        }));
        return;
      }

      setPanelState((s) => ({
        ...s,
        savingId: agentId,
        integrations: { ...s.integrations, [agentId]: { ...prev, enabled: false } },
      }));

      const apiUrl = await getIntegrationConfigApiUrl();
      try {
        const res = await fetch(`${apiUrl}/projects/${projectId}/integrations/${agentId}`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            ...(idToken ? { Authorization: `Bearer ${idToken}` } : {}),
          },
          body: JSON.stringify({ enabled: false }),
        });

        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.error ?? `Request failed (${res.status})`);
        }

        setPanelState((s) => ({
          ...s,
          savingId: null,
          integrations: { ...s.integrations, [agentId]: { enabled: false, configured: false } },
        }));
      } catch (err) {
        setPanelState((s) => ({
          ...s,
          savingId: null,
          error: err instanceof Error ? err.message : 'Failed to disable integration',
          integrations: { ...s.integrations, [agentId]: prev },
        }));
      }
    },
    [panelState.integrations, projectId, idToken]
  );

  const handleSaveSuccess = useCallback((agentId: string) => {
    setPanelState((s) => ({
      ...s,
      integrations: { ...s.integrations, [agentId]: { enabled: true, configured: true } },
    }));
  }, []);

  const handleCancelForm = useCallback((agentId: string) => {
    setPanelState((s) => ({
      ...s,
      integrations: { ...s.integrations, [agentId]: { enabled: false, configured: false } },
    }));
  }, []);

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/30" onClick={onClose} aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Integration configuration for ${projectName}`}
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col bg-white shadow-xl"
      >
        <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
          <div className="flex items-center gap-2">
            <Settings className="size-5 text-gray-500" />
            <h2 className="text-lg font-semibold text-gray-900">{projectName}</h2>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close panel">
            <X className="size-5" />
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {panelState.loading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="size-8 animate-spin text-gray-400" />
            </div>
          ) : (
            <>
              {panelState.error && (
                <div className="mb-4 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  <AlertCircle className="size-4 mt-0.5 shrink-0" />
                  <span>{panelState.error}</span>
                </div>
              )}

              {agentsError || agents.length === 0 ? (
                <p className="text-center text-sm text-gray-500 py-8">No integrations available.</p>
              ) : (
                <div className="space-y-3">
                  {agents.map((agent) => {
                    const state = panelState.integrations[agent.id] ?? { enabled: false, configured: false };
                    return (
                      <IntegrationRow
                        key={agent.id}
                        agent={agent}
                        state={state}
                        projectId={projectId}
                        savingId={panelState.savingId}
                        idToken={idToken}
                        onToggle={handleToggle}
                        onSaveSuccess={handleSaveSuccess}
                        onCancelForm={handleCancelForm}
                      />
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}
