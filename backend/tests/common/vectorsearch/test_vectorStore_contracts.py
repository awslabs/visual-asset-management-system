# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""One Stubber contract test per DynamoDB operation the vector store issues.

``test_vectorStore.py`` proves the behaviour against a fake that accepts any keyword. These tests wrap a
REAL client in ``botocore.stub.Stubber`` (tests/vectorStub.py), so botocore validates every parameter
name and type against the service model, the stubbed responses are validated against the output
shapes, and ``expected_params`` pins the exact request each operation sends. They need the Task 1 SDK
floor and never skip.

The Stubber serves its queue in order. The store's per-item flips run on a thread pool within one Query
page, so every walk scripted here carries at most one update per page: the update of page one is
issued before page two is read, which keeps the order deterministic.
"""

import importlib.util
import os

import pytest

from backend.tests.vectorStub import stubbed_dynamodb

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "vectorsearch", "vectorStore.py"
)

TABLE = "vector-table"
INDEX = "vec-amazon-titan-embed-text-v2-0-2"
MODEL = "amazon.titan-embed-text-v2:0"
PK = "databaseId:assetId"
SK = "fileVersionKey"
NAMES = {"#pk": PK, "#sk": SK}
FILE_PROJECTION = "#pk, #sk, versionId, isLatest, isArchived"
KEY_PROJECTION = "#pk, #sk"
SEGMENT_PROJECTION = "#pk, #sk, pipelineExecutionId"


@pytest.fixture
def vs():
    spec = importlib.util.spec_from_file_location("vectorStore_contracts_under_test", os.path.abspath(_MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _key(version, key_path="/m.glb"):
    """``version`` may carry a segment suffix (``"v2#t0000010000"``) to stand for a segment item's key."""
    return {PK: {"S": "db1:a1"}, SK: {"S": f"{key_path}#{version}"}}


def _image(version, is_latest="true", is_archived="false"):
    return {**_key(version), "versionId": {"S": version}, "isLatest": {"S": is_latest}, "isArchived": {"S": is_archived}}


def _segment_image(version, segment_key, pipeline_execution_id):
    return {**_key(f"{version}#{segment_key}"), "pipelineExecutionId": {"S": pipeline_execution_id}}


def _file_query(projection):
    return {
        "TableName": TABLE,
        "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :prefix)",
        "ExpressionAttributeNames": NAMES,
        "ExpressionAttributeValues": {":pk": {"S": "db1:a1"}, ":prefix": {"S": "/m.glb#"}},
        "ProjectionExpression": projection,
    }


def _asset_query(projection):
    return {
        "TableName": TABLE,
        "KeyConditionExpression": "#pk = :pk",
        "ExpressionAttributeNames": NAMES,
        "ExpressionAttributeValues": {":pk": {"S": "db1:a1"}},
        "ProjectionExpression": projection,
    }


def _archived_update(version, target, current):
    return {
        "method": "update_item",
        "expected_params": {
            "TableName": TABLE, "Key": _key(version), "UpdateExpression": "SET isArchived = :target",
            "ConditionExpression": "isArchived = :current",
            "ExpressionAttributeValues": {":target": {"S": target}, ":current": {"S": current}},
        },
        "response": {},
    }


def _item(vs, **overrides):
    fields = dict(
        databaseId="db1", assetId="a1", filePath="/m.glb", versionId="v2", isLatest=True, isArchived=False,
        fileClass="mesh", fileExt="glb", embeddingModelId=MODEL, embeddingDimensions=2, embedding=[0.5, 0.25],
        sourceText="a mesh", sourceModalities=["renders"], contentEtag="etag", bucketId="b1", fileSize=42,
        contentType="model/gltf-binary", analysisModelId="anthropic.claude-haiku-4-5", indexedAt="2026-09-08T00:00:00Z",
        pipelineExecutionId="pe1", workflowExecutionId="we1",
    )
    fields.update(overrides)
    return vs.VectorItem(**fields)


def _segment_item(vs):
    """A videoTime segment item of the same file version as ``_item``."""
    return _item(
        vs, segmentKey="t0000083456", segmentKind="videoTime", segmentLabel="00:01:23.456-00:01:33.456",
        segmentStartMs=83456, segmentEndMs=93456, segmentCount=12,
    )


