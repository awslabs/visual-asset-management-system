# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""POST /addon/miris/viewer-key   body: {"databaseId": ..., "assetId": ..., "filePath": ...}

Returns a Miris viewer key that can stream the Miris asset recorded on one asset file. The Miris
asset UUID is read server side from the file's metadata (`miris_asset_uuid`), exactly as the stream
status endpoint reads it; the request carries no UUID. `filePath` accepts the same forms as the
status endpoint.

Before any key is returned, the Miris asset is read with the Miris Integration Key and must carry
both tags `vams-asset-{assetId}` and `vams-database-{databaseId}` that bind it to this VAMS asset.
A Miris asset without both tags, or one Miris reports as missing (404), is `notBound`.

The key is created with the Miris Integration Key, with no tags and the one asset ID, and expires
`MIRIS_VIEWER_KEY_TTL_MINUTES` minutes after it is minted. Each key is recorded in the add-on's viewer
key table under its Miris key ID, with a `ttl` one hour after its expiry; the viewer key cleanup
Lambda deletes the key from Miris when TTL removes the row.

The key material is returned to the caller only, and is never stored, logged or audited.

Response body (200, `Cache-Control: no-cache, no-store`):

    filePath         the normalized asset-relative path, e.g. "/scene.usd"
    mirisAssetUuid   the Miris asset the key can stream
    viewerKey        the viewer key material
    expiresAt        the key's expiry, RFC 3339 UTC

Errors:

    400  invalid request, missing asset, or `miris_asset_uuid` is not a UUID (`state: invalidUuid`)
    403  Tier 1 or Tier 2 denial, or the Miris asset is missing or lacks the asset's binding tags
         (`state: notBound`)
    404  the file records no Miris stream (`state: notStreamed`)
    500  the key could not be recorded (the minted key is deleted from Miris), or misconfiguration
    502  Miris could not be read or did not create the key
    503  the Integration Key secret or the Miris API base URL is not configured
    504  the request deadline passed before Miris answered

Authorization is two-tier:
  - Tier 1: enforceAPI on the route (POST)
  - Tier 2: enforce GET on the asset named by databaseId and assetId, before any metadata is read.
    A viewer key grants streaming only, so it is checked as a read of the asset.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.config import Config

from common.addonApiRoutes import API_ADDON_MIRIS_VIEWER_KEY
from common.compliance.quarantineGuard import (
    QuarantineBlockedResponse,
    check_quarantine_block,
    quarantine_refusal,
)
from common.downloadEligibility import require_distributable_asset
from common.addonResourceNames import AddonResourceKeys
from common.resourceNames import ResourceKeys, get_table_name
from customLogging.auditLogging import audited_handler, log_auth_other
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from models.common import (
    APIGatewayProxyResponseV2,
    VAMSGeneralErrorResponse,
    authorization_error,
    general_error,
    internal_error,
    success,
    validation_error,
    validation_error_message,
)
from models.addon.miris import (
    STATE_INVALID_UUID,
    STATE_NOT_BOUND,
    STATE_NOT_STREAMED,
    MirisViewerKeyRequestModel,
    MirisViewerKeyResponseModel,
)

