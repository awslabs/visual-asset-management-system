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
from .client import VamsClient  # noqa: E402
from .config import Config, ConfigError  # noqa: E402

_force_logging_to_stderr()

from mcp.server.fastmcp import FastMCP  # noqa: E402

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

mcp = FastMCP("vams")


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


# =========================================================================
# READ / SEARCH TOOLS (always available)
# =========================================================================


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
def list_buckets() -> Dict[str, Any]:
    """List asset storage buckets available for creating databases."""
    return CLIENT.api.list_buckets()


@mcp.tool()
@tool_result
def list_assets(database_id: str, include_archived: bool = False, max_items: Optional[int] = None) -> Dict[str, Any]:
    """List assets within a database (auto-paginated)."""
    endpoint = f"/database/{database_id}/assets"

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
    )


@mcp.tool()
@tool_result
def get_asset_metadata(database_id: str, asset_id: str) -> Dict[str, Any]:
    """Get all metadata key/value pairs for an asset."""
    return CLIENT.api.get_asset_metadata_v2(database_id, asset_id)


@mcp.tool()
@tool_result
def get_database_metadata(database_id: str) -> Dict[str, Any]:
    """Get metadata key/value pairs for a database."""
    return CLIENT.api.get_database_metadata_v2(database_id)


@mcp.tool()
@tool_result
def list_asset_versions(database_id: str, asset_id: str, max_items: Optional[int] = None) -> Dict[str, Any]:
    """List versions of an asset (auto-paginated)."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_asset_versions(database_id, asset_id, params=params),
        max_items=max_items,
    )


@mcp.tool()
@tool_result
def get_asset_version(database_id: str, asset_id: str, asset_version_id: str) -> Dict[str, Any]:
    """Get a specific asset version by its version ID."""
    return CLIENT.api.get_asset_version(database_id, asset_id, asset_version_id)


@mcp.tool()
@tool_result
def get_asset_history(database_id: str, asset_id: str, max_items: Optional[int] = None) -> Dict[str, Any]:
    """Get the change history for an asset (auto-paginated)."""
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
    return request


@mcp.tool()
@tool_result
def search_assets(
    query: Optional[str] = None,
    database_id: Optional[str] = None,
    metadata_query: Optional[str] = None,
    size: int = 25,
    include_archived: bool = False,
) -> Dict[str, Any]:
    """Full-text / metadata search across assets (OpenSearch).

    - query: free text (matches names, descriptions, metadata)
    - database_id: restrict to one database
    - metadata_query: metadata field search, e.g. 'MD_str_product:Training'
    Returns a compact list of hits (id, score, source fields)."""
    request = _build_search_request(["asset"], query, database_id, metadata_query, size, include_archived)
    raw = CLIENT.api.search_query(request)
    return CLIENT.trim_search_results(raw, max_hits=size)


@mcp.tool()
@tool_result
def search_files(
    query: Optional[str] = None,
    database_id: Optional[str] = None,
    size: int = 25,
    include_archived: bool = False,
) -> Dict[str, Any]:
    """Full-text search across asset files (OpenSearch)."""
    request = _build_search_request(["file"], query, database_id, None, size, include_archived)
    raw = CLIENT.api.search_query(request)
    return CLIENT.trim_search_results(raw, max_hits=size)


@mcp.tool()
@tool_result
def get_search_fields() -> Dict[str, Any]:
    """List available search fields/types (the OpenSearch index mapping)."""
    return CLIENT.api.get_search_mapping()


@mcp.tool()
@tool_result
def list_workflows(database_id: Optional[str] = None, max_items: Optional[int] = None) -> Dict[str, Any]:
    """List workflows, optionally scoped to a database (auto-paginated)."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_workflows(database_id=database_id, params=params),
        max_items=max_items,
    )


