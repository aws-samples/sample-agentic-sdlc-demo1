import { describe, it, expect } from 'vitest';
import {
  displaySegment,
  relabelArtifactPaths,
  FOLDER_DISPLAY_NAMES,
} from '../../app/lib/displayLabels';

describe('displayLabels', () => {
  it('maps the aidlc-docs segment to agentic-sdlc, leaving others untouched', () => {
    expect(displaySegment('aidlc-docs')).toBe('agentic-sdlc');
    expect(displaySegment('inception')).toBe('inception');
    expect(FOLDER_DISPLAY_NAMES['aidlc-docs']).toBe('agentic-sdlc');
  });

  it('relabels aidlc-docs path references inside display text', () => {
    expect(
      relabelArtifactPaths(
        'All of your inception artifacts are packaged and ready in the project workspace (aidlc-docs/inception/).',
      ),
    ).toBe(
      'All of your inception artifacts are packaged and ready in the project workspace (agentic-sdlc/inception/).',
    );
  });

  it('relabels every occurrence in a block of text', () => {
    const md = '- aidlc-docs/construction/construction-brief.md\n- aidlc-docs/construction/tasks/x.md';
    expect(relabelArtifactPaths(md)).toBe(
      '- agentic-sdlc/construction/construction-brief.md\n- agentic-sdlc/construction/tasks/x.md',
    );
  });

  it('leaves the aidlc-state.md file name intact (only the folder token is remapped)', () => {
    expect(relabelArtifactPaths('aidlc-docs/aidlc-state.md')).toBe('agentic-sdlc/aidlc-state.md');
  });

  it('does not touch unrelated text or other aidlc-* identifiers', () => {
    expect(relabelArtifactPaths('bucket aidlc-assets-123 and aidlc-ui-123')).toBe(
      'bucket aidlc-assets-123 and aidlc-ui-123',
    );
    expect(relabelArtifactPaths('')).toBe('');
  });
});
