#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Trigger handler (SNS-invoked).

Subscribes to the asset and file indexer SNS topics. For each asset created or updated in a
database with `complianceAutoEval` on, it resolves the asset's bound schema (the asset override,
else the database binding — auto-registering the asset under it) and runs an evaluation through
`complianceEvaluationStore.run_evaluation`. After an evaluation of an asset that has children it
opens a cascade awaiting approval so the downstream assets can be re-evaluated.

A file indexer message is unwrapped with `common.indexerEvents.s3_records_from_indexer_message`
(the same walk the file indexer performs) and every S3 record it carries is processed, with one
evaluation per distinct asset per message.

Three guards keep the trigger from multiplying evaluations or relaunching a pipeline rule's own
workflow:

  - a file event for an object a workflow execution wrote (`vams-changesource` object metadata of
    `workflowExecution`, read with a HEAD on the object) is skipped, so a pipeline rule whose
    workflow writes its outputs into the asset does not re-enter the trigger and relaunch itself;
  - a stream MODIFY whose new image records `lastChangeSource` of `workflowExecution` (the
    provenance `uploadFile` writes onto the asset row when a workflow execution's outputs are
    completed into the asset) is skipped for the same reason;
  - an event for an asset whose state row points at an evaluation still awaiting its pipeline
    rules is coalesced into that evaluation, so an N-file upload yields one evaluation rather than
    N concurrent pipeline launches.

