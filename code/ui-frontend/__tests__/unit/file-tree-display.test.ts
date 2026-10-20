import { describe, it, expect } from 'vitest';
import { buildFileTree, displaySegment } from '../../app/services/s3Service';

/**
 * The file tree relabels the `aidlc-docs` folder to `agentic-sdlc` for display
 * only. The underlying S3 keys (used for fetching) and the `.md` file names
 * must remain unchanged.
 */
describe('file tree display relabel', () => {
  it('maps the aidlc-docs segment to agentic-sdlc, leaving others untouched', () => {
    expect(displaySegment('aidlc-docs')).toBe('agentic-sdlc');
    expect(displaySegment('inception')).toBe('inception');
    expect(displaySegment('aidlc-state.md')).toBe('aidlc-state.md');
  });

  it('shows agentic-sdlc as the folder label but keeps the real aidlc-docs S3 key', () => {
    const tree = buildFileTree(
      [
        { Key: 'my-project/aidlc-docs/inception/requirements/requirements.md', Size: 10 },
        { Key: 'my-project/aidlc-docs/aidlc-state.md', Size: 5 },
      ],
      'my-project',
    );

    const topFolder = tree.children?.[0];
    // Folder is displayed as agentic-sdlc...
    expect(topFolder?.name).toBe('agentic-sdlc');
    expect(topFolder?.type).toBe('folder');

    // ...but the .md file names are unchanged...
    const stateFile = topFolder?.children?.find((c) => c.type === 'file');
    expect(stateFile?.name).toBe('aidlc-state.md');

    // ...and the real S3 key still points at aidlc-docs (internals unchanged).
    expect(stateFile?.key).toBe('my-project/aidlc-docs/aidlc-state.md');

    const reqMd = topFolder?.children
      ?.find((c) => c.name === 'inception')
      ?.children?.find((c) => c.name === 'requirements')
      ?.children?.find((c) => c.name === 'requirements.md');
    expect(reqMd?.key).toBe('my-project/aidlc-docs/inception/requirements/requirements.md');
  });

  it('groups all aidlc-docs entries under a single relabeled folder', () => {
    const tree = buildFileTree(
      [
        { Key: 'p/aidlc-docs/inception/a.md', Size: 1 },
        { Key: 'p/aidlc-docs/construction/b.md', Size: 1 },
      ],
      'p',
    );
    const agentic = tree.children?.filter((c) => c.name === 'agentic-sdlc');
    expect(agentic?.length).toBe(1);
    expect(agentic?.[0].children?.map((c) => c.name).sort()).toEqual(['construction', 'inception']);
  });

  it('hides internal dot-prefixed entries (e.g. the .collab marker)', () => {
    const tree = buildFileTree(
      [
        { Key: 'p/aidlc-docs/inception/a.md', Size: 1 },
        { Key: 'p/.collab/last-processed-bounce', Size: 1 },
      ],
      'p',
    );
    const names = tree.children?.map((c) => c.name) ?? [];
    expect(names).toContain('agentic-sdlc');
    expect(names).not.toContain('.collab');
  });
});
