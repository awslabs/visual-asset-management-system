# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File-upload workflow trigger dispatcher.

Delivery path: an asset file upload publishes an `asset.file.uploaded` event to the VAMS
orchestration EventBridge bus; a standing rule (deployment event-source prefix + that detail-type)
targets this dispatcher's own durable SQS buffer (the WorkflowTriggerDispatchQueue created + consumed
by buildWorkflowTriggerDispatchFunction); this lambda consumes that buffer. SQS fronts the lambda so a
single upload action fanning out to many files gets batching / retry / throttled concurrency.

For each uploaded file the dispatcher resolves its asset (databaseId/assetId + asset-relative key),
enumerates the fileUpload trigger rows (WorkflowTriggersTable TriggersByBaseTypeGSI), matches each row's
inputFileFilters + database scope (common.workflows.triggerMatching), and invokes the asset-less
executeWorkflowV2 handler once per firing trigger as SYSTEM_USER with triggerType=fileUpload. Each
launch is best-effort + isolated: one failing workflow does not stop the others or fail the batch.
"""

import json
import os

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext

from common.resourceNames import get_table_name, ResourceKeys
from common.s3MetadataKeys import (
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
)
from common.s3PathPatterns import RESERVED_S3_PREFIX_FOLDERS, EXCLUDED_FILE_PATH_PATTERNS
from common.workflows import triggerMatching as tm
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, success

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})

# The executeWorkflowV2 Invoke is synchronous and NOT idempotent: each delivered request launches an
# execution. A retry on a slow-but-successful call would therefore launch a duplicate run, so this
# client delivers exactly one Invoke and waits out the callee's full 15-minute runtime instead. The
# retrying config stays on the read-only clients.
invoke_config = Config(retries={"total_max_attempts": 1}, read_timeout=900, connect_timeout=60)

dynamodb = boto3.resource("dynamodb", config=retry_config)
s3_client = boto3.client("s3", config=retry_config)
lambda_client = boto3.client("lambda", config=invoke_config)
logger = safeLogger(service_name="WorkflowTriggerDispatch")

TRIGGER_TYPE_FILE_UPLOAD = "fileUpload"

try:
    workflow_triggers_table_name = get_table_name(ResourceKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE)
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    s3_asset_buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    workflow_storage_table_v2_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    # Pipeline handlers are excluded from SSM resolution; this dispatcher is a non-pipeline handler,
    # so the executeWorkflowV2 target function name is a direct env var set by the CDK builder.
    execute_workflow_v2_function = os.environ["EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

workflow_triggers_table = dynamodb.Table(workflow_triggers_table_name)
asset_storage_table = dynamodb.Table(asset_storage_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_table_name)
workflow_storage_table_v2 = dynamodb.Table(workflow_storage_table_v2_name)

# Per-invocation memo of each workflow's systemConfig. One SQS batch can carry many objects destined
# for the same workflow, so the record is read once per workflow rather than per object.
_workflow_system_config_cache = {}

_excluded_prefixes = RESERVED_S3_PREFIX_FOLDERS
_excluded_patterns = EXCLUDED_FILE_PATH_PATTERNS


def _should_skip_key(s3_key):
    """Skip folder markers, VAMS-reserved prefixes, and excluded patterns."""
    if not s3_key or s3_key.endswith("/"):
        return True
    if any(pattern in s3_key for pattern in _excluded_patterns):
        return True
    for part in s3_key.split("/"):
        if part in _excluded_prefixes:
            return True
    return False


def _list_fileupload_triggers():
    """All fileUpload trigger rows via TriggersByBaseTypeGSI. Paginated to exhaustion.

    The index partitions on `triggerBaseType`, which always holds the BARE type, because a workflow may
    carry several fileUpload triggers whose sort keys are suffixed ("fileUpload#7f3a91"). Keying the
    lookup on the sort key instead would put each additional trigger in its own partition, and an
    exact-match query would never find it — the trigger would sit in the table and silently never fire."""
    rows = []
    kwargs = {
        "IndexName": "TriggersByBaseTypeGSI",
        "KeyConditionExpression": Key("triggerBaseType").eq(TRIGGER_TYPE_FILE_UPLOAD),
    }
    resp = workflow_triggers_table.query(**kwargs)
    while True:
        rows.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        resp = workflow_triggers_table.query(**kwargs)
    return rows


def _asset_bucket_name(bucket_id):
    """The asset bucket's name for a bucketId, or '' when it cannot be resolved."""
    if not bucket_id:
        return ""
    try:
        response = s3_asset_buckets_table.query(
            KeyConditionExpression=Key("bucketId").eq(bucket_id), Limit=1)
        return ((response.get("Items") or [{}])[0]).get("bucketName", "") or ""
    except Exception as e:
        logger.info(f"Could not resolve asset bucket {bucket_id} (skipping bucket check): {e}")
        return ""


