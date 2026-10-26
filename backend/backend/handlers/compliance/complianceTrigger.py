#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Trigger handler (SQS-invoked).

Consumes the asset and file indexer SNS topics through its own SQS queue (`ComplianceTriggerQueue`,
one subscription per topic, each with a message-body filter policy). Every SQS record carries one
SNS `Notification` whose `Message` is an indexer-topic message. For each asset created or updated in
a database with `complianceAutoEval` on, the trigger resolves the asset's bound schema (the asset
override, else the database binding — auto-registering the asset under it) and runs an evaluation
through `complianceEvaluationStore.run_evaluation`. After an evaluation of an asset that has children
it opens a cascade awaiting approval so the downstream assets can be re-evaluated.

A batch is processed as a whole (`TriggerBatch`): the database row, the asset row and the
evaluation target of an asset are read once per batch, and an asset is evaluated at most once per
batch however many of its messages the batch carries — an evaluation started now reads the asset as
it is now, so it covers every change the batch reports. Only an infrastructure error (an AWS call
that failed, `is_infrastructure_error`) fails the queue record it belongs to, which SQS redelivers
and, after the queue's receive limit, moves to the dead-letter queue; a schema or rule error is a
recorded `error` evaluation, and a malformed message is logged and dropped.

A file indexer message is unwrapped with `common.indexerEvents.s3_records_from_indexer_message`
(the same walk the file indexer performs) and every S3 record it carries is processed.

Three guards keep the trigger from multiplying evaluations or relaunching a pipeline rule's own
workflow:

  - a file event for an object a workflow execution wrote (`vams-changesource` object metadata of
    `workflowExecution`, read with a HEAD on the object once the asset has passed the
    auto-evaluation and binding gate) is skipped, so a pipeline rule whose workflow writes its
    outputs into the asset does not re-enter the trigger and relaunch itself;
  - a stream MODIFY whose new image records `lastChangeSource` of `workflowExecution` (the
    provenance `uploadFile` writes onto the asset row when a workflow execution's outputs are
    completed into the asset, and the metadata service writes when a workflow execution's metadata
    output is written back) is skipped for the same reason. A MODIFY recording `assetVersion`
    (an asset version was created or reverted to), `assetEdit` (the asset's name, description or
    tags were edited), `metadataEdit` (the asset's metadata, a file's metadata or attributes, or a
    link's metadata were written) or `linkChange` (a link of the asset was created or deleted) is a
    user change and is evaluated, judged by its `lastChangeAt` like an upload's, so a
    version-coverage, asset-attribute, metadata or relationship verdict follows the change;
  - a change the asset's last evaluation already covers (`change_covered_by_last_evaluation`: the
    evaluation started at or after the change, whatever its status — a `pending_pipeline`
    evaluation covers the changes it read too) is not evaluated again, so an N-file upload yields
    one evaluation rather than N concurrent pipeline launches; a change after that evaluation's
    start is new and starts another evaluation, and the older evaluation's callback never
    overwrites the newer state. An evaluation claims the state row (`lastEvaluatedAt` its start)
    before it evaluates or launches anything, and the trigger reads the row strongly consistent, so
    an invocation for the same change that overlaps a running evaluation — a file event arriving
    while a cold workflow launch is still under way — finds the change covered. The file path
    judges an object against the latest recorded user-change
    completion on the row (`file_event_completion_time`): the later of its `lastUploadAt` (an upload
    completion) and its `lastAssetVersionAt` (an asset-version create or revert, which a revert writes
    after copying the target version's objects), whatever `lastChangeSource` says now, so a revert
    yields one evaluation as an upload does even after a pipeline rule's output write re-stamps the
    row.

Each coverage decision logs one INFO line (`coverage_log_line`) naming the asset by id only, never an
object key; wrapped here, one line at runtime:

    Compliance coverage asset=<assetId> path=<stream|file|direct> stamp=<attribute|none>
    stampAt=<instant|none> eventTime=<instant|none> lastEvaluationId=<id|none>
    lastEvaluatedAt=<instant|none> outcome=<covered|evaluating>

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
from botocore.exceptions import BotoCoreError, ClientError

from common.compliance import evaluationEngine as engine
from models.compliance import EvaluationVerdict
from common.dynamodb import query_all_items
from common.indexerEvents import s3_records_from_indexer_message
from common.resourceNames import ResourceKeys, get_table_name
from common.s3 import region_routing_s3_client
from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
)
from customLogging.logger import safeLogger
from handlers.compliance import complianceEvaluationStore as store

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
# Reads the change-provenance metadata of the object a file event names (HeadObject only), signed
# for the Region of the event's bucket.
s3_client = region_routing_s3_client()
logger = safeLogger(service_name="ComplianceTrigger")

