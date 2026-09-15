# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Garnet indexer module-load contract and sync-tracking action derivation.

Covers S2-BACKEND-067: a resource-name resolution failure must abort the cold
start. When the name was swallowed into `None` the module imported cleanly and
the first table call raised inside a broad per-record `except`, so every stream
and S3 event was discarded with a 200/500 that the SQS event-source mapping
treats as processed -- an indefinite silent data drop.

Covers S2-BACKEND-082: both `_record_sync` call sites in garnetDataIndexFile
hardcoded action="modify", so a first-time index was recorded as a modification
and no assetFile row ever carried create or delete. The action is derived from
the event name, mirroring the asset and database indexers.

The module-load tests load a FRESH copy of each indexer by file path rather than
reloading the shared module: a failed `importlib.reload` mutates the live module
in place and would leave the other tests in this directory running against a
half-initialized module.
"""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.addon.garnetFramework import garnetDataIndexFile

_GARNET_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "backend", "handlers", "addon", "garnetFramework",
)

_INDEXERS = {
    "asset": ("garnetDataIndexAsset.py", "asset_storage_table"),
    "database": ("garnetDataIndexDatabase.py", "database_storage_table"),
    "file": ("garnetDataIndexFile.py", "asset_storage_table"),
}


def _load_fresh(file_name, unique_suffix):
    """Load an independent copy of a garnet indexer module by file path."""
    path = os.path.abspath(os.path.join(_GARNET_DIR, file_name))
    spec = importlib.util.spec_from_file_location(
        f"{file_name[:-3]}_under_test_{unique_suffix}", path)
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.resource", return_value=MagicMock()), \
            patch("boto3.client", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestModuleLoadFailsClosed:
    """S2-BACKEND-067: an unresolvable resource name must fail the cold start."""

    @pytest.mark.parametrize("indexer", sorted(_INDEXERS))
    def test_import_raises_when_resource_name_unresolvable(self, indexer):
        file_name, _ = _INDEXERS[indexer]
        boom = RuntimeError("SSM unreachable")
        with patch("common.resourceNames.get_table_name", side_effect=boom):
            with pytest.raises(RuntimeError) as excinfo:
                _load_fresh(file_name, f"fail_{indexer}")
        assert "SSM unreachable" in str(excinfo.value)

    @pytest.mark.parametrize("indexer", sorted(_INDEXERS))
    def test_import_succeeds_and_tables_are_built_when_names_resolve(self, indexer):
        """Positive control for the test above: with names resolvable the module
        imports and every table is a real object, not None. Without this a
        constructor error unrelated to resolution would make the negative test
        pass for the wrong reason."""
        file_name, table_attr = _INDEXERS[indexer]
        module = _load_fresh(file_name, f"ok_{indexer}")
        assert getattr(module, table_attr) is not None
        # No table may be left as None -- that was the swallowed-name shape.
        table_attrs = [name for name in vars(module) if name.endswith("_table")]
        assert table_attrs, "expected the module to build at least one table"
        for name in table_attrs:
            assert getattr(module, name) is not None, f"{name} resolved to None"


def _s3_record(event_name):
    return {
        "eventName": event_name,
        "s3": {"bucket": {"name": "bucket"}, "object": {"key": "a1/part.stp"}},
    }


def _stream_record(event_name):
    key = {"databaseId:assetId:filePath": {"S": "db1:a1:/part.stp"}}
    return {"eventName": event_name,
            "dynamodb": {"Keys": key, "NewImage": key}}


def _s3_patches(m):
    """Stubs for the lookups handle_s3_notification performs before recording."""
    s3_client = MagicMock()
    s3_client.head_object.return_value = {
        "Metadata": {"assetid": "a1", "databaseid": "db1"}
    }
    return [
        patch.object(m, "s3_client", s3_client),
        patch.object(m, "get_asset_details",
                     return_value={"assetId": "a1", "bucketId": "b1",
                                   "assetLocation": {"Key": "a1/"}}),
        patch.object(m, "get_bucket_details",
                     return_value={"bucketName": "bucket", "baseAssetsPrefix": ""}),
        patch.object(m, "get_file_metadata", return_value=({}, {})),
        patch.object(m, "get_s3_file_info", return_value=({"versionId": "v1"}, False)),
        patch.object(m, "convert_file_to_ngsi_ld",
                     return_value={"id": "urn:vams:file:db1:a1:%2Fpart.stp",
                                   "type": "VAMSFile"}),
        patch.object(m, "send_to_garnet_ingestion_queue", return_value=True),
    ]


def _stream_patches(m):
    """Stubs for the lookups handle_file_metadata_stream performs."""
    return [
        patch.object(m, "get_asset_details",
                     return_value={"assetId": "a1", "bucketId": "b1",
                                   "assetLocation": {"Key": "a1/"}}),
        patch.object(m, "get_bucket_details",
                     return_value={"bucketName": "bucket", "baseAssetsPrefix": ""}),
        patch.object(m, "get_file_metadata", return_value=({}, {})),
        patch.object(m, "get_s3_file_info", return_value=({"versionId": "v1"}, False)),
        patch.object(m, "convert_file_to_ngsi_ld",
                     return_value={"id": "urn:vams:file:db1:a1:%2Fpart.stp",
                                   "type": "VAMSFile"}),
        patch.object(m, "send_to_garnet_ingestion_queue", return_value=True),
    ]


def _recorded_actions(handler, record, patches, m):
    """Run one handler and return the actions handed to write_outbound_sync_record.

    Asserted on the arguments rather than a call count so the test does not pin
    how many records the handler writes.
    """
    recorder = MagicMock()
    with patch.object(m, "_record_sync", recorder):
        for p in patches:
            p.start()
        try:
            handler(record)
        finally:
            for p in patches:
                p.stop()
    return [call.args[1] for call in recorder.call_args_list]


@pytest.mark.unit
class TestFileIndexerSyncAction:
    """S2-BACKEND-082: the action must follow the event, not be a constant."""

    @pytest.mark.parametrize("event_name,expected", [
        ("ObjectCreated:Put", "create"),
        ("ObjectCreated:CompleteMultipartUpload", "create"),
        ("ObjectRemoved:Delete", "delete"),
        ("ObjectRestore:Completed", "modify"),
    ])
    def test_s3_notification_action_follows_event(self, event_name, expected):
        m = garnetDataIndexFile
        actions = _recorded_actions(m.handle_s3_notification,
                                    _s3_record(event_name), _s3_patches(m), m)
        assert actions == [expected]

    @pytest.mark.parametrize("event_name,expected", [
        ("INSERT", "create"),
        ("REMOVE", "delete"),
        ("MODIFY", "modify"),
    ])
    def test_metadata_stream_action_follows_event(self, event_name, expected):
        m = garnetDataIndexFile
        actions = _recorded_actions(m.handle_file_metadata_stream,
                                    _stream_record(event_name), _stream_patches(m), m)
        assert actions == [expected]

    def test_extension_less_file_is_not_treated_as_a_folder(self):
        """S2-BACKEND-097's second site. The finding cites the OpenSearch file
        indexer; the Garnet file indexer carried the same `'.' not in basename`
        test, so `LICENSE` and friends were skipped from the knowledge graph too.
        Asserted through the handler, because the skip happens before any lookup:
        a recorded sync proves the path was not short-circuited."""
        m = garnetDataIndexFile
        assert m.is_folder_path("/LICENSE") is False
        assert m.is_folder_path("/folder/") is True
        actions = _recorded_actions(m.handle_file_metadata_stream,
                                    {"eventName": "INSERT",
                                     "dynamodb": {"NewImage": {
                                         "databaseId:assetId:filePath": {"S": "db1:a1:/LICENSE"}}}},
                                    _stream_patches(m), m)
        assert actions == ["create"], "an extension-less file was skipped as a folder"

    def test_folder_metadata_is_still_skipped(self):
        """Positive control for the test above."""
        m = garnetDataIndexFile
        actions = _recorded_actions(m.handle_file_metadata_stream,
                                    {"eventName": "INSERT",
                                     "dynamodb": {"NewImage": {
                                         "databaseId:assetId:filePath": {"S": "db1:a1:/folder/"}}}},
                                    _stream_patches(m), m)
        assert actions == []

    def test_action_constants_are_the_shared_sync_tracking_values(self):
        """The literals asserted above are the shared constants, so a rename in
        common.syncTracking cannot leave these tests asserting stale strings."""
        m = garnetDataIndexFile
        assert m.SYNC_ACTION_CREATE == "create"
        assert m.SYNC_ACTION_DELETE == "delete"
        assert m.SYNC_ACTION_MODIFY == "modify"
