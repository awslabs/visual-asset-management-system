# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An ObjectCreated record written by a restore un-archives the file's items without demoting them.

A file unarchive is an S3 copy of the newest content version under a new version id, stamped
`vams-changesource=fileUnarchive`. No run embeds a restore, so no item will ever exist for the copy: the
copied content's items must keep their latest marks. The rule reads the source from the HEAD it already
performs for the identity, and for a restore runs only the un-archive flip: the file's `isArchived` clears
and exactly one latest item per segment remains. Every other source is a new version, which un-archives the
file and also leaves the latest marks to the new version's own whole-file write. A restore (or a delete
marker removed) of a file with no item at all, never embedded because it was archived before vector search
indexed it, queues one launch of the system workflow; a file with items is never re-embedded.
"""

import json
import os
import re
from unittest.mock import MagicMock

import pytest

from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_ASSET_UNARCHIVE,
    VAMS_CHANGE_SOURCE_DIRECT,
    VAMS_CHANGE_SOURCE_FILE_COPY,
    VAMS_CHANGE_SOURCE_FILE_MOVE,
    VAMS_CHANGE_SOURCE_FILE_RENAME,
    VAMS_CHANGE_SOURCE_FILE_REVERT,
    VAMS_CHANGE_SOURCE_FILE_UNARCHIVE,
    VAMS_CHANGE_SOURCE_UPLOAD,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
)
from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, SEGMENT_KEY, load_handler, seeded_item

KEY_PATH = "/docs/report.pdf"
OBJECT_KEY = "prefix-a/a1/docs/report.pdf"
OLD_VERSION = "vOld"
RESTORED_VERSION = "vRestored"

RESTORE_SOURCES = [VAMS_CHANGE_SOURCE_FILE_UNARCHIVE, VAMS_CHANGE_SOURCE_ASSET_UNARCHIVE]
NEW_VERSION_SOURCES = [
    VAMS_CHANGE_SOURCE_UPLOAD,
    VAMS_CHANGE_SOURCE_DIRECT,
    VAMS_CHANGE_SOURCE_FILE_COPY,
    VAMS_CHANGE_SOURCE_FILE_MOVE,
    VAMS_CHANGE_SOURCE_FILE_RENAME,
    VAMS_CHANGE_SOURCE_FILE_REVERT,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
    "",  # an object written before provenance was stamped
]


def _created(version_id=RESTORED_VERSION):
    return {"eventSource": "aws:s3", "eventName": "ObjectCreated:Copy",
            "s3": {"bucket": {"name": "assets"}, "object": {"key": OBJECT_KEY, "versionId": version_id}}}


def _head(change_source):
    metadata = {"databaseid": "db1", "assetid": "a1"}
    if change_source:
        metadata["vams-changesource"] = change_source
    return {"VersionId": RESTORED_VERSION, "Metadata": metadata}


def _archived_file_store():
    """The file's items as an archive leaves them: latest, archived; a sibling file untouched."""
    return FakeVectorStore(seed=[
        seeded_item("db1:a1", f"{KEY_PATH}#{OLD_VERSION}", OLD_VERSION, True, True),
        seeded_item("db1:a1", f"{KEY_PATH}#{OLD_VERSION}#{SEGMENT_KEY}", OLD_VERSION, True, True,
                    segment_kind="textChunk"),
        seeded_item("db1:a1", "/docs/other.pdf#v1", "v1", True, False),
    ])


def _by_key(store):
    return {item["fileVersionKey"]: item for item in store.seeded}


