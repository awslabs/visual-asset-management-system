# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download as Zip API (prepared downloads).

Routes (common/apiRoutes.py ZIP_DOWNLOAD_ROUTES):
  POST   /database/{databaseId}/assets/{assetId}/download/zip                    create a job
  GET    /database/{databaseId}/assets/{assetId}/download/zip                    caller's jobs for the asset
  GET    /download/zip                                                           caller's jobs, all assets
  GET    /database/{databaseId}/assets/{assetId}/download/zip/{requestId}        one job
  DELETE /database/{databaseId}/assets/{assetId}/download/zip/{requestId}        cancel or delete
  POST   /database/{databaseId}/assets/{assetId}/download/zip/{requestId}/url    presigned URL

Every call: Tier-1 route check, then the feature check (404 while app.zipDownloads is off), then
validation, then Tier-2 asset GET (the download path's action). Jobs are keyed by the AUTHENTICATED
user id, so a caller only ever reads, signs, cancels or deletes their own; another user's job, or a
job named under another asset's path, answers the same 404 as a missing one.

A create resolves and checks the selection (refusing the whole job on any archived, delete-marker,
archive-storage-class or blocked file, owner decision), writes the request manifest and a QUEUED row,
and launches the archive system workflow as SYSTEM_USER through the system-pipeline cross-call
(common/workflows/systemPipelines.py). The archive container writes progress and the final status;
the terminal callback (zipDownloadCallback.py) and the read-time reconcile here finalize what it
could not.
"""

import base64
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext

from common import zipDownloads as zd
from common.apiRoutes import (
    API_ZIP_DOWNLOADS_ASSET,
    API_ZIP_DOWNLOADS_ASSET_REQUEST,
    API_ZIP_DOWNLOADS_ASSET_REQUEST_URL,
    API_ZIP_DOWNLOADS_GLOBAL,
)
from common.constants import UNALLOWED_FILE_EXTENSION_LIST, UNALLOWED_MIME_LIST
from common.downloadEligibility import (
    MSG_FILE_NOT_FOUND,
    MSG_FILE_TYPE_NOT_ALLOWED,
    MSG_VERSION_ARCHIVED,
    STATUS_VERSION_ARCHIVED,
    asset_relative_key,
    is_file_archived,
    require_distributable_asset,
    resolve_asset_file_key,
)
from common.dynamodb import query_all_items
from common.resourceNames import ResourceKeys, get_bucket_name, get_table_name
from common.s3 import region_routing_s3_client, validateUnallowedFileExtensionAndContentType
from common.validators import validate
from common.workflows import systemPipelines as sp
from customLogging.auditLogging import log_actions
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
from models.zipDownloads import CreateZipDownloadRequestModel, ListZipDownloadsQueryModel

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
lambda_client = boto3.client("lambda", config=retry_config)
s3 = region_routing_s3_client()
logger = safeLogger(service_name="ZipDownloadService")
claims_and_roles = {}


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


ZIPDOWNLOADS_ENABLED = os.environ.get("ZIPDOWNLOADS_ENABLED", "false").strip().lower() == "true"
RETENTION_DAYS = _env_int("ZIP_DOWNLOAD_RETENTION_DAYS", 7)
MAX_FILES_PER_REQUEST = _env_int("ZIP_DOWNLOAD_MAX_FILES_PER_REQUEST", 100000)
MAX_TOTAL_BYTES_PER_REQUEST = _env_int("ZIP_DOWNLOAD_MAX_TOTAL_BYTES_PER_REQUEST", 5497558138880)
MAX_ACTIVE_PER_USER = _env_int("ZIP_DOWNLOAD_MAX_ACTIVE_PER_USER", 5)
ARCHIVE_WORKFLOW_DATABASE_ID = os.environ.get("ZIP_ARCHIVE_WORKFLOW_DATABASE_ID",
                                              zd.ARCHIVE_WORKFLOW_DATABASE_ID_DEFAULT)
ARCHIVE_WORKFLOW_ID = os.environ.get("ZIP_ARCHIVE_WORKFLOW_ID", zd.ARCHIVE_WORKFLOW_ID_DEFAULT)
EXECUTE_FUNCTION = os.environ.get("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "")
EXECUTION_SERVICE_FUNCTION = os.environ.get("EXECUTION_SERVICE_FUNCTION_NAME", "")
PRESIGNED_URL_TIMEOUT_SECONDS = _env_int("PRESIGNED_URL_TIMEOUT_SECONDS", 900)

# Create-time pre-scan of current-files folder / whole-asset entries. Beyond either bound the archive
# container (which checks every member before writing a byte) enforces the refusal rules and limits.
PRESCAN_MAX_OBJECTS = _env_int("ZIP_DOWNLOAD_PRESCAN_MAX_OBJECTS", 10000)
PRESCAN_MAX_SECONDS = float(os.environ.get("ZIP_DOWNLOAD_PRESCAN_MAX_SECONDS", "8"))
# Create-time HeadObject checks of explicit and asset-version-pinned files.
HEAD_CHECK_MAX_FILES = _env_int("ZIP_DOWNLOAD_HEAD_CHECK_MAX_FILES", 2000)
HEAD_CHECK_WORKERS = _env_int("ZIP_DOWNLOAD_HEAD_CHECK_WORKERS", 25)
RECONCILE_MAX_PER_CALL = 25

try:
    zip_table_name = get_table_name(ResourceKeys.ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    executions_table_name = get_table_name(ResourceKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2)
    aux_bucket_name = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

zip_table = dynamodb.Table(zip_table_name)
asset_table = dynamodb.Table(asset_table_name)
buckets_table = dynamodb.Table(buckets_table_name)
executions_table = dynamodb.Table(executions_table_name)

USER_ASSET_INDEX = "UserAssetIndex"


class ZipRefusal(VAMSGeneralErrorResponse):
    """A refused create (400, or the given status)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _not_found(event):
    return general_error(status_code=404, body={"message": zd.MSG_NOT_FOUND}, event=event)


def _caller_id() -> str:
    tokens = claims_and_roles.get("tokens") or []
    return tokens[0] if tokens else ""


def _json_body(event):
    body = event.get("body")
    if body is None or body == "":
        return {}
    if isinstance(body, dict):
        return body
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            raise VAMSGeneralErrorResponse("Invalid JSON in request body")
        if not isinstance(parsed, dict):
            raise VAMSGeneralErrorResponse("Request body must be a JSON object")
        return parsed
    raise VAMSGeneralErrorResponse("Request body cannot be parsed")


def _validate_path(database_id, asset_id, request_id=None):
    checks = {"databaseId": {"value": database_id, "validator": "ID"},
              "assetId": {"value": asset_id, "validator": "ASSET_ID"}}
    (valid, message) = validate(checks)
    if not valid:
        return message
    if request_id is not None and not zd.is_request_id(request_id):
        return "requestId is invalid"
    return None


def _get_asset(database_id, asset_id):
    """The live asset record (archived assets are not in the live table) or None."""
    response = asset_table.get_item(Key={"databaseId": database_id, "assetId": asset_id})
    return response.get("Item")


def _can_read_asset(asset) -> bool:
    """Tier-2 GET on the asset (the download path's action). Fails closed on empty tokens."""
    if not asset:
        return False
    if len(claims_and_roles.get("tokens") or []) == 0:
        return False
    obj = dict(asset)
    obj["object__type"] = "asset"
    return bool(CasbinEnforcer(claims_and_roles).enforce(obj, "GET"))


def _bucket_details(bucket_id):
    response = buckets_table.query(KeyConditionExpression=Key("bucketId").eq(bucket_id), Limit=1)
    items = response.get("Items") or []
    if not items or not items[0].get("bucketName"):
        raise VAMSGeneralErrorResponse("Error getting asset bucket details.")
    return items[0]


def _plain(value):
    """DynamoDB Decimals to int for JSON."""
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _encode_token(last_key) -> str:
    return base64.b64encode(json.dumps(_plain(last_key)).encode("utf-8")).decode("utf-8")


def _decode_token(token, expected: dict, allowed_keys) -> dict:
    """Decode a startingToken and check it names the caller's own partition. Any other token is a
    caller error, so a token can never move a query outside the caller's own rows."""
    try:
        decoded = json.loads(base64.b64decode(token.encode("utf-8"), validate=True).decode("utf-8"))
    except Exception:
        raise VAMSGeneralErrorResponse("Invalid startingToken")
    if not isinstance(decoded, dict) or set(decoded) != set(allowed_keys):
        raise VAMSGeneralErrorResponse("Invalid startingToken")
    for key, value in expected.items():
        if decoded.get(key) != value:
            raise VAMSGeneralErrorResponse("Invalid startingToken")
    if not all(isinstance(v, str) for v in decoded.values()):
        raise VAMSGeneralErrorResponse("Invalid startingToken")
    return decoded


# ---------------------------------------------------------------------------
# Selection resolution and eligibility (create time)
# ---------------------------------------------------------------------------

def _head(bucket, key, version_id=None):
    params = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    return s3.head_object(**params)


def _check_member(bucket, key, version_id):
    """Every per-file refusal rule on one resolved member. Returns its size."""
    try:
        head = _head(bucket, key, version_id)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound", "NoSuchVersion"):
            raise ZipRefusal(MSG_FILE_NOT_FOUND)
        if code in ("405", "MethodNotAllowed"):
            raise ZipRefusal(MSG_VERSION_ARCHIVED, status_code=STATUS_VERSION_ARCHIVED)
        if code in ("400", "InvalidArgument"):
            raise ZipRefusal(MSG_FILE_NOT_FOUND)
        raise
    if is_file_archived(head):
        raise ZipRefusal("The selection includes an archived file; archived files cannot be "
                         "included in a zip download")
    if not validateUnallowedFileExtensionAndContentType(key, head.get("ContentType", "")):
        raise ZipRefusal(MSG_FILE_TYPE_NOT_ALLOWED)
    return int(head.get("ContentLength", 0))


def _check_members(bucket, members):
    """Head-check members in parallel; the first refusal refuses the job."""
    with ThreadPoolExecutor(max_workers=max(1, HEAD_CHECK_WORKERS)) as pool:
        futures = [pool.submit(_check_member, bucket, m["key"], m.get("versionId")) for m in members]
        sizes = []
        try:
            for future in futures:
                sizes.append(future.result())
        finally:
            for future in futures:
                future.cancel()
    return sizes


def _prescan_prefix(bucket, prefix, deadline, budget):
    """List the current (latest, not deleted) objects under a prefix within a budget.

    Returns (count, bytes, complete). Refuses on an archive storage class or a blocked extension.
    Folder marker keys (ending in '/') are not files."""
    count, total, kwargs = 0, 0, {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
    while True:
        if time.monotonic() > deadline or budget[0] <= 0:
            return count, total, False
        response = s3.list_object_versions(**kwargs)
        for version in response.get("Versions", []) or []:
            key = version.get("Key", "")
            if not version.get("IsLatest") or key.endswith("/"):
                continue
            if version.get("StorageClass") in ("GLACIER", "DEEP_ARCHIVE"):
                raise ZipRefusal("The selection includes a file in an archive storage class; "
                                 "archived files cannot be included in a zip download")
            if not validateUnallowedFileExtensionAndContentType(key, ""):
                raise ZipRefusal(MSG_FILE_TYPE_NOT_ALLOWED)
            count += 1
            total += int(version.get("Size", 0))
            budget[0] -= 1
        if not response.get("IsTruncated"):
            return count, total, True
        kwargs["KeyMarker"] = response.get("NextKeyMarker", "")
        kwargs["VersionIdMarker"] = response.get("NextVersionIdMarker", "")


def _enforce_limits(files, size):
    if files > MAX_FILES_PER_REQUEST:
        raise ZipRefusal(f"The selection has more than {MAX_FILES_PER_REQUEST} files")
    if size > MAX_TOTAL_BYTES_PER_REQUEST:
        raise ZipRefusal(f"The selection is larger than {MAX_TOTAL_BYTES_PER_REQUEST} bytes")


def _resolve_pinned(database_id, asset_id, bucket, base_key, request):
    """Resolve the selection against an asset version snapshot: every selected file, folder or the
    whole asset becomes the file versions recorded in that version."""
    # Imported here: the asset-version helpers are a handler module with its own module-level setup.
    from handlers.assets.assetVersions import (
        get_asset_file_versions,
        resolve_asset_version_id_from_alias,
        validate_asset_version_exists,
    )
    asset_version_id = request.assetVersionId
    if request.assetVersionIdAlias:
        asset_version_id = resolve_asset_version_id_from_alias(database_id, asset_id,
                                                               request.assetVersionIdAlias)
    validate_asset_version_exists(database_id, asset_id, asset_version_id)
    snapshot = get_asset_file_versions(database_id, asset_id, asset_version_id) or {}
    recorded = {}
    for f in snapshot.get("files") or []:
        rel = (f.get("relativeKey") or "").lstrip("/")
        if rel and f.get("versionId"):
            recorded[rel] = f
    chosen = {}
    for entry in request.selection:
        rel = entry.relativePath.lstrip("/")
        if entry.type == "asset":
            chosen.update(recorded)
        elif entry.type == "folder":
            matched = {k: v for k, v in recorded.items() if k.startswith(rel)}
            if not matched:
                raise ZipRefusal("A selected folder has no files in the requested asset version")
            chosen.update(matched)
        else:
            if rel not in recorded:
                raise ZipRefusal("File not found in the requested asset version")
            chosen[rel] = recorded[rel]
    if not chosen:
        raise ZipRefusal("No files found in the requested asset version")
    pinned = []
    for rel in sorted(chosen):
        key = resolve_asset_file_key(base_key, "/" + rel)
        pinned.append({"key": key, "relativePath": "/" + rel,
                       "versionId": chosen[rel]["versionId"], "size": int(chosen[rel].get("size") or 0)})
    _enforce_limits(len(pinned), sum(p["size"] for p in pinned))
    if len(pinned) <= HEAD_CHECK_MAX_FILES:
        sizes = _check_members(bucket, pinned)
        for p, size in zip(pinned, sizes):
            p["size"] = size
        _enforce_limits(len(pinned), sum(sizes))
    return asset_version_id, pinned


def _resolve_current(bucket, base_key, request):
    """Resolve a current-files selection. Explicit files are fully checked; folders and the whole
    asset are pre-scanned within a budget. Returns (entries, files, bytes, complete)."""
    entries, explicit = [], []
    for entry in request.selection:
        key = resolve_asset_file_key(base_key, entry.relativePath)
        record = {"type": entry.type, "key": key, "relativePath": entry.relativePath}
        if entry.type == "file":
            record["versionId"] = entry.versionId or None
            explicit.append(record)
        entries.append(record)
    if len(explicit) > HEAD_CHECK_MAX_FILES:
        raise ZipRefusal(f"Select at most {HEAD_CHECK_MAX_FILES} individual files, or select their "
                         f"folder")
    sizes = _check_members(bucket, explicit) if explicit else []
    files, total, complete = len(explicit), sum(sizes), True
    deadline = time.monotonic() + PRESCAN_MAX_SECONDS
    budget = [PRESCAN_MAX_OBJECTS]
    for record in entries:
        if record["type"] == "file":
            continue
        count, size, done = _prescan_prefix(bucket, record["key"], deadline, budget)
        if done and count == 0:
            raise ZipRefusal("A selected folder is empty or does not exist")
        files, total = files + count, total + size
        complete = complete and done
        _enforce_limits(files, total)
    return entries, files, total, complete


# ---------------------------------------------------------------------------
# Launch, reconcile, cleanup
# ---------------------------------------------------------------------------

def _launch(row, request_entries, pinned):
    """Launch the archive system workflow for a job as SYSTEM_USER. Returns the execution id."""
    if not EXECUTE_FUNCTION:
        raise RuntimeError("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME is not configured")
    database_id, asset_id = row["databaseId"], row["assetId"]
    if pinned or len(request_entries) > 1000:
        input_paths = ["/"]
    else:
        input_paths = [e["relativePath"] for e in request_entries]
    body = {"inputFiles": [{"databaseId": database_id, "assetId": asset_id, "relativeFileKey": p}
                           for p in input_paths]}
    context = {"v": zd.CONTEXT_VERSION, "requestId": row["requestId"], "userId": row["userId"],
               "databaseId": database_id, "assetId": asset_id, "manifestKey": row["manifestKey"]}
    execute_path = f"/workflows/{ARCHIVE_WORKFLOW_DATABASE_ID}/{ARCHIVE_WORKFLOW_ID}/execute"
    invoke_event = {
        "requestContext": {"http": {"method": "POST", "path": execute_path}},
        "pathParameters": {"workflowDatabaseId": ARCHIVE_WORKFLOW_DATABASE_ID,
                           "workflowId": ARCHIVE_WORKFLOW_ID},
        "queryStringParameters": {},
        "body": json.dumps(body),
        "lambdaCrossCall": {"userName": sp.SYSTEM_PIPELINE_USER_NAME,
                            "source": sp.SYSTEM_PIPELINE_SOURCE_MARKER},
        sp.SYSTEM_EXECUTION_EVENT_KEY: {"triggerType": zd.ZIP_DOWNLOAD_REQUEST_TRIGGER,
                                        "context": context},
    }
    response = lambda_client.invoke(FunctionName=EXECUTE_FUNCTION, InvocationType="RequestResponse",
                                    Payload=json.dumps(invoke_event).encode("utf-8"))
    payload = response.get("Payload")
    inner = json.loads(payload.read().decode("utf-8")) if payload else {}
    status_code = inner.get("statusCode", 500)
    inner_body = json.loads(inner["body"]) if isinstance(inner.get("body"), str) and inner.get("body") else {}
    if status_code != 200 or not inner_body.get("executionId"):
        raise RuntimeError(f"archive workflow launch answered {status_code}: "
                           f"{str(inner_body.get('message', ''))[:500]}")
    return inner_body["executionId"]


def _abort_execution(execution_id):
    """Abort the job's workflow run through the execution service's abort route (SYSTEM_USER
    cross-call), which also stops its Batch job. Best-effort: the terminal callback and the
    abort-incomplete-multipart lifecycle rule are the backstops."""
    if not execution_id or not EXECUTION_SERVICE_FUNCTION:
        return False
    invoke_event = {
        "requestContext": {"http": {"method": "DELETE",
                                    "path": f"/workflows/executions/{execution_id}"}},
        "pathParameters": {"executionId": execution_id},
        "queryStringParameters": {},
        "lambdaCrossCall": {"userName": sp.SYSTEM_PIPELINE_USER_NAME},
    }
    try:
        response = lambda_client.invoke(FunctionName=EXECUTION_SERVICE_FUNCTION,
                                        InvocationType="RequestResponse",
                                        Payload=json.dumps(invoke_event).encode("utf-8"))
        payload = response.get("Payload")
        inner = json.loads(payload.read().decode("utf-8")) if payload else {}
        if inner.get("statusCode") != 200:
            logger.warning(f"Abort of zip download run {execution_id} answered {inner.get('statusCode')}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Abort of zip download run {execution_id} failed (non-critical): {e}")
        return False


def _job_prefix(row):
    return zd.request_prefix(row["databaseId"], row["assetId"], row["userId"], row["requestId"])


def _reconcile(row):
    """Finalize a non-terminal row whose run already ended (or never started). Returns the row as it
    reads after any write."""
    if row.get("status") not in zd.ACTIVE_STATUSES:
        return row
    execution_id = row.get("workflowExecutionId")
    if not execution_id:
        try:
            created = datetime.strptime(row.get("createdAt") or "", "%Y-%m-%dT%H:%M:%SZ")
            age = (zd.utc_now() - created.replace(tzinfo=timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            age = 0
        if age > zd.LAUNCH_GRACE_SECONDS:
            if zd.finalize_row(zip_table, row["userId"], row["requestId"], zd.STATUS_FAILED,
                               error_message=zd.MSG_LAUNCH_FAILED):
                row = dict(row, status=zd.STATUS_FAILED, errorMessage=zd.MSG_LAUNCH_FAILED)
        return row
    response = executions_table.get_item(Key={
        "workflowExecutionId": execution_id,
        # The main row's sort key (common.workflows.executionRecords.workflow_composite_key).
        "workflowDatabaseId:workflowId": f"{ARCHIVE_WORKFLOW_DATABASE_ID}:{ARCHIVE_WORKFLOW_ID}"})
    main = response.get("Item") or {}
    decision = zd.decide_finalization(
        main.get("executionStatus", ""), row, main.get("executionError", "") or "",
        archive_exists=lambda: zd.archive_head(s3, aux_bucket_name, row.get("archiveKey", "")))
    if not decision:
        return row
    status, fields = decision
    if zd.finalize_row(zip_table, row["userId"], row["requestId"], status,
                       expected_execution_id=execution_id, **fields):
        if status != zd.STATUS_SUCCEEDED:
            zd.abort_open_uploads(s3, aux_bucket_name, _job_prefix(row), logger)
    fresh = zip_table.get_item(Key=zd.row_key(row["userId"], row["requestId"]),
                               ConsistentRead=True).get("Item")
    return fresh or row


def _owned_row(event, database_id, asset_id, request_id):
    """The caller's own, unexpired job under this asset path, or None."""
    row = zip_table.get_item(Key=zd.row_key(_caller_id(), request_id), ConsistentRead=True).get("Item")
    if not row or row.get("databaseId") != database_id or row.get("assetId") != asset_id:
        return None
    if zd.is_expired(row):
        return None
    return row


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def _active_job_count(user_id) -> int:
    rows = query_all_items(
        zip_table,
        KeyConditionExpression=Key("userId").eq(user_id),
        FilterExpression=Attr("status").is_in(list(zd.ACTIVE_STATUSES)),
        ProjectionExpression="requestId, expiresAt, #s",
        ExpressionAttributeNames={"#s": "status"},
    )
    return sum(1 for r in rows if not zd.is_expired(r))


def create_job(event, database_id, asset_id):
    try:
        request = parse(_json_body(event), model=CreateZipDownloadRequestModel)
    except ValidationError as v:
        logger.error(f"Validation error: {v}")
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    asset = _get_asset(database_id, asset_id)
    if not asset:
        return general_error(status_code=404, body={"message": "Asset not found"}, event=event)
    if not _can_read_asset(asset):
        return authorization_error()
    user_id = _caller_id()
    try:
        asset_location = require_distributable_asset(asset)
        bucket = _bucket_details(asset.get("bucketId"))["bucketName"]
        base_key = asset_location.get("Key")
        if _active_job_count(user_id) >= MAX_ACTIVE_PER_USER:
            return general_error(status_code=429, body={"message": zd.MSG_CAP_REACHED}, event=event)
        pinned, asset_version_id = [], None
        if request.assetVersionId or request.assetVersionIdAlias:
            asset_version_id, pinned = _resolve_pinned(database_id, asset_id, bucket, base_key, request)
            entries, files, total, complete = [], len(pinned), sum(p["size"] for p in pinned), True
        else:
            entries, files, total, complete = _resolve_current(bucket, base_key, request)
    except VAMSGeneralErrorResponse as e:
        log_actions(event, "zipDownloadRefused", {"databaseId": database_id, "assetId": asset_id})
        return general_error(status_code=getattr(e, "status_code", 400), body={"message": str(e)},
                             event=event)

    now = zd.utc_now()
    request_id = zd.new_request_id()
    archive_file_name = zd.sanitize_archive_file_name(request.archiveFileName, request.archiveFormat,
                                                      asset.get("assetName", asset_id), now)
    row = {
        "userId": user_id,
        "requestId": request_id,
        "userAssetKey": zd.user_asset_key(user_id, database_id, asset_id),
        "databaseId": database_id,
        "assetId": asset_id,
        "assetName": asset.get("assetName", ""),
        "status": zd.STATUS_QUEUED,
        "archiveFormat": request.archiveFormat,
        "compression": request.compression if request.archiveFormat == "zip" else "store",
        "archiveFileName": archive_file_name,
        "archiveKey": zd.archive_key(database_id, asset_id, user_id, request_id, archive_file_name),
        "manifestKey": zd.manifest_key(database_id, asset_id, user_id, request_id),
        "selectionSummary": {
            "files": sum(1 for e in request.selection if e.type == "file"),
            "folders": sum(1 for e in request.selection if e.type == "folder"),
            "wholeAsset": any(e.type == "asset" for e in request.selection),
        },
        "selectionPreview": [
            {k: v for k, v in {"type": e.type, "relativePath": e.relativePath,
                               "versionId": e.versionId}.items() if v}
            for e in request.selection[:zd.SELECTION_PREVIEW_ENTRIES]],
        "filesTotal": files if complete else 0,
        "bytesTotal": total if complete else 0,
        "filesDone": 0,
        "bytesDone": 0,
        "createdAt": zd.iso(now),
        "updatedAt": zd.iso(now),
        "expiresAt": zd.expires_at_epoch(now, RETENTION_DAYS),
    }
    if asset_version_id:
        row["assetVersionId"] = asset_version_id
    manifest = {
        "schemaVersion": zd.MANIFEST_SCHEMA_VERSION,
        "mode": "zipDownload",
        "requestId": request_id,
        "userId": user_id,
        "databaseId": database_id,
        "assetId": asset_id,
        "assetBucket": bucket,
        "assetBaseKey": base_key,
        "archiveBucket": aux_bucket_name,
        "archiveKey": row["archiveKey"],
        "resolvedManifestKey": zd.resolved_manifest_key(database_id, asset_id, user_id, request_id),
        "archiveFormat": row["archiveFormat"],
        "compression": row["compression"],
        "archiveFileName": archive_file_name,
        "entries": [{k: v for k, v in e.items() if v is not None} for e in entries],
        "pinnedFiles": pinned,
        "estimatedTotalBytes": total,
        "limits": {"maxFiles": MAX_FILES_PER_REQUEST, "maxTotalBytes": MAX_TOTAL_BYTES_PER_REQUEST},
        "blockedExtensions": sorted(UNALLOWED_FILE_EXTENSION_LIST),
        "blockedContentTypes": sorted(UNALLOWED_MIME_LIST),
    }
    s3.put_object(Bucket=aux_bucket_name, Key=row["manifestKey"],
                  Body=json.dumps(manifest).encode("utf-8"), ContentType="application/json")
    zip_table.put_item(Item=row, ConditionExpression="attribute_not_exists(requestId)")
    try:
        execution_id = _launch(row, entries, pinned)
    except Exception as e:
        logger.exception(f"Zip download {request_id} could not be launched: {e}")
        zd.finalize_row(zip_table, user_id, request_id, zd.STATUS_FAILED,
                        error_message=zd.MSG_LAUNCH_FAILED)
        return internal_error(body={"message": zd.MSG_LAUNCH_FAILED}, event=event)
    started = zd.iso(zd.utc_now())
    try:
        zip_table.update_item(
            Key=zd.row_key(user_id, request_id),
            UpdateExpression="SET workflowExecutionId = :eid, startedAt = :s, updatedAt = :s",
            ConditionExpression="attribute_exists(requestId)",
            ExpressionAttributeValues={":eid": execution_id, ":s": started})
        row.update(workflowExecutionId=execution_id, startedAt=started)
    except ClientError as e:
        logger.exception(f"Could not record the run of zip download {request_id}: {e}")
    # AUDIT LOG: the requester of a run that executes as SYSTEM_USER.
    log_actions(event, "zipDownloadCreate", {
        "databaseId": database_id, "assetId": asset_id, "requestId": request_id,
        "workflowExecutionId": execution_id, "archiveFormat": row["archiveFormat"],
        "files": files, "bytes": total, "resolutionComplete": complete,
        "assetVersionId": asset_version_id})
    return success(body=zd.to_job_item(row))


def list_jobs(event, database_id=None, asset_id=None):
    query_params = event.get("queryStringParameters") or {}
    try:
        query = parse(query_params, model=ListZipDownloadsQueryModel)
    except ValidationError as v:
        logger.error(f"Validation error: {v}")
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    user_id = _caller_id()
    kwargs = {"ScanIndexForward": False, "Limit": min(query.maxItems, query.pageSize)}
    if database_id is not None:
        partition = zd.user_asset_key(user_id, database_id, asset_id)
        kwargs.update(IndexName=USER_ASSET_INDEX, KeyConditionExpression=Key("userAssetKey").eq(partition))
        token_keys, expected = ("userAssetKey", "requestId", "userId"), {"userAssetKey": partition,
                                                                        "userId": user_id}
    else:
        kwargs.update(KeyConditionExpression=Key("userId").eq(user_id))
        token_keys, expected = ("userId", "requestId"), {"userId": user_id}
    if query.startingToken:
        kwargs["ExclusiveStartKey"] = _decode_token(query.startingToken, expected, token_keys)
    response = zip_table.query(**kwargs)
    asset_decisions, items, reconciled = {}, [], 0
    for row in response.get("Items", []) or []:
        if zd.is_expired(row):
            continue
        marker = (row.get("databaseId"), row.get("assetId"))
        if marker not in asset_decisions:
            asset_decisions[marker] = _can_read_asset(_get_asset(*marker))
        if not asset_decisions[marker]:
            continue
        if row.get("status") in zd.ACTIVE_STATUSES and reconciled < RECONCILE_MAX_PER_CALL:
            reconciled += 1
            row = _reconcile(row)
        items.append(zd.to_job_item(row))
    result = {"Items": items}
    if "LastEvaluatedKey" in response:
        result["NextToken"] = _encode_token(response["LastEvaluatedKey"])
    return success(body=result)


def get_job(event, database_id, asset_id, request_id):
    row = _owned_row(event, database_id, asset_id, request_id)
    if not row:
        return _not_found(event)
    if not _can_read_asset(_get_asset(database_id, asset_id)):
        return _not_found(event)
    return success(body=zd.to_job_item(_reconcile(row)))


def job_url(event, database_id, asset_id, request_id):
    row = _owned_row(event, database_id, asset_id, request_id)
    if not row:
        return _not_found(event)
    asset = _get_asset(database_id, asset_id)
    if not _can_read_asset(asset):
        log_actions(event, "zipDownloadUrlDenied", {"databaseId": database_id, "assetId": asset_id,
                                                    "requestId": request_id})
        return _not_found(event)
    try:
        require_distributable_asset(asset)
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    row = _reconcile(row)
    if row.get("status") != zd.STATUS_SUCCEEDED:
        return general_error(status_code=409, body={"message": zd.MSG_NOT_READY}, event=event)
    exists, size = zd.archive_head(s3, aux_bucket_name, row.get("archiveKey", ""))
    if not exists:
        return general_error(status_code=410, body={"message": zd.MSG_EXPIRED}, event=event)
    file_name = row.get("archiveFileName", "archive.zip")
    ascii_name = file_name.encode("ascii", "replace").decode("ascii").replace("?", "_").replace('"', "_")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}"
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": aux_bucket_name, "Key": row["archiveKey"],
                "ResponseContentDisposition": disposition},
        ExpiresIn=PRESIGNED_URL_TIMEOUT_SECONDS)
    # AUDIT LOG: a download URL was issued (the URL itself is never logged).
    log_actions(event, "zipDownloadUrl", {"databaseId": database_id, "assetId": asset_id,
                                          "requestId": request_id, "archiveSizeBytes": size})
    return success(body={"url": url, "expiresIn": PRESIGNED_URL_TIMEOUT_SECONDS,
                         "archiveFileName": file_name, "archiveSizeBytes": size})


def delete_job(event, database_id, asset_id, request_id):
    row = _owned_row(event, database_id, asset_id, request_id)
    if not row:
        return _not_found(event)
    if not _can_read_asset(_get_asset(database_id, asset_id)):
        return _not_found(event)
    prefix = _job_prefix(row)
    if row.get("status") in zd.ACTIVE_STATUSES:
        if zd.finalize_row(zip_table, row["userId"], request_id, zd.STATUS_CANCELLED,
                           cancelled_by=zd.CANCELLED_BY_USER):
            _abort_execution(row.get("workflowExecutionId"))
            zd.abort_open_uploads(s3, aux_bucket_name, prefix, logger)
            try:
                s3.delete_object(Bucket=aux_bucket_name, Key=row["manifestKey"])
            except ClientError as e:
                logger.warning(f"Could not delete a cancelled zip download's manifest: {e}")
            log_actions(event, "zipDownloadCancel", {"databaseId": database_id, "assetId": asset_id,
                                                     "requestId": request_id})
            return success(body={"message": "Zip download cancelled", "action": "cancelled"})
        # It ended meanwhile: fall through to delete it.
        row = zip_table.get_item(Key=zd.row_key(row["userId"], request_id),
                                 ConsistentRead=True).get("Item") or row
    zd.abort_open_uploads(s3, aux_bucket_name, prefix, logger)
    zd.delete_prefix(s3, aux_bucket_name, prefix)
    try:
        zip_table.delete_item(Key=zd.row_key(row["userId"], request_id),
                              ConditionExpression="attribute_exists(requestId)")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
    log_actions(event, "zipDownloadDelete", {"databaseId": database_id, "assetId": asset_id,
                                             "requestId": request_id})
    return success(body={"message": "Zip download deleted", "action": "deleted"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    try:
        method = event["requestContext"]["http"]["method"]
        path = event["requestContext"]["http"]["path"]

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            if CasbinEnforcer(claims_and_roles).enforceAPI(event):
                method_allowed_on_api = True
        if not method_allowed_on_api:
            return authorization_error()

        # Searched order: the route exists in every deployment, the capability only when enabled.
        if not ZIPDOWNLOADS_ENABLED:
            return general_error(status_code=404, body={"message": zd.MSG_DISABLED}, event=event)

        if API_ZIP_DOWNLOADS_GLOBAL.matches(path):
            if method == "GET":
                return list_jobs(event)
            return validation_error(body={"message": "Method not allowed"}, event=event)

        path_parameters = event.get("pathParameters") or {}
        database_id = path_parameters.get("databaseId")
        asset_id = path_parameters.get("assetId")
        request_id = path_parameters.get("requestId")

        if API_ZIP_DOWNLOADS_ASSET.matches(path):
            message = _validate_path(database_id, asset_id)
            if message:
                return validation_error(body={"message": message}, event=event)
            if method == "POST":
                return create_job(event, database_id, asset_id)
            if method == "GET":
                if not _can_read_asset(_get_asset(database_id, asset_id)):
                    return authorization_error()
                return list_jobs(event, database_id, asset_id)
        elif API_ZIP_DOWNLOADS_ASSET_REQUEST.matches(path) or API_ZIP_DOWNLOADS_ASSET_REQUEST_URL.matches(path):
            message = _validate_path(database_id, asset_id, request_id)
            if message:
                return validation_error(body={"message": message}, event=event)
            if API_ZIP_DOWNLOADS_ASSET_REQUEST_URL.matches(path):
                if method == "POST":
                    return job_url(event, database_id, asset_id, request_id)
            elif method == "GET":
                return get_job(event, database_id, asset_id, request_id)
            elif method == "DELETE":
                return delete_job(event, database_id, asset_id, request_id)
        return validation_error(body={"message": "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(status_code=getattr(v, "status_code", 400), body={"message": str(v)},
                             event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