SCHEMA_SOURCE_DATABASE = "database"
SCHEMA_SOURCE_ASSET = "asset"
CASCADE_STATE_PENDING_APPROVAL = "pending_approval"
CASCADE_STATE_INDEX = "StateIndex"

# The asset-row attributes `common.assetProvenance` writes with the source and instant of the last
# change (`upload` or `workflowExecution` from an upload completion or a workflow execution's
# metadata write-back, `assetVersion` from an asset version create or revert, `assetEdit` from an
# edit of the asset's name, description or tags, `metadataEdit` from a metadata write, `linkChange`
# from a link create or delete); a stream image carries them in DynamoDB's typed form.
LAST_CHANGE_SOURCE_ATTRIBUTE = "lastChangeSource"
LAST_CHANGE_AT_ATTRIBUTE = "lastChangeAt"
# The instant of the asset's last user-upload completion; a workflow execution's output write moves
# `lastChangeAt` but not this, so an uploaded object is judged against the upload that completed it.
LAST_UPLOAD_AT_ATTRIBUTE = "lastUploadAt"
# The instant of the asset's last asset-version create or revert, its analogue for the objects a revert
# copies: neither a workflow execution's output write nor an asset edit moves it.
LAST_ASSET_VERSION_AT_ATTRIBUTE = "lastAssetVersionAt"
# The asset-row stamps of the user changes that write objects into the asset before stamping the row;
# `file_event_completion_time` judges a file event's object by the latest of them.
FILE_EVENT_COMPLETION_STAMPS = (LAST_UPLOAD_AT_ATTRIBUTE, LAST_ASSET_VERSION_AT_ATTRIBUTE)

# Path names the coverage log line records: the asset table's stream, the file indexer, or a direct
# `{databaseId, assetId}` message.
COVERAGE_PATH_STREAM = "stream"
COVERAGE_PATH_FILE = "file"
COVERAGE_PATH_DIRECT = "direct"

# Skew margin for the one coverage comparison whose two instants come from unrelated clocks: an S3
# object's `eventTime` against an evaluation start, used only for an object written after the latest
# recorded completion (or under an asset that records none). A change older than the last evaluation
# start by at least this margin is covered.
# The margin keeps S3-vs-Lambda clock skew from marking an evaluation as covering a change it in fact
# started before; a duplicate evaluation is the safe failure, a missed one is not, so it errs toward
# evaluating. An upload whose completion the asset row records (`lastUploadAt`) needs no margin: the
# stamp is written after every object of the upload was copied, so the ordering is causal. The same
# holds for the objects an asset-version revert copies and the `lastAssetVersionAt` written after them.
EVALUATION_COVERS_CHANGE_MARGIN = timedelta(seconds=2)

# A cascade awaiting approval expires after this many hours.
CASCADE_APPROVAL_TIMEOUT_HOURS = 24

# Suffix on the partition key of an archived asset row.
ARCHIVED_DATABASE_SUFFIX = "#deleted"

# The DynamoDB error a conditional write raises when its condition does not hold. It answers the
# write, it does not fail the AWS call, so it is never an infrastructure error.
CONDITIONAL_CHECK_FAILED = "ConditionalCheckFailedException"

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


def is_infrastructure_error(error):
    """Whether `error` is an AWS call that failed — a service error (throttling, access, an
    unavailable endpoint) or a client-side transport error — rather than a condition the code
    answered or a fault in the data. Only these fail a queue record for redelivery: a retry can
    succeed where the call failed, while a malformed message or a schema error fails the same way
    every time and would only ride the record into the dead-letter queue. A conditional write whose
    condition did not hold is an answer, not a failure."""
    if isinstance(error, ClientError):
        return (error.response or {}).get("Error", {}).get("Code") != CONDITIONAL_CHECK_FAILED
    return isinstance(error, BotoCoreError)


