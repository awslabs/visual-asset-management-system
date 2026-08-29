# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Partial-batch failure reporting for the asset and file indexers.

Guards S2-BACKEND-031: a failed index operation was converted into
`IndexOperationResponse(success=False)` and the handler still returned
`success(...)`. A response that carries no `batchItemFailures` is a whole-batch
SUCCESS to the event-source mapping, so SQS deleted the message: the asset or
file was never re-indexed and nothing recorded the loss beyond one log line.

Two exit paths matter and are covered separately:

* the normal path, where the failure is attributable to one record -- only that
  record is reported, so the batch still drains;
* the error paths, where an exception escapes the record loop -- the whole batch
  is reported, because an error response without a failure report deletes every
  message in it.

These tests cover the handler half of the contract. The other half is the event
source mapping: SQS reads `batchItemFailures` only because the indexer mappings
declare `FunctionResponseTypes: ["ReportBatchItemFailures"]`, and a record the
indexer can never process is dead-lettered by the source queue's redrive policy
rather than redelivered forever. Both are properties of the emitted
CloudFormation template, asserted in
`infra/test/t1IndexerBatchItemFailures.test.ts` across every partition
configuration; the construct that builds them is
`infra/lib/nestedStacks/searchAndIndexing/searchBuilder-nestedStack.ts`.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-asset-versions-table")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "test-links-table")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}

_INDEXING_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing"
)


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


def _load_indexer(module_name):
    """Load a real indexer module by file path with boto3/SSM stubbed."""
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
                f"{module_name}_batchfail_under_test",
                os.path.abspath(os.path.join(_INDEXING_DIR, f"{module_name}.py")),
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


@pytest.fixture
def asset_indexer():
    return _load_indexer("assetIndexer")


@pytest.fixture
def file_indexer():
    return _load_indexer("fileIndexer")


def _response(module, success_flag, operation="index"):
    from models.indexing import IndexOperationResponse
    return IndexOperationResponse(
        success=success_flag,
        message="stub",
        indexName="idx",
        operation=operation,
    )


def _asset_sqs_record(message_id, asset_id):
    """An SQS record carrying an SNS-wrapped asset-table stream record."""
    stream_record = {
        "eventSourceARN": "arn:aws:dynamodb:us-east-1:1:table/test-asset-table/stream/x",
        "eventName": "MODIFY",
        "dynamodb": {"NewImage": {"databaseId": {"S": "db1"},
                                  "assetId": {"S": asset_id}}},
    }
    return {
        "messageId": message_id,
        "eventSource": "aws:sqs",
        "body": json.dumps({"Type": "Notification",
                            "Message": json.dumps(stream_record)}),
    }


def _file_s3_record(message_id, key):
    """An SQS record carrying an SNS-wrapped S3 notification."""
    inner = {
        "Records": [{
            "eventSource": "aws:s3",
            "eventName": "ObjectCreated:Put",
            "s3": {"bucket": {"name": "bucket"}, "object": {"key": key}},
        }],
        "ASSET_BUCKET_NAME": "bucket",
        "ASSET_BUCKET_PREFIX": "prefix-a/",
    }
    return {
        "messageId": message_id,
        "eventSource": "aws:sqs",
        "body": json.dumps({"Type": "Notification", "Message": json.dumps(inner)}),
    }


def _identifiers(response):
    assert "batchItemFailures" in response, \
        "no batchItemFailures field: the event-source mapping reads that as a whole-batch success"
    failures = response["batchItemFailures"]
    # A malformed entry makes Lambda fail the ENTIRE batch, so the key name is
    # part of the contract.
    for entry in failures:
        assert list(entry) == ["itemIdentifier"], f"unexpected failure entry shape: {entry}"
        assert entry["itemIdentifier"], "an empty identifier fails the whole batch"
    return [entry["itemIdentifier"] for entry in failures]


