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
logger = safeLogger(service_name="SyncTracking")

# Outbound sync statuses. pending/skipped are reserved for future writers.
SYNC_STATUS_PENDING = "pending"
SYNC_STATUS_SUCCESS = "success"
SYNC_STATUS_FAILED = "failed"
SYNC_STATUS_SKIPPED = "skipped"
ALLOWED_SYNC_STATUSES = (SYNC_STATUS_PENDING, SYNC_STATUS_SUCCESS, SYNC_STATUS_FAILED, SYNC_STATUS_SKIPPED)

# Action performed toward the target system.
SYNC_ACTION_CREATE = "create"
SYNC_ACTION_MODIFY = "modify"
SYNC_ACTION_DELETE = "delete"
ALLOWED_SYNC_ACTIONS = (SYNC_ACTION_CREATE, SYNC_ACTION_MODIFY, SYNC_ACTION_DELETE)

# VAMS object types that sync to external systems. systemType is deliberately
# NOT enumerated here — each sync handler defines its own constant.
SYNC_OBJECT_TYPE_DATABASE = "database"
SYNC_OBJECT_TYPE_ASSET = "asset"
SYNC_OBJECT_TYPE_ASSET_FILE = "assetFile"
ALLOWED_SYNC_OBJECT_TYPES = (SYNC_OBJECT_TYPE_DATABASE, SYNC_OBJECT_TYPE_ASSET, SYNC_OBJECT_TYPE_ASSET_FILE)

ERROR_MESSAGE_MAX_LENGTH = 1024

try:
    _sync_tracking_table_name = get_table_name(ResourceKeys.SYNC_TRACKING_OUTBOUND_STORAGE_TABLE)
except Exception:
    _sync_tracking_table_name = None

sync_tracking_outbound_table = (
    dynamodb.Table(_sync_tracking_table_name) if _sync_tracking_table_name else None
)


def write_outbound_sync_record(object_type, database_id, system_type, system_unique_id,
                               action, sync_status, asset_id=None, file_path=None,
                               s3_version_id=None, error_message=None,
                               sync_system_entity_id=None):
    """Write one outbound sync tracking record. Best-effort: failures and
    invalid input are logged and never raised into the calling sync handler."""
    try:
        if not sync_tracking_outbound_table:
            logger.warning("Sync tracking table not configured; skipping sync record")
            return
        if object_type not in ALLOWED_SYNC_OBJECT_TYPES:
            logger.warning(f"Invalid sync objectType {object_type!r}; skipping sync record")
            return
        if action not in ALLOWED_SYNC_ACTIONS:
            logger.warning(f"Invalid sync action {action!r}; skipping sync record")
            return
        if sync_status not in ALLOWED_SYNC_STATUSES:
            logger.warning(f"Invalid syncStatus {sync_status!r}; skipping sync record")
            return
        if not database_id or not system_type or not system_unique_id:
            logger.warning("Missing databaseId/systemType/systemUniqueId; skipping sync record")
            return
        if object_type in (SYNC_OBJECT_TYPE_ASSET, SYNC_OBJECT_TYPE_ASSET_FILE) and not asset_id:
            logger.warning(f"Missing assetId for {object_type} sync record; skipping")
            return
        if object_type == SYNC_OBJECT_TYPE_ASSET_FILE and not file_path:
            logger.warning("Missing filePath for assetFile sync record; skipping")
            return

        if object_type == SYNC_OBJECT_TYPE_DATABASE:
            object_id = database_id
        elif object_type == SYNC_OBJECT_TYPE_ASSET:
            object_id = f"{database_id}:{asset_id}"
        else:
            file_path = "/" + file_path.lstrip("/")
            object_id = f"{database_id}:{asset_id}:{file_path}"

        record_date = datetime.utcnow().isoformat() + "Z"
        item = {
            'objectId': object_id,
            'syncRecordId': f"{record_date}#{uuid.uuid4().hex[:8]}",
            'objectType': object_type,
            'databaseId': database_id,
            'systemType': system_type,
            'systemUniqueId': system_unique_id,
            'systemType:systemUniqueId': f"{system_type}:{system_unique_id}",
            'databaseId:systemType:systemUniqueId': f"{database_id}:{system_type}:{system_unique_id}",
            'action': action,
            'syncStatus': sync_status,
            'recordDate': record_date,
        }
        if asset_id:
            item['assetId'] = asset_id
        if object_type == SYNC_OBJECT_TYPE_ASSET_FILE:
            item['filePath'] = file_path
        if s3_version_id:
            item['s3VersionId'] = s3_version_id
        if error_message:
            item['errorMessage'] = str(error_message)[:ERROR_MESSAGE_MAX_LENGTH]
        if sync_system_entity_id:
            item['syncSystemEntityId'] = sync_system_entity_id
        sync_tracking_outbound_table.put_item(Item=item)
    except Exception as e:
        logger.warning(f"Failed writing outbound sync record for {database_id}: {e}")
