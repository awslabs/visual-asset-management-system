# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset table stream records -> asset-wide archive flag or delete.

Archive moves the row to `{db}#deleted` (INSERT there, REMOVE live); unarchive moves it back (INSERT
live, REMOVE `#deleted`) and by default leaves the files' delete markers in place, so the vector items
keep `isArchived="true"` until each marker removal arrives as its own S3 record; permanent delete removes
both rows. Metadata, attribute and link stream records (incl. the REINDEX_METADATA_RECORD marker) are
ignored. The asset-wide rules reach the whole partition, so a segment item moves with its asset (the seeded
fake's flags are the bools `VectorItem` carries). The time-budget hand-off of these rules is covered in
`test_vectorIndexer_continuation.py`.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.vectorsearch.vectorsearch_support import FakeVectorStore, SEGMENT_KEY, load_handler, seeded_item


@pytest.fixture
def indexer():
    m = load_handler("vectorIndexer")
    m.vector_store = FakeVectorStore()
    m.asset_storage_table = MagicMock()
    return m


def _record(module, event_name, database_key, asset_id="a1", table=None, image=True):
    table_name = table or module.asset_storage_table_name
    keys = {"databaseId": {"S": database_key}, "assetId": {"S": asset_id}}
    record = {"eventSourceARN": f"arn:aws:dynamodb:us-east-1:1:table/{table_name}/stream/x",
              "eventName": event_name, "dynamodb": {"Keys": keys}}
    if image and event_name != "REMOVE":
        record["dynamodb"]["NewImage"] = {**keys, "assetName": {"S": "Part"}}
    return record


def _rows(indexer, live=None, archived=None):
    def get_item(**kw):
        key = kw["Key"]["databaseId"]
        if key.endswith("#deleted"):
            return {"Item": archived} if archived else {}
        return {"Item": live} if live else {}
    indexer.asset_storage_table.get_item.side_effect = get_item


@pytest.mark.unit
class TestArchive:
    def test_insert_into_the_deleted_partition_archives_every_item_of_the_asset(self, indexer):
        outcome = indexer.handle_stream_record(_record(indexer, "INSERT", "db1#deleted"))
        assert outcome.action == "asset-archived"
        assert indexer.vector_store.calls == [("set_archived_for_asset", "db1:a1", True)]

    def test_remove_of_the_live_row_while_the_archived_row_exists_archives(self, indexer):
        _rows(indexer, live=None, archived={"databaseId": "db1#deleted", "assetId": "a1"})
        outcome = indexer.handle_stream_record(_record(indexer, "REMOVE", "db1"))
        assert outcome.action == "asset-archived"
        assert indexer.vector_store.calls == [("set_archived_for_asset", "db1:a1", True)]
        # Consistent reads: the archive's two writes are seconds apart and the flag must not lag them.
        reads = indexer.asset_storage_table.get_item.call_args_list
        assert reads
        assert all(call.kwargs["ConsistentRead"] is True for call in reads)


@pytest.mark.unit
class TestUnarchive:
    def test_insert_of_the_live_row_changes_no_flag(self, indexer):
        outcome = indexer.handle_stream_record(_record(indexer, "INSERT", "db1"))
        assert outcome.ok and outcome.action == "ignore"
        assert indexer.vector_store.calls == []

    def test_remove_of_the_archived_row_while_the_live_row_exists_changes_no_flag(self, indexer):
        _rows(indexer, live={"databaseId": "db1", "assetId": "a1"}, archived=None)
        outcome = indexer.handle_stream_record(_record(indexer, "REMOVE", "db1#deleted"))
        assert outcome.ok and outcome.action == "ignore"
        assert indexer.vector_store.calls == []

    def test_modify_of_the_live_row_changes_nothing(self, indexer):
        outcome = indexer.handle_stream_record(_record(indexer, "MODIFY", "db1"))
        assert outcome.action == "ignore" and indexer.vector_store.calls == []


@pytest.mark.unit
class TestPermanentDelete:
    def test_remove_with_no_row_in_either_partition_deletes_every_item(self, indexer):
        _rows(indexer, live=None, archived=None)
        outcome = indexer.handle_stream_record(_record(indexer, "REMOVE", "db1"))
        assert outcome.action == "asset-deleted"
        assert indexer.vector_store.calls == [("delete_asset", "db1:a1")]

    def test_the_trailing_remove_of_the_archived_row_is_idempotent(self, indexer):
        _rows(indexer, live=None, archived=None)
        indexer.handle_stream_record(_record(indexer, "REMOVE", "db1#deleted"))
        assert indexer.vector_store.calls == [("delete_asset", "db1:a1")]


@pytest.mark.unit
class TestSegmentItemsFollowTheirAsset:
    """The asset archive and the asset delete reach the whole `databaseId:assetId` partition, segment items
    included."""

    def _seed(self, indexer):
        store = FakeVectorStore(seed=[
            seeded_item("db1:a1", "/models/part.glb#v1", "v1"),
            seeded_item("db1:a1", f"/models/part.glb#v1#{SEGMENT_KEY}", "v1", segment_kind="videoTime"),
            seeded_item("db1:a2", "/models/other.glb#v1", "v1"),
        ])
        indexer.vector_store = store
        return store

    def test_an_asset_archive_marks_the_segment_item_with_its_asset(self, indexer):
        store = self._seed(indexer)
        outcome = indexer.handle_stream_record(_record(indexer, "INSERT", "db1#deleted"))
        flags = {item["fileVersionKey"]: item["isArchived"] for item in store.seeded}
        assert flags == {"/models/part.glb#v1": True,
                         f"/models/part.glb#v1#{SEGMENT_KEY}": True,
                         "/models/other.glb#v1": False}
        assert "archived=2" in outcome.detail

    def test_an_asset_permanent_delete_removes_the_segment_item_with_its_asset(self, indexer):
        store = self._seed(indexer)
        _rows(indexer, live=None, archived=None)
        outcome = indexer.handle_stream_record(_record(indexer, "REMOVE", "db1"))
        assert [item["fileVersionKey"] for item in store.seeded] == ["/models/other.glb#v1"]
        assert "deleted=2" in outcome.detail


@pytest.mark.unit
class TestIgnoredStreams:
    def test_metadata_table_records_are_ignored(self, indexer):
        record = _record(indexer, "INSERT", "db1", table="test-file-metadata-table")
        record["dynamodb"]["Keys"] = {"metadataKey": {"S": "REINDEX_METADATA_RECORD"},
                                      "databaseId:assetId:filePath": {"S": "db1:a1:/x.glb"}}
        outcome = indexer.handle_stream_record(record)
        assert outcome.ok and outcome.action == "ignore"
        assert indexer.vector_store.calls == []
        indexer.asset_storage_table.get_item.assert_not_called()

    def test_record_without_keys_is_ignored(self, indexer):
        record = _record(indexer, "REMOVE", "db1")
        record["dynamodb"]["Keys"] = {}
        assert indexer.handle_stream_record(record).action == "ignore"
