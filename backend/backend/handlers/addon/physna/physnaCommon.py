# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Shared helpers for the Physna Sync add-on Lambdas.

Pure helpers in this module have no AWS or network dependencies and are unit
tested directly. Network-dependent components (PhysnaClient, Secrets Manager
loader) are added in later tasks.
"""

import os
from typing import Any, Dict, Optional

from common.dynamoDbMetadataKeys import is_excluded_metadata_record


# ---------------------------------------------------------------------------
# VAMS-reserved metadata keys written onto every synced Physna asset.
#
# These are tracking fields VAMS owns. They are always overwritten from
# authoritative VAMS sources (assetName from the asset record; S3 version id
# from the uploaded object) and win over any same-named keys users might have
# set in VAMS metadata. This is deliberate: the whole point of these keys is
# to reflect VAMS-side truth, not user-entered metadata.
#
# __VAMS__FileVersion specifically is the S3 VersionId of the object that was
# last uploaded to Physna for that path. It is NOT updated on metadata-only
# changes — it only changes when a new file is pushed to Physna, so we can
# tell "is Physna's copy the current S3 version?" by comparing this field
# against the current S3 VersionId before deciding whether to re-upload.
# ---------------------------------------------------------------------------
VAMS_RESERVED_ASSET_NAME_KEY = "__VAMS__AssetName"
VAMS_RESERVED_FILE_VERSION_KEY = "__VAMS__FileVersion"

# File-attribute keys are prefixed before being written to Physna so they
# cannot collide with same-named metadata keys on the same asset (e.g., an
# asset-level "author" metadata field and a file-level "author" attribute
# should both survive as distinct Physna fields). This prefix is also used
# by the prune-stale logic so keys already present on the Physna side with
# this prefix are correctly identified as attribute-derived.
PHYSNA_FILE_ATTRIBUTE_PREFIX = "Attribute_"

VAMS_RESERVED_METADATA_KEYS = frozenset(
    {
        VAMS_RESERVED_ASSET_NAME_KEY,
        VAMS_RESERVED_FILE_VERSION_KEY,
    }
)

# System type identifier for outbound sync tracking records.
SYNC_SYSTEM_TYPE = "physna"


def apply_vams_reserved_metadata(
    metadata_payload: Dict[str, Any],
    asset_name: Optional[str],
    file_version: Optional[str],
) -> Dict[str, Any]:
    """Overlay VAMS-reserved keys onto a metadata payload.

    Reserved keys overwrite any user-supplied key with the same name — VAMS
    tracking fields always reflect VAMS truth, not user metadata. Passing
    ``None`` for either value leaves that reserved key out of the payload
    (caller is responsible for deciding when each value is known).

    The payload is mutated in place and also returned for chaining.
    """
    if asset_name is not None:
        metadata_payload[VAMS_RESERVED_ASSET_NAME_KEY] = str(asset_name)
    if file_version is not None:
        metadata_payload[VAMS_RESERVED_FILE_VERSION_KEY] = str(file_version)
    return metadata_payload


# ---------------------------------------------------------------------------
# Physna file-extension gates.
#
# There are TWO distinct gates here because "what VAMS uploads to Physna" and
# "what the Physna Viewer can render inside VAMS" are different sets:
#
#   * SYNC  (VIEWER_SUPPORTED_EXTENSIONS ∪ documents ∪ images) — every format
#     Physna's upload endpoint accepts. The sync path pushes all of these to
#     Physna so they are indexed and searchable in the customer's tenant.
#   * VIEWER (3D/CAD only) — only the geometry formats Physna's embedded 3D
#     viewer can render. Documents and images are synced for search/indexing
#     but are NOT shown through the Physna Viewer (VAMS has dedicated PDF/
#     image/text viewers for those).
#
# Each list requires a code change to extend — we want the gates explicit.
# The upload endpoint validates the `path` extension server-side and rejects
# any extension it does not accept with HTTP 400 "Invalid path extension", so
# listing an extension in the SYNC set that Physna does not accept causes
# every such file to fail the sync. Physna's accepted set (per their docs):
#   3D/CAD: .3ds .asm .catpart .catproduct .glb .iam .iges .igs .ipt .jt .obj
#           .par .prt .sldasm .sldprt .stl .step .stp .x_b .x_t
#   Document: .txt .pdf
#   Image: .gif .jpeg .jpg .png
# Notably Physna does NOT accept .ifc, .ply, .sat, .3mf, .fbx, .dae, .dwg,
# .dxf, or .gltf (only the binary .glb form).
# ---------------------------------------------------------------------------

# 3D/CAD geometry formats — these are the only formats the embedded Physna
# Viewer can render, so this set gates the viewer proxy (physnaViewer).
VIEWER_SUPPORTED_EXTENSIONS = frozenset(
    {
        "3ds",
        "asm",
        "catpart",
        "catproduct",
        "glb",
        "iam",
        "iges",
        "igs",
        "ipt",
        "jt",
        "obj",
        "par",
        "prt",
        "sldasm",
        "sldprt",
        "stl",
        "step",
        "stp",
        "x_b",
        "x_t",
    }
)

# Document and image formats Physna accepts for upload/indexing but that the
# Physna Viewer does not render.
DOCUMENT_SUPPORTED_EXTENSIONS = frozenset({"txt", "pdf"})
IMAGE_SUPPORTED_EXTENSIONS = frozenset({"gif", "jpeg", "jpg", "png"})

# Full set VAMS uploads to Physna: geometry + documents + images.
SYNC_SUPPORTED_EXTENSIONS = (
    VIEWER_SUPPORTED_EXTENSIONS
    | DOCUMENT_SUPPORTED_EXTENSIONS
    | IMAGE_SUPPORTED_EXTENSIONS
)


# Physna-accepted metadata value types. Everything else falls back to string.
_PHYSNA_NATIVE_TYPES = frozenset({"string", "number", "boolean", "date"})


def _file_extension(file_path: str) -> Optional[str]:
    """Return the lowercased extension of a file path, or None if it has none."""
    if not file_path:
        return None
    base = os.path.basename(file_path)
    if "." not in base:
        return None
    return base.rsplit(".", 1)[-1].lower()


def is_sync_supported_file(file_path: str) -> bool:
    """Return True if the file extension is in the Physna upload/sync set.

    This is the gate the sync Lambda (physnaFileSync) uses to decide whether
    to push a file to Physna. It is broader than the viewer gate — it includes
    documents and images that Physna indexes but does not render in its viewer.
    """
    ext = _file_extension(file_path)
    return ext is not None and ext in SYNC_SUPPORTED_EXTENSIONS


def is_viewer_supported_file(file_path: str) -> bool:
    """Return True if the file extension can be rendered by the Physna Viewer.

    Only 3D/CAD geometry formats qualify. Documents and images are synced to
    Physna but shown through VAMS's own PDF/image/text viewers, not the
    embedded Physna Viewer.
    """
    ext = _file_extension(file_path)
    return ext is not None and ext in VIEWER_SUPPORTED_EXTENSIONS


def _normalize_relative(relative_path: str) -> str:
    """Strip leading slashes. Internal helper."""
    return relative_path.lstrip("/") if relative_path else ""


def build_physna_path(database_id: str, asset_id: str, relative_path: str) -> str:
    """Build the Physna asset path: '{databaseId}/{assetId}/{relativePath}'.

    Leading slashes on the relative path are stripped so the result never has
    a doubled slash.
    """
    return f"{database_id}/{asset_id}/{_normalize_relative(relative_path)}"


def build_physna_folder_path(
    database_id: str, asset_id: str, relative_path: str
) -> str:
    """Build the Physna parent-folder path for a file (everything but the filename)."""
    full = build_physna_path(database_id, asset_id, relative_path)
    if "/" not in full:
        return ""
    return full.rsplit("/", 1)[0]


def build_physna_filename(relative_path: str) -> str:
    """Extract the filename from a relative file path."""
    return os.path.basename(_normalize_relative(relative_path))


def merge_metadata(
    asset_metadata: Optional[Dict[str, Dict[str, str]]],
    file_metadata: Optional[Dict[str, Dict[str, str]]],
    file_attributes: Optional[Dict[str, Dict[str, str]]],
) -> Dict[str, Dict[str, str]]:
    """Merge three metadata dicts into the shape Physna stores on an asset.

    All three inputs use the shape ``{key: {"value": ..., "type": ...}}``.

    File-attribute keys are prefixed with
    :data:`PHYSNA_FILE_ATTRIBUTE_PREFIX` so they occupy their own namespace
    on the Physna side and cannot collide with same-named asset-level or
    file-level metadata keys. With the prefix in place, the only remaining
    cross-source collision is between ``asset_metadata`` and
    ``file_metadata``; on that conflict, ``file_metadata`` wins (it is the
    more specific source).
    """
    merged: Dict[str, Dict[str, str]] = {}
    if asset_metadata:
        for key, info in asset_metadata.items():
            merged[key] = info
    if file_attributes:
        for key, info in file_attributes.items():
            # Only add the prefix if it isn't already there — the prefix is
            # idempotent so callers that happen to pass pre-prefixed dicts
            # (e.g. when diffing against values we previously stored in
            # Physna) still round-trip safely.
            prefixed_key = (
                key
                if key.startswith(PHYSNA_FILE_ATTRIBUTE_PREFIX)
                else f"{PHYSNA_FILE_ATTRIBUTE_PREFIX}{key}"
            )
            merged[prefixed_key] = info
    if file_metadata:
        for key, info in file_metadata.items():
            merged[key] = info
    return merged


def map_vams_type_to_physna(vams_type: Optional[str]) -> str:
    """Map a VAMS metadata value type to a Physna-supported type.

    Anything Physna does not natively support falls back to ``"string"`` —
    callers are responsible for ensuring the value is also serialized to a
    string form when that happens.
    """
    if not vams_type:
        return "string"
    normalized = vams_type.lower()
    if normalized in _PHYSNA_NATIVE_TYPES:
        return normalized
    return "string"


def physna_format_metadata(merged: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """Convert the merged metadata dict to Physna's expected object shape.

    Physna's API expects metadata as a JSON **object** mapping key to value
    (``{"partName": "widget-01", "weightKg": 12}``). Earlier versions of this
    helper produced a list of ``{key, value, type}`` entries, which Physna
    rejects with ``Expected object, received array``.

    Values are coerced to strings when their VAMS type does not map to a
    Physna-native type (``number``, ``boolean``, ``date``) so the payload is
    JSON-safe and Physna stores them as strings.
    """
    formatted: Dict[str, Any] = {}
    for key, info in merged.items():
        raw_type = info.get("type") if isinstance(info, dict) else None
        physna_type = map_vams_type_to_physna(raw_type)
        value = info.get("value") if isinstance(info, dict) else info
        if physna_type == "string" and not isinstance(value, str):
            value = str(value)
        formatted[key] = value
    return formatted


import base64
import json
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import urllib3
from botocore.config import Config as BotoConfig

from customLogging.logger import safeLogger

logger = safeLogger(service_name="PhysnaCommon")

_retry_config = BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"})

# Lazy module-level singletons, kept across warm invocations.
_secretsmanager = None
_http = None
_cached_secret: Optional[Dict[str, str]] = None
_cached_token: Optional[str] = None
_token_expiry: Optional[datetime] = None

# Module-level config — read once at cold start.
try:
    PHYSNA_TENANT_ID = os.environ["PHYSNA_TENANT_ID"]
    PHYSNA_API_BASE = os.environ["PHYSNA_API_BASE"]
    PHYSNA_TOKEN_URL = os.environ["PHYSNA_TOKEN_URL"]
    PHYSNA_AUTH_TYPE = os.environ["PHYSNA_AUTH_TYPE"]
    PHYSNA_CREDS_SECRET_ARN = os.environ["PHYSNA_CREDS_SECRET_ARN"]
except KeyError as e:
    logger.warning(f"Physna env vars not set at import time (OK during test collection): {e}")
    PHYSNA_TENANT_ID = os.environ.get("PHYSNA_TENANT_ID", "")
    PHYSNA_API_BASE = os.environ.get("PHYSNA_API_BASE", "")
    PHYSNA_TOKEN_URL = os.environ.get("PHYSNA_TOKEN_URL", "")
    PHYSNA_AUTH_TYPE = os.environ.get("PHYSNA_AUTH_TYPE", "cognito")
    PHYSNA_CREDS_SECRET_ARN = os.environ.get("PHYSNA_CREDS_SECRET_ARN", "")


def get_sync_system_unique_id():
    """Identifies the target Physna environment + tenant for sync tracking."""
    return f"{PHYSNA_API_BASE}#{PHYSNA_TENANT_ID}"


class PhysnaError(Exception):
    """Base class for Physna client errors."""


class PhysnaAuthError(PhysnaError):
    """Raised when authentication fails after a token-refresh retry."""


class PhysnaApiError(PhysnaError):
    """Raised for non-2xx, non-401 API responses after retries."""


def _get_http_pool() -> urllib3.PoolManager:
    global _http
    if _http is None:
        _http = urllib3.PoolManager(timeout=urllib3.util.Timeout(connect=10.0, read=60.0))
    return _http


def _get_secretsmanager_client():
    global _secretsmanager
    if _secretsmanager is None:
        _secretsmanager = boto3.client("secretsmanager", config=_retry_config)
    return _secretsmanager


def _load_physna_credentials() -> Dict[str, str]:
    """Fetch the Physna credentials secret from Secrets Manager (cached)."""
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret
    sm = _get_secretsmanager_client()
    response = sm.get_secret_value(SecretId=PHYSNA_CREDS_SECRET_ARN)
    payload = json.loads(response["SecretString"])
    _cached_secret = {
        "clientId": payload["clientId"],
        "clientSecret": payload["clientSecret"],
    }
    return _cached_secret


def _http_post_token(client_id: str, client_secret: str):
    """Perform the OAuth2 client-credentials POST and return the response."""
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
    return _get_http_pool().request(
        "POST",
        PHYSNA_TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body="grant_type=client_credentials",
    )


def _http_request(method: str, url: str, **kwargs):
    """Thin wrapper around the module-level urllib3 pool.

    Split out so tests can monkeypatch a single seam.
    """
    return _get_http_pool().request(method, url, **kwargs)


def _reset_client_state_for_tests() -> None:
    """Test hook: clear module-level caches."""
    global _cached_secret, _cached_token, _token_expiry
    _cached_secret = None
    _cached_token = None
    _token_expiry = None


class PhysnaClient:
    """Authenticated client for the Physna REST API.

    Token lifecycle:
      - Tokens are cached in module memory across Lambda invocations.
      - Refresh is triggered when: (a) no cached token, (b) expiry within
        buffer, (c) a 401 comes back from any API call.
      - On 401 we retry exactly once after refreshing. A second 401 raises
        ``PhysnaAuthError``.
    """

    # Refresh a bit early so we never serve an expired token.
    _TOKEN_REFRESH_BUFFER_SECONDS = 60
    _MAX_TOTAL_RETRIES = 3

    def _ensure_token(self, force_refresh: bool = False) -> str:
        global _cached_token, _token_expiry
        now = datetime.now(timezone.utc)
        if (
            not force_refresh
            and _cached_token
            and _token_expiry
            and _token_expiry > now
        ):
            return _cached_token

        creds = _load_physna_credentials()
        response = _http_post_token(creds["clientId"], creds["clientSecret"])
        if response.status != 200:
            raise PhysnaAuthError(
                f"Token endpoint returned status {response.status}"
            )
        body = json.loads(response.data.decode("utf-8"))
        _cached_token = body["access_token"]
        expires_in = int(body.get("expires_in", 3600))
        _token_expiry = now + timedelta(
            seconds=max(0, expires_in - self._TOKEN_REFRESH_BUFFER_SECONDS)
        )
        return _cached_token

    def request(self, method: str, path: str, **kwargs: Any):
        """Send an authenticated request to the Physna API.

        path: either a relative path (starts with /) — which gets joined to
        PHYSNA_API_BASE — or a fully-qualified URL.
        """
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = PHYSNA_API_BASE.rstrip("/") + (path if path.startswith("/") else f"/{path}")

        last_error: Optional[Exception] = None
        attempted_refresh = False
        for attempt in range(self._MAX_TOTAL_RETRIES):
            token = self._ensure_token()
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("Authorization", f"Bearer {token}")
            headers.setdefault("Accept", "application/json")

            try:
                response = _http_request(method, url, headers=headers, **kwargs)
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Physna request {method} {url} failed (attempt {attempt + 1}): {e}"
                )
                time.sleep(min(2 ** attempt, 10))
                continue

            if response.status == 401:
                if attempted_refresh:
                    raise PhysnaAuthError(
                        f"Physna returned 401 after token refresh for {method} {url}"
                    )
                logger.warning(f"Physna returned 401 for {method} {url}; refreshing token")
                self._ensure_token(force_refresh=True)
                attempted_refresh = True
                # Re-inject kwargs for the next iteration
                kwargs["headers"] = headers
                continue

            if 500 <= response.status < 600:
                logger.warning(
                    f"Physna returned {response.status} for {method} {url} "
                    f"(attempt {attempt + 1}); retrying"
                )
                time.sleep(min(2 ** attempt, 10))
                continue

            return response

        if last_error is not None:
            raise PhysnaApiError(
                f"Physna request {method} {url} failed after retries: {last_error}"
            )
        raise PhysnaApiError(
            f"Physna request {method} {url} exhausted retries without success"
        )


_PHYSNA_LIST_PER_PAGE = 1000


def list_physna_assets_under(
    client: "PhysnaClient",
    tenant_id: str,
    path_prefix: str,
    client_side_filter: bool = True,
):
    """Yield Physna assets whose path starts with ``path_prefix``.

    Per Physna's v3 API (OpenAPI spec), ``GET /tenants/{tenantId}/assets``:

    - Paginates with 1-based ``page`` + ``perPage`` (max 1000); response is
      ``{"assets": [...], "pageData": {"currentPage", "lastPage", ...}}``.
    - Returns assets in ALL states, including ``indexing``, ``finished``,
      ``failed``, ``unsupported``, ``no-3d-data``, and ``missing-dependencies``.
    - Supports a ``folders`` query parameter (comma-separated folder paths)
      to narrow the result set to assets in specific folders.

    There is no server-side exact-path filter, so we narrow via the
    ``folders`` param to the parent folder of ``path_prefix`` when possible,
    then filter the results client-side to keep only items whose ``path``
    starts with ``path_prefix``.

    Set ``client_side_filter=False`` to disable the prefix filter — useful if
    callers only want items inside a specific folder regardless of exact
    string prefix.
    """
    # Determine the folder to pass as a Physna `folders` filter.
    # Physna's folder semantics: a folder path ends WITHOUT a trailing slash.
    if "/" in path_prefix:
        folder_filter = path_prefix.rsplit("/", 1)[0]
    else:
        folder_filter = None

    page = 1
    while True:
        query_parts = [f"page={page}", f"perPage={_PHYSNA_LIST_PER_PAGE}"]
        if folder_filter:
            query_parts.append(
                f"folders={urllib.parse.quote(folder_filter, safe='/')}"
            )
        url = f"/tenants/{tenant_id}/assets?{'&'.join(query_parts)}"

        response = client.request("GET", url)
        if response.status != 200:
            raise PhysnaApiError(
                f"list_physna_assets_under failed: status={response.status}: "
                f"{response.data!r}"
            )
        body = json.loads(response.data.decode("utf-8"))
        assets = body.get("assets") or body.get("items") or []
        for item in assets:
            if client_side_filter:
                item_path = item.get("path", "")
                if not item_path.startswith(path_prefix):
                    continue
            yield item

        page_data = body.get("pageData") or {}
        current_page = page_data.get("currentPage", page)
        last_page = page_data.get("lastPage", current_page)
        if current_page >= last_page:
            return
        page = current_page + 1


def _folder_delete_stub_callback(client: "PhysnaClient", tenant_id: str, folder: str) -> None:
    """Test seam for delete_folder_if_empty. Production no-op."""
    return None


def physna_asset_exists(client: "PhysnaClient", tenant_id: str, full_path: str) -> bool:
    """Return True if a Physna asset exists at the exact ``full_path``,
    regardless of its indexing state.

    Delegates to ``lookup_physna_asset_id`` (text-search). Asset existence
    reflects any state — an asset still in ``indexing`` is treated as
    present because it will become viewable once indexing completes.
    """
    return lookup_physna_asset_id(client, tenant_id, full_path) is not None


def get_physna_asset(
    client: "PhysnaClient", tenant_id: str, physna_asset_uuid: str
) -> Optional[Dict[str, Any]]:
    """Fetch a single Physna asset by UUID.

    Returns the ``asset`` dict from the response (which includes ``metadata``,
    ``state``, ``path``, etc.) or None on 404. This endpoint returns assets
    in any state, including ``indexing``.
    """
    response = client.request(
        "GET", f"/tenants/{tenant_id}/assets/{physna_asset_uuid}"
    )
    if response.status == 404:
        return None
    if response.status != 200:
        raise PhysnaApiError(
            f"GET asset {physna_asset_uuid} failed: status={response.status}: "
            f"{response.data!r}"
        )
    body = json.loads(response.data.decode("utf-8"))
    # Response shape from live traffic: {"asset": {...}}
    return body.get("asset") or body


def delete_physna_metadata_fields(
    client: "PhysnaClient",
    tenant_id: str,
    physna_asset_uuid: str,
    field_names,
) -> None:
    """Delete the named metadata fields from a Physna asset.

    Endpoint: ``DELETE /tenants/{tenant}/assets/{uuid}/metadata`` with body
    ``{"metadataFieldNames": [...]}``. This clears the **values** of those
    fields on this asset only; the tenant-level metadata field definitions
    are left in place.
    """
    names = [n for n in field_names if n]
    if not names:
        return
    response = client.request(
        "DELETE",
        f"/tenants/{tenant_id}/assets/{physna_asset_uuid}/metadata",
        body=json.dumps({"metadataFieldNames": names}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    if response.status not in (200, 204, 404):
        raise PhysnaApiError(
            f"Metadata delete failed for asset {physna_asset_uuid} "
            f"(fields={names}): status={response.status}: {response.data!r}"
        )


# Per-page size used by text-search. Matches what Physna itself returns when
# the client doesn't override (see sample: "perPage": 50 in response). Higher
# values reduce round-trip count for filenames shared across many folders.
_TEXT_SEARCH_PER_PAGE = 50

# Safety cap on the number of pages we'll pull. Prevents runaway iteration if
# Physna ever returns malformed `pageData`. At 50/page this caps us at 1000
# candidates for a single lookup — far more than any realistic name collision.
_TEXT_SEARCH_MAX_PAGES = 20


def lookup_physna_asset_id(
    client: "PhysnaClient", tenant_id: str, full_path: str
) -> Optional[str]:
    """Return the Physna asset UUID for ``full_path``, or None if not found.

    Uses Physna's text-search endpoint
    (``POST /tenants/{tenantId}/assets/text-search``). Unlike the plain
    list endpoint, text-search returns assets in every state — including
    ``indexing`` — which is exactly what the viewer needs: a file that was
    just uploaded must be resolvable to its UUID *before* indexing
    finishes, so we can then poll ``GET /assets/{uuid}`` for the actual
    state and show the proper "still indexing" page instead of a
    misleading "not synced yet" page.

    The search is scoped by folder (the parent portion of ``full_path``)
    and filtered by the filename. text-search treats ``searchQuery`` as
    a substring match across every asset with that filename in the
    tenant — so a common filename like ``part.step`` can yield many
    matches across unrelated folders. Every page is walked until we find
    an exact match on the item's ``path`` field or the pagination
    exhausts, so a needle in a 500-item haystack still resolves.
    """
    if not full_path:
        return None

    if "/" in full_path:
        folder, filename = full_path.rsplit("/", 1)
    else:
        folder, filename = "", full_path

    if not filename:
        return None

    # Paginate through every page of matches looking for an exact path hit.
    # Physna's response envelope looks like:
    #   {"matches": [{"asset": {...}}, ...],
    #    "pageData": {"currentPage", "lastPage", "total", ...}}
    page = 1
    while page <= _TEXT_SEARCH_MAX_PAGES:
        # Physna's own filterData responses report folder identifiers with a
        # trailing slash (e.g. ``"building/x880.../"``) — match that form on
        # the request side. Physna also rejects partial ``filters`` objects
        # on some tenants, so populate every field from the text-search spec
        # even when empty.
        folder_filter = f"{folder}/" if folder else None
        body = json.dumps(
            {
                "page": page,
                "perPage": _TEXT_SEARCH_PER_PAGE,
                "searchQuery": filename,
                "filters": {
                    "labels": [],
                    "folderIds": [],
                    # When the asset is at the tenant root, leave ``folders``
                    # empty so text-search spans the whole tenant. The
                    # client-side exact-path match below still pins the
                    # result even with no server-side folder narrowing.
                    "folders": [folder_filter] if folder_filter else [],
                    "metadata": {},
                    "extensions": [],
                },
            }
        ).encode("utf-8")

        response = client.request(
            "POST",
            f"/tenants/{tenant_id}/assets/text-search",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        if response.status == 404:
            return None
        if response.status != 200:
            raise PhysnaApiError(
                f"text-search lookup failed for {full_path}: "
                f"status={response.status}: {response.data!r}"
            )

        try:
            payload = json.loads(response.data.decode("utf-8"))
        except (ValueError, AttributeError) as e:
            logger.exception(
                f"Failed to parse text-search response for {full_path}: {e}"
            )
            return None

        for match in payload.get("matches") or []:
            asset = match.get("asset") or match
            if asset.get("path") == full_path:
                asset_id = (
                    asset.get("id") or asset.get("assetId") or asset.get("uuid")
                )
                if asset_id:
                    return str(asset_id)
                logger.warning(
                    f"text-search matched path {full_path} but item had no "
                    f"recognizable id field; keys were: {list(asset.keys())}"
                )
                return None

        # No exact path match on this page — advance. Physna's pageData
        # reports 1-based ``currentPage`` and ``lastPage``; stop when the
        # two meet (or when ``lastPage`` is unreported, which we treat as
        # "single page").
        page_data = payload.get("pageData") or {}
        current_page = page_data.get("currentPage", page)
        last_page = page_data.get("lastPage", current_page)
        if current_page >= last_page:
            return None
        page = current_page + 1

    logger.warning(
        f"text-search pagination hit safety cap ({_TEXT_SEARCH_MAX_PAGES} "
        f"pages) while resolving {full_path!r}; giving up"
    )
    return None


# ---------------------------------------------------------------------------
# Metadata-field registration
#
# Physna's tenant metadata schema is explicit: every custom key you want to set
# on an asset must exist as a ``metadataField`` in the tenant first. Attempting
# to PATCH an asset with an unregistered key returns 400 with:
#     "Metadata field not found: '<Key>'"
# and the entire PATCH is rejected. We auto-register any missing fields before
# sending metadata, so VAMS metadata keys flow through transparently.
#
# Endpoint shapes (verified from live traffic, 2026-05-10):
#   GET  /tenants/{tenantId}/metadata-fields?page={n}&perPage=200
#     → {"metadataFields": [{"id","name","type",...}], "pageData": {...}}
#   POST /tenants/{tenantId}/metadata-fields
#     body: {"name": "<key>", "type": "text"}
#     → {"metadataField": {"id","name","type",...}}
#
# Process-lifetime cache of known field names so we don't re-list on every call.
# Keyed by tenant id; value is a set of field names already present in Physna.
# ---------------------------------------------------------------------------

_known_metadata_fields_by_tenant: Dict[str, set] = {}


def _reset_metadata_field_cache_for_tests() -> None:
    """Test hook: clear the in-process metadata-field cache."""
    _known_metadata_fields_by_tenant.clear()


def _fetch_known_metadata_fields(
    client: "PhysnaClient", tenant_id: str
) -> set:
    """Return the set of metadata field names already registered on the tenant.

    Paginates through the tenant's metadata-fields endpoint. The result is
    cached in-process under the tenant id so subsequent calls are free.
    """
    cached = _known_metadata_fields_by_tenant.get(tenant_id)
    if cached is not None:
        return cached

    names: set = set()
    page = 1
    per_page = 200
    while True:
        url = (
            f"/tenants/{tenant_id}/metadata-fields"
            f"?page={page}&perPage={per_page}"
        )
        response = client.request("GET", url)
        if response.status != 200:
            raise PhysnaApiError(
                f"Failed to list metadata fields for tenant {tenant_id}: "
                f"status={response.status}: {response.data!r}"
            )
        body = json.loads(response.data.decode("utf-8"))
        for entry in body.get("metadataFields") or []:
            name = entry.get("name")
            if name:
                names.add(name)
        page_data = body.get("pageData") or {}
        current_page = page_data.get("currentPage", page)
        last_page = page_data.get("lastPage", current_page)
        if current_page >= last_page:
            break
        page = current_page + 1

    _known_metadata_fields_by_tenant[tenant_id] = names
    return names


def ensure_metadata_fields_registered(
    client: "PhysnaClient", tenant_id: str, field_names
) -> None:
    """Ensure every name in ``field_names`` exists as a Physna metadata field.

    For any names not present in the tenant's schema, POSTs a new
    ``metadataField`` of type ``text``. Existing fields are left alone —
    changing types or deleting fields is intentionally out of scope here.
    Successful registrations are added to the in-process cache.

    Failure to register a specific field is logged and skipped so one bad
    name doesn't block the rest. Callers that PATCH metadata will still get
    a Physna 400 for unregistered fields in that case; this is a best-effort
    smoothing layer, not a replacement for Physna's own validation.
    """
    requested = {n for n in field_names if n}
    if not requested:
        return
    known = _fetch_known_metadata_fields(client, tenant_id)
    missing = requested - known
    if not missing:
        return

    for name in sorted(missing):
        response = client.request(
            "POST",
            f"/tenants/{tenant_id}/metadata-fields",
            body=json.dumps({"name": name, "type": "text"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        if response.status in (200, 201):
            known.add(name)
            logger.info(
                f"Registered Physna metadata field {name!r} on tenant {tenant_id}"
            )
        elif response.status == 409:
            # Already exists (race with another caller) — treat as success.
            known.add(name)
        else:
            logger.warning(
                f"Failed to register Physna metadata field {name!r} on tenant "
                f"{tenant_id}: status={response.status}: {response.data!r}"
            )


def delete_folder_if_empty(
    client: "PhysnaClient", tenant_id: str, folder: str
) -> bool:
    """Delete a Physna folder if it contains no assets.

    Returns True if the folder was (or would have been) deleted. Returns
    False if the folder still has assets.

    NOTE: The Physna folder-delete endpoint could not be conclusively
    identified from the public API docs at implementation time. The list+
    empty-check logic is fully wired. The single HTTP call is stubbed below
    with a mock URL/payload and left commented out — uncomment and fix the
    endpoint / request shape once verified with Physna.
    """
    # Empty-check via the same listing helper
    has_any = False
    for _item in list_physna_assets_under(client, tenant_id, folder):
        has_any = True
        break
    if has_any:
        logger.info(f"Skipping folder delete; still has assets: {folder}")
        return False

    logger.info(f"Folder empty, would delete: {folder}")
    _folder_delete_stub_callback(client, tenant_id, folder)

    # TODO: verify with Physna — fill in real endpoint + params.
    # Once confirmed, uncomment the block below and delete this TODO.
    # Example based on a plausible REST shape:
    #
    # path = f"/tenants/{tenant_id}/folders?path={folder}"
    # response = client.request("DELETE", path)
    # if response.status not in (200, 204, 404):
    #     raise PhysnaApiError(
    #         f"Folder delete failed for {folder}: status={response.status}"
    #     )

    return True


from boto3.dynamodb.conditions import Key
from common.resourceNames import get_table_name, ResourceKeys

_dynamodb = boto3.resource("dynamodb", config=_retry_config)
_s3_client = boto3.client("s3", config=_retry_config)

try:
    _ASSET_STORAGE_TABLE_NAME = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
except Exception as e:
    logger.warning(f"Failed resolving asset storage table name (OK for tests): {e}")
    _ASSET_STORAGE_TABLE_NAME = None

try:
    _DATABASE_STORAGE_TABLE_NAME = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
except Exception as e:
    logger.warning(f"Failed resolving database storage table name (OK for tests): {e}")
    _DATABASE_STORAGE_TABLE_NAME = None

try:
    _ASSET_FILE_METADATA_STORAGE_TABLE_NAME = get_table_name(ResourceKeys.ASSET_FILE_METADATA_STORAGE_TABLE)
except Exception as e:
    logger.warning(f"Failed resolving asset file metadata table name (OK for tests): {e}")
    _ASSET_FILE_METADATA_STORAGE_TABLE_NAME = None

try:
    _FILE_ATTRIBUTE_STORAGE_TABLE_NAME = get_table_name(ResourceKeys.FILE_ATTRIBUTE_STORAGE_TABLE)
except Exception as e:
    logger.warning(f"Failed resolving file attribute table name (OK for tests): {e}")
    _FILE_ATTRIBUTE_STORAGE_TABLE_NAME = None

try:
    _S3_ASSET_BUCKETS_STORAGE_TABLE_NAME = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
except Exception as e:
    logger.warning(f"Failed resolving S3 asset buckets table name (OK for tests): {e}")
    _S3_ASSET_BUCKETS_STORAGE_TABLE_NAME = None

asset_storage_table = _dynamodb.Table(_ASSET_STORAGE_TABLE_NAME) if _ASSET_STORAGE_TABLE_NAME else None
database_storage_table = _dynamodb.Table(_DATABASE_STORAGE_TABLE_NAME) if _DATABASE_STORAGE_TABLE_NAME else None
asset_file_metadata_table = (
    _dynamodb.Table(_ASSET_FILE_METADATA_STORAGE_TABLE_NAME)
    if _ASSET_FILE_METADATA_STORAGE_TABLE_NAME
    else None
)
file_attribute_table = (
    _dynamodb.Table(_FILE_ATTRIBUTE_STORAGE_TABLE_NAME)
    if _FILE_ATTRIBUTE_STORAGE_TABLE_NAME
    else None
)
s3_asset_buckets_table = (
    _dynamodb.Table(_S3_ASSET_BUCKETS_STORAGE_TABLE_NAME)
    if _S3_ASSET_BUCKETS_STORAGE_TABLE_NAME
    else None
)


def get_file_metadata(
    database_id: str, asset_id: str, file_path: str
):
    """Return (metadata, attributes) dicts for a specific file.

    Each dict has shape ``{key: {"value": str, "type": str}}``. System records
    (``REINDEX_METADATA_RECORD``) are filtered out.
    """
    composite_key = f"{database_id}:{asset_id}:{file_path}"
    metadata: Dict[str, Dict[str, str]] = {}
    attributes: Dict[str, Dict[str, str]] = {}

    meta_response = asset_file_metadata_table.query(
        IndexName="DatabaseIdAssetIdFilePathIndex",
        KeyConditionExpression=Key("databaseId:assetId:filePath").eq(composite_key),
    )
    for item in meta_response.get("Items", []):
        key = item.get("metadataKey")
        value = item.get("metadataValue")
        if not key or not value or is_excluded_metadata_record(key):
            continue
        metadata[key] = {
            "value": value,
            "type": item.get("metadataValueType", "string"),
        }

    attr_response = file_attribute_table.query(
        IndexName="DatabaseIdAssetIdFilePathIndex",
        KeyConditionExpression=Key("databaseId:assetId:filePath").eq(composite_key),
    )
    for item in attr_response.get("Items", []):
        key = item.get("attributeKey")
        value = item.get("attributeValue")
        if not key or not value:
            continue
        attributes[key] = {
            "value": value,
            "type": item.get("attributeValueType", "string"),
        }

    return metadata, attributes


def get_asset_metadata(database_id: str, asset_id: str) -> Dict[str, Dict[str, str]]:
    """Return the asset-level metadata dict (composite key ends in ':/')."""
    composite_key = f"{database_id}:{asset_id}:/"
    metadata: Dict[str, Dict[str, str]] = {}
    response = asset_file_metadata_table.query(
        IndexName="DatabaseIdAssetIdFilePathIndex",
        KeyConditionExpression=Key("databaseId:assetId:filePath").eq(composite_key),
    )
    for item in response.get("Items", []):
        key = item.get("metadataKey")
        value = item.get("metadataValue")
        if not key or not value or is_excluded_metadata_record(key):
            continue
        metadata[key] = {
            "value": value,
            "type": item.get("metadataValueType", "string"),
        }
    return metadata


def get_asset_details(database_id: str, asset_id: str) -> Optional[Dict[str, Any]]:
    """Return the asset record from ``assetStorageTable`` or None."""
    response = asset_storage_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    return response.get("Item")


def get_bucket_details(bucket_id: str) -> Optional[Dict[str, Any]]:
    """Return normalized bucket details for a bucketId, or None if not found."""
    response = s3_asset_buckets_table.query(
        KeyConditionExpression=Key("bucketId").eq(bucket_id), Limit=1
    )
    items = response.get("Items", [])
    if not items:
        return None
    bucket = items[0]
    base_prefix = bucket.get("baseAssetsPrefix", "/")
    if not base_prefix.endswith("/"):
        base_prefix += "/"
    if base_prefix.startswith("/"):
        base_prefix = base_prefix[1:]
    return {
        "bucketId": bucket_id,
        "bucketName": bucket.get("bucketName"),
        "baseAssetsPrefix": base_prefix,
    }


def get_bucket_details_by_name(bucket_name: str) -> Optional[Dict[str, Any]]:
    """Reverse-lookup bucket registry by bucketName.

    S3 ObjectRemoved events deliver the bucket name (not bucketId), and by
    the time we handle them we can no longer head_object the deleted key
    to read VAMS S3 user-metadata (databaseId/assetId). This helper reads
    the bucket registry via the ``bucketNameGSI`` so the metadata-free
    delete-resolver can still reconstruct the asset path.
    """
    if not bucket_name or s3_asset_buckets_table is None:
        return None
    response = s3_asset_buckets_table.query(
        IndexName="bucketNameGSI",
        KeyConditionExpression=Key("bucketName").eq(bucket_name),
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return None
    bucket = items[0]
    base_prefix = bucket.get("baseAssetsPrefix", "/")
    if not base_prefix.endswith("/"):
        base_prefix += "/"
    if base_prefix.startswith("/"):
        base_prefix = base_prefix[1:]
    return {
        "bucketId": bucket.get("bucketId"),
        "bucketName": bucket.get("bucketName") or bucket_name,
        "baseAssetsPrefix": base_prefix,
    }


def get_database_id_for_asset_id(
    asset_id: str,
    bucket_name: Optional[str] = None,
    base_assets_prefix: Optional[str] = None,
) -> Optional[str]:
    """Return the databaseId for a VAMS assetId via the ``assetIdGSI``.

    Used by the delete-resolver path: an S3 ObjectRemoved event gives us
    an assetId (extracted from the key) but no databaseId, and reading the
    deleted object's S3 user-metadata is no longer possible.

    When a single assetId appears in more than one database (e.g. the same
    ID has been assigned in two separate databases because the tables are
    not cross-database-unique), we need to disambiguate. Passing
    ``bucket_name`` and ``base_assets_prefix`` lets us filter to the one
    match whose asset record points at the same physical bucket+prefix
    the event came from. If disambiguation still leaves zero or multiple
    matches, we return ``None`` — guessing would risk deleting the wrong
    Physna asset. This mirrors the approach in
    ``fileIndexer.lookup_database_id_for_permanent_delete``.
    """
    if not asset_id or asset_storage_table is None:
        return None

    response = asset_storage_table.query(
        IndexName="assetIdGSI",
        KeyConditionExpression=Key("assetId").eq(asset_id),
    )
    items = response.get("Items", [])
    if not items:
        return None
    if len(items) == 1:
        db = items[0].get("databaseId")
        return str(db) if db else None

    # Multiple matches — try to narrow by bucket. Without bucket context
    # we must refuse to pick arbitrarily.
    if not bucket_name:
        logger.warning(
            f"assetIdGSI returned {len(items)} matches for assetId={asset_id} "
            f"but no bucket context to disambiguate; refusing to guess."
        )
        return None

    def _normalize_prefix(prefix: Optional[str]) -> str:
        prefix = prefix or ""
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        if prefix.startswith("/"):
            prefix = prefix[1:]
        return prefix

    event_prefix = _normalize_prefix(base_assets_prefix)

    matches: list = []
    for item in items:
        bucket_id = item.get("bucketId")
        if not bucket_id:
            continue
        bucket_details = get_bucket_details(bucket_id)
        if not bucket_details:
            continue
        item_bucket = bucket_details.get("bucketName")
        item_prefix = _normalize_prefix(bucket_details.get("baseAssetsPrefix"))
        if item_bucket == bucket_name and item_prefix == event_prefix:
            matches.append(item)

    if len(matches) == 1:
        db = matches[0].get("databaseId")
        return str(db) if db else None
    if not matches:
        logger.warning(
            f"No assetId matches found for bucket={bucket_name} "
            f"prefix={event_prefix!r} (assetId={asset_id})."
        )
    else:
        logger.warning(
            f"{len(matches)} assetId matches remain after bucket filter for "
            f"assetId={asset_id} bucket={bucket_name} prefix={event_prefix!r}; "
            f"cannot determine unique databaseId."
        )
    return None
