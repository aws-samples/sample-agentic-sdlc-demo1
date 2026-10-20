"""Atlassian REST API Proxy Lambda.

Acts as a proxy between AgentCore Gateway and the Atlassian REST APIs.
The Gateway registers this Lambda as an MCP target with a static tool schema.
This Lambda translates MCP tool calls into Atlassian REST API calls using
an API token stored in Secrets Manager.

Supports Confluence and Jira REST APIs directly — bypasses mcp.atlassian.com
which blocks AWS Lambda IPs via Cloudflare.
"""

import json
import os
import urllib.request
import urllib.error
import base64

import boto3

_secrets_client = None
_credential_cache: dict[str, dict] = {}
_current_project_id: str | None = None


def _get_credentials(project_id: str | None = None) -> dict:
    """Fetch credentials from Secrets Manager (cached per project_id).

    Returns dict with 'authorization' header value and 'cloud_id',
    or an error dict with 'error' key if credentials cannot be resolved.
    """
    global _secrets_client, _credential_cache

    if project_id is not None:
        cache_key = project_id
        secret_name = f"aidlc/projects/{project_id}/atlassian"
    else:
        cache_key = "__global__"
        secret_name = os.environ["ATLASSIAN_SECRET_ARN"]

    if cache_key in _credential_cache:
        return _credential_cache[cache_key]

    if _secrets_client is None:
        _secrets_client = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_REGION", "us-west-2"),
        )

    try:
        response = _secrets_client.get_secret_value(SecretId=secret_name)
    except _secrets_client.exceptions.ResourceNotFoundException:
        return {"error": f"Atlassian credentials not configured for project {project_id}"}

    secret = json.loads(response["SecretString"])

    email = secret.get("email")
    api_token = secret.get("api_token")
    if not email or not api_token:
        return {"error": f"Invalid secret format for project {project_id}"}

    secret["authorization"] = "Basic " + base64.b64encode(f"{email}:{api_token}".encode()).decode()
    _credential_cache[cache_key] = secret
    return secret


def _atlassian_request(method: str, url: str, body: dict = None, project_id: str | None = None) -> dict:
    """Make an authenticated request to the Atlassian REST API."""
    effective_project_id = project_id if project_id is not None else _current_project_id
    creds = _get_credentials(effective_project_id)
    if "error" in creds:
        return creds
    auth_header = creds["authorization"]

    data = json.dumps(body).encode("utf-8") if body else None
    headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print(f"[PROXY] REST {method} {url}")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310  # nosemgrep: dynamic-urllib-use-detected — URL from Atlassian API config
            resp_body = resp.read().decode("utf-8")
            parsed = json.loads(resp_body) if resp_body else {}
            print(f"[PROXY] REST response: status={resp.status} body_preview={str(parsed)[:200]}")
            return {"success": True, "status": resp.status, "body": parsed}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        print(f"[PROXY] HTTP error {e.code}: {error_body[:500]}")
        return {"success": False, "status": e.code, "error": error_body}
    except Exception as e:
        print(f"[PROXY] Request error: {e}")
        return {"success": False, "error": str(e)}


def _get_cloud_id() -> str:
    """Get the Atlassian cloud ID from credentials or by querying the API."""
    creds = _get_credentials(_current_project_id)
    if "error" in creds:
        raise ValueError(creds["error"])
    if "cloud_id" in creds:
        return creds["cloud_id"]

    # Auto-discover cloud ID
    result = _atlassian_request("GET", "https://api.atlassian.com/oauth/token/accessible-resources")
    if result["success"] and result["body"]:
        resources = result["body"]
        if isinstance(resources, list) and resources:
            cloud_id = resources[0]["id"]
            print(f"[PROXY] Auto-discovered cloud_id: {cloud_id}")
            return cloud_id
    raise ValueError("Could not determine Atlassian cloud ID")


# ---------------------------------------------------------------------------
# Project/Space auto-creation helpers
# ---------------------------------------------------------------------------

def _project_key_from_name(name: str) -> str:
    """Derive a valid Jira project key from a project name.
    Jira keys: uppercase letters only, 2-10 chars.
    """
    import re
    # Take first letter of each word, uppercase, max 10 chars
    words = re.sub(r'[^a-zA-Z0-9\s]', '', name).split()
    if not words:
        return "PROJ"
    key = ''.join(w[0].upper() for w in words if w)[:10]
    if len(key) < 2:
        key = re.sub(r'[^A-Z0-9]', '', name.upper())[:10]
    return key or "PROJ"


