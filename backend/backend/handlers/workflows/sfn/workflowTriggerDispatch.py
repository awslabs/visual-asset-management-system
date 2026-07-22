# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File-upload workflow trigger dispatcher.

Delivery path: an asset file upload publishes an `asset.file.uploaded` event to the VAMS
orchestration EventBridge bus; a standing rule (deployment event-source prefix + that detail-type)
targets this dispatcher's own durable SQS buffer (the WorkflowTriggerDispatchQueue created + consumed
by buildWorkflowTriggerDispatchFunction); this lambda consumes that buffer. SQS fronts the lambda so a
single upload action fanning out to many files gets batching / retry / throttled concurrency.

For each uploaded file the dispatcher resolves its asset (databaseId/assetId + asset-relative key),
enumerates the fileUpload trigger rows (WorkflowTriggersTable TriggersByTypeGSI), matches each row's
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
from common.s3MetadataKeys import ASSET_ID_METADATA_KEY, DATABASE_ID_METADATA_KEY
from common.s3PathPatterns import RESERVED_S3_PREFIX_FOLDERS, EXCLUDED_FILE_PATH_PATTERNS
from common.workflows import triggerMatching as tm
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})

dynamodb = boto3.resource("dynamodb", config=retry_config)
s3_client = boto3.client("s3", config=retry_config)
lambda_client = boto3.client("lambda", config=retry_config)
logger = safeLogger(service_name="WorkflowTriggerDispatch")

TRIGGER_TYPE_FILE_UPLOAD = "fileUpload"

try:
    workflow_triggers_table_name = get_table_name(ResourceKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE)
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    s3_asset_buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    # Pipeline handlers are excluded from SSM resolution; this dispatcher is a non-pipeline handler,
    # so the executeWorkflowV2 target function name is a direct env var set by the CDK builder.
    execute_workflow_v2_function = os.environ["EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

workflow_triggers_table = dynamodb.Table(workflow_triggers_table_name)
asset_storage_table = dynamodb.Table(asset_storage_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_table_name)

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
    """All fileUpload trigger rows via TriggersByTypeGSI (PK triggerType). Paginated to exhaustion."""
    rows = []
    kwargs = {
        "IndexName": "TriggersByTypeGSI",
        "KeyConditionExpression": Key("triggerType").eq(TRIGGER_TYPE_FILE_UPLOAD),
    }
    resp = workflow_triggers_table.query(**kwargs)
    while True:
        rows.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        resp = workflow_triggers_table.query(**kwargs)
    return rows


def _resolve_asset_relative_key(bucket_name, s3_key):
    """Resolve (databaseId, assetId, assetRelativeKey) for an uploaded S3 object from its metadata +
    asset record. Returns None when the object has no VAMS asset metadata or the asset is unknown."""
    try:
        head = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
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

    asset_location = asset.get("assetLocation") or {}
    asset_base_key = asset_location.get("Key", "") if isinstance(asset_location, dict) else ""
    if asset_base_key and s3_key.startswith(asset_base_key):
        relative = s3_key[len(asset_base_key):]
    else:
        relative = s3_key
    relative = "/" + relative.lstrip("/")
    return database_id, asset_id, relative


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


def _dispatch_uploaded_file(bucket_name, s3_key, trigger_rows):
    """Match + launch every fileUpload trigger that fires for one uploaded object. Returns the number
    of workflows launched."""
    if _should_skip_key(s3_key):
        return 0
    resolved = _resolve_asset_relative_key(bucket_name, s3_key)
    if not resolved:
        return 0
    database_id, asset_id, relative_key = resolved
    matches = tm.match_fileupload_triggers(trigger_rows, database_id, asset_id, relative_key)
    launched = 0
    for workflow_database_id, workflow_id, body in matches:
        if _invoke_execute(workflow_database_id, workflow_id, body):
            launched += 1
    return launched


def _iter_uploaded_objects(event):
    """Yield (bucket_name, s3_key) for each uploaded object in the event. Handles the two shapes the
    orchestration-bus -> SQS buffer delivers: an EventBridge S3 detail (detail.bucket.name +
    detail.object.key) and a legacy S3-notification Records array, each possibly wrapped in SQS
    Records / an EventBridge envelope."""
    def _from_detail(detail):
        bucket = ((detail.get("bucket") or {}).get("name")
                  or detail.get("ASSET_BUCKET_NAME") or "")
        key = (detail.get("object") or {}).get("key") or detail.get("key") or ""
        if bucket and key:
            yield bucket, key
        # A detail may itself carry an S3-notification Records array.
        for rec in detail.get("Records", []) or []:
            s3_info = rec.get("s3", {})
            b = s3_info.get("bucket", {}).get("name")
            k = s3_info.get("object", {}).get("key")
            if b and k:
                yield b, k

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
        for rec in message.get("Records", []) or []:
            s3_info = rec.get("s3", {})
            b = s3_info.get("bucket", {}).get("name")
            k = s3_info.get("object", {}).get("key")
            if b and k:
                yield b, k

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
    EventBridge) event. Best-effort: per-file/per-workflow failures are logged and do not fail the
    batch."""
    import urllib.parse

    try:
        trigger_rows = _list_fileupload_triggers()
        if not trigger_rows:
            logger.info("No fileUpload triggers configured; nothing to dispatch")
            return success(body={"message": "No fileUpload triggers configured", "workflowsLaunched": 0})

        total_launched = 0
        files_seen = 0
        for bucket_name, raw_key in _iter_uploaded_objects(event):
            files_seen += 1
            s3_key = urllib.parse.unquote_plus(raw_key)
            # Per-file isolation: an unexpected failure resolving/dispatching one file must not drop
            # trigger dispatch for the rest of the batch (best-effort contract).
            try:
                total_launched += _dispatch_uploaded_file(bucket_name, s3_key, trigger_rows)
            except Exception as e:
                logger.exception(f"Error dispatching triggers for {s3_key} (continuing): {e}")

        logger.info(f"fileUpload dispatch: {files_seen} file(s), {total_launched} workflow(s) launched")
        return success(body={"message": "File-upload triggers dispatched",
                             "filesProcessed": files_seen, "workflowsLaunched": total_launched})
    except Exception as e:
        logger.exception(f"Internal error in workflow trigger dispatch: {e}")
        return internal_error(event=event)
