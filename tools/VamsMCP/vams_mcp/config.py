"""Configuration for the VAMS MCP server.

This server stores **no credentials**. Over stdio (the default) it reuses the vamscli profile the user
has already configured on their own machine:

    vamscli setup <api-gateway-url>     # stores the API Gateway URL
    vamscli auth login -u <user>        # stores tokens (with refresh)

The MCP server reads that profile to discover the API URL and to authenticate.
This makes it safe to distribute publicly: every user runs it against their own
VAMS account using their own vamscli login. No API keys live in mcp.json or in
this server.

As a remote server (``VAMS_MCP_TRANSPORT=streamable-http``) it reads no profile at all. Each HTTP
request carries its user's OAuth access token, which the server verifies itself and then sends on to
the VAMS API for that request only (``remote_auth.py``, ``http_app.py``). The issuer, keys and
accepted audiences come from the JSON file ``vams-mcp setup-remote`` writes, named by
``VAMS_MCP_AUTH_CONFIG``.

Environment variables (all optional):
    VAMS_PROFILE            vamscli profile name to use (stdio only). Defaults to the active
                            vamscli profile. Refused in remote mode.
    VAMS_ENABLE_WRITES      "true"/"1" to expose create/update tools. Default off.
    VAMS_ENABLE_DESTRUCTIVE "true"/"1" to expose archive/delete tools. Default
                            off. Requires writes enabled.
    VAMS_MAX_PAGES          Max pages auto-followed for list endpoints. Default 20.
    VAMS_PAGE_SIZE          Page size per paginated call. Default 100.
    VAMS_MCP_TRANSPORT      "stdio" (default) or "streamable-http".
    VAMS_MCP_TOOLSETS       Comma-separated tool sets (toolsets.py). Default "all" over stdio,
                            "consumer" (read-only) in remote mode.
    VAMS_MCP_HOST           Remote mode: the address to listen on. Default 127.0.0.1.
    VAMS_MCP_PORT           Remote mode: the port to listen on. Default 8000.
    VAMS_MCP_AUTH_CONFIG    Remote mode: path of the auth configuration file (required).
    VAMS_MCP_TOOL_TIMEOUT   Remote mode: seconds one tool call may take. Default 240.
    VAMS_MCP_MAX_CALLS_PER_USER
                            Remote mode: tool calls one user may have in progress. Default 8.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Optional, Tuple

from . import toolsets


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid.

    ``str()`` is the full message for a person, setup command included. A tool result must not tell
    the agent to run a CLI (Mandatory Rule 14), so the server reports ``reason`` (the fact, with no
    command) to the agent and puts ``user_step`` (the command the USER runs) in ``user_action``.
    """

    def __init__(self, message: str, reason: Optional[str] = None, user_step: Optional[str] = None) -> None:
        super().__init__(message)
        self.reason = reason or message
        self.user_step = user_step


# Ceiling for VAMS_PAGE_SIZE. The metadata GETs refuse a pageSize above 1000 with a 400 rather than
# reducing it, and this one value is sent as pageSize on every paged read, so a larger clamp lets an
# operator's environment variable turn an ordinary metadata read into an error. Every other paged
# route accepts 1000, and the routes with a tighter limit of their own apply it at the call site.
MAX_PAGE_SIZE = 1000

TRANSPORT_STDIO = "stdio"
TRANSPORT_HTTP = "streamable-http"
TRANSPORTS = (TRANSPORT_STDIO, TRANSPORT_HTTP)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# Remote time budget of one tool call. Amazon Quick gives an operation 5 minutes; 240 seconds leaves
# room for the HTTP front end and the client's own overhead.
DEFAULT_TOOL_TIMEOUT = 240
MIN_TOOL_TIMEOUT = 10
MAX_TOOL_TIMEOUT = 1800

# Remote mode: the tool calls one user may have in progress at once, so one user cannot occupy every
# worker thread and delay everyone else's calls.
DEFAULT_MAX_CALLS_PER_USER = 8
MAX_MAX_CALLS_PER_USER = 64


