"""Configuration for the VAMS MCP server.

This server stores **no credentials**. It reuses the vamscli profile the user
has already configured on their own machine:

    vamscli setup <api-gateway-url>     # stores the API Gateway URL
    vamscli auth login -u <user>        # stores tokens (with refresh)

The MCP server reads that profile to discover the API URL and to authenticate.
This makes it safe to distribute publicly: every user runs it against their own
VAMS account using their own vamscli login. No API keys live in mcp.json or in
this server.

Environment variables (all optional):
    VAMS_PROFILE            vamscli profile name to use. Defaults to the active
                            vamscli profile.
    VAMS_ENABLE_WRITES      "true"/"1" to expose create/update tools. Default off.
    VAMS_ENABLE_DESTRUCTIVE "true"/"1" to expose archive/delete tools. Default
                            off. Requires writes enabled.
    VAMS_MAX_PAGES          Max pages auto-followed for list endpoints. Default 20.
    VAMS_PAGE_SIZE          Page size per paginated call. Default 100.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    profile: Optional[str] = None
    enable_writes: bool = False
    enable_destructive: bool = False
    max_pages: int = 20
    page_size: int = 100

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

        return cls(
            profile=profile,
            enable_writes=enable_writes,
            # Destructive tools require writes to also be enabled.
            enable_destructive=enable_destructive and enable_writes,
            max_pages=max(1, max_pages),
            page_size=max(1, min(page_size, 2000)),
        )