@pytest.mark.unit
class TestSearchVectorsContract:
    def test_filtered_search(self, vs):
        expected = {
            "TableName": TABLE,
            "IndexName": INDEX,
            "SearchVector": [{"N": "0.5"}, {"N": "0.25"}],
            "TopK": 5,
            "SearchConditionExpression": "#isLatest = :isLatest AND #embeddingModelId = :embeddingModelId",
            "ExpressionAttributeNames": {"#isLatest": "isLatest", "#embeddingModelId": "embeddingModelId"},
            "ExpressionAttributeValues": {":isLatest": {"S": "true"}, ":embeddingModelId": {"S": MODEL}},
        }
        response = {"SearchResults": [{"Item": {**_key("v1"), "fileClass": {"S": "mesh"}, "fileSize": {"N": "42"}}, "Score": 0.25}]}
        with stubbed_dynamodb([{"method": "search_vectors", "expected_params": expected, "response": response}]) as client:
            hits = vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).search(
                [0.5, 0.25], top_k=5, filters={"isLatest": "true", "embeddingModelId": MODEL})
        assert len(hits) == 1
        assert hits[0].distance == 0.25
        assert hits[0].item == {PK: "db1:a1", SK: "/m.glb#v1", "fileClass": "mesh", "fileSize": 42}

    def test_unfiltered_search_sends_no_expression_keys(self, vs):
        expected = {"TableName": TABLE, "IndexName": INDEX, "SearchVector": [{"N": "0.5"}, {"N": "0.25"}], "TopK": 100}
        with stubbed_dynamodb([{"method": "search_vectors", "expected_params": expected, "response": {"SearchResults": []}}]) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).search([0.5, 0.25], top_k=100, filters={}) == []

    def test_backfilling_index_is_reported_as_not_ready(self, vs):
        expected = {"TableName": TABLE, "IndexName": INDEX, "SearchVector": [{"N": "0.5"}, {"N": "0.25"}], "TopK": 5}
        calls = [{"method": "search_vectors", "expected_params": expected,
                  "error": {"code": "ValidationException", "message": f"The table does not have the specified index: {INDEX}", "http_status_code": 400}}]
        with stubbed_dynamodb(calls) as client:
            with pytest.raises(vs.VectorIndexNotReady):
                vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).search([0.5, 0.25], top_k=5, filters={})


@pytest.mark.unit
class TestPutItemContract:
    def test_put_item_image_is_valid_for_the_service_model(self, vs):
        """The whole-file image: the segment fields at their defaults, the time window as NULL."""
        item = _item(vs)
        expected = {"TableName": TABLE, "Item": vs.serialize_item(item)}
        with stubbed_dynamodb([{"method": "put_item", "expected_params": expected, "response": {}}]) as client:
            vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).put_item(item)

    def test_segment_item_image_is_valid_for_the_service_model(self, vs):
        """The segment image: the time window as N, the key under the segment sort key."""
        item = _segment_item(vs)
        expected = {"TableName": TABLE, "Item": vs.serialize_item(item)}
        with stubbed_dynamodb([{"method": "put_item", "expected_params": expected, "response": {}}]) as client:
            vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).put_item(item)
        assert expected["Item"][SK] == {"S": "/m.glb#v2#t0000083456"}


def _sibling_query(version_id="v2"):
    return {
        "TableName": TABLE,
        "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :prefix)",
        "FilterExpression": "isLatest = :latest AND versionId <> :v",
        "ExpressionAttributeNames": NAMES,
        "ExpressionAttributeValues": {
            ":pk": {"S": "db1:a1"}, ":prefix": {"S": "/m.glb#"}, ":latest": {"S": "true"}, ":v": {"S": version_id},
        },
        "ProjectionExpression": KEY_PROJECTION,
    }


def _demotion(version):
    return {"Update": {
        "TableName": TABLE,
        "Key": _key(version),
        "UpdateExpression": "SET isLatest = :target",
        "ConditionExpression": "isLatest = :current",
        "ExpressionAttributeValues": {":target": {"S": "false"}, ":current": {"S": "true"}},
    }}


def _put(vs, item):
    return {"Put": {"TableName": TABLE, "Item": vs.serialize_item(item)}}


def _transaction(actions):
    return {"method": "transact_write_items", "expected_params": {"TransactItems": actions}, "response": {}}


