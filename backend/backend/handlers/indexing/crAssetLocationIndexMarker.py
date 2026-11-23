# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deploy-time custom resource: mark the asset location index ready on a new deployment.

The location index is complete once every asset row carries its location attributes. On an upgrade
the v2.6-to-v2.7 `assetLocationIndex` migration step writes them and then its completion marker. A new
deployment has no asset rows to backfill, so this resource writes the same marker when the stack is
created, and only when:

- the request is a Create;
- the asset table was created within `newTableWindowSeconds` of this request, so it belongs to this
  deployment rather than to an earlier one being upgraded; and
- the asset table holds no row (one `Scan` with `Limit` 1).

An existing marker is never overwritten. An Update changes nothing and keeps the physical id, so an
update never makes CloudFormation replace (and so delete) the resource. A Delete, when the stack is
removed or a release without this resource (v2.6) is deployed over it, deletes the marker, whichever
step wrote it: rows written without the index attributes after that would make it stale. Any failure is
logged and the deployment goes on: without a marker after a Create, which only means the backfill must
be run, as on an upgrade; with the marker left in place after a Delete, to be deleted by hand.
"""

import json
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from customLogging.auditLogging import audited_handler, log_action
from customLogging.logger import safeLogger

logger = safeLogger(service_name="CrAssetLocationIndexMarker")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb_client = boto3.client('dynamodb', config=retry_config)
ssm_client = boto3.client('ssm', config=retry_config)

MARKER_VERSION = 1
MARKER_STATUS_COMPLETE = "complete"
MARKER_WRITTEN_BY = "deploy"
DEFAULT_NEW_TABLE_WINDOW_SECONDS = 3600
PHYSICAL_RESOURCE_ID = "assetLocationIndexMarker"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def marker_value(asset_table_name: str, completed_at: datetime) -> str:
    """The marker the backfill writes, for a table that holds no asset row."""
    return json.dumps({
        'version': MARKER_VERSION,
        'status': MARKER_STATUS_COMPLETE,
        'assetTable': asset_table_name,
        'completedAt': completed_at.isoformat(),
        'assetRows': 0,
        'indexed': 0,
        'notIndexed': 0,
        'writtenBy': MARKER_WRITTEN_BY,
    }, sort_keys=True)


def table_is_new_and_empty(asset_table_name: str, window_seconds: int, now: datetime) -> bool:
    """Whether the table was created within the window before `now` and holds no row."""
    table = dynamodb_client.describe_table(TableName=asset_table_name)['Table']
    created = table.get('CreationDateTime')
    if not isinstance(created, datetime):
        logger.warning(f"Asset table {asset_table_name} reports no creation time; no marker is written")
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_seconds = (now - created).total_seconds()
    if age_seconds > window_seconds:
        logger.info(f"Asset table {asset_table_name} was created {int(age_seconds)} s ago, before this "
                    "deployment; the index backfill decides readiness")
        return False
    response = dynamodb_client.scan(TableName=asset_table_name, Limit=1, ProjectionExpression='databaseId')
    if response.get('Items'):
        logger.info(f"Asset table {asset_table_name} already holds asset rows; the index backfill decides readiness")
        return False
    return True


def write_marker_if_new_deployment(asset_table_name: str, parameter_name: str, window_seconds: int) -> bool:
    """Write the readiness marker for a new, empty asset table. Returns whether a marker was written."""
    now = _now()
    if not table_is_new_and_empty(asset_table_name, window_seconds, now):
        return False
    try:
        ssm_client.put_parameter(
            Name=parameter_name,
            Value=marker_value(asset_table_name, now),
            Type='String',
            Overwrite=False,
            Description='VAMS asset location index ready (new deployment, no asset rows to backfill)',
        )
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'ParameterAlreadyExists':
            logger.info(f"Location index marker {parameter_name} already exists; left unchanged")
            return False
        raise
    logger.info(f"Wrote location index marker {parameter_name} for new asset table {asset_table_name}")
    return True


def delete_marker(parameter_name: str) -> bool:
    """Delete the readiness marker. Returns whether one was deleted (False when there was none)."""
    try:
        ssm_client.delete_parameter(Name=parameter_name)
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'ParameterNotFound':
            logger.info(f"Location index marker {parameter_name} does not exist; nothing to delete")
            return False
        raise
    logger.info(f"Deleted location index marker {parameter_name}")
    return True


def _on_delete(event, parameter_name: str, physical_id: str):
    if not parameter_name:
        logger.error("The marker parameter name is missing; no marker is deleted")
        return {'PhysicalResourceId': physical_id}
    try:
        deleted = delete_marker(parameter_name)
    except Exception as e:
        logger.exception(f"Could not delete the location index marker {parameter_name}; delete it by hand: {e}")
        log_action(event, "assetLocationIndexUnmark", outcome="failed", reason="internal",
                   details={'deleted': False})
        return {'PhysicalResourceId': physical_id}
    log_action(event, "assetLocationIndexUnmark", details={'deleted': deleted})
    return {'PhysicalResourceId': physical_id}


@audited_handler
def lambda_handler(event, context):
    """cr.Provider onEvent handler. A Create writes the marker for a new deployment, an Update changes
    nothing, and a Delete deletes the marker; the deployment never fails because of it.

    A Create is recorded as the system action `assetLocationIndexMark`, with `details.written` saying
    whether the marker was written, and a Delete as `assetLocationIndexUnmark`, with `details.deleted`."""
    request_type = event.get('RequestType')
    physical_id = event.get('PhysicalResourceId') or PHYSICAL_RESOURCE_ID
    props = event.get('ResourceProperties') or {}
    asset_table_name = props.get('assetTableName', '')
    parameter_name = props.get('markerParameterName', '')
    if request_type == 'Delete':
        return _on_delete(event, parameter_name, physical_id)
    if request_type != 'Create':
        return {'PhysicalResourceId': physical_id}
    try:
        window_seconds = int(props.get('newTableWindowSeconds', DEFAULT_NEW_TABLE_WINDOW_SECONDS))
    except (TypeError, ValueError):
        window_seconds = DEFAULT_NEW_TABLE_WINDOW_SECONDS
    if not asset_table_name or not parameter_name:
        logger.error("The asset table name or the marker parameter name is missing; no marker is written")
        return {'PhysicalResourceId': physical_id}
    try:
        written = write_marker_if_new_deployment(asset_table_name, parameter_name, window_seconds)
    except Exception as e:
        logger.exception(f"Could not decide or write the location index marker; run the index backfill: {e}")
        log_action(event, "assetLocationIndexMark", outcome="failed", reason="internal",
                   details={'written': False})
        return {'PhysicalResourceId': physical_id}
    log_action(event, "assetLocationIndexMark", details={'written': written})
    return {'PhysicalResourceId': physical_id}
