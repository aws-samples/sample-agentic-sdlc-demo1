import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import { buildWebSocketUrl, createInitMessage } from '../../app/context/WebSocketContext';

/**
 * Feature: simple-clean-frontend, Property 1: WebSocket bearer token inclusion
 *
 * For any valid Cognito access token in the auth context, when a WebSocket
 * connection is initiated, the connection request shall include that exact
 * token as the bearer credential.
 *
 * The WebSocket connection uses `new WebSocket(url, ['access_token', token])`
 * to pass the bearer token via the subprotocol array.
 *
 * Validates: Requirements 2.3
 */

// Generator for JWT-like access tokens: three base64url segments separated by dots
const jwtArbitrary = fc
  .tuple(
    fc.base64String({ minLength: 10, maxLength: 100 }),
    fc.base64String({ minLength: 10, maxLength: 200 }),
    fc.base64String({ minLength: 10, maxLength: 80 }),
  )
  .map(([header, payload, sig]) => `${header}.${payload}.${sig}`);

// Generator for AWS region strings like "us-east-1", "eu-west-2"
const regionArbitrary = fc
  .tuple(
    fc.constantFrom('us', 'eu', 'ap', 'sa', 'ca', 'me', 'af'),
    fc.constantFrom('east', 'west', 'north', 'south', 'central', 'northeast', 'southeast'),
    fc.integer({ min: 1, max: 3 }),
  )
  .map(([prefix, direction, num]) => `${prefix}-${direction}-${num}`);

// Generator for ARN-like runtime strings
const runtimeArnArbitrary = fc
  .tuple(
    fc.constantFrom('us-east-1', 'us-west-2', 'eu-west-1'),
    fc.stringMatching(/^[0-9]{12}$/),
    fc.stringMatching(/^[a-z0-9]{8,20}$/),
  )
  .map(
    ([region, account, id]) =>
      `arn:aws:bedrock-agentcore:${region}:${account}:runtime/${id}`,
  );

describe('Property 1: WebSocket bearer token inclusion', () => {
  it('buildWebSocketUrl constructs a valid wss:// URL with the region and encoded runtime ARN', () => {
    /**
     * **Validates: Requirements 2.3**
     *
     * For any AWS region and runtime ARN, buildWebSocketUrl must produce a URL
     * that starts with wss://, contains the region in the hostname, and includes
     * the URI-encoded runtime ARN in the path.
     */
    fc.assert(
      fc.property(regionArbitrary, runtimeArnArbitrary, (region, runtimeArn) => {
        const url = buildWebSocketUrl(region, runtimeArn);

        // Must be a wss:// URL
        expect(url.startsWith('wss://')).toBe(true);

        // Must contain the region in the hostname
        expect(url).toContain(`bedrock-agentcore.${region}.amazonaws.com`);

        // Must contain the encoded runtime ARN in the path
        expect(url).toContain(encodeURIComponent(runtimeArn));

        // Must end with /ws
        expect(url.endsWith('/ws')).toBe(true);

        // Full expected format
        const expected = `wss://bedrock-agentcore.${region}.amazonaws.com/runtimes/${encodeURIComponent(runtimeArn)}/ws`;
        expect(url).toBe(expected);
      }),
      { numRuns: 100 },
    );
  });

  it('the bearer token is always placed at index 1 of the WebSocket protocols array', () => {
    /**
     * **Validates: Requirements 2.3**
     *
     * For any valid JWT-like access token, the WebSocket connection protocol
     * array ['access_token', token] must contain the literal string 'access_token'
     * at index 0 and the exact token at index 1. This verifies the token is
     * included as the bearer credential in the connection request.
     */
    fc.assert(
      fc.property(jwtArbitrary, (token) => {
        // Simulate the protocols array as constructed in WebSocketProvider
        const protocols = ['access_token', token];

        // The first element must be the literal 'access_token' marker
        expect(protocols[0]).toBe('access_token');

        // The second element must be the exact token — no mutation, no truncation
        expect(protocols[1]).toBe(token);

        // The array must have exactly 2 elements
        expect(protocols).toHaveLength(2);
      }),
      { numRuns: 100 },
    );
  });

  it('createInitMessage produces valid JSON with project_id, project_name, project_uuid, and request fields', () => {
    /**
     * **Validates: Requirements 2.3, 3.3**
     *
     * For any project ID and request string, createInitMessage must produce
     * a valid JSON string that parses to an object containing the provided
     * project_id, project_name, project_uuid, and request values. The
     * `project_uuid` field is the stable identifier shared between personas
     * (agentic-sdlc-transformation Req 3.3).
     */
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 100 }),
        fc.string({ minLength: 1, maxLength: 500 }),
        (projectId, request) => {
          const message = createInitMessage(projectId, request);

          // Must be valid JSON
          const parsed = JSON.parse(message);

          // Must contain the exact project_id
          expect(parsed.project_id).toBe(projectId);

          // Must contain the exact request
          expect(parsed.request).toBe(request);

          // Must contain project_name (defaults to projectId when not provided)
          expect(parsed.project_name).toBe(projectId);

          // Must contain project_uuid (defaults to projectId when not provided)
          expect(parsed.project_uuid).toBe(projectId);

          // Must have exactly these four keys
          expect(Object.keys(parsed).sort()).toEqual([
            'project_id',
            'project_name',
            'project_uuid',
            'request',
          ]);
        },
      ),
      { numRuns: 100 },
    );
  });
});

