"""Interactive CLI for Agentic SDLC Transformation.

Runs the agent in human-in-the-loop mode using the Strands `handoff_to_user`
tool. The agent pauses and waits for your input whenever it needs a decision,
answer, or approval.

Usage:
    # Local mode (default)
    .venv/bin/python run.py --project-name CYOA --project-path /tmp/CYOA "Build a CYOA app"
    .venv/bin/python run.py                    # prompts for all inputs

    # AgentCore WebSocket mode (connects to deployed Runtime)
    .venv/bin/python run.py --agentcore --projectname CYOA "Build a CYOA app"
"""

import argparse
import asyncio
import json
import os
import sys

# Add the icode-agent package root to sys.path so imports resolve correctly
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__), "..", "agentcore-runtime", "icode-agent"
    ),
)

from main import main_interactive, main_sync


def _fetch_cognito_token(region, ssm_prefix, secret_id):
    """Fetch Cognito OAuth token and runtime config from AWS."""
    import boto3
    import urllib.request
    import urllib.parse

    ssm = boto3.client("ssm", region_name=region)
    sm = boto3.client("secretsmanager", region_name=region)

    def ssm_get(name):
        return ssm.get_parameter(
            Name=f"{ssm_prefix}{name}"
        )["Parameter"]["Value"]

    print("  Fetching configuration from SSM …")
    client_id = ssm_get("cognito/client-id")
    token_endpoint = ssm_get("cognito/token-endpoint")
    runtime_arn = ssm_get("runtime/arn")

    print(f"  Client ID:      {client_id[:12]}…")
    print(f"  Token endpoint: {token_endpoint}")
    print(f"  Runtime ARN:    {runtime_arn}")

    print("  Fetching client secret from Secrets Manager …")
    client_secret = sm.get_secret_value(SecretId=secret_id)["SecretString"]

    print("  Requesting OAuth access token …")
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "agentcore/gateway",
    }).encode()
    req = urllib.request.Request(
        token_endpoint,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:  # nosec B310  # nosemgrep: dynamic-urllib-use-detected — URL from trusted config
        token_data = json.loads(resp.read())

    access_token = token_data["access_token"]
    print(f"  Access token obtained ({len(access_token)} chars)")

    ws_url = (
        f"wss://bedrock-agentcore.{region}.amazonaws.com"
        f"/runtimes/{runtime_arn}/ws"
    )
    return {
        "access_token": access_token,
        "runtime_ws_url": ws_url,
        "client_id": client_id,
        "token_endpoint": token_endpoint,
        "runtime_arn": runtime_arn,
    }


# Ordered stage list matching the server graph node IDs.
_ALL_STAGES = [
    "workspace_detection",
    "reverse_engineering",
    "requirements_analysis",
    "user_stories",
    "workflow_planning",
    "application_design",
    "units_generation",
    "functional_design",
    "nfr_requirements",
    "nfr_design",
    "infrastructure_design",
    "code_generation",
    "build_and_test",
]


def _pretty_stage_name(node_id):
    """Convert 'requirements_analysis' to 'Requirements Analysis'."""
    return node_id.replace("_", " ").title()



async def _run_agentcore_ws(
    ws_url, access_token, project_id, query, region, runtime_arn,
):
    """Connect to AgentCore Runtime WebSocket and stream the response."""
    try:
        import websockets
    except ImportError:
        print("ERROR: 'websockets' package required.")
        sys.exit(1)

    C = {
        "cyan": "\033[0;36m", "green": "\033[0;32m",
        "yellow": "\033[1;33m", "red": "\033[0;31m",
        "bold": "\033[1m", "dim": "\033[2m", "nc": "\033[0m",
    }

    stage_status = {}
    seen_stages = set()
    last_was_text = False

    def render_banner(current_stage):  # nosemgrep: useless-inner-function — called in message loop below
        lines = ["", "---"]
        lines.append(f"{C['bold']}📍 Workflow Progress{C['nc']}")
        lines.append("")
        for stg in _ALL_STAGES:
            if stg not in seen_stages and stg != current_stage:
                continue
            name = _pretty_stage_name(stg)
            st = stage_status.get(stg, "")
            if st == "completed":
                lines.append(f"  ✅ {name}")
            elif st == "skipped":
                lines.append(f"  ⏭️  {name}")
            elif stg == current_stage:
                lines.append(
                    f"  {C['cyan']}🔄 {name}"
                    f" ← You are here{C['nc']}"
                )
            else:
                lines.append(f"  ▶️  {name} — REQUIRED")
        lines.append("---")
        lines.append("")
        print("\n".join(lines))

    def end_text():  # nosemgrep: useless-inner-function — called in message loop below
        nonlocal last_was_text
        if last_was_text:
            print()
            last_was_text = False

    # --- Authenticate ---
    try:
        from bedrock_agentcore.runtime import AgentCoreRuntimeClient
        ac = AgentCoreRuntimeClient(region=region)
        resolved_url, headers = ac.generate_ws_connection_oauth(
            runtime_arn=runtime_arn,
            bearer_token=access_token,
        )
        ws_url = resolved_url
        print("  Using AgentCore SDK for WebSocket auth")
    except ImportError:
        print("  bedrock-agentcore SDK not available; "
              "using raw Authorization header")
        headers = {"Authorization": f"Bearer {access_token}"}
    except Exception as e:
        print(f"  SDK auth failed ({e}); "
              "falling back to raw Authorization header")
        headers = {"Authorization": f"Bearer {access_token}"}


    # --- Connect and stream ---
    try:
        async with websockets.connect(
            ws_url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=300,
            close_timeout=10,
        ) as ws:
            init_msg = json.dumps({
                "project_id": project_id,
                "request": query,
            })
            await ws.send(init_msg)
            print(f"  {C['dim']}Init message sent, "
                  f"waiting for response…{C['nc']}")
            print()

            received_any = False
            async for raw in ws:
                received_any = True
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    print(raw, end="", flush=True)
                    continue

                t = msg.get("type", "")

                if t == "text_chunk":
                    content = msg.get("content", "")
                    print(content, end="", flush=True)
                    last_was_text = True

                elif t == "stage_start":
                    end_text()
                    node_id = msg.get("node_id", "?")
                    seen_stages.add(node_id)
                    stage_status[node_id] = "in_progress"
                    render_banner(node_id)

                elif t == "stage_complete":
                    end_text()
                    node_id = msg.get("node_id", "?")
                    seen_stages.add(node_id)
                    stage_status[node_id] = "completed"
                    name = _pretty_stage_name(node_id)
                    print(f"{C['green']}✅ {name}"
                          f" — Complete{C['nc']}")

                elif t == "tool_use":
                    end_text()
                    tool_name = msg.get("tool_name", "?")
                    print(f"{C['dim']}  Tool : "
                          f"{tool_name}{C['nc']}")

                elif t == "approval_required":
                    end_text()
                    options = msg.get("options", [])
                    stage_label = msg.get("stage", "")
                    summary = msg.get("summary", "")
                    print(f"\n{C['yellow']}{C['bold']}"
                          f"⏸  Approval: "
                          f"{stage_label}{C['nc']}")
                    print(f"   {summary}")
                    for i, opt in enumerate(options, 1):
                        print(f"   [{i}] {opt}")
                    loop = asyncio.get_event_loop()
                    choice = (await loop.run_in_executor(
                        None, input,
                        f"{C['yellow']}   Choice: {C['nc']}",
                    )).strip()
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(options):
                            choice = options[idx]
                    except ValueError:
                        pass
                    await ws.send(choice)


                elif t == "human_input":
                    end_text()
                    prompt = msg.get("prompt", "")
                    print(f"\n{C['yellow']}{C['bold']}"
                          f"🤝 Agent:{C['nc']} {prompt}")
                    loop = asyncio.get_event_loop()
                    response = (await loop.run_in_executor(
                        None, input,
                        f"{C['yellow']}   Your response: "
                        f"{C['nc']}",
                    )).strip()
                    await ws.send(response)

                elif t == "human_input_notify":
                    end_text()
                    print(f"\n{C['dim']}📋 "
                          f"{msg.get('message', '')}{C['nc']}")

                elif t == "workflow_complete":
                    end_text()
                    print(f"\n{C['green']}{C['bold']}"
                          f"✔ Workflow complete{C['nc']}\n")
                    break

                elif t == "error":
                    end_text()
                    print(f"\n{C['red']}✖ Error: "
                          f"{msg.get('message', raw)}"
                          f"{C['nc']}\n",
                          file=sys.stderr)
                    break

                else:
                    end_text()
                    print(f"{C['dim']}  [{t}] {raw}{C['nc']}")

            if not received_any:
                close_code = ws.close_code
                close_reason = ws.close_reason or "no reason"
                print(
                    f"\n{C['yellow']}⚠ Connection closed "
                    f"without data.{C['nc']}",
                    file=sys.stderr,
                )
                print(f"  Close code: {close_code}, "
                      f"reason: {close_reason}",
                      file=sys.stderr)
                print("  Check CloudWatch logs for the "
                      "Runtime.", file=sys.stderr)

    except Exception as e:
        err_name = type(e).__name__
        print(f"\n{C['red']}✖ WebSocket error "
              f"({err_name}): {e}{C['nc']}",
              file=sys.stderr)
        if hasattr(e, 'rcvd') and e.rcvd:
            print(f"  Close code: {e.rcvd.code}, "
                  f"reason: {e.rcvd.reason}",
                  file=sys.stderr)
        sys.exit(1)



def main():
    parser = argparse.ArgumentParser(
        description="Agentic SDLC Transformation — Interactive CLI",
    )
    parser.add_argument(
        "request", nargs="*",
        help="Describe what you want to build",
    )
    parser.add_argument(
        "--non-interactive", action="store_true",
        help="Run without human-in-the-loop",
    )
    parser.add_argument(
        "--agentcore", action="store_true",
        help="Connect to deployed AgentCore Runtime via WebSocket",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-west-2"),
        help="AWS region (default: us-west-2)",
    )
    parser.add_argument(
        "--ssm-prefix",
        default=os.environ.get("SSM_PREFIX", "/agentcore/"),
        help="SSM parameter prefix",
    )
    parser.add_argument("--ws-url", default=None)
    parser.add_argument("--projectname", default=None)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--project-path", default=None)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()

    # AgentCore WebSocket mode
    if args.agentcore:
        print("=" * 60)
        print("  Agentic SDLC Transformation")
        print("  AgentCore WebSocket Mode")
        print("  (Inspired by the AWS AI-DLC methodology)")
        print("=" * 60)
        print()

        project_id = args.projectname
        if not project_id:
            project_id = input(
                "Project name (S3 root folder): "
            ).strip()
            if not project_id:
                print("No project name provided. Exiting.")
                sys.exit(1)

        request = " ".join(args.request) if args.request else ""
        if not request:
            request = input("What do you want to build? ").strip()
            if not request:
                print("No request provided. Exiting.")
                sys.exit(1)

        secret_id = os.environ.get(
            "SECRET_ID",
            f"{args.ssm_prefix}secrets/cognito-client-secret",
        )
        creds = _fetch_cognito_token(
            args.region, args.ssm_prefix, secret_id,
        )
        ws_url = args.ws_url or creds["runtime_ws_url"]
        print(f"  Project ID:     {project_id}")
        print(f"  WebSocket URL:  {ws_url}\n")

        asyncio.run(_run_agentcore_ws(
            ws_url, creds["access_token"], project_id,
            request,
            region=args.region,
            runtime_arn=creds["runtime_arn"],
        ))
        return

    # Local mode
    print("=" * 60)
    print("  Agentic SDLC Transformation")
    print("  Interactive Mode — Human in the Loop")
    print("  (Inspired by the AWS AI-DLC methodology)")
    print("=" * 60)
    print()

    project_name = args.project_name
    if not project_name:
        project_name = input("Project name: ").strip()
        if not project_name:
            print("No project name provided. Exiting.")
            sys.exit(1)

    project_path = args.project_path
    if not project_path:
        project_path = input(
            "Project workspace path (absolute): "
        ).strip()
        if not project_path:
            print("No project path provided. Exiting.")
            sys.exit(1)
    project_path = os.path.abspath(project_path)

    request = " ".join(args.request) if args.request else ""
    if not request:
        request = input("What do you want to build? ").strip()
        if not request:
            print("No request provided. Exiting.")
            sys.exit(1)
    print()

    if args.fresh:
        state_path = os.path.join(
            project_path, "aidlc-docs", "aidlc-state.md",
        )
        if os.path.isfile(state_path):
            os.remove(state_path)
            print("🗑️  Removed existing state file.\n")

    if args.non_interactive:
        os.environ["BYPASS_TOOL_CONSENT"] = "true"
        print("Running in non-interactive mode...\n")
        main_sync(
            request,
            project_name=project_name,
            workspace_root=project_path,
        )
    else:
        print("Running in interactive mode...\n")
        print("The agent will pause and ask for your input.")
        print("Type your responses when prompted.\n")
        main_interactive(
            request,
            project_name=project_name,
            workspace_root=project_path,
        )


if __name__ == "__main__":
    main()
