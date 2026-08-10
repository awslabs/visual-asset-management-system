"""VAMS MCP server.

Exposes the Visual Asset Management System REST API as MCP tools. Read/search
tools are always available. Write tools require VAMS_ENABLE_WRITES=true and
destructive tools additionally require VAMS_ENABLE_DESTRUCTIVE=true.
"""

from __future__ import annotations

import functools
import logging
import sys
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
from .client import VamsClient, API_ASSETS, API_DATABASE_ASSETS  # noqa: E402
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
# Its collections. 'input' is the asset/file rows, 'inputDatabase' the database-scope rows, 'output'
# the per-pipeline output metadata; anything else is a 400.
EXECUTION_DETAIL_METADATA_COLLECTIONS = ("input", "inputDatabase", "output")


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
def list_databases(include_deleted: bool = False, max_items: Optional[int] = None) -> Dict[str, Any]:
    """List VAMS databases (auto-paginated). Returns database IDs, descriptions,
    asset counts, and bucket info."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_databases(show_deleted=include_deleted, params=params),
        max_items=max_items,
    )


@mcp.tool()
@tool_result
def get_database(database_id: str, include_deleted: bool = False) -> Dict[str, Any]:
    """Get details for a single database by ID."""
    return CLIENT.api.get_database(database_id, include_deleted)


@mcp.tool()
@tool_result
def list_buckets(max_items: Optional[int] = None) -> Dict[str, Any]:
    """List asset storage buckets available for creating databases."""
    return CLIENT.paginate(lambda params: CLIENT.api.list_buckets(params=params), max_items=max_items)


@mcp.tool()
@tool_result
def list_assets(
    database_id: Optional[str] = None,
    include_archived: bool = False,
    max_items: Optional[int] = None,
) -> Dict[str, Any]:
    """List assets, scoped to a database when database_id is given, otherwise
    across all databases the user can read (auto-paginated)."""
    endpoint = API_DATABASE_ASSETS.format(databaseId=database_id) if database_id else API_ASSETS

    def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
        if include_archived:
            params = {**params, "showArchived": "true"}
        return CLIENT.get_json(endpoint, params=params)

    return CLIENT.paginate(fetch, max_items=max_items)


@mcp.tool()
@tool_result
def get_asset(database_id: str, asset_id: str, include_archived: bool = False) -> Dict[str, Any]:
    """Get details for a single asset."""
    return CLIENT.api.get_asset(database_id, asset_id, show_archived=include_archived)


@mcp.tool()
@tool_result
def list_asset_files(database_id: str, asset_id: str, max_items: Optional[int] = None) -> Dict[str, Any]:
    """List files belonging to an asset (auto-paginated)."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_asset_files(database_id, asset_id, params=params),
        max_items=max_items,
        items_key="items",
    )


@mcp.tool()
@tool_result
def get_asset_metadata(database_id: str, asset_id: str) -> Dict[str, Any]:
    """Get all metadata key/value pairs for an asset."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_asset_metadata_v2(
            database_id, asset_id, page_size=params["pageSize"], starting_token=params.get("startingToken")
        ),
        items_key="metadata",
    )


@mcp.tool()
@tool_result
def get_database_metadata(database_id: str) -> Dict[str, Any]:
    """Get metadata key/value pairs for a database."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_database_metadata_v2(
            database_id, page_size=params["pageSize"], starting_token=params.get("startingToken")
        ),
        items_key="metadata",
    )


@mcp.tool()
@tool_result
def list_asset_versions(database_id: str, asset_id: str, max_items: Optional[int] = None) -> Dict[str, Any]:
    """List versions of an asset (auto-paginated)."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_asset_versions(database_id, asset_id, params=params),
        max_items=max_items,
        items_key="versions",
    )


@mcp.tool()
@tool_result
def get_asset_version(database_id: str, asset_id: str, asset_version_id: str) -> Dict[str, Any]:
    """Get a specific asset version by its version ID."""
    return CLIENT.api.get_asset_version(database_id, asset_id, asset_version_id)


@mcp.tool()
@tool_result
def get_asset_history(database_id: str, asset_id: str, max_items: Optional[int] = None) -> Dict[str, Any]:
    """Get the asset lifecycle history (create/edit/archive/unarchive/delete
    records, newest first, auto-paginated)."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_asset_history(database_id, asset_id, params=params),
        max_items=max_items,
    )


@mcp.tool()
@tool_result
def get_asset_links(database_id: str, asset_id: str, child_tree_view: bool = False) -> Dict[str, Any]:
    """List relationship links for an asset (related/parent/child assets)."""
    return CLIENT.api.get_asset_links_for_asset(database_id, asset_id, child_tree_view=child_tree_view)


