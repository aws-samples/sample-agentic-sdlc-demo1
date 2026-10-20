import { createBrowserRouter, Navigate } from 'react-router';
import { LandingPage } from './components/LandingPage';
import { SignInPage } from './components/SignInPage';
import { SignUpPage } from './components/SignUpPage';
import { ProjectWorkspace } from './components/ProjectWorkspace';
import { AdminDashboard } from './components/AdminDashboard';
import { ErrorBoundary } from './components/ErrorBoundary';

export const router = createBrowserRouter([
  {
    path: '/',
    element: (
      <ErrorBoundary>
        <LandingPage />
      </ErrorBoundary>
    ),
  },
  {
    path: '/sign-in',
    Component: SignInPage,
  },
  {
    path: '/sign-up',
    Component: SignUpPage,
  },
  {
    path: '/project/:projectId',
    element: (
      <ErrorBoundary>
        <ProjectWorkspace />
      </ErrorBoundary>
    ),
  },
  {
    path: '/dashboard',
    element: (
      <ErrorBoundary>
        <AdminDashboard />
      </ErrorBoundary>
    ),
  },
  {
    path: '*',
    element: <Navigate to="/" replace />,
  },
]);
