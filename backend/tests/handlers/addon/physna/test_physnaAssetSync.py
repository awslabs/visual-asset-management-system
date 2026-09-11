# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch

# Module-level import ensures the real `backend.backend.handlers` package is
# populated in sys.modules before the root conftest's autouse fixture runs,
# preventing it from stubbing the package with a MockModule.
from backend.backend.handlers.addon.physna import physnaAssetSync as _pas  # noqa: F401


def _asset_stream_event(event_name, database_id, asset_id):
    record = {
        "eventSource": "aws:dynamodb",
        "eventName": event_name,
        "dynamodb": {
            "Keys": {
                "databaseId": {"S": database_id},
                "assetId": {"S": asset_id},
            },
            "NewImage": {
                "databaseId": {"S": database_id},
                "assetId": {"S": asset_id},
            },
        },
    }
    sns_message = json.dumps(record)
    return {
        "Records": [
            {
                "eventSource": "aws:sqs",
                "body": json.dumps({"Type": "Notification", "Message": sns_message}),
            }
        ]
    }


@pytest.mark.unit
class TestAssetMetadataSync:
    def test_modify_event_triggers_per_file_sync(self):
        from backend.backend.handlers.addon.physna import physnaAssetSync

        event = _asset_stream_event("MODIFY", "db-1", "asset-1")
        with patch.object(physnaAssetSync, "_sync_asset_metadata_to_physna") as sync:
            response = physnaAssetSync.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 200
        sync.assert_called_once_with("db-1", "asset-1", is_delete=False)

    def test_remove_event_triggers_full_delete(self):
        from backend.backend.handlers.addon.physna import physnaAssetSync

        event = _asset_stream_event("REMOVE", "db-1", "asset-1")
        with patch.object(physnaAssetSync, "_sync_asset_metadata_to_physna") as sync:
            response = physnaAssetSync.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 200
        sync.assert_called_once_with("db-1", "asset-1", is_delete=True)

    def test_asset_level_metadata_row_triggers_resync(self):
        """Asset-level metadata edits arrive via assetFileMetadataStorageTable
        streams whose sort key is 'databaseId:assetId:/'. Those must also
        trigger the per-file sync."""
        from backend.backend.handlers.addon.physna import physnaAssetSync

        inner = {
            "eventSource": "aws:dynamodb",
            "eventName": "MODIFY",
            "dynamodb": {
                "Keys": {
                    "metadataKey": {"S": "partFamily"},
                    "databaseId:assetId:filePath": {"S": "db-1:asset-1:/"},
                },
                "NewImage": {
                    "databaseId:assetId:filePath": {"S": "db-1:asset-1:/"},
                    "metadataKey": {"S": "partFamily"},
                    "metadataValue": {"S": "widgets"},
                },
            },
        }
        event = {
            "Records": [
                {
                    "eventSource": "aws:sqs",
                    "body": json.dumps(
                        {"Type": "Notification", "Message": json.dumps(inner)}
                    ),
                }
            ]
        }
        with patch.object(physnaAssetSync, "_sync_asset_metadata_to_physna") as sync:
            response = physnaAssetSync.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 200
        sync.assert_called_once_with("db-1", "asset-1", is_delete=False)

    def test_file_level_metadata_row_is_skipped_in_asset_sync(self):
        """File-level metadata edits (filePath != '/') must be ignored here —
        they are handled by physnaFileSync."""
        from backend.backend.handlers.addon.physna import physnaAssetSync

        inner = {
            "eventSource": "aws:dynamodb",
            "eventName": "MODIFY",
            "dynamodb": {
                "Keys": {
                    "metadataKey": {"S": "color"},
                    "databaseId:assetId:filePath": {"S": "db-1:asset-1:/part.step"},
                },
                "NewImage": {
                    "databaseId:assetId:filePath": {"S": "db-1:asset-1:/part.step"},
                },
            },
        }
        event = {
            "Records": [
                {
                    "eventSource": "aws:sqs",
                    "body": json.dumps(
                        {"Type": "Notification", "Message": json.dumps(inner)}
                    ),
                }
            ]
        }
        with patch.object(physnaAssetSync, "_sync_asset_metadata_to_physna") as sync:
            response = physnaAssetSync.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 200
        assert sync.call_count == 0


@pytest.mark.unit
class TestAssetSyncReuploadsWhenFileVersionMissing:
    """Inside the per-file sync loop, a Physna asset missing
    __VAMS__FileVersion must be treated as stale and routed to
    ``physnaFileSync._upload_file_to_physna`` instead of a plain metadata
    PATCH. A Physna asset that still has the tag takes the PATCH path."""

    def _run_with_physna_asset_having_version(self, physna_version):
        from backend.backend.handlers.addon.physna import (
            physnaAssetSync,
            physnaFileSync,
        )

        physna_listing_item = {
            "id": "uuid-1",
            "path": "db-1/asset-1/part.step",
            "metadata": (
                {"__VAMS__FileVersion": physna_version}
                if physna_version is not None
                else {}
            ),
        }

        with patch.object(physnaAssetSync, "PhysnaClient") as PhysnaClientCls, \
             patch.object(
                 physnaAssetSync, "list_physna_assets_under"
             ) as list_assets, \
             patch.object(
                 physnaAssetSync, "get_asset_details"
             ) as get_details, \
             patch.object(
                 physnaAssetSync, "get_asset_metadata"
             ) as get_asset_meta, \
             patch.object(
                 physnaAssetSync, "get_file_metadata"
             ) as get_file_meta, \
             patch.object(
                 physnaAssetSync, "get_bucket_details"
             ) as get_bucket, \
             patch.object(
                 physnaAssetSync, "_list_vams_file_paths"
             ) as vams_files, \
             patch.object(
                 physnaAssetSync.physnaFileSync, "_upload_file_to_physna"
             ) as upload, \
             patch.object(
                 physnaAssetSync, "ensure_metadata_fields_registered"
             ), \
             patch.object(
                 physnaAssetSync, "delete_physna_metadata_fields"
             ):
            PhysnaClientCls.return_value = MagicMock()
            list_assets.return_value = iter([physna_listing_item])
            get_details.return_value = {
                "assetName": "My Asset",
                "bucketId": "b-1",
                "assetLocation": {"Key": "prefix/asset-1/"},
            }
            get_asset_meta.return_value = {}
            get_file_meta.return_value = ({}, {})
            get_bucket.return_value = {
                "bucketName": "bucket-1",
                "baseAssetsPrefix": "prefix/",
            }
            vams_files.return_value = {"/part.step"}

            # Prevent the PATCH from actually hitting the network; return 204
            # so the loop continues cleanly when it's the PATCH path.
            patch_resp = MagicMock()
            patch_resp.status = 204
            PhysnaClientCls.return_value.request.return_value = patch_resp

            physnaAssetSync._sync_asset_metadata_to_physna(
                "db-1", "asset-1", is_delete=False
            )

        return upload, PhysnaClientCls.return_value

    def test_missing_file_version_tag_triggers_reupload(self):
        upload, client = self._run_with_physna_asset_having_version(None)
        upload.assert_called_once()
        # No PATCH should have been issued for this file in the loop
        patch_calls = [
            c
            for c in client.request.call_args_list
            if c.args and c.args[0] == "PATCH"
        ]
        assert patch_calls == []

    def test_present_file_version_tag_keeps_patch_path(self):
        upload, client = self._run_with_physna_asset_having_version("v-abc")
        upload.assert_not_called()
        patch_calls = [
            c
            for c in client.request.call_args_list
            if c.args and c.args[0] == "PATCH"
        ]
        assert len(patch_calls) == 1
