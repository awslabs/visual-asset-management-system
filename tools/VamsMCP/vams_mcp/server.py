"""VAMS MCP server.

Exposes the Visual Asset Management System REST API as MCP tools. Read/search
tools are always available. Write tools require VAMS_ENABLE_WRITES=true and
destructive tools additionally require VAMS_ENABLE_DESTRUCTIVE=true.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


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
    CLI_VERSION,
    SUBSCRIPTION_ENTITY_ASSET,
    SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
    AuthenticationError,
    OverrideTokenError,
    SearchDisabledError,
    TokenExpiredError,
    describe_profile,
    login_step_for,
    sign_in_marker,
)
from . import __version__ as MCP_VERSION  # noqa: E402
from . import results  # noqa: E402
from .annotations import COMPUTE, DESTRUCTIVE, READ, WRITE  # noqa: E402
from .config import Config, ConfigError  # noqa: E402
from .addon_tools import register_addon_tools  # noqa: E402
from .addon_tools import ADDON_TOOL_MODULES, addon_deployed  # noqa: E402
from .custom_tools import register_custom_tools  # noqa: E402

_force_logging_to_stderr()

try:  # mcp >= 2.0 renamed FastMCP to MCPServer and dropped the fastmcp module
    from mcp.server.mcpserver import MCPServer as McpServer  # noqa: E402
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as McpServer  # noqa: E402

# --- Bootstrap -----------------------------------------------------------

# Config parsing is env-only and always safe. Building the client requires a
# configured vamscli profile. Without one the server still starts (degraded): vams_status answers,
# and every other tool returns a NotAuthenticated error asking the agent to have the USER sign in,
# retrying the profile on each call so a sign-in made later is picked up without a restart.
CONFIG = Config.from_env()
STARTUP_ERROR: Optional[str] = None
STARTUP_REASON: Optional[str] = None
STARTUP_USER_STEP: Optional[str] = None

try:
    CLIENT = VamsClient(CONFIG)
except ConfigError as exc:
    CLIENT = None  # type: ignore[assignment]
    STARTUP_ERROR = str(exc)
    STARTUP_REASON = exc.reason
    STARTUP_USER_STEP = exc.user_step

# What the server tells every connecting client, once, before any tool call (MCP `instructions`).
# Agent-facing, held to SERVER_INSTRUCTIONS_MAX_CHARS by tests/test_agent_surface.py. Update it when
# a tool family is added (Mandatory Rule 14).
SERVER_INSTRUCTIONS_MAX_CHARS = 2000
SERVER_INSTRUCTIONS = """\
VAMS (Visual Asset Management System) stores 3D, point-cloud, image and other files as ASSETS \
inside DATABASES, runs WORKFLOWS (made of pipelines) on them, and records each run as an \
EXECUTION. These tools call the VAMS API as the signed-in user; that user's permissions bound every \
answer, and a 403 means they may not.

Identifiers: an asset is addressed by databaseId + assetId; a file by its relativePath inside the \
asset (the file_key); a version by its Version id. Workflows and pipelines live in a database or \
the shared "GLOBAL" catalog.

Start points:
- Find assets: search_assets (name, text or metadata), search_nlp (files or assets by meaning; billed \
per call), or list_databases then list_assets.
- Then: get_asset -> list_asset_files / get_asset_metadata / list_asset_versions.
- Download: generate_download_url, only when a download was asked.
- Runs: list_executions (newest first, last 90 days) -> get_execution_details -> \
get_execution_logs.
- vams_status says who is signed in, which deployment and which tool tiers are on.

Results are BOUNDED. A result with truncated=true, a NextToken or warnings has rows you did not see: \
never report its count as a total or conclude something does not exist. Continue with \
starting_token or narrow the query. Search totals with total_is_lower_bound are minimums.

Errors come back as data (error, error_type). NotAuthenticated means nobody is signed in: ask \
the user to sign in (user_action says how) and stop; do not try to sign in yourself.

Use these tools, not shell commands, the vamscli CLI or the VAMS source code, to answer questions \
about this deployment. If no tool covers what is asked, say so plainly instead of working around it. \
Uploads, sync and user, role or constraint administration have no tools: the user runs them in the \
vamscli CLI. Write and delete tools exist only when the operator enabled them; tools that start compute \
(execute_workflow, rerun_execution, abort_execution) need the user's confirmation first.
"""

mcp = McpServer("vams", instructions=SERVER_INSTRUCTIONS)

# The workflow-executions endpoint rejects a larger page size (Step Functions throttling).
WORKFLOW_EXECUTIONS_MAX_PAGE_SIZE = 50

# The paged execution-detail metadata endpoint clamps a larger page size to this.
EXECUTION_DETAIL_METADATA_MAX_PAGE_SIZE = 500
# The text get_execution_results gathers before it hands back a NextToken: the service's own per-page
# response budget, so one tool answer is never larger than one API page could be. Measured on the
# text as SENT (see _result_text_sent_bytes), checked before a page is added.
EXECUTION_RESULT_MAX_BYTES = 5 * 1024 * 1024

# find_and_summarize issues one paginated version request PER HIT, so its hit count is the fan-out
# factor of a single tool call. Kept small deliberately; search_assets() pages instead.
_FIND_AND_SUMMARIZE_MAX_HITS = 25
# Its collections. 'input' is the asset/file rows, 'inputDatabase' the database-scope rows, 'output'
# the per-pipeline output metadata; anything else is a 400.
EXECUTION_DETAIL_METADATA_COLLECTIONS = ("input", "inputDatabase", "output")
# The FILE collections of the detail view (no inputDatabase — that split is metadata-only): 'input'
# is the workflow-level input files, 'output' the per-pipeline output files.
EXECUTION_DETAIL_FILE_COLLECTIONS = ("input", "output")

# The maxItems/pageSize the comment routes apply when the request names neither
# (common.dynamodb.validate_pagination_info). Named here because those routes return no
# continuation token, so it is the ceiling on what is reachable rather than a page size.
COMMENT_LIST_DEFAULT_BOUND = 10000

# The search tools' error on a deployment without OpenSearch, mirroring the CLI's search commands.
SEARCH_DISABLED_MESSAGE = (
    "Search is not enabled on this deployment (NOOPENSEARCH feature). "
    "Use list_assets to browse a database's assets instead."
)

# check_subscription answers 200 either way and carries the verdict in the message string, so these
# are the two values that decide it. Anything else is reported rather than read as "not subscribed".
SUBSCRIBED_MESSAGE = "success"
NOT_SUBSCRIBED_MESSAGE = "Subscription doesn't exists."


def tool_result(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool so API/client errors are returned as data, not exceptions.

    Agents handle a structured ``{"error": ...}`` far better than a raised
    exception, and it keeps one failing call from derailing a session. The
    result is ``{"error", "error_type"}`` plus the exception's ``json_fields``,
    which never replace those two: a ``RequestBlockedError`` (a request the
    deployment's AWS WAF web ACL blocked) carries ``blockedBy`` and, when
    known, ``requestId``.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        if fn.__name__ not in _WORKS_WITHOUT_SIGN_IN and _current_client() is None:
            return _not_authenticated(STARTUP_REASON or "no VAMS profile is signed in", STARTUP_USER_STEP)
        global _SIGN_IN_FAILURE
        try:
            result = fn(*args, **kwargs)
        except _SIGN_IN_ERRORS_TUPLE as exc:
            # The stored sign-in stopped working mid-session (expired refresh token, a 401 that
            # survives a refresh). The exception's own text tells the reader to run vamscli, which is
            # an instruction for the USER, so it goes to user_action and not to the agent's error.
            reason = next(text for cls, text in _SIGN_IN_ERRORS.items() if isinstance(exc, cls))
            # Remembered so vams_status reports the same state the tools answer with.
            _SIGN_IN_FAILURE = (reason, sign_in_marker(CONFIG))
            return _not_authenticated(reason, login_step_for(CONFIG))
        except Exception as exc:  # noqa: BLE001 - surface any failure cleanly
            result = {"error": str(exc), "error_type": type(exc).__name__}
            # Machine-readable facts the exception carries (for example blockedBy and requestId), as
            # the CLI's --json-output carries them.
            fields = getattr(exc, "json_fields", None)
            if isinstance(fields, dict):
                for key, value in fields.items():
                    result.setdefault(key, value)
            return result
        if fn.__name__ not in _WORKS_WITHOUT_SIGN_IN:
            # A call that reached the API without a sign-in error: the sign-in works again.
            _SIGN_IN_FAILURE = None
        return result

    # The MCP description is the docstring. Python 3.13 strips a docstring's common indentation at
    # compile time and 3.12 does not, so without this a 3.12 host publishes every description with
    # its source indentation: characters every agent reads, and over the Rule 11 budget.
    wrapper.__doc__ = inspect.cleandoc(fn.__doc__) if fn.__doc__ else fn.__doc__
    return wrapper


# (reason, sign_in_marker) of the last API call VAMS refused for its sign-in, until a later call
# succeeds or the profile holds a newer sign-in. A profile can hold a sign-in that VAMS no longer
# accepts; vams_status reads this so it does not say authenticated while the tools answer
# NotAuthenticated.
_SIGN_IN_FAILURE: Optional[Tuple[str, Any]] = None


def _refused_sign_in() -> Optional[str]:
    """Why VAMS refused the stored sign-in, or None when it was not refused or was replaced since."""
    global _SIGN_IN_FAILURE
    if _SIGN_IN_FAILURE is not None and sign_in_marker(CONFIG) != _SIGN_IN_FAILURE[1]:
        _SIGN_IN_FAILURE = None
    return _SIGN_IN_FAILURE[0] if _SIGN_IN_FAILURE else None


# Tools that answer without a signed-in profile. Every other tool returns NotAuthenticated then.
_WORKS_WITHOUT_SIGN_IN = frozenset({"vams_status"})

# The CLI's sign-in failures, each with the sentence the agent reads instead of the CLI's own text.
_SIGN_IN_ERRORS: Dict[type, str] = {
    AuthenticationError: "VAMS did not accept the stored sign-in (it was refused or has been cleared)",
    TokenExpiredError: "the VAMS sign-in has expired and could not be refreshed",
    OverrideTokenError: "the VAMS override token has expired or was refused",
}
_SIGN_IN_ERRORS_TUPLE = tuple(_SIGN_IN_ERRORS)


def _current_client() -> Optional[VamsClient]:
    """The client, building it again when the server started without one.

    A server started before the user signed in would otherwise answer NotAuthenticated until it is
    restarted. Building the client reads only the local profile files, so retrying on each call is
    cheap. Add-on tools are registered at start, so they still need a restart after the sign-in.
    """
    global CLIENT, STARTUP_ERROR, STARTUP_REASON, STARTUP_USER_STEP
    if CLIENT is None:
        try:
            CLIENT = VamsClient(CONFIG)
            STARTUP_ERROR = STARTUP_REASON = STARTUP_USER_STEP = None
        except ConfigError as exc:
            STARTUP_ERROR, STARTUP_REASON, STARTUP_USER_STEP = str(exc), exc.reason, exc.user_step
    return CLIENT


def _not_authenticated(reason: str, user_step: Optional[str]) -> Dict[str, Any]:
    """The structured error of a call made with nobody signed in.

    `error` states the fact and tells the agent what to do (ask the user); it names no command, so the
    agent does not go and run one. The command is in `user_action`, as the step the USER takes.
    """
    step = user_step or login_step_for(CONFIG)
    return {
        "error": f"Not signed in to VAMS: {reason}. Ask the user to sign in, then call the tool again.",
        "error_type": "NotAuthenticated",
        "user_action": (
            f"Ask the user to sign in to VAMS: the user runs {step} in their own terminal. "
            "Tools of an add-on appear only after the MCP server is restarted."
        ),
    }


# --- Response-shape helpers ----------------------------------------------
#
# These live here rather than on VamsClient deliberately: they are how the tool surface presents a
# response, not part of the client's contract with the CLI (whose `message`-envelope asymmetry other
# callers depend on). Moving them onto the client would couple the two again.


def _unwrap_message_with_warnings(page: Any, siblings: Tuple[str, ...] = ("warnings",)) -> Any:
    """Unwrap the ``message`` envelope, keeping the named sibling top-level arrays (``warnings`` by
    default).

    A pipeline save reports its non-blocking warnings as a SIBLING of ``message`` — the response
    model carries no warnings field, so that array is the only copy and plain unwrapping drops it.
    An abort passes ``("warnings", "notAbortable")``: a group abort nests its results under
    ``message`` and reports both arrays beside it.
    """
    payload = CLIENT.unwrap_message(page)
    if not isinstance(page, dict) or not isinstance(payload, dict) or payload is page:
        return payload
    kept = {key: page[key] for key in siblings if page.get(key)}
    if not kept:
        return payload
    return {**payload, **kept}


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


# list_tags marks the tag type of a required type with this suffix (tagService), and an agent copies it
# back. The write routes reject it (a name cannot contain brackets), so it is removed as the CLI's
# `tag update` does.
REQUIRED_TAG_TYPE_SUFFIX = " [R]"


def _tag_type_name(name: str) -> str:
    """A tag type name as the write routes take it: the list_tags required marker removed."""
    return name.replace(REQUIRED_TAG_TYPE_SUFFIX, "")


def _tag_body(names: Dict[str, Any], database_id: Optional[str]) -> Dict[str, Any]:
    """The flat object the tag and tag-type write routes take: a database_id scopes it, none is global.
    A tagTypeName copied from list_tags loses its required marker."""
    body = dict(names)
    if isinstance(body.get("tagTypeName"), str):
        body["tagTypeName"] = _tag_type_name(body["tagTypeName"])
    return {**body, **({"databaseId": database_id} if database_id else {})}


# =========================================================================
# READ / SEARCH TOOLS (always available)
# =========================================================================


@mcp.tool(title="VAMS connection status", annotations=READ)
@tool_result
def vams_status() -> results.Status:
    """Check the VAMS connection: call this first, or when a tool returns NotAuthenticated.

    Answers without any API request and also when nobody is signed in. Returns `authenticated`,
    `profile`, `apiUrl` (the deployment), `userId` (who the tools act as), `featureSwitches` (what the
    deployment enabled at the last sign-in), `gates` (whether create/update and delete tools and
    which add-ons are registered), `mcpVersion` and `cliVersion`. When `authenticated` is false,
    `reason` says why and `user_action` is the step to ask the user to take. `tokenExpired` true with
    a `note`: the next call renews the sign-in or answers NotAuthenticated."""
    status = describe_profile(CONFIG)
    client = _current_client()
    refused = _refused_sign_in() if client is not None else None
    status["authenticated"] = client is not None and refused is None
    status["gates"] = {
        "reads": True,
        "writes": CONFIG.enable_writes,
        "destructive": CONFIG.enable_destructive,
        "addons": [m.__name__.rsplit(".", 1)[-1] for m in ADDON_TOOL_MODULES if _ADDONS_REGISTERED.get(m)],
    }
    status["mcpVersion"] = MCP_VERSION
    status["cliVersion"] = CLI_VERSION
    if client is None:
        unauthenticated = _not_authenticated(STARTUP_REASON or "no VAMS profile is signed in", STARTUP_USER_STEP)
        status["reason"] = STARTUP_REASON
        status["user_action"] = unauthenticated["user_action"]
    elif refused is not None:
        status["reason"] = refused
        status["user_action"] = _not_authenticated(refused, login_step_for(CONFIG))["user_action"]
    elif status.get("tokenExpired"):
        status["note"] = (
            "The stored sign-in has expired. The next call renews it, or answers NotAuthenticated "
            "when VAMS refuses the renewal."
        )
    return status


@mcp.tool(title="List feature switches", annotations=READ)
@tool_result
def list_features() -> results.Features:
    """Use to see the deployment's enabled feature switches (`enabled`), as of `fetched_at` (the
    last sign-in)."""
    snapshot = CLIENT.feature_switches()
    result: Dict[str, Any] = {key: snapshot[key] for key in ("enabled", "total", "fetched_at")}
    if not snapshot["has_feature_switches"]:
        result["note"] = (
            "The sign-in holds no feature switches, so none are known (not: none are enabled). "
            "Ask the user to sign in again to record them."
        )
    return result


@mcp.tool(title="List my allowed API routes", annotations=READ)
@tool_result
def list_allowed_api_routes() -> Dict[str, Any]:
    """Use to see what this user may do: the API routes and methods the user's permissions allow; a
    route missing here is refused with 403. Each {param} is checked as 'vams:any', so a permission for
    a concrete path value can make a route look missing although calls on that path are allowed."""
    return CLIENT.api.list_allowed_api_routes()


@mcp.tool(title="List all API routes", annotations=READ)
@tool_result
def list_api_routes() -> Dict[str, Any]:
    """Use when writing an `api` permission constraint: EVERY VAMS API route (`path`, `methods`,
    `category`, `unauthenticated`), regardless of what the current user may call; use
    list_allowed_api_routes for that."""
    return CLIENT.api.list_api_routes()


@mcp.tool(title="List constraint vocabulary", annotations=READ)
@tool_result
def list_constraint_permission_objects() -> Dict[str, Any]:
    """Use when building a permission constraint: `objectTypes` (with their `fields`), `operators`,
    `permissions` and `permissionTypes` as `{label, value}` pairs. Send the `value`."""
    return CLIENT.api.list_constraint_permission_objects()


@mcp.tool(title="List databases", annotations=READ)
@tool_result
def list_databases(
    include_deleted: bool = False,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> results.DatabasePage:
    """Use to find databases (most asset tools need a databaseId). Rows: databaseId, description,
    assetCount, defaultBucketId. include_deleted adds deleted ones.

    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue.
    Never report a count, or conclude something does not exist, from a truncated
    result."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_databases(show_deleted=include_deleted, params=params),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="Get database", annotations=READ)