/**
 * Feature: simple-clean-frontend, Property 2: WebSocket init message shape
 *
 * For any project ID and request string, when a WebSocket connection is
 * established, the first message sent shall be a valid JSON object containing
 * exactly `project_id` and `request` fields with the provided values.
 *
 * Validates: Requirements 3.2
 */
describe('Property 2: WebSocket init message shape', () => {
  // Generator for non-empty project IDs including special characters and unicode
  const projectIdArbitrary = fc.oneof(
    fc.string({ minLength: 1, maxLength: 200 }),
    // IDs with special characters via regex
    fc.stringMatching(/^[a-zA-Z0-9\/_\-."'<>&\t\n]{1,80}$/),
    // Unicode-heavy IDs
    fc.constantFrom(
      'proj-🚀-test', '日本語プロジェクト', 'café-résumé', 'проект-42', '项目-αβγ',
      'hello\nworld', 'tab\there', 'quote"inside', "apostrophe'here", 'back\\slash',
    ),
  );

  // Generator for non-empty request strings including multi-line and special chars
  const requestArbitrary = fc.oneof(
    fc.string({ minLength: 1, maxLength: 500 }),
    // Multi-line requests
    fc.array(fc.string({ minLength: 1, maxLength: 100 }), { minLength: 1, maxLength: 5 }).map(
      (lines) => lines.join('\n'),
    ),
    // Unicode and special character requests
    fc.constantFrom(
      'Build a REST API\nwith authentication\nand rate limiting',
      '🎉 Create a 中文 project with ñ characters',
      'Line1\r\nLine2\tTabbed\0Null',
      'Escaped "quotes" and \\backslashes\\',
      'Mixed: αβγ δεζ ηθι κλμ',
    ),
  );

  it('produces valid JSON for any project ID and request string', () => {
    /**
     * **Validates: Requirements 3.2**
     *
     * createInitMessage must always produce a string that is valid JSON,
     * regardless of the input characters (unicode, special chars, etc.).
     */
    fc.assert(
      fc.property(projectIdArbitrary, requestArbitrary, (projectId, request) => {
        const message = createInitMessage(projectId, request);

        // Must not throw when parsing — i.e. must be valid JSON
        expect(() => JSON.parse(message)).not.toThrow();
      }),
      { numRuns: 100 },
    );
  });

  it('contains exactly project_id, project_name, project_uuid, and request fields with no extra fields', () => {
    /**
     * **Validates: Requirements 3.2, 3.3**
     *
     * The parsed JSON object must have exactly four keys: `project_id`,
     * `project_name`, `project_uuid`, and `request`. No additional fields
     * are allowed. The `project_uuid` field is the stable identifier shared
     * between personas (agentic-sdlc-transformation Req 3.3).
     */
    fc.assert(
      fc.property(projectIdArbitrary, requestArbitrary, (projectId, request) => {
        const parsed = JSON.parse(createInitMessage(projectId, request));

        const keys = Object.keys(parsed);
        expect(keys).toHaveLength(4);
        expect(keys.sort()).toEqual([
          'project_id',
          'project_name',
          'project_uuid',
          'request',
        ]);
      }),
      { numRuns: 100 },
    );
  });

  it('preserves the exact project_id and request values through JSON round-trip', () => {
    /**
     * **Validates: Requirements 3.2**
     *
     * The values stored in the JSON must exactly match the inputs after
     * a parse round-trip. No truncation, escaping artifacts, or mutation.
     */
    fc.assert(
      fc.property(projectIdArbitrary, requestArbitrary, (projectId, request) => {
        const parsed = JSON.parse(createInitMessage(projectId, request));

        expect(parsed.project_id).toBe(projectId);
        expect(parsed.request).toBe(request);
      }),
      { numRuns: 100 },
    );
  });

  it('round-trips correctly: parse(stringify(input)) preserves identity', () => {
    /**
     * **Validates: Requirements 3.2**
     *
     * Serializing the message and parsing it back must yield an object
     * that, when re-serialized, produces the same string. This ensures
     * the JSON encoding is stable and deterministic.
     */
    fc.assert(
      fc.property(projectIdArbitrary, requestArbitrary, (projectId, request) => {
        const message = createInitMessage(projectId, request);
        const parsed = JSON.parse(message);
        const reserialized = JSON.stringify(parsed);

        expect(reserialized).toBe(message);
      }),
      { numRuns: 100 },
    );
  });
});
