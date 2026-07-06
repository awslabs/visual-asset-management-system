# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for fileIndexer archive/unarchive/permanent-delete handling.

Covers three defects observed in live testing:
1. Asset archive: file events for an archived asset (record in the
   {databaseId}#deleted partition) were skipped, leaving stale live file docs.
2. Asset unarchive: delete-marker removal emits ObjectRemoved events which the
   permanent-delete branch treated as real deletes, removing live file docs.
3. Asset permanent delete: trailing S3 version-delete events could not resolve
   the database_id (record gone), orphaning file docs.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


_FILE_INDEXER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing", "fileIndexer.py"
)


@pytest.fixture
def fileIndexer():
    """Load the real fileIndexer module by file path with boto3 stubbed
    (same pattern as test_fileIndexer_preview_skip)."""
    saved = {name: sys.modules.get(name) for name in ("handlers.auth", "handlers.authz")}

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["mock_token"]})
    sys.modules["handlers.auth"] = auth_stub

    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "fileIndexer_archive_under_test", os.path.abspath(_FILE_INDEXER_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


def _client_error(code, op="HeadObject"):
    return ClientError({"Error": {"Code": code}}, op)


@pytest.mark.unit
class TestGetAssetDetailsAnyState:
    def test_live_record_returned_not_archived(self, fileIndexer):
        fileIndexer.get_asset_details = MagicMock(return_value={"assetId": "a1"})
        details, archived = fileIndexer.get_asset_details_any_state("db1", "a1")
        assert details == {"assetId": "a1"} and archived is False

    def test_falls_back_to_deleted_partition(self, fileIndexer):
        def lookup(db, a):
            return {"assetId": "a1", "status": "archived"} if db == "db1#deleted" else None
        fileIndexer.get_asset_details = MagicMock(side_effect=lookup)
        details, archived = fileIndexer.get_asset_details_any_state("db1", "a1")
        assert details is not None and archived is True

    def test_missing_in_both_partitions(self, fileIndexer):
        fileIndexer.get_asset_details = MagicMock(return_value=None)
        details, archived = fileIndexer.get_asset_details_any_state("db1", "a1")
        assert details is None and archived is False

    def test_no_double_suffix_lookup(self, fileIndexer):
        fileIndexer.get_asset_details = MagicMock(return_value=None)
        fileIndexer.get_asset_details_any_state("db1#deleted", "a1")
        # Only one lookup — never db1#deleted#deleted
        assert fileIndexer.get_asset_details.call_count == 1


@pytest.mark.unit
class TestLookupDatabaseIdStripsDeletedSuffix:
    def test_single_match_in_archived_partition(self, fileIndexer):
        fileIndexer.asset_storage_table = MagicMock()
        fileIndexer.asset_storage_table.query.return_value = {
            "Items": [{"databaseId": "db1#deleted", "assetId": "a1"}]
        }
        db_id, ok = fileIndexer.lookup_database_id_for_permanent_delete("a1", "bucket", "/")
        assert ok is True and db_id == "db1"


@pytest.mark.unit
class TestUnarchiveNotTreatedAsPermanentDelete:
    """ObjectRemoved from delete-marker removal (unarchive) must re-index, not delete."""

    def _s3_record(self):
        return {
            "eventName": "ObjectRemoved:DeleteMarkerCreated",
            "s3": {"bucket": {"name": "bucket"}, "object": {"key": "a1/file.txt"}},
        }

    def test_live_object_after_marker_removal_reindexes(self, fileIndexer):
        # list_object_versions: live versions remain, NO delete marker
        fileIndexer.s3_client = MagicMock()
        fileIndexer.s3_client.list_object_versions.return_value = {
            "Versions": [{"Key": "a1/file.txt", "VersionId": "v1"}],
            "DeleteMarkers": [],
        }
        fileIndexer.s3_client.head_object.return_value = {
            "Metadata": {"databaseid": "db1", "assetid": "a1"}
        }
        fileIndexer.get_asset_details_any_state = MagicMock(
            return_value=({"assetId": "a1", "bucketId": "b1",
                           "assetLocation": {"Key": "a1/"}}, False)
        )
        fileIndexer.get_bucket_details = MagicMock(return_value={
            "bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""
        })
        captured = {}

        def capture(request):
            captured["request"] = request
            return fileIndexer.IndexOperationResponse(
                success=True, message="ok", indexName="idx", operation="index")

        # IndexOperationResponse is imported into the module namespace
        fileIndexer.process_file_index_request = capture
        result = fileIndexer.handle_s3_notification(self._s3_record())

        assert result.operation == "index"
        req = captured["request"]
        assert req.operation == "index"
        assert req.isArchived is False

    def test_stale_archived_record_does_not_flip_back(self, fileIndexer):
        # Same as above, but the asset record still sits in the archived
        # partition (unarchive moves DDB after S3) — force_live must win.
        fileIndexer.s3_client = MagicMock()
        fileIndexer.s3_client.list_object_versions.return_value = {
            "Versions": [{"Key": "a1/file.txt", "VersionId": "v1"}],
            "DeleteMarkers": [],
        }
        fileIndexer.s3_client.head_object.return_value = {
            "Metadata": {"databaseid": "db1", "assetid": "a1"}
        }
        fileIndexer.get_asset_details_any_state = MagicMock(
            return_value=({"assetId": "a1", "bucketId": "b1",
                           "assetLocation": {"Key": "a1/"}}, True)  # archived partition
        )
        fileIndexer.get_bucket_details = MagicMock(return_value={
            "bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""
        })
        captured = {}

        def capture(request):
            captured["request"] = request
            return fileIndexer.IndexOperationResponse(
                success=True, message="ok", indexName="idx", operation="index")

        fileIndexer.process_file_index_request = capture
        fileIndexer.handle_s3_notification(self._s3_record())

        assert captured["request"].isArchived is False

    def test_live_object_but_asset_gone_skips_reindex(self, fileIndexer):
        # Mid permanent-delete burst: an early per-version ObjectRemoved event
        # can observe the key still live while the asset record is already gone
        # from both partitions. Must NOT re-index (delete in progress).
        fileIndexer.s3_client = MagicMock()
        fileIndexer.s3_client.list_object_versions.return_value = {
            "Versions": [{"Key": "a1/file.txt", "VersionId": "v1"}],
            "DeleteMarkers": [],
        }
        fileIndexer.s3_client.head_object.return_value = {
            "Metadata": {"databaseid": "db1", "assetid": "a1"}
        }
        fileIndexer.get_asset_details_any_state = MagicMock(return_value=(None, False))
        fileIndexer.process_file_index_request = MagicMock()

        result = fileIndexer.handle_s3_notification(self._s3_record())
        assert result.operation == "skip"
        fileIndexer.process_file_index_request.assert_not_called()

    def test_truly_permanent_delete_still_deletes(self, fileIndexer):
        # No versions and no markers -> permanent-delete branch as before
        fileIndexer.s3_client = MagicMock()
        fileIndexer.s3_client.list_object_versions.return_value = {
            "Versions": [], "DeleteMarkers": [],
        }
        fileIndexer.asset_storage_table = MagicMock()
        fileIndexer.asset_storage_table.query.return_value = {
            "Items": [{"databaseId": "db1", "assetId": "a1"}]
        }
        fileIndexer.delete_file_document = MagicMock(return_value=True)
        result = fileIndexer.handle_s3_notification(self._s3_record())
        assert result.operation == "delete"
        fileIndexer.delete_file_document.assert_called_once_with("db1", "a1", "/file.txt")


@pytest.mark.unit
class TestOrphanCleanupFallback:
    def test_orphan_docs_deleted_when_record_gone(self, fileIndexer):
        fileIndexer.s3_client = MagicMock()
        fileIndexer.s3_client.list_object_versions.return_value = {
            "Versions": [], "DeleteMarkers": [],
        }
        # Asset record entirely gone
        fileIndexer.asset_storage_table = MagicMock()
        fileIndexer.asset_storage_table.query.return_value = {"Items": []}
        fileIndexer.delete_file_documents_by_asset_and_path = MagicMock(return_value=1)

        record = {
            "eventName": "ObjectRemoved:Delete",
            "s3": {"bucket": {"name": "bucket"}, "object": {"key": "a1/file.txt"}},
        }
        result = fileIndexer.handle_s3_notification(record)
        assert result.operation == "delete"
        fileIndexer.delete_file_documents_by_asset_and_path.assert_called_once_with(
            "a1", "/file.txt", "bucket"
        )

    def test_orphan_search_drains_and_deletes_by_id(self, fileIndexer):
        client = MagicMock()
        # First search returns two hits, second returns none (drained)
        client.search.side_effect = [
            {"hits": {"hits": [{"_id": "d1"}, {"_id": "d2"}]}},
            {"hits": {"hits": []}},
        ]
        fileIndexer.opensearch_manager = MagicMock()
        fileIndexer.opensearch_manager.is_available.return_value = True
        fileIndexer.opensearch_manager.get_client.return_value = client

        deleted = fileIndexer.delete_file_documents_by_asset_and_path(
            "a1", "/file.txt", "bucket")
        assert deleted == 2
        assert client.delete.call_count == 2

    def test_orphan_search_stops_on_stale_repeat_hits(self, fileIndexer):
        # Deleted docs may linger in results until index refresh; identical
        # hits on the next round must terminate the loop, not spin.
        client = MagicMock()
        client.search.return_value = {"hits": {"hits": [{"_id": "d1"}]}}
        fileIndexer.opensearch_manager = MagicMock()
        fileIndexer.opensearch_manager.is_available.return_value = True
        fileIndexer.opensearch_manager.get_client.return_value = client

        deleted = fileIndexer.delete_file_documents_by_asset_and_path(
            "a1", "/file.txt", "bucket")
        assert deleted == 1
        assert client.delete.call_count == 1


@pytest.mark.unit
class TestArchivedAssetFileEvents:
    def test_archived_branch_uses_any_state_lookup(self, fileIndexer):
        """Asset-archive delete markers: file doc must be indexed archived even
        though the asset record moved to the #deleted partition."""
        from datetime import datetime
        fileIndexer.s3_client = MagicMock()
        fileIndexer.s3_client.list_object_versions.return_value = {
            "Versions": [{"Key": "a1/file.txt", "VersionId": "v1",
                          "LastModified": datetime(2026, 7, 4), "Size": 10, "ETag": '"e"'}],
            "DeleteMarkers": [{"Key": "a1/file.txt", "VersionId": "m1", "IsLatest": True}],
        }
        fileIndexer.s3_client.list_objects_v2.return_value = {"Contents": []}
        fileIndexer.s3_client.head_object.return_value = {
            "Metadata": {"databaseid": "db1", "assetid": "a1"}
        }
        # Live lookup misses; archived partition hits
        fileIndexer.get_asset_details_any_state = MagicMock(
            return_value=({"assetId": "a1", "bucketId": "b1", "assetName": "n",
                           "tags": [], "assetLocation": {"Key": "a1/"}}, True)
        )
        fileIndexer.get_bucket_details = MagicMock(return_value={
            "bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""
        })
        fileIndexer.get_file_metadata = MagicMock(return_value=({}, {}))
        fileIndexer.index_file_document = MagicMock(return_value=True)

        record = {
            "eventName": "ObjectRemoved:DeleteMarkerCreated",
            "s3": {"bucket": {"name": "bucket"}, "object": {"key": "a1/file.txt"}},
        }
        result = fileIndexer.handle_s3_notification(record)
        assert result.operation == "index"
        # Document built with archived flag
        doc = fileIndexer.index_file_document.call_args.args[0]
        assert doc.bool_archived is True
