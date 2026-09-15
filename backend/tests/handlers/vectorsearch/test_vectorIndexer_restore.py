# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An ObjectCreated record written by a restore un-archives the file's items without demoting them.

A file unarchive is an S3 copy of the newest content version under a new version id, stamped
`vams-changesource=fileUnarchive`. Treated as an ordinary new version, the ObjectCreated rule flips the
file's items to `isLatest="false"` before any item exists for the copy -- and since no run embeds a
restore, none ever will -- so the file vanishes from every search. The rule instead reads the source from
the HEAD it already performs for the identity, and for a restore runs only the un-archive flip: the copied
content's items keep their latest marks, the file's `isArchived` clears, and exactly one latest item per
segment remains. Every other source keeps the demote-then-un-archive path.
"""

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
from tests.handlers.vectorsearch.vectorsearch_support import FakeVectorStore, SEGMENT_KEY, load_handler, seeded_item

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

    def test_a_restore_of_an_unindexed_file_changes_nothing(self, indexer):
        """No items exist for the file (the content was never embedded): nothing to un-archive, and
        nothing is written for the copy -- the store is left for the next real run to populate."""
        indexer.vector_store = FakeVectorStore(seed=[seeded_item("db1:a1", "/docs/other.pdf#v1", "v1")])
        indexer.s3_client.head_object.return_value = _head(VAMS_CHANGE_SOURCE_FILE_UNARCHIVE)
        outcome = indexer.handle_s3_record(_created(), "assets", "prefix-a/")
        assert outcome.ok and "unarchived=0" in outcome.detail
        assert _by_key(indexer.vector_store)["/docs/other.pdf#v1"]["isLatest"] is True


@pytest.mark.unit
class TestOtherSourcesKeepTheNewVersionRule:
    @pytest.mark.parametrize("source", NEW_VERSION_SOURCES)
    def test_a_new_version_demotes_then_unarchives(self, indexer, source):
        indexer.s3_client.head_object.return_value = _head(source)
        outcome = indexer.handle_s3_record(_created(), "assets", "prefix-a/")

        assert outcome.ok and outcome.action == "created"
        assert indexer.vector_store.calls == [
            ("set_not_latest_for_file_except", "db1:a1", KEY_PATH, RESTORED_VERSION),
            ("set_archived_for_file", "db1:a1", KEY_PATH, False),
        ]
        items = _by_key(indexer.vector_store)
        assert items[f"{KEY_PATH}#{OLD_VERSION}"]["isLatest"] is False
        assert items[f"{KEY_PATH}#{OLD_VERSION}#{SEGMENT_KEY}"]["isLatest"] is False
        assert items[f"{KEY_PATH}#{OLD_VERSION}"]["isArchived"] is False
