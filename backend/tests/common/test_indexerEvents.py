# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`common.indexerEvents.s3_records_from_indexer_message`: every envelope shape the file indexer topic
carries flattens to the same S3 records, and anything that is not an S3 event yields nothing.

The builders below reproduce the message `sqsBucketSync.build_filtered_event` +
`publish_to_file_indexer_sns` publish for the deployed delivery path (S3 -> SNS -> SQS ->
sqsBucketSync): the SQS records the handler was invoked with, each wrapping an SNS `Notification`
whose `Message` is the S3 event, stamped with the bucket identity. `tests/handlers/compliance/
test_complianceTrigger.py` imports them so the trigger is fed the same shape."""

import json

import pytest

from common.indexerEvents import s3_records_from_indexer_message

BUCKET = "asset-bucket"
TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:asset-created"
QUEUE_ARN = "arn:aws:sqs:us-east-1:123456789012:bucket-sync"


def s3_event_record(key, bucket=BUCKET, event_name="ObjectCreated:Put",
                    event_time="2026-03-01T12:00:00.000Z", version_id="v1"):
    """One S3 event record as the bucket notification emits it."""
    return {
        "eventVersion": "2.1",
        "eventSource": "aws:s3",
        "awsRegion": "us-east-1",
        "eventTime": event_time,
        "eventName": event_name,
        "s3": {
            "s3SchemaVersion": "1.0",
            "bucket": {"name": bucket, "arn": f"arn:aws:s3:::{bucket}"},
            "object": {"key": key, "size": 3, "eTag": "e", "versionId": version_id, "sequencer": "0"},
        },
    }


def sns_envelope(message):
    """The SNS `Notification` an SQS body carries after an SNS -> SQS hop."""
    return {
        "Type": "Notification",
        "MessageId": "6f1e2d3c-0000-4000-8000-000000000001",
        "TopicArn": TOPIC_ARN,
        "Subject": "Amazon S3 Notification",
        "Message": json.dumps(message),
        "Timestamp": "2026-03-01T12:00:00.100Z",
    }


def sqs_record(body, message_id="msg-1"):
    """One record of an SQS-invoked Lambda event; `body` is serialized the way SQS delivers it."""
    return {
        "messageId": message_id,
        "receiptHandle": "handle",
        "body": json.dumps(body),
        "attributes": {"ApproximateReceiveCount": "1"},
        "messageAttributes": {},
        "eventSource": "aws:sqs",
        "eventSourceARN": QUEUE_ARN,
        "awsRegion": "us-east-1",
    }


def indexer_message(*s3_records, bucket=BUCKET, prefix=""):
    """The SNS `Message` sqsBucketSync publishes to the file indexer topic for the deployed delivery
    path: one SQS record per bucket notification (each an SNS envelope around a one-record S3
    event), plus the bucket identity `publish_to_file_indexer_sns` stamps on the payload."""
    return {
        "Records": [sqs_record(sns_envelope({"Records": [record]}), message_id=f"msg-{index}")
                    for index, record in enumerate(s3_records, start=1)],
        "ASSET_BUCKET_NAME": bucket,
        "ASSET_BUCKET_PREFIX": prefix,
    }


def nested_indexer_message(*s3_records, bucket=BUCKET, prefix=""):
    """The same message after one more SNS -> SQS hop: each SQS record's `Notification` wraps another
    SQS-shaped event whose records carry the S3 event `Notification`."""
    return {
        "Records": [sqs_record(sns_envelope({"Records": [
            sqs_record(sns_envelope({"Records": [record]}), message_id=f"inner-{index}")]}),
            message_id=f"msg-{index}")
                    for index, record in enumerate(s3_records, start=1)],
        "ASSET_BUCKET_NAME": bucket,
        "ASSET_BUCKET_PREFIX": prefix,
    }


def _keys(records):
    return [record["s3"]["object"]["key"] for record in records]


@pytest.mark.unit
class TestTheDeployedSqsWrappedShape:

    def test_one_notification_yields_its_record_with_the_event_fields_intact(self):
        records = s3_records_from_indexer_message(indexer_message(s3_event_record("a1/model.stl")))
        assert len(records) == 1
        record = records[0]
        assert record["s3"]["bucket"]["name"] == BUCKET
        assert record["s3"]["object"]["key"] == "a1/model.stl"
        assert record["s3"]["object"]["versionId"] == "v1"
        assert record["eventName"] == "ObjectCreated:Put"
        assert record["eventTime"] == "2026-03-01T12:00:00.000Z"

    def test_a_batch_of_notifications_yields_every_record_in_delivery_order(self):
        message = indexer_message(s3_event_record("a1/one.stl"), s3_event_record("a1/two.stl"),
                                  s3_event_record("a2/three.stl"))
        assert _keys(s3_records_from_indexer_message(message)) == [
            "a1/one.stl", "a1/two.stl", "a2/three.stl"]

    def test_the_json_string_form_of_the_message_is_accepted(self):
        message = json.dumps(indexer_message(s3_event_record("a1/model.stl")))
        assert _keys(s3_records_from_indexer_message(message)) == ["a1/model.stl"]

    def test_a_notification_carrying_several_records_is_flattened(self):
        message = {"Records": [sqs_record(sns_envelope({"Records": [
            s3_event_record("a1/one.stl"), s3_event_record("a1/two.stl")]}))]}
        assert _keys(s3_records_from_indexer_message(message)) == ["a1/one.stl", "a1/two.stl"]

    def test_the_nested_inner_notification_variant_is_unwrapped(self):
        message = nested_indexer_message(s3_event_record("a1/one.stl"), s3_event_record("a1/two.stl"))
        assert _keys(s3_records_from_indexer_message(message)) == ["a1/one.stl", "a1/two.stl"]

    def test_an_sqs_body_that_is_the_bare_s3_event_is_unwrapped(self):
        """A bucket that notifies its queue directly (no SNS hop) puts the S3 event in the body."""
        message = {"Records": [sqs_record({"Records": [s3_event_record("a1/model.stl")]})]}
        assert _keys(s3_records_from_indexer_message(message)) == ["a1/model.stl"]

    def test_a_body_already_parsed_to_a_dict_is_accepted(self):
        record = sqs_record(sns_envelope({"Records": [s3_event_record("a1/model.stl")]}))
        record["body"] = json.loads(record["body"])
        assert _keys(s3_records_from_indexer_message({"Records": [record]})) == ["a1/model.stl"]


@pytest.mark.unit
class TestTheOtherAcceptedShapes:

    def test_a_flat_s3_event(self):
        message = {"Records": [s3_event_record("a1/one.stl"), s3_event_record("a1/two.stl")],
                   "ASSET_BUCKET_NAME": BUCKET, "ASSET_BUCKET_PREFIX": ""}
        assert _keys(s3_records_from_indexer_message(message)) == ["a1/one.stl", "a1/two.stl"]

    def test_a_single_s3_record(self):
        record = s3_event_record("a1/model.stl")
        assert s3_records_from_indexer_message(record) == [record]

    def test_a_record_reduced_to_its_s3_block_still_counts(self):
        message = {"Records": [{"s3": {"object": {"key": "a1/model.stl"}}}]}
        assert _keys(s3_records_from_indexer_message(message)) == ["a1/model.stl"]

    def test_an_sns_event(self):
        message = {"Records": [{"EventSource": "aws:sns", "Sns": sns_envelope(
            {"Records": [s3_event_record("a1/model.stl")]})}]}
        assert _keys(s3_records_from_indexer_message(message)) == ["a1/model.stl"]

    def test_a_bare_sns_envelope(self):
        message = sns_envelope({"Records": [s3_event_record("a1/model.stl")]})
        assert _keys(s3_records_from_indexer_message(message)) == ["a1/model.stl"]

    def test_a_mixed_batch_flattens_every_record_once(self):
        message = {"Records": [
            s3_event_record("a1/flat.stl"),
            sqs_record(sns_envelope({"Records": [s3_event_record("a1/wrapped.stl")]})),
        ]}
        assert _keys(s3_records_from_indexer_message(message)) == ["a1/flat.stl", "a1/wrapped.stl"]


@pytest.mark.unit
class TestShapesThatAreNotS3Events:

    @pytest.mark.parametrize("message", [
        None, "", "not json", "[1, 2]", 42, [], [s3_event_record("a1/x.stl")],
        {}, {"hello": "world"},
        {"databaseId": "db1", "assetId": "a1"},
        {"eventName": "MODIFY", "dynamodb": {"NewImage": {"assetId": {"S": "a1"}}}},
        {"Records": []}, {"Records": "not a list"}, {"Records": [None, 3, "x"]},
        {"Records": [{"eventSource": "aws:dynamodb", "eventName": "INSERT", "dynamodb": {}}]},
        {"Type": "Notification"}, {"Type": "Notification", "Message": "not json"},
        {"Type": "Notification", "Message": json.dumps({"databaseId": "db1"})},
        {"s3": "not a dict"},
    ], ids=repr)
    def test_yield_nothing(self, message):
        assert s3_records_from_indexer_message(message) == []

    def test_an_sqs_body_wrapping_a_stream_record_yields_nothing(self):
        stream_record = {"eventSource": "aws:dynamodb", "eventName": "MODIFY",
                         "dynamodb": {"NewImage": {"assetId": {"S": "a1"}}}}
        message = {"Records": [sqs_record(sns_envelope(stream_record))]}
        assert s3_records_from_indexer_message(message) == []

    def test_an_unparsable_body_drops_only_its_own_record(self):
        broken = sqs_record({})
        broken["body"] = "{not json"
        message = {"Records": [broken,
                               sqs_record(sns_envelope({"Records": [s3_event_record("a1/ok.stl")]}))]}
        assert _keys(s3_records_from_indexer_message(message)) == ["a1/ok.stl"]