def _build_search_request(
    entity_types: List[str],
    query: Optional[str],
    database_id: Optional[str],
    metadata_query: Optional[str],
    size: int,
    include_archived: bool,
    geo_search: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    request: Dict[str, Any] = {
        "entityTypes": entity_types,
        "from": 0,
        "size": max(1, min(size, 2000)),
        "includeArchived": include_archived,
        "explainResults": False,
        "includeMetadataInSearch": True,
        "sort": ["_score"],
    }
    if query:
        request["query"] = query
    if metadata_query:
        request["metadataQuery"] = metadata_query
        request["metadataSearchMode"] = "both"
    if database_id:
        request["filters"] = [{"query_string": {"query": f'str_databaseid:"{database_id}"'}}]
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
) -> Dict[str, Any]:
    """Full-text / metadata / geospatial search across assets (OpenSearch).

    - query: free text (matches names, descriptions, metadata)
    - database_id: restrict to one database
    - metadata_query: metadata field search. Metadata is indexed with an `MD_`
      prefix plus a type prefix, e.g. 'MD_str_product:Training'. Call
      get_search_fields() to see the indexed field names.
    - geo_search: geospatial filter on `geo_MD_location`. Supply exactly one of
      `point` ({lat, lon, radiusMeters}), `bbox` ({topLeft, bottomRight} of
      points), or `geoJson` (GeoJSON geometry/Feature/FeatureCollection), plus
      an optional `relation` of intersects (default) / within / contains /
      disjoint.
    Returns a compact list of hits (id, score, source fields)."""
    request = _build_search_request(
        ["asset"], query, database_id, metadata_query, size, include_archived, geo_search
    )
    raw = CLIENT.api.search_query(request)
    return CLIENT.trim_search_results(raw, max_hits=size)


