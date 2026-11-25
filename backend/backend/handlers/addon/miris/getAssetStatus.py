# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""GET /addon/miris/asset-status?databaseId=<id>&assetId=<id>&filePath=<path or object key>

Reports the Miris stream recorded on one asset file. The Miris asset UUID is read server side
from the file's metadata (`miris_asset_uuid` in the asset file metadata table, keyed by exactly
this databaseId, assetId and filePath). The request carries no UUID, so a caller can only learn
the status of a stream that the file itself references.

`filePath` is the asset-relative path (`/folder/scene.usd`) or the file's full object key. A
key under the asset's root key (`assetLocation.Key`) or under `{assetId}/` is reduced to the
relative path the metadata service stores the file's metadata under.

Response body:

    filePath         the normalized asset-relative path, e.g. "/scene.usd"
    mirisAssetUuid   `miris_asset_uuid`, or null when it is absent or empty
    sourceVersionId  `miris_source_version_id`, or null when absent ("" when the source
                     version was not known at upload time)
    uploadedAt       `miris_uploaded_at` (ISO 8601), or null when absent
    displayName      `miris_display_name`, or null when absent
    state            one of the states below
    stale            whether a file the stream was built from has changed since the upload (see
                     Staleness), or null when that cannot be established

States:

    notStreamed    `miris_asset_uuid` is absent or empty. Miris is not called.
    invalidUuid    `miris_asset_uuid` is set but is not a UUID. Miris is not called.
    notBound       Miris has no such asset (404), reports it `deleted`, or the asset lacks either
                   tag that binds it to this VAMS asset, `vams-asset-{assetId}` and
                   `vams-database-{databaseId}`.
    unknown        The processing state could not be established: Miris was unreachable, did
                   not answer before the request deadline, returned a non-2xx status, or
                   returned a payload without a usable `state`.
    <Miris state>  The processing state reported by Miris, passed through as is:
                   `preview` while Miris prepares the asset, `streamable` once it can be
                   streamed, and `error` or `failed` for a definitive processing failure.

The asset, its tags and its processing state are read with the Miris Integration Key
(`GET /v1/asset/{uuid}`, see `mirisIntegration`), which also establishes the binding. The
Integration Key resolves to its owning Miris user's workspace, which must be the workspace holding
the assets.

The probe is advisory and must never block streaming. Everything short of a definitive Miris
processing failure or a missing binding is reported as `unknown`, and the response still carries
the UUID so the client can attempt the stream.

Staleness: the upload pipeline records on the file the id of a dependency manifest
(`miris_dependency_manifest`), stored in the auxiliary bucket, that lists the root USD file and
every file of its dependency tree (sublayers, references, textures and other assets) with the ETag
the stream was built from. `stale` is true when a listed file no longer exists under the asset's
root prefix or its current ETag differs, and false when every listed file matches. It is null when
the file records no manifest (hand-written stream metadata), the file records no usable Miris asset
UUID, the state is `notBound` or `unknown` (nothing is read), or the manifest or listing cannot be
read, fails its SHA-256 check, or would not finish before the request deadline. The comparison
reads the manifest once and lists the asset's root prefix once (one paginated ListObjectsV2), after
the Miris calls and within the same deadline. Each of its calls uses clients with short timeouts and
at most two attempts, and no call starts with less than one call's worst case left.

Authorization is two-tier:
  - Tier 1: enforceAPI on the route
  - Tier 2: enforce GET on the asset named by databaseId and assetId, before any metadata is read
