"""Upstream-owned add-on lane. Upstream VAMS edits this package when it ships an add-on that adds MCP
tools; a fork does not.

MCP tools the optional add-ons add. ``server.py`` calls ``register_addon_tools`` once, at module level
after its own read, write and destructive tools and before ``register_custom_tools``. Each add-on
keeps its tools in one module of this package, listed in ``ADDON_TOOL_MODULES``, which names the
add-on's feature switch in ``FEATURE`` and whose ``register(mcp, client, config, tool_result)`` defines
each tool with ``@mcp.tool()`` then ``@tool_result`` (in that order, as ``server.py`` does), puts write
tools under ``if config.enable_writes:`` and destructive tools under ``if config.enable_destructive:``,
and reaches the API through ``client.api.<method>`` (the add-on's ``APIClient`` mixin in
``vamscli/utils/addon/``).

An add-on's tools are registered only when the feature-switch snapshot the vamscli profile holds at
server start carries the module's ``FEATURE`` (``client.has_feature``), so on a deployment without the
add-on no MCP client lists them. The snapshot is the one ``vamscli auth login`` stored, so an add-on
enabled after that login appears once the profile signs in again and the MCP server restarts. A
registered tool still opens with ``client.require_feature(<switch>, <message>)``: an optional feature
of the add-on (container jobs, AMI recipes) is answered "not enabled" before any request.

A tool whose name is already registered is not: the MCP SDK keeps the first registration and logs
``Tool already exists: <name>``, so a core tool wins over an add-on tool and an add-on tool over a
fork's. ``tests/test_addon_tools.py`` holds every module here to that layout.
"""

from typing import Any, Callable

from . import edh

ADDON_TOOL_MODULES = (edh,)


def addon_deployed(client: Any, module: Any) -> bool:
    """Whether the profile's feature-switch snapshot carries the add-on's switch.

    False without a client (no vamscli profile is set up or signed in), so nothing of an add-on is
    registered on a server that cannot tell which deployment it serves.
    """
    return client is not None and bool(client.has_feature(module.FEATURE))


def register_addon_tools(
    mcp: Any,
    client: Any,
    config: Any,
    tool_result: Callable[[Callable[..., Any]], Callable[..., Any]],
) -> None:
    """Register the MCP tools of every add-on the deployment enabled, in ``ADDON_TOOL_MODULES`` order."""
    for module in ADDON_TOOL_MODULES:
        if addon_deployed(client, module):
            module.register(mcp, client, config, tool_result)
