/**
 * Unit tests for ConstructionBriefDownload — the Developer's brief-download
 * convenience control.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

const { getFileContentMock } = vi.hoisted(() => ({ getFileContentMock: vi.fn() }));

vi.mock('../../app/services/s3Service', () => ({
  getFileContent: getFileContentMock,
}));

import { ConstructionBriefDownload } from '../../app/components/ConstructionBriefDownload';
import type { ProjectRecord } from '../../app/services/projectService';

const DEV = 'software-developer';

function makeRecord(over: Partial<ProjectRecord> = {}): ProjectRecord {
  return {
    project_uuid: 'uuid-dev',
    project_id: 'sample',
    name: 'Sample',
    description: '',
    type: 'development',
    created_at: '2026-01-01T00:00:00Z',
    created_by: 'x@example.com',
    handoff_state: 'assigned_to_dev',
    owning_role: DEV,
    artifact_prefix: 'sample',
    assignment_history: [],
    ...over,
  } as ProjectRecord;
}

beforeEach(() => {
  getFileContentMock.mockReset();
  getFileContentMock.mockResolvedValue('# Construction Brief\n\nDo the thing.');
  // jsdom lacks URL.createObjectURL/revokeObjectURL — stub them.
  (URL as unknown as { createObjectURL: () => string }).createObjectURL = vi.fn(
    () => 'blob:mock',
  );
  (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = vi.fn();
});
afterEach(cleanup);

describe('ConstructionBriefDownload', () => {
  it('shows for the owning developer', () => {
    render(<ConstructionBriefDownload record={makeRecord()} userGroups={[DEV]} />);
    expect(screen.getByTestId('construction-brief-download')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /Download construction brief/i }),
    ).toBeTruthy();
  });

  it('renders nothing for a non-owner / non-developer', () => {
    const { container } = render(
      <ConstructionBriefDownload
        record={makeRecord({ owning_role: 'system-architect' })}
        userGroups={['system-architect']}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('fetches the brief at the project artifact prefix and triggers a download', async () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(<ConstructionBriefDownload record={makeRecord()} userGroups={[DEV]} />);

    fireEvent.click(screen.getByRole('button', { name: /Download construction brief/i }));

    await waitFor(() => {
      expect(getFileContentMock).toHaveBeenCalledWith(
        'sample/aidlc-docs/construction/construction-brief.md',
      );
    });
    await waitFor(() => {
      expect(clickSpy).toHaveBeenCalled();
    });
    expect((URL as unknown as { createObjectURL: ReturnType<typeof vi.fn> }).createObjectURL)
      .toHaveBeenCalled();
    clickSpy.mockRestore();
  });

  it('surfaces a generic error inline when the brief cannot be loaded', async () => {
    getFileContentMock.mockRejectedValueOnce(new Error('network unavailable'));
    render(<ConstructionBriefDownload record={makeRecord()} userGroups={[DEV]} />);
    fireEvent.click(screen.getByRole('button', { name: /Download construction brief/i }));
    await waitFor(() => {
      expect(screen.getByText(/network unavailable/i)).toBeTruthy();
    });
  });

  it('shows an actionable message when the brief has not been generated yet (NoSuchKey)', async () => {
    const err = new Error('The specified key does not exist.');
    err.name = 'NoSuchKey';
    getFileContentMock.mockRejectedValueOnce(err);
    render(<ConstructionBriefDownload record={makeRecord()} userGroups={[DEV]} />);
    fireEvent.click(screen.getByRole('button', { name: /Download construction brief/i }));
    await waitFor(() => {
      expect(screen.getByText(/has not been generated yet/i)).toBeTruthy();
    });
  });
});
