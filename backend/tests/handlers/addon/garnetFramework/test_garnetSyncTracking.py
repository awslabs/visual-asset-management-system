# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests that Garnet indexers write outbound sync tracking records."""

import pytest
from unittest.mock import patch

from backend.backend.handlers.addon.garnetFramework import (
    garnetDataIndexDatabase,
    garnetDataIndexAsset,
    garnetDataIndexFile,
)


def _stream_record(event_name, keys=None, new_image=None):
    return {"eventName": event_name,
            "dynamodb": {"Keys": keys or {}, "NewImage": new_image or {}}}


@pytest.mark.unit
class TestDatabaseIndexerSyncTracking:
    def test_remove_records_delete(self):
        m = garnetDataIndexDatabase
        record = _stream_record("REMOVE", keys={"databaseId": {"S": "db1"}})
        with patch.object(m, "send_to_garnet_ingestion_queue", return_value=True), \
             patch.object(m, "_record_sync") as rec:
            assert m.handle_database_stream(record) is True
        rec.assert_called_once()
        args = rec.call_args[0]
        assert args[0] == m.SYNC_OBJECT_TYPE_DATABASE
        assert args[1] == m.SYNC_ACTION_DELETE
        assert args[2] is True
        assert args[3] == "db1"
        assert rec.call_args[1]["entity_id"] == "urn:vams:database:db1"

    def test_insert_records_create_and_failure_flag(self):
        m = garnetDataIndexDatabase
        record = _stream_record("INSERT", new_image={"databaseId": {"S": "db1"}})
        with patch.object(m, "get_database_details", return_value={"databaseId": "db1"}), \
             patch.object(m, "get_bucket_details", return_value=None), \
             patch.object(m, "get_database_metadata", return_value={}), \
             patch.object(m, "convert_database_to_ngsi_ld",
                          return_value={"id": "urn:vams:database:db1", "type": "VAMSDatabase"}), \
             patch.object(m, "send_to_garnet_ingestion_queue", return_value=False), \
             patch.object(m, "_record_sync") as rec:
            assert m.handle_database_stream(record) is False
        args = rec.call_args[0]
        assert args[1] == m.SYNC_ACTION_CREATE
        assert args[2] is False


@pytest.mark.unit
class TestAssetIndexerSyncTracking:
    def test_remove_records_delete(self):
        m = garnetDataIndexAsset
        record = _stream_record("REMOVE", keys={"databaseId": {"S": "db1"},
                                                "assetId": {"S": "a1"}})
        with patch.object(m, "send_to_garnet_ingestion_queue", return_value=True), \
             patch.object(m, "_record_sync") as rec:
            assert m.handle_asset_stream(record) is True
        args = rec.call_args[0]
        assert args[0] == m.SYNC_OBJECT_TYPE_ASSET
        assert args[1] == m.SYNC_ACTION_DELETE
        assert rec.call_args[1]["asset_id"] == "a1"

    def test_modify_records_only_primary_entity(self):
        m = garnetDataIndexAsset
        record = _stream_record("MODIFY", new_image={"databaseId": {"S": "db1"},
                                                     "assetId": {"S": "a1"}})
        with patch.object(m, "get_asset_details", return_value={"assetId": "a1"}), \
             patch.object(m, "get_bucket_details", return_value=None), \
             patch.object(m, "get_asset_metadata", return_value={}), \
             patch.object(m, "get_asset_version_info", return_value={}), \
             patch.object(m, "get_asset_relationship_flags", return_value={}), \
             patch.object(m, "convert_asset_to_ngsi_ld",
                          return_value={"id": "urn:vams:asset:db1:a1", "type": "VAMSAsset"}), \
             patch.object(m, "get_all_asset_links_for_asset", return_value=["link-1"]), \
             patch.object(m, "get_asset_link_details", return_value={"assetLinkId": "link-1"}), \
             patch.object(m, "get_asset_link_metadata", return_value={}), \
             patch.object(m, "convert_asset_link_to_ngsi_ld",
                          return_value={"id": "urn:vams:assetlink:link-1", "type": "VAMSAssetLink"}), \
             patch.object(m, "send_to_garnet_ingestion_queue", return_value=True), \
             patch.object(m, "_record_sync") as rec:
            assert m.handle_asset_stream(record) is True
        # One record for the asset entity; none for the cascaded link re-send
        rec.assert_called_once()
        assert rec.call_args[0][1] == m.SYNC_ACTION_MODIFY


@pytest.mark.unit
class TestFileIndexerSyncTracking:
    def test_metadata_stream_records_modify_with_version(self):
        m = garnetDataIndexFile
        record = _stream_record(
            "MODIFY",
            new_image={"databaseId:assetId:filePath": {"S": "db1:a1:/part.stp"}})
        with patch.object(m, "get_asset_details",
                          return_value={"assetId": "a1", "bucketId": "b1",
                                        "assetLocation": {"Key": "a1/"}}), \
             patch.object(m, "get_bucket_details",
                          return_value={"bucketName": "bucket", "baseAssetsPrefix": ""}), \
             patch.object(m, "get_file_metadata", return_value=({}, {})), \
             patch.object(m, "get_s3_file_info",
                          return_value=({"versionId": "v42"}, False)), \
             patch.object(m, "is_folder_path", return_value=False), \
             patch.object(m, "convert_file_to_ngsi_ld",
                          return_value={"id": "urn:vams:file:db1:a1:%2Fpart.stp",
                                        "type": "VAMSFile"}), \
             patch.object(m, "send_to_garnet_ingestion_queue", return_value=True), \
             patch.object(m, "_record_sync") as rec:
            assert m.handle_file_metadata_stream(record) is True
        args, kwargs = rec.call_args
        assert args[0] == m.SYNC_OBJECT_TYPE_ASSET_FILE
        assert args[1] == m.SYNC_ACTION_MODIFY
        assert kwargs["file_path"] == "/part.stp"
        assert kwargs["s3_version_id"] == "v42"
        assert kwargs["entity_id"] == "urn:vams:file:db1:a1:%2Fpart.stp"

    def test_metadata_stream_send_failure_records_failed_flag(self):
        m = garnetDataIndexFile
        record = _stream_record(
            "MODIFY",
            new_image={"databaseId:assetId:filePath": {"S": "db1:a1:/part.stp"}})
        with patch.object(m, "get_asset_details",
                          return_value={"assetId": "a1", "bucketId": "b1",
                                        "assetLocation": {"Key": "a1/"}}), \
             patch.object(m, "get_bucket_details",
                          return_value={"bucketName": "bucket", "baseAssetsPrefix": ""}), \
             patch.object(m, "get_file_metadata", return_value=({}, {})), \
             patch.object(m, "get_s3_file_info", return_value=(None, False)), \
             patch.object(m, "is_folder_path", return_value=False), \
             patch.object(m, "convert_file_to_ngsi_ld",
                          return_value={"id": "urn:vams:file:db1:a1:%2Fpart.stp",
                                        "type": "VAMSFile"}), \
             patch.object(m, "send_to_garnet_ingestion_queue", return_value=False), \
             patch.object(m, "_record_sync") as rec:
            assert m.handle_file_metadata_stream(record) is False
        assert rec.call_args[0][2] is False