from . import mirisIntegration
from .streamMetadata import (
    file_path_for_asset,
    load_readable_asset,
    read_stream_metadata,
    stream_uuid,
    unusable_uuid_state,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
# The key row is written after the key is minted, late in the request, so its write is short.
key_table_config = Config(
    connect_timeout=1, read_timeout=2, retries={"total_max_attempts": 2, "mode": "standard"}
)
key_table_dynamodb = boto3.resource("dynamodb", config=key_table_config)
logger = safeLogger(service_name="MirisMintViewerKey")

claims_and_roles = {}

try:
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    asset_file_metadata_table_name = get_table_name(ResourceKeys.ASSET_FILE_METADATA_STORAGE_TABLE)
    miris_viewer_key_table_name = get_table_name(AddonResourceKeys.MIRIS_VIEWER_KEY_STORAGE_TABLE)
    # Validated per request, so a bad value fails the mint rather than the import.
    viewer_key_ttl_minutes_setting = os.environ.get("MIRIS_VIEWER_KEY_TTL_MINUTES", "")
except Exception:
    logger.exception("Failed loading resource names")
    raise

asset_table = dynamodb.Table(asset_storage_table_name)
asset_file_metadata_table = dynamodb.Table(asset_file_metadata_table_name)
miris_viewer_key_table = key_table_dynamodb.Table(miris_viewer_key_table_name)

# Rows outlive the key by this long, so TTL never removes a row for a key that is still valid.
_ROW_TTL_GRACE_SECONDS = 3600
_TTL_MINUTES_RANGE = range(1, 61)


@audited_handler
def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        path = event["requestContext"]["http"]["path"]
        method = event["requestContext"]["http"]["method"]

        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforceAPI(event):
                return authorization_error()
        else:
            return authorization_error()

        if method == "POST" and API_ADDON_MIRIS_VIEWER_KEY.matches(path):
            return _handle_post(event, context)
        return validation_error(body={"message": "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={"message": str(v)}, event=event)
    except Exception as e:
        # The type only: a viewer key may exist by now, and an exception message or traceback
        # could carry its material.
        logger.error(f"Internal error ({type(e).__name__})")
    # Answered outside the except block, so a traceback logged by the error response's audit write
    # does not chain the exception.
    return internal_error(event=event)


def _request_body(event) -> dict:
    body = event.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            raise VAMSGeneralErrorResponse("Invalid JSON in request body")
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise VAMSGeneralErrorResponse("Request body must be a JSON object")
    return body


def _viewer_key_ttl_minutes() -> int:
    """MIRIS_VIEWER_KEY_TTL_MINUTES as a whole number of minutes from 1 to 60."""
    ttl_minutes = int(viewer_key_ttl_minutes_setting)
    if ttl_minutes not in _TTL_MINUTES_RANGE:
        raise ValueError("MIRIS_VIEWER_KEY_TTL_MINUTES is outside 1-60")
    return ttl_minutes


def _configured() -> bool:
    """True when the Integration Key secret and the Miris API base URL are present."""
    return mirisIntegration.integration_configured()


def _not_configured_response(event):
    return general_error(
        status_code=503,
        body={"message": "Miris viewer keys are not configured"},
        event=event,
    )


def _deadline_response(event):
    return general_error(
        status_code=504,
        body={"message": "Miris did not answer in time"},
        event=event,
    )


def _handle_post(event, context):
    if not _configured():
        logger.error("The Miris Integration Key secret or API base URL is not configured")
        return _not_configured_response(event)

    body = _request_body(event)
    request_model = parse(
        {
            "databaseId": body.get("databaseId"),
            "assetId": body.get("assetId"),
            "filePath": body.get("filePath"),
        },
        model=MirisViewerKeyRequestModel,
    )
    database_id = request_model.databaseId
    asset_id = request_model.assetId

    asset, denied = load_readable_asset(
        asset_table, CasbinEnforcer(claims_and_roles), database_id, asset_id, event
    )
    if denied:
        return denied
    refused = _distribution_refusal(asset, database_id, asset_id, event)
    if refused:
        return refused

    file_path = file_path_for_asset(asset, asset_id, request_model.filePath)
    stream_metadata = read_stream_metadata(
        asset_file_metadata_table, database_id, asset_id, file_path
    )
    miris_asset_uuid = stream_uuid(stream_metadata)

    unusable_state = unusable_uuid_state(miris_asset_uuid)
    if unusable_state == STATE_NOT_STREAMED:
        return general_error(
            status_code=404,
            body={"message": "The file records no Miris stream", "state": STATE_NOT_STREAMED},
            event=event,
        )
    if unusable_state == STATE_INVALID_UUID:
        logger.warning("miris_asset_uuid on the file is not a UUID")
        return general_error(
            body={"message": "The file's Miris asset UUID is not a UUID", "state": STATE_INVALID_UUID},
            event=event,
        )

    try:
        ttl_minutes = _viewer_key_ttl_minutes()
    except ValueError:
        logger.error("MIRIS_VIEWER_KEY_TTL_MINUTES is not a whole number of minutes from 1 to 60")
        return internal_error(event=event)

    deadline = mirisIntegration.request_deadline(context, event)
    unbound = _binding_failure(miris_asset_uuid, database_id, asset_id, deadline, event)
    if unbound is not None:
        return unbound

    return _mint_key(event, context, deadline, ttl_minutes, database_id, asset_id, file_path,
                     miris_asset_uuid)


def _distribution_refusal(asset, database_id, asset_id, event):
    """The rule the core file routes apply before serving an asset's content: distributable, with a
    location, and not quarantined while the deployment's quarantine block is on. Returns None when a
    key may be returned, else the refusal, answered as the download route answers it."""
    try:
        require_distributable_asset(asset, check_quarantine_block, database_id, asset_id)
    except QuarantineBlockedResponse as refusal:
        return quarantine_refusal(refusal, event)
    except VAMSGeneralErrorResponse as refusal:
        return general_error(status_code=refusal.status_code, body={"message": refusal.user_message},
                             event=event)
    return None


def _binding_failure(miris_asset_uuid, database_id, asset_id, deadline, event):
    """Read the Miris asset and check its binding tags. Returns None for a bound asset, else the
    error response."""
    try:
        miris_asset = mirisIntegration.get_asset(miris_asset_uuid, deadline=deadline)
    except mirisIntegration.MirisDeadlineExceeded:
        logger.error(f"Request deadline passed before Miris asset {miris_asset_uuid} was read")
        return _deadline_response(event)
    except mirisIntegration.MirisIntegrationError as e:
        logger.error(f"Miris asset read failed for {miris_asset_uuid}: {e}")
        return general_error(
            status_code=502,
            body={"message": "Miris could not be reached"},
            event=event,
        )
    if miris_asset is not None and mirisIntegration.is_bound_to_asset(
        miris_asset.tags, database_id, asset_id
    ):
        return None
    logger.warning(
        f"Miris asset {miris_asset_uuid} is missing or not tagged for {database_id}/{asset_id}"
    )
    asset_tag, database_tag = mirisIntegration.binding_tags(database_id, asset_id)
    return general_error(
        status_code=403,
        body={
            "message": (
                f"The file's Miris asset is not bound to this asset: it must exist and carry"
                f" the tags {asset_tag} and {database_tag}"
            ),
            "state": STATE_NOT_BOUND,
        },
        event=event,
    )


def _mint_key(event, context, deadline, ttl_minutes, database_id, asset_id, file_path,
              miris_asset_uuid):
    """Create, record and return a viewer key for a bound asset."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = now + timedelta(minutes=ttl_minutes)
    name = mirisIntegration.viewer_key_name(database_id, asset_id, int(time.time() * 1000))

    try:
        minted = mirisIntegration.create_viewer_key(
            miris_asset_uuid, expires_at, name, deadline=deadline, context=context
        )
    except mirisIntegration.MirisDeadlineExceeded:
        logger.error(f"Request deadline passed before a viewer key was created for {miris_asset_uuid}")
        return _deadline_response(event)
    except mirisIntegration.MirisIntegrationError as e:
        logger.error(f"Miris viewer key create failed for {miris_asset_uuid}: {e}")
        return general_error(
            status_code=502,
            body={"message": "Miris did not issue a viewer key"},
            event=event,
        )

    user_id = claims_and_roles["tokens"][0]
    recorded = False
    try:
        miris_viewer_key_table.put_item(
            Item={
                "mirisViewerKeyId": minted.key_id,
                "databaseId": database_id,
                "assetId": asset_id,
                "filePath": file_path,
                "userId": user_id,
                "mirisAssetUuid": miris_asset_uuid,
                "createdAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expiresAt": minted.expires_at,
                "ttl": int(expires_at.timestamp()) + _ROW_TTL_GRACE_SECONDS,
            },
            ConditionExpression="attribute_not_exists(mirisViewerKeyId)",
        )
        recorded = True
    except Exception as e:
        logger.error(f"Could not record Miris viewer key {minted.key_id} ({type(e).__name__})")
    # Handled outside the except block, so no later traceback chains the put failure.
    if not recorded:
        _delete_unrecorded_key(minted.key_id, context)
        return internal_error(event=event)

    log_auth_other(
        event,
        "mirisViewerKeyMint",
        {
            "userId": user_id,
            "databaseId": database_id,
            "assetId": asset_id,
            "filePath": file_path,
            "mirisAssetUuid": miris_asset_uuid,
            "mirisViewerKeyId": minted.key_id,
            "expiresAt": minted.expires_at,
        },
    )

    response = MirisViewerKeyResponseModel(
        filePath=file_path,
        mirisAssetUuid=miris_asset_uuid,
        viewerKey=minted.key,
        expiresAt=minted.expires_at,
    )
    return success(body=response.dict())


def _delete_unrecorded_key(key_id: str, context) -> None:
    """Delete a minted key that has no table row, so nothing would ever clean it up. The delete
    gets its own short grace period, so it is attempted even after the request deadline."""
    try:
        mirisIntegration.delete_viewer_key(key_id, deadline=mirisIntegration.cleanup_deadline(context))
        logger.info(f"Deleted unrecorded Miris viewer key {key_id}")
    except Exception as e:
        logger.error(f"Could not delete unrecorded Miris viewer key {key_id} ({type(e).__name__})")
