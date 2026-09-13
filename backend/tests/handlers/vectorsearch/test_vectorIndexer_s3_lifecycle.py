# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S3 lifecycle records -> `isLatest` / `isArchived` flips and deletes (spec §7.2, rows 2-5).

Created: every other item of the file becomes not-latest and the file is un-archived (a new live version
is not archived by definition). DeleteMarkerCreated: archived. Delete with versions remaining and no
current marker (a marker was removed): un-archived, unconditionally. Delete with nothing remaining: the
file's items are removed, resolving the database through assetIdGSI + bucket when the asset row is gone.
Folder markers, reserved segments and `.previewFile.*` siblings are ignored. Every rule reaches the store
through the file's key-path prefix, so a segment item (`…#v1#t0000083456`) follows its whole-file item
through each of the four transitions; the seeded fake shows which items a call reaches (flags as the bools
`VectorItem` carries). The time-budget hand-off of these rules is covered in
`test_vectorIndexer_continuation.py`.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.handlers.vectorsearch.vectorsearch_support import FakeVectorStore, SEGMENT_KEY, load_handler, seeded_item

KEY_PATH = "/models/part.glb"


def _not_found():
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"},
                        "ResponseMetadata": {"HTTPStatusCode": 404, "HTTPHeaders": {}}}, "HeadObject")


def _record(event_name, key="prefix-a/a1/models/part.glb", version_id="v2", bucket="assets"):
    return {"eventSource": "aws:s3", "eventName": event_name,
            "s3": {"bucket": {"name": bucket}, "object": {"key": key, "versionId": version_id}}}


@pytest.fixture
def indexer():
    m = load_handler("vectorIndexer")
    m.vector_store = FakeVectorStore()
    m.s3_client = MagicMock()
    m.s3_client.head_object.return_value = {"VersionId": "v2", "Metadata": {"databaseid": "db1", "assetid": "a1"}}
    m.asset_storage_table = MagicMock()
    m.asset_storage_table.get_item.side_effect = lambda **kw: (
        {"Item": {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-guid",
                  "assetLocation": {"Key": "prefix-a/a1/"}}}
        if kw["Key"]["databaseId"] == "db1" else {})
    m.s3_asset_buckets_table = MagicMock()
    m.s3_asset_buckets_table.query.return_value = {
        "Items": [{"bucketId": "bucket-guid", "bucketName": "assets", "baseAssetsPrefix": "prefix-a/"}]}
    return m


@pytest.mark.unit
class TestObjectCreated:
    def test_new_version_flips_other_items_and_unarchives_the_file(self, indexer):
        outcome = indexer.handle_s3_record(_record("ObjectCreated:Copy", version_id="v2"), "assets", "prefix-a/")
        assert outcome.ok and outcome.action == "created"
        assert indexer.vector_store.calls == [
            ("set_not_latest_for_file_except", "db1:a1", KEY_PATH, "v2"),
            ("set_archived_for_file", "db1:a1", KEY_PATH, False),
        ]

    def test_url_encoded_key_is_decoded_before_resolution(self, indexer):
        indexer.handle_s3_record(_record("ObjectCreated:Put", key="prefix-a/a1/models/my+part%20x.glb"), "assets", "prefix-a/")
        indexer.s3_client.head_object.assert_called_once_with(Bucket="assets", Key="prefix-a/a1/models/my part x.glb")
        assert indexer.vector_store.calls[0][2] == "/models/my part x.glb"

    def test_unversioned_bucket_uses_the_null_version_id(self, indexer):
        record = _record("ObjectCreated:Put")
        del record["s3"]["object"]["versionId"]
        indexer.handle_s3_record(record, "assets", "prefix-a/")
        assert indexer.vector_store.calls[0] == ("set_not_latest_for_file_except", "db1:a1", KEY_PATH, "null")