@pytest.fixture
def indexer():
    m = load_handler("vectorIndexer")
    m.vector_store = _archived_file_store()
    m.s3_client = MagicMock()
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
class TestRestoredVersion:
    @pytest.mark.parametrize("source", RESTORE_SOURCES)
    def test_a_restore_unarchives_without_demoting(self, indexer, source):
        indexer.s3_client.head_object.return_value = _head(source)
        outcome = indexer.handle_s3_record(_created(), "assets", "prefix-a/")

        assert outcome.ok and outcome.action == "unarchived"
        assert indexer.vector_store.calls == [("set_archived_for_file", "db1:a1", KEY_PATH, False)], \
            "only the archived flip runs; set_not_latest_for_file_except is never called"
        items = _by_key(indexer.vector_store)
        assert items[f"{KEY_PATH}#{OLD_VERSION}"] == {
            **items[f"{KEY_PATH}#{OLD_VERSION}"], "isLatest": True, "isArchived": False}
        assert items[f"{KEY_PATH}#{OLD_VERSION}#{SEGMENT_KEY}"]["isLatest"] is True
        assert items[f"{KEY_PATH}#{OLD_VERSION}#{SEGMENT_KEY}"]["isArchived"] is False
        assert items["/docs/other.pdf#v1"] == seeded_item("db1:a1", "/docs/other.pdf#v1", "v1", True, False)
        assert "unarchived=2" in outcome.detail

    @pytest.mark.parametrize("source", RESTORE_SOURCES)
    def test_the_file_keeps_exactly_one_latest_item_per_segment(self, indexer, source):
        indexer.s3_client.head_object.return_value = _head(source)
        indexer.handle_s3_record(_created(), "assets", "prefix-a/")

        live = [item for item in indexer.vector_store._file_items("db1:a1", KEY_PATH)
                if item["isLatest"] and not item["isArchived"]]
        by_segment = {}
        for item in live:
            segment = item["fileVersionKey"].split("#", 2)[2] if item["fileVersionKey"].count("#") == 2 else ""
            by_segment.setdefault(segment, []).append(item["versionId"])
        assert by_segment == {"": [OLD_VERSION], SEGMENT_KEY: [OLD_VERSION]}

    def test_the_source_comes_from_the_single_identity_head(self, indexer):
        indexer.s3_client.head_object.return_value = _head(VAMS_CHANGE_SOURCE_FILE_UNARCHIVE)
        indexer.handle_s3_record(_created(), "assets", "prefix-a/")
        indexer.s3_client.head_object.assert_called_once_with(Bucket="assets", Key=OBJECT_KEY)

    def test_a_restore_of_an_unindexed_file_flips_nothing(self, indexer):
        """No items exist for the file (the content was never embedded, e.g. archived before vector
        search was deployed): nothing to un-archive, and no item of another file changes."""
        indexer.vector_store = FakeVectorStore(seed=[seeded_item("db1:a1", "/docs/other.pdf#v1", "v1")])
        indexer.s3_client.head_object.return_value = _head(VAMS_CHANGE_SOURCE_FILE_UNARCHIVE)
        outcome = indexer.handle_s3_record(_created(), "assets", "prefix-a/")
        assert outcome.ok and "unarchived=0" in outcome.detail
        assert _by_key(indexer.vector_store)["/docs/other.pdf#v1"]["isLatest"] is True


def _launches(indexer):
    """The system-workflow launch messages the indexer sent, parsed."""
    return [json.loads(call.kwargs["MessageBody"]) for call in indexer.sqs_client.send_message.call_args_list
            if call.kwargs.get("QueueUrl") == os.environ["WORKFLOW_LAUNCH_QUEUE_URL"]]


def _marker_removed():
    return {"eventSource": "aws:s3", "eventName": "ObjectRemoved:Delete",
            "s3": {"bucket": {"name": "assets"}, "object": {"key": OBJECT_KEY, "versionId": "marker-1"}}}


