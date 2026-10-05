# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Partial-batch failure reporting for the indexing SNS queuing Lambda.

`snsQueuing.lambda_handler` sits between the DynamoDB streams of the asset, database, metadata,
file attribute and asset link tables and the three indexer topics that every indexer subscribes
to. A stream event source mapping checkpoints past a batch on any normal return, so a response
without `batchItemFailures` drops every record it did not publish, for every subscriber at once.

A record Amazon SNS can never accept (one over the message size limit, or one rejected as an
invalid parameter) is logged and skipped rather than reported: reported, it would become the
checkpoint on every retry and take the healthy records behind it down with it when the mapping
discards the batch.

The mapping half of the contract -- `FunctionResponseTypes: ["ReportBatchItemFailures"]`, bisect
and a retry bound -- is a property of the emitted template, asserted in
`infra/test/storage/indexingFanoutFailureHandling.test.ts`.
"""

import importlib.util
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:test-indexer-topic")

_SNS_QUEUING_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing", "snsQueuing.py"
)

# The Amazon SNS publish limit for a message body.
_SNS_MESSAGE_LIMIT_BYTES = 256 * 1024


@pytest.fixture
def sns_queuing():
    """The real snsQueuing module, loaded by file path with its SNS client stubbed.

    Loaded by path because the root conftest registers a mock `handlers` package that shadows the
    real one. A fresh module per test, so no test sees another's client stub.
    """
    with patch("boto3.client", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location(
            "snsQueuing_batchfail_under_test", os.path.abspath(_SNS_QUEUING_PATH)
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def _stream_record(sequence_number, asset_id="a1"):
    """A DynamoDB stream record as the event source mapping delivers it."""
    return {
        "eventID": f"evt-{sequence_number}",
        "eventName": "MODIFY",
        "eventSource": "aws:dynamodb",
        "eventSourceARN": "arn:aws:dynamodb:us-east-1:123456789012:table/test-asset-table/stream/x",
        "dynamodb": {
            "Keys": {"databaseId": {"S": "db1"}, "assetId": {"S": asset_id}},
            "NewImage": {"databaseId": {"S": "db1"}, "assetId": {"S": asset_id}},
            "SequenceNumber": sequence_number,
            "StreamViewType": "NEW_IMAGE",
        },
    }


def _throttled():
    return ClientError({"Error": {"Code": "Throttling", "Message": "Rate exceeded"}}, "Publish")


def _invalid_parameter():
    return ClientError(
        {"Error": {"Code": "InvalidParameter", "Message": "Invalid parameter: Message too long"}},
        "Publish",
    )


def _publish_failing_on(*failing_sequence_numbers, error=_throttled):
    """An sns.publish stand-in that fails for the named records and succeeds for the rest."""
    def publish(TopicArn, Message, Subject):
        sequence_number = json.loads(Message)["dynamodb"]["SequenceNumber"]
        if sequence_number in failing_sequence_numbers:
            raise error()
        return {"MessageId": f"msg-{sequence_number}"}
    return publish


def _published_sequence_numbers(module):
    """The records a publish was attempted for, in call order (a failed attempt included)."""
    return [
        json.loads(c.kwargs["Message"])["dynamodb"]["SequenceNumber"]
        for c in module.sns_client.publish.call_args_list
    ]


def _identifiers(response):
    assert isinstance(response, dict) and "batchItemFailures" in response, (
        f"response {response!r} carries no batchItemFailures: the stream event source mapping "
        "reads that as a whole-batch success and checkpoints past every unpublished record"
    )
    failures = response["batchItemFailures"]
    # A malformed entry makes Lambda fail the ENTIRE batch, so the key name is part of the contract.
    for entry in failures:
        assert list(entry) == ["itemIdentifier"], f"unexpected failure entry shape: {entry}"
        assert entry["itemIdentifier"], "an empty identifier fails the whole batch"
    return [entry["itemIdentifier"] for entry in failures]


@pytest.mark.unit
class TestSnsQueuingBatchFailures:
    def test_all_published_reports_an_empty_failure_list(self, sns_queuing):
        """Positive control: the field is present and empty, not absent."""
        m = sns_queuing
        m.sns_client = MagicMock()
        m.sns_client.publish.side_effect = _publish_failing_on()
        event = {"Records": [_stream_record("100"), _stream_record("200")]}

        response = m.lambda_handler(event, MagicMock())

        assert _identifiers(response) == []
        assert _published_sequence_numbers(m) == ["100", "200"]

    def test_failed_publish_reports_it_and_every_later_record(self, sns_queuing):
        """The mapping resumes from the lowest reported sequence number. Reporting the failed
        record and everything after it, and publishing nothing past it, keeps each item's
        changes in stream order on the retry."""
        m = sns_queuing
        m.sns_client = MagicMock()
        m.sns_client.publish.side_effect = _publish_failing_on("200")
        event = {"Records": [_stream_record("100"), _stream_record("200"), _stream_record("300")]}

        response = m.lambda_handler(event, MagicMock())

        assert _identifiers(response) == ["200", "300"]
        # 300 is never attempted, so it is not published ahead of the retried 200.
        assert _published_sequence_numbers(m) == ["100", "200"]

    def test_every_publish_failing_reports_the_whole_batch(self, sns_queuing):
        m = sns_queuing
        m.sns_client = MagicMock()
        m.sns_client.publish.side_effect = _throttled()
        event = {"Records": [_stream_record("100"), _stream_record("200")]}

        response = m.lambda_handler(event, MagicMock())

        assert _identifiers(response) == ["100", "200"]

    def test_exception_outside_the_publish_loop_reports_the_whole_batch(self, sns_queuing):
        """An error response with no failure report is still a checkpoint for a stream mapping."""
        m = sns_queuing
        event = {"Records": [_stream_record("100"), _stream_record("200")]}

        with patch.object(m, "publish_to_sns", side_effect=RuntimeError("unexpected")):
            response = m.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 500
        assert _identifiers(response) == ["100", "200"]

    def test_failure_that_names_no_record_raises(self, sns_queuing):
        """With no sequence number to report, an empty list would read as success. Raising fails
        the batch whole, which the mapping retries and bisects."""
        m = sns_queuing
        m.sns_client = MagicMock()
        m.sns_client.publish.side_effect = _throttled()
        record = _stream_record("100")
        del record["dynamodb"]["SequenceNumber"]

        with pytest.raises(Exception):
            m.lambda_handler({"Records": [record]}, MagicMock())

    def test_direct_invocation_carries_no_failure_report(self, sns_queuing):
        """A non-event-source invocation must not grow the field: it is only meaningful for a
        batch."""
        m = sns_queuing
        response = m.lambda_handler({}, MagicMock())
        assert response["statusCode"] == 200
        assert "batchItemFailures" not in response

    def test_record_over_the_sns_size_limit_is_skipped_not_reported(self, sns_queuing):
        """A record SNS can never accept is skipped before the publish, so it cannot become the
        checkpoint that holds the healthy records behind it until the mapping discards them."""
        m = sns_queuing
        m.sns_client = MagicMock()
        m.sns_client.publish.side_effect = _publish_failing_on()
        oversized = _stream_record("200")
        oversized["dynamodb"]["NewImage"]["description"] = {
            "S": "x" * (_SNS_MESSAGE_LIMIT_BYTES + 1)
        }
        event = {"Records": [_stream_record("100"), oversized, _stream_record("300")]}

        response = m.lambda_handler(event, MagicMock())

        assert _identifiers(response) == []
        assert _published_sequence_numbers(m) == ["100", "300"]

    def test_record_sns_rejects_as_invalid_is_skipped_not_reported(self, sns_queuing):
        """An invalid-parameter rejection cannot clear on a retry, so that record is logged and
        skipped and the records behind it are still published."""
        m = sns_queuing
        m.sns_client = MagicMock()
        m.sns_client.publish.side_effect = _publish_failing_on("200", error=_invalid_parameter)
        event = {"Records": [_stream_record("100"), _stream_record("200"), _stream_record("300")]}

        response = m.lambda_handler(event, MagicMock())

        assert _identifiers(response) == []
        assert _published_sequence_numbers(m) == ["100", "200", "300"]