def message_from_queue_record(record):
    """The indexer-topic message one SQS record carries: the record's body is the SNS `Notification`
    the topic subscription delivered, and its `Message` is the message the indexer topic was given
    (a DynamoDB stream record, a file-indexer S3 message, or a direct `{databaseId, assetId}`
    message). A body that is not an SNS envelope is read as the message itself (raw delivery). None
    when the body is not a JSON object."""
    body = record.get("body") if isinstance(record, dict) else None
    try:
        payload = json.loads(body) if isinstance(body, str) else body
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("Type") == "Notification" and "Message" in payload:
        try:
            message = json.loads(payload["Message"]) if isinstance(payload["Message"], str) \
                else payload["Message"]
        except (TypeError, ValueError):
            return None
        return message if isinstance(message, dict) else None
    return payload


class TriggerBatch:
    """The state of one SQS batch: the evaluation candidates its messages report, in arrival order,
    and the reads shared between them.

    `collect` reads a message into candidates without evaluating anything; `evaluate_all` then
    judges them in order. A database row (`database_item`), an asset's row (`file_event_target`)
    and an asset's evaluation target (`evaluation_target`) are read once per batch, and an asset
    evaluated once (`evaluated`) is not evaluated again in the batch. `run` runs one step for one
    queue record and records the record as failed when the step raised an infrastructure error."""

    def __init__(self):
        self.candidates = []
        self.evaluated = set()
        self._failed = []
        self._database_items = {}
        self._targets = {}
        self._file_targets = {}

    def run(self, record_id, step, *args, **kwargs):
        """Run `step` for the queue record `record_id`; an exception is logged, and an
        infrastructure error also fails the record so SQS redelivers it."""
        try:
            return step(*args, **kwargs)
        except Exception as e:
            if is_infrastructure_error(e):
                logger.exception(f"Infrastructure error processing a queue record; it is retried: {e}")
                self.fail(record_id)
            else:
                logger.exception(f"Error processing a queue record; it is not retried: {e}")
            return None

    def fail(self, message_id):
        if message_id and message_id not in self._failed:
            self._failed.append(message_id)

    def failures(self):
        """The `batchItemFailures` an SQS event source with `ReportBatchItemFailures` reads."""
        return [{"itemIdentifier": message_id} for message_id in self._failed]

    def add_candidate(self, message_id, step, *args, **kwargs):
        self.candidates.append((message_id, step, args, kwargs))

    def evaluate_all(self):
        for message_id, step, args, kwargs in self.candidates:
            self.run(message_id, step, *args, batch=self, **kwargs)

    def database_item(self, database_id):
        if database_id not in self._database_items:
            self._database_items[database_id] = _database_item(database_id)
        return self._database_items[database_id]

    def evaluation_target(self, database_id, asset_id):
        key = (database_id, asset_id)
        if key not in self._targets:
            self._targets[key] = _evaluation_target(database_id, asset_id, batch=self)
        return self._targets[key]

    def file_event_target(self, asset_id):
        if asset_id not in self._file_targets:
            self._file_targets[asset_id] = _file_event_target(asset_id, batch=self)
        return self._file_targets[asset_id]


def lambda_handler(event, context):
    """Process one SQS batch of indexer-topic messages and report the records an infrastructure
    error failed (`batchItemFailures`), so only those are redelivered."""
    batch = TriggerBatch()
    for record in event.get("Records", []) or []:
        message_id = record.get("messageId", "") if isinstance(record, dict) else ""
        message = message_from_queue_record(record)
        if message is None:
            logger.info("Queue record carries no indexer message, skipping")
            continue
        batch.run(message_id, _dispatch_event, message, batch=batch, message_id=message_id)
    batch.evaluate_all()
    return {"batchItemFailures": batch.failures()}


def _dispatch_event(message, batch=None, message_id=""):
    """Route one indexer-topic message by its shape into the batch's candidates."""
    batch = batch if batch is not None else TriggerBatch()
    if message.get("eventName") in ("INSERT", "MODIFY", "REMOVE"):
        _process_stream_record(message, batch, message_id)
    elif message.get("s3") or message.get("Records"):
        _process_file_event(message, batch, message_id)
    elif message.get("databaseId") and message.get("assetId"):
        batch.add_candidate(message_id, _process_compliance_event,
                            message.get("databaseId"), message.get("assetId"))
    else:
        logger.info("Unrecognized message format, skipping")


