/**
 * Persona/Workflow Registry — Frontend mirror of the shared source of truth.
 *
 * This is the TypeScript counterpart of `workflow_registry.py` (design Section
 * 3a-i). The Python module loads `workflow_registry.json` from disk at import
 * time; the Frontend receives a byte-identical copy of that same JSON merged
 * into `/config.json` at deploy time (design Section 3a-ii / 3a-iii) and reads
 * it through {@link getConfig}.
 *
 * Every persona-, segment-, and state-specific behavior on the Frontend
 * (RBAC-derived action gating, handoff status indicators, the assign card)
 * derives from this registry instead of hardcoding persona names, so adding a
 * future persona is a registry edit rather than a code change (Req 4.7).
 *
 * The {@link WorkflowRegistry} class wraps a registry object so callers (and
 * tests) can query an arbitrary registry; {@link getRegistry} returns the
 * default registry carried by `/config.json`.
 */

import { getConfig } from './config';

// ---------------------------------------------------------------------------
// Registry shape — mirrors workflow_registry.json (design Section 3a)
// ---------------------------------------------------------------------------

/** A persona entry: a Cognito group's display label and chain position. */
export interface PersonaSpec {
  /** Human-readable label shown in the UI, e.g. "Business Analyst". */
  display: string;
  /** Position in the SDLC chain (lower runs earlier). */
  order: number;
}

/** A workflow segment: an ordered set of graph stages owned by one role. */
export interface SegmentSpec {
  /** Cognito group name that owns this segment. */
  owning_role: string;
  /** Ordered workflow stages owned by this segment. */
  stages: string[];
  /** Graph entry node to use when a session enters at this segment. */
  entry_node: string;
  /** Node whose outbound edge becomes a handoff boundary. */
  exit_hub: string;
}

/** A handoff state: its owning role, segment, and single forward successor. */
export interface StateSpec {
  /** Cognito group name that owns this state. */
  owning_role: string;
  /** Segment this state belongs to. */
  segment: string;
  /** Single forward successor state, or `null` when terminal. */
  forward: string | null;
}

/** A request-changes (backward) reassignment descriptor. */
export interface RequestChangesSpec {
  /** State the project bounces back to. */
  target: string;
  /** UI label, e.g. "Changes requested". */
  label: string;
}

/**
 * The full registry object, mirroring the JSON shape in
 * `workflow_registry.json` (design Section 3a). This is the type carried by
 * `/config.json` under the `workflowRegistry` field.
 */
export interface RegistryData {
  /** Starting handoff state for a new project, e.g. "draft". */
  initial_state: string;
  /** Cognito group name -> persona spec. */
  personas: Record<string, PersonaSpec>;
  /** Segment name -> segment spec. */
  segments: Record<string, SegmentSpec>;
  /** Handoff state name -> state spec. */
  states: Record<string, StateSpec>;
  /** Source state name -> request-changes descriptor. */
  request_changes: Record<string, RequestChangesSpec>;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/** Raised when the registry is missing from config or is malformed. */
export class RegistryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'RegistryError';
  }
}

// ---------------------------------------------------------------------------
// Registry wrapper — pure lookup helpers mirroring workflow_registry.py
// ---------------------------------------------------------------------------

/**
 * A loaded Persona/Workflow Registry with pure lookup helpers.
 *
 * Each method mirrors the corresponding helper in `workflow_registry.py`:
 * `owning_role` -> {@link owningRole}, `segment_of` -> {@link segmentOf},
 * `forward` -> {@link forward}, `request_changes` -> {@link requestChangesInto},
 * `segment_stages` -> {@link segmentStages}, `entry_node` -> {@link entryNode},
 * `upstream_segments` -> {@link upstreamSegments},
 * `ordered_segments` -> {@link orderedSegments},
 * `display_label` -> {@link displayLabel}, `initial_state` -> {@link initialState}.
 */
export class WorkflowRegistry {
  private readonly data: RegistryData;

  constructor(data: RegistryData) {
    if (data === null || typeof data !== 'object') {
      throw new RegistryError('registry data must be an object');
    }
    this.data = data;
  }

  /** Return the underlying registry object. */
  get raw(): RegistryData {
    return this.data;
  }

  // -- state lookups ------------------------------------------------------

  /** Return the registry's `initial_state`. */
  initialState(): string {
    return this.data.initial_state;
  }

  /** Return the Cognito group name that owns `state`. */
  owningRole(state: string): string {
    return this.requireState(state).owning_role;
  }

  /** Return the workflow segment that `state` belongs to. */
  segmentOf(state: string): string {
    return this.requireState(state).segment;
  }

  /** Return the single forward successor of `state` (`null` if terminal). */
  forward(state: string): string | null {
    return this.requireState(state).forward;
  }

  /**
   * Return the request-changes descriptor for `state` (`{ target, label }`)
   * or `null` when no backward reassignment is defined for it.
   */
  requestChangesInto(state: string): RequestChangesSpec | null {
    return this.data.request_changes[state] ?? null;
  }

