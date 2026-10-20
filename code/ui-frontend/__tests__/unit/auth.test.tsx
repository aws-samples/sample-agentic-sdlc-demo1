import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import React from 'react';
import {
  AuthProvider,
  useAuth,
  groupToDisplayRole,
  COGNITO_GROUPS,
  type CognitoUser,
} from '../../app/context/AuthContext';

// Mock aws-amplify/auth
vi.mock('aws-amplify/auth', () => ({
  signIn: vi.fn(),
  signUp: vi.fn(),
  signOut: vi.fn(),
  getCurrentUser: vi.fn(),
  fetchAuthSession: vi.fn(),
}));

// Mock amplify config
vi.mock('../../app/lib/amplify', () => ({
  configureAmplify: vi.fn(),
}));

import {
  signIn as amplifySignIn,
  signUp as amplifySignUp,
  signOut as amplifySignOut,
  getCurrentUser,
  fetchAuthSession,
} from 'aws-amplify/auth';

const mockGetCurrentUser = vi.mocked(getCurrentUser);
const mockFetchAuthSession = vi.mocked(fetchAuthSession);
const mockAmplifySignIn = vi.mocked(amplifySignIn);
const mockAmplifySignUp = vi.mocked(amplifySignUp);
const mockAmplifySignOut = vi.mocked(amplifySignOut);

function createMockSession(overrides: {
  sub?: string;
  username?: string;
  email?: string;
  groups?: string[];
} = {}) {
  const sub = overrides.sub ?? 'user-sub-123';
  const email = overrides.email ?? 'test@example.com';
  const groups = overrides.groups ?? ['software-developer'];

  return {
    currentUser: {
      userId: sub,
      username: overrides.username ?? 'testuser',
      signInDetails: undefined,
    },
    session: {
      tokens: {
        idToken: {
          payload: {
            sub,
            email,
            'cognito:username': overrides.username ?? 'testuser',
            'cognito:groups': groups,
          },
        },
        accessToken: {
          toString: () => 'mock-access-token-jwt',
        },
      },
    },
  };
}

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <AuthProvider>{children}</AuthProvider>
);

describe('groupToDisplayRole', () => {
  it('converts known group names to display roles', () => {
    expect(groupToDisplayRole('requirements-analyst')).toBe('Requirements Analyst');
    expect(groupToDisplayRole('system-architect')).toBe('System Architect');
    expect(groupToDisplayRole('software-developer')).toBe('Software Developer');
    expect(groupToDisplayRole('qa-engineer')).toBe('QA Engineer');
    expect(groupToDisplayRole('devops-engineer')).toBe('DevOps Engineer');
    expect(groupToDisplayRole('maintenance-specialist')).toBe('Maintenance Specialist');
  });

  it('returns the group name as-is for unknown groups', () => {
    expect(groupToDisplayRole('unknown-group')).toBe('unknown-group');
    expect(groupToDisplayRole('')).toBe('');
  });
});

describe('COGNITO_GROUPS', () => {
  it('contains exactly 6 groups', () => {
    expect(Object.keys(COGNITO_GROUPS)).toHaveLength(6);
  });

  it('maps all SDLC roles', () => {
    expect(COGNITO_GROUPS['requirements-analyst']).toBe('Requirements Analyst');
    expect(COGNITO_GROUPS['system-architect']).toBe('System Architect');
    expect(COGNITO_GROUPS['software-developer']).toBe('Software Developer');
    expect(COGNITO_GROUPS['qa-engineer']).toBe('QA Engineer');
    expect(COGNITO_GROUPS['devops-engineer']).toBe('DevOps Engineer');
    expect(COGNITO_GROUPS['maintenance-specialist']).toBe('Maintenance Specialist');
  });
});

