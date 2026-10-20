import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import {
  RequirementsUploadControl,
  validateUploadFilename,
  SUPPORTED_UPLOAD_EXTENSIONS,
} from '../../app/components/RequirementsUploadControl';

// --- Mock the auth context: a Business Analyst user with an idToken. -------

const mockAuthState = {
  user: {
    sub: 'ba-sub',
    username: 'ba',
    email: 'ba@example.com',
    groups: ['requirements-analyst'],
    role: 'Requirements Analyst',
  },
  isAuthenticated: true,
  isLoading: false,
  accessToken: 'access-token',
  idToken: 'id-token-jwt',
  signIn: vi.fn(),
  signUp: vi.fn(),
  signOut: vi.fn(),
};

vi.mock('../../app/context/AuthContext', () => ({
  useAuth: () => mockAuthState,
}));

// --- Mock loadConfig so we control the projectApiUrl ------------------------

vi.mock('../../app/lib/config', () => ({
  loadConfig: vi.fn().mockResolvedValue({
    cognitoUserPoolId: '',
    cognitoClientId: '',
    cognitoIdentityPoolId: '',
    cognitoDomain: '',
    awsRegion: 'us-east-1',
    runtimeArn: '',
    filesBucket: '',
    projectApiUrl: 'https://api.example.com/prod',
  }),
  getConfig: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Pure validator tests (Req 5.4)
// ---------------------------------------------------------------------------

describe('validateUploadFilename', () => {
  it.each(SUPPORTED_UPLOAD_EXTENSIONS.map((ext) => `requirements${ext}`))(
    'accepts supported format %s',
    (filename) => {
      const result = validateUploadFilename(filename);
      expect(result.ok).toBe(true);
    },
  );

  it('is case-insensitive on the extension', () => {
    expect(validateUploadFilename('SPEC.PDF').ok).toBe(true);
    expect(validateUploadFilename('notes.MD').ok).toBe(true);
    expect(validateUploadFilename('README.Txt').ok).toBe(true);
    expect(validateUploadFilename('design.DocX').ok).toBe(true);
  });

  it('rejects an unsupported extension with a format-specific error', () => {
    const result = validateUploadFilename('virus.exe');
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain('.exe');
      // Names every allowed format so the user knows what they CAN upload.
      for (const allowed of SUPPORTED_UPLOAD_EXTENSIONS) {
        expect(result.error).toContain(allowed);
      }
    }
  });

  it('rejects a filename with no extension', () => {
    const result = validateUploadFilename('README');
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain('(no extension)');
    }
  });

  it('rejects an extension that is a prefix of an allowed one', () => {
    // ".m" must not be treated as ".md".
    expect(validateUploadFilename('notes.m').ok).toBe(false);
    expect(validateUploadFilename('notes.mdx').ok).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Component tests
// ---------------------------------------------------------------------------

function makeFile(name: string, type = 'text/plain'): File {
  return new File(['hello world'], name, { type });
}

describe('<RequirementsUploadControl />', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('renders the allowed-format hint and the file input', () => {
    render(<RequirementsUploadControl projectId="proj-123" />);

    expect(
      screen.getByText(/Allowed: \.md, \.txt, \.pdf, \.docx/),
    ).toBeInTheDocument();

    const input = screen.getByLabelText(
      /Choose a requirements document to upload/i,
    ) as HTMLInputElement;
    expect(input.type).toBe('file');
    expect(input.accept).toBe('.md,.txt,.pdf,.docx');
  });

  it('rejects an unsupported format inline before any network call (Req 5.4)', async () => {
    render(<RequirementsUploadControl projectId="proj-123" />);

    const input = screen.getByLabelText(
      /Choose a requirements document to upload/i,
    ) as HTMLInputElement;

    fireEvent.change(input, { target: { files: [makeFile('virus.exe')] } });

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Unsupported file format/i);
    expect(alert).toHaveTextContent('.exe');
    // No fetch attempted.
    expect(fetchMock).not.toHaveBeenCalled();
    // No "Upload" button shown for an invalid file.
    expect(screen.queryByRole('button', { name: /^Upload$/ })).toBeNull();
  });

  it('accepts a supported format and uploads via the presigned PUT URL', async () => {
    fetchMock
      // 1) presigned URL request
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          upload_url: 'https://s3.example.com/presigned-put',
          key: 'online_bookstore/aidlc-docs/inception/requirements/uploaded/spec.md',
          expires_in: 300,
        }),
      })
      // 2) PUT to S3
      .mockResolvedValueOnce({ ok: true });

    const onUploaded = vi.fn();
    render(
      <RequirementsUploadControl projectId="proj-123" onUploaded={onUploaded} />,
    );

    const input = screen.getByLabelText(
      /Choose a requirements document to upload/i,
    ) as HTMLInputElement;

    fireEvent.change(input, { target: { files: [makeFile('spec.md')] } });

    // The "Upload" button appears for an accepted file.
    const uploadBtn = await screen.findByRole('button', { name: /^Upload$/ });
    fireEvent.click(uploadBtn);

    await waitFor(() => {
      expect(onUploaded).toHaveBeenCalledWith(
        'online_bookstore/aidlc-docs/inception/requirements/uploaded/spec.md',
      );
    });

    // Verify the presigned-URL request: correct method, body, and Authorization
    // header carrying the ID token.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const presignCall = fetchMock.mock.calls[0];
    expect(presignCall[0]).toBe(
      'https://api.example.com/prod/projects/proj-123/upload-url',
    );
    expect(presignCall[1].method).toBe('POST');
    expect(presignCall[1].headers).toMatchObject({
      'Content-Type': 'application/json',
      Authorization: 'Bearer id-token-jwt',
    });
    expect(JSON.parse(presignCall[1].body)).toEqual({ filename: 'spec.md' });

    // Verify the PUT to the presigned URL: must NOT forward the ID token
    // (the presigned URL carries its own SigV4 query-string credential).
    const putCall = fetchMock.mock.calls[1];
    expect(putCall[0]).toBe('https://s3.example.com/presigned-put');
    expect(putCall[1].method).toBe('PUT');
    expect(putCall[1].body).toBeInstanceOf(File);
    expect(putCall[1].headers).toBeUndefined();

    // Success message rendered.
    expect(await screen.findByText(/Uploaded spec\.md/)).toBeInTheDocument();
  });

  it('surfaces the API error message when presigned-URL minting fails', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 403,
      json: async () => ({ error: 'Forbidden', code: 'FORBIDDEN' }),
    });

    render(<RequirementsUploadControl projectId="proj-123" />);

    const input = screen.getByLabelText(
      /Choose a requirements document to upload/i,
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeFile('design.docx')] } });

    fireEvent.click(await screen.findByRole('button', { name: /^Upload$/ }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Forbidden');
    // PUT was never reached.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('surfaces a generic error when the PUT to S3 fails', async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          upload_url: 'https://s3.example.com/presigned-put',
          key: 'p/aidlc-docs/inception/requirements/uploaded/req.txt',
          expires_in: 300,
        }),
      })
      .mockResolvedValueOnce({ ok: false, status: 500 });

    render(<RequirementsUploadControl projectId="proj-123" />);

    const input = screen.getByLabelText(
      /Choose a requirements document to upload/i,
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeFile('req.txt')] } });
    fireEvent.click(await screen.findByRole('button', { name: /^Upload$/ }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Upload failed/i);
    expect(alert).toHaveTextContent('500');
  });
});