def _resolve_asset_relative_key(bucket_name, s3_key, version_id=""):
    """Resolve (databaseId, assetId, assetRelativeKey) for an uploaded S3 object from its metadata +
    asset record. Returns None when the object has no VAMS asset metadata, the asset is unknown, the
    object sits in a different bucket than the asset, or the object does not sit within the resolved
    asset's own location."""
    try:
        head_kwargs = {"Bucket": bucket_name, "Key": s3_key}
        if version_id:
            head_kwargs["VersionId"] = version_id
        head = s3_client.head_object(**head_kwargs)
    except Exception as e:
        logger.info(f"Could not head uploaded object (skipping): {e}")
        return None
    metadata = head.get("Metadata", {}) or {}
    asset_id = metadata.get(ASSET_ID_METADATA_KEY)
    database_id = metadata.get(DATABASE_ID_METADATA_KEY)
    if not asset_id or not database_id:
        logger.info(f"Uploaded object missing asset/database metadata (skipping): {s3_key}")
        return None

    asset = asset_storage_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}).get("Item")
    if not asset:
        logger.info(f"Asset not found for uploaded object (skipping): {database_id}/{asset_id}")
        return None

    # Same-prefix assets can exist in different buckets, so the binding also requires the object to
    # sit in the asset's own bucket. An unresolvable bucket row leaves the key check as the only gate.
    asset_bucket_name = _asset_bucket_name(asset.get("bucketId", ""))
    if asset_bucket_name and asset_bucket_name != bucket_name:
        logger.info(f"Uploaded object {s3_key} is in a different bucket than asset "
                    f"{database_id}/{asset_id} (skipping)")
        return None

    asset_location = asset.get("assetLocation") or {}
    asset_base_key = asset_location.get("Key", "") if isinstance(asset_location, dict) else ""
    # The metadata that named the asset is client-settable on a direct asset-bucket write, so the
    # binding only holds when the object actually lives inside that asset's own S3 location.
    normalized_key = s3_key.lstrip("/")
    normalized_base = (asset_base_key or "").lstrip("/")
    if not normalized_base:
        logger.warning(f"Asset {database_id}/{asset_id} has no location key; fileUpload triggers "
                       f"cannot be dispatched for {s3_key}")
        return None
    remainder = None
    if normalized_key.startswith(normalized_base):
        candidate = normalized_key[len(normalized_base):]
        # Containment: the base key is either a prefix ending in '/', or the remainder starts at a
        # path boundary (or is empty). A shared name prefix ("db/a1" vs "db/a10/x.glb") is not
        # containment.
        if normalized_base.endswith("/") or candidate == "" or candidate.startswith("/"):
            remainder = candidate
    if remainder is None:
        logger.info(f"Uploaded object {s3_key} is outside the location of asset "
                    f"{database_id}/{asset_id} (skipping)")
        return None
    relative = "/" + remainder.lstrip("/")
    # The provenance of the write travels with the binding: who wrote this object decides whether a
    # workflow may re-fire on it. It comes from the SAME head_object above, so reading it is free.
    change_source = metadata.get(VAMS_CHANGE_SOURCE_METADATA_KEY, "") or ""
    change_workflow_id = metadata.get(VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY, "") or ""
    return database_id, asset_id, relative, change_source, change_workflow_id


