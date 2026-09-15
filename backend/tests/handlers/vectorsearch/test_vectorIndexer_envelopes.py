# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The vector indexer unwraps its four inbound envelopes and reports partial-batch failures.

One SQS queue carries: EventBridge `vector.embedding.ready` events (the event JSON IS the message body),
SNS-wrapped DynamoDB stream records from the asset indexer topic, the bucket-sync envelope from the file
indexer topic (SQS -> SNS -> SQS -> SNS -> S3, the nesting `fileIndexer.py` unwraps), and the indexer's
own `vector.indexer.continue` messages. Each record's dispatch is asserted through the handler it must
reach, and the lifecycle handlers receive the invocation's remaining-time callable; the rule bodies are
covered in their own files. Only the records whose processing failed may be reported for redelivery.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.handlers.vectorsearch.vectorsearch_support import load_handler


@pytest.fixture
def indexer():
    module = load_handler("vectorIndexer")
    module.asset_storage_table = MagicMock()
    module.s3_asset_buckets_table = MagicMock()
    return module


def _sqs(message_id, body):
    return {"messageId": message_id, "eventSource": "aws:sqs", "body": json.dumps(body)}


def _sns_notification(message):
    return {"Type": "Notification", "Message": json.dumps(message)}


def _embedding_ready_body(detail):
    return {"version": "0", "id": "e1", "detail-type": "vector.embedding.ready",
            "source": "vams.t1.pipeline.systemGenAi", "detail": detail}


def _stream_record(module, event_name="INSERT", database_id="db1", asset_id="a1"):
    return {
        "eventSourceARN": f"arn:aws:dynamodb:us-east-1:1:table/{module.asset_storage_table_name}/stream/x",
        "eventName": event_name,
        "dynamodb": {"Keys": {"databaseId": {"S": database_id}, "assetId": {"S": asset_id}},
                     "NewImage": {"databaseId": {"S": database_id}, "assetId": {"S": asset_id}}},
    }


def _bucket_sync_envelope(key, event_name="ObjectCreated:Put", bucket="assets", prefix="prefix-a/"):
    s3_event = {"Records": [{"eventSource": "aws:s3", "eventName": event_name,
                             "s3": {"bucket": {"name": bucket}, "object": {"key": key, "versionId": "v2"}}}]}
    inner_sqs = {"eventSource": "aws:sqs", "body": json.dumps(_sns_notification(s3_event))}
    return {"Records": [inner_sqs], "ASSET_BUCKET_NAME": bucket, "ASSET_BUCKET_PREFIX": prefix}


def _identifiers(response):
    assert "batchItemFailures" in response, \
        "no batchItemFailures field: the event-source mapping reads that as a whole-batch success"
    for entry in response["batchItemFailures"]:
        assert list(entry) == ["itemIdentifier"] and entry["itemIdentifier"]
    return [e["itemIdentifier"] for e in response["batchItemFailures"]]