@mcp.tool()
@tool_result
def search_files(
    query: Optional[str] = None,
    database_id: Optional[str] = None,
    metadata_query: Optional[str] = None,
    size: int = 25,
    include_archived: bool = False,
    geo_search: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Full-text / metadata / geospatial search across asset files (OpenSearch).
    Takes the same metadata_query and geo_search shapes as search_assets."""
    request = _build_search_request(
        ["file"], query, database_id, metadata_query, size, include_archived, geo_search
    )
    raw = CLIENT.api.search_query(request)
    return CLIENT.trim_search_results(raw, max_hits=size)


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
) -> Dict[str, Any]:
    """List workflows, optionally scoped to a database (auto-paginated).

    Archived workflows are filtered out server-side unless include_archived is set, which is how an
    archived workflow's id is found in order to restore it.
    """
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_workflows(
            database_id=database_id, include_archived=include_archived, params=params
        ),
        max_items=max_items,
    )


@mcp.tool()
@tool_result
def list_workflow_executions(
    database_id: str,
    asset_id: str,
    workflow_id: Optional[str] = None,
    workflow_database_id: Optional[str] = None,
    max_items: Optional[int] = None,
) -> Dict[str, Any]:
    """List workflow executions for an asset (auto-paginated).

    Optionally narrow to one workflow. A workflow id is unique only within its database, so pass
    workflow_database_id as well when the same id exists in more than one; either filter also works
    alone. Use "GLOBAL" as workflow_database_id for the shared workflow catalog.
    """
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_workflow_executions(
            database_id,
            asset_id,
            workflow_database_id=workflow_database_id,
            workflow_id=workflow_id,
            params=params,
        ),
        max_items=max_items,
        # The executions endpoint caps pageSize at 50 to avoid Step Functions throttling.
        page_size=min(CONFIG.page_size, WORKFLOW_EXECUTIONS_MAX_PAGE_SIZE),
    )


@mcp.tool()
@tool_result
def list_tags(max_items: Optional[int] = None) -> Dict[str, Any]:
    """List all tags."""
    return CLIENT.paginate(lambda params: CLIENT.api.get_tags(params=params), max_items=max_items)


@mcp.tool()
@tool_result
def list_tag_types(max_items: Optional[int] = None) -> Dict[str, Any]:
    """List all tag types."""
    return CLIENT.paginate(lambda params: CLIENT.api.get_tag_types(params=params), max_items=max_items)


@mcp.tool()
@tool_result
def list_metadata_schemas(
    database_id: Optional[str] = None,
    metadata_entity_type: Optional[str] = None,
) -> Dict[str, Any]:
    """List metadata schemas, optionally filtered by database and entity type
    (databaseMetadata, assetMetadata, fileMetadata, fileAttribute,
    assetLinkMetadata)."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_metadata_schemas(
            database_id=database_id,
            metadata_entity_type=metadata_entity_type,
            page_size=params["pageSize"],
            starting_token=params.get("startingToken"),
        ),
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
    Non-mutating: creates a URL, does not transfer data through this server."""
    return CLIENT.api.download_asset_file(
        database_id, asset_id, file_key=file_key, version_id=version_id
    )


@mcp.tool()
@tool_result
def find_and_summarize(query: str, database_id: Optional[str] = None, size: int = 10) -> Dict[str, Any]:
    """Composite: search assets, then enrich each hit with details and version
    count in a single call. Best for 'find X and tell me about them' requests."""
    request = _build_search_request(["asset"], query, database_id, None, size, False)
    trimmed = CLIENT.trim_search_results(CLIENT.api.search_query(request), max_hits=size)

    enriched: List[Dict[str, Any]] = []
    for hit in trimmed.get("results", []):
        source = hit.get("source", {}) or {}
        db = source.get("str_databaseid") or database_id
        aid = source.get("str_assetid") or hit.get("id")
        entry: Dict[str, Any] = {"asset_id": aid, "database_id": db, "score": hit.get("score"), "source": source}
        if db and aid:
            try:
                versions = CLIENT.paginate(
                    lambda params: CLIENT.api.get_asset_versions(db, aid, params=params),
                    items_key="versions",
                )
                entry["version_count"] = versions.get("count")
                if versions.get("truncated"):
                    entry["version_count_truncated"] = True
            except Exception as exc:  # noqa: BLE001
                entry["version_lookup_error"] = str(exc)
        enriched.append(entry)

    return {"total": trimmed.get("total"), "returned": len(enriched), "assets": enriched}


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
) -> Dict[str, Any]:
    """List processing pipelines, optionally scoped to a database (auto-paginated).

    Omit database_id to list every pipeline the user can see, including the shared GLOBAL catalog.
    """
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_pipelines(
            database_id=database_id, include_archived=include_archived, params=params
        ),
        max_items=max_items,
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
def list_workflow_triggers(database_id: str, workflow_id: str) -> Dict[str, Any]:
    """List a workflow's triggers (e.g. fileUpload) and whether each is enabled.

    A workflow may carry several triggers of one type, each with its own filters and default templates.
    Each item's `triggerType` is its KEY — the bare type for the first trigger of a type, or
    'type#triggerId' for an additional one — and is what the get/set/delete tools take.
    """
    return CLIENT.unwrap_message(CLIENT.api.list_workflow_triggers(database_id, workflow_id))


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
    max_items: Optional[int] = None,
) -> Dict[str, Any]:
    """List workflow executions across every workflow and database the user can see.

    Distinct from list_workflow_executions(), which is scoped to ONE asset's history. Optional
    filters narrow by status (e.g. RUNNING, SUCCEEDED, FAILED, ABORTED) and by workflow.

    The listing is lower-bounded by start date: `filterStartDate` reports the applied window (90 days
    back by default), so executions older than it are absent by design, not missing.

    A `warnings` entry means the walk WITHHELD rows: a page can reach a cap on the distinct assets it
    resolves for permission checks and skip the executions it could not evaluate. The result is then
    also flagged `truncated`. Do not report a count or conclude an execution does not exist from a
    result carrying warnings — narrow the filters and list again.
    """
    extra = {
        key: value
        for key, value in (
            ("status", status),
            ("workflowId", workflow_id),
            ("workflowDatabaseId", workflow_database_id),
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
    reached before the walk finished, not that the collection ends there.
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
        databaseMetadata.
        """
        return CLIENT.unwrap_message(CLIENT.api.create_pipeline_template(database_id, pipeline_id, body))

    @mcp.tool()
    @tool_result
    def update_pipeline_template(
        database_id: str, pipeline_id: str, template_id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update a pipeline template. Only the fields present in `body` change."""
        return CLIENT.unwrap_message(CLIENT.api.update_pipeline_template(database_id, pipeline_id, template_id, body))

    @mcp.tool()
    @tool_result
    def set_pipeline_template_tag_schema(
        database_id: str, pipeline_id: str, template_id: str, fields: list
    ) -> Dict[str, Any]:
        """Replace a template's tag schema. This REPLACES the whole schema, not a merge."""
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

        Pass group_id to abort every active execution in that group. Aborting is not reversible: the
        run stops where it is and partial outputs may already have been written.
        """
        return CLIENT.unwrap_message(CLIENT.api.abort_execution(execution_id, group_id=group_id))


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
        """PERMANENTLY delete an asset. Irreversible."""
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


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    if CLIENT is None:
        raise SystemExit(f"[vams-mcp] {STARTUP_ERROR}")
    mcp.run()


if __name__ == "__main__":
    main()
