/**
 * Render tests for AdminDashboard — persona gating + project listing.
 *
 * Mocks react-router's useNavigate, AuthContext's useAuth, and the
 * projectService listing, mirroring the patterns in landing-page.test.tsx.
 */

import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockNavigate = vi.fn();
vi.mock('react-router', () => ({
  useNavigate: () => mockNavigate,
}));

const mockSignOut = vi.fn().mockResolvedValue(undefined);
let mockAuthState: {
  isAuthenticated: boolean;
  isLoading: boolean;
  user: { username: string; role: string; groups: string[] } | null;
  signOut: typeof mockSignOut;
};

vi.mock('../../app/context/AuthContext', async () => {
  const actual = await vi.importActual('../../app/context/AuthContext');
  return { ...actual, useAuth: () => mockAuthState };
});

const mockListProjects = vi.fn();
vi.mock('../../app/services/projectService', () => ({
  listProjects: () => mockListProjects(),
  ProjectApiError: class ProjectApiError extends Error {},
}));

// getRegistry() requires runtime config (loaded at app startup) which isn't
// present under vitest; stub it so persona-label resolution falls back to the
// known-role map in dashboardMetrics.
vi.mock('../../app/lib/workflowRegistry', async () => {
  const actual = await vi.importActual('../../app/lib/workflowRegistry');
  return {
    ...actual,
    getRegistry: () => ({
      displayLabel: () => {
        throw new Error('registry not loaded in test');
      },
    }),
  };
});

import { AdminDashboard } from '../../app/components/AdminDashboard';

function project(over: Record<string, unknown> = {}) {
  return {
    project_uuid: 'u1',
    project_id: 'p1',
    name: 'Online Bookstore',
    description: '',
    type: 'development',
    created_at: '2026-01-01T00:00:00Z',
    created_by: 'ba-tester@example.com',
    handoff_state: 'assigned_to_sa',
    owning_role: 'system-architect',
    artifact_prefix: 'online_bookstore',
    assignment_history: [
      { from: null, to: 'draft', actor: 'ba-tester@example.com', timestamp: '2026-01-01T00:00:00Z', note: null },
    ],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockListProjects.mockResolvedValue([project()]);
});

describe('AdminDashboard', () => {
  it('renders all projects with assigned persona and stage for a maintenance specialist', async () => {
    mockAuthState = {
      isAuthenticated: true,
      isLoading: false,
      user: { username: 'maint-tester@example.com', role: 'Maintenance Specialist', groups: ['maintenance-specialist'] },
      signOut: mockSignOut,
    };

    render(<AdminDashboard />);

    // The project row shows the stage and the assigned persona.
    await waitFor(() => {
      expect(screen.getByText('Online Bookstore')).toBeTruthy();
    });
    // "Assigned to Solution Architect" appears both as the stage label and
    // (potentially) elsewhere; at least one occurrence is required.
    expect(screen.getAllByText('Assigned to Solution Architect').length).toBeGreaterThan(0);
    expect(screen.getByText('Operations Dashboard')).toBeTruthy();
  });

  it('blocks a non-maintenance persona with an unavailable message', async () => {
    mockAuthState = {
      isAuthenticated: true,
      isLoading: false,
      user: { username: 'ba-tester@example.com', role: 'Business Analyst', groups: ['requirements-analyst'] },
      signOut: mockSignOut,
    };

    render(<AdminDashboard />);

    expect(screen.getByText('Dashboard unavailable')).toBeTruthy();
    // It must not fetch the project list for an unauthorized persona.
    expect(mockListProjects).not.toHaveBeenCalled();
  });
});