def _workflow_system_config(workflow_database_id, workflow_id):
    """That workflow's stored `systemConfig`, memoized per invocation.

    Read from the workflow record rather than mirrored onto the trigger row: a mirrored copy would go
    stale the moment the workflow's systemConfig changed without the trigger being re-saved. An
    unreadable row yields {}, so each reader falls back to its own conservative default."""
    cache_key = (workflow_database_id, workflow_id)
    if cache_key in _workflow_system_config_cache:
        return _workflow_system_config_cache[cache_key]
    system_config = {}
    try:
        item = workflow_storage_table_v2.get_item(
            Key={"databaseId": workflow_database_id, "workflowId": workflow_id}).get("Item") or {}
        system_config = item.get("systemConfig") or {}
    except Exception as e:
        logger.info(f"Could not read systemConfig for {workflow_database_id}:{workflow_id} "
                    f"(using defaults): {e}")
    _workflow_system_config_cache[cache_key] = system_config
    return system_config


def _workflow_allows_trigger_chaining(workflow_database_id, workflow_id):
    """That workflow's `systemConfig.allowWorkflowTriggerChaining`. Only consulted for a
    workflow-written file (see triggerMatching.match_fileupload_triggers), so an ordinary user upload
    performs no extra reads. Missing key / unreadable row -> False, which keeps chaining opt-in and
    matches the pre-chaining behavior."""
    return bool(_workflow_system_config(workflow_database_id, workflow_id)
                .get("allowWorkflowTriggerChaining", False))


def _workflow_input_file_arity(workflow_database_id, workflow_id):
    """That workflow's `systemConfig.inputFileArity`. An arity-"none" workflow takes no input files, so
    its trigger must fire with an empty selection rather than the uploaded file its own launch
    validation would then reject. Missing key / unreadable row -> '', which the body builder treats as
    "takes the uploaded file" (the behavior for every other arity)."""
    return _workflow_system_config(workflow_database_id, workflow_id).get("inputFileArity") or ""


def _invoke_execute(workflow_database_id, workflow_id, body):
    """Invoke executeWorkflowV2 as SYSTEM_USER for one fired trigger. Returns True on a 200 launch.
    Best-effort: logs and returns False on any error (one workflow must not break the others)."""
    try:
        invoke_event = {
            "requestContext": {
                "http": {"method": "POST",
                         "path": f"/workflows/{workflow_database_id}/{workflow_id}/execute"},
            },
            "pathParameters": {"workflowDatabaseId": workflow_database_id, "workflowId": workflow_id},
            "queryStringParameters": {},
            "body": json.dumps(body),
            "lambdaCrossCall": {"userName": "SYSTEM_USER"},
        }
        response = lambda_client.invoke(
            FunctionName=execute_workflow_v2_function,
            InvocationType="RequestResponse",
            Payload=json.dumps(invoke_event).encode("utf-8"))
        payload = response.get("Payload")
        if not payload:
            return False
        inner = json.loads(payload.read().decode("utf-8"))
        status_code = inner.get("statusCode", 500)
        if status_code == 200:
            logger.info(f"fileUpload trigger launched workflow {workflow_database_id}:{workflow_id}")
            return True
        logger.warning(
            f"fileUpload trigger for workflow {workflow_database_id}:{workflow_id} returned "
            f"status {status_code}")
        return False
    except Exception as e:
        logger.exception(f"Error launching fileUpload trigger workflow {workflow_id}: {e}")
        return False


def _dispatch_uploaded_file(bucket_name, s3_key, trigger_rows, version_id=""):
    """Match + launch every fileUpload trigger that fires for one uploaded object. Returns the number
    of workflows launched. version_id pins the run to the object version that was uploaded (empty on
    an unversioned bucket, where the run reads the current object)."""
    if _should_skip_key(s3_key):
        return 0
    resolved = _resolve_asset_relative_key(bucket_name, s3_key, version_id)
    if not resolved:
        return 0
    database_id, asset_id, relative_key, change_source, change_workflow_id = resolved
    matches = tm.match_fileupload_triggers(
        trigger_rows, database_id, asset_id, relative_key, version_id,
        change_source=change_source, change_workflow_id=change_workflow_id,
        chaining_allowed_for=_workflow_allows_trigger_chaining,
        input_file_arity_for=_workflow_input_file_arity)
    launched = 0
    for workflow_database_id, workflow_id, body in matches:
        if _invoke_execute(workflow_database_id, workflow_id, body):
            launched += 1
    return launched


def _object_version_id(object_info):
    """The uploaded object's S3 VersionId from an S3-notification/EventBridge object block. Empty on
    an unversioned bucket (and for the literal "null" version id S3 reports there)."""
    version_id = object_info.get("versionId") or object_info.get("version-id") or ""
    return "" if version_id == "null" else version_id


