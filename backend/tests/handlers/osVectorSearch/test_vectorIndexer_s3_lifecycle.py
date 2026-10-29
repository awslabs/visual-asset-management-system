# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S3 lifecycle records -> `isArchived` flips and deletes.

Created: the file is un-archived (a new live version is not archived by definition) and no latest mark
changes; the new version's whole-file write is the one demotion. DeleteMarkerCreated: archived. Delete with
versions remaining and no current marker (a marker was removed): un-archived, unconditionally. Delete with
nothing remaining: the file's items are removed, resolving the database through assetIdGSI + bucket when
the asset row is gone. Folder markers, reserved segments and `.previewFile.*` siblings are ignored. Every
rule reaches the store through the file's key-path prefix, so a segment item (`…#v1#t0000083456`) follows
its whole-file item through each transition; the seeded fake shows which items a call reaches (flags as the
bools `VectorItem` carries). The time-budget hand-off of these rules is covered in
`test_vectorIndexer_continuation.py`.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, SEGMENT_KEY, load_handler, seeded_item

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
    def test_new_version_unarchives_the_file(self, indexer):
        outcome = indexer.handle_s3_record(_record("ObjectCreated:Copy", version_id="v2"), "assets", "prefix-a/")
        assert outcome.ok and outcome.action == "created"
        assert indexer.vector_store.calls == [("set_archived_for_file", "db1:a1", KEY_PATH, False)]

    def test_url_encoded_key_is_decoded_before_resolution(self, indexer):
        indexer.handle_s3_record(_record("ObjectCreated:Put", key="prefix-a/a1/models/my+part%20x.glb"), "assets", "prefix-a/")
        indexer.s3_client.head_object.assert_called_once_with(Bucket="assets", Key="prefix-a/a1/models/my part x.glb")
        assert indexer.vector_store.calls[0][2] == "/models/my part x.glb"


def _whole_file_item(indexer, version_id):
    """A latest whole-file VectorItem of the fixture's file, as an embedding.ready write builds it."""
    return indexer.VectorItem(
        databaseId="db1", assetId="a1", filePath=KEY_PATH, versionId=version_id, isLatest=True,
        isArchived=False, fileClass="mesh", fileExt="glb", embeddingModelId=indexer.embedding_model_id,
        embeddingDimensions=indexer.embedding_dimensions, embedding=[0.1] * indexer.embedding_dimensions,
        sourceText="", sourceModalities=[], contentEtag="", bucketId="bucket-guid", fileSize=0,
        contentType="", analysisModelId="", indexedAt="2026-10-29T00:00:00+00:00",
        pipelineExecutionId="pe-2", workflowExecutionId="we-2")