def is_loopback_host(host: str) -> bool:
    """Whether ``host`` names only this machine (``localhost`` or a loopback address)."""
    value = (host or "").strip().strip("[]").lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Config:
    profile: Optional[str] = None
    enable_writes: bool = False
    enable_destructive: bool = False
    max_pages: int = 20
    page_size: int = 100
    transport: str = TRANSPORT_STDIO
    toolsets: Tuple[str, ...] = toolsets.DEFAULT_STDIO
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    auth_config_path: Optional[str] = None
    tool_timeout: int = DEFAULT_TOOL_TIMEOUT
    max_calls_per_user: int = DEFAULT_MAX_CALLS_PER_USER

    @property
    def remote(self) -> bool:
        """True when the server serves remote clients over streamable HTTP."""
        return self.transport == TRANSPORT_HTTP

    @classmethod
    def from_env(cls) -> "Config":
        profile = (os.environ.get("VAMS_PROFILE") or "").strip() or None

        enable_writes = _as_bool(os.environ.get("VAMS_ENABLE_WRITES"))
        enable_destructive = _as_bool(os.environ.get("VAMS_ENABLE_DESTRUCTIVE"))

        try:
            max_pages = int(os.environ.get("VAMS_MAX_PAGES", "20"))
            page_size = int(os.environ.get("VAMS_PAGE_SIZE", "100"))
        except ValueError as exc:
            raise ConfigError(f"VAMS_MAX_PAGES / VAMS_PAGE_SIZE must be integers: {exc}")

        transport = (os.environ.get("VAMS_MCP_TRANSPORT") or TRANSPORT_STDIO).strip().lower()
        if transport not in TRANSPORTS:
            raise ConfigError(f"VAMS_MCP_TRANSPORT must be one of {', '.join(TRANSPORTS)}, not '{transport}'")
        remote = transport == TRANSPORT_HTTP

        try:
            selected = toolsets.parse(
                os.environ.get("VAMS_MCP_TOOLSETS"),
                toolsets.DEFAULT_REMOTE if remote else toolsets.DEFAULT_STDIO,
            )
        except ValueError as exc:
            raise ConfigError(f"VAMS_MCP_TOOLSETS: {exc}")

        host = (os.environ.get("VAMS_MCP_HOST") or DEFAULT_HOST).strip()
        try:
            port = int(os.environ.get("VAMS_MCP_PORT") or DEFAULT_PORT)
            tool_timeout = int(os.environ.get("VAMS_MCP_TOOL_TIMEOUT") or DEFAULT_TOOL_TIMEOUT)
            max_calls_per_user = int(os.environ.get("VAMS_MCP_MAX_CALLS_PER_USER") or DEFAULT_MAX_CALLS_PER_USER)
        except ValueError as exc:
            raise ConfigError(
                f"VAMS_MCP_PORT / VAMS_MCP_TOOL_TIMEOUT / VAMS_MCP_MAX_CALLS_PER_USER must be integers: {exc}"
            )
        if not 1 <= max_calls_per_user <= MAX_MAX_CALLS_PER_USER:
            raise ConfigError(
                f"VAMS_MCP_MAX_CALLS_PER_USER must be between 1 and {MAX_MAX_CALLS_PER_USER}, not {max_calls_per_user}"
            )
        if not 1 <= port <= 65535:
            raise ConfigError(f"VAMS_MCP_PORT must be between 1 and 65535, not {port}")
        if not MIN_TOOL_TIMEOUT <= tool_timeout <= MAX_TOOL_TIMEOUT:
            raise ConfigError(
                f"VAMS_MCP_TOOL_TIMEOUT must be between {MIN_TOOL_TIMEOUT} and {MAX_TOOL_TIMEOUT} seconds, "
                f"not {tool_timeout}"
            )
        auth_config_path = (os.environ.get("VAMS_MCP_AUTH_CONFIG") or "").strip() or None

        if remote:
            # A remote server acts for each request's own user. A profile would make every user act
            # as one CLI identity, so a configuration that names one is refused, not ignored.
            if profile:
                raise ConfigError(
                    "VAMS_PROFILE cannot be used with VAMS_MCP_TRANSPORT=streamable-http: a remote server "
                    "acts for each request's own user and never reads a vamscli profile. Unset VAMS_PROFILE."
                )
            if not auth_config_path:
                raise ConfigError(
                    "VAMS_MCP_TRANSPORT=streamable-http needs VAMS_MCP_AUTH_CONFIG, the auth configuration "
                    "file that `vams-mcp setup-remote` writes."
                )

        return cls(
            profile=profile,
            enable_writes=enable_writes,
            # Destructive tools require writes to also be enabled.
            enable_destructive=enable_destructive and enable_writes,
            max_pages=max(1, max_pages),
            page_size=max(1, min(page_size, MAX_PAGE_SIZE)),
            transport=transport,
            toolsets=selected,
            host=host,
            port=port,
            auth_config_path=auth_config_path,
            tool_timeout=tool_timeout,
            max_calls_per_user=max_calls_per_user,
        )