def _process_stream_record(message, batch, message_id=""):
    """A DynamoDB stream record of the asset table relayed by the asset indexer topic."""
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

    is_modify = message.get("eventName") == "MODIFY"
    if is_modify and _image_change_source(new_image) == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION:
        logger.info(f"Asset {database_id}:{asset_id} row was last changed by a workflow execution; "
                    "skipping compliance evaluation")
        return

    # An INSERT's image carries whatever `lastChangeAt` the row had when it was written (an
    # unarchived asset keeps its old stamp), which says nothing about this change; only a MODIFY's
    # stamp is the completion that raised the event.
    completion_time = (parse_event_time(_image_string(new_image, LAST_CHANGE_AT_ATTRIBUTE))
                       if is_modify else None)
    batch.add_candidate(message_id, _process_compliance_event, database_id, asset_id,
                        completion_time=completion_time, path=COVERAGE_PATH_STREAM,
                        stamp_attribute=LAST_CHANGE_AT_ATTRIBUTE)


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

    `completion_time` is the instant of the recorded completion the change belongs to — on the file
    path `file_event_completion_time` of the row (the later of its `lastUploadAt` and
    `lastAssetVersionAt`), on the stream path the MODIFY image's `lastChangeAt` (a workflow
    execution's MODIFY is skipped before this point, so that stamp is an upload's, an asset
    version's, an asset edit's, a metadata write's or a link change's). The stamp is written after
    the change it records (every object of an upload or revert copied, the metadata or link row
    written), so an evaluation whose start (`lastEvaluatedAt`) is at or after the stamp has seen
    that change — the comparison is causal and needs no margin.
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


def _process_file_event(message, batch, message_id=""):
    """A file indexer message: one evaluation candidate per S3 record it carries.

    Within the message, the first record of an asset that is not a workflow execution's decides for
    the asset (`_evaluate_file_record`): a second record for it (an N-file upload) is neither read
    nor evaluated again. Across the batch, the asset row and the evaluation target are read once."""
    s3_records = s3_records_from_indexer_message(message)
    if not s3_records:
        logger.info("File event has no s3 data, skipping")
        return

    decided = set()
    for s3_record in s3_records:
        batch.add_candidate(message_id, _evaluate_file_record, s3_record, message, decided)


def _evaluate_file_record(s3_record, message, decided, batch=None):
    """Judge one S3 record of a file indexer message (see `_process_file_event`).

    The asset row and the evaluation target (the database's auto-evaluation flag and the asset's
    bound schema) are resolved before any object is read: an asset that is not evaluated costs no
    HEAD. For an asset that is, the object's provenance is read and a record naming an object a
    workflow execution wrote is skipped."""
    batch = batch if batch is not None else TriggerBatch()
    prefix = message.get("ASSET_BUCKET_PREFIX", "")
    s3_info = s3_record.get("s3") or {}
    object_key = (s3_info.get("object") or {}).get("key", "")
    change_time = parse_event_time(s3_record.get("eventTime"))

    asset_id = _extract_asset_id_from_key(object_key, prefix)
    if not asset_id:
        logger.info("Could not extract an assetId from the object key")
        return

    resolved = batch.file_event_target(asset_id)
    if resolved is None or asset_id in decided:
        return
    database_id, asset_row, target = resolved
    if (database_id, asset_id) in batch.evaluated:
        return

    if object_change_source(s3_info, message) == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION:
        logger.info(f"Object under asset {asset_id} was written by a workflow execution; "
                    "skipping compliance evaluation")
        return

    decided.add(asset_id)
    # The object is not a workflow execution's (those were skipped above), so it belongs to a
    # user upload's completion or to an asset-version revert's stamp.
    stamp_attribute, completion_time = file_event_completion_stamp(asset_row)
    _evaluate_if_uncovered(
        database_id, asset_id, target, change_time=change_time,
        completion_time=completion_time, path=COVERAGE_PATH_FILE,
        stamp_attribute=stamp_attribute, batch=batch)


