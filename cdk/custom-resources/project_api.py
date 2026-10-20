"""Project API Lambda.

Provides the shared, server-side Project Store + Project API that replaces the
browser-local project list, so two different users (a Business Analyst and a
Solution Architect) can see and act on the same project (design Sections 2a-2c,
3a-3c, 7a).

Project records are stored as JSON objects in the existing project bucket
(``PROJECT_BUCKET`` env var) under a reserved platform prefix that does not
collide with per-project artifact prefixes:

    _platform/projects/{project_uuid}.json

The data-driven handoff state machine (states, owning roles, permitted
transitions) is read from the shared ``workflow_registry`` module bundled
alongside this Lambda, so the BA->SA chain is encoded in exactly one place.

Routes (API Gateway proxy integration, Cognito-authorized):
  POST   /projects                                Create a project
  GET    /projects                                List projects (broad visibility)
  GET    /projects/{projectId}                    Read one project record
  POST   /projects/{projectId}/transition         Perform a handoff transition
  POST   /projects/{projectId}/reassign           Manual reassignment
  POST   /projects/{projectId}/upload-url          Presigned PUT for requirements upload
  POST   /projects/{projectId}/link-repo           Link a Git repo (C7 round-trip)
  POST   /projects/{projectId}/ingest-callback     Ingestion pipeline callback (AWS_IAM)
  POST   /projects/{projectId}/run-tests           Start the QA test-runner (QA-owner-gated)
  POST   /projects/{projectId}/test-callback       QA test-runner callback (direct invoke)
  DELETE /projects/{projectId}                     Delete a project
"""

import json
import os
import re
import uuid as uuid_lib
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import workflow_registry as registry

# ---------------------------------------------------------------------------
# AWS clients (module-level for Lambda container reuse)
# ---------------------------------------------------------------------------

_s3_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        # Force SigV4 presigning. The default client can presign with the
        # legacy SigV2 scheme (``AWSAccessKeyId=`` URLs), which folds
        # ``Content-Type`` into the signature. Browsers auto-add a
        # ``Content-Type`` header when PUTting a File body, which then no
        # longer matches the signed empty value → S3 returns 403
        # SignatureDoesNotMatch. SigV4 only signs ``host`` here, so the
        # browser's Content-Type is ignored and the upload succeeds.
        _s3_client = boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            config=Config(signature_version="s3v4"),
        )
    return _s3_client


_codebuild_client = None


