# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download as Zip API (prepared downloads).

Routes (common/apiRoutes.py ZIP_DOWNLOAD_ROUTES):
  POST   /database/{databaseId}/assets/{assetId}/download/zip                    create a job
  GET    /database/{databaseId}/assets/{assetId}/download/zip                    caller's jobs for the asset
  GET    /download/zip                                                           caller's jobs, all assets
  GET    /database/{databaseId}/assets/{assetId}/download/zip/{requestId}        one job
  DELETE /database/{databaseId}/assets/{assetId}/download/zip/{requestId}        cancel or delete
                                                                                 (?intent=cancel: never deletes)
  POST   /database/{databaseId}/assets/{assetId}/download/zip/{requestId}/url    presigned URL

Every call: Tier-1 route check, then the feature check (404 while app.zipDownloads is off), then
validation, then Tier-2 asset GET (the download path's action). Jobs are keyed by the AUTHENTICATED
user id, so a caller only ever reads, signs, cancels or deletes their own; another user's job, or a
job named under another asset's path, answers the same 404 as a missing one.

A create resolves and checks the selection (refusing the whole job on any archived, delete-marker,
archive-storage-class or blocked file, owner decision), writes the request manifest and a QUEUED row,
and launches the zipArchive system workflow as SYSTEM_USER through the system-pipeline cross-call
(common/workflows/systemPipelines.py), recording the run's id (workflowExecutionId) on the job. The
zipArchive container writes progress and the final status; the read-time reconcile here (_reconcile)
finalizes what it could not, from the run's status on its workflow execution row, whenever the job is
next read (get, list, presigned URL, the per-user cap check). No terminal callback reaches this
function: the job follows its run by the stored id only. Incomplete uploads a run leaves behind that
no reader aborts are removed by the ``vams.preparedDownloads/`` lifecycle rule after a day.

The same function also serves one direct invoke that no API route reaches: a pass of a stopped job's
delayed sweep, sent by the zip download sweep state machine. lambda_handler dispatches on the event's
envelope, never on a request body: API Gateway builds every proxy event with a ``requestContext`` and
cannot add top-level keys, so an event with one is always an API request, whatever its body says; an
event without one is an internal invoke and never reaches the API dispatch. Any other shape is
logged and ignored.
"""

import base64
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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
from common.s3 import (deployment_region, region_routing_s3_client, register_bucket_region,
                       validateUnallowedFileExtensionAndContentType)
from common.validators import validate
from common.workflows import executionLocks as el
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
from models.assetsV3 import (
    CreateZipDownloadRequestModel,
    DeleteZipDownloadQueryModel,
    ListZipDownloadsQueryModel,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
# Read timeout of the synchronous cross-calls (launch, abort, details read): above the 900 s
# (15 minute) timeout of the invoked functions, so a call is never abandoned while it still runs.
CROSS_CALL_READ_TIMEOUT_SECONDS = 910
CROSS_CALL_CONNECT_TIMEOUT_SECONDS = 10
# Attempts of a synchronous cross-call that Lambda throttled (refused before the function ran).
CROSS_CALL_THROTTLE_ATTEMPTS = 4
CROSS_CALL_THROTTLE_BACKOFF_SECONDS = 1
# Every invoke here is synchronous (RequestResponse), and the launch is not safe to repeat: a second
# send starts a second zipArchive run the job row never records. So botocore neither retries these
# calls nor gives up on them while they still run (the #1205 rule, as in the output step and the
# trigger dispatch); only a throttle the function never ran for is retried (_invoke_request_response).
invoke_config = Config(retries={"total_max_attempts": 1},
                       read_timeout=CROSS_CALL_READ_TIMEOUT_SECONDS,
                       connect_timeout=CROSS_CALL_CONNECT_TIMEOUT_SECONDS)
lambda_client = boto3.client("lambda", config=invoke_config)
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
                                              zd.ZIP_ARCHIVE_WORKFLOW_DATABASE_ID_DEFAULT)
ZIP_ARCHIVE_WORKFLOW_ID = os.environ.get("ZIP_ARCHIVE_WORKFLOW_ID", zd.ZIP_ARCHIVE_WORKFLOW_ID_DEFAULT)
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
# Backstop for a lost Step Functions status change event (_check_run_ended): a job whose row has been
# idle this long while its run's main row is not terminal has its run checked, at most once per
# interval per job and at most RUN_CHECKS_PER_CALL jobs per list call.
RUN_CHECK_IDLE_SECONDS = 300
RUN_CHECK_INTERVAL_SECONDS = 300
RUN_CHECKS_PER_CALL = 5

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
# The auxiliary bucket is the stack's own, in this deployment's Region: its manifest writes, aborts,
# lists, deletes and presigns (and the reconcile's and the delayed sweep's aborts, lists and
# deletes) are signed for that Region without an asset buckets table read (#1125 D17).
register_bucket_region(aux_bucket_name, deployment_region())

USER_ASSET_INDEX = "UserAssetIndex"


class ZipRefusal(VAMSGeneralErrorResponse):
    """A refused create (400, or the given status)."""


class ZipLaunchRefused(Exception):
    """The execute handler refused the zipArchive launch with a refusal it only gives before any run
    starts (see _classify_launch_answer): no run exists. The message is the refusal's detail, for the
    service log only."""


class ZipLaunchRetryable(Exception):
    """The execute handler refused the launch for a transient reason it only gives before any run
    starts (a stale deployment it is redeploying, the state machine's execution limit): no run exists,
    and the same launch can be tried again."""


class ZipLaunchMayHaveStarted(Exception):
    """The launch failed in a way that may come after the run started (a 5xx, a timeout, or a 4xx the
    execute handler also gives after start_execution, such as a throttle or the item-size refusal).
    ``retryable`` marks a transient cause (a throttle), answered 503 instead of 500."""

    def __init__(self, detail, retryable=False):
        super().__init__(detail)
        self.retryable = retryable


# The execute handler's 4xx answers that it only gives BEFORE start_execution, so no run exists for
# them: its early returns in execute_workflow (fixed texts), the lock conflict, and its structured
# validation envelopes. Every other 4xx text can also come out of lambda_handler's exception mapping
# after a run started (a ValidationError, a VAMSGeneralErrorResponse, a throttling ClientError, the
# item-size refusal), so it is treated as "a run may exist". Pinned to executeWorkflow.py by
# tests/handlers/zipDownloads/test_zip_launch_answers.py.
PRE_START_REFUSAL_MESSAGES = frozenset({
    sp.SYSTEM_EXECUTION_INVALID_MESSAGE,
    "Workflow is archived and cannot be executed.",
    "Workflow is disabled and cannot be executed.",
    "Workflow has no deployed state machine.",
    "Workflow has no pipelines to execute.",
    "A referenced pipeline could not be found.",
    ("Workflow's deployed state machine is out of sync with its record. Update the workflow "
     "to redeploy its state machine before executing it."),
    el.LOCK_CONFLICT_MESSAGE,
    *el.LOCK_CONFLICT_MESSAGE_BY_SCOPE.values(),
})
PRE_START_REFUSAL_ENVELOPES = ("executionValidationErrors", "templateResolutionErrors")
# 403 (authorization) and 404 (a missing workflow, asset or input file) are only early returns.
PRE_START_REFUSAL_STATUSES = frozenset({403, 404})
# 409: a stale deployment (refused before the start, or Step Functions refused a pruned version).
PRE_START_RETRYABLE_STATUSES = frozenset({409})
# start_execution itself refused it: nothing started.
PRE_START_RETRYABLE_PREFIXES = ("ExecutionLimitExceeded:",)
# A throttle can hit the record writes after start_execution: a run may exist, but a retry later works.
MAY_HAVE_STARTED_RETRYABLE_PREFIXES = ("ThrottlingException:",)

LAUNCH_RETRY_DELAY_SECONDS = 1


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


def _is_delete_marker(error):
    """Whether a HeadObject error answered for a delete marker (x-amz-delete-marker: true)."""
    headers = (error.response.get("ResponseMetadata") or {}).get("HTTPHeaders") or {}
    return str(headers.get("x-amz-delete-marker", "")).lower() == "true"


def _check_member(bucket, key, version_id):
    """Every per-file refusal rule on one resolved member. Returns its size."""
    try:
        head = _head(bucket, key, version_id)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound", "NoSuchVersion"):
            # An archived file's current version is a delete marker: S3 answers 404 and marks it.
            if _is_delete_marker(e):
                raise ZipRefusal(zd.MSG_ARCHIVED_IN_SELECTION)
            raise ZipRefusal(MSG_FILE_NOT_FOUND)
        if code in ("405", "MethodNotAllowed"):
            raise ZipRefusal(MSG_VERSION_ARCHIVED, status_code=STATUS_VERSION_ARCHIVED)
        if code in ("400", "InvalidArgument"):
            raise ZipRefusal(MSG_FILE_NOT_FOUND)
        raise
    if is_file_archived(head):
        raise ZipRefusal(zd.MSG_ARCHIVED_IN_SELECTION)
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

def _invoke_request_response(function_name, invoke_event):
    """Invoke a function synchronously, once. Lambda refuses a throttled invoke before the function
    runs (TooManyRequestsException), so that one is retried with a short backoff; any other error,
    including a read timeout, is raised and never repeated, since the call may have done its work."""
    body = json.dumps(invoke_event).encode("utf-8")
    for attempt in range(CROSS_CALL_THROTTLE_ATTEMPTS):
        try:
            return lambda_client.invoke(FunctionName=function_name, InvocationType="RequestResponse",
                                        Payload=body)
        except ClientError as e:
            throttled = e.response.get("Error", {}).get("Code") == "TooManyRequestsException"
            if not throttled or attempt == CROSS_CALL_THROTTLE_ATTEMPTS - 1:
                raise
            logger.warning(f"Invoke of {function_name} throttled; retrying (attempt {attempt + 2})")
            time.sleep(CROSS_CALL_THROTTLE_BACKOFF_SECONDS * (2 ** attempt))


def _launch(row, request_entries, pinned):
    """Launch the zipArchive system workflow for a job as SYSTEM_USER. Returns the execution id."""
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
    execute_path = f"/workflows/{ARCHIVE_WORKFLOW_DATABASE_ID}/{ZIP_ARCHIVE_WORKFLOW_ID}/execute"
    invoke_event = {
        "requestContext": {"http": {"method": "POST", "path": execute_path}},
        "pathParameters": {"workflowDatabaseId": ARCHIVE_WORKFLOW_DATABASE_ID,
                           "workflowId": ZIP_ARCHIVE_WORKFLOW_ID},
        "queryStringParameters": {},
        "body": json.dumps(body),
        "lambdaCrossCall": {"userName": sp.SYSTEM_PIPELINE_USER_NAME,
                            "source": sp.SYSTEM_PIPELINE_SOURCE_MARKER},
        sp.SYSTEM_EXECUTION_EVENT_KEY: {"triggerType": zd.ZIP_SYSTEM_TRIGGER,
                                        "context": context},
    }
    response = _invoke_request_response(EXECUTE_FUNCTION, invoke_event)
    payload = response.get("Payload")
    inner = json.loads(payload.read().decode("utf-8")) if payload else {}
    status_code = inner.get("statusCode", 500)
    inner_body = json.loads(inner["body"]) if isinstance(inner.get("body"), str) and inner.get("body") else {}
    # The execute handler answers {"message": ExecuteWorkflowResponseModel} on success, and
    # {"message": "<reason>"} on a refusal (the same envelope the execution service's re-run reads).
    message = inner_body.get("message")
    execution_id = message.get("executionId") if isinstance(message, dict) else None
    if status_code == 200 and execution_id:
        return execution_id
    # The refusal as the execute handler gave it: a string, or {"executionValidationErrors": [...]}
    # / {"templateResolutionErrors": ...}. Kept whole for the service log (D10); never returned.
    detail = message if isinstance(message, str) else json.dumps(message, default=str)
    detail = f"zipArchive workflow launch answered {status_code}: {str(detail)[:2000]}"
    raise _classify_launch_answer(status_code, message, detail)


def _classify_launch_answer(status_code, message, detail):
    """The exception for an execute handler answer that started no recorded run. Only answers the
    handler gives before start_execution say "no run exists"; any other 4xx may come after a run
    started (lambda_handler maps exceptions from the record writes to 4xx too), so it is treated like
    a 5xx: the job row is kept FAILED, and a run that did start loses against it."""
    if not isinstance(status_code, int) or not 400 <= status_code < 500:
        return ZipLaunchMayHaveStarted(detail)
    text = message if isinstance(message, str) else ""
    if status_code in PRE_START_RETRYABLE_STATUSES or text.startswith(PRE_START_RETRYABLE_PREFIXES):
        return ZipLaunchRetryable(detail)
    if status_code in PRE_START_REFUSAL_STATUSES:
        return ZipLaunchRefused(detail)
    if status_code == 400 and (
            text in PRE_START_REFUSAL_MESSAGES
            or (isinstance(message, dict) and any(k in message for k in PRE_START_REFUSAL_ENVELOPES))):
        return ZipLaunchRefused(detail)
    return ZipLaunchMayHaveStarted(detail, retryable=text.startswith(MAY_HAVE_STARTED_RETRYABLE_PREFIXES))


def _launch_retrying_once(row, request_entries, pinned):
    """_launch, tried a second time only when the first answer said no run exists and the cause is
    transient (a stale deployment the handler redeploys, the execution limit). Never retried when a
    run may exist: a repeated launch would start a second run the job row never records (#1205)."""
    try:
        return _launch(row, request_entries, pinned)
    except ZipLaunchRetryable as first:
        logger.warning(f"Zip download {row['requestId']} launch retried once: {first}")
        time.sleep(LAUNCH_RETRY_DELAY_SECONDS)
        return _launch(row, request_entries, pinned)


def _abort_execution(execution_id):
    """Abort the job's workflow run through the execution service's abort route (SYSTEM_USER
    cross-call), which also stops its Batch job. Best-effort: the job is already finalized, the
    container stops when it finds that, and the delayed sweep and the abort-incomplete-multipart
    lifecycle rule are the backstops for its uploads."""
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
        response = _invoke_request_response(EXECUTION_SERVICE_FUNCTION, invoke_event)
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


def _discard_unlaunched_job(row):
    """Remove a job whose launch was refused: its working objects, then its row (only while it is
    still QUEUED with no run recorded, so nothing another writer recorded is removed)."""
    zd.delete_working_objects(s3, aux_bucket_name, row, logger)
    try:
        zip_table.delete_item(
            Key=zd.row_key(row["userId"], row["requestId"]),
            ConditionExpression=("attribute_exists(requestId) AND #status = :queued AND "
                                 "attribute_not_exists(workflowExecutionId)"),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":queued": zd.STATUS_QUEUED})
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            logger.warning(f"Could not remove refused zip download {row.get('requestId')}: {e}")


def _seconds_since(stamp):
    """Seconds since an ISO ``%Y-%m-%dT%H:%M:%SZ`` stamp, or None when it does not parse."""
    try:
        parsed = datetime.strptime(stamp or "", "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return (zd.utc_now() - parsed.replace(tzinfo=timezone.utc)).total_seconds()


def _main_row(execution_id):
    """The zipArchive run's main row in the executions table ({} when missing)."""
    response = executions_table.get_item(Key={
        "workflowExecutionId": execution_id,
        # The main row's sort key (common.workflows.executionRecords.workflow_composite_key).
        "workflowDatabaseId:workflowId": f"{ARCHIVE_WORKFLOW_DATABASE_ID}:{ZIP_ARCHIVE_WORKFLOW_ID}"})
    return response.get("Item") or {}


def _claim_run_check(row):
    """Claim this interval's run check for a job (conditional write of ``runCheckedAt``), so readers
    of the same job check its run at most once per RUN_CHECK_INTERVAL_SECONDS. True when claimed.
    Best-effort: a failed write is logged and the check skipped."""
    now = zd.utc_now()
    try:
        zip_table.update_item(
            Key=zd.row_key(row["userId"], row["requestId"]),
            UpdateExpression="SET runCheckedAt = :now",
            ConditionExpression=("attribute_exists(requestId) AND #status IN (:queued, :running) "
                                 "AND workflowExecutionId = :eid AND "
                                 "(attribute_not_exists(runCheckedAt) OR runCheckedAt < :cutoff)"),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":now": zd.iso(now), ":queued": zd.STATUS_QUEUED, ":running": zd.STATUS_RUNNING,
                ":eid": row.get("workflowExecutionId") or "",
                ":cutoff": zd.iso(now - timedelta(seconds=RUN_CHECK_INTERVAL_SECONDS))})
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            logger.warning(f"Could not claim the run check of zip download {row.get('requestId')}: {e}")
        return False


def _run_check_due(row, budget):
    """Whether this read checks the job's run: the row has been idle RUN_CHECK_IDLE_SECONDS (the
    container writes progress every few seconds while it works), the call's budget allows another
    check, and this reader claimed the job's check for the interval."""
    idle = _seconds_since(row.get("updatedAt") or row.get("startedAt") or row.get("createdAt"))
    if idle is None or idle < RUN_CHECK_IDLE_SECONDS:
        return False
    if budget is not None and budget[0] <= 0:
        return False
    if not _claim_run_check(row):
        return False
    if budget is not None:
        budget[0] -= 1
    return True


def _check_run_ended(execution_id):
    """Backstop for a lost Step Functions status change event of the job's run.

    The run's main row records an end outside VAMS (a stop in the Step Functions console, a timeout,
    the history quota) only from that event or from the execution service's details read, which
    checks Step Functions (_reconcile_main_status). The zip routes read the main row directly and the
    executions views hide system runs, so without this a lost event leaves the job active, holding a
    slot of the per-user cap, until it expires. The details read, made as SYSTEM_USER through the
    same cross-call as the abort, writes the end under the shared terminal guard and settles the
    run's locks and pipeline rows, exactly as when someone opens the run; the caller then re-reads
    the main row and finalizes the job from it (finalize_ended_run). Best-effort: False when the
    read did not answer 200."""
    if not execution_id or not EXECUTION_SERVICE_FUNCTION:
        return False
    invoke_event = {
        "requestContext": {"http": {"method": "GET",
                                    "path": f"/workflows/executions/{execution_id}/details"}},
        "pathParameters": {"executionId": execution_id},
        "queryStringParameters": {},
        "lambdaCrossCall": {"userName": sp.SYSTEM_PIPELINE_USER_NAME},
    }
    try:
        response = _invoke_request_response(EXECUTION_SERVICE_FUNCTION, invoke_event)
        payload = response.get("Payload")
        inner = json.loads(payload.read().decode("utf-8")) if payload else {}
        if inner.get("statusCode") != 200:
            logger.warning(f"Run check of zip download run {execution_id} answered "
                           f"{inner.get('statusCode')}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Run check of zip download run {execution_id} failed (non-critical): {e}")
        return False


def _reconcile(row, run_check_budget=None):
    """Finalize a non-terminal row whose run already ended (or never started). Returns the row as it
    reads after any write.

    This is the only finalizer of a job besides the container and the user's cancel/delete: the run
    sends the job nothing when it ends. From the run's status on its main execution row (by the stored
    workflowExecutionId) finalize_ended_run first records the stop and aborts the job's open uploads,
    starts the delayed sweep, then decides: the job's own landed archive wins (SUCCEEDED); a recorded
    user cancel wins next (CANCELLED); otherwise a run that SUCCEEDED without the archive, was ABORTED
    (from the workflow executions views), FAILED or TIMED_OUT ends FAILED with its documented message,
    and the job's working objects are removed.

    ``run_check_budget`` is a one-element list counting the run checks (_check_run_ended) this call
    may still make; None allows the one check a single-job route needs."""
    if row.get("status") not in zd.ACTIVE_STATUSES:
        return row
    execution_id = row.get("workflowExecutionId")
    if not execution_id:
        age = _seconds_since(row.get("createdAt")) or 0
        if age > zd.LAUNCH_GRACE_SECONDS:
            if zd.finalize_row(zip_table, row["userId"], row["requestId"], zd.STATUS_FAILED,
                               error_message=zd.MSG_LAUNCH_FAILED, unless_cancel_requested=True):
                zd.delete_working_objects(s3, aux_bucket_name, row, logger)
                row = dict(row, status=zd.STATUS_FAILED, errorMessage=zd.MSG_LAUNCH_FAILED)
        return row
    main = _main_row(execution_id)
    if main.get("executionStatus", "") not in zd.RUN_TERMINAL_STATUSES \
            and _run_check_due(row, run_check_budget) and _check_run_ended(execution_id):
        main = _main_row(execution_id)
    # Aborts the job's open uploads before deciding, so a landed archive wins (finalize_ended_run).
    status, _ = zd.finalize_ended_run(zip_table, s3, aux_bucket_name, row,
                                      main.get("executionStatus", ""),
                                      main.get("executionError", "") or "", logger)
    if not status:
        return row
    fresh = zip_table.get_item(Key=zd.row_key(row["userId"], row["requestId"]),
                               ConsistentRead=True).get("Item")
    return fresh or row


def _owned_row(event, database_id, asset_id, request_id, operation):
    """The caller's own, unexpired job under this asset path, or None.

    A miss answers the same 404 whatever the reason (no such job, another user's, another asset's
    path, expired), so nothing about other users' jobs leaks; it is audited as a denial
    (zipDownloadNotFound) with the operation, like the Tier-1/Tier-2 denials Casbin audits."""
    row = zip_table.get_item(Key=zd.row_key(_caller_id(), request_id), ConsistentRead=True).get("Item")
    if (not row or row.get("databaseId") != database_id or row.get("assetId") != asset_id
            or zd.is_expired(row)):
        # AUDIT LOG: a job lookup that is not the caller's own, unexpired job on this path.
        log_actions(event, "zipDownloadNotFound", {"databaseId": database_id, "assetId": asset_id,
                                                   "requestId": request_id, "operation": operation})
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


def _cap_reached(user_id) -> bool:
    """Whether the caller holds MAX_ACTIVE_PER_USER active jobs. At the cap, each active job is first
    reconciled like a list does (same run-check budget), so a job whose run ended without the job
    recording it yet (no read since, or the run's status change event was lost) frees its slot here
    rather than only when the caller next lists or opens it."""
    if _active_job_count(user_id) < MAX_ACTIVE_PER_USER:
        return False
    rows = query_all_items(
        zip_table,
        KeyConditionExpression=Key("userId").eq(user_id),
        FilterExpression=Attr("status").is_in(list(zd.ACTIVE_STATUSES)),
    )
    budget, active, reconciled = [RUN_CHECKS_PER_CALL], 0, 0
    for row in rows:
        if zd.is_expired(row):
            continue
        if reconciled < RECONCILE_MAX_PER_CALL:
            reconciled += 1
            row = _reconcile(row, budget)
        if row.get("status") in zd.ACTIVE_STATUSES:
            active += 1
    return active >= MAX_ACTIVE_PER_USER


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
        if _cap_reached(user_id):
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
        execution_id = _launch_retrying_once(row, entries, pinned)
    except (ZipLaunchRefused, ZipLaunchRetryable) as refused:
        # The execute handler answered with a refusal it only gives before a run starts, so no run
        # exists: the job is removed (row and working objects) instead of being left FAILED. A
        # refusal of the selection answers 400; a transient one that failed its retry answers 503.
        # The refusal itself goes to this log only (D10): it names workflow configuration.
        retryable = isinstance(refused, ZipLaunchRetryable)
        logger.warning(f"Zip download {request_id} refused at launch: {refused}")
        _discard_unlaunched_job(row)
        log_actions(event, "zipDownloadRefused", {
            "databaseId": database_id, "assetId": asset_id,
            "reason": "launchRetryable" if retryable else "launchRefused"})
        if retryable:
            return general_error(status_code=503, body={"message": zd.MSG_LAUNCH_BUSY}, event=event)
        return general_error(status_code=400, body={"message": zd.MSG_LAUNCH_REFUSED}, event=event)
    except Exception as e:
        # The launch may or may not have started a run (a read timeout, a 5xx, or a 4xx the execute
        # handler also gives after the start), so the row stays, FAILED with a generic reason: it
        # keeps the job visible to the user and, if a run did start, the container's writes lose
        # against the terminal row and it stops. A throttle answers 503 so the caller tries again.
        logger.exception(f"Zip download {request_id} could not be launched: {e}")
        if zd.finalize_row(zip_table, user_id, request_id, zd.STATUS_FAILED,
                           error_message=zd.MSG_LAUNCH_FAILED):
            zd.delete_working_objects(s3, aux_bucket_name, row, logger)
        if isinstance(e, ZipLaunchMayHaveStarted) and e.retryable:
            return general_error(status_code=503, body={"message": zd.MSG_LAUNCH_BUSY}, event=event)
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
    run_check_budget = [RUN_CHECKS_PER_CALL]
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
            row = _reconcile(row, run_check_budget)
        items.append(zd.to_job_item(row))
    result = {"Items": items}
    if "LastEvaluatedKey" in response:
        result["NextToken"] = _encode_token(response["LastEvaluatedKey"])
    return success(body=result)


def get_job(event, database_id, asset_id, request_id):
    row = _owned_row(event, database_id, asset_id, request_id, "get")
    if not row:
        return _not_found(event)
    if not _can_read_asset(_get_asset(database_id, asset_id)):
        return _not_found(event)
    return success(body=zd.to_job_item(_reconcile(row)))


def job_url(event, database_id, asset_id, request_id):
    row = _owned_row(event, database_id, asset_id, request_id, "url")
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


def _ended_answer(event, database_id, asset_id, request_id, status):
    """A cancel that found the job finished: the job is kept with its outcome (#1125 D22, D23)."""
    log_actions(event, "zipDownloadCancel", {"databaseId": database_id, "assetId": asset_id,
                                             "requestId": request_id, "outcome": "ended",
                                             "status": status})
    return success(body={"message": zd.MSG_ALREADY_FINISHED, "action": "ended", "status": status})


def delete_job(event, database_id, asset_id, request_id):
    try:
        query = parse(event.get("queryStringParameters") or {}, model=DeleteZipDownloadQueryModel)
    except ValidationError as v:
        logger.error(f"Validation error: {v}")
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    cancel_intent = query.intent == "cancel"
    row = _owned_row(event, database_id, asset_id, request_id, "delete")
    if not row:
        return _not_found(event)
    if not _can_read_asset(_get_asset(database_id, asset_id)):
        return _not_found(event)
    prefix = _job_prefix(row)
    if cancel_intent and row.get("status") not in zd.ACTIVE_STATUSES:
        # The caller asked for a cancel (intent=cancel) and the job had already finished, for example
        # while a confirm dialog was open: a cancel never deletes, so the job keeps its outcome
        # (#1125 D23). A repeated cancel of a CANCELLED job answers cancelled.
        if row.get("status") == zd.STATUS_CANCELLED:
            return success(body={"message": "Zip download cancelled", "action": "cancelled"})
        return _ended_answer(event, database_id, asset_id, request_id, row.get("status", ""))
    if row.get("status") in zd.ACTIVE_STATUSES:
        # A cancel. It records the cancel first and aborts the open uploads; an archive that already
        # landed wins (common/zipDownloads.cancel_job). It never deletes the job: a job that ended
        # before the cancel was recorded keeps its outcome and the caller is told so (#1125 D22);
        # only a DELETE of a job that has already finished deletes it.
        outcome = zd.cancel_job(zip_table, s3, aux_bucket_name, row, zd.CANCELLED_BY_USER, logger)
        if outcome == zd.CANCEL_CANCELLED:
            _abort_execution(row.get("workflowExecutionId"))
            # An upload the container created since the first abort.
            zd.abort_open_uploads(s3, aux_bucket_name, prefix, logger)
            # cancel_job removed the working objects; again, for any the container wrote since.
            zd.delete_working_objects(s3, aux_bucket_name, row, logger)
            log_actions(event, "zipDownloadCancel", {"databaseId": database_id, "assetId": asset_id,
                                                     "requestId": request_id})
            return success(body={"message": "Zip download cancelled", "action": "cancelled"})
        fresh = zip_table.get_item(Key=zd.row_key(row["userId"], request_id),
                                   ConsistentRead=True).get("Item") or {}
        return _ended_answer(event, database_id, asset_id, request_id, fresh.get("status", ""))
    zd.abort_open_uploads(s3, aux_bucket_name, prefix, logger)
    # The row is removed below, so the delayed sweep carries its archive key and recorded upload ids
    # (a sweep the cancel already started keeps its own; ExecutionAlreadyExists counts as scheduled).
    if row.get("status") != zd.STATUS_SUCCEEDED:
        zd.schedule_delayed_sweep(row, logger)
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
# Delayed sweep (a direct invoke; no API route reaches it)
# ---------------------------------------------------------------------------
#
# Only the sweep state machine sends a sweep pass. The run of a job sends this function nothing when
# it ends: _reconcile finalizes the job from the run's status on its next read.

# Top-level marker of a delayed sweep pass (the sweep state machine's LambdaInvoke payload,
# infra lambdaBuilder/assetFunctions.ts buildZipDownloadSweep).
SWEEP_ACTION = "sweep"


def handle_sweep(event) -> dict:
    """One pass of a job's delayed sweep (the zip download sweep state machine, #1125 D16): abort
    the archive uploads the job recorded that are still listed, unless its archive landed."""
    result = zd.sweep_recorded_uploads(zip_table, s3, aux_bucket_name, event.get("zipSweep") or {},
                                       final=bool(event.get("final")), logger=logger)
    logger.info(f"Zip download delayed sweep ({'final' if event.get('final') else 'first'} pass): "
                f"{result}")
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _is_api_request(event) -> bool:
    """An API Gateway proxy event (HTTP API v2, or REST normalized by request_to_claims): API Gateway
    builds every one with a ``requestContext`` and cannot add top-level keys."""
    return isinstance(event, dict) and "requestContext" in event


def _is_internal_event(event, key, value) -> bool:
    """A direct invoke marked by a top-level key, which no API Gateway event can carry."""
    return isinstance(event, dict) and "requestContext" not in event and event.get(key) == value


def lambda_handler(event, context: LambdaContext):
    # Dispatch on the envelope, never on the body: an API request is always an API request, whatever
    # its body imitates, and the internal branches run before any request handling or request log.
    if _is_api_request(event):
        return handle_api_request(event, context)
    if _is_internal_event(event, "action", SWEEP_ACTION):
        # Raises on a failed check or listing, so the state machine retries the pass.
        return handle_sweep(event)
    # Neither an API request nor a marked internal invoke: no read, no write, no retry.
    logger.warning("Zip download service ignored an event of an unknown shape")
    return {"status": "ignored", "reason": "unknown event shape"}


def handle_api_request(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
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
