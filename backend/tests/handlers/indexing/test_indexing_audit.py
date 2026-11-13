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