@pytest.mark.unit
class TestPutLatestItemContract:
    """The isLatest write path: Query, then a plain PutItem when it returns nothing, otherwise the Put and
    the demotions in TransactWriteItems of at most DEMOTION_BATCH_ACTIONS (25) actions, whose shapes and
    sizes the stub pins."""

    def test_query_then_one_transaction_with_the_put_and_a_demotion_per_sibling(self, vs):
        item = _item(vs)
        calls = [
            {"method": "query", "expected_params": _sibling_query(), "response": {"Items": [_key("v1"), _key("v0")]}},
            _transaction([_put(vs, item), _demotion("v1"), _demotion("v0")]),
        ]
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).put_latest_item(item) == 2

    def test_no_sibling_is_a_plain_put_item(self, vs):
        """Nothing to demote -- a first version, or a file whose S3 ObjectCreated demotion already ran --
        is a PutItem, not a one-action transaction."""
        item = _item(vs)
        calls = [
            {"method": "query", "expected_params": _sibling_query(), "response": {"Items": []}},
            {"method": "put_item", "expected_params": {"TableName": TABLE, "Item": vs.serialize_item(item)}, "response": {}},
        ]
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).put_latest_item(item) == 0

    def test_cancelled_transaction_is_retried_from_a_fresh_query(self, vs):
        """The sibling read v1 was demoted by another writer before the transaction landed: the retry
        re-reads (no sibling left) and writes the item with a plain PutItem."""
        item = _item(vs)
        calls = [
            {"method": "query", "expected_params": _sibling_query(), "response": {"Items": [_key("v1")]}},
            {"method": "transact_write_items", "expected_params": {"TransactItems": [_put(vs, item), _demotion("v1")]},
             "error": {"code": "TransactionCanceledException",
                       "message": "Transaction cancelled, please refer cancellation reasons for specific reasons [None, ConditionalCheckFailed]",
                       "http_status_code": 400}},
            {"method": "query", "expected_params": _sibling_query(), "response": {"Items": []}},
            {"method": "put_item", "expected_params": {"TableName": TABLE, "Item": vs.serialize_item(item)}, "response": {}},
        ]
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).put_latest_item(item) == 0

    def test_a_segment_document_demotes_other_versions_only(self, vs):
        """The Put lands under the segment sort key; the sibling Query excludes the document's own
        version by versionId, so the same version's whole-file item and sibling segments are never
        demoted, while an older version's whole-file item and segments are."""
        item = _segment_item(vs)
        calls = [
            {"method": "query", "expected_params": _sibling_query(), "response": {"Items": [_key("v1"), _key("v1#t0000083456")]}},
            _transaction([_put(vs, item), _demotion("v1"), _demotion("v1#t0000083456")]),
        ]
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).put_latest_item(item) == 2
        assert vs.serialize_item(item)[SK] == {"S": "/m.glb#v2#t0000083456"}

    def test_a_hundred_siblings_are_demoted_across_five_transactions(self, vs):
        """The Put and 24 demotions fill the first transaction to DEMOTION_BATCH_ACTIONS, three more
        transactions of 25 follow, and the hundredth sibling is demoted alone in a fifth. The stub pins
        every list, so a batch that grew past 25 -- accepted by the service model, whose own limit is
        100 -- fails here."""
        item = _item(vs)
        keys = [_key(f"v{i}") for i in range(100)]
        actions = [_put(vs, item)] + [_demotion(f"v{i}") for i in range(100)]
        transactions = [_transaction(actions[start:start + 25]) for start in range(0, 101, 25)]
        assert [len(t["expected_params"]["TransactItems"]) for t in transactions] == [25, 25, 25, 25, 1]
        calls = [{"method": "query", "expected_params": _sibling_query(), "response": {"Items": keys}}] + transactions
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).put_latest_item(item) == 100


@pytest.mark.unit
class TestQueryAndUpdateContracts:
    def test_set_not_latest_for_file_except(self, vs):
        calls = [
            {"method": "query", "expected_params": _file_query(FILE_PROJECTION),
             "response": {"Items": [_image("v1"), _image("v2")]}},
            {"method": "update_item",
             "expected_params": {
                 "TableName": TABLE, "Key": _key("v1"),
                 "UpdateExpression": "SET isLatest = :target",
                 "ConditionExpression": "isLatest = :current",
                 "ExpressionAttributeValues": {":target": {"S": "false"}, ":current": {"S": "true"}},
             },
             "response": {}},
        ]
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).set_not_latest_for_file_except("db1:a1", "/m.glb", "v2") == (1, None)

    def test_paged_query_threads_the_cursor(self, vs):
        """Each page is processed before the next is read: query, update, query, update."""
        first = _file_query(FILE_PROJECTION)
        second = {**first, "ExclusiveStartKey": _key("v1")}
        calls = [
            {"method": "query", "expected_params": first, "response": {"Items": [_image("v1", is_archived="true")], "LastEvaluatedKey": _key("v1")}},
            _archived_update("v1", "false", "true"),
            {"method": "query", "expected_params": second, "response": {"Items": [_image("v2", is_archived="true")]}},
            _archived_update("v2", "false", "true"),
        ]
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).set_archived_for_file("db1:a1", "/m.glb", False) == (2, None)

    def test_set_archived_for_asset(self, vs):
        calls = [
            {"method": "query", "expected_params": _asset_query(FILE_PROJECTION), "response": {"Items": [_image("v1")]}},
            _archived_update("v1", "true", "false"),
        ]
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).set_archived_for_asset("db1:a1", True) == (1, None)

    def test_a_short_budget_returns_the_cursor_and_the_resumed_walk_sends_it_as_the_exclusive_start_key(self, vs):
        first = _file_query(FILE_PROJECTION)
        resumed = {**first, "ExclusiveStartKey": _key("v1")}
        calls = [
            {"method": "query", "expected_params": first, "response": {"Items": [_image("v1")], "LastEvaluatedKey": _key("v1")}},
            _archived_update("v1", "true", "false"),
            {"method": "query", "expected_params": resumed, "response": {"Items": [_image("v2")]}},
            _archived_update("v2", "true", "false"),
        ]
        with stubbed_dynamodb(calls) as client:
            store = vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client)
            count, next_key = store.set_archived_for_file("db1:a1", "/m.glb", True, time_remaining_fn=lambda: 0)
            assert (count, next_key) == (1, _key("v1"))
            assert store.set_archived_for_file("db1:a1", "/m.glb", True, start_key=next_key, time_remaining_fn=lambda: 0) == (1, None)