A cascade awaiting approval is opened once per trigger asset: while one is pending for the asset,
a further evaluation does not open another.
"""

import json
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config

from common.compliance import evaluationEngine as engine
from models.compliance import EvaluationVerdict
from common.dynamodb import query_all_items
from common.indexerEvents import s3_records_from_indexer_message
from common.resourceNames import ResourceKeys, get_table_name
from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
)
from customLogging.logger import safeLogger
from handlers.compliance import complianceEvaluationStore as store

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
# Reads the change-provenance metadata of the object a file event names (HeadObject only).
s3_client = boto3.client("s3", config=retry_config)
logger = safeLogger(service_name="ComplianceTrigger")

SCHEMA_SOURCE_DATABASE = "database"
SCHEMA_SOURCE_ASSET = "asset"
CASCADE_STATE_PENDING_APPROVAL = "pending_approval"
CASCADE_STATE_INDEX = "StateIndex"

# The asset-row attributes `uploadFile` writes with the source and instant of the last change
# (`upload` or `workflowExecution`); a stream image carries them in DynamoDB's typed form.
LAST_CHANGE_SOURCE_ATTRIBUTE = "lastChangeSource"
LAST_CHANGE_AT_ATTRIBUTE = "lastChangeAt"

# Skew margin for the one coverage comparison whose two instants come from unrelated clocks: an S3
# object's `eventTime` against an evaluation start, used only for an object written outside a recorded
# upload completion. A change older than the last evaluation start by at least this margin is covered.
# The margin keeps S3-vs-Lambda clock skew from marking an evaluation as covering a change it in fact
# started before; a duplicate evaluation is the safe failure, a missed one is not, so it errs toward
# evaluating. An upload whose completion the asset row records (`lastChangeAt`) needs no margin: the
# stamp is written after every object of the upload was copied, so the ordering is causal.
EVALUATION_COVERS_CHANGE_MARGIN = timedelta(seconds=2)

# A cascade awaiting approval expires after this many hours.
CASCADE_APPROVAL_TIMEOUT_HOURS = 24

# Suffix on the partition key of an archived asset row.
ARCHIVED_DATABASE_SUFFIX = "#deleted"

try:
    database_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
    cascade_table_name = get_table_name(ResourceKeys.COMPLIANCE_CASCADE_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

database_table = dynamodb.Table(database_table_name)
cascade_table = dynamodb.Table(cascade_table_name)

# Tables the shared store resolved at import.
asset_state_table = store.asset_state_table
asset_table = store.asset_table


def lambda_handler(event, context):
    """Process the SNS records of an asset / file change event."""
    for record in event.get("Records", []):
        try:
            message_body = json.loads(record.get("Sns", {}).get("Message", "{}"))
            _dispatch_event(message_body)
        except Exception as e:
            logger.exception(f"Error processing SNS record: {e}")
            continue


def _dispatch_event(message):
    """Route one SNS message by its shape."""
    if message.get("eventName") in ("INSERT", "MODIFY", "REMOVE"):
        _process_stream_record(message)
    elif message.get("s3") or message.get("Records"):
        _process_file_event(message)
    elif message.get("databaseId") and message.get("assetId"):
        _process_compliance_event(message.get("databaseId"), message.get("assetId"))
    else:
        logger.info("Unrecognized message format, skipping")


def _process_stream_record(message):
    """A DynamoDB stream record relayed by the asset indexer topic."""
    if message.get("eventName") == "REMOVE":
        return

    dynamodb_data = message.get("dynamodb", {})
    new_image = dynamodb_data.get("NewImage", {})
    keys = dynamodb_data.get("Keys", {})

    database_id = (new_image.get("databaseId", {}).get("S")
                   or keys.get("databaseId", {}).get("S"))
    asset_id = (new_image.get("assetId", {}).get("S")
                or keys.get("assetId", {}).get("S"))

    if not database_id or not asset_id:
        logger.info("Stream record missing databaseId or assetId, skipping")
        return
    if database_id.endswith(ARCHIVED_DATABASE_SUFFIX):
        return

    if message.get("eventName") == "MODIFY" and _image_change_source(new_image) \
            == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION:
        logger.info(f"Asset {database_id}:{asset_id} row was last changed by a workflow execution; "
                    "skipping compliance evaluation")
        return

    _process_compliance_event(
        database_id, asset_id,
        completion_time=parse_event_time(_image_string(new_image, LAST_CHANGE_AT_ATTRIBUTE)))


def _image_string(new_image, attribute):
    """A string attribute of a stream image (DynamoDB's typed form), or "" when absent."""
    value = new_image.get(attribute)
    return value.get("S", "") or "" if isinstance(value, dict) else ""


def _image_change_source(new_image):
    """The `lastChangeSource` a stream image carries, or "" when the row records none."""
    return _image_string(new_image, LAST_CHANGE_SOURCE_ATTRIBUTE)


def parse_event_time(value):
    """An S3 `eventTime` (`Z` suffix) or a stored ISO-8601 instant (`+00:00`) as an aware datetime;
    None when absent or unparseable, which the callers read as "evaluate"."""
    return engine.parse_timestamp(value)


def change_covered_by_last_evaluation(compliance_record, change_time=None, completion_time=None):
    """Whether the asset's last evaluation started after the change and so already covers it.

    `completion_time` is the asset row's `lastChangeAt`: the upload completion that stamped it ran
    after every object of that upload was copied, so an evaluation whose start (`lastEvaluatedAt`)
    is at or after the stamp has seen those files — the comparison is causal and needs no margin.
    `change_time` is an S3 object's `eventTime`. An object written at or before the recorded
    completion belongs to that upload and is judged by the stamp; an object written after it (or an
    asset with no stamp) is judged against its own `eventTime` with EVALUATION_COVERS_CHANGE_MARGIN,
    the two instants coming from unrelated clocks. An asset never evaluated, or a change with neither
    instant known, is never covered. Any `lastEvaluationStatus` counts, because the inputs were read
    after the change either way. A file that lands after a covering evaluation started is a new
    change and yields a second evaluation."""
    last_evaluated_at = parse_event_time(compliance_record.get("lastEvaluatedAt"))
    if last_evaluated_at is None:
        return False
    if completion_time is not None and (change_time is None or completion_time >= change_time):
        return last_evaluated_at >= completion_time
    if change_time is None:
        return False
    return last_evaluated_at >= change_time + EVALUATION_COVERS_CHANGE_MARGIN


def _process_file_event(message):
    """A file indexer message: every S3 record it carries, one evaluation per distinct asset.

    A record naming an object a workflow execution wrote is skipped. The database of each asset is
    resolved once per message, and a second record for an asset already evaluated in this message
    (an N-file upload) is not evaluated again."""
    s3_records = s3_records_from_indexer_message(message)
    if not s3_records:
        logger.info("File event has no s3 data, skipping")
        return

    prefix = message.get("ASSET_BUCKET_PREFIX", "")
    rows_by_asset = {}
    evaluated = set()
    for s3_record in s3_records:
        s3_info = s3_record.get("s3") or {}
        object_key = (s3_info.get("object") or {}).get("key", "")
        change_time = parse_event_time(s3_record.get("eventTime"))

        asset_id = _extract_asset_id_from_key(object_key, prefix)
        if not asset_id:
            logger.info("Could not extract an assetId from the object key")
            continue

        if object_change_source(s3_info, message) == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION:
            logger.info(f"Object under asset {asset_id} was written by a workflow execution; "
                        "skipping compliance evaluation")
            continue

        if asset_id not in rows_by_asset:
            rows_by_asset[asset_id] = _resolve_asset_row(asset_id)
        asset_row = rows_by_asset[asset_id]
        database_id = (asset_row or {}).get("databaseId") or None
        if not database_id:
            logger.info(f"Could not resolve a databaseId for asset {asset_id}")
            continue

        asset_key = f"{database_id}:{asset_id}"
        if asset_key in evaluated:
            continue
        evaluated.add(asset_key)
        _process_compliance_event(
            database_id, asset_id, change_time=change_time,
            completion_time=parse_event_time(asset_row.get(LAST_CHANGE_AT_ATTRIBUTE)))


def object_change_source(s3_info, message):
    """The `vams-changesource` object metadata of the object a file event names, read with a HEAD on
    the event's object version; "" when the object carries none or cannot be read.

    The file indexer message carries the S3 event record only, not the object's metadata, so the
    provenance is read from the object itself. Event keys arrive form-encoded; the decoded key is
    tried first and the raw key second, since a literal '+' in a key decodes to a space that names
    no object. Unreadable is reported as unknown rather than as a workflow write, so a missing
    permission or a deleted object leaves the evaluation to proceed."""
    bucket = (s3_info.get("bucket") or {}).get("name") or message.get("ASSET_BUCKET_NAME", "")
    raw_key = (s3_info.get("object") or {}).get("key", "")
    version_id = (s3_info.get("object") or {}).get("versionId", "")
    if not bucket or not raw_key:
        return ""
    decoded_key = urllib.parse.unquote_plus(raw_key)
    candidates = [decoded_key] if decoded_key == raw_key else [decoded_key, raw_key]
    for key in candidates:
        head_kwargs = {"Bucket": bucket, "Key": key}
        if version_id and version_id != "null":
            head_kwargs["VersionId"] = version_id
        try:
            head = s3_client.head_object(**head_kwargs)
        except Exception as e:
            logger.info(f"Could not read the change provenance of an object under {bucket}: {e}")
            continue
        return (head.get("Metadata") or {}).get(VAMS_CHANGE_SOURCE_METADATA_KEY, "") or ""
    return ""


def _extract_asset_id_from_key(object_key, prefix):
    """The assetId segment of an S3 key under the bucket's asset prefix."""
    if prefix and prefix != "/":
        if not prefix.endswith("/"):
            prefix = prefix + "/"
        if object_key.startswith(prefix):
            object_key = object_key[len(prefix):]
    parts = object_key.split("/")
    return parts[0] if parts and parts[0] else None


def _resolve_asset_row(asset_id):
    """The asset's row via the assetIdGSI (the first match; an asset id is unique), carrying its
    `databaseId` and the `lastChangeAt` of its last upload completion. An asset whose row sits under
    an archived partition resolves to None so it is not evaluated."""
    try:
        response = asset_table.query(
            IndexName="assetIdGSI",
            KeyConditionExpression=Key("assetId").eq(asset_id),
            Limit=1,
        )
        items = response.get("Items", [])
        if not items:
            return None
        database_id = items[0].get("databaseId", "")
        if database_id.endswith(ARCHIVED_DATABASE_SUFFIX):
            logger.info(f"Asset {asset_id} is archived; skipping compliance evaluation")
            return None
        return items[0] if database_id else None
    except Exception as e:
        logger.exception(f"Error resolving the database for asset {asset_id}: {e}")
        return None


def _database_item(database_id):
    return database_table.get_item(Key={"databaseId": database_id}).get("Item")


def _process_compliance_event(database_id, asset_id, change_time=None, completion_time=None):
    """Evaluate an asset when its database has auto-evaluation on and it has a bound schema.

    `completion_time` is the asset row's `lastChangeAt` (the stream path's own instant; read from
    the row on the file path) and `change_time` the S3 record's `eventTime` (file path only). A
    change the asset's last evaluation already covers is not evaluated again, so one upload reaches
    one evaluation whichever of the two paths reports it first and however many files it carries;
    neither is an asset whose state row points at an evaluation still awaiting its pipeline rules,
    since the in-flight evaluation covers the change."""
    db_item = _database_item(database_id)
    if not db_item or db_item.get("complianceAutoEval") is not True:
        logger.info(f"Database {database_id} does not have complianceAutoEval enabled, skipping")
        return

    compliance_record = store.get_compliance_record(database_id, asset_id)
    if not compliance_record:
        compliance_record = check_default_schema(database_id, asset_id, db_item)
        if not compliance_record:
            return
    elif compliance_record.get("schemaSource") == SCHEMA_SOURCE_ASSET:
        logger.info(f"Asset {database_id}:{asset_id} has an asset-level schema override")

    schema_name = compliance_record.get("schemaName")
    if not schema_name:
        logger.info(f"Asset {database_id}:{asset_id} has no schema assigned")
        return

    if change_covered_by_last_evaluation(compliance_record, change_time, completion_time):
        changed_at = (completion_time or change_time).isoformat()
        logger.info(f"Asset {database_id}:{asset_id} changed at {changed_at} and its evaluation "
                    f"{compliance_record.get('lastEvaluationId')} started at "
                    f"{compliance_record.get('lastEvaluatedAt')}, after the change; the change is "
                    "already covered")
        return

    if covered_by_pending_evaluation(compliance_record):
        logger.info(f"Asset {database_id}:{asset_id} has evaluation "
                    f"{compliance_record.get('lastEvaluationId')} awaiting its pipeline rules; "
                    "the change is coalesced into it")
        return

    trigger_evaluation(database_id, asset_id, schema_name)


def covered_by_pending_evaluation(compliance_record):
    """Whether the asset's state row points at an evaluation still `pending_pipeline`.

    The state row alone is not trusted: its `pending_evaluation` state is confirmed against the
    evaluation row (consistent read), so a state row left behind by an evaluation that has since
    completed does not suppress the next evaluation. The instant of the change plays no part: the
    workflow callback that finalizes a pending evaluation re-evaluates nothing, so whatever the
    change was, the in-flight evaluation's outcome is the one the asset's state reflects until the
    next evaluation runs."""
    if compliance_record.get("complianceState") != engine.STATE_PENDING_EVALUATION:
        return False
    evaluation_id = compliance_record.get("lastEvaluationId")
    if not evaluation_id:
        return False
    evaluation = store.get_evaluation(evaluation_id, consistent_read=True) or {}
    return evaluation.get("status") == engine.EVALUATION_STATUS_PENDING_PIPELINE


def check_default_schema(database_id, asset_id, db_item=None):
    """Register an asset under its database's bound schema (when the database has one) and
    return the new asset-state row, else None."""
    db_item = db_item or _database_item(database_id)
    if not db_item:
        return None
    schema_name = db_item.get("complianceSchemaName")
    if not schema_name:
        return None

    now = datetime.now(timezone.utc).isoformat()
    record = {
        "databaseId": database_id,
        "assetId": asset_id,
        "schemaName": schema_name,
        "schemaSource": SCHEMA_SOURCE_DATABASE,
        "complianceState": engine.STATE_UNKNOWN,
        "registeredAt": now,
        "updatedAt": now,
    }
    asset_state_table.put_item(Item=record)
    logger.info(f"Registered asset {database_id}:{asset_id} under the database schema '{schema_name}'")
    return record


def trigger_evaluation(database_id, asset_id, schema_name):
    """Run an evaluation for the asset and open a cascade for its children."""
    result = store.run_evaluation(database_id, asset_id, schema_name, store.SYSTEM_ACTOR)
    logger.info(
        f"Evaluation {result.get('evaluationId')} for {database_id}:{asset_id}: "
        f"verdict={result.get('verdict')}")
    if not result.get("error") and result.get("verdict") != EvaluationVerdict.error.value:
        check_and_trigger_cascade(database_id, asset_id)


def check_and_trigger_cascade(database_id: str, asset_id: str):
    """Open a cascade awaiting approval when the asset has downstream children and none is already
    pending for it."""
    children = store.get_child_links(database_id, asset_id)
    if not children:
        return

    asset_key = f"{database_id}:{asset_id}"
    if pending_cascade_exists(database_id, asset_id):
        logger.info(f"Asset {asset_key} already has a cascade awaiting approval; not opening another")
        return
    logger.info(f"Asset {asset_key} has {len(children)} children; opening a cascade")

    now = datetime.now(timezone.utc)
    cascade_id = str(uuid.uuid4())
    cascade_table.put_item(Item={
        "cascadeId": cascade_id,
        "state": CASCADE_STATE_PENDING_APPROVAL,
        "triggeredByDatabaseId": database_id,
        "triggeredByAssetId": asset_id,
        "triggerReason": "Parent asset updated; downstream re-evaluation needed",
        "createdAt": now.isoformat(),
        "actor": store.SYSTEM_ACTOR,
        "requireApproval": True,
        "approvalTimeoutAt": (now + timedelta(hours=CASCADE_APPROVAL_TIMEOUT_HOURS)).isoformat(),
        "nodes": json.dumps({}),
        "executionOrder": json.dumps([]),
    })

    store.write_audit(
        database_id, asset_id,
        event_type="cascade_auto_triggered",
        actor=store.SYSTEM_ACTOR,
        cascade_id=cascade_id,
        details={"reason": "parent_update", "childCount": len(children)},
    )

    try:
        from handlers.compliance.complianceNotifications import notify_cascade_pending
        notify_cascade_pending(database_id, asset_id, cascade_id, len(children))
    except Exception as e:
        logger.exception(f"Failed sending cascade notification: {e}")

    logger.info(f"Created cascade {cascade_id} for parent {asset_key}")


def pending_cascade_exists(database_id: str, asset_id: str) -> bool:
    """Whether a cascade in `pending_approval` was triggered by the given asset.

    Reads the cascade table's `StateIndex` (partition `state`) to exhaustion with a filter on the
    trigger asset: the filter is applied after each page is read, so a single page is not an
    answer (backend Rule 14)."""
    rows = query_all_items(
        cascade_table,
        IndexName=CASCADE_STATE_INDEX,
        KeyConditionExpression=Key("state").eq(CASCADE_STATE_PENDING_APPROVAL),
        FilterExpression=(Attr("triggeredByDatabaseId").eq(database_id)
                          & Attr("triggeredByAssetId").eq(asset_id)),
    )
    return len(rows) > 0
