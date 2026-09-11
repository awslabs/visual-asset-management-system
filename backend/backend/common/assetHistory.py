# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import boto3
import uuid
from datetime import datetime
from botocore.config import Config
from customLogging.logger import safeLogger
from common.resourceNames import ResourceKeys, get_table_name

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="AssetHistory")

# Asset lifecycle history change sources. The *Direct variants mark records
# originated from S3 bucket-sync ingestion rather than a VAMS API call.
CHANGE_SOURCE_CREATE = "create"
CHANGE_SOURCE_CREATE_DIRECT = "createDirect"
CHANGE_SOURCE_EDIT = "edit"
CHANGE_SOURCE_ARCHIVE = "archive"
CHANGE_SOURCE_UNARCHIVE = "unarchive"
CHANGE_SOURCE_UNARCHIVE_DIRECT = "unarchiveDirect"
CHANGE_SOURCE_PERMANENT_DELETE = "permanentDelete"

try:
    _asset_history_table_name = get_table_name(ResourceKeys.ASSET_HISTORY_STORAGE_TABLE)
except Exception:
    _asset_history_table_name = None

asset_history_table = dynamodb.Table(_asset_history_table_name) if _asset_history_table_name else None


def build_asset_snapshot(asset_record, archived_reason=None, unarchived_reason=None):
    """Build the open-schema assetSnapshot map from an asset record's fields
    as they stand after the operation being recorded."""
    snapshot = {
        'assetName': asset_record.get('assetName', ''),
        'description': asset_record.get('description', ''),
        'isDistributable': asset_record.get('isDistributable', False),
        'tags': asset_record.get('tags', []),
        'bucketId': asset_record.get('bucketId', ''),
    }
    asset_location = asset_record.get('assetLocation') or {}
    if asset_location.get('Key'):
        snapshot['assetLocationKey'] = asset_location['Key']
    if archived_reason:
        snapshot['archivedReason'] = archived_reason
    if unarchived_reason:
        snapshot['unarchivedReason'] = unarchived_reason
    return snapshot


def write_asset_history_record(database_id, asset_id, change_source, change_user_id, asset_snapshot):
    """Write one asset lifecycle history record. Best-effort: failures are
    logged and never raised into the calling operation. Records persist across
    asset permanent deletes."""
    try:
        if not asset_history_table:
            logger.warning("Asset history table not configured; skipping history record")
            return
        record_date = datetime.utcnow().isoformat() + "Z"
        item = {
            'databaseId:assetId': f"{database_id}:{asset_id}",
            'historyRecordId': f"{record_date}#{uuid.uuid4().hex[:8]}",
            'databaseId': database_id,
            'assetId': asset_id,
            'recordDate': record_date,
            'changeSource': change_source,
            'changeUserId': change_user_id or 'SYSTEM_USER',
            'assetSnapshot': asset_snapshot or {},
        }
        asset_history_table.put_item(Item=item)
    except Exception as e:
        logger.warning(f"Failed writing asset history record for {asset_id}: {e}")
