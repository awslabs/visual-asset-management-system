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

from vamscli.constants import (  # noqa: E402,F401
    API_ASSETS,
    API_DATABASE_ASSETS,
    SUBSCRIPTION_ENTITY_ASSET,
    SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
)
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
        starting_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Follow VAMS ``NextToken`` pagination up to configured limits.

        ``fetch_page`` is a callable taking a ``params`` dict and returning a
        response dict shaped like ``{"Items": [...], "NextToken": "..."}``.
        ``items_key`` names the list field, which differs per endpoint
        (``Items``, ``items``, ``versions``). Results are always returned under
        ``Items`` so every list tool has one shape.

        ``starting_token`` resumes a walk a previous call stopped: it is sent as the FIRST page's
        ``startingToken``. When the walk stops with a token still in hand, that token is returned as
        ``NextToken`` — without it the ceiling below is a wall rather than a page size, and rows past
        it are unreachable through this server no matter what the caller asks for.

        Two independent bounds can stop the walk, and ``note`` names which fired:

        * ``max_items`` (or ``max_pages * page_size`` when the caller gave none) caps the rows
          returned. It can also cut a page that carried no token at all, which is still an
          incomplete answer and is still flagged ``truncated``.
        * ``max_pages`` caps the requests one tool call may issue. It is a work bound on the server
          process, so a larger ``max_items`` does not raise it; resume with ``NextToken`` instead.
        """
        limit = max_items if max_items is not None else (self.config.max_pages * self.config.page_size)
        effective_page_size = page_size or self.config.page_size
        items: List[Any] = []
        next_token: Optional[str] = starting_token
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

        kept = items[:limit]
        result: Dict[str, Any] = {"Items": kept, "count": len(kept), "pages": pages}
        if next_token:
            result["NextToken"] = next_token
        # `len(items) > limit` is the case a token-only check misses: a single page can return more
        # rows than max_items and carry no NextToken, so the surplus is dropped while the result
        # reports a clean count — the exact reading the `truncated` contract exists to prevent.
        if next_token or len(items) > limit:
            result["truncated"] = True
            if next_token and pages >= self.config.max_pages and len(kept) < limit:
                reason = (
                    f"stopped after {pages} page(s), the max_pages work bound on this server "
                    f"(VAMS_MAX_PAGES={self.config.max_pages})"
                )
            elif next_token:
                reason = f"stopped at the requested limit of {limit} item(s)"
            else:
                reason = (
                    f"the last page returned more rows than the requested limit of {limit}; "
                    f"{len(items) - limit} were dropped"
                )
            result["note"] = (
                f"Result is INCOMPLETE: {reason}. Returned {len(kept)} item(s) over {pages} page(s)."
                + (
                    " More items are available — call this tool again with "
                    f"starting_token={next_token!r} to continue."
                    if next_token
                    else " Raise max_items to see the rest."
                )
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
