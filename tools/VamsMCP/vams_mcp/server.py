"""VAMS MCP server.

Exposes the Visual Asset Management System REST API as MCP tools. Read/search
tools are always available. Write tools require VAMS_ENABLE_WRITES=true and
destructive tools additionally require VAMS_ENABLE_DESTRUCTIVE=true.
"""

from __future__ import annotations

import functools
import logging
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional


def _force_logging_to_stderr() -> None:
    """Route ALL logging to stderr.

    MCP stdio transport uses stdout as the JSON-RPC channel; any log line on
    stdout corrupts the protocol. vamscli/boto3 may configure rich/stream
    handlers that target stdout, so we forcibly reconfigure the root logger to
    stderr and quiet noisy third-party loggers. ``force=True`` removes any
    handlers installed at import time.
    """
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer", "vamscli"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# Importing .client pulls in vamscli (which may install stdout log handlers),
# so reconfigure logging immediately afterward and before any client activity.
from .client import (  # noqa: E402
    VamsClient,
    API_ASSETS,
    API_DATABASE_ASSETS,
    SUBSCRIPTION_ENTITY_ASSET,
    SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
)
from .config import Config, ConfigError  # noqa: E402

_force_logging_to_stderr()

try:  # mcp >= 2.0 renamed FastMCP to MCPServer and dropped the fastmcp module
    from mcp.server.mcpserver import MCPServer as McpServer  # noqa: E402
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as McpServer  # noqa: E402

# --- Bootstrap -----------------------------------------------------------

# Config parsing is env-only and always safe. Building the client requires a
# configured vamscli profile; defer failures to run time so the module can be
# imported (e.g. for tests) without a live profile.
CONFIG = Config.from_env()
STARTUP_ERROR: Optional[str] = None

try:
    CLIENT = VamsClient(CONFIG)
except ConfigError as exc:
    CLIENT = None  # type: ignore[assignment]
    STARTUP_ERROR = str(exc)

mcp = McpServer("vams")

# The workflow-executions endpoint rejects a larger page size (Step Functions throttling).
WORKFLOW_EXECUTIONS_MAX_PAGE_SIZE = 50

# The paged execution-detail metadata endpoint clamps a larger page size to this.
EXECUTION_DETAIL_METADATA_MAX_PAGE_SIZE = 500

# find_and_summarize issues one paginated version request PER HIT, so its hit count is the fan-out
# factor of a single tool call. Kept small deliberately; search_assets() pages instead.
_FIND_AND_SUMMARIZE_MAX_HITS = 25
# Its collections. 'input' is the asset/file rows, 'inputDatabase' the database-scope rows, 'output'
# the per-pipeline output metadata; anything else is a 400.
EXECUTION_DETAIL_METADATA_COLLECTIONS = ("input", "inputDatabase", "output")

# The maxItems/pageSize the comment routes apply when the request names neither
# (common.dynamodb.validate_pagination_info). Named here because those routes return no
# continuation token, so it is the ceiling on what is reachable rather than a page size.
COMMENT_LIST_DEFAULT_BOUND = 10000

# check_subscription answers 200 either way and carries the verdict in the message string, so these
# are the two values that decide it. Anything else is reported rather than read as "not subscribed".
SUBSCRIBED_MESSAGE = "success"
NOT_SUBSCRIBED_MESSAGE = "Subscription doesn't exists."


