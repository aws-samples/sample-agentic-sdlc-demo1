import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { SignUpPage } from '../../app/components/SignUpPage';
import { COGNITO_GROUPS } from '../../app/context/AuthContext';

// Radix Select uses scrollIntoView which jsdom doesn't implement
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

// Mock useAuth
const mockSignUp = vi.fn();
vi.mock('../../app/context/AuthContext', async () => {
  const actual = await vi.importActual('../../app/context/AuthContext');
  return {
    ...actual,
    useAuth: () => ({
      signUp: mockSignUp,
    }),
  };
});

// Helper to select a role from the Radix Select dropdown
async function selectRole(user: ReturnType<typeof userEvent.setup>, roleName: string) {
  await user.click(screen.getByLabelText('Role'));
  // Radix renders options with role="option"
  const options = await screen.findAllByRole('option');
  const target = options.find((opt) => opt.textContent === roleName);
  if (!target) throw new Error(`Role option "${roleName}" not found`);
  await user.click(target);
}

describe('SignUpPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders all form fields', () => {
    render(<SignUpPage />);

    expect(screen.getByLabelText('Username')).toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
    expect(screen.getByLabelText('Role')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign Up' })).toBeInTheDocument();
  });

  it('displays all 6 SDLC roles in the role dropdown', async () => {
    const user = userEvent.setup();
    render(<SignUpPage />);

    await user.click(screen.getByLabelText('Role'));

    const options = await screen.findAllByRole('option');
    const optionTexts = options.map((opt) => opt.textContent);

    for (const displayName of Object.values(COGNITO_GROUPS)) {
      expect(optionTexts).toContain(displayName);
    }
    expect(options).toHaveLength(6);
  });

  it('shows validation error when no role is selected', async () => {
    render(<SignUpPage />);

    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'testuser' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'test@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password123' } });

    fireEvent.click(screen.getByRole('button', { name: 'Sign Up' }));

    expect(await screen.findByText('Please select a role')).toBeInTheDocument();
    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it('calls signUp with Cognito group name and navigates on success', async () => {
    mockSignUp.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<SignUpPage />);

    await user.type(screen.getByLabelText('Username'), 'testuser');
    await user.type(screen.getByLabelText('Email'), 'test@example.com');
    await user.type(screen.getByLabelText('Password'), 'password123');

    await selectRole(user, 'QA Engineer');

    await user.click(screen.getByRole('button', { name: 'Sign Up' }));

    await waitFor(() => {
      expect(mockSignUp).toHaveBeenCalledWith('testuser', 'test@example.com', 'password123', 'qa-engineer');
    });
    expect(mockNavigate).toHaveBeenCalledWith('/sign-in');
  });

  it('displays "Email already exists" for duplicate email error', async () => {
    mockSignUp.mockRejectedValue(new Error('An account with the given email already exists'));
    const user = userEvent.setup();
    render(<SignUpPage />);

    await user.type(screen.getByLabelText('Username'), 'testuser');
    await user.type(screen.getByLabelText('Email'), 'dup@example.com');
    await user.type(screen.getByLabelText('Password'), 'password123');

    await selectRole(user, 'Software Developer');

    await user.click(screen.getByRole('button', { name: 'Sign Up' }));

    expect(await screen.findByText('Email already exists')).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('displays generic error message for other failures', async () => {
    mockSignUp.mockRejectedValue(new Error('Network error'));
    const user = userEvent.setup();
    render(<SignUpPage />);

    await user.type(screen.getByLabelText('Username'), 'testuser');
    await user.type(screen.getByLabelText('Email'), 'test@example.com');
    await user.type(screen.getByLabelText('Password'), 'password123');

    await selectRole(user, 'Requirements Analyst');

    await user.click(screen.getByRole('button', { name: 'Sign Up' }));

    expect(await screen.findByText('Network error')).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('navigates to sign-in page when "Sign In" link is clicked', () => {
    render(<SignUpPage />);

    fireEvent.click(screen.getByText('Sign In'));

    expect(mockNavigate).toHaveBeenCalledWith('/sign-in');
  });
});