def _iter_uploaded_objects(event):
    """Yield (bucket_name, s3_key, versionId) for each uploaded object in the event. Handles the two
    shapes the orchestration-bus -> SQS buffer delivers: an EventBridge S3 detail (detail.bucket.name
    + detail.object.key) and a legacy S3-notification Records array, each possibly wrapped in SQS
    Records / an EventBridge envelope."""
    def _from_records(records):
        for rec in records or []:
            s3_info = rec.get("s3", {})
            object_info = s3_info.get("object", {}) or {}
            b = (s3_info.get("bucket", {}) or {}).get("name")
            k = object_info.get("key")
            if b and k:
                yield b, k, _object_version_id(object_info)

    def _from_detail(detail):
        bucket = ((detail.get("bucket") or {}).get("name")
                  or detail.get("ASSET_BUCKET_NAME") or "")
        object_info = detail.get("object") or {}
        key = object_info.get("key") or detail.get("key") or ""
        if bucket and key:
            yield bucket, key, _object_version_id(object_info)
        # A detail may itself carry an S3-notification Records array.
        yield from _from_records(detail.get("Records", []))

    def _from_message(message):
        # message is a dict: an EventBridge event (has 'detail'), an SNS Notification envelope
        # (Type=Notification + Message), or an S3-notification (Records).
        if "detail" in message:
            yield from _from_detail(message.get("detail") or {})
            return
        if message.get("Type") == "Notification" and message.get("Message"):
            inner = message["Message"]
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except json.JSONDecodeError:
                    return
            if isinstance(inner, dict):
                yield from _from_message(inner)
            return
        yield from _from_records(message.get("Records", []))

    # SQS-buffered delivery: event.Records[] each carry a JSON body.
    if "Records" in event:
        for record in event["Records"]:
            body = record.get("body", record)
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError:
                    logger.info("Skipping non-JSON SQS record body")
                    continue
            yield from _from_message(body if isinstance(body, dict) else {})
    elif "detail" in event:
        # Direct EventBridge invocation (no SQS buffer).
        yield from _from_detail(event.get("detail") or {})


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Dispatch fileUpload workflow triggers for each uploaded object in the (SQS-buffered
    EventBridge) event. Best-effort per file: per-file/per-workflow failures are logged and do not
    fail the batch. A failure that prevents dispatching the batch at all (trigger enumeration, or
    an error outside the per-file loop) raises so the SQS event source retries the batch and it
    eventually reaches the DLQ rather than being silently deleted."""
    import urllib.parse

    # A warm container keeps module state between invocations, so the systemConfig memo is cleared per
    # invocation — a workflow whose configuration changed since the last event must not be judged on a
    # stale value.
    _workflow_system_config_cache.clear()

    try:
        trigger_rows = _list_fileupload_triggers()
    except Exception as e:
        logger.exception(f"Could not enumerate fileUpload triggers; failing the batch: {e}")
        raise

    if not trigger_rows:
        logger.info("No fileUpload triggers configured; nothing to dispatch")
        return success(body={"message": "No fileUpload triggers configured", "workflowsLaunched": 0})

    try:
        total_launched = 0
        files_seen = 0
        for bucket_name, raw_key, version_id in _iter_uploaded_objects(event):
            files_seen += 1
            s3_key = urllib.parse.unquote_plus(raw_key)
            # Per-file isolation: an unexpected failure resolving/dispatching one file must not drop
            # trigger dispatch for the rest of the batch (best-effort contract).
            try:
                total_launched += _dispatch_uploaded_file(
                    bucket_name, s3_key, trigger_rows, version_id)
            except Exception as e:
                logger.exception(f"Error dispatching triggers for {s3_key} (continuing): {e}")

        if files_seen == 0:
            # An event carrying no recognizable object means the producer-side envelope no longer
            # matches what this dispatcher enumerates; triggers silently stop firing otherwise.
            logger.warning("fileUpload dispatch found no recognizable uploaded object in the event")
        logger.info(f"fileUpload dispatch: {files_seen} file(s), {total_launched} workflow(s) launched")
        return success(body={"message": "File-upload triggers dispatched",
                             "filesProcessed": files_seen, "workflowsLaunched": total_launched})
    except Exception as e:
        logger.exception(f"Internal error in workflow trigger dispatch; failing the batch: {e}")
        raise
