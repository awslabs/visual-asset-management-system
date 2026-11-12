"""Thin wrapper around the vamscli APIClient for the MCP server.

Reuses vamscli's ``APIClient`` (retries, 429 backoff, typed errors) and, more
importantly, its ``ProfileManager`` — so the server authenticates with the
credentials the user already established via ``vamscli auth login``. No keys or
URLs are stored by this server; everything is read from the vamscli profile.

Adds MCP-friendly helpers: auto-pagination for list endpoints, a raw request
escape hatch for endpoints without a dedicated client method (e.g. listing
assets in a database), search-result trimming, and the profile's feature-switch
snapshot for tools that are feature-gated.
"""

from __future__ import annotations

import sys
import time
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
    FEATURE_NOOPENSEARCH,
    SUBSCRIPTION_ENTITY_ASSET,
    SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
)
from vamscli.utils.api_client import APIClient  # noqa: E402
from vamscli.utils.exceptions import SearchDisabledError  # noqa: E402,F401
from vamscli.utils.exceptions import (  # noqa: E402,F401
    AuthenticationError,
    OverrideTokenError,
    TokenExpiredError,
)
from vamscli.version import CLI_VERSION  # noqa: E402,F401
from vamscli.utils.features import is_feature_enabled  # noqa: E402
from vamscli.utils.profile import ProfileManager  # noqa: E402

from .config import Config, ConfigError  # noqa: E402


class FeatureDisabledError(Exception):
    """The deployment has not enabled the feature a tool requires."""


def login_step_for(config: Config) -> str:
    """The sign-in command the USER runs for this server's profile (``--profile`` before the command)."""
    prefix = f"vamscli --profile {config.profile} " if config.profile else "vamscli "
    return f"`{prefix}auth login -u <user>`"


def describe_profile(config: Config) -> Dict[str, Any]:
    """What the vamscli profile this server uses holds, read from disk and without any request.

    Used by the ``vams_status`` tool, which must answer even when no client could be built. Returns
    the profile name, its API URL, the signed-in user id, whether the stored access token's expiry
    has passed (the client refreshes it on the next call when the refresh token still works), and the
    feature switches the last ``vamscli auth login`` saw. Never returns a token.
    """
    try:
        name = config.profile or ProfileManager().get_active_profile()
    except Exception:  # noqa: BLE001 - a status read must not fail
        name = config.profile or "default"
    manager = ProfileManager(name)
    status: Dict[str, Any] = {"profile": name, "apiUrl": None, "userId": None}
    try:
        status["apiUrl"] = manager.load_config().get("api_gateway_url")
    except Exception:  # noqa: BLE001 - not set up
        pass
    auth = manager.load_auth_profile() or {}
    if auth:
        status["userId"] = auth.get("user_id")
        expires_at = auth.get("expires_at")
        if isinstance(expires_at, (int, float)):
            status["tokenExpired"] = time.time() >= expires_at
    try:
        status["featureSwitches"] = list(manager.get_feature_switches())
    except Exception:  # noqa: BLE001
        status["featureSwitches"] = []
    return status


def sign_in_marker(config: Config) -> Any:
    """The stored sign-in's expiry, which every new sign-in or renewal rewrites; None without one.

    The server compares it with the value seen when VAMS refused a call, so a sign-in the user made
    since then is not reported as refused.
    """
    try:
        name = config.profile or ProfileManager().get_active_profile()
        return (ProfileManager(name).load_auth_profile() or {}).get("expires_at")
    except Exception:  # noqa: BLE001 - a status read must not fail
        return None