@pytest.mark.unit
class TestDeleteContracts:
    def test_delete_file(self, vs):
        calls = [
            {"method": "query", "expected_params": _file_query(KEY_PROJECTION), "response": {"Items": [_key("v1"), _key("v2")]}},
            {"method": "batch_write_item",
             "expected_params": {"RequestItems": {TABLE: [{"DeleteRequest": {"Key": _key("v1")}}, {"DeleteRequest": {"Key": _key("v2")}}]}},
             "response": {"UnprocessedItems": {}}},
        ]
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).delete_file("db1:a1", "/m.glb") == (2, None)

    def test_delete_asset(self, vs):
        calls = [
            {"method": "query", "expected_params": _asset_query(KEY_PROJECTION), "response": {"Items": [_key("v1"), _key("v1", "/n.glb")]}},
            {"method": "batch_write_item",
             "expected_params": {"RequestItems": {TABLE: [{"DeleteRequest": {"Key": _key("v1")}}, {"DeleteRequest": {"Key": _key("v1", "/n.glb")}}]}},
             "response": {"UnprocessedItems": {}}},
        ]
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).delete_asset("db1:a1") == (2, None)


@pytest.mark.unit
class TestDeleteOtherRunSegmentsContract:
    def test_delete_other_run_segments(self, vs):
        query = {
            "TableName": TABLE,
            "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :prefix)",
            "ExpressionAttributeNames": NAMES,
            "ExpressionAttributeValues": {":pk": {"S": "db1:a1"}, ":prefix": {"S": "/m.glb#v2#"}},
            "ProjectionExpression": SEGMENT_PROJECTION,
        }
        delete = {
            "TableName": TABLE,
            "Key": _key("v2#t0000010000"),
            "ConditionExpression": "pipelineExecutionId <> :run",
            "ExpressionAttributeValues": {":run": {"S": "pe1"}},
        }
        calls = [
            {"method": "query", "expected_params": query,
             "response": {"Items": [_segment_image("v2", "t0000000000", "pe1"), _segment_image("v2", "t0000010000", "pe0")]}},
            {"method": "delete_item", "expected_params": delete, "response": {}},
        ]
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).delete_other_run_segments(
                "db1:a1", "/m.glb", "v2", "pe1") == 1

    def test_a_delete_that_loses_its_condition_is_not_counted(self, vs):
        calls = [
            {"method": "query", "response": {"Items": [_segment_image("v2", "t0000010000", "pe0")]}},
            {"method": "delete_item", "error": {"code": "ConditionalCheckFailedException",
                                                "message": "The conditional request failed", "http_status_code": 400}},
        ]
        with stubbed_dynamodb(calls) as client:
            assert vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client).delete_other_run_segments(
                "db1:a1", "/m.glb", "v2", "pe1") == 0


@pytest.mark.unit
class TestScanContract:
    def test_scan_keys_first_page_and_continuation(self, vs):
        first = {"TableName": TABLE, "ProjectionExpression": KEY_PROJECTION, "ExpressionAttributeNames": NAMES, "Limit": 2}
        second = {**first, "ExclusiveStartKey": _key("v2")}
        calls = [
            {"method": "scan", "expected_params": first, "response": {"Items": [_key("v1"), _key("v2")], "LastEvaluatedKey": _key("v2")}},
            {"method": "scan", "expected_params": second, "response": {"Items": [_key("v3")]}},
        ]
        with stubbed_dynamodb(calls) as client:
            store = vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, 2, client)
            items, last = store.scan_keys(limit=2)
            assert (items, last) == ([_key("v1"), _key("v2")], _key("v2"))
            items, last = store.scan_keys(start_key=last, limit=2)
            assert (items, last) == ([_key("v3")], None)
