"""FMM Compliance Trigger handler.

Subscribes to the asset and file indexer SNS topics and triggers
compliance evaluation when assets are created or updated.

This Lambda is triggered by SNS messages from the indexing system.
It checks if the asset's database has auto-eval enabled, then verifies
if the asset has a registered compliance schema and, if so, initiates
a compliance evaluation.
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from customLogging.logger import safeLogger

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="FMMComplianceTrigger")

try:
    compliance_table_name = os.environ["FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME"]
    evaluation_table_name = os.environ["FMM_EVALUATION_STORAGE_TABLE_NAME"]
    audit_table_name = os.environ["FMM_AUDIT_STORAGE_TABLE_NAME"]
    database_table_name = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    schema_table_name = os.environ["FMM_SCHEMA_STORAGE_TABLE_NAME"]
    asset_links_table_name = os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"]
    cascade_table_name = os.environ["FMM_CASCADE_STORAGE_TABLE_NAME"]
    asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

compliance_table = dynamodb.Table(compliance_table_name)
evaluation_table = dynamodb.Table(evaluation_table_name)
audit_table = dynamodb.Table(audit_table_name)
database_table = dynamodb.Table(database_table_name)
schema_table = dynamodb.Table(schema_table_name)
asset_links_table = dynamodb.Table(asset_links_table_name)
cascade_table = dynamodb.Table(cascade_table_name)
asset_table = dynamodb.Table(asset_table_name)


def lambda_handler(event, context):
    """Process SNS messages for asset/file change events."""
    for record in event.get("Records", []):
        try:
            sns_message = record.get("Sns", {})
            message_body = json.loads(sns_message.get("Message", "{}"))
            _dispatch_event(message_body)
        except Exception as e:
            logger.exception(f"Error processing SNS record: {e}")
            continue


def _dispatch_event(message):
    """Route the SNS message to the appropriate handler based on format."""
    if message.get("eventName") in ("INSERT", "MODIFY", "REMOVE"):
        _process_stream_record(message)
    elif message.get("s3") or message.get("Records"):
        _process_file_event(message)
    elif message.get("databaseId") and message.get("assetId"):
        _process_compliance_event(message.get("databaseId"), message.get("assetId"))
    else:
        logger.info("Unrecognized message format, skipping")


def _process_stream_record(message):
    """Handle a DynamoDB stream record from the asset indexer SNS topic."""
    event_name = message.get("eventName")
    if event_name == "REMOVE":
        return

    dynamodb_data = message.get("dynamodb", {})
    new_image = dynamodb_data.get("NewImage", {})
    keys = dynamodb_data.get("Keys", {})

    database_id = (
        new_image.get("databaseId", {}).get("S")
        or keys.get("databaseId", {}).get("S")
    )
    asset_id = (
        new_image.get("assetId", {}).get("S")
        or keys.get("assetId", {}).get("S")
    )

    if not database_id or not asset_id:
        logger.info("Stream record missing databaseId or assetId, skipping")
        return

    if database_id.endswith("#deleted"):
        return

    _process_compliance_event(database_id, asset_id)


def _process_file_event(message):
    """Handle a file indexer SNS message (S3 event format)."""
    s3_info = message.get("s3")
    if not s3_info:
        records = message.get("Records", [])
        if records:
            s3_info = records[0].get("s3")
    if not s3_info:
        logger.info("File event has no s3 data, skipping")
        return

    object_key = s3_info.get("object", {}).get("key", "")
    prefix = message.get("ASSET_BUCKET_PREFIX", "")

    asset_id = _extract_asset_id_from_key(object_key, prefix)
    if not asset_id:
        logger.info(f"Could not extract assetId from key: {object_key}")
        return

    database_id = _resolve_database_for_asset(asset_id)
    if not database_id:
        logger.info(f"Could not resolve databaseId for asset: {asset_id}")
        return

    _process_compliance_event(database_id, asset_id)


def _extract_asset_id_from_key(object_key, prefix):
    """Extract assetId from S3 key by stripping the prefix."""
    if prefix and prefix != "/" and prefix != "":
        if not prefix.endswith("/"):
            prefix = prefix + "/"
        if object_key.startswith(prefix):
            object_key = object_key[len(prefix):]

    parts = object_key.split("/")
    if parts and parts[0]:
        return parts[0]
    return None


def _resolve_database_for_asset(asset_id):
    """Look up the databaseId for an asset via the assetIdGSI."""
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
        if database_id.endswith("#deleted"):
            database_id = database_id[: -len("#deleted")]
        return database_id or None
    except Exception as e:
        logger.exception(f"Error resolving database for asset {asset_id}: {e}")
        return None


def _check_auto_eval_enabled(database_id):
    """Check if the database has complianceAutoEval enabled."""
    try:
        response = database_table.get_item(Key={"databaseId": database_id})
        db_item = response.get("Item")
        if not db_item:
            return False
        return db_item.get("complianceAutoEval") is True
    except Exception as e:
        logger.exception(
            f"Error checking auto-eval for database {database_id}: {e}"
        )
        return False


def _process_compliance_event(database_id, asset_id):
    """Check if asset has compliance schema and trigger evaluation."""
    if not _check_auto_eval_enabled(database_id):
        logger.info(
            f"Database {database_id} does not have complianceAutoEval enabled, "
            "skipping"
        )
        return

    compliance_record = compliance_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    ).get("Item")

    if not compliance_record:
        logger.info(
            f"Asset {database_id}:{asset_id} has no compliance record, "
            "checking for default schema"
        )
        compliance_record = check_default_schema(database_id, asset_id)
        if not compliance_record:
            return
    elif compliance_record.get("schemaSource") == "asset":
        logger.info(
            f"Asset {database_id}:{asset_id} has asset-level schema override, "
            "using asset binding"
        )

    schema_name = compliance_record.get("schemaName")
    if not schema_name:
        logger.info(f"Asset {database_id}:{asset_id} has no schema assigned")
        return

    trigger_evaluation(database_id, asset_id, schema_name)


def check_default_schema(database_id, asset_id):
    """Check if the asset's database has a compliance schema bound to it.

    If the database has a complianceSchemaName, auto-register the asset.
    """
    db_response = database_table.get_item(Key={"databaseId": database_id})
    db_item = db_response.get("Item")
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
        "schemaSource": "database",
        "complianceState": "unknown",
        "registeredAt": now,
        "updatedAt": now,
    }
    compliance_table.put_item(Item=record)
    logger.info(
        f"Auto-registered asset {database_id}:{asset_id} "
        f"with schema '{schema_name}' from database binding"
    )
    return record


def trigger_evaluation(database_id, asset_id, schema_name):
    """Trigger compliance evaluation for the asset.

    For vams-rules-v1 schemas, runs the evaluation engine directly.
    For legacy schemas, creates a pending evaluation record.
    """
    schema_body = _load_schema_body(schema_name)
    if schema_body and schema_body.get("schemaFormat") == "vams-rules-v1":
        from handlers.fmm.fmmEvaluationEngine import (
            evaluate_asset as run_evaluation,
        )
        result = run_evaluation(database_id, asset_id, schema_name, "system")
        logger.info(
            f"Evaluation engine completed for {database_id}:{asset_id}: "
            f"verdict={result.get('verdict')}"
        )
        check_and_trigger_cascade(database_id, asset_id)
        return

    now = datetime.now(timezone.utc).isoformat()
    evaluation_id = str(uuid.uuid4())

    evaluation_table.put_item(
        Item={
            "evaluationId": evaluation_id,
            "databaseId:assetId": f"{database_id}:{asset_id}",
            "databaseId": database_id,
            "assetId": asset_id,
            "schemaName": schema_name,
            "evaluatedAt": now,
            "actor": "system",
            "status": "pending",
            "trigger": "sns_upload",
        }
    )

    compliance_table.update_item(
        Key={"databaseId": database_id, "assetId": asset_id},
        UpdateExpression=(
            "SET complianceState = :state, "
            "lastEvaluationId = :evalId, "
            "lastEvaluationAt = :now, "
            "updatedAt = :now"
        ),
        ExpressionAttributeValues={
            ":state": "pending_evaluation",
            ":evalId": evaluation_id,
            ":now": now,
        },
    )

    audit_table.put_item(
        Item={
            "entryId": str(uuid.uuid4()),
            "databaseId:assetId": f"{database_id}:{asset_id}",
            "timestamp": now,
            "eventType": "compliance_check",
            "databaseId": database_id,
            "assetId": asset_id,
            "actor": "system",
            "schemaName": schema_name,
            "evaluationId": evaluation_id,
            "details": json.dumps({
                "status": "pending",
                "trigger": "sns_upload",
            }),
        }
    )

    logger.info(
        f"Triggered compliance evaluation for {database_id}:{asset_id} "
        f"(schema: {schema_name}, evaluation: {evaluation_id})"
    )


def _load_schema_body(schema_name):
    """Load schema body to determine format."""
    response = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return None
    body = items[0].get("schemaBody", "{}")
    if isinstance(body, str):
        try:
            return json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return None
    return body


def check_and_trigger_cascade(database_id: str, asset_id: str):
    """If this asset has children, create a cascade for re-evaluation.

    Called after evaluating a parent asset to propagate compliance
    checks to downstream dependents.
    """
    asset_key = f"{database_id}:{asset_id}"
    response = asset_links_table.query(
        IndexName="fromAssetGSI",
        KeyConditionExpression=Key("fromAssetDatabaseId:fromAssetId").eq(
            asset_key
        ),
        Limit=1,
    )
    children = response.get("Items", [])
    if not children:
        return

    logger.info(
        f"Asset {asset_key} has children, creating cascade for "
        "downstream re-evaluation"
    )

    now = datetime.now(timezone.utc)
    cascade_id = str(uuid.uuid4())

    cascade_table.put_item(
        Item={
            "cascadeId": cascade_id,
            "state": "pending_approval",
            "triggeredByDatabaseId": database_id,
            "triggeredByAssetId": asset_id,
            "triggerReason": "Parent asset updated — downstream re-evaluation needed",
            "createdAt": now.isoformat(),
            "actor": "system",
            "requireApproval": True,
            "approvalTimeoutAt": (
                now + timedelta(hours=24)
            ).isoformat(),
            "nodes": json.dumps({}),
            "executionOrder": json.dumps([]),
        }
    )

    audit_table.put_item(
        Item={
            "entryId": str(uuid.uuid4()),
            "databaseId:assetId": asset_key,
            "timestamp": now.isoformat(),
            "eventType": "cascade_auto_triggered",
            "databaseId": database_id,
            "assetId": asset_id,
            "actor": "system",
            "details": json.dumps({
                "cascadeId": cascade_id,
                "reason": "parent_update",
            }),
        }
    )

    try:
        from handlers.fmm.fmmNotifications import notify_cascade_pending

        all_children_resp = asset_links_table.query(
            IndexName="fromAssetGSI",
            KeyConditionExpression=Key("fromAssetDatabaseId:fromAssetId").eq(
                asset_key
            ),
            Select="COUNT",
        )
        child_count = all_children_resp.get("Count", 1)
        notify_cascade_pending(database_id, asset_id, cascade_id, child_count)
    except Exception as e:
        logger.exception(f"Failed sending cascade notification: {e}")

    logger.info(f"Created cascade {cascade_id} for parent {asset_key}")
