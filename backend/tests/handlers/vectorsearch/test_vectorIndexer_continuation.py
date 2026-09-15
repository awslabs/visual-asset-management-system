# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset- and file-wide rules hand off under the time budget and resume from a continuation message.

Each of the five rules calls its store method with the invocation's remaining-time callable and, when the
store returns a `next_key`, sends ONE `vector.indexer.continue` message to the indexer's own queue naming
the rule, the partition key, the key path / version / archived flag the rule needs, and the start key. A
continuation message resumes the named rule from that key and passes it to the store; a run that completes
sends nothing. The `objectCreated` rule keeps its two steps in order across the hand-off. The fake store's
`hand_off` decides which method returns a key; the real paging belongs to the store's own tests.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.handlers.vectorsearch.vectorsearch_support import FakeVectorStore, load_handler

KEY_PATH = "/models/part.glb"
NEXT_KEY = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": f"{KEY_PATH}#v1#t0000083456"}}
START_KEY = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": f"{KEY_PATH}#v0"}}
CONTINUE = "vector.indexer.continue"


def _budget():
    return 42_000


def _not_found():
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"},
                        "ResponseMetadata": {"HTTPStatusCode": 404, "HTTPHeaders": {}}}, "HeadObject")


def _s3(event_name, version_id="v2"):
    return {"eventSource": "aws:s3", "eventName": event_name,
            "s3": {"bucket": {"name": "assets"},
                   "object": {"key": "prefix-a/a1/models/part.glb", "versionId": version_id}}}


def _stream(module, event_name, database_key):
    keys = {"databaseId": {"S": database_key}, "assetId": {"S": "a1"}}
    record = {"eventSourceARN": f"arn:aws:dynamodb:us-east-1:1:table/{module.asset_storage_table_name}/stream/x",
              "eventName": event_name, "dynamodb": {"Keys": keys}}
    if event_name != "REMOVE":
        record["dynamodb"]["NewImage"] = dict(keys)
    return record


def _sent(indexer):
    """The one continuation message the indexer sent to its own queue, parsed."""
    indexer.sqs_client.send_message.assert_called_once()
    kwargs = indexer.sqs_client.send_message.call_args.kwargs
    assert kwargs["QueueUrl"] == indexer.vector_indexer_queue_url
    return json.loads(kwargs["MessageBody"])