def _codebuild():
    """Lazy CodeBuild client (module-level for Lambda container reuse)."""
    global _codebuild_client
    if _codebuild_client is None:
        _codebuild_client = boto3.client(
            "codebuild", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
    return _codebuild_client


_secrets_client = None


def _secrets():
    """Lazy Secrets Manager client (module-level for Lambda container reuse)."""
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client(
            "secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
    return _secrets_client


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Reserved platform prefix for project records. Sits outside any
# ``{project_id}/`` artifact tree so it is invisible to the file browser
# (design Section 2a, Req 11.3).
PROJECTS_PREFIX = "_platform/projects/"

# Presigned upload URLs are short-lived (seconds).
UPLOAD_URL_TTL_SECONDS = 300

# Uploaded requirements documents land under this per-project sub-path
# (design Section 7a, Req 5.1).
UPLOAD_KEY_TEMPLATE = "{prefix}/aidlc-docs/inception/requirements/uploaded/{filename}"

# A project_uuid is the stable identifier (design Section 2a, Req 3.1) and is
# interpolated directly into the S3 record key ``_platform/projects/{uuid}.json``.
# Constrain it to a safe character set so a client-supplied value can never
# escape the platform prefix (path traversal) or destabilize the
# identifier->record mapping. A standard ``crypto.randomUUID()`` (hex + hyphens)
# and the Frontend slug fallback (``[a-z0-9_-]``) both satisfy this pattern.
_VALID_PROJECT_UUID = re.compile(r"^[A-Za-z0-9_-]{1,256}$")

# ---------------------------------------------------------------------------
# Git repository linking (C7 Git round-trip)
# ---------------------------------------------------------------------------

# The handoff state a project enters once the developer submits the linked
# implementation repository. Kept as a single named constant (the C7 "submit"
# boundary) so the transition-gating check reads clearly; the registry remains
# the authority on whether the transition itself is permitted.
IMPLEMENTATION_SUBMITTED_STATE = "implementation_submitted"

# Allowlist of Git hosting providers accepted by POST /link-repo. Restricting to
# an explicit set of public hosts is an SSRF guard: a client-supplied repo URL
# can never point the downstream ingestion job at an internal/metadata
# endpoint. Extend this map to onboard self-managed GitLab / GitHub Enterprise
# hosts.
_ALLOWED_REPO_HOSTS = {
    "github.com": "github",
    "www.github.com": "github",
    "gitlab.com": "gitlab",
    "www.gitlab.com": "gitlab",
}

# https://<host>/<owner>/<repo>[...] only. The host is checked separately
# against _ALLOWED_REPO_HOSTS; the charset excludes credentials ("@"), an
# explicit port (":") and whitespace so only a bare https URL matches.
_REPO_URL_RE = re.compile(
    r"^https://(?P<host>[A-Za-z0-9.-]+)/(?P<path>[A-Za-z0-9][A-Za-z0-9._/-]*)$"
)

# Branch assumed when the caller does not specify one.
_DEFAULT_REPO_BRANCH = "main"

# CodeBuild project (name set by CDK) that clones the linked repo and syncs it
# into the project's ``{slug}/source/`` prefix, then calls the ingest-callback
# route to advance the project to QA. When this env var is unset (unit tests, or
# a deployment that has not wired ingestion yet) linking simply records
# ``pending`` and no build is triggered — the feature degrades gracefully.
_INGEST_CODEBUILD_PROJECT_ENV = "REPO_INGEST_CODEBUILD_PROJECT"

# Actor recorded in assignment_history for automated (non-human) transitions
# performed by the ingestion pipeline.
_SYSTEM_INGEST_ACTOR = "system:repo-ingest"

# CodeBuild project (name set by CDK) that runs the ingested repo's tests and
# writes results back. Unset in unit tests / undeployed environments.
_TEST_CODEBUILD_PROJECT_ENV = "REPO_TEST_CODEBUILD_PROJECT"

# Secrets Manager path for a project's optional private-repo Git token. Matches
# the ID the ingest CodeBuild reads (`aidlc/projects/{uuid}/git`) and the stable
# per-project secrets prefix (steering `10-stable-identifiers.md`).
_GIT_SECRET_TEMPLATE = "aidlc/projects/{uuid}/git"  # nosec B105 — Secrets Manager resource path, not a credential


def _store_git_token(project_uuid: str, token: str) -> None:
    """Store a private-repo Git token in Secrets Manager (put-or-create).

    Writes the token to ``aidlc/projects/{uuid}/git`` so the ingest CodeBuild
    can clone a private repository. Uses ``put_secret_value`` when the secret
    already exists and falls back to ``create_secret`` on first use. The token
    is never logged, returned, or written to the project record — only stored
    in Secrets Manager, referenced by name.
    """
    secret_id = _GIT_SECRET_TEMPLATE.format(uuid=project_uuid)
    client = _secrets()
    try:
        client.put_secret_value(SecretId=secret_id, SecretString=token)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            client.create_secret(Name=secret_id, SecretString=token)
        else:
            raise


def _validate_repo_url(url: str):
    """Validate a client-supplied Git repository URL (SSRF guard).

    Returns ``(provider, normalized_url)`` for an accepted URL, or
    ``(None, None)`` when the value is not an ``https`` URL to an allowlisted
    public Git host with an ``owner/repo`` path. Rejecting arbitrary hosts (and
    path traversal) prevents the downstream ingestion job from being pointed at
    an internal endpoint.
    """
    if not isinstance(url, str):
        return None, None
    candidate = url.strip()
    match = _REPO_URL_RE.match(candidate)
    if not match:
        return None, None
    provider = _ALLOWED_REPO_HOSTS.get(match.group("host").lower())
    if provider is None:
        return None, None
    path = match.group("path")
    if ".." in path:
        return None, None
    # Require at least an owner/repo pair.
    if len([seg for seg in path.split("/") if seg]) < 2:
        return None, None
    return provider, candidate.rstrip("/")


# ---------------------------------------------------------------------------
# Transition action vocabulary (registry-driven, design Sections 3b, 8d, 8e)
# ---------------------------------------------------------------------------
#
# A transition request names its intent with a high-level ``action`` rather than
# a raw target state. Two intents are recognized; both are resolved *through*
# the registry helpers (``forward``/``request_changes``) so the BA->SA chain is
# never hardcoded here -- adding a persona extends the registry, not this map.
#
#   * Forward intent (``assign``, ``approve``, the SA completion forwards, ...)
#     -> ``forward(current)`` (the state's single forward successor).
#   * Request-changes intent (``request-changes``, ``reject``, ...)
#     -> ``request_changes(current).target`` (a registry-defined backward
#     reassignment), recording the note in history.
#
# Action strings are normalized (lower-cased, whitespace/underscores collapsed
# to a single hyphen) before lookup so ``request_changes`` / ``request-changes``
# / ``Request Changes`` all match. Request-changes is matched *first* so a
# request-changes synonym can never fall through and be treated as a forward.
# An unrecognized action resolves to no target (HTTP 400) rather than silently
# advancing the state, which would otherwise always pass ``validate_transition``.

_REQUEST_CHANGES_ACTIONS = frozenset(
    {
        "request-changes",
        "changes-requested",
        "reject",
    }
)

_FORWARD_ACTIONS = frozenset(
    {
        "assign",          # requirements_ready -> assigned_to_sa (6.4)
        "approve",         # assigned_to_sa -> in_design (7.4)
        "accept",
        "complete",        # generic SA completion forward
        "design-complete", # in_design -> design_ready / design_ready -> ready_for_dev (7.7)
        "mark-design-complete",
        "advance",
        "forward",
        "next",
        "submit",
        "handoff",
        "ready",
    }
)


def _normalize_action(action: str) -> str:
    """Normalize a raw ``action`` string for synonym lookup.

    Lower-cases and collapses any run of whitespace/underscores to a single
    hyphen so ``request_changes``, ``request-changes`` and ``Request Changes``
    all resolve identically.
    """
    return re.sub(r"[\s_]+", "-", action.strip().lower())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
        "body": json.dumps(body),
    }


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(name: str) -> str:
    """Derive the S3 artifact-prefix slug from a project name.

    Mirrors the Frontend's slug derivation
    (``name.toLowerCase().replace(/\\s+/g, '_').replace(/[^a-z0-9_\\-]/g, '')``)
    so both personas resolve the same ``artifact_prefix`` for the same project
    (design Section 2a, Req 3.3). No trimming is applied so the result matches
    the ``project_id`` the Frontend already sends over the WebSocket.
    """
    slug = name.lower()
    slug = re.sub(r"\s+", "_", slug)
    slug = re.sub(r"[^a-z0-9_\-]", "", slug)
    return slug


def _record_key(project_uuid: str) -> str:
    """Return the S3 key of a project record."""
    return f"{PROJECTS_PREFIX}{project_uuid}.json"


def _is_valid_uuid(project_uuid: str) -> bool:
    """Return ``True`` when ``project_uuid`` is safe to use as a record key.

    The value is interpolated into the S3 key ``_platform/projects/{uuid}.json``;
    rejecting anything outside ``[A-Za-z0-9_-]`` prevents path traversal (e.g.
    ``../`` or nested ``a/b`` keys) and keeps the identifier->record mapping
    stable (design Section 2a, Req 3.1).
    """
    return bool(project_uuid) and bool(_VALID_PROJECT_UUID.match(project_uuid))


def _caller_identity(event: dict) -> dict:
    """Extract the caller's identity from the Cognito authorizer claims.

    The API Gateway Cognito authorizer places the verified JWT claims into
    ``event.requestContext.authorizer.claims``; these cannot be forged by the
    client (design Section 4b). Returns the caller's email/username and the raw
    Cognito group names. ``cognito:groups`` may arrive as a comma-separated
    string or a list depending on the authorizer.
    """
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    claims = authorizer.get("claims") or {}

    actor = (
        claims.get("email")
        or claims.get("cognito:username")
        or claims.get("username")
        or "unknown"
    )

    raw_groups = claims.get("cognito:groups", "")
    if isinstance(raw_groups, list):
        groups = [g for g in raw_groups if g]
    elif isinstance(raw_groups, str):
        groups = [g for g in raw_groups.split(",") if g]
    else:
        groups = []

    return {"actor": actor, "groups": groups}


def _authorize_owner(record: dict, identity: dict):
    """Owner-gate a state-changing action against the record's current state.

    A state-changing action (transition, reassign, delete) is permitted only
    when the caller's Cognito groups CONTAIN the record's current ``owning_role``
    -- which equals ``owning_role(handoff_state)`` and is the single role
    permitted to act while the project sits in its current ``Handoff_State``
    (design Section 4b, Req 2.2/2.3). This also enforces Req 6.5: while a project
    is ``assigned_to_sa`` (owning role ``system-architect``) a Business Analyst
    is not in the owning group and is therefore blocked.

    The group claim is supplied by the API Gateway Cognito authorizer from the
    verified JWT, so it cannot be forged by the client -- this is the trust
    anchor for Project API RBAC.

    Returns ``None`` when the caller is authorized, or a ready-to-return HTTP 403
    ``_response`` when not. Callers must invoke this *before* any S3 mutation so
    an unauthorized caller never changes state.
    """
    owning = record.get("owning_role")
    groups = identity.get("groups") or []
    if owning in groups:
        return None
    return _response(
        403,
        {
            "error": (
                f"Caller is not the owning role '{owning}' for this project's "
                "current handoff state"
            ),
            "code": "FORBIDDEN",
        },
    )


def _read_record(project_uuid: str):
    """Read a project record from S3.

    Returns ``(record, etag)`` where ``record`` is the parsed JSON dict and
    ``etag`` is the S3 object ETag (used for conditional writes). Returns
    ``(None, None)`` when the record does not exist.
    """
    bucket = os.environ["PROJECT_BUCKET"]
    key = _record_key(project_uuid)
    try:
        resp = _s3().get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404", "NoSuchBucket"):
            return None, None
        raise
    record = json.loads(resp["Body"].read().decode("utf-8"))
    return record, resp.get("ETag")


def _write_record(project_uuid: str, record: dict, etag: str = None) -> None:
    """Write a project record to S3.

    When ``etag`` is provided the write is conditional (``IfMatch``) so a lost
    update from a concurrent writer is rejected with ``PreconditionFailed``
    (design Section 3c, Req 4.5). When ``etag`` is ``None`` the write is
    unconditional (used on create).
    """
    bucket = os.environ["PROJECT_BUCKET"]
    key = _record_key(project_uuid)
    kwargs = {
        "Bucket": bucket,
        "Key": key,
        "Body": json.dumps(record).encode("utf-8"),
        "ContentType": "application/json",
    }
    if etag is not None:
        kwargs["IfMatch"] = etag
    _s3().put_object(**kwargs)


def _delete_artifact_prefix(bucket: str, artifact_prefix: str) -> int:
    """Best-effort delete of every object under a project's artifact prefix.

    Removes ``{artifact_prefix}/*`` when a project is deleted so a later
    project whose name slugifies to the same prefix starts clean, instead of
    inheriting the deleted project's stale state/artifacts (the cross-project
    "same name -> same slug" bleed).

    Guarded so it can never wipe unintended data:
      * a blank/whitespace-only prefix is ignored (never deletes the whole
        bucket), and
      * the trailing-slash boundary (``{prefix}/``) prevents a prefix like
        ``task`` from also matching ``task_flow/``.

    Returns the number of objects deleted (0 when nothing matched).
    """
    prefix = (artifact_prefix or "").strip().strip("/")
    if not prefix:
        return 0
    prefix_with_slash = prefix + "/"

    s3 = _s3()
    deleted = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix_with_slash):
        objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if not objects:
            continue
        # delete_objects accepts up to 1000 keys; list pages are <= 1000.
        s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
        deleted += len(objects)
    return deleted