def file_event_completion_time(asset_row):
    """The recorded completion a file event's object is judged against: the LATEST user-change
    completion the asset row records, the later of its `lastUploadAt` (an upload completion) and its
    `lastAssetVersionAt` (an asset-version create or revert), either of which may be absent;
    None when the row records neither. `lastChangeSource` is not consulted.

    Every completion raises a stream MODIFY that evaluates the asset, and each is stamped only after
    the objects it wrote were copied: an upload completion after its copies, a revert after copying
    the target version's objects (new S3 versions, `eventTime` the revert's), a create having copied
    none. So an evaluation started at or after the latest completion has seen every object written
    before it, whichever completion wrote it, and the indexer's later messages for those objects do
    not start a second evaluation. A workflow execution's output write and an asset edit move neither
    stamp — they re-stamp `lastChangeSource` / `lastChangeAt` only — so a pipeline rule's output
    landing between a revert and its copies' messages, or an edit after a revert, leaves the copies
    judged by the revert's stamp. (Keyed on `lastChangeSource` instead, the copies fell back to the
    older `lastUploadAt` after such a write and started a second evaluation, and for a pipeline rule
    a second workflow execution, per revert.) An object written after the latest stamp (an upload
    whose completion has not stamped the row yet) postdates it and is judged by its own `eventTime`
    with the margin; that upload's completion then raises its own MODIFY."""
    return file_event_completion_stamp(asset_row)[1]


def file_event_completion_stamp(asset_row):
    """`(attribute, instant)` of the latest completion stamp of FILE_EVENT_COMPLETION_STAMPS on the
    asset row (see `file_event_completion_time`); `(None, None)` when the row records none that
    parses. A tie keeps the earlier-listed attribute."""
    latest_attribute, latest_instant = None, None
    for attribute in FILE_EVENT_COMPLETION_STAMPS:
        instant = parse_event_time(asset_row.get(attribute))
        if instant is not None and (latest_instant is None or instant > latest_instant):
            latest_attribute, latest_instant = attribute, instant
    return latest_attribute, latest_instant


