"""Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.

MCP tools a fork adds. ``server.py`` calls ``register_custom_tools`` once, after its own read, write
and destructive tools are defined. Define each tool inside the function with ``@mcp.tool()`` then
``@tool_result`` (in that order, as ``server.py`` does), put write tools under
``if config.enable_writes:`` and destructive tools under ``if config.enable_destructive:``, and reach
the API through ``client.api.<method>`` (an ``APIClient`` method, including those a fork adds in
``vamscli/utils/custom_api_client.py``) or ``client.get_json`` / ``client.post_json`` for a one-off
call. Tool names prefixed ``custom_`` are recommended so a later core tool does not collide with one
defined here.
"""

from typing import Any, Callable


def register_custom_tools(
    mcp: Any,
    client: Any,
    config: Any,
    tool_result: Callable[[Callable[..., Any]], Callable[..., Any]],
) -> None:
    """Register the fork's MCP tools on ``mcp``. Upstream ships this empty."""
    return None
