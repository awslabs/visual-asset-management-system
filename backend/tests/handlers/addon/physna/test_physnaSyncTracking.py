# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests that Physna sync handlers write outbound sync tracking records."""

import json
import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.addon.physna import physnaFileSync, physnaAssetSync


@pytest.mark.unit
class TestUploadSyncTracking:
    def test_upload_failure_records_failed(self):
        with patch.object(physnaFileSync, "_upload_file_to_physna_impl",
                          side_effect=RuntimeError("physna down")), \
             patch.object(physnaFileSync, "_record_file_sync") as record:
            with pytest.raises(RuntimeError):
                physnaFileSync._upload_file_to_physna(
                    "db1", "a1", "/part.CATPart", "bucket", "prefix/a1/part.CATPart")
        record.assert_called_once()
        args = record.call_args[0]
        assert args[:3] == ("db1", "a1", "/part.CATPart")
        assert args[4] == physnaFileSync.SYNC_STATUS_FAILED
        assert record.call_args[1]["error_message"] == "physna down"

    def test_upload_success_passthrough_records_nothing_in_wrapper(self):
        # Success records are written inside the impl; the wrapper must not add one.
        with patch.object(physnaFileSync, "_upload_file_to_physna_impl",
                          return_value=True), \
             patch.object(physnaFileSync, "_record_file_sync") as record:
            assert physnaFileSync._upload_file_to_physna(
                "db1", "a1", "/part.CATPart", "bucket", "key") is True
        record.assert_not_called()


@pytest.mark.unit
class TestDeleteSyncTracking:
    def test_delete_success_records_delete(self):
        client = MagicMock()
        client.request.return_value = MagicMock(status=204)
        with patch.object(physnaFileSync, "lookup_physna_asset_id", return_value="uuid-1"), \
             patch.object(physnaFileSync, "delete_folder_if_empty"), \
             patch.object(physnaFileSync, "_record_file_sync") as record:
            physnaFileSync._delete_physna_asset(
                client, "db1", "a1", "/part.CATPart", skip_s3_existence_check=True)
        record.assert_called_once()
        args = record.call_args[0]
        assert args[:3] == ("db1", "a1", "/part.CATPart")
        assert args[3] == physnaFileSync.SYNC_ACTION_DELETE
        assert args[4] == physnaFileSync.SYNC_STATUS_SUCCESS
        assert record.call_args[1]["physna_asset_uuid"] == "uuid-1"

    def test_delete_failure_records_failed(self):
        client = MagicMock()
        client.request.return_value = MagicMock(status=500, data=b"err")
        with patch.object(physnaFileSync, "lookup_physna_asset_id", return_value="uuid-1"), \
             patch.object(physnaFileSync, "_record_file_sync") as record:
            with pytest.raises(physnaFileSync.PhysnaError):
                physnaFileSync._delete_physna_asset(
                    client, "db1", "a1", "/part.CATPart", skip_s3_existence_check=True)
        assert record.call_args[0][4] == physnaFileSync.SYNC_STATUS_FAILED

    def test_delete_already_gone_records_nothing(self):
        client = MagicMock()
        with patch.object(physnaFileSync, "lookup_physna_asset_id", return_value=None), \
             patch.object(physnaFileSync, "_record_file_sync") as record:
            physnaFileSync._delete_physna_asset(
                client, "db1", "a1", "/part.CATPart", skip_s3_existence_check=True)
        record.assert_not_called()


@pytest.mark.unit
class TestAssetSyncTracking:
    def _event(self):
        sns = {"eventName": "MODIFY",
               "dynamodb": {"Keys": {"databaseId": {"S": "db1"}, "assetId": {"S": "a1"}},
                            "NewImage": {"databaseId": {"S": "db1"}, "assetId": {"S": "a1"}}}}
        return {"Records": [{
            "eventSource": "aws:sqs",
            "body": json.dumps({"Type": "Notification", "Message": json.dumps(sns)}),
        }]}

    def test_asset_sync_success_records_modify(self):
        with patch.object(physnaAssetSync, "_sync_asset_metadata_to_physna"), \
             patch.object(physnaAssetSync, "_record_asset_sync") as record:
            physnaAssetSync.lambda_handler(self._event(), MagicMock())
        record.assert_called_once_with(
            "db1", "a1", physnaAssetSync.SYNC_ACTION_MODIFY,
            physnaAssetSync.SYNC_STATUS_SUCCESS)

    def test_asset_sync_failure_records_failed(self):
        with patch.object(physnaAssetSync, "_sync_asset_metadata_to_physna",
                          side_effect=RuntimeError("boom")), \
             patch.object(physnaAssetSync, "_record_asset_sync") as record:
            physnaAssetSync.lambda_handler(self._event(), MagicMock())
        args, kwargs = record.call_args
        assert args[2] == physnaAssetSync.SYNC_ACTION_MODIFY
        assert args[3] == physnaAssetSync.SYNC_STATUS_FAILED
        assert kwargs["error_message"] == "boom"

    def test_asset_delete_records_delete(self):
        sns = {"eventName": "REMOVE",
               "dynamodb": {"Keys": {"databaseId": {"S": "db1"}, "assetId": {"S": "a1"}}}}
        event = {"Records": [{
            "eventSource": "aws:sqs",
            "body": json.dumps({"Type": "Notification", "Message": json.dumps(sns)}),
        }]}
        with patch.object(physnaAssetSync, "_sync_asset_metadata_to_physna"), \
             patch.object(physnaAssetSync, "_record_asset_sync") as record:
            physnaAssetSync.lambda_handler(event, MagicMock())
        record.assert_called_once_with(
            "db1", "a1", physnaAssetSync.SYNC_ACTION_DELETE,
            physnaAssetSync.SYNC_STATUS_SUCCESS)