  /**
   * Return `true` only when moving `current` -> `target` is permitted.
   *
   * A transition is permitted (design Section 3b, Req 4.3) when either:
   *   - `target` is `current`'s immediate forward successor
   *     (`target === forward(current)`), or
   *   - a request-changes reassignment is defined for `current` and `target`
   *     is its declared target (`target === requestChangesInto(current).target`).
   *
   * Every other `target` is rejected (returns `false`), mirroring the Project
   * API's validation error that leaves the record unchanged (Req 4.4). Pure
   * over the registry data; adding a persona extends the data, not this logic.
   * Counterpart of `validate_transition` in `workflow_registry.py`.
   */
  validateTransition(current: string, target: string): boolean {
    const spec = this.data.states[current];
    if (!spec) {
      return false;
    }
    if (spec.forward === target) {
      return true;
    }
    const rc = this.data.request_changes[current];
    return rc != null && rc.target === target;
  }

  // -- segment lookups ----------------------------------------------------

  /** Return the ordered list of workflow stages owned by segment `seg`. */
  segmentStages(seg: string): string[] {
    return this.requireSegment(seg).stages;
  }

  /** Return the graph entry node to use when a session enters at segment `seg`. */
  entryNode(seg: string): string {
    return this.requireSegment(seg).entry_node;
  }

  /**
   * Return every segment whose owning persona ranks earlier than `seg`'s.
   *
   * A segment is "upstream" when its owning role's `persona.order` is strictly
   * lower than the order of `seg`'s owning role. Used to build `skip_completed`
   * so a persona never re-runs earlier personas' work (design Section 3a-i /
   * 5c). Returns segment names ordered by persona order.
   */
  upstreamSegments(seg: string): string[] {
    const targetOrder = this.segmentOrder(seg);
    return this.orderedSegments().filter(
      (name) => name !== seg && this.segmentOrder(name) < targetOrder,
    );
  }

  /** Return segment names sorted by their owning role's `persona.order`. */
  orderedSegments(): string[] {
    return Object.keys(this.data.segments).sort(
      (a, b) => this.segmentOrder(a) - this.segmentOrder(b),
    );
  }

  // -- persona lookups ----------------------------------------------------

  /** Return the human-readable display label for a Cognito group `role`. */
  displayLabel(role: string): string {
    const persona = this.data.personas[role];
    if (!persona) {
      throw new RegistryError(`unknown persona role: '${role}'`);
    }
    return persona.display;
  }

  // -- internals ----------------------------------------------------------

  /** Return the persona order of `seg`'s owning role (used for sorting). */
  private segmentOrder(seg: string): number {
    const owning = this.requireSegment(seg).owning_role;
    const persona = this.data.personas[owning];
    if (!persona) {
      throw new RegistryError(
        `segment '${seg}' owning_role '${owning}' is not a defined persona`,
      );
    }
    return persona.order;
  }

  private requireState(state: string): StateSpec {
    const spec = this.data.states[state];
    if (!spec) {
      throw new RegistryError(`unknown handoff state: '${state}'`);
    }
    return spec;
  }

  private requireSegment(seg: string): SegmentSpec {
    const spec = this.data.segments[seg];
    if (!spec) {
      throw new RegistryError(`unknown segment: '${seg}'`);
    }
    return spec;
  }
}

// ---------------------------------------------------------------------------
// Default registry — read from /config.json (design Section 3a-iii)
// ---------------------------------------------------------------------------

let cachedRegistry: WorkflowRegistry | null = null;

/**
 * Return the default registry carried by `/config.json` under the
 * `workflowRegistry` field, wrapped in a {@link WorkflowRegistry}.
 *
 * The instance is cached after the first call. Requires {@link loadConfig} to
 * have already populated the config (the app loads it at startup, mirroring
 * other config consumers such as `s3Service`).
 *
 * @throws {RegistryError} when the config has no `workflowRegistry` field.
 */
export function getRegistry(): WorkflowRegistry {
  if (cachedRegistry) return cachedRegistry;

  const registry = getConfig().workflowRegistry;
  if (!registry) {
    throw new RegistryError(
      'workflowRegistry missing from /config.json. Ensure CDK published the ' +
        'registry copy into the UI config (design Section 3a-iii).',
    );
  }
  cachedRegistry = new WorkflowRegistry(registry);
  return cachedRegistry;
}

/** Reset the cached registry. Intended for tests. */
export function resetRegistryCache(): void {
  cachedRegistry = null;
}

/**
 * Return `true` only when moving `current` -> `target` is a permitted
 * transition, using the default registry from `/config.json`.
 *
 * Standalone counterpart of {@link WorkflowRegistry.validateTransition} and of
 * `validate_transition` in `workflow_registry.py` (design Section 3b, Req
 * 4.3/4.4).
 */
export function validateTransition(current: string, target: string): boolean {
  return getRegistry().validateTransition(current, target);
}
