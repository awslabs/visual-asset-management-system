"""FMM Notifications helper.

Publishes compliance event notifications to asset SNS topics,
following the existing VAMS per-asset subscription pattern.
"""

import json
import os
from typing import Dict, Optional

import boto3
from botocore.config import Config

from customLogging.logger import safeLogger

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb_client = boto3.client("dynamodb", config=retry_config)
sns_client = boto3.client("sns", config=retry_config)
logger = safeLogger(service_name="FMMNotifications")

try:
    asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e


def notify_quarantine(
    database_id: str,
    asset_id: str,
    schema_name: str,
    rule_failures: Optional[list] = None,
):
    """Notify subscribers that an asset has been quarantined."""
    asset_name, topic_arn = _get_asset_topic(database_id, asset_id)
    if not topic_arn:
        return

    failures_text = ""
    if rule_failures:
        failures_text = "\n    Failed rules:\n"
        for failure in rule_failures[:10]:
            failures_text += f"      - {failure}\n"

    message = f"""
    Dear Subscriber,

    Asset '{asset_name}' has been QUARANTINED due to compliance failure.

    Database: {database_id}
    Asset: {asset_id}
    Schema: {schema_name}
{failures_text}
    The asset does not meet the compliance requirements defined in the
    schema. Please review and remediate, then re-upload or request an
    exception.

    Best Regards,
    VAMS Compliance System
    """

    _publish(
        topic_arn,
        message,
        subject=f"[QUARANTINED] {asset_name} — compliance failure",
    )


def notify_cascade_pending(
    database_id: str,
    asset_id: str,
    cascade_id: str,
    child_count: int,
):
    """Notify subscribers that a cascade is awaiting approval."""
    asset_name, topic_arn = _get_asset_topic(database_id, asset_id)
    if not topic_arn:
        return

    message = f"""
    Dear Subscriber,

    A compliance cascade has been triggered for asset '{asset_name}'.

    Database: {database_id}
    Asset: {asset_id}
    Cascade ID: {cascade_id}
    Downstream assets affected: {child_count}

    This cascade requires approval before downstream assets are
    re-evaluated. Please review and approve or reject via the VAMS
    compliance API or UI.

    Best Regards,
    VAMS Compliance System
    """

    _publish(
        topic_arn,
        message,
        subject=f"[CASCADE PENDING] {asset_name} — approval required",
    )


def notify_cascade_completed(
    database_id: str,
    asset_id: str,
    cascade_id: str,
    results_summary: Dict[str, int],
):
    """Notify subscribers that a cascade has completed execution."""
    asset_name, topic_arn = _get_asset_topic(database_id, asset_id)
    if not topic_arn:
        return

    summary_lines = "\n".join(
        f"      {status}: {count}"
        for status, count in results_summary.items()
    )

    message = f"""
    Dear Subscriber,

    The compliance cascade for asset '{asset_name}' has completed.

    Database: {database_id}
    Asset: {asset_id}
    Cascade ID: {cascade_id}

    Results:
{summary_lines}

    Best Regards,
    VAMS Compliance System
    """

    _publish(
        topic_arn,
        message,
        subject=f"[CASCADE COMPLETE] {asset_name} — re-evaluation finished",
    )


def _get_asset_topic(
    database_id: str, asset_id: str
) -> tuple:
    """Look up asset name and SNS topic ARN from the asset table."""
    try:
        resp = dynamodb_client.query(
            TableName=asset_table_name,
            ProjectionExpression="assetName, snsTopic",
            KeyConditionExpression=(
                "assetId = :asset_id AND databaseId = :database_id"
            ),
            ExpressionAttributeValues={
                ":asset_id": {"S": asset_id},
                ":database_id": {"S": database_id},
            },
        )
        items = resp.get("Items", [])
        if not items:
            return ("", "")
        item = items[0]
        asset_name = item.get("assetName", {}).get("S", asset_id)
        topic_arn = item.get("snsTopic", {}).get("S", "")
        return (asset_name, topic_arn)
    except Exception as e:
        logger.exception(f"Failed to look up asset topic: {e}")
        return ("", "")


def _publish(topic_arn: str, message: str, subject: str):
    """Publish a message to an SNS topic."""
    try:
        sns_client.publish(
            TopicArn=topic_arn,
            Message=message,
            Subject=subject[:100],
        )
        logger.info(f"Published notification to {topic_arn}")
    except Exception as e:
        logger.exception(f"Failed to publish notification: {e}")
