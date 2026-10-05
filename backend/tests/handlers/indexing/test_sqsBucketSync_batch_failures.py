# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Partial-batch failure reporting for the bucket-sync created and deleted handlers.

Bucket sync is the single path by which a change to an asset bucket reaches the file indexer topic,
and through it every file indexer (OpenSearch, Physna, Garnet). A response without
`batchItemFailures` is a whole-batch success to the SQS event source mapping, which then deletes
every message in the batch, including one whose records never reached the topic. The handlers
report the messages a failed topic publish or an unhandled error left undelivered, so SQS
redelivers them and the queue's redrive policy dead-letters a message that keeps failing.

A record whose VAMS-side processing failed (a metadata stamp, an asset creation) is still forwarded
to the indexers and is NOT redriven: that behaviour is pinned in
`test_sqsBucketSync_indexer_forwarding.py` and asserted as a control here.

The mapping half of the contract (`FunctionResponseTypes: ["ReportBatchItemFailures"]`) is asserted
on the synthesized template in `infra/test/storage/indexingFanoutFailureHandling.test.ts`.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

# _load() caches the module across test files, so every attribute replaced here is restored after
# each test or the stub leaks into the other sqsBucketSync suites.
_PATCHED_ATTRS = (
    "sns_client", "file_indexer_sns_topic_arn", "parse_event", "process_s3_record",
    "publish_to_orchestration_bus", "asset_bucket_name", "asset_bucket_prefix",
    "RESERVED_S3_PREFIX_FOLDERS", "get_bucket_id", "validate_asset_id", "lookup_asset",
    "update_asset_type",
)


@pytest.fixture(autouse=True)
def _restore_module_attrs():
    m = _load()
    saved = {name: getattr(m, name) for name in _PATCHED_ATTRS}
    yield
    for name, value in saved.items():
        setattr(m, name, value)


def _s3_record(key, event_name="ObjectCreated:Put"):
    return {
        "eventSource": "aws:s3",
        "eventName": event_name,
        "s3": {"bucket": {"name": "asset-bucket"}, "object": {"key": key}},
    }


def _sqs_message(message_id, *s3_records):
    """One bucket-sync SQS message: the per-bucket SNS notification wrapping an S3 event."""
    return {
        "messageId": message_id,
        "eventSource": "aws:sqs",
        "body": json.dumps({
            "Type": "Notification",
            "Message": json.dumps({"Records": list(s3_records)}),
        }),
    }


def _topic(m, publish_fails):
    """Point the real publish_to_file_indexer_sns at a stub topic that accepts or rejects."""
    m.file_indexer_sns_topic_arn = "arn:aws:sns:us-east-1:123456789012:test-file-indexer-topic"
    m.sns_client = MagicMock()
    if publish_fails:
        m.sns_client.publish.side_effect = ClientError(
            {"Error": {"Code": "KMSAccessDeniedException", "Message": "denied"}}, "Publish"
        )
    else:
        m.sns_client.publish.return_value = {"MessageId": "sns-1"}


def _identifiers(response):
    assert isinstance(response, dict) and "batchItemFailures" in response, (
        f"response {response!r} carries no batchItemFailures: the SQS event source mapping reads "
        "that as a whole-batch success and deletes every message in it"
    )
    for entry in response["batchItemFailures"]:
        assert list(entry) == ["itemIdentifier"], f"unexpected failure entry shape: {entry}"
        assert entry["itemIdentifier"], "an empty identifier fails the whole batch"
    return sorted(entry["itemIdentifier"] for entry in response["batchItemFailures"])


def _wire_created(m, results_by_key):
    """process_s3_record returns the (success, should_index, message) mapped to each key."""
    m.process_s3_record = MagicMock(
        side_effect=lambda record: results_by_key[record["s3"]["object"]["key"]]
    )
    m.publish_to_orchestration_bus = MagicMock()


def _wire_deleted(m):
    """Wiring so lambda_handler_deleted reaches per-record processing with the asset already gone."""
    m.asset_bucket_name = "asset-bucket"
    m.asset_bucket_prefix = "db/"
    m.RESERVED_S3_PREFIX_FOLDERS = {"temp-uploads"}
    m.get_bucket_id = MagicMock(return_value="bucket-1")
    m.validate_asset_id = MagicMock(return_value=True)
    m.lookup_asset = MagicMock(return_value=None)
    m.update_asset_type = MagicMock(return_value=True)


