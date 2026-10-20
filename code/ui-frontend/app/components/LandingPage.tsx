/* nosemgrep: jsx-not-internationalized — internal demo app, i18n not required */
import { useAuth } from '../context/AuthContext';
import { Button } from './ui/button';
import { Card, CardFooter, CardHeader, CardTitle, CardDescription } from './ui/card';
import { useNavigate } from 'react-router';
import { Plus, LogOut, Trash2, Loader2, FolderOpen, Settings, AlertCircle, LayoutDashboard } from 'lucide-react';
import { useState, useEffect, useCallback } from 'react';
import { CreateProjectDialog } from './CreateProjectDialog';
import { IntegrationConfigPanel } from './IntegrationConfigPanel';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './ui/alert-dialog';
import {
  listProjects,
  createProject,
  deleteProject as deleteProjectApi,
  ProjectApiError,
  type ProjectRecord,
} from '../services/projectService';
import { HandoffStatusBadge } from './HandoffStatusBadge';

/**
 * Surfaces the shared, server-side project record on the project list.
 *
 * The Frontend used to track projects in `localStorage` keyed by
 * `projects_${user.sub}`; that store has been replaced by the Project API
 * (design Section 2d, Req 3.4), so the field names here mirror the wire
 * record (`project_uuid`/`name`/`description`/`type`/`created_at`) instead of
 * the previous browser-shaped `id`/`createdAt` pair.
 */
export type Project = ProjectRecord;

