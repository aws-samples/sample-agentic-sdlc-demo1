import { describe, it, expect, vi, beforeAll } from 'vitest';
import * as fc from 'fast-check';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { DocumentViewer } from '../../app/components/document/DocumentViewer';
import type { ArtifactFile } from '../../app/types/ws-messages';

// Mock scrollIntoView which is not available in jsdom
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/**
 * Printable non-empty strings that won't be transformed by markdown rendering.
 * Avoids markdown-special characters so textContent assertions are reliable.
 */
const visibleTextArbitrary: fc.Arbitrary<string> = fc
  .string({ minLength: 1, maxLength: 80 })
  .map((s: string) =>
    s
      .replace(/[<>&_*`#\[\]\\~|{}()!\-\n\r\t"'+]/g, '')
      .replace(/\s+/g, ' ')
      .trim(),
  )
  .filter((s: string) => s.length > 0);

/** File path segments that look like real paths */
const fileNameArbitrary: fc.Arbitrary<string> = fc
  .stringMatching(/^[a-zA-Z][a-zA-Z0-9_-]{0,20}$/)
  .filter((s: string) => s.length > 0);

/** Non-markdown file extension */
const nonMdExtArbitrary: fc.Arbitrary<string> = fc.constantFrom(
  '.txt', '.py', '.ts', '.json', '.yaml', '.html', '.css', '.js',
);

/** Generate a file path with a non-markdown extension */
const nonMdFilePathArbitrary: fc.Arbitrary<string> = fc
  .tuple(
    fc.array(fileNameArbitrary, { minLength: 1, maxLength: 3 }),
    fileNameArbitrary,
    nonMdExtArbitrary,
  )
  .map(([dirs, name, ext]) => dirs.join('/') + '/' + name + ext);

/** Generate a file path with .md extension */
const mdFilePathArbitrary: fc.Arbitrary<string> = fc
  .tuple(
    fc.array(fileNameArbitrary, { minLength: 1, maxLength: 3 }),
    fileNameArbitrary,
  )
  .map(([dirs, name]) => dirs.join('/') + '/' + name + '.md');

/** Generate a random file path (markdown or non-markdown) */
const filePathArbitrary: fc.Arbitrary<string> = fc.oneof(
  mdFilePathArbitrary,
  nonMdFilePathArbitrary,
);

/** Build an ArtifactFile with content loaded */
function makeLoadedArtifact(filePath: string, content: string): ArtifactFile {
  return { filePath, content, objectUrl: null, loading: false, error: null };
}

/** Generate an array of 2-5 ArtifactFile objects with unique file paths */
const multipleArtifactsArbitrary: fc.Arbitrary<ArtifactFile[]> = fc
  .tuple(
    fc.array(
      fc.tuple(filePathArbitrary, visibleTextArbitrary),
      { minLength: 2, maxLength: 5 },
    ),
  )
  .map(([pairs]) => {
    // Deduplicate by filePath
    const seen = new Set<string>();
    const result: ArtifactFile[] = [];
    for (const [fp, content] of pairs) {
      if (!seen.has(fp)) {
        seen.add(fp);
        result.push(makeLoadedArtifact(fp, content));
      }
    }
    return result;
  })
  .filter((arr) => arr.length >= 2);

// ---------------------------------------------------------------------------
// Property 12: Artifact fetch on update
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 12: Artifact fetch on update
 *
 * For any artifact_updated message with a file_path, the Document_Viewer
 * shall initiate a fetch for that file and, on success, render the content.
 *
 * We test the rendering side: given an ArtifactFile with content loaded,
 * the DocumentViewer renders the content. For markdown files (.md), content
 * is rendered via react-markdown. For non-markdown files, content is rendered
 * in a <pre> block.
 *
 * Validates: Requirements 6.1
 */
describe('Property 12: Artifact fetch on update', () => {
  it('renders markdown content for .md files via react-markdown', () => {
    /**
     * **Validates: Requirements 6.1**
     */
    fc.assert(
      fc.property(mdFilePathArbitrary, visibleTextArbitrary, (filePath, content) => {
        cleanup();
        const mockSelectFile = vi.fn();
        const mockRetry = vi.fn();
        const artifact = makeLoadedArtifact(filePath, content);

        const { container, unmount } = render(
          <DocumentViewer
            artifacts={[artifact]}
            activeFilePath={filePath}
            onSelectFile={mockSelectFile}
            onRetry={mockRetry}
          />,
        );

        // Content must be rendered in the DOM
        expect(container.textContent).toContain(content);

        // Markdown files should be rendered via react-markdown (prose class)
        const proseDiv = container.querySelector('.prose');
        expect(proseDiv).not.toBeNull();

        // Should NOT be in a <pre> block
        const preBlocks = container.querySelectorAll('pre');
        // react-markdown may produce <pre> for code blocks, but our plain text
        // content should be in the prose div, not a standalone pre
        if (preBlocks.length > 0) {
          // If there's a pre, it should be inside the prose div (markdown code block),
          // not the top-level plain text pre
          for (const pre of preBlocks) {
            expect(pre.closest('.prose')).not.toBeNull();
          }
        }

        unmount();
      }),
      { numRuns: 100 },
    );
  });

  it('renders non-markdown content in a <pre> block', () => {
    /**
     * **Validates: Requirements 6.1**
     */
    fc.assert(
      fc.property(nonMdFilePathArbitrary, visibleTextArbitrary, (filePath, content) => {
        cleanup();
        const mockSelectFile = vi.fn();
        const mockRetry = vi.fn();
        const artifact = makeLoadedArtifact(filePath, content);

        const { container, unmount } = render(
          <DocumentViewer
            artifacts={[artifact]}
            activeFilePath={filePath}
            onSelectFile={mockSelectFile}
            onRetry={mockRetry}
          />,
        );

        // Content must be rendered in the DOM
        expect(container.textContent).toContain(content);

        // Non-markdown files should NOT have a prose div
        const proseDiv = container.querySelector('.prose');
        expect(proseDiv).toBeNull();

        // Content should be inside a <pre> element
        const preBlock = container.querySelector('pre');
        expect(preBlock).not.toBeNull();
        expect(preBlock!.textContent).toContain(content);

        unmount();
      }),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 13: Multiple artifact tab accessibility
// ---------------------------------------------------------------------------

/**
 * Feature: simple-clean-frontend, Property 13: Multiple artifact tab accessibility
 *
 * For any set of N artifact files (N > 1) received during a session, the
 * Document_Viewer shall provide a tab or list interface with N entries, and
 * selecting any tab shall display that file's content.
 *
 * Validates: Requirements 6.5
 */
describe('Property 13: Multiple artifact tab accessibility', () => {
  it('renders a tab button for each artifact and clicking any tab calls onSelectFile with the correct path', () => {
    /**
     * **Validates: Requirements 6.5**
     */
    fc.assert(
      fc.property(multipleArtifactsArbitrary, fc.nat(), (artifacts, clickIndexRaw) => {
        cleanup();
        const mockSelectFile = vi.fn();
        const mockRetry = vi.fn();
        const clickIndex = clickIndexRaw % artifacts.length;

        const { unmount } = render(
          <DocumentViewer
            artifacts={artifacts}
            activeFilePath={artifacts[0].filePath}
            onSelectFile={mockSelectFile}
            onRetry={mockRetry}
          />,
        );

        // Each artifact must have a corresponding tab button
        const buttons = screen.getAllByRole('button');
        // Filter to tab buttons (those whose text matches a file name)
        for (const artifact of artifacts) {
          const fileName = artifact.filePath.split('/').pop() || artifact.filePath;
          const tabButton = buttons.find((btn) => btn.textContent === fileName);
          expect(tabButton).toBeTruthy();
        }

        // Click the selected tab
        const targetArtifact = artifacts[clickIndex];
        const targetFileName = targetArtifact.filePath.split('/').pop() || targetArtifact.filePath;
        const targetButton = buttons.find((btn) => btn.textContent === targetFileName);
        expect(targetButton).toBeTruthy();
        fireEvent.click(targetButton!);

        // onSelectFile must have been called with the correct file path
        expect(mockSelectFile).toHaveBeenCalledWith(targetArtifact.filePath);

        unmount();
      }),
      { numRuns: 100 },
    );
  });
});
