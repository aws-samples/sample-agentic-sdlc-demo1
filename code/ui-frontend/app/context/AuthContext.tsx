import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import {
  signIn as amplifySignIn,
  signUp as amplifySignUp,
  signOut as amplifySignOut,
  getCurrentUser,
  fetchAuthSession,
} from 'aws-amplify/auth';

// --- Cognito group-to-display-role mapping ---

export const COGNITO_GROUPS: Record<string, string> = {
  'requirements-analyst': 'Business Analyst',
  'system-architect': 'Solution Architect',
  'software-developer': 'Software Developer',
  'qa-engineer': 'QA Engineer',
  'devops-engineer': 'DevOps Engineer',
  'maintenance-specialist': 'Maintenance Specialist',
};

export function groupToDisplayRole(groupName: string): string {
  return Object.hasOwn(COGNITO_GROUPS, groupName)
    ? COGNITO_GROUPS[groupName]
    : groupName;
}

// --- Interfaces ---

export interface CognitoUser {
  sub: string;
  username: string;
  email: string;
  groups: string[];
  role: string;
}

export interface AuthContextType {
  user: CognitoUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  accessToken: string | null;
  idToken: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (username: string, email: string, password: string, role: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

// Amplify is configured in main.tsx before the app renders

// --- Helper: extract user info from Amplify session ---

async function buildCognitoUser(): Promise<{ user: CognitoUser; accessToken: string; idToken: string } | null> {
  try {
    const currentUser = await getCurrentUser();
    const session = await fetchAuthSession();

    const idToken = session.tokens?.idToken;
    const accessTokenJwt = session.tokens?.accessToken?.toString() ?? null;

    if (!idToken || !accessTokenJwt) {
      return null;
    }

    const payload = idToken.payload ?? {};
    const sub = (payload.sub as string) ?? currentUser.userId;
    const username = currentUser.username ?? (payload['cognito:username'] as string) ?? '';
    const email = (payload.email as string) ?? '';
    const groups = (payload['cognito:groups'] as string[]) ?? [];
    const role = groups.length > 0 ? groupToDisplayRole(groups[0]) : '';

    return {
      user: { sub, username, email, groups, role },
      accessToken: accessTokenJwt,
      idToken: idToken.toString(),
    };
  } catch {
    return null;
  }
}

// --- Provider ---

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<CognitoUser | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [idToken, setIdToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Check existing session on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await buildCognitoUser();
        if (!cancelled && result) {
          setUser(result.user);
          setAccessToken(result.accessToken);
          setIdToken(result.idToken);
        }
      } catch {
        // No valid session — stay unauthenticated
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const signIn = useCallback(async (email: string, password: string): Promise<void> => {
    await amplifySignIn({ username: email, password });
    const result = await buildCognitoUser();
    if (result) {
      setUser(result.user);
      setAccessToken(result.accessToken);
      setIdToken(result.idToken);
    }
  }, []);

  const signUp = useCallback(async (
    username: string,
    email: string,
    password: string,
    role: string,
  ): Promise<void> => {
    await amplifySignUp({
      username: email,
      password,
      options: {
        userAttributes: {
          email,
          preferred_username: username,
          'custom:role': role,
        },
        autoSignIn: false,
      },
    });
    // Group assignment (adminAddUserToGroup) happens server-side
    // via a Cognito post-confirmation trigger that reads custom:role.
  }, []);

  const signOut = useCallback(async (): Promise<void> => {
    await amplifySignOut();
    setUser(null);
    setAccessToken(null);
    setIdToken(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        accessToken,
        idToken,
        signIn,
        signUp,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
