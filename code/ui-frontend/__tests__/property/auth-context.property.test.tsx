import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import { groupToDisplayRole, COGNITO_GROUPS } from '../../app/context/AuthContext';

/**
 * Feature: simple-clean-frontend, Property 15: Auth context group extraction
 *
 * For any ID token containing a cognito:groups claim with one or more group names,
 * the auth context shall expose those group names and derive the correct display role.
 *
 * Validates: Requirements 8.4
 */

const KNOWN_GROUPS = Object.keys(COGNITO_GROUPS);

describe('Property 15: Auth context group extraction', () => {
  it('maps every known Cognito group to its correct display name', () => {
    /**
     * **Validates: Requirements 8.4**
     *
     * Generate random non-empty subsets of the 6 known Cognito groups.
     * For each group in the subset, groupToDisplayRole must return the
     * matching display name from COGNITO_GROUPS.
     */
    fc.assert(
      fc.property(
        fc.uniqueArray(fc.constantFrom(...KNOWN_GROUPS), { minLength: 1, maxLength: 6 }),
        (groups: string[]) => {
          for (const group of groups) {
            const displayRole = groupToDisplayRole(group);
            expect(displayRole).toBe(COGNITO_GROUPS[group]);
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it('returns unknown group names as-is', () => {
    /**
     * **Validates: Requirements 8.4**
     *
     * For any arbitrary string that is NOT one of the 6 known Cognito groups,
     * groupToDisplayRole must return the input string unchanged.
     */
    fc.assert(
      fc.property(
        fc.string({ minLength: 1 }).filter((s) => !KNOWN_GROUPS.includes(s)),
        (unknownGroup: string) => {
          expect(groupToDisplayRole(unknownGroup)).toBe(unknownGroup);
        },
      ),
      { numRuns: 100 },
    );
  });
});

/**
 * Feature: simple-clean-frontend, Property 14: Cognito group description consistency
 *
 * For any Cognito group in the set {requirements-analyst, system-architect,
 * software-developer, qa-engineer, devops-engineer, maintenance-specialist},
 * the group's description shall match its display name (e.g.,
 * requirements-analyst → "Requirements Analyst").
 *
 * Validates: Requirements 8.2
 */

const EXPECTED_GROUPS = [
  'requirements-analyst',
  'system-architect',
  'software-developer',
  'qa-engineer',
  'devops-engineer',
  'maintenance-specialist',
];

describe('Property 14: Cognito group description consistency', () => {
  it('each group display name is a multi-word capitalised form of the kebab-case group name', () => {
    /**
     * **Validates: Requirements 8.2**
     *
     * For any randomly selected Cognito group from the known set,
     * COGNITO_GROUPS[group] must:
     *   - be a non-empty string
     *   - contain the same number of words as the group name has hyphen-separated segments
     *   - have each word match the corresponding segment case-insensitively
     *     (allowing acronyms like "QA" for "qa")
     *   - have every word start with an uppercase letter
     */
    fc.assert(
      fc.property(
        fc.constantFrom(...EXPECTED_GROUPS),
        (group: string) => {
          const displayName = COGNITO_GROUPS[group];
          expect(displayName).toBeDefined();
          expect(displayName.length).toBeGreaterThan(0);

          const segments = group.split('-');
          const words = displayName.split(' ');

          // Same number of words as kebab segments
          expect(words.length).toBe(segments.length);

          for (let i = 0; i < segments.length; i++) {
            // Each word matches its segment case-insensitively
            expect(words[i].toLowerCase()).toBe(segments[i].toLowerCase());
            // Each word starts with an uppercase letter
            expect(words[i][0]).toBe(words[i][0].toUpperCase());
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it('the group-to-display-name mapping is bijective (no duplicate display names)', () => {
    /**
     * **Validates: Requirements 8.2**
     *
     * For any two distinct groups randomly selected from the known set,
     * their display names must be different — ensuring the mapping is
     * one-to-one.
     */
    fc.assert(
      fc.property(
        fc.uniqueArray(fc.constantFrom(...EXPECTED_GROUPS), { minLength: 2, maxLength: 6 }),
        (groups: string[]) => {
          const displayNames = groups.map((g) => COGNITO_GROUPS[g]);
          const uniqueDisplayNames = new Set(displayNames);
          expect(uniqueDisplayNames.size).toBe(displayNames.length);
        },
      ),
      { numRuns: 100 },
    );
  });
});