@pytest.mark.unit
class TestEnvelopeDispatch:
    def test_eventbridge_body_reaches_the_embedding_handler(self, indexer):
        m = indexer
        detail = {"databaseId": "db1", "assetId": "a1", "filePath": "/model.glb", "versionId": "v1"}
        with patch.object(m, "handle_embedding_ready", return_value=m.Outcome(True, "put")) as handler:
            response = m.lambda_handler(
                {"Records": [_sqs("m1", _embedding_ready_body(detail))]}, MagicMock())
        handler.assert_called_once_with(detail)
        assert _identifiers(response) == []

    def test_sns_wrapped_stream_record_reaches_the_stream_handler(self, indexer):
        m = indexer
        record = _stream_record(m)
        with patch.object(m, "handle_stream_record", return_value=m.Outcome(True, "ignore")) as handler:
            m.lambda_handler({"Records": [_sqs("m1", _sns_notification(record))]}, MagicMock())
        handler.assert_called_once()
        assert handler.call_args.args[0]["eventName"] == "INSERT"

    def test_bucket_sync_envelope_reaches_the_s3_handler_with_bucket_identity(self, indexer):
        m = indexer
        envelope = _bucket_sync_envelope("prefix-a/a1/model.glb")
        with patch.object(m, "handle_s3_record", return_value=m.Outcome(True, "created")) as handler:
            m.lambda_handler({"Records": [_sqs("m1", _sns_notification(envelope))]}, MagicMock())
        handler.assert_called_once()
        s3_record, bucket_name, bucket_prefix, time_left = handler.call_args.args
        assert s3_record["s3"]["object"]["key"] == "prefix-a/a1/model.glb"
        assert (bucket_name, bucket_prefix) == ("assets", "prefix-a/")
        assert callable(time_left)

    def test_direct_s3_record_in_the_sns_message_is_also_dispatched(self, indexer):
        m = indexer
        message = {"Records": [{"eventSource": "aws:s3", "eventName": "ObjectRemoved:Delete",
                                "s3": {"bucket": {"name": "assets"}, "object": {"key": "a1/x.glb"}}}],
                   "ASSET_BUCKET_NAME": "assets", "ASSET_BUCKET_PREFIX": "/"}
        with patch.object(m, "handle_s3_record", return_value=m.Outcome(True, "deleted")) as handler:
            m.lambda_handler({"Records": [_sqs("m1", _sns_notification(message))]}, MagicMock())
        assert handler.call_args.args[1:3] == ("assets", "/")

    def test_continuation_body_reaches_the_continuation_handler(self, indexer):
        # The indexer's own message: a raw JSON body keyed by `detailType`, never SNS-wrapped.
        m = indexer
        body = {"detailType": "vector.indexer.continue", "rule": "deleteAsset", "pk": "db1:a1",
                "startKey": {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/m.glb#v1"}}}
        with patch.object(m, "handle_continuation", return_value=m.Outcome(True, "asset-deleted")) as handler:
            response = m.lambda_handler({"Records": [_sqs("m1", body)]}, MagicMock())
        handler.assert_called_once()
        message, time_left = handler.call_args.args
        assert message == body and callable(time_left)
        assert _identifiers(response) == []

    def test_the_lambda_time_budget_reaches_the_s3_and_stream_handlers(self, indexer):
        # The remaining-time callable the lifecycle rules pass to the store is the Lambda context's.
        m = indexer
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = 123_456
        with patch.object(m, "handle_s3_record", return_value=m.Outcome(True, "created")) as s3_handler, \
                patch.object(m, "handle_stream_record", return_value=m.Outcome(True, "ignore")) as stream_handler:
            m.lambda_handler({"Records": [
                _sqs("m1", _sns_notification(_bucket_sync_envelope("prefix-a/a1/model.glb"))),
                _sqs("m2", _sns_notification(_stream_record(m))),
            ]}, context)
        assert s3_handler.call_args.args[3]() == 123_456
        assert stream_handler.call_args.args[1]() == 123_456

    def test_unrecognized_body_is_dropped_not_redriven(self, indexer):
        m = indexer
        response = m.lambda_handler({"Records": [_sqs("m1", {"hello": "world"})]}, MagicMock())
        assert _identifiers(response) == []
        assert response["statusCode"] == 200


@pytest.mark.unit
class TestBatchFailureReporting:
    def test_only_the_failed_record_is_reported(self, indexer):
        m = indexer
        outcomes = {"good": m.Outcome(True, "put"), "bad": m.Outcome(False, "error", "boom")}

        def by_asset(detail):
            return outcomes[detail["assetId"]]

        event = {"Records": [_sqs("msg-good", _embedding_ready_body({"assetId": "good"})),
                             _sqs("msg-bad", _embedding_ready_body({"assetId": "bad"}))]}
        with patch.object(m, "handle_embedding_ready", side_effect=by_asset):
            response = m.lambda_handler(event, MagicMock())
        assert _identifiers(response) == ["msg-bad"]

    def test_exception_inside_one_record_reports_that_record_only(self, indexer):
        m = indexer
        event = {"Records": [_sqs("msg-1", _embedding_ready_body({"assetId": "a"})),
                             _sqs("msg-2", _embedding_ready_body({"assetId": "b"}))]}
        with patch.object(m, "handle_embedding_ready",
                          side_effect=[RuntimeError("unexpected"), m.Outcome(True, "put")]):
            response = m.lambda_handler(event, MagicMock())
        assert _identifiers(response) == ["msg-1"]
        assert response["statusCode"] == 200

    def test_exception_escaping_the_record_loop_reports_the_whole_batch(self, indexer):
        m = indexer
        event = {"Records": [_sqs("msg-1", {"hello": 1}), _sqs("msg-2", {"hello": 2})]}
        with patch.object(m, "success", side_effect=RuntimeError("response builder failed")):
            response = m.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 500
        assert set(_identifiers(response)) == {"msg-1", "msg-2"}

    def test_event_without_records_carries_no_failure_report(self, indexer):
        response = indexer.lambda_handler({"operation": "noop"}, MagicMock())
        assert "batchItemFailures" not in response
