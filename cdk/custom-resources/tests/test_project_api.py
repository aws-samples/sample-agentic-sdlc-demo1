"""Unit tests for the Project API Lambda handler (task 2.7).

Exercises ``project_api.handler`` through its real public surface (API Gateway
proxy events in, ``{statusCode, body}`` out) against an in-memory fake S3
(:mod:`fake_s3`) so the handler's real S3 branches run: ``get_object`` /
``put_object`` with conditional ``IfMatch``, ``list_objects_v2`` pagination,
``delete_object`` and presigned-URL signing. The fake raises the genuine
``botocore.exceptions.ClientError`` the handler catches, so the 404 / 409 paths
are real, not stubbed.

These tests assert *observable* outcomes — HTTP status codes and the bytes
actually persisted to the Project_Store — rather than re-deriving the handler's
own decisions (no calls to ``registry.validate_transition`` from the tests), so
they cannot mirror-test the implementation.

Covers:
  * Create / list / read happy paths; list is cross-creator (Req 3.2).
  * Illegal transition -> 422 with an unchanged stored record (Req 4.4).
  * ETag mismatch (concurrent writer) -> 409 (Req 4.5).
  * Exactly one assignment_history entry appended per transition (Req 4.6).
  * RBAC: non-owning role on transition/reassign/delete -> 403, record
    unchanged; read routes succeed for any group (Req 2.2, 2.3).
  * request-changes records the supplied note in history (Req 4.6 / 7).

Validates: Requirements 2.2, 2.3, 3.2, 4.4, 4.5, 4.6
"""

import json
import os
import sys

import pytest

# Make the Lambda module importable regardless of pytest's rootdir/cwd:
# project_api.py / workflow_registry.py live one directory up from this
# tests/ package (mirrors icode-agent/tests/test_workflow_registry.py).
_CUSTOM_RESOURCES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _CUSTOM_RESOURCES_DIR not in sys.path:
    sys.path.insert(0, _CUSTOM_RESOURCES_DIR)
# fake_s3 sits beside this test file.
_TESTS_DIR = os.path.dirname(__file__)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import project_api  # noqa: E402
from fake_s3 import FakeS3Client  # noqa: E402

BUCKET = "test-project-bucket"
BA_ROLE = "requirements-analyst"
SA_ROLE = "system-architect"


# ---------------------------------------------------------------------------
# Fixtures + event/request helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def s3(monkeypatch):
    """Install a fresh in-memory fake S3 and point the handler at it."""
    fake = FakeS3Client()
    monkeypatch.setenv("PROJECT_BUCKET", BUCKET)
    monkeypatch.setattr(project_api, "_s3", lambda: fake)
    return fake


def _event(method, resource, project_id=None, body=None, groups=None,
           actor="user@example.com"):
    """Build an API Gateway proxy event with Cognito authorizer claims."""
    claims = {"email": actor}
    if groups is not None:
        # cognito:groups may arrive as a list or a comma-separated string;
        # tests pass a list (the handler normalizes both).
        claims["cognito:groups"] = groups
    return {
        "httpMethod": method,
        "resource": resource,
        "pathParameters": {"projectId": project_id} if project_id else None,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {"claims": claims}},
    }


def _json_body(response):
    """Parse the JSON body out of a handler response."""
    return json.loads(response["body"])


def _record_key(project_uuid):
    return f"_platform/projects/{project_uuid}.json"


def _create(s3, uuid, name, groups=None, actor="ba@example.com", **extra):
    """Drive a create through the handler and return the parsed record."""
    body = {"name": name, "project_uuid": uuid, **extra}
    resp = project_api.handler(
        _event("POST", "/projects", body=body, groups=groups, actor=actor), None
    )
    assert resp["statusCode"] == 201, resp
    return _json_body(resp)


def _transition(s3, uuid, *, action=None, target_state=None, note=None,
                groups, actor="user@example.com", resource_suffix="transition"):
    """Drive a transition/reassign through the handler; return the raw response."""
    body = {}
    if action is not None:
        body["action"] = action
    if target_state is not None:
        body["target_state"] = target_state
    if note is not None:
        body["note"] = note
    return project_api.handler(
        _event(
            "POST",
            f"/projects/{{projectId}}/{resource_suffix}",
            project_id=uuid,
            body=body,
            groups=groups,
            actor=actor,
        ),
        None,
    )


# ===========================================================================
# 1. Create happy path (Req 4.1 baseline, supports 3.x)
# ===========================================================================


def test_create_persists_draft_record_with_initial_state(s3):
    """POST /projects -> 201 and a fully initialized, persisted draft record."""
    resp = project_api.handler(
        _event(
            "POST",
            "/projects",
            body={"name": "My Cool Project", "project_uuid": "proj-1"},
            groups=[BA_ROLE],
            actor="ba@example.com",
        ),
        None,
    )

    assert resp["statusCode"] == 201
    record = _json_body(resp)

    # Initial handoff state machine seeding (registry initial_state == draft).
    assert record["handoff_state"] == "draft"
    assert record["owning_role"] == BA_ROLE
    assert record["created_by"] == "ba@example.com"
    # artifact_prefix is the slug of the name, and equals project_id.
    assert record["artifact_prefix"] == "my_cool_project"
    assert record["project_id"] == "my_cool_project"
    assert record["project_uuid"] == "proj-1"

    # Exactly one seed history entry: None -> draft, note "created".
    history = record["assignment_history"]
    assert len(history) == 1
    assert history[0]["from"] is None
    assert history[0]["to"] == "draft"
    assert history[0]["actor"] == "ba@example.com"
    assert history[0]["note"] == "created"
    assert history[0]["timestamp"]  # non-empty timestamp present

    # The record is actually persisted in the store (not just returned).
    raw = s3.raw_body(BUCKET, _record_key("proj-1"))
    assert raw is not None
    assert json.loads(raw.decode("utf-8"))["handoff_state"] == "draft"


