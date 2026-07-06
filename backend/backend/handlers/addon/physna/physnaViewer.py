# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Physna Viewer metadata endpoint.

``GET /addon/physna/viewer`` is a VAMS API-authorized JSON endpoint that
tells the frontend everything it needs to embed Physna's hosted viewer in
an iframe. Given a VAMS ``databaseId`` / ``assetId`` / ``relativePath`` it:

1. Enforces VAMS two-tier Casbin authorization (API route + the specific
   asset object).
2. Verifies the file extension is one Physna can render.
3. Looks up the matching Physna asset UUID via text-search (works for
   assets that are still indexing, not just finished ones).
4. Fetches the Physna asset record to read ``state``.
5. For assets in the ``finished`` state, mints a short-lived Physna viewer
   token.
6. Returns a JSON envelope describing what the frontend should do
   (``ready`` | ``indexing`` | ``failed`` | ``not_synced`` |
   ``unsupported``) and, for ``ready``, the ``physnaAssetId``,
   ``tenantId``, ``viewerToken``, and ``physnaApiBase`` needed to build a
   direct Physna viewer URL.

Previous versions of this handler returned pre-rendered HTML and proxied
hoops.js / model-viewer.js / viewer-file / manifest-dependencies through
Lambda so the iframe never left VAMS. That approach hit two hard limits:

- Hoops.js and related script resources exceed the API Gateway response
  size cap.
- Rewriting the JS bundle's internal URLs broke its deeper dependency
  graph.