def _list_records() -> list:
    """List and read every project record under the platform prefix.

    Returns the parsed records. Listing is independent of ``created_by`` so a
    project never disappears from a collaborator's view (design Section 2c,
    Req 3.2).
    """
    bucket = os.environ["PROJECT_BUCKET"]
    records = []
    paginator = _s3().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=PROJECTS_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            try:
                resp = _s3().get_object(Bucket=bucket, Key=key)
                records.append(json.loads(resp["Body"].read().decode("utf-8")))
            except ClientError as e:
                # A record deleted mid-listing is skipped rather than failing
                # the whole list.
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    continue
                raise
    return records


def _resolve_target_state(current_state: str, body: dict):
    """Resolve the requested target Handoff_State from a transition request.

    A request may specify the target either explicitly (``target_state`` /
    ``target``) or, preferably, via a high-level ``action`` that is mapped to a
    registry edge:

      * a request-changes synonym (``request-changes``, ``reject`` ...) ->
        ``request_changes(current).target`` (a registry-defined backward
        reassignment); the note is taken from the request, or the registry's
        ``label`` when the caller supplied none (design Sections 3b, 8d);
      * a forward synonym (``assign``, ``approve``, ``design-complete`` ...) ->
        ``forward(current)`` (the state's single forward successor, design
        Sections 8d/8e).

    Request-changes synonyms are matched *first* so they can never fall through
    and be misread as a forward. An unrecognized action resolves to ``None`` so
    the caller returns HTTP 400 rather than silently advancing the state.

    Returns ``(target_state, note)`` or ``(None, None)`` when the request does
    not name a resolvable target. The returned target is *not* yet validated --
    ``validate_transition`` is the authority on whether it is permitted (Req 4.3,
    4.4).
    """
    note = body.get("note")

    explicit = body.get("target_state") or body.get("target")
    if explicit:
        return explicit, note

    raw_action = body.get("action")
    if not raw_action:
        return None, None

    action = _normalize_action(raw_action)
    if not action:
        return None, None

    # Request-changes is resolved first so a request-changes synonym can never
    # be misclassified as a forward transition.
    if action in _REQUEST_CHANGES_ACTIONS:
        rc = registry.request_changes(current_state)
        if rc is None:
            # No request-changes edge from this state; surface as unresolved so
            # validate_transition is not handed a forward target by accident.
            return None, note
        if note is None:
            note = rc.get("label")
        return rc.get("target"), note

    if action in _FORWARD_ACTIONS:
        return registry.forward(current_state), note

    # Unknown action: do not guess. Returning None yields an HTTP 400 instead of
    # silently treating it as a forward (which would always pass validation).
    return None, note