@pytest.mark.unit
class TestUnarchiveEmbedsAFileWithNoItem:
    """A file restored with no vector item (never embedded, e.g. archived before the upgrade) is embedded
    through the system workflow's launch queue, the same paced launch a reindex uses, which runs the
    workflow exactly as its upload trigger would. A file that has items is only flipped live."""

    @pytest.fixture(autouse=True)
    def _sqs(self, indexer):
        indexer.sqs_client = MagicMock()

    @staticmethod
    def _unindexed(indexer):
        indexer.vector_store = FakeVectorStore(seed=[seeded_item("db1:a1", "/docs/other.pdf#v1", "v1")])

    @pytest.mark.parametrize("source", RESTORE_SOURCES)
    def test_a_restore_of_an_unindexed_file_enqueues_one_embedding(self, indexer, source):
        self._unindexed(indexer)
        indexer.s3_client.head_object.return_value = _head(source)
        outcome = indexer.handle_s3_record(_created(), "assets", "prefix-a/")
        assert outcome.ok and "embedQueued" in outcome.detail
        (launch,) = _launches(indexer)
        assert {k: launch[k] for k in ("databaseId", "assetId", "relativeFileKey", "versionId", "chunk")} == {
            "databaseId": "db1", "assetId": "a1", "relativeFileKey": KEY_PATH, "versionId": "", "chunk": 0}
        assert launch["reindexRunId"]
        assert indexer.vector_store.names() == ["set_archived_for_file", "file_has_items"]

    def test_a_marker_removal_on_an_unindexed_file_enqueues_one_embedding(self, indexer):
        self._unindexed(indexer)
        indexer.s3_client.head_object.return_value = _head("")
        outcome = indexer.handle_s3_record(_marker_removed(), "assets", "prefix-a/")
        assert outcome.ok and outcome.action == "unarchived"
        assert len(_launches(indexer)) == 1

    @pytest.mark.parametrize("record, source", [(_created, VAMS_CHANGE_SOURCE_FILE_UNARCHIVE), (_marker_removed, "")])
    def test_an_indexed_file_is_flipped_live_and_not_re_embedded(self, indexer, record, source):
        indexer.s3_client.head_object.return_value = _head(source)
        outcome = indexer.handle_s3_record(record(), "assets", "prefix-a/")
        assert outcome.ok and "unarchived=2" in outcome.detail
        assert _launches(indexer) == []

    def test_a_redelivered_restore_of_a_file_already_live_is_not_re_embedded(self, indexer):
        indexer.vector_store = FakeVectorStore(seed=[seeded_item("db1:a1", f"{KEY_PATH}#{OLD_VERSION}", OLD_VERSION)])
        indexer.s3_client.head_object.return_value = _head(VAMS_CHANGE_SOURCE_FILE_UNARCHIVE)
        outcome = indexer.handle_s3_record(_created(), "assets", "prefix-a/")
        assert outcome.ok and "unarchived=0" in outcome.detail
        assert _launches(indexer) == []

    @pytest.mark.parametrize("source", NEW_VERSION_SOURCES)
    def test_a_new_version_of_an_unindexed_file_is_left_to_its_upload_trigger(self, indexer, source):
        self._unindexed(indexer)
        indexer.s3_client.head_object.return_value = _head(source)
        indexer.handle_s3_record(_created(), "assets", "prefix-a/")
        assert _launches(indexer) == []

    def test_a_delete_marker_never_enqueues(self, indexer):
        self._unindexed(indexer)
        indexer.s3_client.head_object.return_value = _head("")
        record = {**_marker_removed(), "eventName": "ObjectRemoved:DeleteMarkerCreated"}
        indexer.handle_s3_record(record, "assets", "prefix-a/")
        assert _launches(indexer) == []

    def test_a_resumed_unarchive_never_enqueues(self, indexer):
        # A walk only hands off when the file has items, so its continuation has nothing to embed.
        self._unindexed(indexer)
        message = {"detailType": "vector.indexer.continue", "rule": "setArchivedForFile", "pk": "db1:a1",
                   "keyPath": KEY_PATH, "archived": False, "startKey": {"k": {"S": "v"}}}
        assert indexer.handle_continuation(message, lambda: 900_000).ok
        assert _launches(indexer) == []

    def test_the_launcher_accepts_the_message_as_a_valid_execute_request(self, indexer):
        self._unindexed(indexer)
        indexer.s3_client.head_object.return_value = _head(VAMS_CHANGE_SOURCE_FILE_UNARCHIVE)
        indexer.handle_s3_record(_created(), "assets", "prefix-a/")
        (launch,) = _launches(indexer)
        launcher = load_handler("systemWorkflowLauncher")
        launcher._trigger_row_cache["row"] = {}
        record = {"messageId": "m1", "body": json.dumps(launch)}
        body = launcher.build_launch_body(launcher._parse_message(record))
        assert body["inputFiles"] == [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": KEY_PATH, "versionId": ""}]
        # executionGroupId is validated by the execute request's ID rule (3-63 of [-_a-zA-Z0-9]).
        assert re.fullmatch(r"vec-unarchive-[0-9a-f]{12}-0", body["executionGroupId"])


@pytest.mark.unit
class TestOtherSourcesKeepTheNewVersionRule:
    @pytest.mark.parametrize("source", NEW_VERSION_SOURCES)
    def test_a_new_version_unarchives_and_demotes_nothing(self, indexer, source):
        indexer.s3_client.head_object.return_value = _head(source)
        outcome = indexer.handle_s3_record(_created(), "assets", "prefix-a/")

        assert outcome.ok and outcome.action == "created"
        assert indexer.vector_store.calls == [("set_archived_for_file", "db1:a1", KEY_PATH, False)]
        items = _by_key(indexer.vector_store)
        # The new version's own whole-file write demotes these; until then they stay searchable.
        assert items[f"{KEY_PATH}#{OLD_VERSION}"]["isLatest"] is True
        assert items[f"{KEY_PATH}#{OLD_VERSION}#{SEGMENT_KEY}"]["isLatest"] is True
        assert items[f"{KEY_PATH}#{OLD_VERSION}"]["isArchived"] is False