export function LandingPage() {
  const { isAuthenticated, isLoading, user, signOut } = useAuth();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [deleteProjectId, setDeleteProjectId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [integrationPanelProject, setIntegrationPanelProject] = useState<Project | null>(null);

  // Fetch the project list from the Project API (design Section 2d, Req 3.4).
  // Replaces the prior `localStorage.getItem('projects_${user.sub}')` read.
  useEffect(() => {
    if (!isAuthenticated || !user) return;
    let cancelled = false;
    setProjectsLoading(true);
    setProjectsError(null);
    listProjects()
      .then((records) => {
        if (cancelled) return;
        setProjects(records);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof ProjectApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : 'Failed to load projects';
        setProjectsError(message);
      })
      .finally(() => {
        if (!cancelled) setProjectsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, user]);

  const handleCreateProject = useCallback(
    async (name: string, description: string, type: string) => {
      if (!user) return;
      setCreateError(null);
      try {
        const created = await createProject({
          name,
          description,
          type,
          // Mint a stable UUID client-side so URLs are stable across the
          // round-trip (matches the prior localStorage behavior).
          project_uuid: crypto.randomUUID(),
        });
        setProjects((prev) => [...prev, created]);
        setIsCreateDialogOpen(false);
      } catch (err: unknown) {
        const message =
          err instanceof ProjectApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : 'Failed to create project';
        setCreateError(message);
      }
    },
    [user],
  );

  const handleDeleteProject = useCallback(
    async (projectUuid: string) => {
      if (!user) return;
      setDeleteError(null);
      try {
        await deleteProjectApi(projectUuid);
        setProjects((prev) => prev.filter((p) => p.project_uuid !== projectUuid));
        // The chat/document caches are still per-browser and are not part of
        // the Project_Store (design Section 2d): clean them up here so a
        // newly-created project with the same uuid does not inherit stale data.
        localStorage.removeItem(`messages_${projectUuid}`);
        localStorage.removeItem(`document_${projectUuid}`);
        setDeleteProjectId(null);
      } catch (err: unknown) {
        const message =
          err instanceof ProjectApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : 'Failed to delete project';
        setDeleteError(message);
      }
    },
    [user],
  );

  const handleSignOut = async () => {
    await signOut();
    setProjects([]);
  };

  // Loading state
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-indigo-100">
        <Loader2 className="size-8 animate-spin text-primary" />
      </div>
    );
  }

  // Unauthenticated welcome screen — fancy "Agentic SDLC Platform" hero.
  // The title and the gradient "Sign In" button are real, accessible elements
  // (not a baked-in image), so they stay responsive, focusable, and themeable.
  // The Sign In button keeps the existing login wiring: navigate('/sign-in')
  // routes to SignInPage, whose form calls signIn() -> Amplify/Cognito.
  if (!isAuthenticated) {
    return (
      <div
        className="relative min-h-screen flex flex-col items-center justify-center overflow-hidden px-4"
        style={{
          backgroundColor: '#070b22',
          // Layered: teal/cyan glows + a faint circuit-board grid + a deep
          // navy-to-teal base gradient (last entry paints at the bottom).
          backgroundImage: [
            'radial-gradient(circle at 72% 28%, rgba(20, 184, 166, 0.28), transparent 45%)',
            'radial-gradient(circle at 28% 72%, rgba(56, 189, 248, 0.20), transparent 42%)',
            'repeating-linear-gradient(0deg, rgba(56, 189, 248, 0.05) 0 1px, transparent 1px 42px)',
            'repeating-linear-gradient(90deg, rgba(56, 189, 248, 0.05) 0 1px, transparent 1px 42px)',
            'linear-gradient(135deg, #070b22 0%, #0b1437 45%, #0a2a3f 100%)',
          ].join(', '),
        }}
      >
        <div className="relative z-10 flex flex-col items-center gap-10 text-center">
          <h1
            className="text-5xl font-bold tracking-tight text-white sm:text-6xl md:text-7xl"
            style={{ textShadow: '0 2px 30px rgba(56, 189, 248, 0.45)' }}
          >
            Agentic SDLC Platform
          </h1>
          <Button
            size="lg"
            onClick={() => navigate('/sign-in')}
            className="rounded-full border-0 px-10 py-6 text-base font-semibold text-white shadow-lg transition-transform hover:scale-105 focus-visible:scale-105"
            style={{
              background: 'linear-gradient(90deg, #06b6d4 0%, #14b8a6 100%)',
              boxShadow: '0 10px 30px rgba(20, 184, 166, 0.45)',
            }}
          >
            Sign In
          </Button>
        </div>
      </div>
    );
  }

  // Authenticated view with project grid
  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow-sm">
        <div className="max-w-7xl mx-auto px-4 py-4 flex justify-between items-center">
          <h1 className="text-2xl font-bold text-gray-900">My Projects</h1>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <p className="text-sm font-medium text-gray-900">{user?.username}</p>
              {user?.role && (
                <p className="text-xs text-gray-500">{user.role}</p>
              )}
            </div>
            {user?.groups?.includes('maintenance-specialist') && (
              <Button variant="outline" size="sm" onClick={() => navigate('/dashboard')}>
                <LayoutDashboard className="size-4 mr-2" />
                Dashboard
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={handleSignOut}>
              <LogOut className="size-4 mr-2" />
              Sign Out
            </Button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-8">
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-xl font-semibold text-gray-900">Your Projects</h2>
          <Button onClick={() => setIsCreateDialogOpen(true)}>
            <Plus className="size-4 mr-2" />
            Create Project
          </Button>
        </div>

        {projectsError && (
          <div
            role="alert"
            className="mb-4 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700"
          >
            <AlertCircle className="size-4 mt-0.5 shrink-0" />
            <span>{projectsError}</span>
          </div>
        )}
        {deleteError && (
          <div
            role="alert"
            className="mb-4 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700"
          >
            <AlertCircle className="size-4 mt-0.5 shrink-0" />
            <span>{deleteError}</span>
          </div>
        )}

        {projectsLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="size-8 animate-spin text-gray-400" />
          </div>
        ) : projects.length === 0 ? (
          <div className="text-center py-16">
            <FolderOpen className="size-16 mx-auto text-gray-300 mb-4" />
            <h3 className="text-lg font-medium text-gray-900 mb-2">No projects yet</h3>
            <p className="text-gray-500 mb-6">Create your first project to get started!</p>
            <Button onClick={() => setIsCreateDialogOpen(true)}>
              <Plus className="size-4 mr-2" />
              Create Your First Project
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {projects.map((project) => (
              <Card
                key={project.project_uuid}
                className="cursor-pointer hover:shadow-lg transition-shadow relative group"
                onClick={() => navigate(`/project/${project.project_uuid}`)}
              >
                <CardHeader>
                  <CardTitle className="text-lg">{project.name}</CardTitle>
                  <CardDescription className="line-clamp-2">{project.description}</CardDescription>
                  <div className="mt-2 flex">
                    <HandoffStatusBadge
                      record={project}
                      userGroups={user?.groups ?? []}
                    />
                  </div>
                </CardHeader>
                <CardFooter className="flex items-center justify-between">
                  <span className="text-xs text-gray-500 bg-gray-100 px-2 py-1 rounded">
                    {project.type}
                  </span>
                  <span className="text-xs text-gray-400">
                    {new Date(project.created_at).toLocaleDateString()}
                  </span>
                </CardFooter>
                <Button
                  variant="ghost"
                  size="icon"
                  className="absolute top-2 right-10 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-blue-50 hover:text-blue-600"
                  onClick={(e: React.MouseEvent) => {
                    e.stopPropagation();
                    setIntegrationPanelProject(project);
                  }}
                >
                  <Settings className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-50 hover:text-red-600"
                  onClick={(e: React.MouseEvent) => {
                    e.stopPropagation();
                    setDeleteProjectId(project.project_uuid);
                  }}
                >
                  <Trash2 className="size-4" />
                </Button>
              </Card>
            ))}
          </div>
        )}
      </main>

      <CreateProjectDialog
        open={isCreateDialogOpen}
        onOpenChange={(open) => {
          setIsCreateDialogOpen(open);
          if (!open) setCreateError(null);
        }}
        onCreateProject={handleCreateProject}
        error={createError}
      />

      <AlertDialog open={deleteProjectId !== null} onOpenChange={() => setDeleteProjectId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Are you absolutely sure?</AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. This will permanently delete the project and all related data.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleteProjectId && handleDeleteProject(deleteProjectId)}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {integrationPanelProject && (
        <IntegrationConfigPanel
          projectId={integrationPanelProject.project_uuid}
          projectName={integrationPanelProject.name}
          open={integrationPanelProject !== null}
          onClose={() => setIntegrationPanelProject(null)}
        />
      )}
    </div>
  );
}