def _apply_transition(project_uuid: str, body: dict, identity: dict) -> dict:
    """Validate and apply a handoff transition with history + atomic write.

    Reads the record (capturing its ETag), owner-gates the action against the
    record's current ``owning_role`` (HTTP 403 before any mutation), validates
    the requested transition against the registry, mutates ``handoff_state`` +
    ``owning_role`` together, appends one ``assignment_history`` entry, and
    writes the whole object back with a conditional ``PutObject`` (design
    Sections 3c/4b, Req 2.2, 2.3, 4.3-4.6, 6.5).
    """
    record, etag = _read_record(project_uuid)
    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    # RBAC: only the current owning role may change state. Checked after the
    # read but before any validate/mutate/write so an unauthorized caller never
    # changes state (design Section 4b, Req 2.2/2.3/6.5).
    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    actor = identity["actor"]
    current_state = record.get("handoff_state")
    target_state, note = _resolve_target_state(current_state, body)

    if not target_state:
        return _response(
            400,
            {"error": "Could not resolve a target state from the request",
             "code": "INVALID_REQUEST"},
        )

    if not registry.validate_transition(current_state, target_state):
        # Illegal transition: reject and leave the record unchanged (Req 4.4).
        return _response(
            422,
            {"error": f"Illegal transition: {current_state} -> {target_state}",
             "code": "ILLEGAL_TRANSITION"},
        )

    # C7 gate: entering the implementation-submitted state requires a linked
    # repository — the developer must link the pushed repo (POST /link-repo)
    # before submitting the implementation for QA. The registry permits the
    # transition; this adds the repo precondition on top.
    if target_state == IMPLEMENTATION_SUBMITTED_STATE and not record.get("repo_url"):
        return _response(
            422,
            {"error": "A repository must be linked (POST /link-repo) before "
                      "submitting the implementation",
             "code": "REPO_NOT_LINKED"},
        )

    record["handoff_state"] = target_state
    record["owning_role"] = registry.owning_role(target_state)
    record.setdefault("assignment_history", []).append(
        {
            "from": current_state,
            "to": target_state,
            "actor": actor,
            "timestamp": _now_iso(),
            "note": note,
        }
    )

    try:
        _write_record(project_uuid, record, etag=etag)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("PreconditionFailed", "412"):
            # A concurrent writer updated the record first (Req 4.5).
            return _response(
                409,
                {"error": "Project was modified concurrently; refetch and retry",
                 "code": "ETAG_MISMATCH"},
            )
        print(f"[PROJECT_API] S3 write error on transition: {e}")
        return _response(500, {"error": str(e), "code": "S3_WRITE_FAILED"})

    # When the developer submits (project enters implementation_submitted), kick
    # off the repo ingestion build. Triggering here (not on link-repo) means the
    # ingest-callback always sees ``implementation_submitted`` and can advance to
    # QA deterministically. Guarded/degradable and best-effort (see helper).
    if target_state == IMPLEMENTATION_SUBMITTED_STATE:
        _maybe_trigger_ingest(project_uuid, record)

    return _response(200, record)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _handle_create_project(body: dict, identity: dict) -> dict:
    """POST /projects — create and persist a new project record.

    Accepts a client-provided ``project_uuid`` (the Frontend already mints one
    via ``crypto.randomUUID()`` and threads it through the WebSocket init
    message) or generates a stable one. The id is validated and, when supplied
    by the client, must not already name an existing record so a create can
    never silently clobber a project's handoff state/history (Req 3.1).
    """
    name = (body.get("name") or "").strip()
    if not name:
        return _response(400, {"error": "'name' is required", "code": "INVALID_REQUEST"})

    client_supplied_uuid = bool(body.get("project_uuid"))
    project_uuid = body.get("project_uuid") or str(uuid_lib.uuid4())

    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'project_uuid' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    # A client-provided id must not already exist: re-creating onto an existing
    # record would reset its handoff_state/assignment_history, breaking the
    # stable identifier->record guarantee (Req 3.1).
    if client_supplied_uuid:
        try:
            existing, _ = _read_record(project_uuid)
        except ClientError as e:
            print(f"[PROJECT_API] S3 read error on create existence check: {e}")
            return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})
        if existing is not None:
            return _response(
                409,
                {"error": f"Project '{project_uuid}' already exists",
                 "code": "ALREADY_EXISTS"},
            )

    slug = _slugify(name)
    now = _now_iso()
    actor = identity["actor"]

    # Handoff state machine seeded from the registry's single source of truth
    # (design Section 3a): initial_state == "draft", owned by "requirements-analyst".
    initial_state = registry.initial_state()
    owning = registry.owning_role(initial_state)

    record = {
        "project_uuid": project_uuid,
        "project_id": slug,
        "name": name,
        "description": body.get("description", ""),
        "type": body.get("type", "development"),
        "created_at": now,
        "created_by": actor,
        "handoff_state": initial_state,
        "owning_role": owning,
        "artifact_prefix": slug,
        # Git round-trip (C7) metadata; populated by POST /link-repo and the
        # ingestion job. Present (as defaults) from creation so the UI/dashboard
        # can render repo status uniformly.
        "repo_url": None,
        "repo_provider": None,
        "repo_branch": None,
        "repo_commit": None,
        "repo_ingest_status": "not_linked",
        "repo_last_synced_at": None,
        "repo_ingest_error": None,
        # QA test execution (CodeBuild test-runner) status.
        "qa_test_status": "not_run",
        "qa_test_summary": None,
        "qa_test_last_run_at": None,
        "assignment_history": [
            {
                "from": None,
                "to": initial_state,
                "actor": actor,
                "timestamp": now,
                "note": "created",
            }
        ],
    }

    try:
        _write_record(project_uuid, record)
    except ClientError as e:
        print(f"[PROJECT_API] S3 write error on create: {e}")
        return _response(500, {"error": str(e), "code": "S3_WRITE_FAILED"})

    return _response(201, record)