"""
import re
import time
from typing import Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.config import Config

from common.addonApiRoutes import API_ADDON_MIRIS_ASSET_STATUS
from common.resourceNames import ResourceKeys, get_bucket_name, get_table_name
from common.s3 import (
    S3_OBJECTS_PAGE_SIZE,
    bucket_region_fields,
    deployment_region,
    region_routing_s3_client,
    register_bucket_region,
)
from customLogging.auditLogging import audited_handler, log_action
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
    MIRIS_DEPENDENCY_MANIFEST_KEY,
    MIRIS_DISPLAY_NAME_KEY,
    MIRIS_SOURCE_VERSION_ID_KEY,
    MIRIS_STATE_DELETED,
    MIRIS_UPLOADED_AT_KEY,
    STATE_NOT_BOUND,
    STATE_UNKNOWN,
    GetMirisAssetStatusRequestModel,
    MirisAssetStatusResponseModel,
)

from . import dependencyManifest, mirisIntegration
from .streamMetadata import (
    file_path_for_asset,
    load_readable_asset,
    read_stream_metadata,
    stream_uuid,
    unusable_uuid_state,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
# The staleness reads (buckets table, manifest, listing): short timeouts and at most two attempts,
# so each call ends well inside the request deadline.
_STALENESS_CONNECT_TIMEOUT_SECONDS = 1
_STALENESS_READ_TIMEOUT_SECONDS = 2
_STALENESS_TOTAL_MAX_ATTEMPTS = 2
staleness_call_config = Config(
    connect_timeout=_STALENESS_CONNECT_TIMEOUT_SECONDS,
    read_timeout=_STALENESS_READ_TIMEOUT_SECONDS,
    retries={"total_max_attempts": _STALENESS_TOTAL_MAX_ATTEMPTS, "mode": "standard"},
)
staleness_dynamodb = boto3.resource("dynamodb", config=staleness_call_config)
logger = safeLogger(service_name="MirisGetAssetStatus")

claims_and_roles = {}

try:
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    asset_file_metadata_table_name = get_table_name(ResourceKeys.ASSET_FILE_METADATA_STORAGE_TABLE)
    s3_asset_buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    # The dependency manifests the upload pipeline writes.
    auxiliary_bucket_name = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)
except Exception:
    logger.exception("Failed loading resource names")
    raise

asset_table = dynamodb.Table(asset_storage_table_name)
asset_file_metadata_table = dynamodb.Table(asset_file_metadata_table_name)
buckets_table = staleness_dynamodb.Table(s3_asset_buckets_table_name)
# Each call is signed for the Region of the bucket it names, on a client with the staleness timeouts.
s3 = region_routing_s3_client(staleness_call_config)
# The auxiliary bucket is in the deployment Region.
register_bucket_region(auxiliary_bucket_name, deployment_region())

# Worst case of one staleness call: every attempt's timeouts, plus the standard mode's backoff of at
# most 2^(n-1) seconds before retry n. No call starts with less than this left before the deadline.
_STALENESS_MIN_SECONDS = _STALENESS_TOTAL_MAX_ATTEMPTS * (
    _STALENESS_CONNECT_TIMEOUT_SECONDS + _STALENESS_READ_TIMEOUT_SECONDS
) + sum(2 ** (n - 1) for n in range(1, _STALENESS_TOTAL_MAX_ATTEMPTS))
# The manifest is read in chunks of this size, with a deadline check before each.
_MANIFEST_CHUNK_BYTES = 1024 * 1024
# A manifest larger than this is not read.
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024

# A Miris `state` is passed through only when it looks like a state token.
_MIRIS_STATE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


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

        if method == "GET" and API_ADDON_MIRIS_ASSET_STATUS.matches(path):
            response = _handle_get(event, context)
            if response.get("statusCode") == 200:
                query = event.get("queryStringParameters") or {}
                log_action(event, "mirisAssetStatusGet", database_id=query.get("databaseId"),
                           asset_id=query.get("assetId"), file_path=query.get("filePath"),
                           request_parameters={"databaseId": query.get("databaseId"),
                                               "assetId": query.get("assetId"),
                                               "filePath": query.get("filePath")})
            return response
        return validation_error(body={"message": "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={"message": str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)


def _handle_get(event, context):
    query_params = event.get("queryStringParameters") or {}

    request_model = parse(
        {
            "databaseId": query_params.get("databaseId"),
            "assetId": query_params.get("assetId"),
            "filePath": query_params.get("filePath"),
        },
        model=GetMirisAssetStatusRequestModel,
    )
    database_id = request_model.databaseId
    asset_id = request_model.assetId

    asset, denied = load_readable_asset(
        asset_table, CasbinEnforcer(claims_and_roles), database_id, asset_id, event
    )
    if denied:
        return denied

    # A full object key is reduced against this asset's own root key only.
    file_path = file_path_for_asset(asset, asset_id, request_model.filePath)

    stream_metadata = read_stream_metadata(
        asset_file_metadata_table, database_id, asset_id, file_path
    )

    miris_asset_uuid = stream_uuid(stream_metadata)
    deadline = mirisIntegration.request_deadline(context, event)
    state = _stream_state(miris_asset_uuid, database_id, asset_id, deadline)
    stale = None
    # A stream Miris cannot vouch for (missing, unbound or unreadable) is not compared.
    if unusable_uuid_state(miris_asset_uuid) is None and state not in (STATE_NOT_BOUND, STATE_UNKNOWN):
        stale = _stream_staleness(
            asset, database_id, asset_id, stream_metadata.get(MIRIS_DEPENDENCY_MANIFEST_KEY), deadline
        )
    response = MirisAssetStatusResponseModel(
        filePath=file_path,
        mirisAssetUuid=miris_asset_uuid or None,
        sourceVersionId=stream_metadata.get(MIRIS_SOURCE_VERSION_ID_KEY),
        uploadedAt=stream_metadata.get(MIRIS_UPLOADED_AT_KEY),
        displayName=stream_metadata.get(MIRIS_DISPLAY_NAME_KEY),
        state=state,
        stale=stale,
    )
    return success(body=response.dict())


def _stream_state(miris_asset_uuid: str, database_id: str, asset_id: str, deadline: float) -> str:
    """Resolve the response `state` for the UUID stored on the file."""
    unusable_state = unusable_uuid_state(miris_asset_uuid)
    if unusable_state is not None:
        if miris_asset_uuid:
            logger.warning("miris_asset_uuid on the file is not a UUID")
        return unusable_state

    miris_asset, state = _read_bound_asset(miris_asset_uuid, database_id, asset_id, deadline)
    return state if miris_asset is None else _usable_state(miris_asset.state)


def _usable_state(state: str) -> str:
    if not _MIRIS_STATE_PATTERN.fullmatch(state):
        logger.warning("Miris asset response contained no usable state")
        return STATE_UNKNOWN
    if state == MIRIS_STATE_DELETED:
        return STATE_NOT_BOUND
    return state


def _read_bound_asset(miris_asset_uuid: str, database_id: str, asset_id: str,
                      deadline: float) -> Tuple[Optional[mirisIntegration.MirisAsset], str]:
    """Read the Miris asset with the Integration Key and check its binding to this VAMS asset.

    Returns `(asset, "")` for a bound asset, or `(None, state)` with `notBound` for a missing,
    deleted or unbound asset and `unknown` when the read failed.
    """
    try:
        miris_asset = mirisIntegration.get_asset(miris_asset_uuid, deadline=deadline)
    except mirisIntegration.MirisIntegrationError as e:
        logger.warning(f"Miris asset read failed for {miris_asset_uuid}: {e}")
        return None, STATE_UNKNOWN

    if miris_asset is None:
        logger.warning(f"Miris has no asset {miris_asset_uuid}")
        return None, STATE_NOT_BOUND
    if miris_asset.state == MIRIS_STATE_DELETED:
        logger.warning(f"Miris reports asset {miris_asset_uuid} deleted")
        return None, STATE_NOT_BOUND
    if not mirisIntegration.is_bound_to_asset(miris_asset.tags, database_id, asset_id):
        logger.warning(f"Miris asset {miris_asset_uuid} is not tagged for {database_id}/{asset_id}")
        return None, STATE_NOT_BOUND
    return miris_asset, ""


class _StalenessUnavailable(Exception):
    """Staleness cannot be established within this request."""


def _stream_staleness(asset: dict, database_id: str, asset_id: str, manifest_id: Optional[str],
                      deadline: float) -> Optional[bool]:
    """Compare the file's dependency manifest with the asset's current files: True when a listed
    file is missing or changed, False when all match, None when it cannot be established."""
    manifest_id = (manifest_id or "").strip()
    if not manifest_id:
        return None
    if not dependencyManifest.is_manifest_id(manifest_id):
        logger.warning(f"{MIRIS_DEPENDENCY_MANIFEST_KEY} on {database_id}/{asset_id} is not a manifest id")
        return None
    try:
        return _compare_with_manifest(asset, database_id, asset_id, manifest_id, deadline)
    except Exception as e:
        logger.warning(
            f"Could not establish whether the Miris stream of {database_id}/{asset_id} is stale: "
            f"{type(e).__name__}: {str(e)[:300]}"
        )
        return None


def _check_deadline(deadline: float) -> None:
    """Raise `_StalenessUnavailable` unless one call's worst case still fits before `deadline`."""
    if deadline - time.monotonic() < _STALENESS_MIN_SECONDS:
        raise _StalenessUnavailable("Too little time is left before the request deadline")