@pytest.mark.unit
class TestNewVersionKeepsThePreviousLatestUntilItsItemIsWritten:
    """A new version's ObjectCreated record changes no latest mark: the previous version's items stay
    latest, so the file stays searchable while the new version is processed (and if its run writes no
    item at all). The new version's whole-file write is the one demotion."""

    def _seed(self, indexer):
        store = FakeVectorStore(seed=[
            seeded_item("db1:a1", f"{KEY_PATH}#v1", "v1", True, False),
            seeded_item("db1:a1", f"{KEY_PATH}#v1#{SEGMENT_KEY}", "v1", True, False, segment_kind="videoTime"),
        ])
        indexer.vector_store = store
        return store

    @staticmethod
    def _latest(store):
        return sorted(item["fileVersionKey"] for item in store.seeded if item["isLatest"])

    def test_the_previous_version_stays_latest_until_the_new_item_is_written(self, indexer):
        store = self._seed(indexer)
        outcome = indexer.handle_s3_record(_record("ObjectCreated:Put", version_id="v2"), "assets", "prefix-a/")
        assert outcome.ok and outcome.action == "created"
        assert self._latest(store) == [f"{KEY_PATH}#v1", f"{KEY_PATH}#v1#{SEGMENT_KEY}"], \
            "the ObjectCreated record must not demote the previous version before the new item exists"

        store.put_latest_item(_whole_file_item(indexer, "v2"))
        assert self._latest(store) == [f"{KEY_PATH}#v2"]

    def test_the_object_created_rule_only_unarchives(self, indexer):
        self._seed(indexer)
        indexer.handle_s3_record(_record("ObjectCreated:Put", version_id="v2"), "assets", "prefix-a/")
        assert indexer.vector_store.calls == [("set_archived_for_file", "db1:a1", KEY_PATH, False)]


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
        # The unseeded store holds no item of the file, so the restore also checks whether one exists.
        assert indexer.vector_store.calls == [("set_archived_for_file", "db1:a1", KEY_PATH, False),
                                              ("file_has_items", "db1:a1", KEY_PATH)]

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
    """Every per-file rule operates on `begins_with(SK, key_path + "#")`, so
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

    def test_a_new_version_leaves_the_segment_item_latest_and_unarchives_it_with_its_file(self, indexer):
        store = self._seed(indexer, is_archived=True)
        outcome = indexer.handle_s3_record(_record("ObjectCreated:Put", version_id="v2"), "assets", "prefix-a/")
        items = self._by_key(store)
        assert items[f"{KEY_PATH}#v1"]["isLatest"] is True
        assert items[f"{KEY_PATH}#v1#{SEGMENT_KEY}"]["isLatest"] is True
        assert items[f"{KEY_PATH}#v1#{SEGMENT_KEY}"]["isArchived"] is False
        assert items["/models/other.glb#v1"]["isArchived"] is True
        assert "unarchived=2" in outcome.detail

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


# The one INFO line a permanent delete of a file whose asset is also gone writes.
GONE_LINE = ("S3 record for assets/prefix-a/a1/models/part.glb: the object and its asset are gone, "
             "nothing to update")


@pytest.mark.unit
class TestUnattributedRecordLogging:
    """One log line per unattributed record, at a level that tells the expected case from a failure.

    A permanent asset delete removes the asset row and then every S3 version, so each file's ObjectRemoved
    record arrives with the object unreadable and no row in assetIdGSI. The asset-level rule has already
    removed the items, so that record is logged once at INFO. An assetId registered in two databases on the
    event's bucket and prefix, or a readable object no asset claims, stays a WARNING.
    """

    @pytest.fixture
    def log(self, indexer, monkeypatch):
        fake = MagicMock()
        monkeypatch.setattr(indexer, "logger", fake)
        assert indexer.handle_s3_record.__globals__ is vars(indexer)
        return fake

    @staticmethod
    def _messages(method):
        return [str(c.args[0]) for c in method.call_args_list]

    def test_object_and_asset_gone_after_a_permanent_delete_is_one_info_line(self, indexer, log):
        indexer.s3_client.head_object.side_effect = _not_found()
        indexer.asset_storage_table.get_item.side_effect = lambda **kw: {}
        indexer.asset_storage_table.query.return_value = {"Items": []}
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:Delete"), "assets", "prefix-a/")
        assert outcome.ok and outcome.action == "ignore"
        assert indexer.vector_store.calls == []
        assert indexer.asset_storage_table.query.call_args.kwargs["IndexName"] == "assetIdGSI"
        log.warning.assert_not_called()
        assert self._messages(log.info).count(GONE_LINE) == 1

    def test_readable_object_that_no_asset_claims_is_one_warning(self, indexer, log):
        indexer.s3_client.head_object.side_effect = None
        indexer.s3_client.head_object.return_value = {"VersionId": "v2", "Metadata": {}}
        indexer.asset_storage_table.get_item.side_effect = lambda **kw: {}
        indexer.asset_storage_table.query.return_value = {"Items": []}
        outcome = indexer.handle_s3_record(_record("ObjectCreated:Put"), "assets", "prefix-a/")
        assert outcome.ok and outcome.action == "ignore"
        assert indexer.vector_store.calls == []
        assert self._messages(log.warning) == [
            "S3 record for assets/prefix-a/a1/models/part.glb resolves to no asset; nothing to update"]
        assert GONE_LINE not in self._messages(log.info)

    def test_asset_id_in_two_databases_is_one_warning_naming_the_ambiguity(self, indexer, log):
        indexer.s3_client.head_object.side_effect = _not_found()
        indexer.asset_storage_table.get_item.side_effect = lambda **kw: {}
        indexer.asset_storage_table.query.return_value = {"Items": [
            {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-guid"},
            {"databaseId": "db2#deleted", "assetId": "a1", "bucketId": "bucket-guid"}]}
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:Delete"), "assets", "prefix-a/")
        assert outcome.ok and outcome.action == "ignore"
        assert indexer.vector_store.calls == []
        assert self._messages(log.warning) == [
            "assetId a1 resolves to 2 databases for bucket assets/prefix-a/; cannot attribute the file"]
        assert GONE_LINE not in self._messages(log.info)