describe('AuthProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: no existing session
    mockGetCurrentUser.mockRejectedValue(new Error('No user'));
    mockFetchAuthSession.mockResolvedValue({ tokens: undefined });
  });

  it('starts with isLoading true and resolves to unauthenticated', async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });

    expect(result.current.isLoading).toBe(true);
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
    expect(result.current.accessToken).toBeNull();

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(false);
  });

  it('loads existing session on mount', async () => {
    const mock = createMockSession({ groups: ['system-architect'] });
    mockGetCurrentUser.mockResolvedValue(mock.currentUser as any);
    mockFetchAuthSession.mockResolvedValue(mock.session as any);

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user).toEqual({
      sub: 'user-sub-123',
      username: 'testuser',
      email: 'test@example.com',
      groups: ['system-architect'],
      role: 'System Architect',
    });
    expect(result.current.accessToken).toBe('mock-access-token-jwt');
  });

  it('signIn sets user and accessToken', async () => {
    const mock = createMockSession();
    mockAmplifySignIn.mockResolvedValue({ isSignedIn: true, nextStep: { signInStep: 'DONE' } });

    const { result } = renderHook(() => useAuth(), { wrapper });

    // Wait for mount session check to complete (no session)
    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    // Reset mocks after mount consumed them, then set up for signIn flow
    mockGetCurrentUser.mockReset();
    mockFetchAuthSession.mockReset();
    mockGetCurrentUser.mockResolvedValue(mock.currentUser as any);
    mockFetchAuthSession.mockResolvedValue(mock.session as any);

    await act(async () => {
      await result.current.signIn('test@example.com', 'password123');
    });

    expect(mockAmplifySignIn).toHaveBeenCalledWith({
      username: 'test@example.com',
      password: 'password123',
    });
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user?.email).toBe('test@example.com');
    expect(result.current.accessToken).toBe('mock-access-token-jwt');
  });

  it('signUp calls amplifySignUp with correct params', async () => {
    mockAmplifySignUp.mockResolvedValue({
      isSignUpComplete: true,
      userId: 'new-user-id',
      nextStep: { signUpStep: 'DONE' },
    });

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    await act(async () => {
      await result.current.signUp('newuser', 'new@example.com', 'pass123', 'qa-engineer');
    });

    expect(mockAmplifySignUp).toHaveBeenCalledWith({
      username: 'new@example.com',
      password: 'pass123',
      options: {
        userAttributes: {
          email: 'new@example.com',
          preferred_username: 'newuser',
          'custom:role': 'qa-engineer',
        },
        autoSignIn: false,
      },
    });
  });

  it('signOut clears user and accessToken', async () => {
    const mock = createMockSession();
    mockGetCurrentUser.mockResolvedValue(mock.currentUser as any);
    mockFetchAuthSession.mockResolvedValue(mock.session as any);
    mockAmplifySignOut.mockResolvedValue(undefined);

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isAuthenticated).toBe(true);
    });

    await act(async () => {
      await result.current.signOut();
    });

    expect(mockAmplifySignOut).toHaveBeenCalled();
    expect(result.current.user).toBeNull();
    expect(result.current.accessToken).toBeNull();
    expect(result.current.isAuthenticated).toBe(false);
  });

  it('derives role from first group in cognito:groups', async () => {
    const mock = createMockSession({ groups: ['devops-engineer', 'software-developer'] });
    mockGetCurrentUser.mockResolvedValue(mock.currentUser as any);
    mockFetchAuthSession.mockResolvedValue(mock.session as any);

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.user?.role).toBe('DevOps Engineer');
    expect(result.current.user?.groups).toEqual(['devops-engineer', 'software-developer']);
  });

  it('sets empty role when no groups present', async () => {
    const mock = createMockSession({ groups: [] });
    mockGetCurrentUser.mockResolvedValue(mock.currentUser as any);
    mockFetchAuthSession.mockResolvedValue(mock.session as any);

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.user?.role).toBe('');
    expect(result.current.user?.groups).toEqual([]);
  });
});

describe('useAuth', () => {
  it('throws when used outside AuthProvider', () => {
    expect(() => {
      renderHook(() => useAuth());
    }).toThrow('useAuth must be used within an AuthProvider');
  });
});
