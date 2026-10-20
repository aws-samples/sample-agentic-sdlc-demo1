/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
/**
 * AdminDashboard — read-only, cross-project operations view for the
 * Maintenance Specialist persona.
 *
 * Lists every project in the shared Project Store (via the existing broad
 * `listProjects()` call) with the persona it is currently assigned to and the
 * stage it is in (e.g. "Assigned to Business Analyst" / "Assigned to Solution
 * Architect", and any future roles as the registry grows). Also shows a
 * per-persona workload summary and status rollup.
 *
 * Access is gated to the `maintenance-specialist` Cognito group. The gate is
 * presentational; the underlying `GET /projects` is already broadly readable,
 * so this view exposes nothing new — it just presents it for an operator.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { ArrowLeft, LogOut, Loader2, AlertCircle, LayoutDashboard, RefreshCw } from 'lucide-react';

import { useAuth } from '../context/AuthContext';
import { Button } from './ui/button';
import { Card, CardHeader, CardTitle, CardDescription } from './ui/card';
import { listProjects, type ProjectRecord } from '../services/projectService';
import {
  buildProjectRows,
  summarizeByPersona,
  summarizeTotals,
  stageLabel,
} from '../services/dashboardMetrics';

const MAINTENANCE_GROUP = 'maintenance-specialist';

export function AdminDashboard() {
  const { isAuthenticated, isLoading, user, signOut } = useAuth();
  const navigate = useNavigate();

  const [records, setRecords] = useState<ProjectRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isMaintenance = !!user?.groups?.includes(MAINTENANCE_GROUP);

  // Redirect unauthenticated users to sign-in (mirrors LandingPage).
  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/sign-in');
    }
  }, [isLoading, isAuthenticated, navigate]);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listProjects()
      .then((recs) => {
        if (!cancelled) setRecords(recs);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load projects');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isAuthenticated || !isMaintenance) return;
    const cleanup = load();
    return cleanup;
  }, [isAuthenticated, isMaintenance, load]);

  const rows = useMemo(() => buildProjectRows(records), [records]);
  const personas = useMemo(() => summarizeByPersona(records), [records]);
  const totals = useMemo(() => summarizeTotals(records), [records]);

  const handleSignOut = useCallback(async () => {
    await signOut();
    navigate('/');
  }, [signOut, navigate]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50">
        <Loader2 className="size-6 animate-spin text-gray-400" />
      </div>
    );
  }

  // Persona gate: only the Maintenance Specialist may view the dashboard.
  if (isAuthenticated && !isMaintenance) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-gray-50 px-4 text-center">
        <AlertCircle className="size-8 text-amber-500" />
        <h1 className="text-lg font-semibold text-gray-900">Dashboard unavailable</h1>
        <p className="max-w-md text-sm text-gray-600">
          The operations dashboard is available to the Maintenance Specialist role only.
        </p>
        <Button variant="outline" onClick={() => navigate('/')}>
          <ArrowLeft className="mr-2 size-4" />
          Back to projects
        </Button>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow-sm">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4">
          <div className="flex items-center gap-2">
            <LayoutDashboard className="size-5 text-gray-700" aria-hidden="true" />
            <h1 className="text-2xl font-bold text-gray-900">Operations Dashboard</h1>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <p className="text-sm font-medium text-gray-900">{user?.username}</p>
              {user?.role && <p className="text-xs text-gray-500">{user.role}</p>}
            </div>
            <Button variant="outline" size="sm" onClick={() => navigate('/')}>
              <ArrowLeft className="mr-2 size-4" />
              Projects
            </Button>
            <Button variant="outline" size="sm" onClick={handleSignOut}>
              <LogOut className="mr-2 size-4" />
              Sign Out
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-8">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-gray-900">
            All projects ({totals.totalProjects})
          </h2>
          <Button variant="outline" size="sm" onClick={() => load()} disabled={loading}>
            <RefreshCw className={`mr-2 size-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>

        {error && (
          <div
            role="alert"
            className="mb-4 flex items-center gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700"
          >
            <AlertCircle className="size-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {loading && records.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <Loader2 className="size-4 animate-spin" /> Loading projects…
          </div>
        ) : (
          <>
            {/* Persona workload summary */}
            <section className="mb-8" aria-label="Persona summary">
              <h3 className="mb-2 text-sm font-semibold text-gray-700">By persona</h3>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                {personas.map((p) => (
                  <Card key={p.role} className="p-3">
                    <CardHeader className="p-0">
                      <CardTitle className="text-2xl font-bold text-gray-900">
                        {p.owned}
                      </CardTitle>
                      <CardDescription className="text-xs text-gray-600">
                        {p.label}
                      </CardDescription>
                    </CardHeader>
                  </Card>
                ))}
              </div>
            </section>

            {/* Status rollup */}
            <section className="mb-8" aria-label="Status summary">
              <h3 className="mb-2 text-sm font-semibold text-gray-700">By stage</h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(totals.byState).map(([state, count]) => (
                  <span
                    key={state}
                    className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700"
                  >
                    {stageLabel(state)}
                    <span className="rounded-full bg-gray-100 px-1.5 text-gray-900">{count}</span>
                  </span>
                ))}
              </div>
            </section>

            {/* Projects table */}
            <section aria-label="Projects">
              <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
                <table className="min-w-full divide-y divide-gray-200 text-sm">
                  <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-4 py-3">Project</th>
                      <th className="px-4 py-3">Created by</th>
                      <th className="px-4 py-3">Stage</th>
                      <th className="px-4 py-3">Assigned to</th>
                      <th className="px-4 py-3">Repository</th>
                      <th className="px-4 py-3">Transitions</th>
                      <th className="px-4 py-3">Last activity</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {rows.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="px-4 py-6 text-center text-gray-500">
                          No projects yet.
                        </td>
                      </tr>
                    ) : (
                      rows.map((row) => (
                        <tr key={row.projectUuid} className="hover:bg-gray-50">
                          <td className="px-4 py-3 font-medium text-gray-900">{row.name}</td>
                          <td className="px-4 py-3 text-gray-600">{row.createdBy}</td>
                          <td className="px-4 py-3 text-gray-700">{row.stage}</td>
                          <td className="px-4 py-3 text-gray-700">{row.assignedTo}</td>
                          <td className="px-4 py-3 text-gray-700">
                            {row.repoStatus === 'not_linked' || !row.repoUrl ? (
                              <span className="text-gray-400">—</span>
                            ) : (
                              <span
                                title={row.repoError ?? row.repoUrl ?? ''}
                                className={
                                  row.repoStatus === 'ready'
                                    ? 'text-emerald-700'
                                    : row.repoStatus === 'failed'
                                      ? 'text-red-700'
                                      : 'text-amber-700'
                                }
                              >
                                {row.repoStatus}
                                {row.repoCommit ? ` · ${row.repoCommit.slice(0, 7)}` : ''}
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-gray-700">{row.transitions}</td>
                          <td className="px-4 py-3 text-gray-500">
                            {row.lastActivity
                              ? new Date(row.lastActivity).toLocaleString()
                              : '—'}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
