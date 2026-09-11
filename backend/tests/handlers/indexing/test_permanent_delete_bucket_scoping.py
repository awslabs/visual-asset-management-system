# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket scoping of the permanent-delete database lookup.

`lookup_database_id_for_permanent_delete` resolves which database a permanently
deleted S3 object belonged to, and the caller then deletes that database's file
document by exact `_id`. `assetId` is unique within a database but not across
databases, so a resolution that does not confirm the record's registered bucket
and `baseAssetsPrefix` against the event's addresses another database's LIVE
document.

Every case here pairs both directions: a record that disagrees with the event
must not resolve, and a record that agrees must still resolve. The negative
direction alone also passes on an implementation that resolves nothing at all.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}

_FILE_INDEXER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing",
    "fileIndexer.py"
)


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


@pytest.fixture
def fileIndexer():
    """Load the real fileIndexer by path with boto3 stubbed.

    Loaded by path rather than reloaded through `sys.path` so the module under test
    is this worktree's file and nothing else; `test_module_is_the_worktree_file`
    asserts that in band.
    """
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
                "fileIndexer_delete_scoping_under_test", os.path.abspath(_FILE_INDEXER_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


class _AssetTable:
    """Serves one page of `assetIdGSI` items and counts its own reads."""

    def __init__(self, items):
        self._items = list(items)
        self.calls = 0

    def query(self, **kwargs):
        self.calls += 1
        # No LastEvaluatedKey: the walk ends on the key's ABSENCE.
        return {"Items": list(self._items)}

    def get_item(self, **kwargs):
        # The permanent-delete path resolves the database from the GSI, never from a
        # keyed read: the record it would read is already gone.
        return {}


class _BucketRegistry:
    """`s3_asset_buckets_table` stub routing the two distinct reads apart.

    `get_bucket_details` queries by `bucketId` with no IndexName;
    `get_registered_bucket_prefixes` queries the `bucketNameGSI`. One shared return
    value would answer both, so a lookup that consulted the wrong one would still
    look right. Each read counts separately.
    """

    def __init__(self, by_bucket_id, by_bucket_name=None):
        self._by_bucket_id = by_bucket_id
        self._by_bucket_name = by_bucket_name or {}
        self.bucket_id_reads = []
        self.bucket_name_reads = []

    def query(self, **kwargs):
        value = kwargs["KeyConditionExpression"].get_expression()["values"][1]
        if kwargs.get("IndexName") == "bucketNameGSI":
            self.bucket_name_reads.append(value)
            return {"Items": list(self._by_bucket_name.get(value, []))}
        self.bucket_id_reads.append(value)
        record = self._by_bucket_id.get(value)
        return {"Items": [record] if record else []}


def _registration(bucket_name, prefix):
    return {"bucketName": bucket_name, "baseAssetsPrefix": prefix}


@pytest.mark.unit
def test_module_is_the_worktree_file(fileIndexer):
    assert fileIndexer.__file__ == os.path.abspath(_FILE_INDEXER_PATH), fileIndexer.__file__


@pytest.mark.unit
class TestSingleMatchIsScopedToTheEventBucket:
    """Step 1: one `assetIdGSI` item is only usable once it is confirmed to be the
    event's."""

    def _lookup(self, fileIndexer, items, registrations, bucket_name, bucket_prefix):
        asset_table = _AssetTable(items)
        registry = _BucketRegistry(registrations)
        with patch.object(fileIndexer, "asset_storage_table", asset_table), \
                patch.object(fileIndexer, "s3_asset_buckets_table", registry):
            result = fileIndexer.lookup_database_id_for_permanent_delete(
                "a1", bucket_name, bucket_prefix)
        return result, asset_table, registry

    def test_cross_database_single_match_does_not_resolve(self, fileIndexer):
        """The defect: database B still holds an asset with the deleted asset's ID, so
        the GSI returns exactly one item — B's — and an unscoped step 1 hands the caller
        B's live database to delete by exact _id."""
        result, asset_table, registry = self._lookup(
            fileIndexer,
            [{"assetId": "a1", "databaseId": "dbB", "bucketId": "b-B"}],
            {"b-B": _registration("bucket-B", "")},
            "bucket-A", "",
        )
        assert result == (None, False), (
            f"resolved {result} from a record registered in bucket-B while the event came "
            "from bucket-A; the caller deletes that database's live document by exact _id")
        assert asset_table.calls == 1
        assert registry.bucket_id_reads == ["b-B"], (
            "the single-match branch never read the record's bucket registration, so its "
            "agreement with the event was never checked")

    def test_matching_single_match_resolves(self, fileIndexer):
        """Positive control. Without it the negative above also passes on a lookup that
        resolves nothing. The event spells the root prefix `/`, the registration `''`."""
        result, _, registry = self._lookup(
            fileIndexer,
            [{"assetId": "a1", "databaseId": "dbA", "bucketId": "b-A"}],
            {"b-A": _registration("bucket-A", "")},
            "bucket-A", "/",
        )
        assert result == ("dbA", True)
        assert registry.bucket_id_reads == ["b-A"]

    def test_matching_single_match_below_the_root_resolves(self, fileIndexer):
        result, _, _ = self._lookup(
            fileIndexer,
            [{"assetId": "a1", "databaseId": "dbA", "bucketId": "b-A"}],
            {"b-A": _registration("bucket-A", "prefix-a/")},
            "bucket-A", "prefix-a/",
        )
        assert result == ("dbA", True)

    def test_archived_partition_record_still_strips_the_suffix(self, fileIndexer):
        """Document IDs always carry the live databaseId, so the `#deleted` suffix comes
        off — the bucket check must not cost that."""
        result, _, _ = self._lookup(
            fileIndexer,
            [{"assetId": "a1", "databaseId": "dbA#deleted", "bucketId": "b-A"}],
            {"b-A": _registration("bucket-A", "")},
            "bucket-A", "/",
        )
        assert result == ("dbA", True)

    def test_same_bucket_different_prefix_does_not_resolve(self, fileIndexer):
        """Two databases share the physical bucket and differ only in prefix, so the
        prefix alone is what separates them."""
        result, _, _ = self._lookup(
            fileIndexer,
            [{"assetId": "a1", "databaseId": "dbB", "bucketId": "b-B"}],
            {"b-B": _registration("shared", "prefix-b/")},
            "shared", "prefix-a/",
        )
        assert result == (None, False)

    def test_record_without_bucket_id_does_not_resolve(self, fileIndexer):
        """Unverifiable, so treated the way step 2 has always treated it: skipped."""
        result, _, registry = self._lookup(
            fileIndexer,
            [{"assetId": "a1", "databaseId": "dbA"}],
            {},
            "bucket-A", "/",
        )
        assert result == (None, False)
        assert registry.bucket_id_reads == []

    def test_unresolvable_bucket_id_does_not_resolve(self, fileIndexer):
        """The registration the record points at is gone, so the record's bucket and
        prefix are unknown."""
        result, _, registry = self._lookup(
            fileIndexer,
            [{"assetId": "a1", "databaseId": "dbA", "bucketId": "b-missing"}],
            {},
            "bucket-A", "/",
        )
        assert result == (None, False)
        assert registry.bucket_id_reads == ["b-missing"]

    def test_root_default_event_prefix_against_a_non_root_record_does_not_resolve(
            self, fileIndexer):
        """The caller substitutes `/` when it could not resolve the event's prefix, which
        is indistinguishable from a bucket genuinely rooted at `/`. Against a record
        registered below the root that is a disagreement, not a match."""
        result, _, _ = self._lookup(
            fileIndexer,
            [{"assetId": "a1", "databaseId": "dbA", "bucketId": "b-A"}],
            {"b-A": _registration("bucket-A", "assets/")},
            "bucket-A", "/",
        )
        assert result == (None, False)

    def test_unknown_prefix_on_both_sides_is_not_agreement(self, fileIndexer):
        """`normalize_bucket_prefix(None)` is None, so a bare equality check would read
        two unknowns as a match and resolve on no evidence at all."""
        result, _, registry = self._lookup(
            fileIndexer,
            [{"assetId": "a1", "databaseId": "dbA", "bucketId": "b-A"}],
            {"b-A": {"bucketName": "bucket-A"}},
            "bucket-A", None,
        )
        assert result == (None, False)
        assert registry.bucket_id_reads == [], (
            "an unresolved event prefix is unverifiable before any record is read")


@pytest.mark.unit
class TestMultipleMatchesStillFilterByBucket:
    """Step 2 behaviour is unchanged: the two prefix spellings it compared inline
    (`/prefix-a/`) and the shared helper's (`prefix-a/`) differ in spelling but agree on
    every equivalence, so no match outcome moves."""

    def _lookup(self, fileIndexer, items, registrations, bucket_name, bucket_prefix):
        with patch.object(fileIndexer, "asset_storage_table", _AssetTable(items)), \
                patch.object(fileIndexer, "s3_asset_buckets_table",
                             _BucketRegistry(registrations)):
            return fileIndexer.lookup_database_id_for_permanent_delete(
                "a1", bucket_name, bucket_prefix)

    _TWO_DATABASES = [
        {"assetId": "a1", "databaseId": "dbA", "bucketId": "b-A"},
        {"assetId": "a1", "databaseId": "dbB", "bucketId": "b-B"},
    ]

    def test_bucket_name_picks_the_right_database(self, fileIndexer):
        assert self._lookup(
            fileIndexer, self._TWO_DATABASES,
            {"b-A": _registration("bucket-A", ""), "b-B": _registration("bucket-B", "")},
            "bucket-B", "/",
        ) == ("dbB", True)

    def test_prefix_picks_the_right_database_in_a_shared_bucket(self, fileIndexer):
        assert self._lookup(
            fileIndexer, self._TWO_DATABASES,
            {"b-A": _registration("shared", "db-a/"), "b-B": _registration("shared", "db-b/")},
            "shared", "db-b/",
        ) == ("dbB", True)

    def test_no_candidate_matches_the_event_bucket(self, fileIndexer):
        assert self._lookup(
            fileIndexer, self._TWO_DATABASES,
            {"b-A": _registration("bucket-A", ""), "b-B": _registration("bucket-B", "")},
            "unrelated", "/",
        ) == (None, False)

    def test_two_candidates_in_the_same_bucket_stay_ambiguous(self, fileIndexer):
        assert self._lookup(
            fileIndexer, self._TWO_DATABASES,
            {"b-A": _registration("shared", ""), "b-B": _registration("shared", "")},
            "shared", "/",
        ) == (None, False)


@pytest.mark.unit
class TestCallerFallsThroughToPrefixScopedCleanup:
    """The whole point of refusing: the caller must reach the prefix-scoped orphan
    cleanup instead of deleting a document by exact `_id`."""

    def _permanent_delete_event(self, fileIndexer, asset_items, registrations,
                                registered_prefixes):
        fileIndexer.s3_client = MagicMock()
        fileIndexer.s3_client.list_object_versions.return_value = {
            "Versions": [], "DeleteMarkers": [],
        }
        fileIndexer.asset_storage_table = _AssetTable(asset_items)
        fileIndexer.s3_asset_buckets_table = _BucketRegistry(
            registrations,
            {"bucket-A": [_registration("bucket-A", p) for p in registered_prefixes]},
        )
        fileIndexer.delete_file_document = MagicMock(return_value=True)
        fileIndexer.delete_file_documents_by_asset_and_path = MagicMock(return_value=1)
        return {
            "eventName": "ObjectRemoved:Delete",
            "s3": {"bucket": {"name": "bucket-A"}, "object": {"key": "a1/file.txt"}},
            "ASSET_BUCKET_NAME": "bucket-A",
            "ASSET_BUCKET_PREFIX": "/",
        }

    def test_cross_database_match_deletes_nothing_by_id(self, fileIndexer):
        record = self._permanent_delete_event(
            fileIndexer,
            [{"assetId": "a1", "databaseId": "dbB", "bucketId": "b-B"}],
            {"b-B": _registration("bucket-B", "")},
            registered_prefixes=("/",),
        )
        result = fileIndexer.handle_s3_notification(record)

        assert fileIndexer.delete_file_document.call_count == 0, (
            "another database's live document was deleted by exact _id: "
            f"{fileIndexer.delete_file_document.call_args_list}")
        assert [c.args for c in
                fileIndexer.delete_file_documents_by_asset_and_path.call_args_list] == [
            ("a1", "/file.txt", "bucket-A", "")], (
            "the prefix-scoped orphan cleanup did not run, so the event's own document "
            "is left in the index")
        assert result.operation == "delete"

    def test_own_database_match_still_deletes_by_id(self, fileIndexer):
        """Must-still-work: the ordinary single-database permanent delete resolves and
        removes its document by exact _id, taking no cleanup fallback."""
        record = self._permanent_delete_event(
            fileIndexer,
            [{"assetId": "a1", "databaseId": "dbA", "bucketId": "b-A"}],
            {"b-A": _registration("bucket-A", "")},
            registered_prefixes=("/",),
        )
        result = fileIndexer.handle_s3_notification(record)

        fileIndexer.delete_file_document.assert_called_once_with("dbA", "a1", "/file.txt")
        assert fileIndexer.delete_file_documents_by_asset_and_path.call_count == 0
        assert result.operation == "delete"