We now deliberately expose the short-lived viewer token to the browser and
let Physna's hosted viewer load directly. This trades a small window where
a user could reuse the token against Physna directly for vastly simpler
code and robust viewer behavior. Two-tier VAMS authorization still gates
the metadata call, which is the first-touch check on every render.
"""

import json
from typing import Any, Dict, Optional, Tuple

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.config import Config as BotoConfig

from common.resourceNames import get_table_name, ResourceKeys
from customLogging.logger import safeLogger
from models.common import commonHeaders

from . import physnaCommon
from .physnaCommon import (
    PhysnaClient,
    build_physna_path,
    get_physna_asset,
    is_viewer_supported_file,
    lookup_physna_asset_id,
)


# Lazy imports — the Pydantic request model, Casbin enforcer, and
# request_to_claims all depend on the Lambda runtime having the vendored
# version of aws_lambda_powertools (2.36.0) and Pydantic v1. Importing them
# eagerly at module load time breaks unit-test collection in dev environments
# that have newer (Pydantic v2 / powertools 3.x) versions installed.
def _request_to_claims(event):
    from handlers.auth import request_to_claims  # noqa: WPS433 (lazy import)

    return request_to_claims(event)


def _CasbinEnforcer(claims_and_roles):  # noqa: N802 — mirrors import name
    from handlers.authz import CasbinEnforcer  # noqa: WPS433 (lazy import)

    return CasbinEnforcer(claims_and_roles)


def _parse_viewer_request(raw: Dict[str, Any]):
    from models.physnaViewer import PhysnaViewerRequestModel  # noqa: WPS433

    return PhysnaViewerRequestModel(**raw)


def _VAMSGeneralErrorResponse():  # noqa: N802 — class factory
    from models.common import VAMSGeneralErrorResponse  # noqa: WPS433

    return VAMSGeneralErrorResponse


def _ValidationError():  # noqa: N802
    from aws_lambda_powertools.utilities.parser import ValidationError  # noqa: WPS433

    return ValidationError


APIGatewayProxyResponseV2 = Dict[str, Any]

logger = safeLogger(service_name="PhysnaViewer")

_retry_config = BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"})
_dynamodb = boto3.resource("dynamodb", config=_retry_config)

try:
    _ASSET_STORAGE_TABLE_NAME = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
except Exception as e:
    logger.warning(
        f"Failed resolving asset storage table name (OK for tests): {e}"
    )
    _ASSET_STORAGE_TABLE_NAME = None

try:
    _DATABASE_STORAGE_TABLE_NAME = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
except Exception as e:
    logger.warning(
        f"Failed resolving database storage table name (OK for tests): {e}"
    )
    _DATABASE_STORAGE_TABLE_NAME = None

asset_storage_table = (
    _dynamodb.Table(_ASSET_STORAGE_TABLE_NAME)
    if _ASSET_STORAGE_TABLE_NAME
    else None
)

# Global claims_and_roles, mirroring the pattern in other handlers.
claims_and_roles: Dict[str, Any] = {}


# Permanent failure states per Physna API docs. Anything that isn't
# "finished" or one of these is treated as "still indexing" so the frontend
# keeps polling.
_PERMANENT_FAILURE_STATES = frozenset(
    {"failed", "unsupported", "no-3d-data", "missing-dependencies"}
)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _json_response(
    body: Dict[str, Any], status_code: int = 200
) -> APIGatewayProxyResponseV2:
    return {
        "isBase64Encoded": False,
        "statusCode": status_code,
        "headers": {
            **commonHeaders(),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
        "body": json.dumps(body),
    }


def _status_response(
    status: str,
    message: str,
    *,
    http_status: int = 200,
    extra: Optional[Dict[str, Any]] = None,
) -> APIGatewayProxyResponseV2:
    """Build a JSON envelope the frontend can switch on.

    Every response includes at minimum ``status`` (machine-readable) and
    ``message`` (human-readable fallback the frontend can show if it
    doesn't special-case ``status``). ``extra`` merges additional fields
    (e.g. the viewer-token bundle when ``status == "ready"``).
    """
    body: Dict[str, Any] = {"status": status, "message": message}
    if extra:
        body.update(extra)
    return _json_response(body, status_code=http_status)


def _strip_leading_asset_id(relative_path: str, asset_id: str) -> str:
    """Strip a leading ``/<asset_id>`` (or ``<asset_id>``) segment from
    ``relative_path`` if present.

    The web UI builds ``relativePath`` from the file's S3 key, which in VAMS
    is stored as ``{assetId}/{relative_within_asset}``. Passing that shape
    straight to ``build_physna_path`` would produce
    ``{databaseId}/{assetId}/{assetId}/{rest}`` — a doubled assetId that
    fails every Physna lookup. Normalise at the viewer boundary so only the
    portion inside the asset reaches the path builder. The file-sync handler
    already computes ``relativePath`` without the assetId prefix, so this
    helper is a viewer-only concern.
    """
    if not relative_path or not asset_id:
        return relative_path
    for prefix in (f"/{asset_id}/", f"{asset_id}/"):
        if relative_path.startswith(prefix):
            return "/" + relative_path[len(prefix):]
    return relative_path


# ---------------------------------------------------------------------------
# Authz + Physna lookup
# ---------------------------------------------------------------------------


def _authorize_and_lookup(
    database_id: str,
    asset_id: str,
    relative_path: str,
) -> Tuple[
    Optional[str],
    Optional[APIGatewayProxyResponseV2],
    Optional[PhysnaClient],
]:
    """Enforce object-level authz and resolve the Physna UUID for a file.

    Returns ``(physna_uuid, error_response, physna_client)``. Exactly one of
    ``physna_uuid`` or ``error_response`` is non-None on every return
    path. The PhysnaClient is returned on success so the caller can reuse
    its cached OAuth2 token for a follow-up ``/viewer/token`` mint or a
    ``GET /assets/{uuid}`` state read.
    """
    if not is_viewer_supported_file(relative_path):
        return (
            None,
            _status_response(
                "unsupported",
                "This file type cannot be rendered by the Physna Viewer.",
                http_status=400,
            ),
            None,
        )

    if asset_storage_table is None:
        raise _VAMSGeneralErrorResponse()(
            "Asset storage table is not configured for the Physna Viewer lambda."
        )
    asset_response = asset_storage_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    )
    asset = asset_response.get("Item")
    if not asset:
        return (
            None,
            _status_response(
                "not_found",
                "This asset does not exist in VAMS.",
                http_status=404,
            ),
            None,
        )
    asset["object__type"] = "asset"
    casbin_enforcer = _CasbinEnforcer(claims_and_roles)
    if not casbin_enforcer.enforce(asset, "GET"):
        return (
            None,
            _status_response(
                "forbidden",
                "You do not have permission to view this asset.",
                http_status=403,
            ),
            None,
        )

    full_path = build_physna_path(database_id, asset_id, relative_path)
    client = PhysnaClient()
    try:
        physna_uuid = lookup_physna_asset_id(
            client, physnaCommon.PHYSNA_TENANT_ID, full_path
        )
    except Exception as e:
        logger.exception(f"Physna lookup failed for {full_path}: {e}")
        return (
            None,
            _status_response(
                "upstream_unavailable",
                "Could not reach Physna to retrieve this asset. "
                "Please try again shortly.",
                http_status=502,
            ),
            None,
        )
    if not physna_uuid:
        return (
            None,
            _status_response(
                "not_synced",
                "This file has not been synced to Physna yet. "
                "If you just uploaded it, please check back shortly.",
                http_status=200,
            ),
            None,
        )
    return (physna_uuid, None, client)


def _mint_viewer_token(client: PhysnaClient) -> Optional[str]:
    """POST /viewer/token and return the short-lived viewer token, or None."""
    response = client.request("POST", "/viewer/token")
    if response.status != 200:
        logger.warning(
            f"Physna /viewer/token returned status={response.status}"
        )
        return None
    try:
        body = json.loads(response.data.decode("utf-8"))
    except (ValueError, AttributeError) as e:
        logger.exception(f"Failed to parse /viewer/token response: {e}")
        return None
    for key in ("token", "viewerToken", "accessToken"):
        value = body.get(key)
        if value:
            return str(value)
    logger.warning(
        f"Physna /viewer/token response had no recognizable token key; "
        f"keys present: {list(body.keys())}"
    )
    return None


# ---------------------------------------------------------------------------
# Request handling
# ---------------------------------------------------------------------------


def _handle_get(event: Dict[str, Any]) -> APIGatewayProxyResponseV2:
    ValidationError = _ValidationError()
    try:
        request = _parse_viewer_request(
            event.get("queryStringParameters") or {}
        )
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return _status_response(
            "invalid_request", str(v), http_status=400
        )

    # Web-UI ``relativePath`` starts with the assetId (derived from S3 key).
    # build_physna_path prepends databaseId/assetId itself, so strip the
    # duplicate here.
    relative_path = _strip_leading_asset_id(
        request.relativePath, request.assetId
    )

    physna_uuid, err, client = _authorize_and_lookup(
        request.databaseId,
        request.assetId,
        relative_path,
    )
    if err is not None:
        return err

    try:
        physna_asset = get_physna_asset(
            client, physnaCommon.PHYSNA_TENANT_ID, physna_uuid
        )
    except Exception as e:
        logger.exception(
            f"Physna get_physna_asset failed for uuid={physna_uuid}: {e}"
        )
        return _status_response(
            "upstream_unavailable",
            "Could not retrieve this asset's state from Physna. "
            "Please try again shortly.",
            http_status=502,
        )
    if not physna_asset:
        return _status_response(
            "not_synced",
            "This file is no longer present in Physna.",
            http_status=200,
        )

    state = str(physna_asset.get("state") or "").lower()
    if state in _PERMANENT_FAILURE_STATES:
        return _status_response(
            "failed",
            f"Physna reported a permanent failure state: {state}.",
            http_status=200,
            extra={"physnaState": state},
        )

    if state != "finished":
        # Treat every non-permanent, non-finished state as "still working".
        # The frontend polls on a short cadence until state becomes finished.
        return _status_response(
            "indexing",
            "Physna is still indexing this file. Please check back shortly.",
            http_status=200,
            extra={"physnaState": state or "unknown"},
        )

    # finished — mint a viewer token and hand the frontend everything it
    # needs to build a direct Physna iframe src.
    viewer_token = _mint_viewer_token(client)
    if not viewer_token:
        return _status_response(
            "upstream_unavailable",
            "Could not mint a Physna viewer token for this asset.",
            http_status=502,
        )

    # ``physnaApiBase`` carries the trailing slash from config; hand the
    # frontend the trimmed form so it can cleanly concatenate a path.
    physna_api_base = (physnaCommon.PHYSNA_API_BASE or "").rstrip("/")

    return _status_response(
        "ready",
        "Physna viewer is ready.",
        http_status=200,
        extra={
            "tenantId": physnaCommon.PHYSNA_TENANT_ID,
            "physnaAssetId": physna_uuid,
            "viewerToken": viewer_token,
            "physnaApiBase": physna_api_base,
        },
    )


def lambda_handler(
    event: Dict[str, Any], context: LambdaContext
) -> APIGatewayProxyResponseV2:
    """Entry point. GET-only."""
    global claims_and_roles
    claims_and_roles = _request_to_claims(event)

    ValidationError = _ValidationError()
    VAMSGeneralErrorResponse = _VAMSGeneralErrorResponse()

    try:
        method = (
            event.get("requestContext", {}).get("http", {}).get("method", "GET")
        )

        # API-level authorization
        method_allowed_on_api = False
        if len(claims_and_roles.get("tokens", [])) > 0:
            casbin_enforcer = _CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
        if not method_allowed_on_api:
            return _status_response(
                "forbidden",
                "You do not have permission to access this viewer.",
                http_status=403,
            )

        if method != "GET":
            return _status_response(
                "method_not_allowed",
                "This endpoint only supports GET requests.",
                http_status=405,
            )

        return _handle_get(event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return _status_response(
            "invalid_request", str(v), http_status=400
        )
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return _status_response("request_failed", str(v), http_status=400)
    except Exception as e:  # pylint: disable=broad-except
        logger.exception(f"Internal error: {e}")
        return _status_response(
            "internal_error",
            "An unexpected error occurred while loading the Physna Viewer.",
            http_status=500,
        )