@pytest.mark.unit
class TestAssetIndexerBatchFailures:
    def test_only_the_failed_record_is_reported(self, asset_indexer):
        m = asset_indexer
        event = {"Records": [_asset_sqs_record("msg-good", "a-good"),
                             _asset_sqs_record("msg-bad", "a-bad")]}

        def stream(record):
            asset_id = record["dynamodb"]["NewImage"]["assetId"]["S"]
            return _response(m, asset_id != "a-bad")

        with patch.object(m, "handle_asset_stream", side_effect=stream):
            response = m.lambda_handler(event, MagicMock())

        identifiers = _identifiers(response)
        assert "msg-bad" in identifiers
        # The record that indexed cleanly must NOT be redriven, or the batch
        # never drains.
        assert "msg-good" not in identifiers

    def test_all_success_reports_nothing(self, asset_indexer):
        """Positive control: the field is present and empty, not absent."""
        m = asset_indexer
        event = {"Records": [_asset_sqs_record("msg-1", "a1")]}
        with patch.object(m, "handle_asset_stream", return_value=_response(m, True)):
            response = m.lambda_handler(event, MagicMock())
        assert _identifiers(response) == []

    def test_exception_escaping_the_record_loop_reports_the_whole_batch(self, asset_indexer):
        """An error response with no failure report deletes every message in the
        batch. Reporting all of them redrives the batch instead; re-indexing an
        already-indexed record is an idempotent upsert."""
        m = asset_indexer
        event = {"Records": [_asset_sqs_record("msg-1", "a1"),
                             _asset_sqs_record("msg-2", "a2")]}
        with patch.object(m, "handle_asset_stream",
                          side_effect=RuntimeError("unexpected")):
            response = m.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 500
        assert set(_identifiers(response)) == {"msg-1", "msg-2"}

    def test_direct_invocation_carries_no_failure_report(self, asset_indexer):
        """A non-event-source invocation must not grow the field: it is only
        meaningful for a batch."""
        m = asset_indexer
        with patch.object(m, "process_asset_index_request",
                          return_value=_response(m, True)):
            response = m.lambda_handler(
                {"databaseId": "db1", "assetId": "a1", "operation": "index"},
                MagicMock())
        assert "batchItemFailures" not in response


@pytest.mark.unit
class TestFileIndexerBatchFailures:
    def test_only_the_failed_record_is_reported(self, file_indexer):
        m = file_indexer
        event = {"Records": [_file_s3_record("msg-good", "a1/good.glb"),
                             _file_s3_record("msg-bad", "a1/bad.glb")]}

        def notify(record):
            key = record["s3"]["object"]["key"]
            return _response(m, "bad" not in key)

        with patch.object(m, "handle_s3_notification", side_effect=notify):
            response = m.lambda_handler(event, MagicMock())

        identifiers = _identifiers(response)
        assert "msg-bad" in identifiers
        assert "msg-good" not in identifiers

    def test_all_success_reports_nothing(self, file_indexer):
        m = file_indexer
        event = {"Records": [_file_s3_record("msg-1", "a1/good.glb")]}
        with patch.object(m, "handle_s3_notification", return_value=_response(m, True)):
            response = m.lambda_handler(event, MagicMock())
        assert _identifiers(response) == []

    def test_exception_escaping_the_record_loop_reports_the_whole_batch(self, file_indexer):
        m = file_indexer
        event = {"Records": [_file_s3_record("msg-1", "a1/one.glb"),
                             _file_s3_record("msg-2", "a1/two.glb")]}
        with patch.object(m, "handle_s3_notification",
                          side_effect=RuntimeError("unexpected")):
            response = m.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 500
        assert set(_identifiers(response)) == {"msg-1", "msg-2"}

    def test_direct_invocation_carries_no_failure_report(self, file_indexer):
        m = file_indexer
        with patch.object(m, "process_file_index_request",
                          return_value=_response(m, True)):
            response = m.lambda_handler(
                {"databaseId": "db1", "assetId": "a1", "filePath": "/f.glb",
                 "bucketName": "bucket", "s3Key": "a1/f.glb", "operation": "index"},
                MagicMock())
        assert "batchItemFailures" not in response


@pytest.mark.unit
class TestBatchItemIdentifier:
    """A DynamoDB-stream record is identified by its SequenceNumber, an SQS record
    by its messageId. A record carrying neither cannot be reported at all, which
    must be logged rather than silently reported as an empty identifier (an empty
    identifier fails the whole batch)."""

    @pytest.mark.parametrize("module_name", ["assetIndexer", "fileIndexer"])
    def test_identifier_sources(self, module_name):
        m = _load_indexer(module_name)
        assert m.batch_item_identifier({"messageId": "msg-1"}) == "msg-1"
        assert m.batch_item_identifier(
            {"dynamodb": {"SequenceNumber": "42"}}) == "42"
        assert m.batch_item_identifier({"eventSource": "aws:sqs"}) is None
        # A record with no identifier is skipped rather than reported blank.
        assert m.all_batch_item_failures(
            {"Records": [{"eventSource": "aws:sqs"}, {"messageId": "msg-2"}]}) == \
            [{"itemIdentifier": "msg-2"}]

    @pytest.mark.parametrize("module_name", ["assetIndexer", "fileIndexer"])
    def test_non_dict_event_is_not_reported(self, module_name):
        m = _load_indexer(module_name)
        assert m.all_batch_item_failures(["not", "a", "dict"]) == []
