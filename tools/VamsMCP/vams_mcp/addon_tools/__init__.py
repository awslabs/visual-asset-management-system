"""Upstream-owned add-on lane. Upstream VAMS edits this package when it ships an add-on that adds MCP
tools; a fork does not.

MCP tools the optional add-ons add. ``server.py`` calls ``register_addon_tools`` once, at module level
after its own read, write and destructive tools and before ``register_custom_tools``. Each add-on
keeps its tools in one module of this package, listed in ``ADDON_TOOL_MODULES``, whose
``register(mcp, client, config, tool_result)`` defines each tool with ``@mcp.tool()`` then
``@tool_result`` (in that order, as ``server.py`` does), puts write tools under
``if config.enable_writes:`` and destructive tools under ``if config.enable_destructive:``, and reaches
the API through ``client.api.<method>`` (the add-on's ``APIClient`` mixin in
``vamscli/utils/addon/``). An add-on tool is registered whether or not the deployment enables the
add-on, so its first statement is ``client.require_feature(<switch>, <message>)``. A tool whose name is
already registered is not: the MCP SDK keeps the first registration and logs
``Tool already exists: <name>``, so a core tool wins over an add-on tool and an add-on tool over a
fork's. ``tests/test_addon_tools.py`` holds every module here to that layout.
"""

from typing import Any, Callable

from . import edh

ADDON_TOOL_MODULES = (edh,)


def register_addon_tools(
    mcp: Any,
    client: Any,
    config: Any,
    tool_result: Callable[[Callable[..., Any]], Callable[..., Any]],
) -> None:
    """Register every add-on's MCP tools on ``mcp``, in ``ADDON_TOOL_MODULES`` order."""
    for module in ADDON_TOOL_MODULES:
        module.register(mcp, client, config, tool_result)