def _handle_list_projects(identity: dict) -> dict:
    """GET /projects — list all project records visible to the caller."""
    try:
        records = _list_records()
    except ClientError as e:
        print(f"[PROJECT_API] S3 list error: {e}")
        return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})

    return _response(200, {"projects": records})


def _handle_get_project(project_uuid: str) -> dict:
    """GET /projects/{projectId} — read one project record.

    Broad visibility: the record is returned regardless of ``created_by`` so
    both personas resolve the same ``artifact_prefix`` for the same
    ``project_uuid`` (design Sections 2a/2c, Req 3.3). Not owner-gated.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    try:
        record, _ = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] S3 read error: {e}")
        return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    return _response(200, record)


def _handle_transition(project_uuid: str, body: dict, identity: dict) -> dict:
    """POST /projects/{projectId}/transition — perform a handoff transition."""
    try:
        return _apply_transition(project_uuid, body, identity)
    except ClientError as e:
        print(f"[PROJECT_API] S3 error on transition: {e}")
        return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})


def _handle_reassign(project_uuid: str, body: dict, identity: dict) -> dict:
    """POST /projects/{projectId}/reassign — manual reassignment.

    Reassignment is validated through the same registry transition rules as a
    normal transition (design Section 3b); only registry-permitted targets are
    accepted.
    """
    try:
        return _apply_transition(project_uuid, body, identity)
    except ClientError as e:
        print(f"[PROJECT_API] S3 error on reassign: {e}")
        return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})


def _handle_upload_url(project_uuid: str, body: dict, identity: dict) -> dict:
    """POST /projects/{projectId}/upload-url — presigned PUT for an upload.

    Returns a short-lived presigned PUT URL constrained to the exact
    ``uploaded/`` key for the project's artifact prefix (design Section 7a,
    Req 5.1). Keeps the browser's standing S3 permissions read-only.

    Owner-gated (BA action): the record is read first (404 when missing) and the
    caller is authorized against the record's current ``owning_role`` before any
    presigned URL is minted, so only the project's current owning role (the
    Business_Analyst while the project is in its requirements/draft phase) may
    request an upload URL (design Section 4b/7a, Req 5.1). The 403 occurs before
    the presigned PUT is generated.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    filename = (body.get("filename") or "").strip()
    if not filename:
        return _response(400, {"error": "'filename' is required", "code": "INVALID_REQUEST"})

    # Guard against path traversal: only a bare filename is allowed.
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        return _response(400, {"error": "Invalid filename", "code": "INVALID_REQUEST"})

    try:
        record, _ = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] S3 read error on upload-url: {e}")
        return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    # RBAC: only the current owning role may request an upload URL. Checked
    # after the read but before any presigned URL is minted so a non-owning
    # caller never obtains write access to the uploaded/ key (Req 5.1).
    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    bucket = os.environ["PROJECT_BUCKET"]
    key = UPLOAD_KEY_TEMPLATE.format(prefix=record["artifact_prefix"], filename=filename)

    try:
        url = _s3().generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        )
    except ClientError as e:
        print(f"[PROJECT_API] Presigned URL error: {e}")
        return _response(500, {"error": str(e), "code": "PRESIGN_FAILED"})

    return _response(200, {"upload_url": url, "key": key, "expires_in": UPLOAD_URL_TTL_SECONDS})


