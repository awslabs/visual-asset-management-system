# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync and the reindexer record what they changed, as the system, in the invocation's context."""

import importlib.util
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load as _load_sync

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_CR_REINDEXER_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing",
                                  "crReindexer.py")


@pytest.mark.unit
class TestBucketSyncRecords:
    def test_a_sync_record_names_the_invocation_event_and_the_source(self, monkeypatch):
        m = _load_sync()
        recorder = MagicMock()
        event = {"Records": []}
        monkeypatch.setattr(m, "log_action", recorder)
        monkeypatch.setattr(m, "current_audit_context", lambda: SimpleNamespace(event=event))
        m._log_sync("fileSync", "created", database_id="db1", asset_id="asset-01", file_path="/a.glb", bytes=42)
        recorder.assert_called_once_with(event, "fileSync", details={"source": "bucketSync", "operation": "created"},
                                         database_id="db1", asset_id="asset-01", file_path="/a.glb", bytes=42)

    def test_no_record_is_written_outside_an_invocation(self, monkeypatch):
        m = _load_sync()
        recorder = MagicMock()
        monkeypatch.setattr(m, "log_action", recorder)
        monkeypatch.setattr(m, "current_audit_context", lambda: None)
        m._log_sync("assetSync", "create", database_id="db1", asset_id="asset-01")
        recorder.assert_not_called()


@pytest.fixture
def reindexer():
    with patch("boto3.client", return_value=MagicMock()), patch("boto3.resource", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location("crReindexer_under_test_audit",
                                                      os.path.abspath(_CR_REINDEXER_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestReindexRecord:
    def test_a_run_records_the_counts_it_reindexed(self, reindexer, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(reindexer, "log_action", recorder)
        reindexer._log_reindex({"operation": "both"}, {"assets": {"success_count": 3}, "files": {"success_count": 4}},
                               [], "both", False)
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args[1] == "searchReindex"
        assert kwargs["outcome"] == "success" and kwargs["item_count"] == 7
        assert kwargs["details"] == {"operation": "both", "clearIndexes": False, "dryRun": False}

    def test_an_incomplete_run_records_a_failure(self, reindexer, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(reindexer, "log_action", recorder)
        reindexer._log_reindex({}, {}, ["assets: 2 failed"], "assets", True, dry_run=True)
        kwargs = recorder.call_args.kwargs
        assert kwargs["outcome"] == "failed" and kwargs["reason"] == "internal" and kwargs["item_count"] is None
        assert "assets: 2 failed" not in str(kwargs), "the failure text is never recorded"


def _wire_existing_asset(monkeypatch, m, object_metadata):
    """Run process_s3_record on an object of an existing asset; the head the handler already makes
    returns ``object_metadata``."""
    recorder = MagicMock()
    event = {"Records": []}
    monkeypatch.setattr(m, "log_action", recorder)
    monkeypatch.setattr(m, "current_audit_context", lambda: SimpleNamespace(event=event))
    monkeypatch.setattr(m, "asset_bucket_name", "asset-bucket")
    monkeypatch.setattr(m, "asset_bucket_prefix", "db/")
    monkeypatch.setattr(m, "RESERVED_S3_PREFIX_FOLDERS", set())
    monkeypatch.setattr(m, "get_bucket_id", MagicMock(return_value="bucket-1"))
    monkeypatch.setattr(m, "lookup_asset", MagicMock(return_value={"databaseId": "db-1", "assetId": "x-asset-1"}))
    monkeypatch.setattr(m, "update_s3_metadata", MagicMock(return_value=True))
    monkeypatch.setattr(m, "update_asset_type", MagicMock(return_value=True))
    monkeypatch.setattr(m, "write_file_version_history", MagicMock())
    monkeypatch.setattr(m, "is_vams_metadata_stamp_echo", MagicMock(return_value=False))
    s3 = MagicMock()
    s3.head_object.return_value = {"VersionId": "v1", "Metadata": object_metadata}
    monkeypatch.setattr(m, "s3_client", s3)
    record = {"s3": {"bucket": {"name": "asset-bucket"}, "object": {"key": "db/x-asset-1/docs/b.glb", "size": 42}},
              "eventName": "ObjectCreated:Put"}
    m.process_s3_record(record)
    return recorder, event


@pytest.mark.unit
class TestBucketSyncSkipsVamsWrites:
    def test_a_direct_write_is_recorded_with_the_api_file_path(self, monkeypatch):
        m = _load_sync()
        recorder, event = _wire_existing_asset(monkeypatch, m, {"vams-changesource": "direct"})
        recorder.assert_called_once()
        assert recorder.call_args.args == (event, "fileSync")
        assert recorder.call_args.kwargs["file_path"] == "/docs/b.glb"
        assert recorder.call_args.kwargs["bytes"] == 42

    def test_an_unstamped_write_is_recorded(self, monkeypatch):
        m = _load_sync()
        recorder, _ = _wire_existing_asset(monkeypatch, m, {})
        assert recorder.call_args.args[1] == "fileSync"

    @pytest.mark.parametrize("source", ["upload", "fileCopy", "fileRevert", "workflowExecution"])
    def test_a_write_made_through_vams_is_not_recorded_again(self, monkeypatch, source):
        m = _load_sync()
        recorder, _ = _wire_existing_asset(monkeypatch, m, {"vams-changesource": source})
        recorder.assert_not_called()

    def test_a_delete_writes_no_sync_record(self):
        source = open(os.path.join(os.path.dirname(_CR_REINDEXER_PATH), "sqsBucketSync.py")).read()
        handler = source[source.index("def lambda_handler_deleted("):]
        assert "_log_sync(" not in handler