@pytest.fixture
def indexer():
    m = load_handler("vectorIndexer")
    m.vector_store = FakeVectorStore()
    m.sqs_client = MagicMock()
    m.s3_client = MagicMock()
    m.s3_client.head_object.return_value = {"VersionId": "v2", "Metadata": {"databaseid": "db1", "assetid": "a1"}}
    m.s3_client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
    m.asset_storage_table = MagicMock()
    m.asset_storage_table.get_item.side_effect = lambda **kw: (
        {"Item": {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-guid",
                  "assetLocation": {"Key": "prefix-a/a1/"}}}
        if kw["Key"]["databaseId"] == "db1" else {})
    m.asset_storage_table.query.return_value = {"Items": [
        {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-guid"}]}
    m.s3_asset_buckets_table = MagicMock()
    m.s3_asset_buckets_table.query.return_value = {
        "Items": [{"bucketId": "bucket-guid", "bucketName": "assets", "baseAssetsPrefix": "prefix-a/"}]}
    return m


@pytest.mark.unit
class TestHandOff:
    """A store method that returns a next_key makes its rule send exactly one message with the exact body."""

    def test_a_delete_marker_hands_off_the_file_archive(self, indexer):
        indexer.vector_store = FakeVectorStore(hand_off={"set_archived_for_file": NEXT_KEY})
        outcome = indexer.handle_s3_record(_s3("ObjectRemoved:DeleteMarkerCreated"), "assets", "prefix-a/", _budget)
        assert outcome.ok and outcome.action == "archived" and "continued" in outcome.detail
        assert _sent(indexer) == {"detailType": CONTINUE, "rule": "setArchivedForFile", "pk": "db1:a1",
                                  "keyPath": KEY_PATH, "archived": True, "startKey": NEXT_KEY}

    def test_a_new_version_hands_off_the_demotion_before_the_unarchive_step(self, indexer):
        indexer.vector_store = FakeVectorStore(hand_off={"set_not_latest_for_file_except": NEXT_KEY})
        outcome = indexer.handle_s3_record(_s3("ObjectCreated:Put"), "assets", "prefix-a/", _budget)
        assert outcome.action == "created" and "continued" in outcome.detail
        # The un-archive step is not run yet: the continuation finishes the demotion first, then runs it.
        assert indexer.vector_store.names() == ["set_not_latest_for_file_except"]
        assert _sent(indexer) == {"detailType": CONTINUE, "rule": "objectCreated", "pk": "db1:a1",
                                  "keyPath": KEY_PATH, "versionId": "v2", "startKey": NEXT_KEY}

    def test_a_new_version_hands_off_the_unarchive_step_as_its_own_rule(self, indexer):
        indexer.vector_store = FakeVectorStore(hand_off={"set_archived_for_file": NEXT_KEY})
        outcome = indexer.handle_s3_record(_s3("ObjectCreated:Put"), "assets", "prefix-a/", _budget)
        assert outcome.action == "created" and "continued" in outcome.detail
        assert indexer.vector_store.names() == ["set_not_latest_for_file_except", "set_archived_for_file"]
        assert _sent(indexer) == {"detailType": CONTINUE, "rule": "setArchivedForFile", "pk": "db1:a1",
                                  "keyPath": KEY_PATH, "archived": False, "startKey": NEXT_KEY}

    def test_a_permanent_delete_hands_off(self, indexer):
        indexer.vector_store = FakeVectorStore(hand_off={"delete_file": NEXT_KEY})
        indexer.s3_client.head_object.side_effect = _not_found()
        outcome = indexer.handle_s3_record(_s3("ObjectRemoved:Delete"), "assets", "prefix-a/", _budget)
        assert outcome.action == "deleted" and "continued" in outcome.detail
        assert _sent(indexer) == {"detailType": CONTINUE, "rule": "deleteFile", "pk": "db1:a1",
                                  "keyPath": KEY_PATH, "startKey": NEXT_KEY}

    def test_an_asset_archive_hands_off(self, indexer):
        indexer.vector_store = FakeVectorStore(hand_off={"set_archived_for_asset": NEXT_KEY})
        outcome = indexer.handle_stream_record(_stream(indexer, "INSERT", "db1#deleted"), _budget)
        assert outcome.action == "asset-archived" and "continued" in outcome.detail
        assert _sent(indexer) == {"detailType": CONTINUE, "rule": "setArchivedForAsset", "pk": "db1:a1",
                                  "archived": True, "startKey": NEXT_KEY}

    def test_an_asset_delete_hands_off(self, indexer):
        indexer.vector_store = FakeVectorStore(hand_off={"delete_asset": NEXT_KEY})
        indexer.asset_storage_table.get_item.side_effect = lambda **kw: {}
        outcome = indexer.handle_stream_record(_stream(indexer, "REMOVE", "db1"), _budget)
        assert outcome.action == "asset-deleted" and "continued" in outcome.detail
        assert _sent(indexer) == {"detailType": CONTINUE, "rule": "deleteAsset", "pk": "db1:a1",
                                  "startKey": NEXT_KEY}


RESUMES = [
    ({"rule": "objectCreated", "pk": "db1:a1", "keyPath": KEY_PATH, "versionId": "v2"},
     "set_not_latest_for_file_except"),
    ({"rule": "setArchivedForFile", "pk": "db1:a1", "keyPath": KEY_PATH, "archived": True},
     "set_archived_for_file"),
    ({"rule": "deleteFile", "pk": "db1:a1", "keyPath": KEY_PATH}, "delete_file"),
    ({"rule": "setArchivedForAsset", "pk": "db1:a1", "archived": True}, "set_archived_for_asset"),
    ({"rule": "deleteAsset", "pk": "db1:a1"}, "delete_asset"),
]


@pytest.mark.unit
class TestResume:
    @pytest.mark.parametrize("fields, method", RESUMES, ids=[fields["rule"] for fields, _ in RESUMES])
    def test_a_continuation_resumes_the_named_rule_from_its_start_key(self, indexer, fields, method):
        message = {"detailType": CONTINUE, **fields, "startKey": START_KEY}
        outcome = indexer.handle_continuation(message, _budget)
        assert outcome.ok and "continued" not in outcome.detail
        # The store receives the key the message carried and the invocation's budget; nothing is re-sent.
        assert indexer.vector_store.paging[0] == (method, START_KEY, _budget)
        indexer.sqs_client.send_message.assert_not_called()
        # No S3 or asset-table lookup: the message carries the identity the rule needs.
        indexer.s3_client.head_object.assert_not_called()
        indexer.asset_storage_table.get_item.assert_not_called()

    def test_a_resumed_object_created_finishes_the_demotion_then_unarchives(self, indexer):
        message = {"detailType": CONTINUE, "rule": "objectCreated", "pk": "db1:a1",
                   "keyPath": KEY_PATH, "versionId": "v2", "startKey": START_KEY}
        outcome = indexer.handle_continuation(message, _budget)
        assert outcome.action == "created"
        assert indexer.vector_store.calls == [
            ("set_not_latest_for_file_except", "db1:a1", KEY_PATH, "v2"),
            ("set_archived_for_file", "db1:a1", KEY_PATH, False),
        ]
        # The demotion resumes from the message's key; the un-archive step starts from its own first page.
        assert [(method, key) for method, key, _ in indexer.vector_store.paging] == [
            ("set_not_latest_for_file_except", START_KEY), ("set_archived_for_file", None)]


@pytest.mark.unit
class TestTimeBudget:
    def test_every_paging_call_receives_the_invocation_budget(self, indexer):
        indexer.handle_s3_record(_s3("ObjectCreated:Put"), "assets", "prefix-a/", _budget)
        indexer.handle_s3_record(_s3("ObjectRemoved:DeleteMarkerCreated"), "assets", "prefix-a/", _budget)
        indexer.handle_stream_record(_stream(indexer, "INSERT", "db1#deleted"), _budget)
        assert [method for method, _, _ in indexer.vector_store.paging] == [
            "set_not_latest_for_file_except", "set_archived_for_file", "set_archived_for_file",
            "set_archived_for_asset"]
        assert all(budget is _budget for _, _, budget in indexer.vector_store.paging)


@pytest.mark.unit
class TestCompleteRuns:
    def test_a_rule_that_completes_sends_nothing(self, indexer):
        # Negative control for TestHandOff: the same five rules, no hand-off configured.
        indexer.handle_s3_record(_s3("ObjectCreated:Put"), "assets", "prefix-a/", _budget)
        indexer.handle_s3_record(_s3("ObjectRemoved:DeleteMarkerCreated"), "assets", "prefix-a/", _budget)
        indexer.handle_stream_record(_stream(indexer, "INSERT", "db1#deleted"), _budget)
        indexer.asset_storage_table.get_item.side_effect = lambda **kw: {}
        indexer.handle_stream_record(_stream(indexer, "REMOVE", "db1"), _budget)
        indexer.s3_client.head_object.side_effect = _not_found()
        outcome = indexer.handle_s3_record(_s3("ObjectRemoved:Delete"), "assets", "prefix-a/", _budget)
        assert outcome.action == "deleted"
        assert len(indexer.vector_store.paging) == 6
        indexer.sqs_client.send_message.assert_not_called()


@pytest.mark.unit
class TestMalformedContinuations:
    def test_a_message_without_a_start_key_is_dropped(self, indexer):
        outcome = indexer.handle_continuation({"detailType": CONTINUE, "rule": "deleteAsset", "pk": "db1:a1"}, _budget)
        assert outcome.ok and outcome.action == "drop"
        assert indexer.vector_store.calls == []

    def test_a_file_rule_without_a_key_path_is_dropped(self, indexer):
        outcome = indexer.handle_continuation(
            {"detailType": CONTINUE, "rule": "deleteFile", "pk": "db1:a1", "startKey": START_KEY}, _budget)
        assert outcome.ok and outcome.action == "drop"
        assert indexer.vector_store.calls == []

    def test_an_unknown_rule_is_dropped(self, indexer):
        outcome = indexer.handle_continuation(
            {"detailType": CONTINUE, "rule": "reindexEverything", "pk": "db1:a1", "startKey": START_KEY}, _budget)
        assert outcome.ok and outcome.action == "drop"
        assert indexer.vector_store.calls == []