def _handle_link_repo(project_uuid: str, body: dict, identity: dict) -> dict:
    """POST /projects/{projectId}/link-repo — attach a Git repository (C7).

    After implementing from the construction brief in an external IDE (e.g.
    Kiro) and pushing to GitHub/GitLab, the Software Developer links the pushed
    repository to the project. This records the repo metadata on the project
    record so the (forthcoming) ingestion job can clone the code into the
    project's artifact prefix and the downstream QA/DevOps personas can act on
    it (design: Git round-trip C7).

    Owner-gated exactly like /upload-url: the record is read first (404 when
    missing) and the caller is authorized against the record's current
    ``owning_role`` before any mutation, so only the project's current owning
    role (the Software Developer while the project sits in ``assigned_to_dev``)
    may link a repo (design Section 4b, Req 2.2/2.3). The repo URL is validated
    against an allowlist of public Git hosts (SSRF guard) before it is stored.

    Records metadata only: it does not itself advance the handoff state (the
    developer submits via the normal transition route, which is gated on a
    linked repo) and does not yet trigger ingestion (the CodeBuild ingest job is
    wired in a later step — C7-D). The write is conditional on the record ETag,
    so a concurrent update is rejected with HTTP 409.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    provider, normalized_url = _validate_repo_url(body.get("repo_url") or "")
    if provider is None:
        return _response(
            400,
            {"error": "'repo_url' must be an https URL to a supported Git host "
                      "(github.com or gitlab.com) with an owner/repo path",
             "code": "INVALID_REPO_URL"},
        )

    branch = (body.get("branch") or _DEFAULT_REPO_BRANCH).strip() or _DEFAULT_REPO_BRANCH

    try:
        record, etag = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] S3 read error on link-repo: {e}")
        return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    # RBAC: only the current owning role may link a repo. Checked after the read
    # but before any mutation so a non-owning caller never changes the record
    # (design Section 4b, Req 2.2/2.3).
    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    # Optional private-repo token: stored in Secrets Manager only (never on the
    # project record, never logged, never returned) so the ingest job can clone
    # a private repository. Stored before the record write so a credential
    # failure surfaces without half-linking the repo.
    git_token = body.get("git_token")
    if git_token:
        try:
            _store_git_token(project_uuid, git_token)
        except ClientError as e:
            # Log the error TYPE only — never the message/token.
            print(
                f"[PROJECT_API] Failed to store git token for {project_uuid}: "
                f"{type(e).__name__}"
            )
            return _response(
                500,
                {"error": "Failed to store repository credentials",
                 "code": "SECRET_WRITE_FAILED"},
            )

    record["repo_url"] = normalized_url
    record["repo_provider"] = provider
    record["repo_branch"] = branch
    # (Re)linking resets ingest tracking; the ingestion job (C7-D) will set the
    # synced commit, status and timestamp.
    record["repo_commit"] = None
    record["repo_ingest_status"] = "pending"
    record["repo_last_synced_at"] = None
    record["repo_ingest_error"] = None

    try:
        _write_record(project_uuid, record, etag=etag)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("PreconditionFailed", "412"):
            return _response(
                409,
                {"error": "Project was modified concurrently; refetch and retry",
                 "code": "ETAG_MISMATCH"},
            )
        print(f"[PROJECT_API] S3 write error on link-repo: {e}")
        return _response(500, {"error": str(e), "code": "S3_WRITE_FAILED"})

    # Note: ingestion is NOT triggered here. It is triggered when the project
    # actually enters ``implementation_submitted`` (the submit transition), so
    # the ingest-callback always observes the submit state and can advance to QA
    # deterministically (avoids a link/submit race).
    return _response(200, record)


def _maybe_trigger_ingest(project_uuid: str, record: dict) -> None:
    """Start the ingest CodeBuild for ``project_uuid`` when one is configured."""
    codebuild_project = os.environ.get(_INGEST_CODEBUILD_PROJECT_ENV)
    if not codebuild_project:
        return
    try:
        _codebuild().start_build(
            projectName=codebuild_project,
            environmentVariablesOverride=[
                {"name": "PROJECT_UUID", "value": project_uuid, "type": "PLAINTEXT"},
                {"name": "REPO_URL", "value": record.get("repo_url") or "", "type": "PLAINTEXT"},
                {"name": "REPO_BRANCH", "value": record.get("repo_branch") or _DEFAULT_REPO_BRANCH, "type": "PLAINTEXT"},
                {"name": "PROJECT_SLUG", "value": record.get("artifact_prefix") or "", "type": "PLAINTEXT"},
            ],
        )
    except ClientError as e:
        print(f"[PROJECT_API] StartBuild failed for ingest of {project_uuid}: {e}")


def _maybe_trigger_tests(project_uuid: str, record: dict) -> None:
    """Start the QA test-runner CodeBuild for ``project_uuid`` when configured."""
    codebuild_project = os.environ.get(_TEST_CODEBUILD_PROJECT_ENV)
    if not codebuild_project:
        return
    try:
        _codebuild().start_build(
            projectName=codebuild_project,
            environmentVariablesOverride=[
                {"name": "PROJECT_UUID", "value": project_uuid, "type": "PLAINTEXT"},
                {"name": "PROJECT_SLUG", "value": record.get("artifact_prefix") or "", "type": "PLAINTEXT"},
            ],
        )
    except ClientError as e:
        print(f"[PROJECT_API] StartBuild failed for tests of {project_uuid}: {e}")


def _handle_ingest_callback(project_uuid: str, body: dict) -> dict:
    """POST /projects/{projectId}/ingest-callback — ingestion pipeline callback.

    Called by the ingest CodeBuild job (authorized at the API Gateway with
    ``AWS_IAM`` — the CodeBuild role — NOT the Cognito user authorizer), so this
    handler is a trusted internal caller and is deliberately NOT owner-gated by
    Cognito group; the IAM policy on the route is the trust anchor.

    Records the sync outcome on the project record and, on success, advances the
    handoff state from ``implementation_submitted`` to its forward successor
    (``assigned_to_qa``) through the shared registry — ``validate_transition``
    remains the single authority on transition legality. The advance is
    idempotent: if the project is already past ``implementation_submitted`` (a
    duplicate/late callback) the status fields are updated but the state is left
    unchanged. Uses an ETag-conditional write (409 on concurrent modification).
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    status = (body.get("status") or "").strip().lower()
    if status not in ("ready", "failed"):
        return _response(
            400,
            {"error": "'status' must be 'ready' or 'failed'", "code": "INVALID_REQUEST"},
        )

    try:
        record, etag = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] S3 read error on ingest-callback: {e}")
        return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    record["repo_commit"] = body.get("commit") or record.get("repo_commit")
    record["repo_last_synced_at"] = _now_iso()
    record["repo_ingest_status"] = "ready" if status == "ready" else "failed"
    # Capture/clear a human-readable failure reason (e.g. "repository exceeds
    # the ingest size limit") so the UI can explain a failed ingest.
    record["repo_ingest_error"] = None if status == "ready" else (
        body.get("reason") or "ingestion failed"
    )

    advanced = False
    if status == "ready":
        current_state = record.get("handoff_state")
        # Only advance out of the submit state; a duplicate/late callback for a
        # project already in QA (or beyond) updates status only.
        if current_state == IMPLEMENTATION_SUBMITTED_STATE:
            target_state = registry.forward(current_state)
            if target_state and registry.validate_transition(current_state, target_state):
                record["handoff_state"] = target_state
                record["owning_role"] = registry.owning_role(target_state)
                record.setdefault("assignment_history", []).append(
                    {
                        "from": current_state,
                        "to": target_state,
                        "actor": _SYSTEM_INGEST_ACTOR,
                        "timestamp": _now_iso(),
                        "note": "repository ingested; advanced to QA",
                    }
                )
                advanced = True

    try:
        _write_record(project_uuid, record, etag=etag)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("PreconditionFailed", "412"):
            return _response(
                409,
                {"error": "Project was modified concurrently; refetch and retry",
                 "code": "ETAG_MISMATCH"},
            )
        print(f"[PROJECT_API] S3 write error on ingest-callback: {e}")
        return _response(500, {"error": str(e), "code": "S3_WRITE_FAILED"})

    return _response(200, {"record": record, "advanced": advanced})


