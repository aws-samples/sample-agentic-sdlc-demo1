import { describe, it, expect, vi, beforeAll, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { DocumentViewer } from '../../app/components/document/DocumentViewer';
import type { ArtifactFile } from '../../app/types/ws-messages';

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

function makeImageArtifact(filePath: string, objectUrl: string): ArtifactFile {
  return { filePath, content: null, objectUrl, loading: false, error: null };
}

function makeTextArtifact(filePath: string, content: string): ArtifactFile {
  return { filePath, content, objectUrl: null, loading: false, error: null };
}

describe('Image rendering in DocumentViewer', () => {
  afterEach(() => cleanup());

  it('renders an <img> tag for a .png file with an objectUrl', () => {
    const artifact = makeImageArtifact(
      'projects/sample-app/diagrams/arch.png',
      'blob:http://localhost/fake-uuid',
    );

    const { container } = render(
      <DocumentViewer
        artifacts={[artifact]}
        activeFilePath={artifact.filePath}
        onSelectFile={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img!.src).toBe('blob:http://localhost/fake-uuid');
    expect(img!.alt).toBe('arch.png');
  });

  it('renders an <img> tag for a .jpg file with an objectUrl', () => {
    const artifact = makeImageArtifact(
      'projects/foo/photo.jpg',
      'blob:http://localhost/jpg-uuid',
    );

    const { container } = render(
      <DocumentViewer
        artifacts={[artifact]}
        activeFilePath={artifact.filePath}
        onSelectFile={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img!.src).toBe('blob:http://localhost/jpg-uuid');
  });

  it('does NOT render an <img> tag for a non-image file', () => {
    const artifact = makeTextArtifact(
      'projects/sample-app/readme.txt',
      'Hello world',
    );

    const { container } = render(
      <DocumentViewer
        artifacts={[artifact]}
        activeFilePath={artifact.filePath}
        onSelectFile={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    const img = container.querySelector('img');
    expect(img).toBeNull();

    const pre = container.querySelector('pre');
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain('Hello world');
  });

  it('renders an <img> for image files regardless of extension casing', () => {
    const artifact = makeImageArtifact(
      'projects/foo/DIAGRAM.PNG',
      'blob:http://localhost/upper-uuid',
    );

    const { container } = render(
      <DocumentViewer
        artifacts={[artifact]}
        activeFilePath={artifact.filePath}
        onSelectFile={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    const img = container.querySelector('img');
    expect(img).not.toBeNull();
  });

  it('shows empty state when image artifact has no objectUrl', () => {
    const artifact: ArtifactFile = {
      filePath: 'projects/foo/broken.png',
      content: null,
      objectUrl: null,
      loading: false,
      error: null,
    };

    const { container } = render(
      <DocumentViewer
        artifacts={[artifact]}
        activeFilePath={artifact.filePath}
        onSelectFile={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    const img = container.querySelector('img');
    expect(img).toBeNull();
    expect(container.textContent).toContain('This file is empty');
  });
});