def ensure_jira_project(project_name: str, project_key: str = "") -> dict:
    """Ensure a Jira project exists, creating it if needed.

    Returns dict with 'key', 'id', 'name', 'existed' (bool).
    """
    cloud_id = _get_cloud_id()
    if not project_key:
        project_key = _project_key_from_name(project_name)

    # Check if project exists
    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/project/{project_key}"
    result = _atlassian_request("GET", url)
    if result["success"]:
        proj = result["body"]
        print(f"[PROXY] Jira project '{project_key}' already exists: {proj.get('name')}")
        return {"key": proj["key"], "id": proj["id"], "name": proj["name"], "existed": True}

    # Create the project
    print(f"[PROXY] Creating Jira project: key={project_key} name={project_name}")
    create_url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/project"

    # Get account ID for project lead
    me_result = _atlassian_request("GET", f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/myself")
    account_id = me_result["body"].get("accountId", "") if me_result["success"] else ""

    body = {
        "key": project_key,
        "name": project_name,
        "projectTypeKey": "software",
        "projectTemplateKey": "com.pyxis.greenhopper.jira:gh-scrum-template",
        "description": f"Project created by iCode AI-SDLC for {project_name}",
        "leadAccountId": account_id,
    }
    create_result = _atlassian_request("POST", create_url, body)
    if create_result["success"]:
        proj = create_result["body"]
        print(f"[PROXY] Created Jira project: key={proj.get('key')} id={proj.get('id')}")
        return {"key": proj["key"], "id": proj["id"], "name": project_name, "existed": False}

    error = create_result.get("error", str(create_result.get("status")))
    print(f"[PROXY] Failed to create Jira project: {error}")
    return {"key": project_key, "id": "", "name": project_name, "existed": False, "error": error}


def ensure_confluence_space(space_name: str, space_key: str = "") -> dict:
    """Ensure a Confluence space exists, creating it if needed.

    Returns dict with 'key', 'id', 'name', 'existed' (bool).
    """
    cloud_id = _get_cloud_id()
    if not space_key:
        space_key = _project_key_from_name(space_name)

    # Check if space exists
    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api/space/{space_key}"
    result = _atlassian_request("GET", url)
    if result["success"]:
        space = result["body"]
        print(f"[PROXY] Confluence space '{space_key}' already exists: {space.get('name')}")
        return {"key": space["key"], "id": space.get("id", ""), "name": space["name"], "existed": True}

    # Create the space
    print(f"[PROXY] Creating Confluence space: key={space_key} name={space_name}")
    create_url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api/space"
    body = {
        "key": space_key,
        "name": space_name,
        "description": {
            "plain": {
                "value": f"Space created by iCode AI-SDLC for {space_name}",
                "representation": "plain",
            }
        },
    }
    create_result = _atlassian_request("POST", create_url, body)
    if create_result["success"]:
        space = create_result["body"]
        print(f"[PROXY] Created Confluence space: key={space.get('key')}")
        return {"key": space["key"], "id": space.get("id", ""), "name": space_name, "existed": False}

    error = create_result.get("error", str(create_result.get("status")))
    print(f"[PROXY] Failed to create Confluence space: {error}")
    return {"key": space_key, "id": "", "name": space_name, "existed": False, "error": error}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def confluence_create_page(space_key: str, title: str, content: str, parent_page_id: str = "") -> str:
    cloud_id = _get_cloud_id()

    # Ensure the space exists, create if not
    space_info = ensure_confluence_space(space_key, space_key)
    actual_space_key = space_info["key"]
    if space_info.get("error") and not space_info.get("existed"):
        print(f"[PROXY] Space creation failed, attempting with original key: {space_key}")
        actual_space_key = space_key

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api/content"

    body = {
        "type": "page",
        "title": title,
        "space": {"key": actual_space_key},
        "body": {
            "storage": {
                "value": f"<p>{content}</p>",
                "representation": "storage",
            }
        },
    }
    if parent_page_id:
        body["ancestors"] = [{"id": parent_page_id}]

    print(f"[PROXY] confluence_create_page: space={actual_space_key} title={title}")
    result = _atlassian_request("POST", url, body)
    print(f"[PROXY] confluence_create_page result: success={result.get('success')} status={result.get('status')} body={str(result.get('body', result.get('error', '')))[:300]}")
    if result["success"]:
        page_id = result["body"].get("id", "unknown")
        page_url = result["body"].get("_links", {}).get("webui", "")
        return f"Created Confluence page '{title}' (ID: {page_id}) at {page_url}"
    return f"Error creating Confluence page: {result.get('error', result.get('status'))}"


def jira_create_issue(project_key: str, summary: str, issue_type: str, description: str = "", parent_key: str = "") -> str:
    cloud_id = _get_cloud_id()

    # Ensure the project exists, create if not
    project_info = ensure_jira_project(project_key, project_key)
    actual_project_key = project_info["key"]
    if project_info.get("error") and not project_info.get("existed"):
        print(f"[PROXY] Project creation failed, attempting with original key: {project_key}")
        actual_project_key = project_key

    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue"

    body = {
        "fields": {
            "project": {"key": actual_project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
    }
    if description:
        body["fields"]["description"] = {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
        }
    if parent_key:
        body["fields"]["parent"] = {"key": parent_key}

    print(f"[PROXY] jira_create_issue: project={project_key} type={issue_type} summary={summary[:50]}")
    result = _atlassian_request("POST", url, body)
    print(f"[PROXY] jira_create_issue result: success={result.get('success')} status={result.get('status')} body={str(result.get('body', result.get('error', '')))[:300]}")
    if result["success"]:
        issue_key = result["body"].get("key", "unknown")
        return f"Created Jira {issue_type} {issue_key}: {summary}"
    return f"Error creating Jira issue: {result.get('error', result.get('status'))}"


def jira_transition_issue(issue_key: str, transition_name: str) -> str:
    cloud_id = _get_cloud_id()

    # Get available transitions
    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/transitions"
    result = _atlassian_request("GET", url)
    if not result["success"]:
        return f"Error getting transitions for {issue_key}: {result.get('error')}"

    transitions = result["body"].get("transitions", [])
    transition_id = None
    for t in transitions:
        if t["name"].lower() == transition_name.lower():
            transition_id = t["id"]
            break

    if not transition_id:
        available = [t["name"] for t in transitions]
        return f"Transition '{transition_name}' not found. Available: {available}"

    # Apply transition
    result = _atlassian_request("POST", url, {"transition": {"id": transition_id}})
    if result["success"] or result.get("status") == 204:
        return f"Transitioned {issue_key} to '{transition_name}'"
    return f"Error transitioning {issue_key}: {result.get('error')}"


def jira_get_board(project_key: str) -> str:
    cloud_id = _get_cloud_id()

    # Ensure project exists first
    ensure_jira_project(project_key, project_key)

    # Filter boards by project key to get the correct board
    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/agile/1.0/board?projectKeyOrId={project_key}&type=scrum"
    result = _atlassian_request("GET", url)
    if result["success"]:
        boards = result["body"].get("values", [])
        if boards:
            board = boards[0]
            print(f"[PROXY] jira_get_board: found board id={board['id']} name={board['name']} for project={project_key}")
            return json.dumps({"id": board["id"], "name": board["name"], "type": board["type"]})
        # No scrum board found — try kanban
        url_kanban = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/agile/1.0/board?projectKeyOrId={project_key}"
        result2 = _atlassian_request("GET", url_kanban)
        if result2["success"]:
            boards2 = result2["body"].get("values", [])
            if boards2:
                board = boards2[0]
                print(f"[PROXY] jira_get_board: found board id={board['id']} name={board['name']} for project={project_key}")
                return json.dumps({"id": board["id"], "name": board["name"], "type": board["type"]})
        return f"No boards found for project {project_key}"
    return f"Error getting board: {result.get('error')}"


def jira_create_sprint(board_id: int, name: str, goal: str = "") -> str:
    cloud_id = _get_cloud_id()
    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/agile/1.0/sprint"
    body = {"name": name, "originBoardId": board_id}
    if goal:
        body["goal"] = goal

    result = _atlassian_request("POST", url, body)
    if result["success"]:
        sprint_id = result["body"].get("id", "unknown")
        return f"Created sprint '{name}' (ID: {sprint_id})"
    return f"Error creating sprint: {result.get('error')}"


def jira_list_backlog_issues(board_id: int, project_key: str = "") -> str:
    """List issues in the backlog for a Jira board.

    Args:
        board_id: The Jira board ID.
        project_key: Optional project key to filter by.

    Returns:
        JSON string with list of issues (key, summary, type).
    """
    cloud_id = _get_cloud_id()
    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/agile/1.0/board/{board_id}/backlog"
    if project_key:
        url += f"?jql=project={project_key}"

    print(f"[PROXY] jira_list_backlog_issues: board_id={board_id}")
    result = _atlassian_request("GET", url)
    if result["success"]:
        issues = []
        for issue in result["body"].get("issues", []):
            issues.append({
                "key": issue.get("key", ""),
                "summary": issue.get("fields", {}).get("summary", ""),
                "issue_type": issue.get("fields", {}).get("issuetype", {}).get("name", ""),
            })
        return json.dumps({"issues": issues, "total": len(issues)})
    return f"Error listing backlog: {result.get('error')}"


def jira_move_to_sprint(sprint_id: int, issue_keys: list) -> str:
    """Move issues to a sprint.

    Args:
        sprint_id: The sprint ID to move issues into.
        issue_keys: List of issue keys (e.g. ["PROJ-1", "PROJ-2"]).

    Returns:
        Success or error message.
    """
    cloud_id = _get_cloud_id()
    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/agile/1.0/sprint/{sprint_id}/issue"
    body = {"issues": issue_keys}

    print(f"[PROXY] jira_move_to_sprint: sprint_id={sprint_id} issues={issue_keys}")
    result = _atlassian_request("POST", url, body)
    if result["success"] or result.get("status") == 204:
        return f"Moved {len(issue_keys)} issues to sprint {sprint_id}: {', '.join(issue_keys)}"
    return f"Error moving issues to sprint: {result.get('error')}"


def confluence_add_label(page_id: str, label: str) -> str:
    """Add a label to a Confluence page.

    Args:
        page_id: The Confluence page ID.
        label: The label name to add (e.g. "pending-review").

    Returns:
        Success or error message.
    """
    cloud_id = _get_cloud_id()
    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api/content/{page_id}/label"
    body = [{"prefix": "global", "name": label}]

    print(f"[PROXY] confluence_add_label: page_id={page_id} label={label}")
    result = _atlassian_request("POST", url, body)
    if result["success"]:
        return f"Added label '{label}' to page {page_id}"
    return f"Error adding label: {result.get('error', result.get('status'))}"


def confluence_get_labels(page_id: str) -> str:
    """Get all labels on a Confluence page.

    Args:
        page_id: The Confluence page ID.

    Returns:
        JSON string with list of label names.
    """
    cloud_id = _get_cloud_id()
    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api/content/{page_id}/label"

    print(f"[PROXY] confluence_get_labels: page_id={page_id}")
    result = _atlassian_request("GET", url)
    if result["success"]:
        labels = [l.get("name", "") for l in result["body"].get("results", [])]
        return json.dumps({"labels": labels, "page_id": page_id})
    return f"Error getting labels: {result.get('error', result.get('status'))}"


# ---------------------------------------------------------------------------
# MCP tool dispatch
# ---------------------------------------------------------------------------

_TOOL_HANDLERS = {
    "confluence_create_page": lambda args: confluence_create_page(**args),
    "confluence_update_page": lambda args: f"confluence_update_page not yet implemented",
    "confluence_get_page": lambda args: f"confluence_get_page not yet implemented",
    "confluence_search": lambda args: f"confluence_search not yet implemented",
    "confluence_add_label": lambda args: confluence_add_label(**args),
    "confluence_get_labels": lambda args: confluence_get_labels(**args),
    "jira_create_issue": lambda args: jira_create_issue(**args),
    "jira_update_issue": lambda args: f"jira_update_issue not yet implemented",
    "jira_transition_issue": lambda args: jira_transition_issue(**args),
    "jira_get_issue": lambda args: f"jira_get_issue not yet implemented",
    "jira_create_sprint": lambda args: jira_create_sprint(**args),
    "jira_get_board": lambda args: jira_get_board(**args),
    "jira_list_backlog_issues": lambda args: jira_list_backlog_issues(**args),
    "jira_move_to_sprint": lambda args: jira_move_to_sprint(**args),
    "ensure_jira_project": lambda args: json.dumps(ensure_jira_project(**args)),
    "ensure_confluence_space": lambda args: json.dumps(ensure_confluence_space(**args)),
}


def _handle_mcp_request(mcp_request: dict) -> dict:
    """Handle an MCP JSON-RPC request."""
    method = mcp_request.get("method", "")
    req_id = mcp_request.get("id")
    params = mcp_request.get("params", {})

    print(f"[PROXY] MCP method: {method}")

    if method == "tools/list":
        tools = [
            {"name": name, "description": f"Atlassian {name}", "inputSchema": {"type": "object"}}
            for name in _TOOL_HANDLERS
        ]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        # Strip project_id — it's used for credential routing, not passed to handlers
        tool_args = {k: v for k, v in tool_args.items() if k != "project_id"}
        handler = _TOOL_HANDLERS.get(tool_name)
        if not handler:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}
        try:
            result_text = handler(tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": result_text}]},
            }
        except Exception as e:
            print(f"[PROXY] Tool error: {e}")
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}

    # Unknown method
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def _extract_project_id(event: dict, context) -> str | None:
    """Extract project_id with priority: tool args > session context > env var > None."""
    # 1. Tool call arguments (direct invocation or MCP JSON-RPC)
    project_id = event.get("project_id")
    if project_id is None:
        params = event.get("params", {})
        project_id = params.get("arguments", {}).get("project_id")

    # 2. Session context header
    if project_id is None:
        try:
            project_id = context.client_context.custom.get("project_id")
        except (AttributeError, TypeError):
            pass

    # 3. Env var fallback
    if project_id is None:
        project_id = os.environ.get("DEFAULT_PROJECT_ID")

    return project_id


def handler(event, context):
    """Lambda handler.

    The AgentCore Gateway can invoke this Lambda in two ways:
    1. MCP JSON-RPC format: {"method": "tools/call", "params": {"name": "...", "arguments": {...}}}
    2. Direct tool invocation: the tool arguments dict is passed directly as the event,
       with the tool name in context.client_context.custom["bedrockAgentCoreToolName"].
    """
    print(f"[PROXY] Received: {json.dumps(event)[:500]}")

    # Extract tool name from Lambda context (Gateway sets this)
    gateway_tool_name = None
    try:
        custom = context.client_context.custom
        if custom and "bedrockAgentCoreToolName" in custom:
            full_name = custom["bedrockAgentCoreToolName"]
            _DELIM = "___"
            if _DELIM in full_name:
                gateway_tool_name = full_name[full_name.index(_DELIM) + len(_DELIM):]
            else:
                gateway_tool_name = full_name
            print(f"[PROXY] Tool name from context: {gateway_tool_name}")
    except (AttributeError, TypeError, KeyError):
        pass

    if isinstance(event, list):
        return [_handle_event(req, gateway_tool_name, context) for req in event]

    return _handle_event(event, gateway_tool_name, context)


def _handle_event(event: dict, gateway_tool_name: str = None, context=None) -> dict:
    """Handle a single event — either MCP JSON-RPC or direct tool invocation."""
    global _current_project_id
    extracted = _extract_project_id(event, context)
    # Only update _current_project_id when we have a real value — preserve the
    # last known project_id for tools that don't pass it (e.g. confluence_add_label)
    if extracted is not None:
        _current_project_id = extracted
    print(f"[PROXY] project_id={_current_project_id}")

    # MCP JSON-RPC format
    if "method" in event:
        return _handle_mcp_request(event)

    # Direct tool invocation from Gateway Lambda target
    # Priority: gateway context > event field > argument inference
    tool_name = gateway_tool_name or event.get("_tool_name") or event.get("tool_name")

    if not tool_name:
        # Infer tool from argument signature
        if "space_name" in event:
            tool_name = "ensure_confluence_space"
        elif "project_name" in event:
            tool_name = "ensure_jira_project"
        elif "space_key" in event and "title" in event and "content" in event:
            tool_name = "confluence_create_page"
        elif "page_id" in event and "label" in event:
            tool_name = "confluence_add_label"
        elif "page_id" in event and "label" not in event and "title" not in event:
            tool_name = "confluence_get_labels"
        elif "project_key" in event and "summary" in event and "issue_type" in event:
            tool_name = "jira_create_issue"
        elif "issue_key" in event and "transition_name" in event:
            tool_name = "jira_transition_issue"
        elif "sprint_id" in event and "issue_keys" in event:
            tool_name = "jira_move_to_sprint"
        elif "board_id" in event and "name" in event:
            tool_name = "jira_create_sprint"
        elif "board_id" in event and "name" not in event:
            tool_name = "jira_list_backlog_issues" if "project_key" not in event or len(event) > 2 else "jira_get_board"
        elif "project_key" in event and len(event) <= 2:
            tool_name = "jira_get_board"
        else:
            print(f"[PROXY] Cannot infer tool from event keys: {list(event.keys())}")
            return {"error": f"Cannot determine tool from event: {list(event.keys())}"}

    print(f"[PROXY] Direct invocation: tool={tool_name} args={list(event.keys())}")
    handler_fn = _TOOL_HANDLERS.get(tool_name)
    if not handler_fn:
        return {"error": f"Unknown tool: {tool_name}"}

    # Remove internal keys and routing-only keys before passing to handler
    args = {k: v for k, v in event.items() if not k.startswith("_") and k != "project_id"}
    try:
        result_text = handler_fn(args)
        print(f"[PROXY] Tool result: {result_text[:200]}")
        return {"result": result_text}
    except Exception as e:
        print(f"[PROXY] Tool error: {e}")
        return {"error": str(e)}