def _handle_run_tests(project_uuid: str, identity: dict) -> dict:
    """POST /projects/{projectId}/run-tests — start the QA test-runner (C7).

    Owner-gated (only the project's current owning role — the QA Engineer while
    the project sits in a QA state — may run tests). Marks the record
    ``qa_test_status="running"`` and starts the test CodeBuild, which pulls the
    ingested source, runs the auto-detected test suite, writes
    ``aidlc-docs/qa/test-results.md``, and calls back with pass/fail. Degrades
    gracefully (no build started) when no test project is configured.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    try:
        record, etag = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] S3 read error on run-tests: {e}")
        return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    record["qa_test_status"] = "running"
    record["qa_test_last_run_at"] = _now_iso()

    try:
        _write_record(project_uuid, record, etag=etag)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("PreconditionFailed", "412"):
            return _response(
                409,
                {"error": "Project was modified concurrently; refetch and retry",
                 "code": "ETAG_MISMATCH"},
            )
        print(f"[PROJECT_API] S3 write error on run-tests: {e}")
        return _response(500, {"error": str(e), "code": "S3_WRITE_FAILED"})

    _maybe_trigger_tests(project_uuid, record)
    return _response(202, record)


def _handle_test_callback(project_uuid: str, body: dict) -> dict:
    """POST /projects/{projectId}/test-callback — QA test-runner callback.

    Trusted internal caller (invoked by the test CodeBuild via direct Lambda
    invoke, not Cognito-gated). Records the test outcome (``passed``/``failed``)
    and a short summary; does not itself change the handoff state (the QA
    Engineer decides whether to advance or bounce). ETag-conditional write.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    status = (body.get("status") or "").strip().lower()
    if status not in ("passed", "failed"):
        return _response(
            400,
            {"error": "'status' must be 'passed' or 'failed'", "code": "INVALID_REQUEST"},
        )

    try:
        record, etag = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] S3 read error on test-callback: {e}")
        return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    record["qa_test_status"] = status
    record["qa_test_summary"] = body.get("summary") or None
    record["qa_test_last_run_at"] = _now_iso()

    try:
        _write_record(project_uuid, record, etag=etag)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("PreconditionFailed", "412"):
            return _response(
                409,
                {"error": "Project was modified concurrently; refetch and retry",
                 "code": "ETAG_MISMATCH"},
            )
        print(f"[PROJECT_API] S3 write error on test-callback: {e}")
        return _response(500, {"error": str(e), "code": "S3_WRITE_FAILED"})

    return _response(200, {"record": record})