def _compare_with_manifest(asset: dict, database_id: str, asset_id: str, manifest_id: str,
                           deadline: float) -> bool:
    root_prefix = dependencyManifest.asset_root_prefix(asset)
    if not root_prefix:
        raise _StalenessUnavailable("The asset has no root prefix")
    manifest_etags = _read_manifest(database_id, asset_id, manifest_id, deadline)
    _check_deadline(deadline)
    bucket_name = get_asset_bucket_details(asset.get("bucketId"))["bucketName"]

    current_etags = {}
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(
        Bucket=bucket_name, Prefix=root_prefix, PaginationConfig={"PageSize": S3_OBJECTS_PAGE_SIZE}
    )
    # Each page is requested as the loop advances, so the check before it bounds every request.
    _check_deadline(deadline)
    for page in pages:
        current_etags.update(dependencyManifest.listed_etags(page.get("Contents", []), root_prefix))
        _check_deadline(deadline)
    return dependencyManifest.is_stale(manifest_etags, current_etags)


def _read_manifest(database_id: str, asset_id: str, manifest_id: str, deadline: float) -> dict:
    """The manifest's {asset-relative path: ETag}, read from the auxiliary bucket in chunks and
    checked against its id."""
    _check_deadline(deadline)
    response = s3.get_object(
        Bucket=auxiliary_bucket_name,
        Key=dependencyManifest.manifest_key(database_id, asset_id, manifest_id),
    )
    body = response["Body"]
    chunks, size = [], 0
    try:
        if int(response.get("ContentLength") or 0) > _MAX_MANIFEST_BYTES:
            raise _StalenessUnavailable("The manifest is larger than the read limit")
        while True:
            _check_deadline(deadline)
            chunk = body.read(_MANIFEST_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_MANIFEST_BYTES:
                raise _StalenessUnavailable("The manifest is larger than the read limit")
            chunks.append(chunk)
    finally:
        body.close()
    return dependencyManifest.parse_manifest(b"".join(chunks), manifest_id)


def get_asset_bucket_details(bucket_id) -> dict:
    """The asset bucket's name, base prefix, Region and owning account, from the buckets table."""
    if not bucket_id:
        raise _StalenessUnavailable("The asset records no bucket")
    response = buckets_table.query(KeyConditionExpression=Key("bucketId").eq(bucket_id), Limit=1)
    items = response.get("Items") or []
    bucket = items[0] if items else {}
    if not bucket.get("bucketName"):
        raise _StalenessUnavailable("The asset's bucket is not registered")
    return {"bucketId": bucket_id, "bucketName": bucket["bucketName"], **bucket_region_fields(bucket)}