@pytest.mark.unit
class TestObjectRemoved:
    def test_delete_marker_created_archives_the_file(self, indexer):
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:DeleteMarkerCreated"), "assets", "prefix-a/")
        assert outcome.action == "archived"
        assert indexer.vector_store.calls == [("set_archived_for_file", "db1:a1", KEY_PATH, True)]

    def test_marker_removal_with_versions_remaining_unarchives_the_file(self, indexer):
        # The object is live again, so head_object succeeds; the ObjectRemoved event was the marker.
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:Delete", version_id="m1"), "assets", "prefix-a/")
        assert outcome.action == "unarchived"
        assert indexer.vector_store.calls == [("set_archived_for_file", "db1:a1", KEY_PATH, False)]

    def test_permanent_delete_removes_the_file_items_when_nothing_remains(self, indexer):
        indexer.s3_client.head_object.side_effect = _not_found()
        indexer.s3_client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
        # The object is gone, so the identity comes from the key and assetIdGSI. Without this row the
        # real `query_all_items` reads the fixture's bare MagicMock response as no rows and the record
        # is dropped as unresolved (`action == "ignore"`), not deleted.
        indexer.asset_storage_table.query.return_value = {"Items": [
            {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-guid"}]}
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:Delete"), "assets", "prefix-a/")
        assert outcome.action == "deleted"
        assert indexer.vector_store.calls == [("delete_file", "db1:a1", KEY_PATH)]

    def test_permanent_delete_resolves_the_database_through_assetidgsi_when_the_row_is_gone(self, indexer):
        indexer.s3_client.head_object.side_effect = _not_found()
        indexer.s3_client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
        indexer.asset_storage_table.get_item.side_effect = lambda **kw: {}
        indexer.asset_storage_table.query.return_value = {"Items": [
            {"databaseId": "other#deleted", "assetId": "a1", "bucketId": "other-bucket"},
            {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-guid"}]}
        indexer.s3_asset_buckets_table.query.side_effect = lambda **kw: {"Items": [
            {"bucketId": "bucket-guid", "bucketName": "assets", "baseAssetsPrefix": "prefix-a/"}
            if "bucket-guid" in str(kw["KeyConditionExpression"]._values) else
            {"bucketId": "other-bucket", "bucketName": "elsewhere", "baseAssetsPrefix": "/"}]}
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:Delete"), "assets", "prefix-a/")
        assert outcome.action == "deleted"
        assert indexer.vector_store.calls == [("delete_file", "db1:a1", KEY_PATH)]
        assert indexer.asset_storage_table.query.call_args.kwargs["IndexName"] == "assetIdGSI"

    def test_old_version_deleted_under_a_current_marker_changes_nothing(self, indexer):
        indexer.s3_client.head_object.side_effect = _not_found()
        indexer.s3_client.list_object_versions.return_value = {
            "Versions": [{"Key": "prefix-a/a1/models/part.glb", "VersionId": "v2", "IsLatest": False,
                          "LastModified": datetime(2026, 9, 1, tzinfo=timezone.utc)}],
            "DeleteMarkers": [{"Key": "prefix-a/a1/models/part.glb", "VersionId": "m1", "IsLatest": True}]}
        indexer.asset_storage_table.query.return_value = {"Items": [
            {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-guid"}]}
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:Delete", version_id="v1"), "assets", "prefix-a/")
        assert outcome.ok and outcome.action == "ignore"
        assert indexer.vector_store.calls == []


@pytest.mark.unit
class TestSegmentItemsFollowTheirFile:
    """Spec §7.2 closing sentence: every per-file rule operates on `begins_with(SK, key_path + "#")`, so
    the segment item `…#v1#t0000083456` moves with its whole-file item and another file's item does not."""

    def _seed(self, indexer, is_latest=True, is_archived=False):
        store = FakeVectorStore(seed=[
            seeded_item("db1:a1", f"{KEY_PATH}#v1", "v1", is_latest, is_archived),
            seeded_item("db1:a1", f"{KEY_PATH}#v1#{SEGMENT_KEY}", "v1", is_latest, is_archived,
                        segment_kind="videoTime"),
            seeded_item("db1:a1", "/models/other.glb#v1", "v1", is_latest, is_archived),
        ])
        indexer.vector_store = store
        return store

    @staticmethod
    def _by_key(store):
        return {item["fileVersionKey"]: item for item in store.seeded}

    def test_a_new_version_demotes_the_segment_item_with_its_file(self, indexer):
        store = self._seed(indexer)
        outcome = indexer.handle_s3_record(_record("ObjectCreated:Put", version_id="v2"), "assets", "prefix-a/")
        items = self._by_key(store)
        assert items[f"{KEY_PATH}#v1"]["isLatest"] is False
        assert items[f"{KEY_PATH}#v1#{SEGMENT_KEY}"]["isLatest"] is False
        assert items["/models/other.glb#v1"]["isLatest"] is True
        assert "notLatest=2" in outcome.detail

    def test_a_delete_marker_archives_the_segment_item_with_its_file(self, indexer):
        store = self._seed(indexer)
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:DeleteMarkerCreated"), "assets", "prefix-a/")
        items = self._by_key(store)
        assert items[f"{KEY_PATH}#v1"]["isArchived"] is True
        assert items[f"{KEY_PATH}#v1#{SEGMENT_KEY}"]["isArchived"] is True
        assert items["/models/other.glb#v1"]["isArchived"] is False
        assert "archived=2" in outcome.detail

    def test_a_marker_removal_unarchives_the_segment_item_with_its_file(self, indexer):
        store = self._seed(indexer, is_archived=True)
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:Delete", version_id="m1"), "assets", "prefix-a/")
        items = self._by_key(store)
        assert items[f"{KEY_PATH}#v1"]["isArchived"] is False
        assert items[f"{KEY_PATH}#v1#{SEGMENT_KEY}"]["isArchived"] is False
        assert items["/models/other.glb#v1"]["isArchived"] is True
        assert "unarchived=2" in outcome.detail

    def test_a_permanent_delete_removes_the_segment_item_with_its_file(self, indexer):
        store = self._seed(indexer)
        indexer.s3_client.head_object.side_effect = _not_found()
        indexer.s3_client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
        indexer.asset_storage_table.query.return_value = {"Items": [
            {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-guid"}]}
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:Delete"), "assets", "prefix-a/")
        assert sorted(self._by_key(store)) == ["/models/other.glb#v1"]
        assert "deleted=2" in outcome.detail


@pytest.mark.unit
class TestIgnoredKeys:
    @pytest.mark.parametrize("key", [
        "prefix-a/a1/models/",                                   # folder marker
        "prefix-a/pipelines/run-1/output/x.glb",                 # reserved segment
        "prefix-a/a1/models/part.glb.previewFile.png",           # preview sibling
    ])
    def test_system_keys_are_ignored_without_any_lookup(self, indexer, key):
        outcome = indexer.handle_s3_record(_record("ObjectCreated:Put", key=key), "assets", "prefix-a/")
        assert outcome.ok and outcome.action == "ignore"
        assert indexer.vector_store.calls == []
        indexer.s3_client.head_object.assert_not_called()

    def test_unresolvable_identity_is_dropped_with_no_write(self, indexer):
        indexer.s3_client.head_object.side_effect = _not_found()
        indexer.asset_storage_table.get_item.side_effect = lambda **kw: {}
        indexer.asset_storage_table.query.return_value = {"Items": []}
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:Delete"), "assets", "prefix-a/")
        assert outcome.ok and outcome.action == "ignore"
        assert indexer.vector_store.calls == []