def _file_event_target(asset_id, batch=None):
    """`(database_id, asset row, evaluation target)` for an asset a file event names, or None
    (logged) when the asset has no row, is archived, or its database or binding does not evaluate
    it."""
    batch = batch if batch is not None else TriggerBatch()
    asset_row = _resolve_asset_row(asset_id)
    database_id = (asset_row or {}).get("databaseId") or None
    if not database_id:
        logger.info(f"Could not resolve a databaseId for asset {asset_id}")
        return None
    target = batch.evaluation_target(database_id, asset_id)
    if target is None:
        return None
    return database_id, asset_row, target


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
    `databaseId` and its change-provenance stamps (`lastUploadAt`, `lastAssetVersionAt`,
    `lastChangeSource`, `lastChangeAt`). An asset whose row sits under an archived partition resolves
    to None so it is not evaluated. A failed read is raised when it is an infrastructure error, so
    the queue record is redelivered, and logged as an unresolved asset otherwise."""
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
        if is_infrastructure_error(e):
            raise
        logger.exception(f"Error resolving the database for asset {asset_id}: {e}")
        return None


def _database_item(database_id):
    return database_table.get_item(Key={"databaseId": database_id}).get("Item")


def _process_compliance_event(database_id, asset_id, change_time=None, completion_time=None,
                              path=COVERAGE_PATH_DIRECT, stamp_attribute=None, batch=None):
    """Evaluate an asset when its database has auto-evaluation on and it has a bound schema.

    `completion_time` is the recorded completion's instant (the MODIFY image's `lastChangeAt` on
    the stream path, `file_event_completion_time` of the row on the file path) and `change_time`
    the S3 record's `eventTime` (file path only). A change the asset's last evaluation already
    covers is not evaluated again, so one upload or revert reaches one evaluation whichever of the
    two paths reports it first and however many files it carries.
    An evaluation still awaiting its pipeline rules covers the changes made at or before its start
    like any other; a change after its start is evaluated now, and the state row records whichever
    evaluation is newest. An asset the batch already evaluated is not evaluated again. `path` and
    `stamp_attribute` (the row attribute `completion_time` was read from) only name the decision in
    its log line."""
    batch = batch if batch is not None else TriggerBatch()
    if (database_id, asset_id) in batch.evaluated:
        return
    target = batch.evaluation_target(database_id, asset_id)
    if target is None:
        return
    _evaluate_if_uncovered(database_id, asset_id, target, change_time, completion_time,
                           path=path, stamp_attribute=stamp_attribute, batch=batch)


def _evaluation_target(database_id, asset_id, batch=None):
    """`(compliance record, schema name)` for an asset to evaluate: its database has
    `complianceAutoEval` on and it has a bound schema (the asset override, else the database
    binding, registering the asset under it). None (logged) when it is not to be evaluated.

    The record is the one the coverage decision reads, so it is read strongly consistent: an
    evaluation of the same change that another invocation started moments ago has already claimed
    the row (`lastEvaluatedAt` its start), and an eventually-consistent replica could still show the
    row from before that claim and start a second evaluation."""
    batch = batch if batch is not None else TriggerBatch()
    db_item = batch.database_item(database_id)
    if not db_item or db_item.get("complianceAutoEval") is not True:
        logger.info(f"Database {database_id} does not have complianceAutoEval enabled, skipping")
        return None

    compliance_record = store.get_compliance_record(database_id, asset_id, consistent_read=True)
    if not compliance_record:
        compliance_record = check_default_schema(database_id, asset_id, db_item)
        if not compliance_record:
            return None
    elif compliance_record.get("schemaSource") == SCHEMA_SOURCE_ASSET:
        logger.info(f"Asset {database_id}:{asset_id} has an asset-level schema override")

    schema_name = compliance_record.get("schemaName")
    if not schema_name:
        logger.info(f"Asset {database_id}:{asset_id} has no schema assigned")
        return None
    return compliance_record, schema_name


def _evaluate_if_uncovered(database_id, asset_id, target, change_time=None, completion_time=None,
                           path=COVERAGE_PATH_DIRECT, stamp_attribute=None, batch=None):
    """Run the evaluation unless the asset's last evaluation already covers the change, logging the
    decision in one INFO line (`coverage_log_line`). The asset is recorded as evaluated in the batch
    before the evaluation runs, so a failed evaluation is retried through its own queue record
    rather than a second time in this batch."""
    compliance_record, schema_name = target
    covered = change_covered_by_last_evaluation(compliance_record, change_time, completion_time)
    logger.info(coverage_log_line(asset_id, path, stamp_attribute, completion_time, change_time,
                                  compliance_record, covered))
    if covered:
        return
    if batch is not None:
        batch.evaluated.add((database_id, asset_id))
    trigger_evaluation(database_id, asset_id, schema_name)


def coverage_log_line(asset_id, path, stamp_attribute, completion_time, change_time,
                      compliance_record, covered):
    """The one INFO line a coverage decision logs: the asset (by id only, no object key), the path
    that reported the change, the row stamp the decision used and its instant, the S3 `eventTime`
    (file path), the last evaluation it was compared with, and the outcome. An absent value reads
    `none`."""
    def _instant(value):
        return value.isoformat() if value is not None else "none"

    stamp = stamp_attribute if stamp_attribute and completion_time is not None else "none"
    return (f"Compliance coverage asset={asset_id} path={path} stamp={stamp} "
            f"stampAt={_instant(completion_time)} eventTime={_instant(change_time)} "
            f"lastEvaluationId={compliance_record.get('lastEvaluationId') or 'none'} "
            f"lastEvaluatedAt={compliance_record.get('lastEvaluatedAt') or 'none'} "
            f"outcome={'covered' if covered else 'evaluating'}")


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
    """Run an evaluation for the asset and open a cascade for its children.

    A failure to open the cascade is logged rather than raised: the evaluation has claimed the
    asset-state row, so a redelivered record would find its change covered and open nothing either."""
    result = store.run_evaluation(database_id, asset_id, schema_name, store.SYSTEM_ACTOR)
    logger.info(
        f"Evaluation {result.get('evaluationId')} for {database_id}:{asset_id}: "
        f"verdict={result.get('verdict')}")
    if not result.get("error") and result.get("verdict") != EvaluationVerdict.error.value:
        try:
            check_and_trigger_cascade(database_id, asset_id)
        except Exception as e:
            logger.exception(f"Failed opening a cascade for {database_id}:{asset_id}: {e}")


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