@tool_result
def get_database(database_id: str, include_deleted: bool = False) -> results.Database:
    """Use to read one database: databaseId, description, assetCount, defaultBucketId, bucketName,
    restrictMetadataOutsideSchemas (keys outside a schema refused), restrictFileUploadsToExtensions."""
    return CLIENT.api.get_database(database_id, include_deleted)


@mcp.tool(title="List asset buckets", annotations=READ)
@tool_result
def list_buckets(
    max_items: Optional[int] = None, starting_token: Optional[str] = None
) -> Dict[str, Any]:
    """Use before create_database to pick a default_bucket_id. Rows carry the bucket id, name,
    `bucketRegion` and, for another account's bucket, `bucketAccountId`.
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_buckets(params=params),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="List assets", annotations=READ)
@tool_result
def list_assets(
    database_id: Optional[str] = None,
    include_archived: bool = False,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> results.AssetPage:
    """Use to browse assets in one database, or in every database the user can read. To find an asset
    by name use search_assets. Rows: databaseId, assetId, assetName, description, tags, status.
    include_archived adds archived assets.

    BOUNDED: `truncated` means rows were
    not seen; pass `NextToken` as `starting_token` to continue. Do not report an asset count, or
    conclude an asset does not exist, from a truncated result."""
    endpoint = API_DATABASE_ASSETS.format(databaseId=database_id) if database_id else API_ASSETS

    def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
        if include_archived:
            params = {**params, "showArchived": "true"}
        return CLIENT.get_json(endpoint, params=params)

    return CLIENT.paginate(fetch, max_items=max_items, starting_token=starting_token)


@mcp.tool(title="Get asset", annotations=READ)
@tool_result
def get_asset(database_id: str, asset_id: str, include_archived: bool = False) -> results.Asset:
    """Use to read one asset by database_id + asset_id: assetName, description, isDistributable (false:
    no downloads), tags, status ("active" or "archived"), currentVersion. include_archived also finds
    an archived asset. Next: list_asset_files, get_asset_metadata or list_asset_versions."""
    return CLIENT.api.get_asset(database_id, asset_id, show_archived=include_archived)


@mcp.tool(title="List asset files", annotations=READ)
@tool_result
def list_asset_files(
    database_id: str,
    asset_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
    include_archived: bool = False,
) -> results.FilePage:
    """Use to see an asset's files and folders. Rows: fileName, relativePath (the file_key other tools
    take), isFolder, size, versionId, isArchived.

    BOUNDED: `truncated` means files were not seen; pass
    `NextToken` as `starting_token` to continue. Never report a file count, or answer "file X is not
    in this asset", from a truncated result; use search_files() to test for one file.
    include_archived also finds an ARCHIVED asset, whose files are all archived."""
    def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
        if include_archived:
            params = {**params, "showArchived": "true"}
        return CLIENT.api.list_asset_files(database_id, asset_id, params=params)

    return CLIENT.paginate(
        fetch,
        max_items=max_items,
        items_key="items",
        starting_token=starting_token,
    )


@mcp.tool(title="Get file info", annotations=READ)
@tool_result
def get_file_info(
    database_id: str,
    asset_id: str,
    file_key: str,
    include_versions: bool = False,
    include_archived: bool = False,
) -> results.FileInfo:
    """Use to read one file by file_key: size, contentType, lastModified, isArchived, primaryType,
    previewFile. include_versions adds its versions; include_archived finds an archived asset's file."""
    params: Dict[str, Any] = {
        "filePath": file_key,
        "includeVersions": "true" if include_versions else "false",
    }
    if include_archived:
        params["showArchived"] = "true"
    return CLIENT.api.get_file_info(database_id, asset_id, params)


@mcp.tool(title="Get asset metadata", annotations=READ)
@tool_result
def get_asset_metadata(
    database_id: str,
    asset_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
    include_archived: bool = False,
) -> results.MetadataPage:
    """Use to read an asset's metadata (rows: metadataKey, metadataValue, metadataValueType).
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue.
    Do not conclude a key is absent from a truncated result. include_archived also
    finds an archived asset."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_asset_metadata_v2(
            database_id, asset_id, page_size=params["pageSize"], starting_token=params.get("startingToken"),
            show_archived=include_archived,
        ),
        items_key="metadata",
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="Get database metadata", annotations=READ)
@tool_result
def get_database_metadata(
    database_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> results.MetadataPage:
    """Use to read a database's own metadata key/value pairs.
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue.
    Do not conclude a key is absent from a truncated result."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_database_metadata_v2(
            database_id, page_size=params["pageSize"], starting_token=params.get("startingToken")
        ),
        items_key="metadata",
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="List asset versions", annotations=READ)
@tool_result
def list_asset_versions(
    database_id: str,
    asset_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
    include_archived: bool = False,
) -> results.VersionPage:
    """Use to see an asset's versions. Rows: Version (the asset_version_id), Comment, DateModified,
    createdBy, isCurrent, fileCount, versionAlias. include_archived adds archived versions.
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue.
    Do not report a version count from a truncated result."""
    def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
        if include_archived:
            params = {**params, "showArchived": "true"}
        return CLIENT.api.get_asset_versions(database_id, asset_id, params=params)

    return CLIENT.paginate(
        fetch,
        max_items=max_items,
        items_key="versions",
        starting_token=starting_token,
    )


@mcp.tool(title="Get asset version", annotations=READ)
@tool_result
def get_asset_version(
    database_id: str, asset_id: str, asset_version_id: str, include_archived: bool = False
) -> results.AssetVersion:
    """Use to read one asset version: its files (relativeKey, versionId, size), comment, createdBy,
    dateCreated and versionedMetadata. include_archived also finds an archived asset."""
    return CLIENT.api.get_asset_version(
        database_id, asset_id, asset_version_id, show_archived=include_archived
    )