def test_create_requires_name(s3):
    """A create without a name is a 400, and nothing is persisted."""
    resp = project_api.handler(
        _event("POST", "/projects", body={"project_uuid": "no-name"}, groups=[BA_ROLE]),
        None,
    )
    assert resp["statusCode"] == 400
    assert s3.raw_body(BUCKET, _record_key("no-name")) is None


# ===========================================================================
# 2. List returns cross-creator projects (Req 3.2)
# ===========================================================================


def test_list_returns_projects_from_different_creators(s3):
    """GET /projects returns every record regardless of created_by (3.2)."""
    _create(s3, "p-alice", "Alice Project", actor="alice@example.com")
    _create(s3, "p-bob", "Bob Project", actor="bob@example.com")

    # A THIRD identity (carol, SA role) lists — she created neither project.
    resp = project_api.handler(
        _event("GET", "/projects", groups=[SA_ROLE], actor="carol@example.com"), None
    )
    assert resp["statusCode"] == 200
    projects = _json_body(resp)["projects"]

    by_uuid = {p["project_uuid"]: p for p in projects}
    assert {"p-alice", "p-bob"} <= set(by_uuid)
    # Cross-creator visibility: the two records were made by different users.
    assert by_uuid["p-alice"]["created_by"] == "alice@example.com"
    assert by_uuid["p-bob"]["created_by"] == "bob@example.com"


def test_read_by_id_returns_same_record_and_prefix(s3):
    """GET /projects/{id} returns the same record/artifact_prefix as created."""
    created = _create(s3, "p-read", "Read Me", actor="alice@example.com")

    resp = project_api.handler(
        _event("GET", "/projects/{projectId}", project_id="p-read",
               groups=[SA_ROLE], actor="someone-else@example.com"),
        None,
    )
    assert resp["statusCode"] == 200
    record = _json_body(resp)
    assert record["project_uuid"] == "p-read"
    assert record["artifact_prefix"] == created["artifact_prefix"]
    assert record["handoff_state"] == "draft"


# ===========================================================================
# 3. Forward transition by owner: state advances, exactly one history entry
#    (Req 4.5, 4.6)
# ===========================================================================


def test_forward_transition_by_owner_advances_state_and_appends_one_history(s3):
    """A forward transition by the owning role advances state + appends 1 entry."""
    _create(s3, "p-fwd", "Forward Project", actor="ba@example.com")

    resp = _transition(s3, "p-fwd", action="assign", groups=[BA_ROLE],
                       actor="ba@example.com")

    assert resp["statusCode"] == 200
    record = _json_body(resp)
    # draft's forward successor is requirements_ready; still BA-owned.
    assert record["handoff_state"] == "requirements_ready"
    assert record["owning_role"] == BA_ROLE

    # Exactly one NEW history entry beyond the seed (seed=1 -> now 2).
    history = record["assignment_history"]
    assert len(history) == 2
    latest = history[-1]
    assert latest["from"] == "draft"
    assert latest["to"] == "requirements_ready"
    assert latest["actor"] == "ba@example.com"
    assert latest["timestamp"]

    # Persisted state matches the response.
    stored = json.loads(s3.raw_body(BUCKET, _record_key("p-fwd")).decode("utf-8"))
    assert stored["handoff_state"] == "requirements_ready"
    assert len(stored["assignment_history"]) == 2


def test_transition_across_ownership_updates_owning_role(s3):
    """Advancing into the SA segment flips owning_role to system-architect."""
    _create(s3, "p-cross", "Cross", actor="ba@example.com")
    # draft -> requirements_ready (BA owns draft).
    _transition(s3, "p-cross", action="assign", groups=[BA_ROLE], actor="ba@example.com")
    # requirements_ready -> assigned_to_sa (BA still owns requirements_ready).
    resp = _transition(s3, "p-cross", action="assign", groups=[BA_ROLE],
                       actor="ba@example.com")

    assert resp["statusCode"] == 200
    record = _json_body(resp)
    assert record["handoff_state"] == "assigned_to_sa"
    assert record["owning_role"] == SA_ROLE  # ownership moved to the SA
    # Three entries: seed + two transitions, one appended per transition.
    assert len(record["assignment_history"]) == 3


# ===========================================================================
# 4. Illegal transition -> 422, record unchanged (Req 4.4)
# ===========================================================================


def test_illegal_transition_returns_422_and_leaves_record_unchanged(s3):
    """An illegal target by the owner -> 422 and byte-for-byte unchanged record."""
    _create(s3, "p-illegal", "Illegal", actor="ba@example.com")
    before = s3.raw_body(BUCKET, _record_key("p-illegal"))

    # draft -> ready_for_dev is neither the forward successor nor a
    # request-changes edge. Caller IS the owner (BA) so RBAC passes and the
    # validation layer is what rejects it.
    resp = _transition(s3, "p-illegal", target_state="ready_for_dev",
                       groups=[BA_ROLE], actor="ba@example.com")

    assert resp["statusCode"] == 422
    assert _json_body(resp)["code"] == "ILLEGAL_TRANSITION"

    # The stored object is unchanged — same bytes, still draft, still 1 entry.
    after = s3.raw_body(BUCKET, _record_key("p-illegal"))
    assert after == before
    stored = json.loads(after.decode("utf-8"))
    assert stored["handoff_state"] == "draft"
    assert len(stored["assignment_history"]) == 1


