"""Thin wrapper around the vamscli APIClient for the MCP server.

Reuses vamscli's ``APIClient`` (retries, 429 backoff, typed errors) and, more
importantly, its ``ProfileManager`` — so the server authenticates with the
credentials the user already established via ``vamscli auth login``. No keys or
URLs are stored by this server; everything is read from the vamscli profile.

Adds MCP-friendly helpers: auto-pagination for list endpoints, a raw request
escape hatch for endpoints without a dedicated client method (e.g. listing
assets in a database), and search-result trimming.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _ensure_vamscli_importable() -> None:
    """Make `vamscli` importable even if it wasn't pip-installed.

    Falls back to inserting the sibling ``tools/VamsCLI`` directory onto
    sys.path so the server is self-contained within the repo checkout.
    """
    try:
        import vamscli  # noqa: F401

        return
    except ImportError:
        pass

    # tools/VamsMCP/vams_mcp/client.py -> tools/VamsCLI
    candidate = Path(__file__).resolve().parents[2] / "VamsCLI"
    if (candidate / "vamscli").is_dir():
        sys.path.insert(0, str(candidate))


_ensure_vamscli_importable()

from vamscli.constants import API_ASSETS, API_DATABASE_ASSETS  # noqa: E402,F401
from vamscli.utils.api_client import APIClient  # noqa: E402
from vamscli.utils.profile import ProfileManager  # noqa: E402

from .config import Config, ConfigError  # noqa: E402


class VamsClient:
    """MCP-oriented facade over the vamscli APIClient, using the vamscli profile."""

    def __init__(self, config: Config) -> None:
        self.config = config

        # Resolve which vamscli profile to use.
        profile_name = config.profile or ProfileManager().get_active_profile()
        self.profile_name = profile_name
        self._profile = ProfileManager(profile_name)

        # Discover the API Gateway URL from the profile (raises a clear,
        # actionable error if the user hasn't run `vamscli setup`).
        try:
            profile_config = self._profile.load_config()
        except Exception as exc:  # ProfileNotFoundError / ConfigurationError
            raise ConfigError(
                f"vamscli profile '{profile_name}' is not set up: {exc} "
                f"Run `vamscli setup <api-gateway-url>"
                + (f" --profile {profile_name}`" if config.profile else "`")
                + " first."
            )

        api_url = profile_config.get("api_gateway_url")
        if not api_url:
            raise ConfigError(
                f"vamscli profile '{profile_name}' has no api_gateway_url. "
                f"Re-run `vamscli setup <api-gateway-url>`."
            )

        # Require an existing login. The APIClient will refresh tokens as needed;
        # here we just fail fast with guidance if the user never logged in.
        if self._profile.load_auth_profile() is None:
            raise ConfigError(
                f"Not authenticated for vamscli profile '{profile_name}'. "
                f"Run `vamscli auth login -u <user>"
                + (f" --profile {profile_name}`" if config.profile else "`")
                + " first."
            )

        self.api_url = api_url
        self.api = APIClient(api_url, self._profile)

    # --- Raw request escape hatch ----------------------------------------

    def get_json(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET an arbitrary endpoint and return parsed JSON."""
        resp = self.api.get(endpoint, params=params or {})
        return resp.json()

    def post_json(self, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """POST an arbitrary endpoint and return parsed JSON."""
        resp = self.api.post(endpoint, data=data or {})
        return resp.json()

    # --- Pagination helper ------------------------------------------------

    @staticmethod
    def unwrap_message(page: Any) -> Dict[str, Any]:
        """Return the payload dict, unwrapping the legacy ``message`` envelope.

        Several VAMS list endpoints (tags, tag types, workflows, workflow
        executions) nest the paged payload under ``message`` for backwards
        compatibility, so the items and ``NextToken`` are one level down.
        """
        if not isinstance(page, dict):
            return {}
        message = page.get("message")
        if isinstance(message, dict):
            return message
        return page

    def paginate(
        self,
        fetch_page,
        max_items: Optional[int] = None,
        items_key: str = "Items",
        page_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Follow VAMS ``NextToken`` pagination up to configured limits.

        ``fetch_page`` is a callable taking a ``params`` dict and returning a
        response dict shaped like ``{"Items": [...], "NextToken": "..."}``.
        ``items_key`` names the list field, which differs per endpoint
        (``Items``, ``items``, ``versions``). Results are always returned under
        ``Items`` so every list tool has one shape.
        """
        limit = max_items if max_items is not None else (self.config.max_pages * self.config.page_size)
        effective_page_size = page_size or self.config.page_size
        items: List[Any] = []
        next_token: Optional[str] = None
        pages = 0

        while True:
            pages += 1
            params: Dict[str, Any] = {"pageSize": effective_page_size}
            if next_token:
                params["startingToken"] = next_token

            payload = self.unwrap_message(fetch_page(params))
            items.extend(payload.get(items_key, []) or [])

            next_token = payload.get("NextToken")
            if not next_token or len(items) >= limit or pages >= self.config.max_pages:
                break

        result: Dict[str, Any] = {"Items": items[:limit], "count": len(items[:limit]), "pages": pages}
        if next_token:
            result["truncated"] = True
            result["note"] = (
                f"Result truncated at {len(items[:limit])} items ({pages} page(s), "
                f"limit {limit}, max_pages {self.config.max_pages}). "
                "More items are available."
            )
        return result

    # --- Search trimming --------------------------------------------------

    @staticmethod
    def trim_search_results(raw: Dict[str, Any], max_hits: int = 50) -> Dict[str, Any]:
        """Reduce a raw OpenSearch response to a compact, agent-friendly shape."""
        hits_container = raw.get("hits", {}) if isinstance(raw, dict) else {}
        total = hits_container.get("total", {})
        total_value = total.get("value") if isinstance(total, dict) else total
        hits = hits_container.get("hits", []) or []

        compact = []
        for hit in hits[:max_hits]:
            source = hit.get("_source", {}) if isinstance(hit, dict) else {}
            compact.append(
                {
                    "id": hit.get("_id"),
                    "score": hit.get("_score"),
                    "source": source,
                }
            )

        return {
            "total": total_value,
            "returned": len(compact),
            "results": compact,
        }
