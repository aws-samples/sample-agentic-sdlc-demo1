import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { LandingPage, type Project } from '../../app/components/landingPage';

// Radix uses scrollIntoView which jsdom doesn't implement
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn().mockReturnValue(false);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
});

// Mock useNavigate
const mockNavigate = vi.fn();
vi.mock('react-router', () => ({
  useNavigate: () => mockNavigate,
}));

// Mock useAuth - default: unauthenticated
const mockSignOut = vi.fn().mockResolvedValue(undefined);
let mockAuthState = {
  isAuthenticated: false,
  isLoading: false,
  user: null as any,
  signOut: mockSignOut,
};

vi.mock('../../app/context/AuthContext', async () => {
  const actual = await vi.importActual('../../app/context/AuthContext');
  return {
    ...actual,
    useAuth: () => mockAuthState,
  };
});

// Mock the projectService — the landing page now reads from the shared,
// server-side Project Store via this client (design Section 2d, Req 3.4).
const mockListProjects = vi.fn();
const mockCreateProject = vi.fn();
const mockDeleteProject = vi.fn();

vi.mock('../../app/services/projectService', () => ({
  listProjects: () => mockListProjects(),
  createProject: (input: unknown) => mockCreateProject(input),
  deleteProject: (uuid: string) => mockDeleteProject(uuid),
  ProjectApiError: class ProjectApiError extends Error {
    status: number;
    code?: string;
    constructor(status: number, message: string, code?: string) {
      super(message);
      this.status = status;
      this.code = code;
    }
  },
}));

const testUser = {
  sub: 'user-sub-abc',
  username: 'testuser',
  email: 'test@example.com',
  groups: ['software-developer'],
  role: 'Software Developer',
};

const testProjects: Project[] = [
  {
    project_uuid: 'proj-1',
    project_id: 'project_alpha',
    name: 'Project Alpha',
    description: 'First project',
    type: 'development',
    created_at: '2024-01-15T10:00:00.000Z',
    created_by: 'test@example.com',
    handoff_state: 'draft',
    owning_role: 'requirements-analyst',
    artifact_prefix: 'project_alpha',
    assignment_history: [],
  },
  {
    project_uuid: 'proj-2',
    project_id: 'project_beta',
    name: 'Project Beta',
    description: 'Second project',
    type: 'research',
    created_at: '2024-02-20T12:00:00.000Z',
    created_by: 'test@example.com',
    handoff_state: 'draft',
    owning_role: 'requirements-analyst',
    artifact_prefix: 'project_beta',
    assignment_history: [],
  },
];

describe('LandingPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('localStorage', {
      store: {} as Record<string, string>,
      getItem(key: string) { return this.store[key] ?? null; },
      setItem(key: string, value: string) { this.store[key] = value; },
      removeItem(key: string) { delete this.store[key]; },
      clear() { this.store = {}; },
    });
    mockAuthState = {
      isAuthenticated: false,
      isLoading: false,
      user: null,
      signOut: mockSignOut,
    };
    // Default: empty list. Individual tests override before render.
    mockListProjects.mockResolvedValue([]);
  });

  describe('Loading state', () => {
    it('shows a loading spinner while auth is loading', () => {
      mockAuthState = { ...mockAuthState, isLoading: true };
      render(<LandingPage />);

      // The Loader2 icon renders as an SVG with animate-spin class
      const spinner = document.querySelector('.animate-spin');
      expect(spinner).toBeInTheDocument();
    });
  });

  describe('Unauthenticated view', () => {
    it('shows welcome screen with app name and description', () => {
      render(<LandingPage />);

      expect(screen.getByText('Agentic SDLC Transformation')).toBeInTheDocument();
      expect(screen.getByText(/Multi-persona, agent-driven collaboration/)).toBeInTheDocument();
    });

    it('shows a Sign In button', () => {
      render(<LandingPage />);

      expect(screen.getByRole('button', { name: 'Sign In' })).toBeInTheDocument();
    });

    it('navigates to /sign-in when Sign In button is clicked', () => {
      render(<LandingPage />);

      fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));

      expect(mockNavigate).toHaveBeenCalledWith('/sign-in');
    });
  });

  describe('Authenticated view - header', () => {
    beforeEach(() => {
      mockAuthState = {
        isAuthenticated: true,
        isLoading: false,
        user: testUser,
        signOut: mockSignOut,
      };
    });

    it('displays the user username', async () => {
      render(<LandingPage />);

      expect(await screen.findByText('testuser')).toBeInTheDocument();
    });

    it('displays the user role', async () => {
      render(<LandingPage />);

      expect(await screen.findByText('Software Developer')).toBeInTheDocument();
    });

    it('shows a Sign Out button', async () => {
      render(<LandingPage />);

      expect(await screen.findByRole('button', { name: /Sign Out/ })).toBeInTheDocument();
    });

    it('calls signOut when Sign Out button is clicked', async () => {
      render(<LandingPage />);

      fireEvent.click(await screen.findByRole('button', { name: /Sign Out/ }));

      await waitFor(() => {
        expect(mockSignOut).toHaveBeenCalled();
      });
    });
  });

  describe('Authenticated view - empty state', () => {
    beforeEach(() => {
      mockAuthState = {
        isAuthenticated: true,
        isLoading: false,
        user: testUser,
        signOut: mockSignOut,
      };
    });

    it('shows empty state CTA when no projects exist', async () => {
      render(<LandingPage />);

      expect(await screen.findByText('No projects yet')).toBeInTheDocument();
      expect(
        screen.getByText('Create your first project to get started!'),
      ).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /Create Your First Project/ }),
      ).toBeInTheDocument();
    });
  });

  describe('Authenticated view - project grid', () => {
    beforeEach(() => {
      mockAuthState = {
        isAuthenticated: true,
        isLoading: false,
        user: testUser,
        signOut: mockSignOut,
      };
      // Project records come from the Project API now (Req 3.4), not from
      // browser localStorage.
      mockListProjects.mockResolvedValue(testProjects);
    });

    it('loads and displays projects from the Project API', async () => {
      render(<LandingPage />);

      expect(await screen.findByText('Project Alpha')).toBeInTheDocument();
      expect(screen.getByText('Project Beta')).toBeInTheDocument();
      expect(mockListProjects).toHaveBeenCalled();
    });

    it('navigates to /project/:projectUuid when a project card is clicked', async () => {
      render(<LandingPage />);

      fireEvent.click(await screen.findByText('Project Alpha'));

      expect(mockNavigate).toHaveBeenCalledWith('/project/proj-1');
    });

    it('shows Create Project button in the header area', async () => {
      render(<LandingPage />);

      // Wait for the project list to settle so we are out of the loading
      // spinner before assertions.
      await screen.findByText('Project Alpha');
      expect(
        screen.getByRole('button', { name: /Create Project/ }),
      ).toBeInTheDocument();
    });
  });

  describe('Project creation', () => {
    beforeEach(() => {
      mockAuthState = {
        isAuthenticated: true,
        isLoading: false,
        user: testUser,
        signOut: mockSignOut,
      };
    });

    it('opens CreateProjectDialog when Create Project is clicked', async () => {
      render(<LandingPage />);

      fireEvent.click(
        await screen.findByRole('button', { name: /Create Your First Project/ }),
      );

      await waitFor(() => {
        expect(screen.getByText('Create New Project')).toBeInTheDocument();
      });
    });
  });
});