# ===========================================================================
# 5. ETag mismatch -> 409 (Req 4.5)
# ===========================================================================


def test_concurrent_write_causes_etag_mismatch_409(s3):
    """A concurrent writer between read and write yields HTTP 409 (lost-update guard)."""
    _create(s3, "p-etag", "Etag", actor="ba@example.com")

    # Simulate a concurrent writer: the instant the handler reads the record,
    # mutate the stored object so its ETag no longer matches the IfMatch the
    # handler captured. The handler's conditional PutObject must then fail.
    def _concurrent_writer(client):
        stored = json.loads(client.raw_body(BUCKET, _record_key("p-etag")).decode("utf-8"))
        stored["name"] = "Changed by a concurrent writer"
        client.seed(BUCKET, _record_key("p-etag"), json.dumps(stored).encode("utf-8"))

    s3.on_next_get(_concurrent_writer)

    resp = _transition(s3, "p-etag", action="assign", groups=[BA_ROLE],
                       actor="ba@example.com")

    assert resp["statusCode"] == 409
    assert _json_body(resp)["code"] == "ETAG_MISMATCH"

    # The handler's transition did NOT overwrite the concurrent writer's value.
    stored = json.loads(s3.raw_body(BUCKET, _record_key("p-etag")).decode("utf-8"))
    assert stored["name"] == "Changed by a concurrent writer"
    assert stored["handoff_state"] == "draft"


# ===========================================================================
# 6. RBAC on state-changing routes (Req 2.2, 2.3)
# ===========================================================================


def test_transition_by_non_owning_role_is_403_and_record_unchanged(s3):
    """A non-owning role transitioning -> 403 before any mutation (2.2/2.3)."""
    _create(s3, "p-rbac-t", "Rbac Transition", actor="ba@example.com")
    before = s3.raw_body(BUCKET, _record_key("p-rbac-t"))

    # Project is in draft (owned by BA); an SA attempts the forward transition.
    resp = _transition(s3, "p-rbac-t", action="assign", groups=[SA_ROLE],
                       actor="sa@example.com")

    assert resp["statusCode"] == 403
    assert _json_body(resp)["code"] == "FORBIDDEN"
    # Record is untouched.
    assert s3.raw_body(BUCKET, _record_key("p-rbac-t")) == before


def test_reassign_by_non_owning_role_is_403_and_record_unchanged(s3):
    """A non-owning role on /reassign -> 403, record unchanged (2.2/2.3)."""
    _create(s3, "p-rbac-r", "Rbac Reassign", actor="ba@example.com")
    before = s3.raw_body(BUCKET, _record_key("p-rbac-r"))

    resp = _transition(s3, "p-rbac-r", action="assign", groups=[SA_ROLE],
                       actor="sa@example.com", resource_suffix="reassign")

    assert resp["statusCode"] == 403
    assert s3.raw_body(BUCKET, _record_key("p-rbac-r")) == before


def test_delete_by_non_owning_role_is_403_and_record_preserved(s3):
    """A non-owning role deleting -> 403 and the record still exists (2.2/2.3)."""
    _create(s3, "p-rbac-d", "Rbac Delete", actor="ba@example.com")

    resp = project_api.handler(
        _event("DELETE", "/projects/{projectId}", project_id="p-rbac-d",
               groups=[SA_ROLE], actor="sa@example.com"),
        None,
    )

    assert resp["statusCode"] == 403
    assert _json_body(resp)["code"] == "FORBIDDEN"
    # Not deleted.
    assert s3.raw_body(BUCKET, _record_key("p-rbac-d")) is not None
    assert s3.delete_calls == 0


def test_delete_by_owning_role_succeeds(s3):
    """The owning role CAN delete — confirms 403 above is about role, not route."""
    _create(s3, "p-del-ok", "Delete OK", actor="ba@example.com")

    resp = project_api.handler(
        _event("DELETE", "/projects/{projectId}", project_id="p-del-ok",
               groups=[BA_ROLE], actor="ba@example.com"),
        None,
    )
    assert resp["statusCode"] == 200
    assert _json_body(resp)["deleted"] is True
    assert s3.raw_body(BUCKET, _record_key("p-del-ok")) is None