def tool_result(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool so API/client errors are returned as data, not exceptions.

    Agents handle a structured ``{"error": ...}`` far better than a raised
    exception, and it keeps one failing call from derailing a session.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any failure cleanly
            return {"error": str(exc), "error_type": type(exc).__name__}

    return wrapper


# --- Response-shape helpers ----------------------------------------------
#
# These live here rather than on VamsClient deliberately: they are how the tool surface presents a
# response, not part of the client's contract with the CLI (whose `message`-envelope asymmetry other
# callers depend on). Moving them onto the client would couple the two again.


def _unwrap_message_with_warnings(page: Any) -> Any:
    """Unwrap the ``message`` envelope, keeping a sibling top-level ``warnings`` array.

    A pipeline save reports its non-blocking warnings as a SIBLING of ``message`` — the response
    model carries no warnings field, so that array is the only copy and plain unwrapping drops it.
    """
    payload = CLIENT.unwrap_message(page)
    if not isinstance(page, dict) or not isinstance(payload, dict):
        return payload
    warnings = page.get("warnings")
    if not warnings or payload is page:
        return payload
    return {**payload, "warnings": warnings}


def _bounded_message_list(
    page: Any, max_items: Optional[int], page_size: Optional[int], noun: str
) -> Dict[str, Any]:
    """Shape a route that nests a BARE array under ``message`` and returns no continuation token.

    ``CLIENT.unwrap_message`` returns the whole page when ``message`` is not a dict, and
    ``paginate()`` reads ``Items`` off it — so both hand back zero rows for this shape. The array is
    lifted onto ``Items`` here so the tool matches every other list tool.

    The bound is reported rather than assumed away. It is knowable without a token: the handler takes
    maxItems as given, falls back to pageSize, and otherwise applies its own default — so a result
    that reached the effective bound is flagged even when the caller narrowed nothing, which is the
    case a max_items-only check misses.
    """
    items = page.get("message") if isinstance(page, dict) else page
    if not isinstance(items, list):
        items = []
    bound = max_items if max_items is not None else (page_size or COMMENT_LIST_DEFAULT_BOUND)
    result: Dict[str, Any] = {"Items": items, "count": len(items)}
    if len(items) >= bound:
        result["truncated"] = True
        result["note"] = (
            f"Result may be INCOMPLETE: returned {len(items)} {noun}(s), which is the bound in "
            f"force ({bound}). This route returns no continuation token, so there is nothing to "
            "resume with — raise max_items to see more, and do not report this count as a total."
        )
    return result


def _paginate_with_page_metadata(
    fetch_page: Callable[[Dict[str, Any]], Any],
    passthrough_keys: tuple = (),
    **paginate_kwargs: Any,
) -> Dict[str, Any]:
    """Paginate, carrying each page's ``warnings`` and named echo fields onto the result.

    ``CLIENT.paginate`` rebuilds its result from the accumulated items alone, so anything a page
    reported alongside them is lost. A ``warnings`` entry names rows the page WITHHELD, which is
    exactly the case where a short list must not read as a complete one — so it is collected here
    (deduplicated, in order) and marks the result truncated. ``passthrough_keys`` carries a page's
    self-describing echoes (e.g. the applied date window) through as well.
    """
    warnings: List[Any] = []
    echoes: Dict[str, Any] = {}

    def _collect(params: Dict[str, Any]) -> Any:
        page = fetch_page(params)
        payload = CLIENT.unwrap_message(page)
        if isinstance(payload, dict):
            for warning in payload.get("warnings") or []:
                if warning not in warnings:
                    warnings.append(warning)
            for key in passthrough_keys:
                if key not in echoes and payload.get(key):
                    echoes[key] = payload[key]
        # Returned untouched: paginate() reads the items off the same page itself.
        return page

    result = CLIENT.paginate(_collect, **paginate_kwargs)
    result.update(echoes)
    if warnings:
        result["warnings"] = warnings
        # Rows were withheld, so the walk did not see everything even when no token remains.
        result["truncated"] = True
    return result


# =========================================================================
# READ / SEARCH TOOLS (always available)
# =========================================================================


@mcp.tool()
@tool_result
def list_allowed_api_routes() -> Dict[str, Any]:
    """List the VAMS API routes and methods the authenticated user is authorized
    to call. Call this first to scope what this session can actually do — the
    user's two-tier permissions bound every other tool, and a route missing here
    will be refused with a 403."""
    return CLIENT.api.list_allowed_api_routes()


@mcp.tool()
@tool_result
def list_databases(
    include_deleted: bool = False,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List VAMS databases (auto-paginated). Returns database IDs, descriptions,
    asset counts, and bucket info.

    The walk is BOUNDED. `truncated` in the result means rows were not seen, `note` says which bound
    stopped it, and `NextToken` (when present) continues the walk — pass it back as `starting_token`.
    Never report a count, or conclude something does not exist, from a truncated result."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_databases(show_deleted=include_deleted, params=params),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def get_database(database_id: str, include_deleted: bool = False) -> Dict[str, Any]:
    """Get details for a single database by ID."""
    return CLIENT.api.get_database(database_id, include_deleted)


@mcp.tool()
@tool_result
def list_buckets(
    max_items: Optional[int] = None, starting_token: Optional[str] = None
) -> Dict[str, Any]:
    """List asset storage buckets available for creating databases.

    The walk is BOUNDED: `truncated` means rows were not seen, and `NextToken` continues it via
    `starting_token`. Do not report a count from a truncated result."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_buckets(params=params),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def list_assets(
    database_id: Optional[str] = None,
    include_archived: bool = False,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List assets, scoped to a database when database_id is given, otherwise
    across all databases the user can read (auto-paginated).

    The walk is BOUNDED, and a large deployment holds far more assets than one walk returns.
    `truncated` means rows were not seen, `note` says which bound stopped it, and `NextToken`
    continues the walk — pass it back as `starting_token`. Do not report an asset count, or conclude
    an asset does not exist, from a truncated result; use search_assets() to look one up by name."""
    endpoint = API_DATABASE_ASSETS.format(databaseId=database_id) if database_id else API_ASSETS

    def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
        if include_archived:
            params = {**params, "showArchived": "true"}
        return CLIENT.get_json(endpoint, params=params)

    return CLIENT.paginate(fetch, max_items=max_items, starting_token=starting_token)


@mcp.tool()
@tool_result
def get_asset(database_id: str, asset_id: str, include_archived: bool = False) -> Dict[str, Any]:
    """Get details for a single asset."""
    return CLIENT.api.get_asset(database_id, asset_id, show_archived=include_archived)


@mcp.tool()
@tool_result
def list_asset_files(
    database_id: str,
    asset_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List files belonging to an asset (auto-paginated).

    The walk is BOUNDED, and a real asset routinely holds thousands of files — more than one walk
    returns. `truncated` means files were not seen, `note` says which bound stopped it, and
    `NextToken` continues the walk: pass it back as `starting_token`. Never report a file count, or
    answer "file X is not in this asset", from a truncated result — use search_files() to test for a
    specific file instead."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_asset_files(database_id, asset_id, params=params),
        max_items=max_items,
        items_key="items",
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def get_asset_metadata(
    database_id: str,
    asset_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Get metadata key/value pairs for an asset (auto-paginated).

    The walk is BOUNDED: `truncated` means rows were not seen and `NextToken` continues it via
    `starting_token`. A truncated result is not the asset's full metadata, so do not conclude a key
    is absent from one."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_asset_metadata_v2(
            database_id, asset_id, page_size=params["pageSize"], starting_token=params.get("startingToken")
        ),
        items_key="metadata",
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def get_database_metadata(
    database_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Get metadata key/value pairs for a database (auto-paginated).

    The walk is BOUNDED: `truncated` means rows were not seen and `NextToken` continues it via
    `starting_token`. Do not conclude a key is absent from a truncated result."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_database_metadata_v2(
            database_id, page_size=params["pageSize"], starting_token=params.get("startingToken")
        ),
        items_key="metadata",
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def list_asset_versions(
    database_id: str,
    asset_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List versions of an asset (auto-paginated).

    The walk is BOUNDED: `truncated` means versions were not seen, and `NextToken` continues it via
    `starting_token`. Do not report a version count from a truncated result."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_asset_versions(database_id, asset_id, params=params),
        max_items=max_items,
        items_key="versions",
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def get_asset_version(database_id: str, asset_id: str, asset_version_id: str) -> Dict[str, Any]:
    """Get a specific asset version by its version ID."""
    return CLIENT.api.get_asset_version(database_id, asset_id, asset_version_id)


@mcp.tool()
@tool_result
def get_asset_history(
    database_id: str,
    asset_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Get the asset lifecycle history (create/edit/archive/unarchive/delete
    records, newest first, auto-paginated).

    The walk is BOUNDED and starts from the NEWEST record, so a `truncated` result is the recent
    history rather than all of it — the absence of an event in one does not mean it never happened.
    `NextToken` continues the walk via `starting_token`."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_asset_history(database_id, asset_id, params=params),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def get_asset_links(database_id: str, asset_id: str, child_tree_view: bool = False) -> Dict[str, Any]:
    """List relationship links for an asset (related/parent/child assets).

    A `child_tree_view` walk is BOUNDED (100 levels, 10,000 nodes), so `treeTruncated` true means the
    returned tree is partial rather than the whole hierarchy — read the tree of an asset further down
    it for the remainder. `unresolvedCounts` counts links whose asset could not be read, which is
    separate from `unauthorizedCounts` and usually clears on a retry."""
    return CLIENT.api.get_asset_links_for_asset(database_id, asset_id, child_tree_view=child_tree_view)


def _escape_query_string_value(value: str) -> str:
    """Escape a value being interpolated into a Lucene ``query_string`` phrase.

    A raw `"` would close the phrase and let an agent-supplied id alter the query syntax; a raw `\\`
    would be read as an escape. Both are escaped, in that order.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


# There is no asset database called GLOBAL — it is the unscoped keyword used for the shared pipeline
# and workflow catalogs. Filtering assets or files on it matches nothing, so it is treated as
# "unscoped", matching the web's isAllDatabases().
_UNSCOPED_DATABASE_IDS = {"GLOBAL"}


def _build_search_request(
    entity_types: List[str],
    query: Optional[str],
    database_id: Optional[str],
    metadata_query: Optional[str],
    size: int,
    include_archived: bool,
    geo_search: Optional[Dict[str, Any]] = None,
    from_offset: int = 0,
    sort_field: Optional[str] = None,
    sort_desc: bool = True,
) -> Dict[str, Any]:
    request: Dict[str, Any] = {
        "entityTypes": entity_types,
        "from": max(0, from_offset),
        "size": max(1, min(size, 2000)),
        "includeArchived": include_archived,
        "explainResults": False,
        "includeMetadataInSearch": True,
        # Relevance ordering by default; a named field replaces it so "the 10 most recent" is
        # expressible at all.
        "sort": [{sort_field: {"order": "desc" if sort_desc else "asc"}}] if sort_field else ["_score"],
    }
    if query:
        request["query"] = query
    if metadata_query:
        request["metadataQuery"] = metadata_query
        request["metadataSearchMode"] = "both"
    if database_id and database_id not in _UNSCOPED_DATABASE_IDS:
        # `.keyword`, not the bare field: `str_databaseid` is ANALYZED, so the standard analyzer
        # splits on hyphens and the quoted phrase `"smoke-db"` matches the adjacent token sequence
        # [smoke, db] — which `smoke-db-2` ([smoke, db, 2]) also contains. Verified against a
        # deployed index: the bare-field filter returned 24 smoke-db assets PLUS one from smoke-db-2.
        # The filter must stay a `query_string`: SearchFilterModel in backend/backend/models/search.py
        # declares that key as required, so a bare `{"term": ...}` is rejected by Pydantic before it
        # reaches OpenSearch. The value stays quoted so a hyphenated id is not tokenized.
        escaped = _escape_query_string_value(database_id)
        request["filters"] = [{"query_string": {"query": f'str_databaseid.keyword:"{escaped}"'}}]
    if geo_search:
        request["geoSearch"] = geo_search
    return request


@mcp.tool()
@tool_result
def search_assets(
    query: Optional[str] = None,
    database_id: Optional[str] = None,
    metadata_query: Optional[str] = None,
    size: int = 25,
    include_archived: bool = False,
    geo_search: Optional[Dict[str, Any]] = None,
    from_offset: int = 0,
    sort_field: Optional[str] = None,
    sort_desc: bool = True,
) -> Dict[str, Any]:
    """Full-text / metadata / geospatial search across assets (OpenSearch).

    - query: free text (matches names, descriptions, metadata)
    - database_id: restrict to one database. "GLOBAL" is not an asset database — it is the unscoped
      keyword for the shared pipeline/workflow catalogs — so passing it searches every database
      rather than returning nothing.
    - metadata_query: metadata field search, as `key:value`. All of a record's metadata is
      stored in one field named `MD_` with the keys carried verbatim, so metadata
      {"product": "Training"} reads back as "MD_": {"product": "Training"} in a hit's
      `_source`; file attributes use `AB_` the same way, on the file index only. The key may
      be written bare ('product:Training'), with the entity prefix ('MD_product:Training',
      or 'AB_colour:red'), or with a type prefix ('MD_str_product:Training') — all three
      address the same field. Do NOT write 'MD_.product': the dot belongs to the internal
      query path, not to a submitted key, and such a query matches nothing. Call
      get_search_fields() for the mapping, which lists `MD_` itself rather than the keys
      inside it.
    - geo_search: geospatial filter on `geo_MD_location`. Supply exactly one of
      `point` ({lat, lon, radiusMeters}), `bbox` ({topLeft, bottomRight} of
      points), or `geoJson` (GeoJSON geometry/Feature/FeatureCollection), plus
      an optional `relation` of intersects (default) / within / contains /
      disjoint.
    - size / from_offset: one page of hits. The result reports `total` (all matches) and `returned`
      (this page); when total exceeds returned, page on by re-issuing the same query with
      from_offset advanced by size rather than by raising size to swallow everything.
    - sort_field / sort_desc: order by an indexed field instead of relevance, e.g.
      sort_field="dateCreated" for the most recent first. Call get_search_fields() for the field
      names. Ordering by relevance (the default) cannot answer "the newest N".

    Returns a compact list of hits (id, score, source fields)."""
    request = _build_search_request(
        ["asset"], query, database_id, metadata_query, size, include_archived, geo_search,
        from_offset=from_offset, sort_field=sort_field, sort_desc=sort_desc,
    )
    raw = CLIENT.api.search_query(request)
    # Trimmed with the same clamp the request carries: a size of 0 or a negative asks OpenSearch for
    # one hit, so trimming to the raw value would report an empty list for a query that matched.
    return CLIENT.trim_search_results(raw, max_hits=request["size"])


@mcp.tool()
@tool_result
def search_files(
    query: Optional[str] = None,
    database_id: Optional[str] = None,
    metadata_query: Optional[str] = None,
    size: int = 25,
    include_archived: bool = False,
    geo_search: Optional[Dict[str, Any]] = None,
    from_offset: int = 0,
    sort_field: Optional[str] = None,
    sort_desc: bool = True,
) -> Dict[str, Any]:
    """Full-text / metadata / geospatial search across asset files (OpenSearch).

    Takes the same database_id, metadata_query, geo_search, paging (size / from_offset) and ordering
    (sort_field / sort_desc) semantics as search_assets."""
    request = _build_search_request(
        ["file"], query, database_id, metadata_query, size, include_archived, geo_search,
        from_offset=from_offset, sort_field=sort_field, sort_desc=sort_desc,
    )
    raw = CLIENT.api.search_query(request)
    return CLIENT.trim_search_results(raw, max_hits=request["size"])


@mcp.tool()
@tool_result
def get_search_fields() -> Dict[str, Any]:
    """List available search fields/types (the OpenSearch index mapping)."""
    return CLIENT.api.get_search_mapping()


@mcp.tool()
@tool_result
def list_workflows(
    database_id: Optional[str] = None,
    include_archived: bool = False,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List workflows, optionally scoped to a database (auto-paginated).

    Archived workflows are filtered out server-side unless include_archived is set, which is how an
    archived workflow's id is found in order to restore it.

    The walk is BOUNDED: `truncated` means rows were not seen and `NextToken` continues it via
    `starting_token`. A workflow missing from a truncated result may simply be past the bound.
    """
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_workflows(
            database_id=database_id, include_archived=include_archived, params=params
        ),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def list_workflow_executions(
    database_id: str,
    asset_id: str,
    workflow_id: Optional[str] = None,
    workflow_database_id: Optional[str] = None,
    status: Optional[str] = None,
    trigger_type: Optional[str] = None,
    group_id: Optional[str] = None,
    triggered_by_user_id: Optional[str] = None,
    filter_start_date: Optional[str] = None,
    filter_end_date: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List workflow executions for an asset (auto-paginated).

    Optionally narrow to one workflow. A workflow id is unique across every database including
    "GLOBAL", so the id identifies the workflow on its own; workflow_database_id is an additional
    narrowing filter rather than a disambiguator, and a value that is not the workflow's own database
    silently empties the result instead of erroring.

    The listing is lower-bounded by start date: `filterStartDate` reports the applied window (90 days
    back by default), so an asset last processed before it lists nothing at all — that is the window,
    not an absence of history. Widen it with filter_start_date; both dates are UTC timestamps of the
    form "YYYY-MM-DDTHH:MM:SSZ" and any other spelling is rejected with a 400.

    The remaining filters each match for equality: status (e.g. RUNNING, SUCCEEDED, FAILED, ABORTED),
    trigger_type, group_id, and triggered_by_user_id.

    A `warnings` entry means the page WITHHELD rows, for either of two reasons: it reached the cap on
    executions inspected for this asset, or it spent its budget re-checking runs an earlier page
    already listed. Each entry names which. The result is then also flagged `truncated`. Do not
    report a run count or conclude a run does not exist from a result carrying warnings — narrow the
    filters and list again.

    The walk is also BOUNDED independently of that, and this endpoint's page size is capped at 50 so
    the bound arrives sooner than elsewhere. `truncated` means runs were not seen, `note` says which
    bound stopped it, and `NextToken` continues the walk via `starting_token`.
    """
    extra = {
        key: value
        for key, value in (
            ("status", status),
            ("triggerType", trigger_type),
            ("groupId", group_id),
            ("triggeredByUserId", triggered_by_user_id),
            ("filterStartDate", filter_start_date),
            ("filterEndDate", filter_end_date),
        )
        if value
    }

    def _call(params: Dict[str, Any]) -> Dict[str, Any]:
        return CLIENT.api.list_workflow_executions(
            database_id,
            asset_id,
            workflow_database_id=workflow_database_id,
            workflow_id=workflow_id,
            params={**params, **extra},
        )

    return _paginate_with_page_metadata(
        _call,
        passthrough_keys=("filterStartDate", "filterEndDate"),
        max_items=max_items,
        # The executions endpoint caps pageSize at 50 to avoid Step Functions throttling.
        page_size=min(CONFIG.page_size, WORKFLOW_EXECUTIONS_MAX_PAGE_SIZE),
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def list_tags(
    database: Optional[str] = None,
    scope: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List all tags.

    database: restrict to only that database's tags (global tags are not included; use scope='global'/'all' for those).
    scope: 'global' for global tags only, 'all' for every tag.

    The walk is BOUNDED: `truncated` means tags were not seen and `NextToken` continues it via
    `starting_token`. Do not conclude a tag does not exist from a truncated result.
    """
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_tags(params=params, database_id=database, scope=scope),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def list_tag_types(
    database: Optional[str] = None,
    scope: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List all tag types.

    database: restrict to only that database's tag types (global tag types are not included; use scope='global'/'all' for those).
    scope: 'global' for global tag types only, 'all' for every tag type.

    The walk is BOUNDED: `truncated` means tag types were not seen and `NextToken` continues it via
    `starting_token`. Do not conclude a tag type does not exist from a truncated result.
    """
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_tag_types(params=params, database_id=database, scope=scope),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def list_metadata_schemas(
    database_id: Optional[str] = None,
    metadata_entity_type: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List metadata schemas, optionally filtered by database and entity type
    (databaseMetadata, assetMetadata, fileMetadata, fileAttribute,
    assetLinkMetadata).

    The walk is BOUNDED: `truncated` means schemas were not seen and `NextToken` continues it via
    `starting_token`. Do not conclude a schema is undefined from a truncated result."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_metadata_schemas(
            database_id=database_id,
            metadata_entity_type=metadata_entity_type,
            page_size=params["pageSize"],
            starting_token=params.get("startingToken"),
        ),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def generate_download_url(
    database_id: str,
    asset_id: str,
    file_key: Optional[str] = None,
    version_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a time-limited presigned download URL for an asset file.
    Non-mutating: creates a URL, does not transfer data through this server.

    The URL is a bearer credential: it carries its own Amazon S3 signature, needs no further
    authentication, and anyone holding it can download the object until it expires. The lifetime is
    the deployment's `app.authProvider.presignedUrlTimeoutSeconds` — 24 hours by default. Returning
    one here puts it in the agent transcript, and therefore in whatever conversation log, trace, or
    telemetry the host retains, so for the whole of that window it is readable by everything with
    access to those. Generate one only when a download was actually asked for, and treat any URL
    already generated as disclosed.

    A deployment can bound where the URL works with
    `app.assetBuckets.presignedUrlNetworkRestrictions` (allowedIpRanges / allowedVpceIds), which
    denies presigned-URL requests originating outside those networks. It is unset by default."""
    return CLIENT.api.download_asset_file(
        database_id, asset_id, file_key=file_key, version_id=version_id
    )


@mcp.tool()
@tool_result
def find_and_summarize(query: str, database_id: Optional[str] = None, size: int = 10) -> Dict[str, Any]:
    """Composite: search assets, then enrich each hit with details and version
    count in a single call. Best for 'find X and tell me about them' requests.

    COST: one search plus ONE additional paginated request per hit, so a call costs `size` + 1
    authenticated API requests. `size` is clamped to 25 for that reason — this tool is for
    summarizing a handful of results, not for enumerating a database. Use search_assets() (which
    pages with from_offset) when you need more, and list_asset_versions() when you need a specific
    asset's versions in full.

    Each entry's `version_count` is therefore the count on the FIRST page of versions;
    `version_count_truncated` marks an asset with more versions than one page holds, and its count
    must not be reported as a total."""
    # Clamped rather than passed through: `size` fans out one paginated version walk per hit, and an
    # unclamped value turns one auto-approved tool call into thousands of API Gateway requests and
    # minutes of wall clock. It also bounds the `_source` documents this returns into the transcript.
    effective_size = max(1, min(size, _FIND_AND_SUMMARIZE_MAX_HITS))
    request = _build_search_request(["asset"], query, database_id, None, effective_size, False)
    trimmed = CLIENT.trim_search_results(CLIENT.api.search_query(request), max_hits=effective_size)

    enriched: List[Dict[str, Any]] = []
    for hit in trimmed.get("results", []):
        source = hit.get("source", {}) or {}
        db = source.get("str_databaseid") or database_id
        aid = source.get("str_assetid") or hit.get("id")
        entry: Dict[str, Any] = {"asset_id": aid, "database_id": db, "score": hit.get("score"), "source": source}
        if db and aid:
            try:
                # One page per hit. Without max_items each inner walk may issue up to max_pages
                # requests, so the fan-out multiplies rather than adds — and only `count` is used
                # here, so the extra pages are fetched and discarded.
                versions = CLIENT.paginate(
                    lambda params: CLIENT.api.get_asset_versions(db, aid, params=params),
                    items_key="versions",
                    max_items=CONFIG.page_size,
                )
                entry["version_count"] = versions.get("count")
                if versions.get("truncated"):
                    entry["version_count_truncated"] = True
            except Exception as exc:  # noqa: BLE001
                entry["version_lookup_error"] = str(exc)
        enriched.append(entry)

    result: Dict[str, Any] = {
        "total": trimmed.get("total"),
        "returned": len(enriched),
        "assets": enriched,
    }
    if size > effective_size:
        result["note"] = (
            f"size was clamped from {size} to {effective_size}: this tool issues one paginated "
            "request per hit. Use search_assets(from_offset=...) to page a larger result set."
        )
    return result


# =========================================================================
# PIPELINE / WORKFLOW / EXECUTION READ TOOLS
#
# The orchestration surface: pipelines (and their config templates + tag schemas), workflows (and
# their triggers), and executions. Read tools are always exposed — inspecting what a deployment runs,
# and why a run failed, is the common agentic task and carries no side effects.
# =========================================================================


@mcp.tool()
@tool_result
def list_pipelines(
    database_id: Optional[str] = None,
    include_archived: bool = False,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List processing pipelines, optionally scoped to a database (auto-paginated).

    Omit database_id to list every pipeline the user can see, including the shared GLOBAL catalog.

    The walk is BOUNDED: `truncated` means rows were not seen and `NextToken` continues it via
    `starting_token`. A pipeline missing from a truncated result may simply be past the bound.
    """
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_pipelines(
            database_id=database_id, include_archived=include_archived, params=params
        ),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def get_pipeline(
    database_id: str, pipeline_id: str, include_archived: bool = False
) -> Dict[str, Any]:
    """Get one pipeline with its execution settings, admin settings, and config templates.

    The inline templates list is capped at the first 10; templateCount is the true total. When it
    exceeds the inline count, use list_pipeline_templates() to page the full set.
    """
    return CLIENT.unwrap_message(CLIENT.api.get_pipeline(database_id, pipeline_id, include_archived=include_archived))


@mcp.tool()
@tool_result
def list_pipeline_templates(database_id: str, pipeline_id: str) -> Dict[str, Any]:
    """List a pipeline's configuration templates.

    The list omits each template's tagSchema and blanks S3-offloaded config bodies — use
    get_pipeline_template() for the full, rehydrated template.
    """
    return CLIENT.unwrap_message(CLIENT.api.list_pipeline_templates(database_id, pipeline_id))


@mcp.tool()
@tool_result
def get_pipeline_template(database_id: str, pipeline_id: str, template_id: str) -> Dict[str, Any]:
    """Get one pipeline template, including its tag schema and full config body."""
    return CLIENT.unwrap_message(CLIENT.api.get_pipeline_template(database_id, pipeline_id, template_id))


@mcp.tool()
@tool_result
def get_pipeline_template_tag_schema(
    database_id: str, pipeline_id: str, template_id: str
) -> Dict[str, Any]:
    """Get a template's tag schema — the fields a caller supplies when running it."""
    return CLIENT.unwrap_message(CLIENT.api.get_pipeline_template_tag_schema(database_id, pipeline_id, template_id))


@mcp.tool()
@tool_result
def get_workflow(database_id: str, workflow_id: str, include_archived: bool = False) -> Dict[str, Any]:
    """Get one workflow with its pipeline references, input/output rules, and triggers."""
    return CLIENT.unwrap_message(CLIENT.api.get_workflow(database_id, workflow_id, include_archived=include_archived))


@mcp.tool()
@tool_result
def list_workflow_triggers(
    database_id: str,
    workflow_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List a workflow's triggers (e.g. fileUpload) and whether each is enabled (auto-paginated).

    A workflow may carry several triggers of one type, each with its own filters and default templates.
    Each item's `triggerType` is its KEY — the bare type for the first trigger of a type, or
    'type#triggerId' for an additional one — and is what the get/set/delete tools take.

    The walk is BOUNDED: `truncated` means triggers were not seen and `NextToken` continues it via
    `starting_token`. A trigger missing from a truncated result may simply be past the bound.
    """
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_workflow_triggers(database_id, workflow_id, params=params),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def get_workflow_trigger(database_id: str, workflow_id: str, trigger_type: str) -> Dict[str, Any]:
    """Get one workflow trigger by its key, including its input-file filters.

    `trigger_type` is the trigger KEY from list_workflow_triggers: the bare type (e.g. 'fileUpload') for
    a workflow's first trigger of that type, or 'type#triggerId' for an additional one.
    """
    return CLIENT.unwrap_message(CLIENT.api.get_workflow_trigger(database_id, workflow_id, trigger_type))


@mcp.tool()
@tool_result
def list_executions(
    status: Optional[str] = None,
    workflow_id: Optional[str] = None,
    workflow_database_id: Optional[str] = None,
    trigger_type: Optional[str] = None,
    group_id: Optional[str] = None,
    triggered_by_user_id: Optional[str] = None,
    filter_start_date: Optional[str] = None,
    filter_end_date: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List workflow executions across every workflow and database the user can see.

    Distinct from list_workflow_executions(), which is scoped to ONE asset's history. The filters
    each match for equality: status (e.g. RUNNING, SUCCEEDED, FAILED, ABORTED), workflow_id,
    workflow_database_id, trigger_type, group_id, and triggered_by_user_id. group_id is how a group's
    members are enumerated — rerun_execution re-runs one execution at a time, so a group re-run means
    listing the group here first.

    An execution is listed only when the user can read its workflow, every asset the run read, and
    the asset it wrote to. A run whose output landed in an asset the user cannot read is therefore
    absent even when the user can read its inputs — an omission by permission, not by date.

    The listing is lower-bounded by start date: `filterStartDate` reports the applied window (90 days
    back by default), so executions older than it are absent by design, not missing. Reach them with
    filter_start_date, and bound the window above with filter_end_date; both are UTC timestamps of
    the form "YYYY-MM-DDTHH:MM:SSZ" and any other spelling is rejected with a 400.

    A `warnings` entry means the walk WITHHELD rows, for either of two reasons: a page reached a cap
    on the distinct assets it resolves for permission checks and skipped the executions it could not
    evaluate, or it spent its per-request work budget before filling the page. Each entry names which.
    The result is then also flagged `truncated`. Do not report a count or conclude an execution does
    not exist from a result carrying warnings — narrow the filters and list again.

    The walk is also BOUNDED independently of that: `note` says which bound stopped it, and
    `NextToken` continues it — pass the token back as `starting_token`.
    """
    extra = {
        key: value
        for key, value in (
            ("status", status),
            ("workflowId", workflow_id),
            ("workflowDatabaseId", workflow_database_id),
            ("triggerType", trigger_type),
            ("groupId", group_id),
            ("triggeredByUserId", triggered_by_user_id),
            ("filterStartDate", filter_start_date),
            ("filterEndDate", filter_end_date),
        )
        if value
    }

    def _call(params: Dict[str, Any]) -> Dict[str, Any]:
        return CLIENT.api.list_executions(params={**params, **extra})

    return _paginate_with_page_metadata(
        _call,
        passthrough_keys=("filterStartDate", "filterEndDate"),
        max_items=max_items,
        # Same Step Functions throttling cap the per-asset listing respects.
        page_size=min(CONFIG.page_size, WORKFLOW_EXECUTIONS_MAX_PAGE_SIZE),
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def get_execution_details(execution_id: str) -> Dict[str, Any]:
    """Get an execution's full detail: per-pipeline step status, inputs, outputs, and any error.

    This is the tool to reach for when asked why a run failed or what it produced.

    Metadata the run read arrives in two SEPARATE collections: `inputMetadata` (asset- and
    file-scope rows) and `inputDatabaseMetadata` (database-scope rows, which belong to no asset).
    Reading only the first understates what the run saw.

    Each row carries TWO content maps: `metadata` and `attributes` (that file's attributes). A pipeline
    is granted fileMetadata and fileAttributes independently, so reading only `metadata` understates
    what a step received — a row may hold attributes with an empty `metadata` map. Asset-level and
    database-scope rows always report an empty `attributes` map.

    The sources are reported alongside them:
    `metadataSourceAssets` ([{databaseId, assetId}], assets read purely as sources),
    `metadataSourceDatabases` (EVERY database whose metadata was captured — for a run with input
    files these are derived from those files' assets), and `metadataSourceDatabaseId` (only the one
    database the caller NAMED, which only a run with no input files has). Render the databases list,
    not the named id, when reporting what a run actually read.

    The response can be PARTIAL. Every collection is bounded server-side, and `truncatedCollections`
    lists the names of any that were cut ("inputFiles", "inputMetadata", "inputDatabaseMetadata",
    "outputs.files", "outputs.metadata", "outputs.results"). Check it before reporting a count or
    concluding something is absent — a name in that list means more rows exist than are shown. For a
    truncated METADATA collection, read the rest with page_execution_detail_metadata(); a truncated
    FILE collection has no paged equivalent, so the flag is the only signal it is incomplete.

    A pipeline entry reports its configuration in TWO places, at different STAGES of the same body:

    - `renderedConfig` — the inline copy, post-user-tag but PRE-system-tag, so its system-tag
      placeholders are still unsubstituted. Held inline only up to a size limit;
      `renderedConfigTruncated` (emitted unconditionally) reports whether this copy was shortened,
      and a bounded step section can also name "pipelines" / "inputConfigurations" in
      `truncatedCollections`.
    - `renderedConfigLocation` ({"bucket", "key"}) — the Amazon S3 object holding the FULLY
      substituted body the pipeline actually read. Present whenever that object exists, NOT only on
      truncation.

    So to report what a step really ran with, read the location's object even when the inline copy is
    complete — which is the common case. Diagnosing from `renderedConfig` alone reports a config the
    step never saw.
    """
    return CLIENT.unwrap_message(CLIENT.api.get_execution_details(execution_id))


@mcp.tool()
@tool_result
def page_execution_detail_metadata(
    execution_id: str,
    collection: str = "input",
    pipeline_id: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Read one metadata collection of an execution's detail view in full (auto-paginated).

    get_execution_details() bounds its metadata collections and names any it cut in
    `truncatedCollections`. This tool reads a named collection past that bound, so reach for it when
    `truncatedCollections` contains "inputMetadata", "inputDatabaseMetadata", or "outputs.metadata"
    — reporting a count from the truncated details response instead understates what the run read.

    `collection` is one of "input" (asset- and file-scope input metadata, the default),
    "inputDatabase" (database-scope input metadata, which belongs to no asset), or "output"
    (per-pipeline output metadata); any other value is rejected. The two input collections are
    SEPARATE halves of what the run read, matching the details view's split — read both to see all of
    it. Pass pipeline_id to narrow to a single workflow step's rows.

    Rows carry the same fields the details view returns plus the producing `pipelineId`: the input
    collections give {databaseId, assetId, filePath, scope, metadata}, and "output" gives
    {targetFilePath, metadataKey, metadataValue}. `truncated` in the result means the row cap was
    reached before the walk finished, not that the collection ends there — `NextToken` continues it,
    passed back as `starting_token` alongside the SAME collection and pipeline_id (the token is
    pinned to them and a mismatch is answered with a 400).
    """
    if collection not in EXECUTION_DETAIL_METADATA_COLLECTIONS:
        return {
            "error": f"collection must be one of {list(EXECUTION_DETAIL_METADATA_COLLECTIONS)}",
            "error_type": "ValueError",
        }

    extra: Dict[str, Any] = {"collection": collection}
    if pipeline_id:
        extra["pipelineId"] = pipeline_id

    def _call(params: Dict[str, Any]) -> Dict[str, Any]:
        # The continuation token is only valid alongside the collection and pipelineId it was issued
        # with, so every page carries the same filters.
        return CLIENT.api.get_execution_details_metadata(execution_id, params={**params, **extra})

    result = CLIENT.paginate(
        _call,
        max_items=max_items,
        page_size=min(CONFIG.page_size, EXECUTION_DETAIL_METADATA_MAX_PAGE_SIZE),
        starting_token=starting_token,
    )
    result["collection"] = collection
    return result


@mcp.tool()
@tool_result
def get_execution_logs(
    execution_id: str,
    mode: str = "full",
    pipeline_execution_id: Optional[str] = None,
    limit: Optional[int] = None,
    next_token: Optional[str] = None,
    filter_pattern: Optional[str] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
) -> Dict[str, Any]:
    """Retrieve an execution's logs.

    mode="full" reads live CloudWatch (reliable; the stored copy is often empty because the
    end-state lambda captures it before CloudWatch finishes ingesting). mode="truncated" reads the
    stored text with a server-side live fallback. Pass pipeline_execution_id to scope to one step.

    Full mode returns at most `limit` events (default 100, server cap 1000) and a `nextToken` when
    more remain. A pipeline container emits thousands of lines, so the failure is routinely past the
    first page: raise `limit`, then walk the rest by passing the returned `nextToken` back with the
    SAME mode, pipeline_execution_id, limit, and filter_pattern. Reporting "the logs show no error"
    from a page that returned a token is a wrong conclusion, not an incomplete read.

    The other full-mode narrowing options: `filter_pattern` is matched as a literal substring (not
    CloudWatch pattern syntax) on top of the execution scope, and `start_time` / `end_time` bound the
    window in epoch MILLISECONDS.

    `next_token` is a CloudWatch token and continues only the `events` list. With no pipeline scoped
    the response also carries `sfnHistoryEvents` (the Step Functions timeline, which needs no
    CloudWatch ingestion), and that section is served only on a tokenless first call — read it there
    rather than expecting it on a continuation.

    This route is administrative — it exposes full execution logs — so a role without it will get a
    403 rather than empty output.
    """
    params: Dict[str, Any] = {"mode": mode}
    if pipeline_execution_id:
        params["pipelineExecutionId"] = pipeline_execution_id
    # Sent in full mode only, as `vamscli execution logs` does: truncated mode joins its events into
    # one text blob and returns no continuation token, so there is nothing there to page.
    if mode == "full":
        for key, value in (
            ("limit", limit),
            ("nextToken", next_token),
            ("filterPattern", filter_pattern),
            ("startTime", start_time),
            ("endTime", end_time),
        ):
            if value:
                params[key] = value
    return CLIENT.unwrap_message(CLIENT.api.get_execution_logs(execution_id, params=params))


# =========================================================================
# COMMENT / SUBSCRIPTION / API-KEY READ TOOLS
#
# Comments are the per-asset-version discussion thread; subscriptions are who gets notified when an
# asset changes. Both answer with the legacy `{"message": ...}` envelope, and not uniformly: the
# comment listings nest a BARE ARRAY under it while the subscription listing nests the usual
# Items/NextToken page, so each is unwrapped for its own shape rather than a shared one.
# =========================================================================


@mcp.tool()
@tool_result
def list_asset_comments(
    asset_id: str,
    max_items: Optional[int] = None,
    page_size: Optional[int] = None,
) -> Dict[str, Any]:
    """List the comments on an asset, across every version of it.

    Each row carries assetId, the composite `assetVersionId:commentId` key, commentBody,
    commentOwnerID / commentOwnerUsername and dateCreated. Rows come back ordered by that composite
    key descending, which for a uuid4 comment id is arbitrary within a version — sort on dateCreated
    rather than reading anything into the position.

    This route CANNOT be paged. It applies max_items / page_size and then discards the pagination
    token, so there is nothing to resume with and comments past the bound are unreachable through the
    API. `truncated` is set when the result reached the bound in force — the max_items you supplied,
    else page_size, else the deployment's own default of 10000 — and the count is then a floor rather
    than a total: raise max_items rather than reporting it, or narrow with
    list_asset_version_comments().

    Deleted comments are never returned: the route accepts a showDeleted flag and the service ignores
    it, so this tool does not offer one.
    """
    return _bounded_message_list(
        CLIENT.api.list_asset_comments(asset_id, max_items=max_items, page_size=page_size),
        max_items,
        page_size,
        "comment",
    )


@mcp.tool()
@tool_result
def list_asset_version_comments(
    asset_id: str,
    asset_version_id: str,
    max_items: Optional[int] = None,
    page_size: Optional[int] = None,
) -> Dict[str, Any]:
    """List the comments on ONE version of an asset.

    Use list_asset_versions() to find an asset_version_id. Same row shape and the same unpageable
    bound as list_asset_comments(): the token is discarded, so a result that reached max_items cannot
    be continued and its count is a floor.
    """
    return _bounded_message_list(
        CLIENT.api.list_asset_version_comments(
            asset_id, asset_version_id, max_items=max_items, page_size=page_size
        ),
        max_items,
        page_size,
        "comment",
    )


@mcp.tool()
@tool_result
def get_comment(asset_id: str, asset_version_id: str, comment_id: str) -> Dict[str, Any]:
    """Read one comment, addressed by asset, asset version and comment id.

    The endpoint answers 200 with an empty object for a comment that does not exist rather than 404,
    so absence surfaces here as a CommentNotFoundError in the `error` field — not as an empty
    success. Neither id may contain a colon: the two are joined into one `assetVersionId:commentId`
    path segment, and an extra colon shifts which value the handler validates.
    """
    return CLIENT.unwrap_message(CLIENT.api.get_comment(asset_id, asset_version_id, comment_id))


@mcp.tool()
@tool_result
def list_subscriptions(
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List the event subscriptions on this deployment (auto-paginated).

    Each row carries eventName, entityName, entityId, subscribers, entityValue and databaseId. A
    subscription is keyed on (eventName, entityName, entityId) — that triple is what the write and
    delete tools address, and `subscribers` is a field of the row rather than a separate record.

    The walk is BOUNDED: `truncated` means subscriptions were not seen and `NextToken` continues it
    via `starting_token`. Do not conclude a user is unsubscribed from a truncated result — use
    check_subscription() for one asset, which answers without paging.
    """
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_subscriptions(
            page_size=params["pageSize"],
            starting_token=params.get("startingToken"),
        ),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool()
@tool_result
def check_subscription(asset_id: str, user_id: str) -> Dict[str, Any]:
    """Check whether a user is subscribed to an asset's version changes.

    Returns `subscribed` (boolean) alongside the endpoint's raw `message`. The endpoint answers HTTP
    200 in BOTH cases and carries the verdict only in that string, so a successful call is never on
    its own an answer. The event and entity are fixed by the route ('Asset Version Change' on
    'Asset'); use list_subscriptions() for any other event or entity type.

    `unrecognizedResponse` is set when the message is neither of the two known values, meaning the
    verdict could not be read — treat that as unknown rather than as not subscribed.
    """
    response = CLIENT.api.check_subscription(asset_id, user_id)
    message = response.get("message") if isinstance(response, dict) else response
    result: Dict[str, Any] = {"subscribed": message == SUBSCRIBED_MESSAGE, "message": message}
    if message not in (SUBSCRIBED_MESSAGE, NOT_SUBSCRIBED_MESSAGE):
        result["unrecognizedResponse"] = True
    return result


@mcp.tool()
@tool_result
def get_api_key(api_key_id: str) -> Dict[str, Any]:
    """Read one API key's record, in the administrative (any user's keys) scope.

    Returns the key's metadata only — apiKeyName, the userId it acts as, expiry and enabled state.
    The key VALUE is shown once at creation and never again, and the stored hash is stripped by the
    handler, so nothing usable as a credential is returned here. Creating, updating and revoking API
    keys is deliberately not exposed by this server.

    There is no list tool for API keys, so `api_key_id` has to come from outside this session — run
    `vamscli api-key list`. A key that does not exist is reported as a 400 rather than a 404.
    """
    return CLIENT.api.get_api_key(api_key_id)


@mcp.tool()
@tool_result
def get_user_api_key(api_key_id: str) -> Dict[str, Any]:
    """Read one of the AUTHENTICATED user's own API key records.

    Same metadata-only response as get_api_key(), scoped to the caller's keys: a key owned by another
    user is reported as not found, so this scope never reveals that it exists. `api_key_id` comes
    from `vamscli api-key user list`.
    """
    return CLIENT.api.get_user_api_key(api_key_id)


# =========================================================================
# WRITE TOOLS (require VAMS_ENABLE_WRITES=true)
# =========================================================================

if CONFIG.enable_writes:

    @mcp.tool()
    @tool_result
    def create_database(database_id: str, description: str, default_bucket_id: str) -> Dict[str, Any]:
        """Create a new database. Use list_buckets() to find a default_bucket_id."""
        return CLIENT.api.create_database(
            {
                "databaseId": database_id,
                "description": description,
                "defaultBucketId": default_bucket_id,
            }
        )

    @mcp.tool()
    @tool_result
    def create_asset(
        database_id: str,
        asset_name: str,
        description: str,
        is_distributable: bool = False,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new asset in a database. The asset ID is generated by VAMS.

        description is required and must be at least 4 characters.
        is_distributable controls whether the asset may be downloaded/exported;
        defaults to False (non-distributable) as the safe default."""
        payload: Dict[str, Any] = {
            "databaseId": database_id,
            "assetName": asset_name,
            "description": description,
            "isDistributable": is_distributable,
            "tags": tags or [],
        }
        return CLIENT.api.create_asset(payload)

    @mcp.tool()
    @tool_result
    def update_asset(database_id: str, asset_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update fields on an existing asset. At least one of assetName,
        description, isDistributable, or tags must be supplied."""
        return CLIENT.api.update_asset(database_id, asset_id, updates)

    @mcp.tool()
    @tool_result
    def set_asset_metadata(
        database_id: str,
        asset_id: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create or update metadata key/value pairs on an asset (upsert; keys
        not listed are left untouched). Values are sent as strings."""
        items = [
            {"metadataKey": k, "metadataValue": str(v), "metadataValueType": "string"}
            for k, v in metadata.items()
        ]
        return CLIENT.api.update_asset_metadata_v2(database_id, asset_id, items, update_type="update")

    @mcp.tool()
    @tool_result
    def create_folder(database_id: str, asset_id: str, folder_path: str) -> Dict[str, Any]:
        """Create a folder within an asset's file tree. folder_path is relative
        to the asset root and must end with a slash."""
        relative_key = folder_path if folder_path.endswith("/") else f"{folder_path}/"
        return CLIENT.api.create_folder(database_id, asset_id, {"relativeKey": relative_key})

    @mcp.tool()
    @tool_result
    def create_asset_version(
        database_id: str,
        asset_id: str,
        comment: str,
        version_alias: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new version snapshot of an asset from its latest files.
        comment is required (1-256 characters)."""
        payload: Dict[str, Any] = {"useLatestFiles": True, "comment": comment}
        if version_alias:
            payload["versionAlias"] = version_alias
        return CLIENT.api.create_asset_version(database_id, asset_id, payload)


    # ---------------------------------------------------------------------
    # Pipeline / workflow / execution writes
    # ---------------------------------------------------------------------

    @mcp.tool()
    @tool_result
    def create_pipeline(database_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Create a pipeline. `body` is the full pipeline definition.

        Pass the same shape the API takes: pipelineId, pipelineName, executionConfig
        (executionType plus the block for that type), and systemConfig (inputFileArity,
        assetScope, requireTemplate, inputFileFilters, metadataInputs, ...). Copy an existing
        pipeline via get_pipeline() to see the expected shape before composing one.

        systemConfig.metadataInputs is a boolean map with exactly four keys — assetMetadata,
        fileMetadata, fileAttributes, databaseMetadata — each defaulting to true. Any other key is
        rejected. It gates which metadata a run captures, not whether a caller must supply it.

        A successful save can still carry a `warnings` array — e.g. a requireTemplate pipeline in an
        auto-triggered workflow with no default template chosen, whose trigger-launched runs will all
        fail. The pipeline saved; relay the warnings rather than reporting a clean success.
        """
        return _unwrap_message_with_warnings(CLIENT.api.create_pipeline(database_id, body))

    @mcp.tool()
    @tool_result
    def update_pipeline(database_id: str, pipeline_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Update a pipeline. Only the fields present in `body` change.

        Carries the same save `warnings` as create_pipeline. An executionConfig change adds a
        stale-deployment warning: referencing workflows keep invoking the PREVIOUS execution target
        until each one is re-saved, so the repoint is not live until then. Always relay it.
        """
        return _unwrap_message_with_warnings(CLIENT.api.update_pipeline(database_id, pipeline_id, body))

    @mcp.tool()
    @tool_result
    def create_pipeline_template(
        database_id: str, pipeline_id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a pipeline configuration template.

        `body` takes templateId, templateName, configFormat, configBody, and optionally
        inputInstructions (operator-facing guidance, max 4096 chars), tagSchema, and overrides.

        `overrides` narrows the pipeline's systemConfig for runs using this template, over the keys
        inputFileArity, assetScope, metadataInputs, and inputFileFilters. metadataInputs takes the
        same four-key boolean map as the pipeline: assetMetadata, fileMetadata, fileAttributes,
        databaseMetadata. The block is at most 65536 bytes serialized.

        A `tagSchema` entry carries only tagKey, type, required, default, label, description and
        enumValues. Any other key is rejected naming the offending index and key, so do not invent a
        spelling — a misspelled 'requried' or a capitalised 'Type' fails the call rather than storing
        a tag that is silently optional or untyped.
        """
        return CLIENT.unwrap_message(CLIENT.api.create_pipeline_template(database_id, pipeline_id, body))

    @mcp.tool()
    @tool_result
    def update_pipeline_template(
        database_id: str, pipeline_id: str, template_id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update a pipeline template. Only the fields present in `body` change.

        `overrides` and `tagSchema` carry the same rules as on create_pipeline_template: the
        overrides block is at most 65536 bytes serialized, and a tag entry's keys are limited to
        tagKey, type, required, default, label, description and enumValues.
        """
        return CLIENT.unwrap_message(CLIENT.api.update_pipeline_template(database_id, pipeline_id, template_id, body))

    @mcp.tool()
    @tool_result
    def set_pipeline_template_tag_schema(
        database_id: str, pipeline_id: str, template_id: str, fields: list
    ) -> Dict[str, Any]:
        """Replace a template's tag schema. This REPLACES the whole schema, not a merge.

        Each entry in `fields` carries only tagKey, type, required, default, label, description and
        enumValues. Any other key is rejected naming the offending index and key, rather than being
        ignored, so a misspelled 'requried' or a capitalised 'Type' fails the call instead of storing
        a tag that is silently optional or untyped.
        """
        return CLIENT.unwrap_message(
            CLIENT.api.set_pipeline_template_tag_schema(
                database_id, pipeline_id, template_id, fields
            )
        )

    @mcp.tool()
    @tool_result
    def create_workflow(database_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Create a workflow referencing one or more pipelines.

        `body` takes workflowId, workflowName, specifiedPipelines (ordered pipeline refs), and
        systemConfig (inputFileArity, assetScope, outputTarget, inputFileFilters, metadataInputs,
        ...). A workflow may not list the same pipeline twice — per-step config is keyed by pipeline
        id, so a repeat silently overwrites the earlier step.

        systemConfig.metadataInputs takes the same four-key boolean map as a pipeline's —
        assetMetadata, fileMetadata, fileAttributes, databaseMetadata — and the workflow's gate
        builds the one metadata envelope every step shares.
        """
        return CLIENT.unwrap_message(CLIENT.api.create_workflow(database_id, body))

    @mcp.tool()
    @tool_result
    def update_workflow(database_id: str, workflow_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Update a workflow. Only the fields present in `body` change."""
        return CLIENT.unwrap_message(CLIENT.api.update_workflow(database_id, workflow_id, body))

    @mcp.tool()
    @tool_result
    def set_workflow_trigger(
        database_id: str, workflow_id: str, trigger_type: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create or replace a workflow trigger (PUT-idempotent).

        `trigger_type` is the trigger KEY: the bare type addresses a workflow's first trigger of that
        type, and 'type#triggerId' addresses an additional one — a key with a NEW id creates another
        trigger of that type, so one workflow can respond differently to different uploads. An upload
        launches the workflow once per matching trigger.

        `body` takes enabled plus the trigger's own settings, e.g. inputFileFilters. A
        match-everything exclude pattern is rejected: exclude is applied last and would remove every
        file, leaving the trigger permanently unable to fire.

        Two conditions are rejected with 400: an additional trigger of a type when the workflow's
        concurrencyRestriction is perAsset (several would contend on the same asset), and an additional
        trigger naming the same defaultTemplateIds as an existing one — including two that both name
        none, which is a valid choice and therefore a comparable value.
        """
        return CLIENT.unwrap_message(CLIENT.api.set_workflow_trigger(database_id, workflow_id, trigger_type, body))

    @mcp.tool()
    @tool_result
    def execute_workflow(
        workflow_database_id: str, workflow_id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a workflow on a set of input files. Returns the new executionId.

        `body` takes inputFiles (each an independent databaseId / assetId / relativeFileKey and
        optional versionId), optional outputAssetId + outputDatabaseId (REQUIRED when the inputs
        span more than one asset, since the output cannot then be inferred), an optional
        outputFileBaseExecutionPathExtension prefix, and per-pipeline parameters keyed by
        pipelineId. A relativeFileKey of "/" selects the whole asset where the workflow allows it.

        Two optional fields name entities the run reads stored METADATA from. They are not input
        files — a metadata source carries no file key and takes no part in arity, input filters, or
        output-target resolution — and naming them is always optional: a pipeline that genuinely
        requires metadata validates and fails on its own, so omitting them is never an error here.

        - metadataSourceAssets: [{"databaseId", "assetId"}]. No file key field exists.
        - metadataSourceDatabaseId: ONE concrete database id, and only meaningful for a run with NO
          input files. With input files the databases are DERIVED from the input files' assets (plus
          any metadataSourceAssets' assets), so this field is ignored. "GLOBAL" is REJECTED — it is
          the unscoped keyword, not a database whose metadata can be read. Database metadata is
          read-only; a database is never an output target.

        Captured metadata is capped per entity, so the response's `warnings` array can report a
        truncated capture or a source database that could not be read. The run still succeeded —
        relay those warnings rather than dropping them, because the inputs are then not what was
        named.

        This starts real compute. Confirm with the user before calling it.
        """
        return CLIENT.unwrap_message(CLIENT.api.execute_workflow(workflow_database_id, workflow_id, body))

    @mcp.tool()
    @tool_result
    def rerun_execution(
        execution_id: str, execution_group_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Re-run ONE execution from its stored inputs, creating a NEW execution.

        Re-launches with the CALLER's permissions, not the original runner's. Returns the new
        execution's id — the id passed in still refers to the original run.

        execution_group_id ASSIGNS the new execution's group membership; it does not select what to
        re-run. Exactly one execution is launched either way. Pass the original run's group id to
        keep the re-run alongside its siblings, a new one to separate it, or omit it. There is no
        re-run-the-whole-group operation: to re-run several, call this once per execution id (find
        them with list_executions()).

        The re-run goes through the execute path, so the response carries the same `warnings` array
        (e.g. a metadata capture bounded by the per-entity cap). Relay them: the run started, but its
        inputs may not match the original.
        """
        return CLIENT.unwrap_message(CLIENT.api.rerun_execution(execution_id, execution_group_id=execution_group_id))

    @mcp.tool()
    @tool_result
    def abort_execution(execution_id: str, group_id: Optional[str] = None) -> Dict[str, Any]:
        """Abort a running execution, terminating its state machine and any AWS Batch job.

        Aborting is NOT reversible: the run stops where it is and partial outputs may already have
        been written. It is write-tier rather than destructive because it removes no stored data, but
        it is the one write tool that irreversibly STOPS running AWS compute, and with group_id it
        fans out across every active execution in that group — so keep it out of `autoApprove`
        alongside execute_workflow and rerun_execution. Confirm a group abort with the user first.

        Pass group_id to abort every active execution in that group.
        """
        return CLIENT.unwrap_message(CLIENT.api.abort_execution(execution_id, group_id=group_id))

    # ---------------------------------------------------------------------
    # Comment / subscription / metadata-schema writes
    # ---------------------------------------------------------------------

    @mcp.tool()
    @tool_result
    def add_comment(
        asset_id: str,
        asset_version_id: str,
        comment_body: str,
        comment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a comment to one version of an asset. Returns the `commentId` written.

        `comment_id` is the caller's to choose and the write is UNCONDITIONAL, so passing the id of
        an existing comment REPLACES it — its body, owner and creation date — with no error and no
        indication that anything was overwritten. Leave it unset unless that is the intent: a uuid4
        is generated, and returned here because the endpoint's acknowledgement does not contain it.

        `comment_body` is free text up to 16384 characters. Use list_asset_versions() to find an
        asset_version_id; neither id may contain a colon.
        """
        resolved_id = comment_id or str(uuid.uuid4())
        response = CLIENT.api.add_comment(asset_id, asset_version_id, resolved_id, comment_body)
        result: Dict[str, Any] = {"commentId": resolved_id}
        if isinstance(response, dict):
            result.update(response)
        else:
            result["response"] = response
        return result

    @mcp.tool()
    @tool_result
    def update_comment(
        asset_id: str, asset_version_id: str, comment_id: str, comment_body: str
    ) -> Dict[str, Any]:
        """Replace the text of an existing comment. Only the body changes.

        The comment's CREATOR is the only user who may edit it; anyone else gets a 403 regardless of
        their VAMS role, so this fails for an agent acting as a different user than the one who
        commented. A comment id that does not exist is reported as not found rather than created —
        add_comment() is the tool that writes a new one.
        """
        return CLIENT.api.update_comment(asset_id, asset_version_id, comment_id, comment_body)

    @mcp.tool()
    @tool_result
    def create_subscription(
        entity_id: str,
        subscribers: List[str],
        event_name: str = SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
        entity_name: str = SUBSCRIPTION_ENTITY_ASSET,
    ) -> Dict[str, Any]:
        """Subscribe users to an entity's events, sending them e-mail on each occurrence.

        Defaults subscribe to asset version changes, where `entity_id` is the assetId. `subscribers`
        are VAMS user IDs, each resolved to the e-mail address on the user's profile (falling back to
        the user ID when that is itself an address) — a user with no usable address fails the call.

        A user already subscribed to this entity is an ERROR, not a no-op: the whole call is rejected,
        including the subscribers that would have been added. Call check_subscription() first, or use
        update_subscription() to state the full list you want.
        """
        return CLIENT.api.create_subscription(event_name, entity_name, entity_id, subscribers)

    @mcp.tool()
    @tool_result
    def update_subscription(
        entity_id: str,
        subscribers: List[str],
        event_name: str = SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
        entity_name: str = SUBSCRIPTION_ENTITY_ASSET,
    ) -> Dict[str, Any]:
        """REPLACE a subscription's subscriber list with the one given.

        This is not an addition. Every user absent from `subscribers` is unsubscribed from the
        underlying notification topic, so passing one user removes all the others. To add someone,
        read the current list with list_subscriptions() and send it back with the addition included —
        this tool deliberately does not do that read for you, because a stale list silently
        unsubscribes whoever joined in between.

        The subscription must already exist; there is no upsert. Use create_subscription() first.
        """
        return CLIENT.api.update_subscription(event_name, entity_name, entity_id, subscribers)

    @mcp.tool()
    @tool_result
    def create_metadata_schema(schema_data: Dict[str, Any]) -> Dict[str, Any]:
        """Define a metadata schema, which constrains and describes metadata fields on an entity.

        `schema_data` takes databaseId (or the literal 'GLOBAL' for every database),
        metadataSchemaEntityType (databaseMetadata, assetMetadata, fileMetadata, fileAttribute or
        assetLinkMetadata), schemaName, and fields — which is nested: `{"fields": [ ... ]}`, not a
        bare list. Optional: fileKeyTypeRestriction (a comma-delimited extension list, accepted only
        for fileMetadata and fileAttribute), and enabled (defaults true).

        Call list_metadata_schemas() first and copy the shape of an existing schema. The response
        carries the generated metadataSchemaId, which is what update_metadata_schema() takes.
        """
        return CLIENT.api.create_metadata_schema(schema_data)

    @mcp.tool()
    @tool_result
    def update_metadata_schema(
        metadata_schema_id: str, update_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update a metadata schema. Only the fields present in `update_data` change.

        Changeable: schemaName, fields (the same nested `{"fields": [ ... ]}` shape as on create),
        fileKeyTypeRestriction, and enabled. The schema's databaseId and entity type are fixed at
        creation. `fields` REPLACES the whole field list rather than merging into it, so send the
        complete set — read it with list_metadata_schemas() first.
        """
        return CLIENT.api.update_metadata_schema(metadata_schema_id, update_data)


# =========================================================================
# DESTRUCTIVE TOOLS (require VAMS_ENABLE_DESTRUCTIVE=true AND writes enabled)
# =========================================================================

if CONFIG.enable_destructive:

    @mcp.tool()
    @tool_result
    def archive_asset(database_id: str, asset_id: str, reason: str = "") -> Dict[str, Any]:
        """Archive (soft-delete) an asset. Reversible via unarchive_asset."""
        return CLIENT.api.archive_asset(database_id, asset_id, reason=reason or None)

    @mcp.tool()
    @tool_result
    def unarchive_asset(
        database_id: str,
        asset_id: str,
        reason: str = "",
        unarchive_files: bool = False,
    ) -> Dict[str, Any]:
        """Restore a previously archived asset. Files stay archived unless
        unarchive_files is set, which restores the files archived by the asset
        archive (files archived individually beforehand always stay archived)."""
        return CLIENT.api.unarchive_asset(
            database_id, asset_id, reason=reason or None, unarchive_files=unarchive_files
        )

    @mcp.tool()
    @tool_result
    def delete_asset(database_id: str, asset_id: str, reason: str = "") -> Dict[str, Any]:
        """PERMANENTLY delete an asset. Irreversible.

        Consider archive_asset() first: it is reversible via unarchive_asset().
        """
        # `confirmPermanentDelete` is a REQUIRED-TRUE field of the request contract, not an optional
        # second signal: DeleteAssetRequestModel declares an always=True validator that rejects any
        # other value, so sending true is the only way to perform the operation at all. The controls
        # on this tool are the destructive gate, its name, and this docstring.
        return CLIENT.api.delete_asset_permanent(database_id, asset_id, reason=reason or None, confirm=True)

    @mcp.tool()
    @tool_result
    def delete_database(database_id: str) -> Dict[str, Any]:
        """PERMANENTLY delete a database. Irreversible, and rejected while the
        database still holds active assets, workflows, or pipelines."""
        return CLIENT.api.delete_database(database_id)

    # ---------------------------------------------------------------------
    # Pipeline / workflow / execution deletes
    # ---------------------------------------------------------------------

    @mcp.tool()
    @tool_result
    def archive_pipeline(database_id: str, pipeline_id: str) -> Dict[str, Any]:
        """Archive (soft-delete) a pipeline. Reversible via unarchive_pipeline."""
        return CLIENT.unwrap_message(CLIENT.api.delete_pipeline(database_id, pipeline_id))

    @mcp.tool()
    @tool_result
    def unarchive_pipeline(
        database_id: str, pipeline_id: str, keep_disabled: bool = False
    ) -> Dict[str, Any]:
        """Restore an archived pipeline, re-enabling it so it is executable again.

        Archiving also DISABLES, so clearing the archived flag alone leaves a pipeline that
        is listed but silently unrunnable. Pass keep_disabled to restore it still disabled.
        """
        body: Dict[str, Any] = {"archived": False}
        if not keep_disabled:
            body["enabled"] = True
        return _unwrap_message_with_warnings(CLIENT.api.update_pipeline(database_id, pipeline_id, body))

    @mcp.tool()
    @tool_result
    def archive_workflow(database_id: str, workflow_id: str) -> Dict[str, Any]:
        """Archive (soft-delete) a workflow. Reversible via unarchive_workflow."""
        return CLIENT.unwrap_message(CLIENT.api.delete_workflow(database_id, workflow_id))

    @mcp.tool()
    @tool_result
    def unarchive_workflow(
        database_id: str, workflow_id: str, keep_disabled: bool = False
    ) -> Dict[str, Any]:
        """Restore an archived workflow, re-enabling it so it is executable again.

        Archiving also DISABLES, so clearing the archived flag alone leaves a workflow that
        is listed but silently unrunnable. Pass keep_disabled to restore it still disabled.
        """
        body: Dict[str, Any] = {"archived": False}
        if not keep_disabled:
            body["enabled"] = True
        return CLIENT.unwrap_message(CLIENT.api.update_workflow(database_id, workflow_id, body))

    @mcp.tool()
    @tool_result
    def delete_pipeline_template(
        database_id: str, pipeline_id: str, template_id: str
    ) -> Dict[str, Any]:
        """Delete a pipeline template. Not reversible.

        A pipeline with requireTemplate becomes unrunnable if its only template is removed.

        The response carries a `warnings` array when a file-upload trigger still names the deleted
        template as a default for this pipeline. The delete happened; triggered executions of the
        named workflows fail until each trigger picks a different default template, so relay the
        warnings rather than reporting a clean delete.
        """
        return CLIENT.unwrap_message(CLIENT.api.delete_pipeline_template(database_id, pipeline_id, template_id))

    @mcp.tool()
    @tool_result
    def delete_workflow_trigger(
        database_id: str, workflow_id: str, trigger_type: str
    ) -> Dict[str, Any]:
        """Delete a workflow trigger, so the workflow stops firing automatically."""
        return CLIENT.unwrap_message(CLIENT.api.delete_workflow_trigger(database_id, workflow_id, trigger_type))

    @mcp.tool()
    @tool_result
    def permanent_delete_execution(execution_id: str) -> Dict[str, Any]:
        """Permanently delete an execution's records. IRREVERSIBLE.

        Administrative route, and blocked while the execution is still in progress — abort it first.
        Removes the run's traceability; the output files it wrote to assets are left in place.
        """
        return CLIENT.unwrap_message(CLIENT.api.permanent_delete_execution(execution_id))

    # ---------------------------------------------------------------------
    # Comment / subscription / metadata-schema deletes
    # ---------------------------------------------------------------------

    @mcp.tool()
    @tool_result
    def delete_comment(asset_id: str, asset_version_id: str, comment_id: str) -> Dict[str, Any]:
        """Delete a comment. A soft delete — the record moves to a deleted partition.

        It cannot be read back through this server either way: the listing tools do not return
        deleted comments and the route's showDeleted flag is ignored by the service, so treat this as
        unrecoverable from an agent's position.

        Only the comment's CREATOR may delete it; anyone else gets a 403 whatever their VAMS role.
        """
        return CLIENT.api.delete_comment(asset_id, asset_version_id, comment_id)

    @mcp.tool()
    @tool_result
    def delete_subscription(
        entity_id: str,
        subscribers: List[str],
        event_name: str = SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
        entity_name: str = SUBSCRIPTION_ENTITY_ASSET,
    ) -> Dict[str, Any]:
        """Delete a WHOLE subscription — every subscriber, not the ones listed.

        For an asset this also deletes the asset's notification topic, so every user on the record is
        unsubscribed and the subscription no longer exists. `subscribers` is required by the endpoint,
        which validates it as a user-ID list and then IGNORES it: passing one name does not scope the
        delete to that name. To remove one user and leave the subscription standing, use
        unsubscribe(); to change the membership, use update_subscription().
        """
        return CLIENT.api.delete_subscription(event_name, entity_name, entity_id, subscribers)

    @mcp.tool()
    @tool_result
    def unsubscribe(
        entity_id: str,
        subscriber: str,
        event_name: str = SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
        entity_name: str = SUBSCRIPTION_ENTITY_ASSET,
    ) -> Dict[str, Any]:
        """Remove ONE subscriber from a subscription, leaving the record and the others in place.

        A different route from delete_subscription(), which removes the entire record. Takes a single
        user rather than a list because the endpoint removes only the first entry it is sent while
        unsubscribing every entry from the notification topic — so a list would leave the two out of
        step. A user who is not subscribed is reported as not found rather than ignored.
        """
        return CLIENT.api.unsubscribe(event_name, entity_name, entity_id, subscriber)

    @mcp.tool()
    @tool_result
    def delete_metadata_schema(database_id: str, metadata_schema_id: str) -> Dict[str, Any]:
        """PERMANENTLY delete a metadata schema. Irreversible, and there is no archived state.

        The schema's constraints stop being applied to the entity type it covered; metadata already
        stored against it is left in place, unvalidated. Read it with list_metadata_schemas() first —
        the definition cannot be recovered afterwards, only re-authored.
        """
        # `confirmDelete` is a REQUIRED-TRUE field of the request contract, not an optional interlock:
        # DeleteMetadataSchemaRequestModel declares an always=True validator that rejects any other
        # value, so the APIClient always sends true and there is nothing to surface as a parameter.
        # The controls on this tool are the destructive gate, its name, and this docstring.
        return CLIENT.api.delete_metadata_schema(database_id, metadata_schema_id)


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    if CLIENT is None:
        raise SystemExit(f"[vams-mcp] {STARTUP_ERROR}")
    mcp.run()


if __name__ == "__main__":
    main()
