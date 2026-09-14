# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""vectorReindexer: the payload contract and the `clear` operation.

`clear` pages the table on the two key attributes only (never the embedding) and batch-deletes; when
fewer than 90 s remain it re-invokes itself asynchronously with a continuation carrying the scan cursor.
With `databaseId` the scan is filtered to partition keys beginning `<databaseId>:`, so a
scoped clear never touches another database's vectors; the Stubber tests pin that filter on the wire.
Unknown top-level keys are rejected: the migration tooling and operators are the only callers, and a
misspelt `dry_run` silently running a real clear is the failure this guards.
"""

import json
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.stub import ANY, Stubber

from tests.handlers.vectorsearch.vectorsearch_support import FakeVectorStore, load_handler

K1 = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v1"}}
K2 = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v2"}}
K3 = {"databaseId:assetId": {"S": "db1:a2"}, "fileVersionKey": {"S": "/b.glb#v1"}}
# Another database whose id merely starts with "db1": only the ":"-terminated prefix keeps it out of scope.
K_OTHER = {"databaseId:assetId": {"S": "db10:a1"}, "fileVersionKey": {"S": "/c.glb#v1"}}
CURSOR = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v2"}}


def _context(remaining_ms=600000):
    context = MagicMock()
    context.get_remaining_time_in_millis.return_value = remaining_ms
    return context


def _body(response):
    return json.loads(response["body"])


@pytest.fixture
def reindexer():
    m = load_handler("vectorReindexer")
    m.vector_store = FakeVectorStore(scan_pages=[(None, ([K1, K2], CURSOR)), (CURSOR, ([K3], None))])
    m.lambda_client = MagicMock()
    m.sqs_client = MagicMock()
    return m


@pytest.mark.unit
class TestPayloadContract:
    @pytest.mark.parametrize("payload, fragment", [
        ({"operation": "clear", "dry_run": True}, "unknown payload keys"),
        ({"operation": "purge"}, "operation must be one of"),
        ({}, "operation must be one of"),
        ({"operation": "clear", "dryRun": "yes"}, "dryRun must be a boolean"),
        ({"operation": "enqueue", "limit": 0}, "limit must be a positive integer"),
        ({"operation": "enqueue", "limit": True}, "limit must be a positive integer"),
        ({"operation": "enqueue", "databaseId": "a"}, "databaseId"),
        ({"operation": "enqueue", "startAfter": 7}, "startAfter must be a string"),
        ({"operation": "clear", "continuation": {"bogus": 1}}, "continuation"),
        ("not-an-object", "payload must be a JSON object"),
    ])
    def test_invalid_payloads_are_rejected_before_any_work(self, reindexer, payload, fragment):
        response = reindexer.lambda_handler(payload, _context())
        assert response["statusCode"] == 400
        assert fragment in _body(response)["error"]
        assert reindexer.vector_store.calls == []
        reindexer.lambda_client.invoke.assert_not_called()

    def test_the_public_keys_are_exactly_the_five_of_the_spec_plus_the_continuation(self, reindexer):
        assert reindexer.ALLOWED_KEYS == frozenset(
            {"operation", "dryRun", "limit", "databaseId", "startAfter", "continuation"})


@pytest.mark.unit
class TestClear:
    def test_clear_pages_the_table_and_deletes_every_key(self, reindexer):
        response = reindexer.lambda_handler({"operation": "clear"}, _context())
        body = _body(response)
        assert response["statusCode"] == 200
        assert reindexer.vector_store.names() == ["scan_keys", "delete_keys", "scan_keys", "delete_keys"]
        assert reindexer.vector_store.calls[1] == ("delete_keys", [K1, K2])
        assert reindexer.vector_store.calls[3] == ("delete_keys", [K3])
        assert (body["deleted"], body["continued"], body["phase"], body["tableEmpty"]) == (3, False, "done", True)
        reindexer.lambda_client.invoke.assert_not_called()

    def test_dry_run_counts_without_deleting(self, reindexer):
        body = _body(reindexer.lambda_handler({"operation": "clear", "dryRun": True}, _context()))
        assert reindexer.vector_store.names() == ["scan_keys", "scan_keys"]
        assert body["deleted"] == 3 and body["dryRun"] is True and body["tableEmpty"] is False

    def test_short_on_time_continues_itself_with_the_scan_cursor(self, reindexer):
        response = reindexer.lambda_handler({"operation": "clear"}, _context(remaining_ms=30000))
        body = _body(response)
        assert body["continued"] is True and body["phase"] == "clear" and body["deleted"] == 2
        assert reindexer.vector_store.names() == ["scan_keys", "delete_keys"]
        invoke = reindexer.lambda_client.invoke.call_args.kwargs
        assert invoke["FunctionName"] == "vectorReindexer-test" and invoke["InvocationType"] == "Event"
        payload = json.loads(invoke["Payload"])
        assert payload["operation"] == "clear" and payload["dryRun"] is False
        assert payload["continuation"]["clearStartKey"] == CURSOR
        assert payload["continuation"]["runId"] == body["reindexRunId"]
        assert payload["continuation"]["phase"] == "clear" and payload["continuation"]["deleted"] == 2

    def test_a_continuation_resumes_from_its_cursor_and_finishes(self, reindexer):
        continuation = {"runId": "abc123def456", "phase": "clear", "clearStartKey": CURSOR,
                        "startAfter": None, "chunk": 0, "enqueued": 0, "deleted": 2, "invocations": 1}
        body = _body(reindexer.lambda_handler({"operation": "clear", "continuation": continuation}, _context()))
        assert reindexer.vector_store.names() == ["scan_keys", "delete_keys"]
        assert reindexer.vector_store.calls[0] == ("scan_keys", CURSOR, reindexer.SCAN_PAGE_SIZE, None)
        assert (body["deleted"], body["reindexRunId"], body["invocations"], body["continued"]) == (3, "abc123def456", 2, False)

    def test_a_store_failure_is_a_500_that_names_the_run(self, reindexer):
        reindexer.vector_store.delete_keys = MagicMock(side_effect=RuntimeError("throttled"))
        response = reindexer.lambda_handler({"operation": "clear"}, _context())
        assert response["statusCode"] == 500
        assert "reindexRunId" in _body(response)
        reindexer.lambda_client.invoke.assert_not_called()


@pytest.mark.unit
class TestScopedClear:
    """`databaseId` confines `clear` to partition keys beginning `<databaseId>:`. `K_OTHER`
    belongs to `db10`, so a prefix without the trailing colon would wrongly sweep it up."""

    @pytest.fixture
    def mixed(self, reindexer):
        reindexer.vector_store = FakeVectorStore(
            scan_pages=[(None, ([K1, K_OTHER, K2], CURSOR)), (CURSOR, ([K3], None))])
        return reindexer

    def test_a_scoped_clear_deletes_only_that_databases_keys(self, mixed):
        body = _body(mixed.lambda_handler({"operation": "clear", "databaseId": "db1"}, _context()))
        assert mixed.vector_store.calls[0] == ("scan_keys", None, mixed.SCAN_PAGE_SIZE, "db1:")
        assert mixed.vector_store.calls[1] == ("delete_keys", [K1, K2])
        assert mixed.vector_store.calls[2] == ("scan_keys", CURSOR, mixed.SCAN_PAGE_SIZE, "db1:")
        assert mixed.vector_store.calls[3] == ("delete_keys", [K3])
        assert (body["deleted"], body["phase"], body["continued"]) == (3, "done", False)
        # The table still holds db10's vectors, so a scoped clear never claims it is empty.
        assert "tableEmpty" not in body

    def test_an_unscoped_clear_over_the_same_pages_deletes_every_database(self, mixed):
        body = _body(mixed.lambda_handler({"operation": "clear"}, _context()))
        assert mixed.vector_store.calls[0] == ("scan_keys", None, mixed.SCAN_PAGE_SIZE, None)
        assert mixed.vector_store.calls[1] == ("delete_keys", [K1, K_OTHER, K2])
        assert body["deleted"] == 4 and body["tableEmpty"] is True

    def test_a_scoped_continuation_keeps_the_scope(self, mixed):
        body = _body(mixed.lambda_handler({"operation": "clear", "databaseId": "db1"}, _context(remaining_ms=30000)))
        assert body["continued"] is True and body["deleted"] == 2
        payload = json.loads(mixed.lambda_client.invoke.call_args.kwargs["Payload"])
        assert payload["databaseId"] == "db1"
        assert payload["continuation"]["clearStartKey"] == CURSOR


@pytest.fixture
def stubbed():
    """The reindexer with a REAL botocore DynamoDB client under `Stubber`: the module builds its
    `DynamoDbVectorStore` on that client at import, so the scan the store sends is validated and pinned."""
    real_dynamodb = boto3.client("dynamodb", region_name="us-east-1",
                                 aws_access_key_id="test", aws_secret_access_key="test")
    stubber = Stubber(real_dynamodb)
    m = load_handler("vectorReindexer", boto_client_factory=lambda name, *args, **kwargs: (
        real_dynamodb if name == "dynamodb" else MagicMock()))
    m.lambda_client = MagicMock()
    m.sqs_client = MagicMock()
    return m, stubber


def _scan_params(m, **extra):
    return {"TableName": m.vector_table_name, "ProjectionExpression": ANY, "ExpressionAttributeNames": ANY,
            "Limit": m.SCAN_PAGE_SIZE, **extra}


@pytest.mark.unit
class TestScanFilterContract:
    """The scoped scan reaches DynamoDB with the `begins_with` FilterExpression and the unscoped scan carries
    none. `Stubber` matches `expected_params` exactly, so a key present in the call but absent from the
    expectation (or the reverse) fails the test — the absence of the filter is asserted, not assumed."""

    def test_a_scoped_clear_scans_with_begins_with_on_the_partition_key(self, stubbed):
        m, stubber = stubbed
        stubber.add_response("scan", {"Items": [K1], "Count": 1, "ScannedCount": 3}, _scan_params(
            m, FilterExpression="begins_with(#pk, :prefix)",
            ExpressionAttributeValues={":prefix": {"S": "db1:"}}))
        stubber.add_response("batch_write_item", {"UnprocessedItems": {}},
                             {"RequestItems": {m.vector_table_name: [{"DeleteRequest": {"Key": K1}}]}})
        with stubber:
            body = _body(m.lambda_handler({"operation": "clear", "databaseId": "db1"}, _context()))
            stubber.assert_no_pending_responses()
        assert body["deleted"] == 1 and "tableEmpty" not in body

    def test_an_unscoped_clear_scans_without_a_filter(self, stubbed):
        m, stubber = stubbed
        stubber.add_response("scan", {"Items": [], "Count": 0, "ScannedCount": 0}, _scan_params(m))
        with stubber:
            body = _body(m.lambda_handler({"operation": "clear"}, _context()))
            stubber.assert_no_pending_responses()
        assert body["deleted"] == 0 and body["tableEmpty"] is True