@mcp.tool()
@tool_result
def list_workflow_executions(database_id: str, asset_id: str, max_items: Optional[int] = None) -> Dict[str, Any]:
    """List workflow executions for an asset (auto-paginated)."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_workflow_executions(database_id, asset_id, params=params),
        max_items=max_items,
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
def list_metadata_schemas(database_id: Optional[str] = None) -> Dict[str, Any]:
    """List metadata schemas, optionally filtered by database."""
    return CLIENT.api.list_metadata_schemas(database_id=database_id)


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
        aid = source.get("str_assetid") or source.get("str_assetId") or hit.get("id")
        entry: Dict[str, Any] = {"asset_id": aid, "database_id": db, "score": hit.get("score"), "source": source}
        if db and aid:
            try:
                versions = CLIENT.api.get_asset_versions(db, aid, params={"pageSize": 1})
                entry["version_count"] = versions.get("count") or len(versions.get("Items", []))
            except Exception as exc:  # noqa: BLE001
                entry["version_lookup_error"] = str(exc)
        enriched.append(entry)

    return {"total": trimmed.get("total"), "returned": len(enriched), "assets": enriched}


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
        description: str = "",
        is_distributable: bool = False,
        asset_type: str = "",
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new asset in a database.

        is_distributable controls whether the asset may be downloaded/exported;
        defaults to False (non-distributable) as the safe default."""
        payload: Dict[str, Any] = {
            "databaseId": database_id,
            "assetName": asset_name,
            "description": description,
            "isDistributable": is_distributable,
        }
        if asset_type:
            payload["assetType"] = asset_type
        if tags:
            payload["tags"] = tags
        return CLIENT.api.create_asset(payload)

    @mcp.tool()
    @tool_result
    def update_asset(database_id: str, asset_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update fields on an existing asset (e.g. description, tags)."""
        return CLIENT.api.update_asset(database_id, asset_id, updates)

    @mcp.tool()
    @tool_result
    def set_asset_metadata(
        database_id: str,
        asset_id: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create or update metadata key/value pairs on an asset."""
        items = [{"metadataKey": k, "metadataValue": v} for k, v in metadata.items()]
        return CLIENT.api.update_asset_metadata_v2(database_id, asset_id, items, update_type="update")

    @mcp.tool()
    @tool_result
    def create_folder(database_id: str, asset_id: str, folder_path: str) -> Dict[str, Any]:
        """Create a folder within an asset's file tree."""
        return CLIENT.api.create_folder(database_id, asset_id, {"folderPath": folder_path})

    @mcp.tool()
    @tool_result
    def create_asset_version(database_id: str, asset_id: str, comment: str = "") -> Dict[str, Any]:
        """Create a new version snapshot of an asset."""
        return CLIENT.api.create_asset_version(database_id, asset_id, {"comment": comment})

    @mcp.tool()
    @tool_result
    def execute_workflow(database_id: str, asset_id: str, workflow_id: str) -> Dict[str, Any]:
        """Run a processing workflow/pipeline on an asset. May incur AWS cost."""
        return CLIENT.api.execute_workflow(database_id, asset_id, workflow_id)


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
    def unarchive_asset(database_id: str, asset_id: str, reason: str = "") -> Dict[str, Any]:
        """Restore a previously archived asset."""
        return CLIENT.api.unarchive_asset(database_id, asset_id, reason=reason or None)

    @mcp.tool()
    @tool_result
    def delete_asset(database_id: str, asset_id: str, reason: str = "") -> Dict[str, Any]:
        """PERMANENTLY delete an asset. Irreversible."""
        return CLIENT.api.delete_asset_permanent(database_id, asset_id, reason=reason or None, confirm=True)

    @mcp.tool()
    @tool_result
    def delete_database(database_id: str) -> Dict[str, Any]:
        """PERMANENTLY delete an (empty) database. Irreversible."""
        return CLIENT.api.delete_database(database_id)


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    if CLIENT is None:
        raise SystemExit(f"[vams-mcp] {STARTUP_ERROR}")
    mcp.run()


if __name__ == "__main__":
    main()