@mcp.tool(title="Get asset history", annotations=READ)
@tool_result
def get_asset_history(
    database_id: str,
    asset_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Use to see who created, edited, archived or deleted an asset, newest first. A `truncated` result
    is the recent history only: an event missing from it may still have happened.
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_asset_history(database_id, asset_id, params=params),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="Get asset links", annotations=READ)
@tool_result
def get_asset_links(
    database_id: str, asset_id: str, child_tree_view: bool = False, include_archived: bool = False
) -> Dict[str, Any]:
    """Use to see an asset's related, parent and child assets. A `child_tree_view` walk is bounded:
    `treeTruncated` true means the tree is partial. `unresolvedCounts` counts links whose asset could
    not be read (retry). include_archived also finds an archived asset."""
    return CLIENT.api.get_asset_links_for_asset(
        database_id, asset_id, child_tree_view=child_tree_view, show_archived=include_archived
    )


# Smaller than the CLI's defaults (100 assets, 2000 files): an export page carries every file's and
# asset's metadata, and the whole page lands in the agent's context.
EXPORT_DEFAULT_MAX_ASSETS = 25
EXPORT_DEFAULT_MAX_FILES = 500
# The most one call asks for, whatever the agent passes (the route itself allows 1000 / 10000).
# Larger trees are read page by page with starting_token.
EXPORT_MAX_ASSETS = 100
EXPORT_MAX_FILES = 1000


@mcp.tool(title="Export asset tree", annotations=READ)
@tool_result
def export_asset(
    database_id: str,
    asset_id: str,
    max_assets: int = EXPORT_DEFAULT_MAX_ASSETS,
    max_files: int = EXPORT_DEFAULT_MAX_FILES,
    starting_token: Optional[str] = None,
    fetch_relationships: bool = True,
    fetch_entire_subtrees: bool = False,
    include_parent_relationships: bool = False,
    include_archived_files: bool = False,
    include_folder_files: bool = False,
    include_only_primary_type_files: bool = False,
    file_extensions: Optional[List[str]] = None,
    include_asset_metadata: bool = True,
    include_file_metadata: bool = True,
    include_asset_link_metadata: bool = True,
) -> results.AssetExport:
    """Use to read an asset and its children in one call: `assets` (metadata, files), `relationships`,
    `totalAssetsInTree`. One child level unless fetch_entire_subtrees; fetch_relationships=False: the
    asset alone. ONE PAGE per call, capped at 100 assets and 1000 files: `truncated` + `NextToken` mean
    more; pass it as `starting_token` (files_truncated assets continue there)."""
    params: Dict[str, Any] = {
        "generatePresignedUrls": False,
        "includeFolderFiles": include_folder_files,
        "includeOnlyPrimaryTypeFiles": include_only_primary_type_files,
        "includeFileMetadata": include_file_metadata,
        "includeAssetLinkMetadata": include_asset_link_metadata,
        "includeAssetMetadata": include_asset_metadata,
        "fetchAssetRelationships": fetch_relationships,
        "fetchEntireChildrenSubtrees": fetch_entire_subtrees,
        "includeParentRelationships": include_parent_relationships,
        "includeArchivedFiles": include_archived_files,
        "maxAssets": max(1, min(max_assets, EXPORT_MAX_ASSETS)),
        "maxFiles": max(1, min(max_files, EXPORT_MAX_FILES)),
    }
    if file_extensions:
        # The handler matches extensions with their leading dot, lower-cased, as the CLI sends them.
        params["fileExtensions"] = [
            (ext if ext.startswith(".") else f".{ext}").lower() for ext in (e.strip() for e in file_extensions)
        ]
    if starting_token:
        params["startingToken"] = starting_token
    result = CLIENT.api.export_asset(database_id, asset_id, params)
    if isinstance(result, dict) and result.get("NextToken"):
        result["truncated"] = True
        result["note"] = (
            f"One page of the export: {result.get('assetsInThisPage', len(result.get('assets') or []))} of "
            f"{result.get('totalAssetsInTree')} asset(s). Call again with starting_token=NextToken for the rest."
        )
        if any(isinstance(a, dict) and a.get("files_truncated") for a in result.get("assets") or []):
            result["note"] += " Assets marked files_truncated have more files on the next page."
    return result


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
        # expressible at all. The entry is `{field, order}` (SearchSortModel in
        # backend/backend/models/search.py), the shape the CLI's `_build_sort_config` sends; the
        # search handler converts it to OpenSearch's native `{<field>: {order}}` itself, and the
        # request model rejects the native form with 400.
        "sort": (
            [{"field": sort_field, "order": "desc" if sort_desc else "asc"}]
            if sort_field
            else ["_score"]
        ),
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


def _require_search() -> None:
    """Refuse a search tool on a deployment without OpenSearch, as the CLI's search commands do."""
    if not CLIENT.search_enabled():
        raise SearchDisabledError(SEARCH_DISABLED_MESSAGE)


@mcp.tool(title="Search assets", annotations=READ)
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
) -> results.SearchResult:
    """Use to find assets by name, text, metadata or location (OpenSearch).

    - query: free text. database_id: one database ("GLOBAL" searches every database).
    - metadata_query: `key:value`, e.g. 'product:Training'. Do NOT write 'MD_.product'.
    - geo_search: one of `point` ({lat, lon, radiusMeters}), `bbox` ({topLeft, bottomRight}) or
      `geoJson`, plus an optional `relation` (intersects, within, contains, disjoint).
    - size / from_offset: one page of hits; page on by advancing from_offset.
    - sort_field / sort_desc: order by an index field, e.g. "date_asset_version_createdate" for the
      newest; get_search_fields() lists the names.

    Returns `total`, `returned` and `results` (id, score, source). When `total_is_lower_bound` is
    true the count is at least `total`: report `total_display` (e.g. "120+"). Without OpenSearch
    (NOOPENSEARCH) it returns an error; use list_assets there."""
    _require_search()
    request = _build_search_request(
        ["asset"], query, database_id, metadata_query, size, include_archived, geo_search,
        from_offset=from_offset, sort_field=sort_field, sort_desc=sort_desc,
    )
    raw = CLIENT.api.search_query(request)
    # Trimmed with the same clamp the request carries: a size of 0 or a negative asks OpenSearch for
    # one hit, so trimming to the raw value would report an empty list for a query that matched.
    return CLIENT.trim_search_results(raw, max_hits=request["size"])


@mcp.tool(title="Search files", annotations=READ)
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
) -> results.SearchResult:
    """Use to find files across assets, or to test whether one file exists. Same parameters and
    NOOPENSEARCH error as search_assets; file index fields differ (e.g. sort_field="date_lastmodified")."""
    _require_search()
    request = _build_search_request(
        ["file"], query, database_id, metadata_query, size, include_archived, geo_search,
        from_offset=from_offset, sort_field=sort_field, sort_desc=sort_desc,
    )
    raw = CLIENT.api.search_query(request)
    return CLIENT.trim_search_results(raw, max_hits=request["size"])


_NLP_ENTITY_TYPES = ("file", "asset")
_NLP_SIZE_MAX = 100
# The fileClass ids the vector index stores, in the order the backend declares them
# (backend/backend/common/vectorsearch/fileClassIntent.py FILE_CLASSES). The route refuses an unknown
# id with a 400 as well; the tool refuses it before the call, with the list in the error.
# tests/test_search_nlp_tool.py pins this copy to the backend's.
_NLP_FILE_CLASSES = (
    "image", "video", "audio", "document", "text", "data", "tiles3d", "mesh", "usd", "cad",
    "pointcloud", "splat", "ifc", "other",
)


def _normalise_nlp_list(values: Optional[List[str]], strip_dot: bool = False) -> List[str]:
    """Lower-cased, trimmed, de-duplicated, first-seen order — the backend's own normalisation,
    applied here so validation sees what the route will see."""
    seen: List[str] = []
    for value in values or []:
        item = value.strip().lower()
        if strip_dot:
            item = item.lstrip(".")
        if item and item not in seen:
            seen.append(item)
    return seen


