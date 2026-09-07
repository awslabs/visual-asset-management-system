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
            "Items": [{"databaseId": "db1#deleted", "assetId": "a1", "bucketId": "b1"}]
        }
        # The record has to be confirmed against the event's bucket before its
        # databaseId is usable; the bucket is registered at the root, which the event
        # spells '/' and the registration ''.
        fileIndexer.get_bucket_details = MagicMock(return_value={
            "bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""})
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
        # No versions and no markers -> permanent-delete branch as before.
        #
        # The record carries the bucketId the event came from, which is what makes its databaseId
        # usable: assetId is unique within a database, not across them, so a single assetIdGSI match
        # identifies the right database only once its registered bucket and baseAssetsPrefix agree
        # with the event's. See the bucketId-less arm below for the other half.
        fileIndexer.s3_client = MagicMock()
        fileIndexer.s3_client.list_object_versions.return_value = {
            "Versions": [], "DeleteMarkers": [],
        }
        fileIndexer.asset_storage_table = MagicMock()
        fileIndexer.asset_storage_table.query.return_value = {
            "Items": [{"databaseId": "db1", "assetId": "a1", "bucketId": "b-1"}]
        }
        fileIndexer.get_bucket_details = MagicMock(
            return_value={"bucketId": "b-1", "bucketName": "bucket", "baseAssetsPrefix": ""}
        )
        fileIndexer.delete_file_document = MagicMock(return_value=True)
        result = fileIndexer.handle_s3_notification(self._s3_record())
        assert result.operation == "delete"
        fileIndexer.delete_file_document.assert_called_once_with("db1", "a1", "/file.txt")

    def test_a_record_without_a_bucket_id_is_not_deleted_by_exact_id(self, fileIndexer):
        """The paired arm: an unverifiable record must not authorize an exact-_id delete.

        `bucketId` is optional on the asset record (`assetsV3.py` declares it `str = None`, and
        `assetService.py` carries an explicit `if not item.get('bucketId')` branch), so records
        without one exist. Since `assetId` is not unique across databases, such a record cannot be
        shown to belong to the bucket the event came from -- and using it anyway is what deleted
        another database's live document. The exact-_id delete is therefore skipped and the caller
        falls through to the prefix-scoped orphan cleanup, which is bounded by the event's own key.
        """
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

        assert result.operation == "skip"
        fileIndexer.delete_file_document.assert_not_called()


@pytest.mark.unit
class TestSplitAssetKey:
    """Object keys are ``{baseAssetsPrefix}{assetId}/{filePath}``. The prefix has to come
    off before the first remaining component is the asset ID — the property that fails for
    every bucket registered below the root."""

    def test_root_bucket(self, fileIndexer):
        assert fileIndexer.split_asset_key("a1/file.txt", "/") == ("a1", "/file.txt")

    def test_root_bucket_empty_and_none_prefix(self, fileIndexer):
        assert fileIndexer.split_asset_key("a1/f.txt", "") == ("a1", "/f.txt")
        assert fileIndexer.split_asset_key("a1/f.txt", None) == ("a1", "/f.txt")

    def test_non_root_prefix_is_removed_first(self, fileIndexer):
        # Splitting the raw key would give ("prefix-a", "/a1/folder/file.txt").
        assert fileIndexer.split_asset_key(
            "prefix-a/a1/folder/file.txt", "prefix-a/") == ("a1", "/folder/file.txt")

    def test_prefix_accepted_in_any_registered_form(self, fileIndexer):
        for prefix in ("prefix-a", "prefix-a/", "/prefix-a", "/prefix-a/"):
            assert fileIndexer.split_asset_key("prefix-a/a1/f.txt", prefix) == ("a1", "/f.txt")

    def test_multi_segment_prefix(self, fileIndexer):
        assert fileIndexer.split_asset_key(
            "team/vams/a1/f.txt", "team/vams/") == ("a1", "/f.txt")

    def test_key_with_no_path_below_the_asset_folder(self, fileIndexer):
        assert fileIndexer.split_asset_key("a1", "/") == (None, None)
        assert fileIndexer.split_asset_key("prefix-a/a1", "prefix-a/") == (None, None)

    def test_prefix_that_does_not_fit_the_key_is_not_applied(self, fileIndexer):
        # Never strip a prefix the key does not carry -- that would eat real path segments.
        assert fileIndexer.split_asset_key("a1/f.txt", "other/") == ("a1", "/f.txt")


@pytest.mark.unit
class TestResolveRegisteredBucketPrefix:
    """Which prefix an object sits under must be resolved, never assumed to be the root:
    the root is also what an absent value defaults to, and treating a non-root bucket as
    root reads the prefix's first segment as the asset ID."""

    def _buckets_table(self, fileIndexer, *prefixes):
        fileIndexer.s3_asset_buckets_table = MagicMock()
        fileIndexer.s3_asset_buckets_table.query.return_value = {
            "Items": [{"bucketName": "bucket", "baseAssetsPrefix": p} for p in prefixes]
        }
        return fileIndexer.s3_asset_buckets_table

    def test_non_root_event_prefix_that_fits_is_taken_without_a_lookup(self, fileIndexer):
        table = self._buckets_table(fileIndexer, "/")
        assert fileIndexer.resolve_registered_bucket_prefix(
            "bucket", "prefix-a/a1/f.txt", "prefix-a/") == "prefix-a/"
        table.query.assert_not_called()

    def test_absent_prefix_recovered_from_the_registration(self, fileIndexer):
        self._buckets_table(fileIndexer, "prefix-a/")
        assert fileIndexer.resolve_registered_bucket_prefix(
            "bucket", "prefix-a/a1/f.txt", None) == "prefix-a/"

    def test_root_default_does_not_mask_a_non_root_registration(self, fileIndexer):
        """The '/' every other read of this field defaults to is indistinguishable from a
        genuine root registration, so it is re-resolved rather than trusted."""
        self._buckets_table(fileIndexer, "prefix-a/")
        assert fileIndexer.resolve_registered_bucket_prefix(
            "bucket", "prefix-a/a1/f.txt", "/") == "prefix-a/"

    def test_root_registration_resolves_to_the_empty_stored_form(self, fileIndexer):
        self._buckets_table(fileIndexer, "/")
        assert fileIndexer.resolve_registered_bucket_prefix(
            "bucket", "a1/f.txt", None) == ""

    def test_unregistered_bucket_stays_unknown(self, fileIndexer):
        self._buckets_table(fileIndexer)  # no records
        assert fileIndexer.resolve_registered_bucket_prefix(
            "bucket", "a1/f.txt", None) is None

    def test_picks_the_registration_the_key_sits_under(self, fileIndexer):
        # One bucket may carry several non-overlapping registrations (getConfig rejects
        # overlap, and the root overlaps everything), so at most one can match a key.
        self._buckets_table(fileIndexer, "prefix-a/", "prefix-b/")
        assert fileIndexer.resolve_registered_bucket_prefix(
            "bucket", "prefix-b/a1/f.txt", None) == "prefix-b/"

    def test_lookup_is_cached_per_bucket(self, fileIndexer):
        table = self._buckets_table(fileIndexer, "prefix-a/")
        for _ in range(3):
            fileIndexer.resolve_registered_bucket_prefix("bucket", "prefix-a/a1/f.txt", None)
        assert table.query.call_count == 1

    def test_lookup_failure_falls_back_to_the_event_value(self, fileIndexer):
        fileIndexer.s3_asset_buckets_table = MagicMock()
        fileIndexer.s3_asset_buckets_table.query.side_effect = RuntimeError("throttled")
        assert fileIndexer.resolve_registered_bucket_prefix(
            "bucket", "a1/f.txt", "/") == ""


@pytest.mark.unit
class TestOrphanCleanupFallback:
    def _permanently_deleted(self, fileIndexer, *, registered_prefixes=("/",)):
        """Drive the permanent-delete branch: no versions and no markers remain, and the
        asset record is gone from both partitions."""
        fileIndexer.s3_client = MagicMock()
        fileIndexer.s3_client.list_object_versions.return_value = {
            "Versions": [], "DeleteMarkers": [],
        }
        fileIndexer.asset_storage_table = MagicMock()
        fileIndexer.asset_storage_table.query.return_value = {"Items": []}
        fileIndexer.s3_asset_buckets_table = MagicMock()
        fileIndexer.s3_asset_buckets_table.query.return_value = {
            "Items": [{"bucketName": "bucket", "baseAssetsPrefix": p}
                      for p in registered_prefixes]
        }
        fileIndexer.delete_file_documents_by_asset_and_path = MagicMock(return_value=1)

    def _cleanup_args(self, fileIndexer):
        return [c.args for c in
                fileIndexer.delete_file_documents_by_asset_and_path.call_args_list]

    def test_orphan_docs_deleted_when_record_gone(self, fileIndexer):
        self._permanently_deleted(fileIndexer, registered_prefixes=("prefix-a/",))
        record = {
            "eventName": "ObjectRemoved:Delete",
            "s3": {"bucket": {"name": "bucket"},
                   "object": {"key": "prefix-a/a1/file.txt"}},
            "ASSET_BUCKET_NAME": "bucket",
            "ASSET_BUCKET_PREFIX": "prefix-a/",
        }
        result = fileIndexer.handle_s3_notification(record)
        assert result.operation == "delete"
        # The event's bucket prefix must reach the cleanup so the match can be
        # scoped to one database. Asserted as a property of the call arguments,
        # not as a call count.
        assert ("a1", "/file.txt", "bucket", "prefix-a/") in self._cleanup_args(fileIndexer)

    def test_orphan_cleanup_runs_when_the_record_carries_no_prefix(self, fileIndexer):
        """The production shape: a delete record with NO `ASSET_BUCKET_PREFIX` key at all.

        This is the gap that let a real defect through three tests written for this exact
        scenario. `test_orphan_docs_deleted_when_record_gone` above supplies the field
        explicitly, and the two `delete_file_documents_by_asset_and_path` tests call that
        function directly — so nothing exercised the CALL SITE with the field absent, which is
        how records actually arrive when the prefix was not propagated through the SNS/SQS
        nesting.

        Read without a default the value is `None`, which the cleanup treats as "prefix
        unknown" and refuses to run unscoped: the orphan cleanup could never delete anything,
        and a permanently deleted asset's file documents stayed in the index indefinitely.
        Verified live on a deployment — the document was still searchable five minutes after
        the asset was permanently deleted, with the indexer logging "Skipping orphan
        file-document cleanup ... the event carries no bucket prefix".

        The prefix is recovered from the bucket's own registration records rather than
        defaulted to the root: the registration outlives the asset, and a root default is
        wrong for every bucket registered below the root.
        """
        self._permanently_deleted(fileIndexer)  # registered at the root
        record = {
            "eventName": "ObjectRemoved:Delete",
            "s3": {"bucket": {"name": "bucket"}, "object": {"key": "a1/file.txt"}},
            "ASSET_BUCKET_NAME": "bucket",
            # No ASSET_BUCKET_PREFIX -- deliberately absent.
        }
        result = fileIndexer.handle_s3_notification(record)
        assert result.operation == "delete"

        arg_sets = self._cleanup_args(fileIndexer)
        assert arg_sets, "the orphan cleanup was never called at all"
        prefixes = [args[3] for args in arg_sets]
        assert None not in prefixes, (
            f"the cleanup was handed None for the bucket prefix, which makes it decline and "
            f"leave the document in the index: {arg_sets}"
        )
        # A bucket rooted at '/' is indexed with an empty str_bucketprefix.
        assert ("a1", "/file.txt", "bucket", "") in arg_sets, arg_sets

    def test_non_root_bucket_with_no_prefix_on_the_record(self, fileIndexer):
        """A bucket registered below the root, with the field absent — the case where
        defaulting to '/' searched for asset "prefix-a" and path "/a1/file.txt", matching
        no document and leaving the orphan in the index with a "deleted 0" success."""
        self._permanently_deleted(fileIndexer, registered_prefixes=("prefix-a/",))
        record = {
            "eventName": "ObjectRemoved:Delete",
            "s3": {"bucket": {"name": "bucket"},
                   "object": {"key": "prefix-a/a1/folder/file.txt"}},
            "ASSET_BUCKET_NAME": "bucket",
        }
        result = fileIndexer.handle_s3_notification(record)
        assert result.operation == "delete"
        assert ("a1", "/folder/file.txt", "bucket", "prefix-a/") in self._cleanup_args(fileIndexer)

    def test_unregistered_bucket_declines_rather_than_assuming_root(self, fileIndexer):
        """With no registration to resolve, the prefix is genuinely unknown and the
        cleanup must decline rather than search unscoped across every database."""
        self._permanently_deleted(fileIndexer, registered_prefixes=())
        record = {
            "eventName": "ObjectRemoved:Delete",
            "s3": {"bucket": {"name": "bucket"}, "object": {"key": "a1/file.txt"}},
            "ASSET_BUCKET_NAME": "bucket",
        }
        fileIndexer.delete_file_documents_by_asset_and_path = MagicMock(return_value=0)
        result = fileIndexer.handle_s3_notification(record)
        assert result.operation == "skip"
        prefixes = [args[3] for args in self._cleanup_args(fileIndexer)]
        assert prefixes == [None], prefixes

    def test_non_root_resolvable_asset_deletes_the_right_document(self, fileIndexer):
        """The lookup_success branch: the asset ID handed to the database lookup, and the
        path handed to the delete, are both below the registered prefix."""
        fileIndexer.s3_client = MagicMock()
        fileIndexer.s3_client.list_object_versions.return_value = {
            "Versions": [], "DeleteMarkers": [],
        }
        fileIndexer.s3_asset_buckets_table = MagicMock()
        fileIndexer.s3_asset_buckets_table.query.return_value = {
            "Items": [{"bucketName": "bucket", "baseAssetsPrefix": "prefix-a/"}]
        }
        fileIndexer.lookup_database_id_for_permanent_delete = MagicMock(
            return_value=("db1", True))
        fileIndexer.delete_file_document = MagicMock(return_value=True)

        record = {
            "eventName": "ObjectRemoved:Delete",
            "s3": {"bucket": {"name": "bucket"},
                   "object": {"key": "prefix-a/a1/folder/file.txt"}},
            "ASSET_BUCKET_NAME": "bucket",
        }
        result = fileIndexer.handle_s3_notification(record)
        assert result.operation == "delete"
        fileIndexer.delete_file_document.assert_called_once_with(
            "db1", "a1", "/folder/file.txt")
        assert fileIndexer.lookup_database_id_for_permanent_delete.call_args.args == (
            "a1", "bucket", "prefix-a/")

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
            "a1", "/file.txt", "bucket", "prefix-a/")
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
            "a1", "/file.txt", "bucket", "prefix-a/")
        assert deleted == 1
        assert client.delete.call_count == 1

    def test_orphan_query_is_scoped_to_the_event_bucket_prefix(self, fileIndexer):
        """S2-BACKEND-096: str_key is asset-relative, so asset+path+bucket-name
        also matches another database's live document on the same bucket under a
        different baseAssetsPrefix. The prefix must be part of the filter."""
        client = MagicMock()
        client.search.return_value = {"hits": {"hits": []}}
        fileIndexer.opensearch_manager = MagicMock()
        fileIndexer.opensearch_manager.is_available.return_value = True
        fileIndexer.opensearch_manager.get_client.return_value = client

        fileIndexer.delete_file_documents_by_asset_and_path(
            "a1", "/file.txt", "bucket", "prefix-a")

        body = client.search.call_args.kwargs["body"]
        filters = body["query"]["bool"]["filter"]
        # A trailing slash is added and a leading one removed, matching the form
        # get_bucket_details stores on the document.
        assert {"term": {"str_bucketprefix.keyword": "prefix-a/"}} in filters
        assert {"term": {"str_assetid.keyword": "a1"}} in filters
        assert {"term": {"str_key.keyword": "/file.txt"}} in filters
        assert {"term": {"str_bucketname.keyword": "bucket"}} in filters

    def test_orphan_query_root_prefix_matches_stored_empty_string(self, fileIndexer):
        """A bucket rooted at '/' is indexed with an empty str_bucketprefix."""
        client = MagicMock()
        client.search.return_value = {"hits": {"hits": []}}
        fileIndexer.opensearch_manager = MagicMock()
        fileIndexer.opensearch_manager.is_available.return_value = True
        fileIndexer.opensearch_manager.get_client.return_value = client

        fileIndexer.delete_file_documents_by_asset_and_path(
            "a1", "/file.txt", "bucket", "/")

        filters = client.search.call_args.kwargs["body"]["query"]["bool"]["filter"]
        assert {"term": {"str_bucketprefix.keyword": ""}} in filters

    def test_orphan_cleanup_skipped_when_prefix_unknown(self, fileIndexer):
        """Positive control for the two tests above: with no prefix the cleanup
        must not run unscoped -- no search, no delete, nothing removed."""
        client = MagicMock()
        client.search.return_value = {"hits": {"hits": [{"_id": "d1"}]}}
        fileIndexer.opensearch_manager = MagicMock()
        fileIndexer.opensearch_manager.is_available.return_value = True
        fileIndexer.opensearch_manager.get_client.return_value = client

        deleted = fileIndexer.delete_file_documents_by_asset_and_path(
            "a1", "/file.txt", "bucket", None)

        assert deleted == 0
        client.search.assert_not_called()
        client.delete.assert_not_called()

    @pytest.mark.parametrize("deleted_prefix,expected_id", [
        ("prefix-a/", "dbA#a1#/file.txt"),
        ("prefix-b/", "dbB#a1#/file.txt"),
    ])
    def test_only_the_matching_prefixes_document_is_removed(
            self, fileIndexer, deleted_prefix, expected_id):
        """S2-BACKEND-096 as behaviour rather than as a query shape.

        Two databases can be backed by one bucket under different
        baseAssetsPrefix values and each hold an asset with the same assetId
        (uniqueness is enforced only per database), and str_key is asset-relative
        -- so both documents share every other filter term. Only the document
        under the deleted key's prefix may go; the other database's file is still
        live in S3.

        Both arms are also the positive control: the document that SHOULD be
        cleaned up is still cleaned up, so scoping the filter did not turn the
        orphan cleanup into a no-op.
        """
        docs = {
            "dbA#a1#/file.txt": {"str_assetid": "a1", "str_key": "/file.txt",
                                 "str_bucketname": "bucket",
                                 "str_bucketprefix": "prefix-a/"},
            "dbB#a1#/file.txt": {"str_assetid": "a1", "str_key": "/file.txt",
                                 "str_bucketname": "bucket",
                                 "str_bucketprefix": "prefix-b/"},
        }
        deleted_ids = set()

        def search(index, body):
            # Match the way OpenSearch would, so the assertion is about which
            # documents the filter selects rather than about the terms present.
            terms = {}
            for clause in body["query"]["bool"]["filter"]:
                (field, value), = clause["term"].items()
                terms[field.replace(".keyword", "")] = value
            return {"hits": {"hits": [
                {"_id": doc_id} for doc_id, doc in docs.items()
                if doc_id not in deleted_ids
                and all(doc.get(f) == v for f, v in terms.items())
            ]}}

        client = MagicMock()
        client.search.side_effect = search
        client.delete.side_effect = (
            lambda index, id, ignore=None: deleted_ids.add(id))
        fileIndexer.opensearch_manager = MagicMock()
        fileIndexer.opensearch_manager.is_available.return_value = True
        fileIndexer.opensearch_manager.get_client.return_value = client

        deleted = fileIndexer.delete_file_documents_by_asset_and_path(
            "a1", "/file.txt", "bucket", deleted_prefix)

        assert deleted_ids == {expected_id}, (
            f"the cleanup removed {deleted_ids}; a document belonging to another "
            f"database on the same bucket is still live in S3"
        )
        assert deleted == 1


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
