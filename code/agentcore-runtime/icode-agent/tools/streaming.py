"""Streaming event processing for the AI-SDLC Multi-Agent System.

Provides functions to process streaming events from the Strands graph
and render them to the user interface (CLI). Supports text data events,
multi-agent node lifecycle events, tool use events, and handoff events.
"""


def process_stream_event(event: dict) -> None:
    """Process a streaming event from the graph and render to user interface.

    Handles the following event types:
    - ``data``: Text chunk — printed progressively.
    - ``multiagent_node_start``: Stage started indicator.
    - ``multiagent_node_stop``: Stage completed indicator.
    - ``current_tool_use``: Tool invocation indicator.
    - ``multiagent_handoff``: Agent handoff indicator.

    Unknown event types are silently ignored.

    Args:
        event: A dictionary representing a single streaming event.
    """
    # Text chunk — print progressively
    if "data" in event:
        print(event["data"], end="", flush=True)
    # Multi-agent node lifecycle
    elif event.get("type") == "multiagent_node_start":
        print(f"\n--- Stage: {event['node_id']} started ---")
    elif event.get("type") == "multiagent_node_stop":
        print(f"\n--- Stage: {event['node_id']} completed ---")
    # Tool usage indicator
    elif "current_tool_use" in event and event["current_tool_use"].get("name"):
        print(f"\n  [Tool: {event['current_tool_use']['name']}]", end="")
    # Agent handoff indicator
    elif event.get("type") == "multiagent_handoff":
        source = event.get("source", "unknown")
        target = event.get("target", "unknown")
        print(f"\n>>> Handoff: {source} -> {target}")


def create_cli_callback_handler():
    """Create a callback handler for synchronous CLI streaming.

    Returns a callable that accepts keyword arguments and delegates
    to :func:`process_stream_event`. This is suitable for use as a
    ``callback_handler`` on a Strands graph in synchronous CLI
    environments.

    Returns:
        A callback function compatible with the Strands callback_handler
        interface.
    """

    def handle_event(**kwargs):
        process_stream_event(kwargs)

    return handle_event
