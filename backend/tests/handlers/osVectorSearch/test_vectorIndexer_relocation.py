# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An ObjectCreated record for a relocated version leaves the file's vector items as they are.

The v2.6 to v2.7 upgrade step `assetLocationFileToFolder` moves a single-file asset's file into its own
folder: every version is copied, oldest first, to a new key at the same asset path, stamped `fileRelocate`,
and the step rewrites the file's vector items to the copies' version ids itself. The indexer therefore
changes nothing for such a record: no archived flag flips (an archived file's copy is put behind a delete
marker by the step, and an un-archive here could land after that marker's event), and no embedding is
queued, even for a file with no item (vectorBackfill embeds it once, after the step).
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from common.s3MetadataKeys import VAMS_CHANGE_SOURCE_FILE_MOVE
from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, load_handler, seeded_item

KEY_PATH = "/five.glb"
# The value the upgrade step stamps (VAMS_CHANGE_SOURCE_FILE_RELOCATE).
RELOCATE = "fileRelocate"
OBJECT_KEY = "prefix-a/a1/five.glb/five.glb"


def _created(version_id="vCopy3"):
    return {"eventSource": "aws:s3", "eventName": "ObjectCreated:Copy",
            "s3": {"bucket": {"name": "assets"}, "object": {"key": OBJECT_KEY, "versionId": version_id}}}


def _head(change_source):
    return {"VersionId": "vCopy6", "Metadata": {"databaseid": "db1", "assetid": "a1",
                                                 "vams-changesource": change_source,
                                                 "vams-changeassetfileversionfrom": "vSrc3"}}


def _launches(indexer):
    return [json.loads(call.kwargs["MessageBody"]) for call in indexer.sqs_client.send_message.call_args_list
            if call.kwargs.get("QueueUrl") == os.environ["WORKFLOW_LAUNCH_QUEUE_URL"]]


@pytest.fixture
def indexer():
    m = load_handler("vectorIndexer")
    m.s3_client = MagicMock()
    m.sqs_client = MagicMock()
    m.asset_storage_table = MagicMock()
    m.asset_storage_table.get_item.side_effect = lambda **kw: (
        {"Item": {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-guid",
                  "assetLocation": {"Key": "prefix-a/a1/five.glb/"}}}
        if kw["Key"]["databaseId"] == "db1" else {})
    m.s3_asset_buckets_table = MagicMock()
    m.s3_asset_buckets_table.query.return_value = {
        "Items": [{"bucketId": "bucket-guid", "bucketName": "assets", "baseAssetsPrefix": "prefix-a/"}]}
    return m


@pytest.mark.unit
class TestARelocatedVersion:
    def test_an_archived_files_items_stay_archived_and_latest(self, indexer):
        seed = [seeded_item("db1:a1", f"{KEY_PATH}#vCopy6", "vCopy6", True, True)]
        indexer.vector_store = FakeVectorStore(seed=[dict(item) for item in seed])
        indexer.s3_client.head_object.return_value = _head(RELOCATE)

        outcome = indexer.handle_s3_record(_created(), "assets", "prefix-a/")

        assert outcome.ok and outcome.action == "ignore" and outcome.detail == "relocated version"
        assert indexer.vector_store.calls == []
        assert indexer.vector_store.seeded == seed
        assert _launches(indexer) == []

    def test_a_file_with_no_item_is_not_queued_for_embedding(self, indexer):
        indexer.vector_store = FakeVectorStore(seed=[])
        indexer.s3_client.head_object.return_value = _head(RELOCATE)

        outcome = indexer.handle_s3_record(_created(), "assets", "prefix-a/")

        assert outcome.ok and outcome.action == "ignore" and outcome.detail == "relocated version"
        assert _launches(indexer) == []
        assert indexer.vector_store.calls == []

    def test_control_a_user_move_unarchives_as_a_new_version(self, indexer):
        indexer.vector_store = FakeVectorStore(seed=[seeded_item("db1:a1", f"{KEY_PATH}#v1", "v1", True, True)])
        indexer.s3_client.head_object.return_value = _head(VAMS_CHANGE_SOURCE_FILE_MOVE)

        outcome = indexer.handle_s3_record(_created(), "assets", "prefix-a/")

        assert outcome.action == "created"
        assert indexer.vector_store.calls == [("set_archived_for_file", "db1:a1", KEY_PATH, False)]