_OK = (True, True, "Successfully processed")


@pytest.mark.unit
class TestCreatedHandlerBatchFailures:
    def test_clean_batch_reports_nothing_and_publishes_the_trigger(self):
        """Positive control: the field is present and empty, and the trigger bus is published."""
        m = _load()
        _topic(m, publish_fails=False)
        _wire_created(m, {"db/a1/one.glb": _OK, "db/a2/two.glb": _OK})
        event = {"Records": [_sqs_message("msg-1", _s3_record("db/a1/one.glb")),
                             _sqs_message("msg-2", _s3_record("db/a2/two.glb"))]}

        response = m.lambda_handler_created(event, MagicMock())

        assert _identifiers(response) == []
        m.sns_client.publish.assert_called_once()
        m.publish_to_orchestration_bus.assert_called_once()

    def test_topic_publish_failure_reports_every_message_it_carried(self):
        m = _load()
        _topic(m, publish_fails=True)
        _wire_created(m, {"db/a1/one.glb": _OK, "db/a2/two.glb": _OK})
        event = {"Records": [_sqs_message("msg-1", _s3_record("db/a1/one.glb")),
                             _sqs_message("msg-2", _s3_record("db/a2/two.glb"))]}

        response = m.lambda_handler_created(event, MagicMock())

        assert _identifiers(response) == ["msg-1", "msg-2"]
        # The trigger is published by the delivery that reaches the topic, so a redriven file fires
        # its fileUpload trigger once rather than once per delivery.
        m.publish_to_orchestration_bus.assert_not_called()

    def test_topic_publish_failure_spares_messages_it_did_not_carry(self):
        """A message whose records were all withheld from the indexers (a folder marker here) was
        never part of the publish, so it is not redriven with the ones that were."""
        m = _load()
        _topic(m, publish_fails=True)
        _wire_created(m, {
            "db/a1/one.glb": _OK,
            "db/a1/folder/": (True, False, "Processed folder marker db/a1/folder/"),
        })
        event = {"Records": [_sqs_message("msg-1", _s3_record("db/a1/one.glb")),
                             _sqs_message("msg-2", _s3_record("db/a1/folder/"))]}

        response = m.lambda_handler_created(event, MagicMock())

        assert _identifiers(response) == ["msg-1"]

    def test_unhandled_error_reports_the_whole_batch(self):
        m = _load()
        _topic(m, publish_fails=False)
        m.parse_event = MagicMock(side_effect=RuntimeError("unexpected"))
        m.publish_to_orchestration_bus = MagicMock()
        event = {"Records": [_sqs_message("msg-1", _s3_record("db/a1/one.glb")),
                             _sqs_message("msg-2", _s3_record("db/a2/two.glb"))]}

        response = m.lambda_handler_created(event, MagicMock())

        assert _identifiers(response) == ["msg-1", "msg-2"]

    def test_vams_side_processing_error_is_forwarded_not_redriven(self):
        """Control: a hard error in VAMS-side processing is forwarded to the indexers once and its
        message is deleted, not reported for redrive."""
        m = _load()
        _topic(m, publish_fails=False)
        _wire_created(m, {"db/a1/bad.glb": (False, True, "Failed to update metadata for db/a1/bad.glb")})
        event = {"Records": [_sqs_message("msg-1", _s3_record("db/a1/bad.glb"))]}

        response = m.lambda_handler_created(event, MagicMock())

        assert _identifiers(response) == []
        m.sns_client.publish.assert_called_once()

    def test_withheld_batch_reports_nothing(self):
        """Nothing to publish is not a failure: an out-of-scope record is not redriven forever."""
        m = _load()
        _topic(m, publish_fails=True)
        _wire_created(m, {"db/a1/folder/": (True, False, "Processed folder marker db/a1/folder/")})
        event = {"Records": [_sqs_message("msg-1", _s3_record("db/a1/folder/"))]}

        response = m.lambda_handler_created(event, MagicMock())

        assert _identifiers(response) == []
        m.sns_client.publish.assert_not_called()