def test_delete_removes_artifact_prefix_and_respects_boundary(s3):
    """Deleting a project also removes its artifact tree so a future same-named
    project starts clean (prevents cross-project state bleed), while a sibling
    prefix that merely shares a leading substring is preserved by the
    trailing-slash boundary."""
    rec = _create(s3, "p-del-art", "Cleanup Me", actor="ba@example.com")
    prefix = rec["artifact_prefix"]  # slug of "Cleanup Me" -> "cleanup_me"

    # Artifacts under the project's own prefix (should be removed)...
    s3.seed(BUCKET, f"{prefix}/aidlc-docs/requirements/requirements.md", b"reqs")
    s3.seed(BUCKET, f"{prefix}/aidlc-docs/audit.md", b"audit")
    # ...and a sibling prefix sharing a leading substring (must survive).
    s3.seed(BUCKET, f"{prefix}_other/aidlc-docs/keep.md", b"keep")

    resp = project_api.handler(
        _event("DELETE", "/projects/{projectId}", project_id="p-del-art",
               groups=[BA_ROLE], actor="ba@example.com"),
        None,
    )

    assert resp["statusCode"] == 200
    body = _json_body(resp)
    assert body["deleted"] is True
    assert body["artifacts_removed"] == 2
    # Record and the project's artifact tree are gone.
    assert s3.raw_body(BUCKET, _record_key("p-del-art")) is None
    assert s3.raw_body(BUCKET, f"{prefix}/aidlc-docs/requirements/requirements.md") is None
    assert s3.raw_body(BUCKET, f"{prefix}/aidlc-docs/audit.md") is None
    # The sibling prefix is untouched (boundary guard).
    assert s3.raw_body(BUCKET, f"{prefix}_other/aidlc-docs/keep.md") == b"keep"


def test_read_routes_open_regardless_of_group(s3):
    """GET list and GET one succeed for any (or no) group (read routes not gated)."""
    _create(s3, "p-open", "Open Read", actor="ba@example.com")

    # List with an unrelated group.
    list_resp = project_api.handler(
        _event("GET", "/projects", groups=["some-other-group"],
               actor="stranger@example.com"),
        None,
    )
    assert list_resp["statusCode"] == 200
    assert any(p["project_uuid"] == "p-open"
               for p in _json_body(list_resp)["projects"])

    # Read one with NO groups claim at all.
    get_resp = project_api.handler(
        _event("GET", "/projects/{projectId}", project_id="p-open",
               groups=None, actor="nobody@example.com"),
        None,
    )
    assert get_resp["statusCode"] == 200
    assert _json_body(get_resp)["project_uuid"] == "p-open"


# ===========================================================================
# 7. request-changes records the note in history (Req 4.6)
# ===========================================================================


def test_request_changes_records_note_in_history(s3):
    """request-changes bounces assigned_to_sa -> requirements_ready, noting the reason."""
    _create(s3, "p-rc", "Request Changes", actor="ba@example.com")
    # Drive forward into assigned_to_sa (both forwards are BA-owned states).
    _transition(s3, "p-rc", action="assign", groups=[BA_ROLE], actor="ba@example.com")
    _transition(s3, "p-rc", action="assign", groups=[BA_ROLE], actor="ba@example.com")

    # SA owns assigned_to_sa; request-changes with an explicit note.
    resp = _transition(
        s3, "p-rc", action="request-changes",
        note="Please clarify the non-functional requirements",
        groups=[SA_ROLE], actor="sa@example.com",
    )

    assert resp["statusCode"] == 200
    record = _json_body(resp)
    # Registry-defined backward reassignment: assigned_to_sa -> requirements_ready.
    assert record["handoff_state"] == "requirements_ready"
    assert record["owning_role"] == BA_ROLE

    latest = record["assignment_history"][-1]
    assert latest["from"] == "assigned_to_sa"
    assert latest["to"] == "requirements_ready"
    assert latest["actor"] == "sa@example.com"
    assert latest["note"] == "Please clarify the non-functional requirements"


# ===========================================================================
# 8. Git round-trip (C7): POST /projects/{id}/link-repo + submit gating
# ===========================================================================

DEV_ROLE = "software-developer"


def _link_repo(s3, uuid, *, repo_url, branch=None, groups, actor="dev@example.com"):
    """Drive a link-repo call through the handler; return the raw response."""
    body = {"repo_url": repo_url}
    if branch is not None:
        body["branch"] = branch
    return project_api.handler(
        _event("POST", "/projects/{projectId}/link-repo", project_id=uuid,
               body=body, groups=groups, actor=actor),
        None,
    )


def _drive_to_assigned_to_dev(s3, uuid):
    """Forward the project from draft all the way to assigned_to_dev.

    Uses the owning role at each step so every forward transition passes RBAC:
    draft/requirements_ready are BA-owned; assigned_to_sa..ready_for_dev are
    SA-owned; the forward out of ready_for_dev lands on assigned_to_dev.
    """
    _transition(s3, uuid, action="assign", groups=[BA_ROLE], actor="ba@example.com")            # draft -> requirements_ready
    _transition(s3, uuid, action="assign", groups=[BA_ROLE], actor="ba@example.com")            # requirements_ready -> assigned_to_sa
    _transition(s3, uuid, action="approve", groups=[SA_ROLE], actor="sa@example.com")           # assigned_to_sa -> in_design
    _transition(s3, uuid, action="design-complete", groups=[SA_ROLE], actor="sa@example.com")   # in_design -> design_ready
    _transition(s3, uuid, action="design-complete", groups=[SA_ROLE], actor="sa@example.com")   # design_ready -> ready_for_dev
    resp = _transition(s3, uuid, action="assign", groups=[SA_ROLE], actor="sa@example.com")     # ready_for_dev -> assigned_to_dev
    assert _json_body(resp)["handoff_state"] == "assigned_to_dev", _json_body(resp)