def _handle_delete_project(project_uuid: str, identity: dict) -> dict:
    """DELETE /projects/{projectId} — delete a project record.

    Owner-gated: the record is read first (404 when missing) and the caller is
    authorized against the record's current ``owning_role`` before any delete,
    so a non-owning caller never removes a project (design Section 4b, Req
    2.2/2.3). The 403 occurs before the S3 ``delete_object`` mutation.
    """
    if not _is_valid_uuid(project_uuid):
        return _response(
            400,
            {"error": "'projectId' must match [A-Za-z0-9_-] (1-256 chars)",
             "code": "INVALID_REQUEST"},
        )

    try:
        record, _ = _read_record(project_uuid)
    except ClientError as e:
        print(f"[PROJECT_API] S3 read error on delete: {e}")
        return _response(500, {"error": str(e), "code": "S3_READ_FAILED"})

    if record is None:
        return _response(404, {"error": "Project not found", "code": "NOT_FOUND"})

    denied = _authorize_owner(record, identity)
    if denied is not None:
        return denied

    bucket = os.environ["PROJECT_BUCKET"]

    # Remove the project's artifact tree first (best-effort) so a future
    # same-named project does not inherit stale state. Failures here are
    # logged but do not block deletion of the record itself.
    artifact_prefix = record.get("artifact_prefix") or record.get("project_id") or ""
    artifacts_removed = 0
    try:
        artifacts_removed = _delete_artifact_prefix(bucket, artifact_prefix)
    except ClientError as e:
        print(
            f"[PROJECT_API] Artifact cleanup error for prefix "
            f"'{artifact_prefix}': {e}"
        )

    key = _record_key(project_uuid)
    try:
        _s3().delete_object(Bucket=bucket, Key=key)
    except ClientError as e:
        print(f"[PROJECT_API] S3 delete error: {e}")
        return _response(500, {"error": str(e), "code": "S3_DELETE_FAILED"})

    return _response(
        200,
        {
            "deleted": True,
            "project_uuid": project_uuid,
            "artifacts_removed": artifacts_removed,
        },
    )


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def _parse_body(event: dict):
    """Parse the JSON request body; returns ``(body, error_response)``."""
    raw_body = event.get("body") or "{}"
    try:
        return json.loads(raw_body), None
    except (json.JSONDecodeError, TypeError):
        return None, _response(400, {"error": "Invalid JSON body", "code": "INVALID_REQUEST"})


def handler(event, context):
    """Lambda entry point for API Gateway proxy integration."""
    print(f"[PROJECT_API] Event: {json.dumps(event)[:500]}")

    http_method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}
    project_uuid = path_params.get("projectId", "")

    identity = _caller_identity(event)

    # Collection routes ------------------------------------------------------

    if http_method == "POST" and resource == "/projects":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_create_project(body, identity)

    if http_method == "GET" and resource == "/projects":
        return _handle_list_projects(identity)

    # Item routes ------------------------------------------------------------

    if resource.startswith("/projects/{projectId}") and not project_uuid:
        return _response(400, {"error": "Missing projectId", "code": "INVALID_REQUEST"})

    if http_method == "GET" and resource == "/projects/{projectId}":
        return _handle_get_project(project_uuid)

    if http_method == "DELETE" and resource == "/projects/{projectId}":
        return _handle_delete_project(project_uuid, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/transition":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_transition(project_uuid, body, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/reassign":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_reassign(project_uuid, body, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/upload-url":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_upload_url(project_uuid, body, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/link-repo":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_link_repo(project_uuid, body, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/ingest-callback":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_ingest_callback(project_uuid, body)

    if http_method == "POST" and resource == "/projects/{projectId}/run-tests":
        return _handle_run_tests(project_uuid, identity)

    if http_method == "POST" and resource == "/projects/{projectId}/test-callback":
        body, err = _parse_body(event)
        if err:
            return err
        return _handle_test_callback(project_uuid, body)

    return _response(
        400, {"error": f"Unknown route: {http_method} {resource}", "code": "INVALID_REQUEST"}
    )