def _build_nlp_search_request(
    query: str,
    database_ids: Optional[List[str]],
    entity_type: str,
    size: int,
    include_archived: bool,
    file_classes: Optional[List[str]],
    metadata_query: Optional[str],
    include_segments: bool,
    file_extensions: Optional[List[str]] = None,
    geo_search: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if entity_type not in _NLP_ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {_NLP_ENTITY_TYPES}, got {entity_type!r}")
    classes = _normalise_nlp_list(file_classes)
    unknown = [c for c in classes if c not in _NLP_FILE_CLASSES]
    if unknown:
        raise ValueError(
            f"file_classes must be from {list(_NLP_FILE_CLASSES)}, got {unknown}"
        )
    request: Dict[str, Any] = {
        "query": query,
        "entityTypes": [entity_type],
        "size": max(1, min(size, _NLP_SIZE_MAX)),
        "includeArchived": include_archived,
    }
    scoped = [d for d in (database_ids or []) if d not in _UNSCOPED_DATABASE_IDS]
    if scoped:
        request["databaseIds"] = scoped
    if classes:
        request["fileClasses"] = classes
    extensions = _normalise_nlp_list(file_extensions, strip_dot=True)
    if extensions:
        request["fileExtensions"] = extensions
    if metadata_query:
        request["metadataQuery"] = metadata_query
        request["metadataSearchMode"] = "both"
    if geo_search:
        request["geoSearch"] = geo_search
    if tags:
        request["tags"] = list(tags)
    # The server default is to search segments; only the opt-out travels on the wire.
    if not include_segments:
        request["includeSegments"] = False
    return request


# Not in the README's autoApprove sample: every call is a billed Amazon Bedrock embedding request.
@mcp.tool(title="Natural-language search", annotations=READ)
@tool_result
def search_nlp(
    query: str,
    database_ids: Optional[List[str]] = None,
    entity_type: str = "file",
    size: int = 25,
    include_archived: bool = False,
    file_classes: Optional[List[str]] = None,
    file_extensions: Optional[List[str]] = None,
    metadata_query: Optional[str] = None,
    geo_search: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    include_segments: bool = True,
) -> results.NlpSearchResult:
    """Use to find files or assets by meaning (vector index; needs the VECTORSEARCH switch, not
    OpenSearch). Each call embeds `query` with Amazon Bedrock and is billed.

    query: 1..1000 characters. database_ids: at most 100 ids. entity_type: "file" or "asset" (each
    asset's best file). size: 1..100; no offset paging, so narrow the filters instead.
    file_classes: image, video, audio, document, text, data, tiles3d, mesh, usd, cad, pointcloud,
    splat, ifc, other. metadata_query, geo_search and tags need OpenSearch (else ignored with a
    warning). include_segments=False matches whole-file vectors only.

    Every `total` that is not zero is a LOWER BOUND (`relation` "gte"; report `total_display`):
    approximate nearest-neighbour search can miss weak matches; so is zero when nlp.truncated.
    `warnings`: truncated:window,
    truncated:targets, databases:none_accessible (no readable database, not "no match"),
    opensearch:fields_ignored, opensearch:enrichment_failed, segments:window_full. A 503: retry later."""
    request = _build_nlp_search_request(
        query, database_ids, entity_type, size, include_archived, file_classes, metadata_query,
        include_segments, file_extensions=file_extensions, geo_search=geo_search, tags=tags,
    )
    raw = CLIENT.api.search_nlp(request)
    trimmed = CLIENT.trim_search_results(raw, max_hits=request["size"])
    total = raw.get("hits", {}).get("total", {}) if isinstance(raw, dict) else {}
    trimmed["relation"] = total.get("relation", "eq") if isinstance(total, dict) else "eq"
    trimmed["nlp"] = raw.get("nlp", {}) if isinstance(raw, dict) else {}
    trimmed["warnings"] = raw.get("warnings", []) if isinstance(raw, dict) else []
    return trimmed


@mcp.tool(title="Get search fields", annotations=READ)
@tool_result
def get_search_fields() -> Dict[str, Any]:
    """Use to learn index field names for sort_field and metadata searches (the OpenSearch mapping).
    Returns an error on a deployment without OpenSearch (NOOPENSEARCH)."""
    _require_search()
    return CLIENT.api.get_search_mapping()


@mcp.tool(title="List workflows", annotations=READ)
@tool_result
def list_workflows(
    database_id: Optional[str] = None,
    include_archived: bool = False,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Use to find a workflow to run or inspect, optionally in one database. include_archived adds
    archived workflows (how an archived workflow's id is found to restore it).
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_workflows(
            database_id=database_id, include_archived=include_archived, params=params
        ),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="List an asset's executions", annotations=READ)
@tool_result
def list_workflow_executions(
    database_id: str,
    asset_id: str,
    workflow_id: Optional[str] = None,
    workflow_database_id: Optional[str] = None,
    status: Optional[str] = None,
    exclude_status: Optional[str] = None,
    trigger_type: Optional[str] = None,
    group_id: Optional[str] = None,
    triggered_by_user_id: Optional[str] = None,
    filter_start_date: Optional[str] = None,
    filter_end_date: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> results.ExecutionPage:
    """Use to see the workflow runs of ONE asset (list_executions covers every asset). A workflow id
    is unique across every database, so workflow_id alone identifies it; workflow_database_id is a
    narrowing filter, and a value that is not its database empties the result.

    `filterStartDate` is the applied window (90 days by default), so an asset processed before it
    lists nothing: widen it with filter_start_date (UTC "YYYY-MM-DDTHH:MM:SSZ"). Other filters match as
    on list_executions; a SKIPPED row is not a run.

    `warnings` means the page WITHHELD rows and the result is `truncated`: do not report a count or
    conclude a run does not exist. Also BOUNDED: pass `NextToken` as `starting_token`."""
    extra = {
        key: value
        for key, value in (
            ("status", status),
            ("excludeStatus", exclude_status),
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


@mcp.tool(title="List tags", annotations=READ)
@tool_result
def list_tags(
    database: Optional[str] = None,
    scope: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Use to see the tags assets can carry. database: that database's tags only; scope: 'global' or
    'all'.
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue.
    Do not conclude a tag does not exist from a truncated result."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_tags(params=params, database_id=database, scope=scope),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="List tag types", annotations=READ)
@tool_result
def list_tag_types(
    database: Optional[str] = None,
    scope: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Use to see tag types (tag groups, some required). database: that database's only; scope:
    'global' or 'all'.
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.get_tag_types(params=params, database_id=database, scope=scope),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="List metadata schemas", annotations=READ)
@tool_result
def list_metadata_schemas(
    database_id: Optional[str] = None,
    metadata_entity_type: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Use to see the metadata fields a database or entity type defines (entity types: databaseMetadata,
    assetMetadata, fileMetadata, fileAttribute, assetLinkMetadata).
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue.
    Do not conclude a schema is undefined from a truncated result."""
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


@mcp.tool(title="Get a download URL", annotations=READ)
@tool_result
def generate_download_url(
    database_id: str,
    asset_id: str,
    file_key: Optional[str] = None,
    version_id: Optional[str] = None,
) -> results.DownloadUrl:
    """Use only when the user asked to download a file: a time-limited presigned download URL for an
    asset file (`downloadUrl`, `expiresIn`; file_key = its relativePath). Non-mutating.

    The URL is a bearer credential: anyone holding it can download the object until it expires, at
    most `presignedUrlTimeoutSeconds` (24 hours by default) or sooner if the credentials that signed it
    expire. It lands in the agent transcript and the host's logs, so treat it as disclosed.
    `presignedUrlNetworkRestrictions` (allowedIpRanges / allowedVpceIds) can limit where it works."""
    return CLIENT.api.download_asset_file(
        database_id, asset_id, file_key=file_key, version_id=version_id
    )


@mcp.tool(title="Get many download URLs", annotations=READ)
@tool_result
def generate_download_urls_bulk(
    database_id: str,
    asset_id: str,
    file_keys: List[Union[str, Dict[str, Any]]],
    asset_version_id: Optional[str] = None,
    asset_version_alias: Optional[str] = None,
) -> results.DownloadUrl:
    """Use only when the user asked to download several files of ONE asset. `file_keys` holds up to 1500
    entries: a relative path or `{"key": path, "versionId": id}`; `asset_version_id` or
    `asset_version_alias` instead pins EVERY key (not with per-key versionIds). Non-mutating.

    Each `files[]` entry has `key`, `success`, and `downloadUrl` + `versionId` or `error`: a missing
    path is SKIPPED with `success: false` while the call succeeds, so check each entry; the top-level
    `downloadUrl` is only the FIRST success.

    Every URL is a bearer credential as generate_download_url describes, usable for at most
    `presignedUrlTimeoutSeconds` (24 hours by default) and landing in the agent transcript."""
    return CLIENT.api.download_asset_files_bulk(
        database_id,
        asset_id,
        file_keys,
        asset_version_id=asset_version_id,
        asset_version_alias=asset_version_alias,
    )


@mcp.tool(title="Find and summarize assets", annotations=READ)
@tool_result
def find_and_summarize(query: str, database_id: Optional[str] = None, size: int = 10) -> Dict[str, Any]:
    """Use for 'find X and tell me about them': searches assets and adds each hit's version count.
    COST: one request per hit, so `size` is clamped to 25; use search_assets() to page more.
    `version_count_truncated` marks a count that must not be reported as a total. Returns an error
    without OpenSearch (NOOPENSEARCH)."""
    _require_search()
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
        "total_is_lower_bound": trimmed.get("total_is_lower_bound", False),
        "total_display": trimmed.get("total_display"),
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


@mcp.tool(title="List pipelines", annotations=READ)
@tool_result
def list_pipelines(
    database_id: Optional[str] = None,
    include_archived: bool = False,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Use to find processing pipelines, in one database or (database_id omitted) everywhere, the
    GLOBAL catalog included.
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_pipelines(
            database_id=database_id, include_archived=include_archived, params=params
        ),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="Get pipeline", annotations=READ)
@tool_result
def get_pipeline(
    database_id: str, pipeline_id: str, include_archived: bool = False
) -> Dict[str, Any]:
    """Use to read one pipeline: executionConfig, systemConfig and config templates (the first 10;
    templateCount is the total, list_pipeline_templates() lists all)."""
    return CLIENT.unwrap_message(CLIENT.api.get_pipeline(database_id, pipeline_id, include_archived=include_archived))


@mcp.tool(title="List pipeline templates", annotations=READ)
@tool_result
def list_pipeline_templates(database_id: str, pipeline_id: str) -> Dict[str, Any]:
    """Use to list a pipeline's templates (no tagSchema, offloaded bodies blank; get_pipeline_template()
    reads one in full)."""
    return CLIENT.unwrap_message(CLIENT.api.list_pipeline_templates(database_id, pipeline_id))


@mcp.tool(title="Get pipeline template", annotations=READ)
@tool_result
def get_pipeline_template(database_id: str, pipeline_id: str, template_id: str) -> Dict[str, Any]:
    """Use to read one pipeline template in full: templateId, templateName, configFormat, configBody,
    inputInstructions, tagSchema and overrides."""
    return CLIENT.unwrap_message(CLIENT.api.get_pipeline_template(database_id, pipeline_id, template_id))


@mcp.tool(title="Get template tag schema", annotations=READ)
@tool_result
def get_pipeline_template_tag_schema(
    database_id: str, pipeline_id: str, template_id: str
) -> Dict[str, Any]:
    """Use before running a template: returns its tag schema, the fields a caller supplies (each tagKey,
    type, required, default, label, description, enumValues)."""
    return CLIENT.unwrap_message(CLIENT.api.get_pipeline_template_tag_schema(database_id, pipeline_id, template_id))


@mcp.tool(title="Get workflow", annotations=READ)
@tool_result
def get_workflow(database_id: str, workflow_id: str, include_archived: bool = False) -> Dict[str, Any]:
    """Use to read one workflow: workflowId, workflowName, specifiedPipelines (its steps), systemConfig
    (input and output rules, concurrencyRestriction), enabled and its triggers."""
    return CLIENT.unwrap_message(CLIENT.api.get_workflow(database_id, workflow_id, include_archived=include_archived))


@mcp.tool(title="List workflow triggers", annotations=READ)
@tool_result
def list_workflow_triggers(
    database_id: str,
    workflow_id: str,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Use to see what launches a workflow automatically and whether each trigger is enabled. Each
    `triggerType` is the trigger KEY ('fileUpload', or 'type#triggerId' for an extra one) that the
    get, set and delete tools take.
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_workflow_triggers(database_id, workflow_id, params=params),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="Get workflow trigger", annotations=READ)
@tool_result
def get_workflow_trigger(database_id: str, workflow_id: str, trigger_type: str) -> Dict[str, Any]:
    """Use to read one trigger and its input-file filters, by its KEY from list_workflow_triggers."""
    return CLIENT.unwrap_message(CLIENT.api.get_workflow_trigger(database_id, workflow_id, trigger_type))


@mcp.tool(title="List executions", annotations=READ)
@tool_result
def list_executions(
    status: Optional[str] = None,
    exclude_status: Optional[str] = None,
    workflow_id: Optional[str] = None,
    workflow_database_id: Optional[str] = None,
    trigger_type: Optional[str] = None,
    group_id: Optional[str] = None,
    triggered_by_user_id: Optional[str] = None,
    filter_start_date: Optional[str] = None,
    filter_end_date: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> results.ExecutionPage:
    """Use to find workflow runs across every workflow and asset, newest first (e.g. "the last failed
    run"). For ONE asset's runs use list_workflow_executions().

    Equality filters: status (RUNNING, SUCCEEDED, FAILED, ABORTED, SKIPPED), workflow_id,
    workflow_database_id, trigger_type ('manual', 'fileUpload' or 'systemReindex'), group_id,
    triggered_by_user_id; exclude_status drops statuses (e.g. "SKIPPED"). A SKIPPED row is not a
    run: do not count it.

    `filterStartDate` is the applied window (90 days by default): older runs are absent by design;
    widen it with filter_start_date / filter_end_date (UTC "YYYY-MM-DDTHH:MM:SSZ"). A run is listed
    only when the user can read its workflow, its inputs and the asset it wrote to.

    `warnings` means rows were WITHHELD (a cap on distinct assets checked, or a work budget) and the
    result is `truncated`: do not report a count or conclude a run does not exist; narrow the filters.
    Also BOUNDED: pass `NextToken` as `starting_token`. `outputTypes` containing "Result" means
    get_execution_results() has text. Next: get_execution_details(workflowExecutionId)."""
    extra = {
        key: value
        for key, value in (
            ("status", status),
            ("excludeStatus", exclude_status),
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


# The description names only what an agent needs to reach a conclusion (Mandatory Rule 11).
# The full field reference of the detail view (sub-execution stages and `stageSource`,
# `workflowVersionCurrent` and what redeploys a workflow, `availableLogs` kinds, the
# `metadataSource*` fields) is the API documentation's workflows page
# (documentation/docusaurus-site/docs/api/workflows.md) and the handler,
# backend/backend/handlers/workflows/executionService.py `get_execution_details`.
@mcp.tool(title="Get execution details", annotations=READ)
@tool_result
def get_execution_details(execution_id: str, include_sub_executions: bool = False) -> results.ExecutionDetails:
    """Use when asked why a run failed or what it produced: per-step (`pipelines`) status, error,
    inputs, outputs, config and `availableLogs` (pass a `logId` to get_execution_logs).

    It can be PARTIAL: `truncatedCollections` names each cut collection. Check it before reporting a
    count or an absence; read the rest with page_execution_detail_metadata(),
    page_execution_detail_files() or get_execution_results().

    Input metadata: `inputMetadata` (asset/file rows) and `inputDatabaseMetadata` (database
    rows), each with `metadata` and `attributes`; `expanded` true marks context, not an input file.
    `metadataSourceDatabases` lists every database read. `renderedConfig` is the PRE-system-tag
    inline copy (`renderedConfigTruncated` when cut); `renderedConfigLocation` (NOT only on
    truncation) holds the FULLY substituted body the step read.

    A SKIPPED execution never ran (`skip` says why). include_sub_executions=True adds each step's
    `subExecutions` (nested state machines, Batch and Deadline Cloud jobs: status, error,
    `stages`; `caught` is a handled failure). Partial reads are marked `stagesTruncated`,
    `historyTruncated`, `subExecutionsTruncated`, `subExecutionWarnings`, "pipelines.subExecutions"."""
    params = {"includeSubExecutions": "true"} if include_sub_executions else None
    return CLIENT.unwrap_message(CLIENT.api.get_execution_details(execution_id, params=params))


@mcp.tool(title="Page execution metadata", annotations=READ)
@tool_result
def page_execution_detail_metadata(
    execution_id: str,
    collection: str = "input",
    pipeline_id: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Use when get_execution_details' `truncatedCollections` names a metadata collection: reads it in
    full. `collection`: "input" (the default), "inputDatabase" or "output"; pipeline_id narrows to one
    step. Rows carry the producing `pipelineId`; input rows `expanded` (context, not an input file);
    output rows targetFilePath, metadataKey, metadataValue and metadataType ("metadata" or
    "attribute": one key written as both is two rows). `truncated` means the row cap was reached:
    pass `NextToken` as `starting_token` with the SAME collection and pipeline_id (a mismatch is a
    400)."""
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


@mcp.tool(title="Page execution files", annotations=READ)
@tool_result
def page_execution_detail_files(
    execution_id: str,
    collection: str = "input",
    pipeline_id: Optional[str] = None,
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Use when get_execution_details' `truncatedCollections` names "inputFiles" or "outputs.files":
    reads that file collection in full. `collection`: "input" (workflow input files, the default) or
    "output" (per-step output files; pipeline_id narrows it to one step). Output rows carry
    relativeFilePath, fileType, fileSize and s3VersionId; no S3 bucket/key is returned. `truncated`
    means the row cap was reached: pass `NextToken` as `starting_token` with the SAME collection and
    pipeline_id (a mismatch is a 400)."""
    if collection not in EXECUTION_DETAIL_FILE_COLLECTIONS:
        return {
            "error": f"collection must be one of {list(EXECUTION_DETAIL_FILE_COLLECTIONS)}",
            "error_type": "ValueError",
        }

    extra: Dict[str, Any] = {"collection": collection}
    if pipeline_id:
        extra["pipelineId"] = pipeline_id

    def _call(params: Dict[str, Any]) -> Dict[str, Any]:
        # The continuation token is only valid alongside the collection and pipelineId it was issued
        # with, so every page carries the same filters.
        return CLIENT.api.get_execution_details_files(execution_id, params={**params, **extra})

    result = CLIENT.paginate(
        _call,
        max_items=max_items,
        page_size=min(CONFIG.page_size, EXECUTION_DETAIL_METADATA_MAX_PAGE_SIZE),
        starting_token=starting_token,
    )
    result["collection"] = collection
    return result


def _result_text_sent_bytes(text: str) -> int:
    """Bytes `text` occupies in the tool answer as sent: UTF-8 inside a JSON string value, so a quote,
    backslash or control character counts its escape and a CJK character its three UTF-8 bytes. The
    same as-sent rule the service applies to its own page budget; the raw `resultContentBytes` a page
    reports is not used, since it under-counts escaped text."""
    return len(json.dumps(text or "", ensure_ascii=False).encode("utf-8")) - 2


@mcp.tool(title="Get execution result text", annotations=READ)
@tool_result
def get_execution_results(execution_id: str,
                          starting_token: Optional[str] = None) -> results.ExecutionResults:
    """Use for "what did the run output" from a pipeline that produces text rather than files. Returns
    {resultContent, resultContentTruncated, resultRowCount, resultContentBytes, pageCount}, at most
    5 MiB per call: a `NextToken` means more text remains (pass it as `starting_token`).
    `resultContentTruncated` true means text was stored cut and cannot be read. A run with no result
    returns resultContent "", not an error."""
    parts = []
    truncated = False
    row_count = byte_count = pages = sent_bytes = 0
    next_token = starting_token
    over_budget = False
    while True:
        params = {"startingToken": next_token} if next_token else {}
        page = CLIENT.unwrap_message(CLIENT.api.get_execution_results(execution_id, params=params))
        content = page.get("resultContent", "") or ""
        page_sent = _result_text_sent_bytes(content)
        # Check BEFORE adding: a page that would carry the answer past the budget is left for the next
        # call, which resumes from the token that fetched it. The first page is always kept — the
        # service already bounds one page by this budget — so a call always makes progress.
        if pages and sent_bytes + page_sent > EXECUTION_RESULT_MAX_BYTES:
            over_budget = True
            break
        pages += 1
        parts.append(content)
        sent_bytes += page_sent
        truncated = truncated or bool(page.get("resultContentTruncated"))
        row_count += page.get("resultRowCount", 0) or 0
        byte_count += page.get("resultContentBytes", 0) or 0
        next_token = page.get("NextToken")
        if not next_token or pages >= CONFIG.max_pages:
            break
    result: Dict[str, Any] = {
        "resultContent": "".join(parts),
        "resultContentTruncated": truncated,
        "resultRowCount": row_count,
        "resultContentBytes": byte_count,
        "pageCount": pages,
    }
    if next_token:
        result["NextToken"] = next_token
        bound = ("the 5 MiB result budget" if over_budget
                 else f"the max_pages work bound on this server (VAMS_MAX_PAGES={CONFIG.max_pages})")
        result["note"] = (f"stopped after {pages} page(s) at {bound}; more result text remains — "
                          f"pass NextToken as starting_token to continue")
    return result


# Log-source details trimmed from the description: a nested state machine's history line
# carries the logId of the group it writes to; a "deadlineCloudJob" source is read by the exact
# session streams of the job's 10 most recent sessions, never by prefix ("notFound" until it has
# a session); "skipped" is past the per-request registered-log cap; sfnHistoryEvents is served only
# on a tokenless first call. Reference: documentation/docusaurus-site/docs/api/workflows.md.
@mcp.tool(title="Get execution logs", annotations=READ)
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
    log_id: Optional[str] = None,
    stage_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Use to read a run's logs after get_execution_details. Administrative route: a role without it
    gets a 403, not empty output.

    mode="full" (default) reads live CloudWatch; "truncated" reads the stored copy (often empty).
    pipeline_execution_id scopes to one step; log_id (an `availableLogs` logId) reads ONE source and
    stage_name one stage, both in full mode with pipeline_execution_id only (an unknown log_id: 404).

    Full mode returns at most `limit` events (default 100, cap 1000) and a `nextToken` when more
    remain; a failure is often past the first page. Pass `nextToken` back with the SAME mode,
    pipeline_execution_id, limit and filter_pattern; it continues only the `events` list. "The logs
    show no error" from a page that returned a token is a wrong conclusion. `filter_pattern` is a
    literal substring; `start_time` / `end_time` are epoch MILLISECONDS.

    The first call without a step also returns `sfnHistoryEvents`. With a step, `logSources` gives
    each source's `status`; "error" names its cause in `warnings`, and "unscoped" and empty means
    unresolved, not silent."""
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
            ("logId", log_id),
            ("stageName", stage_name),
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


@mcp.tool(title="List asset comments", annotations=READ)
@tool_result
def list_asset_comments(
    asset_id: str,
    max_items: Optional[int] = None,
    page_size: Optional[int] = None,
    include_archived: bool = False,
) -> Dict[str, Any]:
    """Use to read the comments on an asset (every version). Rows: `assetVersionId:commentId`,
    commentBody, commentOwnerUsername, dateCreated (sort on it). include_archived also finds an
    archived asset. This route CANNOT be paged (the token is discarded): `truncated` means the result
    reached max_items (else page_size, else 10000) and the count is a floor. Deleted comments are never
    returned (showDeleted is ignored, so not offered)."""
    return _bounded_message_list(
        CLIENT.api.list_asset_comments(
            asset_id, max_items=max_items, page_size=page_size, show_archived=include_archived
        ),
        max_items,
        page_size,
        "comment",
    )


@mcp.tool(title="List version comments", annotations=READ)
@tool_result
def list_asset_version_comments(
    asset_id: str,
    asset_version_id: str,
    max_items: Optional[int] = None,
    page_size: Optional[int] = None,
    include_archived: bool = False,
) -> Dict[str, Any]:
    """Use to read the comments on ONE asset version. Same rows and unpageable bound as
    list_asset_comments() (the token is discarded): a `truncated` count is a floor. include_archived
    also finds an archived asset."""
    return _bounded_message_list(
        CLIENT.api.list_asset_version_comments(
            asset_id, asset_version_id, max_items=max_items, page_size=page_size,
            show_archived=include_archived,
        ),
        max_items,
        page_size,
        "comment",
    )


@mcp.tool(title="Get comment", annotations=READ)
@tool_result
def get_comment(asset_id: str, asset_version_id: str, comment_id: str) -> Dict[str, Any]:
    """Use to read one comment. The route answers 200 with an empty object for a missing comment, which
    comes back as a CommentNotFoundError in `error`. Neither id may contain a colon."""
    return CLIENT.unwrap_message(CLIENT.api.get_comment(asset_id, asset_version_id, comment_id))


@mcp.tool(title="List subscriptions", annotations=READ)
@tool_result
def list_subscriptions(
    max_items: Optional[int] = None,
    starting_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Use to see e-mail event subscriptions (eventName, entityName, entityId, subscribers).
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue.
    For one asset and user use check_subscription()."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_subscriptions(
            page_size=params["pageSize"],
            starting_token=params.get("startingToken"),
        ),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="Check subscription", annotations=READ)
@tool_result
def check_subscription(asset_id: str, user_id: str) -> Dict[str, Any]:
    """Use to tell whether one user is subscribed to an asset's version changes: `subscribed`. The route
    answers 200 in BOTH cases; `unrecognizedResponse` set means the verdict is unknown."""
    response = CLIENT.api.check_subscription(asset_id, user_id)
    message = response.get("message") if isinstance(response, dict) else response
    result: Dict[str, Any] = {"subscribed": message == SUBSCRIBED_MESSAGE, "message": message}
    if message not in (SUBSCRIBED_MESSAGE, NOT_SUBSCRIBED_MESSAGE):
        result["unrecognizedResponse"] = True
    return result


@mcp.tool(title="Get API key (admin)", annotations=READ)
@tool_result
def get_api_key(api_key_id: str) -> Dict[str, Any]:
    """Use to read one API key's record (admin scope; id from list_api_keys()): apiKeyName, the userId it
    acts as, expiry, enabled. The key VALUE is shown once at creation and never again."""
    return CLIENT.api.get_api_key(api_key_id)


@mcp.tool(title="Get my API key", annotations=READ)
@tool_result
def get_user_api_key(api_key_id: str) -> Dict[str, Any]:
    """Use to read one of the signed-in user's own API key records (no key value)."""
    return CLIENT.api.get_user_api_key(api_key_id)


@mcp.tool(title="List API keys (admin)", annotations=READ)
@tool_result
def list_api_keys(
    max_items: Optional[int] = None, starting_token: Optional[str] = None
) -> Dict[str, Any]:
    """Use to audit every user's API keys (admin scope). Key INVENTORY only: apiKeyId, apiKeyName,
    userId, expiry, enabled.
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue.
    Never report a count from a truncated result."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_api_keys(
            page_size=params.get("pageSize"), starting_token=params.get("startingToken")
        ),
        max_items=max_items,
        starting_token=starting_token,
    )


@mcp.tool(title="List my API keys", annotations=READ)
@tool_result
def list_user_api_keys(
    max_items: Optional[int] = None, starting_token: Optional[str] = None
) -> Dict[str, Any]:
    """Use to list the signed-in user's own API keys. Other users' keys are never included, so an empty
    result says nothing about whether they exist.
    BOUNDED: `truncated` means rows were not seen; pass `NextToken` as `starting_token` to continue.
    Never report a count from a truncated result."""
    return CLIENT.paginate(
        lambda params: CLIENT.api.list_user_api_keys(
            page_size=params.get("pageSize"), starting_token=params.get("startingToken")
        ),
        max_items=max_items,
        starting_token=starting_token,
    )


# =========================================================================
# WRITE TOOLS (require VAMS_ENABLE_WRITES=true)
# =========================================================================

if CONFIG.enable_writes:

    @mcp.tool(title="Create database", annotations=WRITE)
    @tool_result
    def create_database(database_id: str, description: str, default_bucket_id: str) -> Dict[str, Any]:
        """Use to create a database: database_id, description, default_bucket_id (from list_buckets())."""
        return CLIENT.api.create_database(
            {
                "databaseId": database_id,
                "description": description,
                "defaultBucketId": default_bucket_id,
            }
        )

    @mcp.tool(title="Create asset", annotations=WRITE)
    @tool_result
    def create_asset(
        database_id: str,
        asset_name: str,
        description: str,
        is_distributable: bool = False,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Use to create an asset (no files); VAMS generates the `assetId`. description: at least 4
        characters. is_distributable (default False) allows downloads."""
        payload: Dict[str, Any] = {
            "databaseId": database_id,
            "assetName": asset_name,
            "description": description,
            "isDistributable": is_distributable,
            "tags": tags or [],
        }
        return CLIENT.api.create_asset(payload)

    @mcp.tool(title="Update asset", annotations=WRITE)
    @tool_result
    def update_asset(database_id: str, asset_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Use to change an asset's assetName, description, isDistributable or tags (at least one). A field
        sent as null is left unchanged; tags=[] clears the tag list."""
        return CLIENT.api.update_asset(database_id, asset_id, updates)

    @mcp.tool(title="Set asset metadata", annotations=WRITE)
    @tool_result
    def set_asset_metadata(
        database_id: str,
        asset_id: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Use to upsert metadata key/value pairs on an asset; keys not listed are untouched."""
        items = [
            {"metadataKey": k, "metadataValue": str(v), "metadataValueType": "string"}
            for k, v in metadata.items()
        ]
        return CLIENT.api.update_asset_metadata_v2(database_id, asset_id, items, update_type="update")

    @mcp.tool(title="Create folder", annotations=WRITE)
    @tool_result
    def create_folder(database_id: str, asset_id: str, folder_path: str) -> Dict[str, Any]:
        """Use to create a folder in an asset; folder_path is relative and ends with a slash."""
        relative_key = folder_path if folder_path.endswith("/") else f"{folder_path}/"
        return CLIENT.api.create_folder(database_id, asset_id, {"relativeKey": relative_key})

    @mcp.tool(title="Create asset version", annotations=WRITE)
    @tool_result
    def create_asset_version(
        database_id: str,
        asset_id: str,
        comment: str,
        version_alias: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Use to snapshot an asset's current files as a version; comment required (1-256 characters)."""
        payload: Dict[str, Any] = {"useLatestFiles": True, "comment": comment}
        if version_alias:
            payload["versionAlias"] = version_alias
        return CLIENT.api.create_asset_version(database_id, asset_id, payload)

    @mcp.tool(title="Move file", annotations=WRITE)
    @tool_result
    def move_file(database_id: str, asset_id: str, source_path: str, destination_path: str) -> Dict[str, Any]:
        """Use to move or rename a file inside its asset (relativePaths). Returns affectedFiles; the
        source stays as an archived entry."""
        return CLIENT.api.move_file(
            database_id, asset_id, {"sourcePath": source_path, "destinationPath": destination_path}
        )

    @mcp.tool(title="Copy file", annotations=WRITE)
    @tool_result
    def copy_file(
        database_id: str,
        asset_id: str,
        source_path: str,
        destination_path: str,
        destination_asset_id: Optional[str] = None,
        destination_database_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Use to copy a file within its asset or into destination_asset_id (destination_database_id
        defaults to the source's). Returns affectedFiles."""
        payload: Dict[str, Any] = {"sourcePath": source_path, "destinationPath": destination_path}
        if destination_asset_id:
            payload["destinationAssetId"] = destination_asset_id
        if destination_database_id:
            payload["destinationDatabaseId"] = destination_database_id
        return CLIENT.api.copy_file(database_id, asset_id, payload)

    @mcp.tool(title="Link assets", annotations=WRITE)
    @tool_result
    def create_asset_link(
        from_database_id: str,
        from_asset_id: str,
        to_database_id: str,
        to_asset_id: str,
        relationship_type: str,
        alias_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Use to link two assets: relationship_type 'related' or 'parentChild' (from = parent; cycles
        refused; alias_id adds a second parentChild link). Returns assetLinkId."""
        payload: Dict[str, Any] = {
            "fromAssetId": from_asset_id,
            "fromAssetDatabaseId": from_database_id,
            "toAssetId": to_asset_id,
            "toAssetDatabaseId": to_database_id,
            "relationshipType": relationship_type,
            "tags": tags or [],
        }
        if alias_id:
            payload["assetLinkAliasId"] = alias_id
        return CLIENT.api.create_asset_link(payload)

    # ---------------------------------------------------------------------
    # Tag / tag-type writes
    # ---------------------------------------------------------------------

    @mcp.tool(title="Create tag", annotations=WRITE)
    @tool_result
    def create_tag(
        tag_name: str, description: str, tag_type_name: str, database_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Use to create a tag of an existing tag type; database_id scopes it (omit: global). Relay
        `warnings`."""
        return CLIENT.api.create_tags(
            _tag_body({"tagName": tag_name, "description": description, "tagTypeName": tag_type_name}, database_id)
        )

    @mcp.tool(title="Update tag", annotations=WRITE)
    @tool_result
    def update_tag(
        tag_name: str, description: str, tag_type_name: str, database_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Use to change a tag's description or tag type; both are REPLACED, so resend the kept one
        (list_tags)."""
        return CLIENT.api.update_tags(
            _tag_body({"tagName": tag_name, "description": description, "tagTypeName": tag_type_name}, database_id)
        )

    @mcp.tool(title="Create tag type", annotations=WRITE)
    @tool_result
    def create_tag_type(
        tag_type_name: str, description: str, required: bool = False, database_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Use to create a tag type (tag group); required=True makes assets need one of its tags."""
        return CLIENT.api.create_tag_types(
            _tag_body(
                {"tagTypeName": tag_type_name, "description": description, "required": "True" if required else "False"},
                database_id,
            )
        )

    @mcp.tool(title="Update tag type", annotations=WRITE)
    @tool_result
    def update_tag_type(
        tag_type_name: str, description: str, required: bool, database_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Use to change a tag type's description and required flag; both are REPLACED."""
        return CLIENT.api.update_tag_types(
            _tag_body(
                {"tagTypeName": tag_type_name, "description": description, "required": "True" if required else "False"},
                database_id,
            )
        )


    # ---------------------------------------------------------------------
    # Pipeline / workflow / execution writes
    # ---------------------------------------------------------------------

    @mcp.tool(title="Create pipeline", annotations=WRITE)
    @tool_result
    def create_pipeline(database_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Use to create a pipeline. `body` is the full definition in the API's shape (pipelineId,
        pipelineName, executionConfig, systemConfig); copy an existing one from get_pipeline() first.
        systemConfig.metadataInputs is a boolean map of exactly six keys: assetMetadata, fileMetadata,
        fileAttributes, databaseMetadata (default true), assetFileMetadata and metadataSchema (default
        false).

        A successful save can still carry a `warnings` array (e.g. a requireTemplate pipeline in an
        auto-triggered workflow with no default template). The pipeline saved; relay the warnings rather
        than reporting a clean success."""
        return _unwrap_message_with_warnings(CLIENT.api.create_pipeline(database_id, body))

    @mcp.tool(title="Update pipeline", annotations=WRITE)
    @tool_result
    def update_pipeline(database_id: str, pipeline_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Use to change a pipeline; only fields in `body` change. Relay the save `warnings` (as
        create_pipeline), including that referencing workflows are being redeployed.
        System pipelines (isSystem) are read-only except enabled and the templates' configBody,
        tagSchema and webFormJson (400 otherwise)."""
        return _unwrap_message_with_warnings(CLIENT.api.update_pipeline(database_id, pipeline_id, body))

    @mcp.tool(title="Create pipeline template", annotations=WRITE)
    @tool_result
    def create_pipeline_template(
        database_id: str, pipeline_id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Use to add a configuration template to a pipeline. `body` takes templateId, templateName,
        configFormat, configBody, and optionally inputInstructions, tagSchema and overrides (at most 65536
        bytes; may narrow metadataInputs (assetMetadata, fileMetadata, fileAttributes, databaseMetadata,
        assetFileMetadata, metadataSchema)). A `tagSchema`
        entry carries only tagKey, type, required, default, label, description and enumValues.
        System pipelines (isSystem) are read-only except enabled and the templates' configBody,
        tagSchema and webFormJson (400 otherwise)."""
        return CLIENT.unwrap_message(CLIENT.api.create_pipeline_template(database_id, pipeline_id, body))

    @mcp.tool(title="Update pipeline template", annotations=WRITE)
    @tool_result
    def update_pipeline_template(
        database_id: str, pipeline_id: str, template_id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Use to change a pipeline template; only fields in `body` change; create_pipeline_template's rules
        apply. System pipelines (isSystem) are read-only except enabled and the templates' configBody,
        tagSchema and webFormJson (400 otherwise)."""
        return CLIENT.unwrap_message(CLIENT.api.update_pipeline_template(database_id, pipeline_id, template_id, body))

    @mcp.tool(title="Replace template tag schema", annotations=WRITE)
    @tool_result
    def set_pipeline_template_tag_schema(
        database_id: str, pipeline_id: str, template_id: str, fields: list
    ) -> Dict[str, Any]:
        """Use to REPLACE a template's whole tag schema. Entries carry only tagKey, type, required, default,
        label, description and enumValues; any other key is rejected.
        System pipelines (isSystem) are read-only except enabled and the templates' configBody,
        tagSchema and webFormJson (400 otherwise)."""
        return CLIENT.unwrap_message(
            CLIENT.api.set_pipeline_template_tag_schema(
                database_id, pipeline_id, template_id, fields
            )
        )

    @mcp.tool(title="Create workflow", annotations=WRITE)
    @tool_result
    def create_workflow(database_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Use to create a workflow from pipelines. `body` takes workflowId, workflowName,
        specifiedPipelines (ordered; no pipeline twice) and systemConfig, including
        metadataInputs (assetMetadata, fileMetadata, fileAttributes, databaseMetadata,
        assetFileMetadata, metadataSchema).
        concurrencyRestriction (none, perAsset, perInputFile, perInputFileVersion) locks that scope while
        a run lasts."""
        return CLIENT.unwrap_message(CLIENT.api.create_workflow(database_id, body))

    @mcp.tool(title="Update workflow", annotations=WRITE)
    @tool_result
    def update_workflow(database_id: str, workflow_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Use to change a workflow; only fields in `body` change.
        System workflows (isSystem) are read-only (400) except the toggles
        update_workflow(..., {"enabled": false}) and set_workflow_trigger(..., {"enabled": false})."""
        return CLIENT.unwrap_message(CLIENT.api.update_workflow(database_id, workflow_id, body))

    @mcp.tool(title="Set workflow trigger", annotations=WRITE)
    @tool_result
    def set_workflow_trigger(
        database_id: str, workflow_id: str, trigger_type: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Use to create or replace a workflow trigger (PUT-idempotent). `trigger_type` is the trigger KEY:
        the bare type for the first trigger of a type, 'type#triggerId' for another (a NEW id creates
        one). `body` takes enabled plus settings such as inputFileFilters. Refused with 400: a
        match-everything exclude, an archived workflow, an extra trigger under perAsset, or an extra one
        with the same defaultTemplateIds. A system workflow (isSystem) changes only {"enabled"}."""
        return CLIENT.unwrap_message(CLIENT.api.set_workflow_trigger(database_id, workflow_id, trigger_type, body))

    @mcp.tool(title="Run workflow", annotations=COMPUTE)
    @tool_result
    def execute_workflow(
        workflow_database_id: str, workflow_id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Use to run a workflow on input files. Returns the new executionId. This starts real compute.
        Confirm with the user before calling it.

        `body` takes inputFiles (each databaseId, assetId, relativeFileKey ("/" = the whole asset) and
        optional versionId), outputAssetId + outputDatabaseId (REQUIRED when inputs span more than one
        asset), and per-pipeline parameters keyed by pipelineId. Optional metadata sources:
        metadataSourceAssets [{"databaseId", "assetId"}], and metadataSourceDatabaseId for a run with NO
        input files ("GLOBAL" is REJECTED).

        Relay the response's `warnings`: the run started, but its inputs are not exactly what was named.
        A concurrencyRestriction (perAsset, perInputFile, perInputFileVersion) refuses a launch on a held
        scope: error_type WorkflowAlreadyRunningError. Nothing started; launch again later."""
        return CLIENT.unwrap_message(CLIENT.api.execute_workflow(workflow_database_id, workflow_id, body))

    @mcp.tool(title="Re-run execution", annotations=COMPUTE)
    @tool_result
    def rerun_execution(
        execution_id: str, execution_group_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Use to re-run ONE execution from its stored inputs, as a NEW execution, with the CALLER's
        permissions: returns the new id. This starts real compute; confirm with the user first.

        execution_group_id ASSIGNS the new run's group; it does not select what to re-run. There is no
        re-run-the-whole-group operation: call this once per execution id. A file the original launch
        pinned is read at that version again, every other file at its current version (executions from
        before 2.7.0 use current versions). Relay the response's `warnings`."""
        return CLIENT.unwrap_message(CLIENT.api.rerun_execution(execution_id, execution_group_id=execution_group_id))

    @mcp.tool(title="Abort execution", annotations=COMPUTE)
    @tool_result
    def abort_execution(execution_id: str, group_id: Optional[str] = None) -> Dict[str, Any]:
        """Use to stop a running execution: it STOPS running AWS compute (state machine, Batch job) and is
        NOT reversible. Keep it out of `autoApprove` alongside execute_workflow and rerun_execution;
        group_id fans out to every active execution of the group, so confirm a group abort first.

        When something could not be stopped the answer carries `warnings` and `notAbortable` (one
        {resourceType, label, stageName, locator} per sub-process; in a group abort each carries the
        member's `executionId`). The execution still reads ABORTED, so either array non-empty means
        compute is still running: do NOT conclude that everything stopped."""
        return _unwrap_message_with_warnings(
            CLIENT.api.abort_execution(execution_id, group_id=group_id), siblings=("warnings", "notAbortable"))

    # ---------------------------------------------------------------------
    # Comment / subscription / metadata-schema writes
    # ---------------------------------------------------------------------

    @mcp.tool(title="Add comment", annotations=WRITE)
    @tool_result
    def add_comment(
        asset_id: str,
        asset_version_id: str,
        comment_body: str,
        comment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Use to comment on one asset version; returns `commentId`. The write is UNCONDITIONAL: passing an
        existing `comment_id` REPLACES that comment with no error, so leave it unset (a uuid4 is
        generated). comment_body: up to 16384 characters."""
        resolved_id = comment_id or str(uuid.uuid4())
        response = CLIENT.api.add_comment(asset_id, asset_version_id, resolved_id, comment_body)
        result: Dict[str, Any] = {"commentId": resolved_id}
        if isinstance(response, dict):
            result.update(response)
        else:
            result["response"] = response
        return result

    @mcp.tool(title="Edit comment", annotations=WRITE)
    @tool_result
    def update_comment(
        asset_id: str, asset_version_id: str, comment_id: str, comment_body: str
    ) -> Dict[str, Any]:
        """Use to edit a comment's text. Only its CREATOR may (403 otherwise)."""
        return CLIENT.api.update_comment(asset_id, asset_version_id, comment_id, comment_body)

    @mcp.tool(title="Subscribe users", annotations=WRITE)
    @tool_result
    def create_subscription(
        entity_id: str,
        subscribers: List[str],
        event_name: str = SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
        entity_name: str = SUBSCRIPTION_ENTITY_ASSET,
    ) -> Dict[str, Any]:
        """Use to subscribe users (VAMS user IDs) to an entity's e-mail events (default: an asset's version
        changes). A user already subscribed is an ERROR, not a no-op, and rejects the whole call; check
        first, or use update_subscription()."""
        return CLIENT.api.create_subscription(event_name, entity_name, entity_id, subscribers)

    @mcp.tool(title="Replace subscribers", annotations=WRITE)
    @tool_result
    def update_subscription(
        entity_id: str,
        subscribers: List[str],
        event_name: str = SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
        entity_name: str = SUBSCRIPTION_ENTITY_ASSET,
    ) -> Dict[str, Any]:
        """Use to REPLACE a subscription's subscriber list; this is not an addition: every user not in
        `subscribers` is unsubscribed. To add one, read list_subscriptions() and send the full list."""
        return CLIENT.api.update_subscription(event_name, entity_name, entity_id, subscribers)

    @mcp.tool(title="Create metadata schema", annotations=WRITE)
    @tool_result
    def create_metadata_schema(schema_data: Dict[str, Any]) -> Dict[str, Any]:
        """Use to define a metadata schema: `schema_data` takes databaseId (or 'GLOBAL'),
        metadataSchemaEntityType, schemaName and fields NESTED as `{"fields": [ ... ]}`. Optional
        fileKeyTypeRestriction, each extension with its leading dot (".stp,.step"), else refused with a
        400. Returns metadataSchemaId."""
        return CLIENT.api.create_metadata_schema(schema_data)

    @mcp.tool(title="Update metadata schema", annotations=WRITE)
    @tool_result
    def update_metadata_schema(
        metadata_schema_id: str, update_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Use to change a metadata schema. `fields` REPLACES the whole list (send the full set, nested). A
        field sent as null is left unchanged; a null or "" fileKeyTypeRestriction removes the restriction.
        It takes the same dotted list as create, and only a fileMetadata or fileAttribute schema has one."""
        return CLIENT.api.update_metadata_schema(metadata_schema_id, update_data)

    @mcp.tool(title="Create API key for a user (admin)", annotations=WRITE)
    @tool_result
    def create_api_key(
        api_key_name: str, user_id: str, description: str, expires_at: Optional[str] = None
    ) -> Dict[str, Any]:
        """Use to create an API key that acts AS `user_id` (admin scope); prefer create_user_api_key(). The
        key VALUE (`apiKey`) is shown this only time: a bearer credential landing in the transcript. Keep
        this tool out of `autoApprove` and hand the value to the user at once. `expires_at`: ISO 8601."""
        payload: Dict[str, Any] = {
            "apiKeyName": api_key_name,
            "userId": user_id,
            "description": description,
        }
        if expires_at:
            payload["expiresAt"] = expires_at
        return CLIENT.api.create_api_key(payload)

    @mcp.tool(title="Create my API key", annotations=WRITE)
    @tool_result
    def create_user_api_key(api_key_name: str, description: str, expires_at: str) -> Dict[str, Any]:
        """Use to create an API key for the signed-in user. The key VALUE (`apiKey`) is shown this one time:
        keep this tool out of `autoApprove` and hand the value to the user at once. `expires_at` is
        required (ISO 8601, within 365 days)."""
        return CLIENT.api.create_user_api_key(
            {"apiKeyName": api_key_name, "description": description, "expiresAt": expires_at}
        )

    @mcp.tool(title="Update API key (admin)", annotations=WRITE)
    @tool_result
    def update_api_key(
        api_key_id: str,
        description: Optional[str] = None,
        expires_at: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Use to change an API key's description, expiry or enabled state (admin scope). `is_active=False`
        is the REVERSIBLE revoke; prefer it to delete_api_key(). Changes can take ~30 seconds (authorizer
        cache)."""
        payload: Dict[str, Any] = {}
        if description is not None:
            payload["description"] = description
        if expires_at is not None:
            payload["expiresAt"] = expires_at
        if is_active is not None:
            payload["isActive"] = "true" if is_active else "false"
        return CLIENT.api.update_api_key(api_key_id, payload)

    @mcp.tool(title="Update my API key", annotations=WRITE)
    @tool_result
    def update_user_api_key(
        api_key_id: str,
        description: Optional[str] = None,
        expires_at: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Use to change one of your own API keys, as update_api_key(); `is_active=False` is the reversible
        revoke. `expires_at` must stay within 365 days of creation."""
        payload: Dict[str, Any] = {}
        if description is not None:
            payload["description"] = description
        if expires_at is not None:
            payload["expiresAt"] = expires_at
        if is_active is not None:
            payload["isActive"] = "true" if is_active else "false"
        return CLIENT.api.update_user_api_key(api_key_id, payload)


# =========================================================================
# DESTRUCTIVE TOOLS (require VAMS_ENABLE_DESTRUCTIVE=true AND writes enabled)
# =========================================================================

if CONFIG.enable_destructive:

    @mcp.tool(title="Archive asset", annotations=DESTRUCTIVE)
    @tool_result
    def archive_asset(database_id: str, asset_id: str, reason: str = "") -> Dict[str, Any]:
        """Use to archive (soft-delete) an asset. Reversible via unarchive_asset."""
        return CLIENT.api.archive_asset(database_id, asset_id, reason=reason or None)

    @mcp.tool(title="Unarchive asset", annotations=DESTRUCTIVE)
    @tool_result
    def unarchive_asset(
        database_id: str,
        asset_id: str,
        reason: str = "",
        unarchive_files: bool = False,
    ) -> Dict[str, Any]:
        """Use to restore an archived asset; unarchive_files also restores its files."""
        return CLIENT.api.unarchive_asset(
            database_id, asset_id, reason=reason or None, unarchive_files=unarchive_files
        )

    @mcp.tool(title="Delete asset permanently", annotations=DESTRUCTIVE)
    @tool_result
    def delete_asset(database_id: str, asset_id: str, reason: str = "") -> Dict[str, Any]:
        """Use only when asked to PERMANENTLY delete an asset. Irreversible; archive_asset() is reversible."""
        # `confirmPermanentDelete` is a REQUIRED-TRUE field of the request contract, not an optional
        # second signal: DeleteAssetRequestModel declares an always=True validator that rejects any
        # other value, so sending true is the only way to perform the operation at all. The controls
        # on this tool are the destructive gate, its name, and this docstring.
        return CLIENT.api.delete_asset_permanent(database_id, asset_id, reason=reason or None, confirm=True)

    @mcp.tool(title="Delete database permanently", annotations=DESTRUCTIVE)
    @tool_result
    def delete_database(database_id: str) -> Dict[str, Any]:
        """Use only when asked to PERMANENTLY delete a database. Irreversible; refused while it holds
        assets, workflows or pipelines."""
        return CLIENT.api.delete_database(database_id)

    @mcp.tool(title="Archive file", annotations=DESTRUCTIVE)
    @tool_result
    def archive_file(database_id: str, asset_id: str, file_key: str, is_prefix: bool = False) -> Dict[str, Any]:
        """Use to archive (soft-delete) a file (is_prefix: a whole folder). Reversible via
        unarchive_file."""
        return CLIENT.api.archive_file(database_id, asset_id, {"filePath": file_key, "isPrefix": is_prefix})

    @mcp.tool(title="Unarchive file", annotations=DESTRUCTIVE)
    @tool_result
    def unarchive_file(database_id: str, asset_id: str, file_key: str) -> Dict[str, Any]:
        """Use to restore an archived file; file_key from list_asset_files(include_archived)."""
        return CLIENT.api.unarchive_file(database_id, asset_id, {"filePath": file_key})

    @mcp.tool(title="Delete file permanently", annotations=DESTRUCTIVE)
    @tool_result
    def delete_file(database_id: str, asset_id: str, file_key: str, is_prefix: bool = False) -> Dict[str, Any]:
        """Use only when asked to PERMANENTLY delete a file with all versions (is_prefix: a whole folder).
        Irreversible; archive_file() is reversible."""
        # `confirmPermanentDelete` is required to be true by the delete route itself (it refuses any
        # other value), so it is part of the request contract, not an interlock to surface. The
        # controls on this tool are the destructive gate, its name, and this docstring.
        return CLIENT.api.delete_file(
            database_id, asset_id, {"filePath": file_key, "isPrefix": is_prefix, "confirmPermanentDelete": True}
        )

    @mcp.tool(title="Delete asset link", annotations=DESTRUCTIVE)
    @tool_result
    def delete_asset_link(asset_link_id: str) -> Dict[str, Any]:
        """Use to remove an asset link (assetLinkId from get_asset_links); the assets stay. Not
        reversible."""
        return CLIENT.api.delete_asset_link(asset_link_id)

    @mcp.tool(title="Delete tag", annotations=DESTRUCTIVE)
    @tool_result
    def delete_tag(tag_name: str, database_id: Optional[str] = None) -> Dict[str, Any]:
        """Use only when asked to delete a tag (database_id: a database's). Not reversible."""
        return CLIENT.api.delete_tag(tag_name, database_id=database_id)

    @mcp.tool(title="Delete tag type", annotations=DESTRUCTIVE)
    @tool_result
    def delete_tag_type(tag_type_name: str, database_id: Optional[str] = None) -> Dict[str, Any]:
        """Use only when asked to delete a tag type; refused while tags use it. Not reversible."""
        return CLIENT.api.delete_tag_type(_tag_type_name(tag_type_name), database_id=database_id)

    # ---------------------------------------------------------------------
    # Pipeline / workflow / execution deletes
    # ---------------------------------------------------------------------

    @mcp.tool(title="Archive pipeline", annotations=DESTRUCTIVE)
    @tool_result
    def archive_pipeline(database_id: str, pipeline_id: str) -> Dict[str, Any]:
        """Use to archive (soft-delete) a pipeline. Reversible via unarchive_pipeline.
        System pipelines (isSystem) are read-only except enabled and the templates' configBody,
        tagSchema and webFormJson (400 otherwise)."""
        return CLIENT.unwrap_message(CLIENT.api.delete_pipeline(database_id, pipeline_id))

    @mcp.tool(title="Unarchive pipeline", annotations=DESTRUCTIVE)
    @tool_result
    def unarchive_pipeline(
        database_id: str, pipeline_id: str, keep_disabled: bool = False
    ) -> Dict[str, Any]:
        """Use to restore an archived pipeline, re-enabled (archiving also DISABLES; pass keep_disabled).
        System pipelines (isSystem) are read-only except enabled and the templates' configBody,
        tagSchema and webFormJson (400 otherwise)."""
        body: Dict[str, Any] = {"archived": False}
        if not keep_disabled:
            body["enabled"] = True
        return _unwrap_message_with_warnings(CLIENT.api.update_pipeline(database_id, pipeline_id, body))

    @mcp.tool(title="Archive workflow", annotations=DESTRUCTIVE)
    @tool_result
    def archive_workflow(database_id: str, workflow_id: str) -> Dict[str, Any]:
        """Use to archive (soft-delete) a workflow. Reversible via unarchive_workflow.
        System workflows (isSystem) are read-only (400) except the toggles
        update_workflow(..., {"enabled": false}) and set_workflow_trigger(..., {"enabled": false})."""
        return CLIENT.unwrap_message(CLIENT.api.delete_workflow(database_id, workflow_id))

    @mcp.tool(title="Unarchive workflow", annotations=DESTRUCTIVE)
    @tool_result
    def unarchive_workflow(
        database_id: str, workflow_id: str, keep_disabled: bool = False
    ) -> Dict[str, Any]:
        """Use to restore an archived workflow, re-enabled (archiving also DISABLES; pass keep_disabled).
        System workflows (isSystem) are read-only (400) except the toggles
        update_workflow(..., {"enabled": false}) and set_workflow_trigger(..., {"enabled": false})."""
        body: Dict[str, Any] = {"archived": False}
        if not keep_disabled:
            body["enabled"] = True
        return CLIENT.unwrap_message(CLIENT.api.update_workflow(database_id, workflow_id, body))

    @mcp.tool(title="Delete pipeline template", annotations=DESTRUCTIVE)
    @tool_result
    def delete_pipeline_template(
        database_id: str, pipeline_id: str, template_id: str
    ) -> Dict[str, Any]:
        """Use to delete a pipeline template. Not reversible. Relay any `warnings`: a trigger still names it
        as a default, so those runs fail until the trigger is changed.
        System pipelines (isSystem) are read-only except enabled and the templates' configBody,
        tagSchema and webFormJson (400 otherwise)."""
        return CLIENT.unwrap_message(CLIENT.api.delete_pipeline_template(database_id, pipeline_id, template_id))

    @mcp.tool(title="Delete workflow trigger", annotations=DESTRUCTIVE)
    @tool_result
    def delete_workflow_trigger(
        database_id: str, workflow_id: str, trigger_type: str
    ) -> Dict[str, Any]:
        """Use to delete a workflow trigger, so the workflow stops firing automatically.
        System workflows (isSystem) are read-only (400) except the toggles
        update_workflow(..., {"enabled": false}) and set_workflow_trigger(..., {"enabled": false})."""
        return CLIENT.unwrap_message(CLIENT.api.delete_workflow_trigger(database_id, workflow_id, trigger_type))

    @mcp.tool(title="Delete execution record", annotations=DESTRUCTIVE)
    @tool_result
    def permanent_delete_execution(execution_id: str) -> Dict[str, Any]:
        """Use only when asked to PERMANENTLY delete an execution's records. IRREVERSIBLE; refused while it
        runs. Its output files stay."""
        return CLIENT.unwrap_message(CLIENT.api.permanent_delete_execution(execution_id))

    # ---------------------------------------------------------------------
    # Comment / subscription / metadata-schema deletes
    # ---------------------------------------------------------------------

    @mcp.tool(title="Delete comment", annotations=DESTRUCTIVE)
    @tool_result
    def delete_comment(asset_id: str, asset_version_id: str, comment_id: str) -> Dict[str, Any]:
        """Use to delete a comment (unrecoverable through these tools). Only its CREATOR may."""
        return CLIENT.api.delete_comment(asset_id, asset_version_id, comment_id)

    @mcp.tool(title="Delete subscription", annotations=DESTRUCTIVE)
    @tool_result
    def delete_subscription(
        entity_id: str,
        subscribers: List[str],
        event_name: str = SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
        entity_name: str = SUBSCRIPTION_ENTITY_ASSET,
    ) -> Dict[str, Any]:
        """Use to delete a WHOLE subscription, every subscriber and the asset's notification topic;
        `subscribers` is required but the endpoint IGNORES it. To remove one user use unsubscribe()."""
        return CLIENT.api.delete_subscription(event_name, entity_name, entity_id, subscribers)

    @mcp.tool(title="Unsubscribe user", annotations=DESTRUCTIVE)
    @tool_result
    def unsubscribe(
        entity_id: str,
        subscriber: str,
        event_name: str = SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
        entity_name: str = SUBSCRIPTION_ENTITY_ASSET,
    ) -> Dict[str, Any]:
        """Use to remove ONE subscriber and keep the subscription (delete_subscription() removes it all)."""
        return CLIENT.api.unsubscribe(event_name, entity_name, entity_id, subscriber)

    @mcp.tool(title="Delete metadata schema", annotations=DESTRUCTIVE)
    @tool_result
    def delete_metadata_schema(database_id: str, metadata_schema_id: str) -> Dict[str, Any]:
        """Use only when asked to PERMANENTLY delete a metadata schema. Irreversible; stored metadata stays."""
        # `confirmDelete` is a REQUIRED-TRUE field of the request contract, not an optional interlock:
        # DeleteMetadataSchemaRequestModel declares an always=True validator that rejects any other
        # value, so the APIClient always sends true and there is nothing to surface as a parameter.
        # The controls on this tool are the destructive gate, its name, and this docstring.
        return CLIENT.api.delete_metadata_schema(database_id, metadata_schema_id)

    @mcp.tool(title="Delete API key (admin)", annotations=DESTRUCTIVE)
    @tool_result
    def delete_api_key(api_key_id: str) -> Dict[str, Any]:
        """Use only when asked to PERMANENTLY delete an API key (admin scope). Irreversible; consider
        update_api_key(is_active=False), which can be undone."""
        return CLIENT.api.delete_api_key(api_key_id)

    @mcp.tool(title="Delete my API key", annotations=DESTRUCTIVE)
    @tool_result
    def delete_user_api_key(api_key_id: str) -> Dict[str, Any]:
        """Use only when asked to PERMANENTLY delete one of your own API keys. Irreversible; consider
        update_user_api_key(is_active=False)."""
        return CLIENT.api.delete_user_api_key(api_key_id)


# =========================================================================
# ADD-ON TOOLS (upstream add-on lane: vams_mcp/addon_tools/)
# =========================================================================

register_addon_tools(mcp, CLIENT, CONFIG, tool_result)
# Which add-ons registered tools at start (vams_status reports it; a later sign-in does not add them).
_ADDONS_REGISTERED = {module: addon_deployed(CLIENT, module) for module in ADDON_TOOL_MODULES}


# =========================================================================
# CUSTOM TOOLS (fork extension point: vams_mcp/custom_tools.py, shipped empty)
# =========================================================================

register_custom_tools(mcp, CLIENT, CONFIG, tool_result)


def main() -> None:
    """Entry point: run the MCP server over stdio.

    Starts even when no profile is signed in: the tools then answer NotAuthenticated (and vams_status
    says why) instead of the server exiting with no tools, which left an agent nothing to call but a
    shell. The reason goes to stderr for the person who configured the host.
    """
    if CLIENT is None:
        logging.getLogger("vams_mcp").warning(
            "[vams-mcp] starting without a VAMS sign-in: %s", STARTUP_ERROR
        )
    mcp.run()


if __name__ == "__main__":
    main()
