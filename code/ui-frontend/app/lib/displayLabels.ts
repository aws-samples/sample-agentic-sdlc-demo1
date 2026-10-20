/**
 * Display-only relabeling of the artifact folder.
 *
 * The platform stores artifacts under the `aidlc-docs/` S3 prefix (a stable
 * identifier — see `.kiro/steering/10-stable-identifiers.md`). For the user
 * experience we present that folder as `agentic-sdlc`. These helpers are
 * **display-only**: they never change S3 keys, file names (e.g.
 * `aidlc-state.md`), agent write paths, or anything fetched/persisted.
 *
 * Single source of truth for the relabel, consumed by:
 *   - the file browser tree (`s3Service.buildFileTree`)
 *   - assistant chat text (`TextMessage`)
 *   - the in-app document viewer (`DocumentViewer`)
 */

/** Folder path-segment relabels (real segment → displayed label). */
export const FOLDER_DISPLAY_NAMES: Record<string, string> = {
  'aidlc-docs': 'agentic-sdlc',
};

/** Relabel a single path segment for display (folder labels only). */
export function displaySegment(part: string): string {
  return FOLDER_DISPLAY_NAMES[part] ?? part;
}

/**
 * Relabel artifact-folder references inside a block of display text
 * (chat narration, markdown, table cells). Replaces the folder token wherever
 * it appears — e.g. `aidlc-docs/inception/requirements.md` is shown as
 * `agentic-sdlc/inception/requirements.md`. Only the folder token is remapped;
 * file names such as `aidlc-state.md` are left intact because `aidlc-docs` is a
 * distinct token from `aidlc-state`.
 *
 * Note: this is cosmetic. The underlying S3 keys remain `aidlc-docs/...`, so a
 * path copied from displayed text will not literally match the stored key.
 */
export function relabelArtifactPaths(text: string): string {
  if (!text) return text;
  let out = text;
  for (const [real, shown] of Object.entries(FOLDER_DISPLAY_NAMES)) {
    out = out.split(real).join(shown);
  }
  return out;
}