class VamsClient:
    """MCP-oriented facade over the vamscli APIClient, using the vamscli profile."""

    def __init__(self, config: Config) -> None:
        self.config = config

        # Resolve which vamscli profile to use.
        profile_name = config.profile or ProfileManager().get_active_profile()
        self.profile_name = profile_name
        self._profile = ProfileManager(profile_name)

        # `--profile` is a vamscli root option and goes before the command.
        cli_prefix = f"vamscli --profile {profile_name} " if config.profile else "vamscli "

        # Discover the API Gateway URL from the profile (raises a clear,
        # actionable error if the user hasn't run `vamscli setup`).
        setup_step = f"`{cli_prefix}setup <api-gateway-url>`"
        login_step = f"`{cli_prefix}auth login -u <user>`"
        try:
            profile_config = self._profile.load_config()
        except Exception as exc:  # ProfileNotFoundError / ConfigurationError
            raise ConfigError(
                f"vamscli profile '{profile_name}' is not set up: {exc} "
                f"Run {setup_step} first.",
                reason=f"the VAMS profile '{profile_name}' is not set up on this machine",
                user_step=f"{setup_step} and then {login_step}",
            )

        api_url = profile_config.get("api_gateway_url")
        if not api_url:
            raise ConfigError(
                f"vamscli profile '{profile_name}' has no api_gateway_url. "
                f"Re-run {setup_step}.",
                reason=f"the VAMS profile '{profile_name}' has no VAMS API URL",
                user_step=f"{setup_step} and then {login_step}",
            )

        # Require an existing login. The APIClient will refresh tokens as needed;
        # here we just fail fast with guidance if the user never logged in.
        if self._profile.load_auth_profile() is None:
            raise ConfigError(
                f"Not authenticated for vamscli profile '{profile_name}'. "
                f"Run {login_step} first.",
                reason=f"nobody is signed in on the VAMS profile '{profile_name}'",
                user_step=login_step,
            )
        self.login_step = login_step

        self.api_url = api_url
        self.api = APIClient(api_url, self._profile)

    # --- Feature switches -------------------------------------------------

    def has_feature(self, feature_name: str) -> bool:
        """Whether the feature-switch snapshot the profile stored at its last ``vamscli auth login``
        (or ``set-override``) carries ``feature_name``."""
        return self._profile.has_feature_switch(feature_name)

    def require_feature(self, feature_name: str, message: str) -> None:
        """Raise ``FeatureDisabledError(message)`` unless the snapshot carries ``feature_name``.

        The MCP counterpart of vamscli's ``requires_feature`` gate, and the first statement of a
        feature-gated tool: a deployment without the feature is answered with ``message`` (the CLI's
        own sentence) before any request. Without it the request meets API Gateway's 403 for a route
        that was never created, which the client reports only as a request no deployed route matches.
        """
        if not self.has_feature(feature_name):
            raise FeatureDisabledError(message)

    def search_enabled(self) -> bool:
        """False on a deployment without OpenSearch (the profile's NOOPENSEARCH feature switch)."""
        return not is_feature_enabled(FEATURE_NOOPENSEARCH, self._profile)

    def feature_switches(self) -> Dict[str, Any]:
        """The profile's feature-switch snapshot as ``vamscli features list`` reports it: ``enabled``
        (sorted), ``total`` and ``fetched_at``, plus ``has_feature_switches``. Read from disk only."""
        info = self._profile.get_feature_switches_info()
        return {
            "has_feature_switches": bool(info.get("has_feature_switches")),
            "enabled": sorted(info.get("enabled") or []),
            "total": info.get("count", 0),
            "fetched_at": info.get("fetched_at"),
        }

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
        ``items_key`` names the list field, which differs per endpoint. It is a parameter, so
        any field name works -- read the handler's response model rather than matching against
        a list here, because an enumeration of the names in use has already gone stale once
        (it omitted ``metadata``). Values passed today: ``Items`` (the default), ``items``,
        ``versions`` and ``metadata``. Results are always returned under ``Items`` so every
        list tool has one shape.

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
            # The last page asks only for the rows still wanted, so the page's NextToken resumes
            # right after the last row kept. A full page cut to max_items would hand back a token
            # pointing past rows the caller never saw.
            params: Dict[str, Any] = {"pageSize": max(1, min(effective_page_size, limit - len(items)))}
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
            # With no max_items the only limit is the one derived from max_pages, so a walk the page
            # bound stopped after full pages must not be reported as a limit the caller requested.
            if next_token and pages >= self.config.max_pages and (max_items is None or len(kept) < limit):
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
        """Reduce a raw OpenSearch response to a compact, agent-friendly shape.

        ``total`` stays the numeric ``hits.total.value``. ``total_is_lower_bound`` is True when the
        API reports ``hits.total.relation == "gte"`` (the true total is at least ``total``), and
        ``total_display`` is the total as text with a trailing '+' in that case (e.g. "120+").
        ``POST /search/nlp`` reports every total that is not zero that way (approximate matching);
        keyword search does when some permission rules can only be counted as a minimum.
        """
        hits_container = raw.get("hits", {}) if isinstance(raw, dict) else {}
        total = hits_container.get("total", {})
        total_value = total.get("value") if isinstance(total, dict) else total
        total_is_lower_bound = isinstance(total, dict) and total.get("relation") == "gte"
        if total_value is None:
            total_display = None
        else:
            total_display = f"{total_value}+" if total_is_lower_bound else f"{total_value}"
        hits = hits_container.get("hits", []) or []

        compact = []
        for hit in hits[:max_hits]:
            source = hit.get("_source", {}) if isinstance(hit, dict) else {}
            # Vector-search hits carry their ranking extras beside _source, not inside it; folding
            # them under one key keeps every consumer's `source` reading valid.
            if isinstance(hit, dict) and "_vector" in hit:
                source = {**source, "_vector": hit["_vector"]}
            compact.append(
                {
                    "id": hit.get("_id"),
                    "score": hit.get("_score"),
                    "source": source,
                }
            )

        return {
            "total": total_value,
            "total_is_lower_bound": total_is_lower_bound,
            "total_display": total_display,
            "returned": len(compact),
            "results": compact,
        }