@pytest.mark.unit
class TestDeletedHandlerBatchFailures:
    def test_clean_batch_reports_nothing(self):
        """Positive control for the two failure tests below."""
        m = _load()
        _topic(m, publish_fails=False)
        _wire_deleted(m)
        event = {"Records": [_sqs_message(
            "msg-1", _s3_record("db/x-asset-1/file.glb", "ObjectRemoved:DeleteMarkerCreated"))]}

        response = m.lambda_handler_deleted(event, MagicMock())

        assert _identifiers(response) == []
        m.sns_client.publish.assert_called_once()

    def test_topic_publish_failure_reports_the_deleted_messages(self):
        """The indexers must see the delete to remove the file; a lost publish leaves it searchable."""
        m = _load()
        _topic(m, publish_fails=True)
        _wire_deleted(m)
        event = {"Records": [
            _sqs_message("msg-1", _s3_record("db/x-asset-1/a.glb", "ObjectRemoved:DeleteMarkerCreated")),
            _sqs_message("msg-2", _s3_record("db/x-asset-1/b.glb", "ObjectRemoved:DeleteMarkerCreated")),
        ]}

        response = m.lambda_handler_deleted(event, MagicMock())

        assert _identifiers(response) == ["msg-1", "msg-2"]

    def test_unhandled_error_reports_the_whole_batch(self):
        m = _load()
        _topic(m, publish_fails=False)
        m.parse_event = MagicMock(side_effect=RuntimeError("unexpected"))
        event = {"Records": [_sqs_message(
            "msg-1", _s3_record("db/x-asset-1/file.glb", "ObjectRemoved:DeleteMarkerCreated"))]}

        response = m.lambda_handler_deleted(event, MagicMock())

        assert _identifiers(response) == ["msg-1"]


# Amazon S3 sends this to a notification's destination when the configuration is created or changed.
# It carries no `Records`, so it names no object.
_S3_TEST_EVENT = {
    "Service": "Amazon S3",
    "Event": "s3:TestEvent",
    "Time": "2026-09-28T00:00:00.000Z",
    "Bucket": "asset-bucket",
    "RequestId": "5582815E1AEA5ADF",
    "HostId": "8cLeGAmw098X5cv4Zkwcmo8vvZa3eH3eKxsPzbB9wrR+YstdA6Knx4Ip8EXAMPLE",
}


def _test_event_message(message_id):
    """An SQS message whose SNS envelope wraps the s3:TestEvent instead of an S3 event."""
    return {
        "messageId": message_id,
        "eventSource": "aws:sqs",
        "body": json.dumps({"Type": "Notification", "Message": json.dumps(_S3_TEST_EVENT)}),
    }


@pytest.mark.unit
class TestS3TestEventMessages:
    """A message that carries no S3 record is never part of a publish, so it is never redriven:
    a redriven s3:TestEvent would be received three times and then dead-lettered, once for every
    bucket notification configuration change."""

    def test_a_created_batch_of_only_a_test_event_reports_nothing(self):
        m = _load()
        _topic(m, publish_fails=False)
        _wire_created(m, {})

        response = m.lambda_handler_created({"Records": [_test_event_message("msg-1")]}, MagicMock())

        assert _identifiers(response) == []
        m.sns_client.publish.assert_not_called()
        m.process_s3_record.assert_not_called()

    def test_a_test_event_is_not_redriven_with_a_failed_publish(self):
        m = _load()
        _topic(m, publish_fails=True)
        _wire_created(m, {"db/a1/one.glb": _OK})
        event = {"Records": [_sqs_message("msg-1", _s3_record("db/a1/one.glb")),
                             _test_event_message("msg-2")]}

        response = m.lambda_handler_created(event, MagicMock())

        assert _identifiers(response) == ["msg-1"]

    def test_a_deleted_batch_of_only_a_test_event_reports_nothing(self):
        m = _load()
        _topic(m, publish_fails=False)
        _wire_deleted(m)

        response = m.lambda_handler_deleted({"Records": [_test_event_message("msg-1")]}, MagicMock())

        assert _identifiers(response) == []
        m.sns_client.publish.assert_not_called()