def test_create_record_has_repo_defaults(s3):
    """A freshly created record carries the C7 repo fields at their defaults."""
    record = _create(s3, "p-repo-defaults", "Repo Defaults", actor="ba@example.com")
    assert record["repo_url"] is None
    assert record["repo_provider"] is None
    assert record["repo_branch"] is None
    assert record["repo_commit"] is None
    assert record["repo_ingest_status"] == "not_linked"
    assert record["repo_last_synced_at"] is None
    assert record["repo_ingest_error"] is None


def test_link_repo_by_owning_dev_stores_metadata(s3):
    """The owning developer links a GitHub repo -> 200 and metadata persisted."""
    _create(s3, "p-link", "Link Repo", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-link")

    resp = _link_repo(s3, "p-link", repo_url="https://github.com/acme/widget.git",
                      branch="release", groups=[DEV_ROLE])

    assert resp["statusCode"] == 200, resp
    record = _json_body(resp)
    assert record["repo_url"] == "https://github.com/acme/widget.git"
    assert record["repo_provider"] == "github"
    assert record["repo_branch"] == "release"
    assert record["repo_ingest_status"] == "pending"
    # Handoff state is unchanged by linking (the developer submits separately).
    assert record["handoff_state"] == "assigned_to_dev"

    # Persisted, not just returned.
    stored = json.loads(s3.raw_body(BUCKET, _record_key("p-link")).decode("utf-8"))
    assert stored["repo_url"] == "https://github.com/acme/widget.git"
    assert stored["repo_ingest_status"] == "pending"


def test_link_repo_defaults_branch_to_main(s3):
    """Omitting the branch defaults it to main."""
    _create(s3, "p-branch", "Branch Default", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-branch")

    resp = _link_repo(s3, "p-branch", repo_url="https://gitlab.com/acme/widget",
                      groups=[DEV_ROLE])
    assert resp["statusCode"] == 200
    record = _json_body(resp)
    assert record["repo_provider"] == "gitlab"
    assert record["repo_branch"] == "main"


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://github.com/acme/widget",          # not https
        "https://evil.com/acme/widget",           # host not allowlisted
        "https://github.com/acme",                # missing repo (no owner/repo)
        "https://github.com/../../etc/passwd",    # path traversal
        "https://169.254.169.254/acme/widget",    # SSRF to metadata endpoint
        "ftp://github.com/acme/widget",           # wrong scheme
        "https://github.com.evil.com/acme/widget",  # look-alike host
        "not-a-url",
        "",
    ],
)
def test_link_repo_rejects_disallowed_urls(s3, bad_url):
    """A repo URL outside the https + allowlisted-host + owner/repo shape -> 400,
    record unchanged (SSRF guard)."""
    _create(s3, "p-badurl", "Bad URL", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-badurl")
    before = s3.raw_body(BUCKET, _record_key("p-badurl"))

    resp = _link_repo(s3, "p-badurl", repo_url=bad_url, groups=[DEV_ROLE])

    assert resp["statusCode"] == 400, (bad_url, resp)
    assert _json_body(resp)["code"] == "INVALID_REPO_URL"
    # Record is untouched by a rejected link.
    assert s3.raw_body(BUCKET, _record_key("p-badurl")) == before


def test_link_repo_by_non_owning_role_is_403_and_record_unchanged(s3):
    """A non-owning role linking a repo -> 403 before any mutation (2.2/2.3)."""
    _create(s3, "p-link-rbac", "Link RBAC", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-link-rbac")
    before = s3.raw_body(BUCKET, _record_key("p-link-rbac"))

    # Project is assigned_to_dev (owned by the developer); an SA attempts to link.
    resp = _link_repo(s3, "p-link-rbac", repo_url="https://github.com/acme/widget",
                      groups=[SA_ROLE], actor="sa@example.com")

    assert resp["statusCode"] == 403
    assert _json_body(resp)["code"] == "FORBIDDEN"
    assert s3.raw_body(BUCKET, _record_key("p-link-rbac")) == before


def test_link_repo_missing_project_is_404(s3):
    """Linking a repo to a non-existent project -> 404."""
    resp = _link_repo(s3, "p-nope", repo_url="https://github.com/acme/widget",
                      groups=[DEV_ROLE])
    assert resp["statusCode"] == 404
    assert _json_body(resp)["code"] == "NOT_FOUND"


def test_submit_transition_blocked_until_repo_linked(s3):
    """assigned_to_dev -> implementation_submitted is rejected (422) with no repo,
    then permitted once a repo is linked."""
    _create(s3, "p-submit", "Submit Gate", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-submit")

    # No repo yet: the forward submit is a legal registry transition but the C7
    # precondition rejects it and leaves the record unchanged.
    before = s3.raw_body(BUCKET, _record_key("p-submit"))
    blocked = _transition(s3, "p-submit", action="submit", groups=[DEV_ROLE],
                          actor="dev@example.com")
    assert blocked["statusCode"] == 422
    assert _json_body(blocked)["code"] == "REPO_NOT_LINKED"
    assert s3.raw_body(BUCKET, _record_key("p-submit")) == before

    # Link a repo, then the submit forward succeeds.
    _link_repo(s3, "p-submit", repo_url="https://github.com/acme/widget",
               groups=[DEV_ROLE])
    ok = _transition(s3, "p-submit", action="submit", groups=[DEV_ROLE],
                     actor="dev@example.com")
    assert ok["statusCode"] == 200, _json_body(ok)
    record = _json_body(ok)
    assert record["handoff_state"] == "implementation_submitted"
    assert record["owning_role"] == DEV_ROLE


# ===========================================================================
# 9. Git round-trip ingestion (C7-D): StartBuild trigger + ingest-callback
# ===========================================================================


class _FakeCodeBuild:
    """Records start_build calls so the trigger can be asserted."""

    def __init__(self):
        self.calls = []

    def start_build(self, **kwargs):
        self.calls.append(kwargs)
        return {"build": {"id": "build-1"}}


def _ingest_callback(s3, uuid, *, status, commit=None, reason=None):
    body = {"status": status}
    if commit is not None:
        body["commit"] = commit
    if reason is not None:
        body["reason"] = reason
    return project_api.handler(
        _event("POST", "/projects/{projectId}/ingest-callback",
               project_id=uuid, body=body),
        None,
    )


def _drive_to_implementation_submitted(s3, uuid):
    """Reach implementation_submitted: to assigned_to_dev, link a repo, submit."""
    _drive_to_assigned_to_dev(s3, uuid)
    _link_repo(s3, uuid, repo_url="https://github.com/acme/widget", groups=[DEV_ROLE])
    resp = _transition(s3, uuid, action="submit", groups=[DEV_ROLE], actor="dev@example.com")
    assert _json_body(resp)["handoff_state"] == "implementation_submitted", _json_body(resp)


def test_submit_triggers_codebuild_when_configured(s3, monkeypatch):
    """Submitting the implementation (entering implementation_submitted) starts
    the ingest build with the project identity passed as environment overrides.
    Linking alone does not start a build."""
    fake_cb = _FakeCodeBuild()
    monkeypatch.setenv("REPO_INGEST_CODEBUILD_PROJECT", "aidlc-repo-ingest")
    monkeypatch.setattr(project_api, "_codebuild", lambda: fake_cb)

    _create(s3, "p-cb", "CodeBuild Trigger", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-cb")
    _link_repo(s3, "p-cb", repo_url="https://github.com/acme/widget",
               branch="release", groups=[DEV_ROLE])
    # Linking alone does not start a build.
    assert fake_cb.calls == []

    # Submitting (assigned_to_dev -> implementation_submitted) does.
    resp = _transition(s3, "p-cb", action="submit", groups=[DEV_ROLE], actor="dev@example.com")
    assert resp["statusCode"] == 200
    assert _json_body(resp)["handoff_state"] == "implementation_submitted"

    assert len(fake_cb.calls) == 1
    call = fake_cb.calls[0]
    assert call["projectName"] == "aidlc-repo-ingest"
    overrides = {e["name"]: e["value"] for e in call["environmentVariablesOverride"]}
    assert overrides["PROJECT_UUID"] == "p-cb"
    assert overrides["REPO_URL"] == "https://github.com/acme/widget"
    assert overrides["REPO_BRANCH"] == "release"


def test_submit_does_not_trigger_codebuild_when_unconfigured(s3, monkeypatch):
    """With no ingest project configured, submitting still succeeds and no build
    is started (graceful degradation)."""
    fake_cb = _FakeCodeBuild()
    monkeypatch.delenv("REPO_INGEST_CODEBUILD_PROJECT", raising=False)
    monkeypatch.setattr(project_api, "_codebuild", lambda: fake_cb)

    _create(s3, "p-nocb", "No CodeBuild", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-nocb")

    assert fake_cb.calls == []
    stored = json.loads(s3.raw_body(BUCKET, _record_key("p-nocb")).decode("utf-8"))
    assert stored["handoff_state"] == "implementation_submitted"


def test_ingest_callback_ready_advances_to_qa(s3):
    """A successful ingest callback advances implementation_submitted ->
    assigned_to_qa (owning role qa-engineer) with a system-actor history entry."""
    _create(s3, "p-ing", "Ingest OK", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-ing")

    resp = _ingest_callback(s3, "p-ing", status="ready", commit="abc123")

    assert resp["statusCode"] == 200, resp
    body = _json_body(resp)
    assert body["advanced"] is True
    record = body["record"]
    assert record["handoff_state"] == "assigned_to_qa"
    assert record["owning_role"] == "qa-engineer"
    assert record["repo_ingest_status"] == "ready"
    assert record["repo_commit"] == "abc123"
    assert record["repo_ingest_error"] is None
    latest = record["assignment_history"][-1]
    assert latest["from"] == "implementation_submitted"
    assert latest["to"] == "assigned_to_qa"
    assert latest["actor"] == "system:repo-ingest"

    # Persisted.
    stored = json.loads(s3.raw_body(BUCKET, _record_key("p-ing")).decode("utf-8"))
    assert stored["handoff_state"] == "assigned_to_qa"


def test_ingest_callback_failed_sets_status_without_advancing(s3):
    """A failed ingest callback marks repo_ingest_status=failed and does NOT
    advance the handoff state."""
    _create(s3, "p-ingfail", "Ingest Fail", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-ingfail")

    resp = _ingest_callback(s3, "p-ingfail", status="failed")

    assert resp["statusCode"] == 200
    body = _json_body(resp)
    assert body["advanced"] is False
    record = body["record"]
    assert record["repo_ingest_status"] == "failed"
    assert record["handoff_state"] == "implementation_submitted"  # unchanged


def test_ingest_callback_failed_records_reason(s3):
    """A failed ingest callback stores the human-readable reason (e.g. the size
    guardrail message) in repo_ingest_error and does not advance."""
    _create(s3, "p-ingreason", "Ingest Reason", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-ingreason")

    resp = _ingest_callback(
        s3, "p-ingreason", status="failed",
        reason="Repository exceeds the ingest size limit (120000 files, 4200000000 bytes)",
    )

    assert resp["statusCode"] == 200
    record = _json_body(resp)["record"]
    assert record["repo_ingest_status"] == "failed"
    assert record["repo_ingest_error"] == (
        "Repository exceeds the ingest size limit (120000 files, 4200000000 bytes)"
    )
    assert record["handoff_state"] == "implementation_submitted"  # unchanged


def test_ingest_callback_is_idempotent_when_not_in_submit_state(s3):
    """A duplicate/late ready callback for a project not in implementation_submitted
    updates status only and does not re-transition."""
    _create(s3, "p-ingdup", "Ingest Dup", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-ingdup")  # state is assigned_to_dev, not submitted
    _link_repo(s3, "p-ingdup", repo_url="https://github.com/acme/widget", groups=[DEV_ROLE])

    resp = _ingest_callback(s3, "p-ingdup", status="ready", commit="def456")

    assert resp["statusCode"] == 200
    body = _json_body(resp)
    assert body["advanced"] is False
    assert body["record"]["handoff_state"] == "assigned_to_dev"  # unchanged
    assert body["record"]["repo_ingest_status"] == "ready"
    assert body["record"]["repo_commit"] == "def456"


def test_ingest_callback_invalid_status_is_400(s3):
    _create(s3, "p-ingbad", "Ingest Bad", actor="ba@example.com")
    _drive_to_implementation_submitted(s3, "p-ingbad")
    resp = _ingest_callback(s3, "p-ingbad", status="in-progress")
    assert resp["statusCode"] == 400
    assert _json_body(resp)["code"] == "INVALID_REQUEST"


def test_ingest_callback_missing_project_is_404(s3):
    resp = _ingest_callback(s3, "p-ghost", status="ready")
    assert resp["statusCode"] == 404
    assert _json_body(resp)["code"] == "NOT_FOUND"


# ===========================================================================
# 10. Private-repo git token provisioning (Secrets Manager)
# ===========================================================================

from botocore.exceptions import ClientError as _BotoClientError  # noqa: E402


class _FakeSecrets:
    """Records Secrets Manager put/create; simulates absent-then-created."""

    def __init__(self, existing=False):
        self.exists = existing
        self.put_calls = []
        self.create_calls = []

    def put_secret_value(self, SecretId, SecretString):  # noqa: N803
        if not self.exists:
            raise _BotoClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "nf"}},
                "PutSecretValue",
            )
        self.put_calls.append((SecretId, SecretString))
        return {}

    def create_secret(self, Name, SecretString):  # noqa: N803
        self.create_calls.append((Name, SecretString))
        self.exists = True
        return {}


def _link_with_token(s3, uuid, token, *, repo_url="https://github.com/acme/private",
                     groups=None):
    return project_api.handler(
        _event("POST", "/projects/{projectId}/link-repo", project_id=uuid,
               body={"repo_url": repo_url, "git_token": token},
               groups=groups if groups is not None else [DEV_ROLE], actor="dev@example.com"),
        None,
    )


def test_link_repo_creates_git_secret_when_absent(s3, monkeypatch):
    """A supplied git_token is created in Secrets Manager on first use, and is
    never echoed back in the response."""
    fake_secrets = _FakeSecrets(existing=False)
    monkeypatch.setattr(project_api, "_secrets", lambda: fake_secrets)

    _create(s3, "p-tok-new", "Token New", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-tok-new")
    resp = _link_with_token(s3, "p-tok-new", "ghp_secrettoken123")

    assert resp["statusCode"] == 200, _json_body(resp)
    # put raised ResourceNotFoundException -> create_secret used.
    assert fake_secrets.put_calls == []
    assert len(fake_secrets.create_calls) == 1
    name, secret = fake_secrets.create_calls[0]
    assert name == "aidlc/projects/p-tok-new/git"
    assert secret == "ghp_secrettoken123"  # nosec B105 — test fixture token, not a real credential

    # Token is never on the record / in the response.
    body = _json_body(resp)
    assert "git_token" not in body
    assert "ghp_secrettoken123" not in json.dumps(body)


def test_link_repo_updates_existing_git_secret(s3, monkeypatch):
    """When the secret already exists, put_secret_value updates it."""
    fake_secrets = _FakeSecrets(existing=True)
    monkeypatch.setattr(project_api, "_secrets", lambda: fake_secrets)

    _create(s3, "p-tok-upd", "Token Upd", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-tok-upd")
    resp = _link_with_token(s3, "p-tok-upd", "glpat_rotated")

    assert resp["statusCode"] == 200
    assert len(fake_secrets.put_calls) == 1
    assert fake_secrets.put_calls[0] == ("aidlc/projects/p-tok-upd/git", "glpat_rotated")
    assert fake_secrets.create_calls == []


def test_link_repo_without_token_does_not_touch_secrets(s3, monkeypatch):
    """A public-repo link (no git_token) never calls Secrets Manager."""
    fake_secrets = _FakeSecrets(existing=True)
    monkeypatch.setattr(project_api, "_secrets", lambda: fake_secrets)

    _create(s3, "p-tok-none", "Token None", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-tok-none")
    resp = _link_repo(s3, "p-tok-none", repo_url="https://github.com/acme/public", groups=[DEV_ROLE])

    assert resp["statusCode"] == 200
    assert fake_secrets.put_calls == []
    assert fake_secrets.create_calls == []


def test_link_repo_secret_write_failure_is_500_and_record_unchanged(s3, monkeypatch):
    """If storing the token fails (non-NotFound error), the link returns 500 and
    the record is not mutated."""
    class _BrokenSecrets:
        def put_secret_value(self, **_):
            raise _BotoClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                "PutSecretValue",
            )

    monkeypatch.setattr(project_api, "_secrets", lambda: _BrokenSecrets())
    _create(s3, "p-tok-fail", "Token Fail", actor="ba@example.com")
    _drive_to_assigned_to_dev(s3, "p-tok-fail")
    before = s3.raw_body(BUCKET, _record_key("p-tok-fail"))

    resp = _link_with_token(s3, "p-tok-fail", "tok")

    assert resp["statusCode"] == 500
    assert _json_body(resp)["code"] == "SECRET_WRITE_FAILED"
    # Record not mutated (repo not linked) because the secret write happens
    # before the record write.
    assert s3.raw_body(BUCKET, _record_key("p-tok-fail")) == before


# ===========================================================================
# 11. QA test-runner: run-tests (QA-owner-gated) + test-callback
# ===========================================================================

QA_ROLE = "qa-engineer"


def _drive_to_assigned_to_qa(s3, uuid):
    """Reach assigned_to_qa: submit implementation, then a ready ingest callback."""
    _drive_to_implementation_submitted(s3, uuid)
    resp = _ingest_callback(s3, uuid, status="ready", commit="c0ffee")
    assert _json_body(resp)["record"]["handoff_state"] == "assigned_to_qa", _json_body(resp)


def _run_tests(s3, uuid, *, groups, actor="qa@example.com"):
    return project_api.handler(
        _event("POST", "/projects/{projectId}/run-tests", project_id=uuid,
               groups=groups, actor=actor),
        None,
    )


def _test_callback(s3, uuid, *, status, summary=None):
    body = {"status": status}
    if summary is not None:
        body["summary"] = summary
    return project_api.handler(
        _event("POST", "/projects/{projectId}/test-callback", project_id=uuid, body=body),
        None,
    )


def test_create_record_has_qa_test_defaults(s3):
    record = _create(s3, "p-qadef", "QA Defaults", actor="ba@example.com")
    assert record["qa_test_status"] == "not_run"
    assert record["qa_test_summary"] is None
    assert record["qa_test_last_run_at"] is None


def test_run_tests_by_qa_owner_sets_running_and_triggers_build(s3, monkeypatch):
    fake_cb = _FakeCodeBuild()
    monkeypatch.setenv("REPO_TEST_CODEBUILD_PROJECT", "aidlc-repo-test")
    monkeypatch.setattr(project_api, "_codebuild", lambda: fake_cb)

    _create(s3, "p-runtests", "Run Tests", actor="ba@example.com")
    _drive_to_assigned_to_qa(s3, "p-runtests")

    resp = _run_tests(s3, "p-runtests", groups=[QA_ROLE])
    assert resp["statusCode"] == 202, _json_body(resp)
    rec = _json_body(resp)
    assert rec["qa_test_status"] == "running"
    assert rec["qa_test_last_run_at"]
    assert len(fake_cb.calls) == 1
    overrides = {e["name"]: e["value"] for e in fake_cb.calls[0]["environmentVariablesOverride"]}
    assert overrides["PROJECT_UUID"] == "p-runtests"


def test_run_tests_by_non_owner_is_403(s3):
    _create(s3, "p-rt-rbac", "RT RBAC", actor="ba@example.com")
    _drive_to_assigned_to_qa(s3, "p-rt-rbac")
    # Project is QA-owned; a developer cannot start the tests.
    resp = _run_tests(s3, "p-rt-rbac", groups=[DEV_ROLE], actor="dev@example.com")
    assert resp["statusCode"] == 403
    assert _json_body(resp)["code"] == "FORBIDDEN"


def test_test_callback_records_passed_status_and_summary(s3):
    _create(s3, "p-tcb", "Test CB", actor="ba@example.com")
    _drive_to_assigned_to_qa(s3, "p-tcb")

    resp = _test_callback(s3, "p-tcb", status="passed", summary="42 passed in 3.1s")
    assert resp["statusCode"] == 200
    rec = _json_body(resp)["record"]
    assert rec["qa_test_status"] == "passed"
    assert rec["qa_test_summary"] == "42 passed in 3.1s"
    assert rec["qa_test_last_run_at"]
    # Handoff state is unchanged (QA decides whether to advance).
    assert rec["handoff_state"] == "assigned_to_qa"


def test_test_callback_failed_status(s3):
    _create(s3, "p-tcbf", "Test CB Fail", actor="ba@example.com")
    _drive_to_assigned_to_qa(s3, "p-tcbf")
    resp = _test_callback(s3, "p-tcbf", status="failed", summary="1 failed")
    assert resp["statusCode"] == 200
    assert _json_body(resp)["record"]["qa_test_status"] == "failed"


def test_test_callback_invalid_status_is_400(s3):
    _create(s3, "p-tcbbad", "Test CB Bad", actor="ba@example.com")
    _drive_to_assigned_to_qa(s3, "p-tcbbad")
    resp = _test_callback(s3, "p-tcbbad", status="maybe")
    assert resp["statusCode"] == 400
