# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The sample indexer-topic messages the infra tests run the trigger queue's SNS filter policies
over (infra/test/platform/fixtures/indexerTopicMessages.json) are the messages the publishers send
and the trigger reads:

- each stream-record sample is published unchanged by the stream relay (snsQueuing drops only an
  INSERT's or MODIFY's OldImage, which the samples do not carry);
- each S3 sample carries the S3 records the trigger unwraps (`s3_records_from_indexer_message`);
- the trigger routes each sample to the handler for its table or to the file-event path, never to
  "Unrecognized message format".

So a change to a publisher's message shape that the filter policies were not re-derived for fails
here or in the infra test."""

import importlib.util
import json
import os
import pathlib
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:test-indexer-topic")

from common.indexerEvents import s3_records_from_indexer_message  # noqa: E402
from handlers.compliance import complianceTrigger as trigger  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[4]
SAMPLES = json.loads((REPO / "infra" / "test" / "platform" / "fixtures" /
                      "indexerTopicMessages.json").read_text(encoding="utf-8"))["samples"]
SNS_QUEUING = REPO / "backend" / "backend" / "handlers" / "indexing" / "snsQueuing.py"

STREAM_SAMPLES = [s for s in SAMPLES if s["body"].get("eventSource") == "aws:dynamodb"]
S3_SAMPLES = [s for s in SAMPLES if s not in STREAM_SAMPLES]

# The table each stream sample comes from, as the trigger tells it apart by its keys.
EXPECTED_KIND = {
    "AssetStorageTable": trigger.RECORD_KIND_ASSET,
    "AssetFileMetadataStorageTableV2": trigger.RECORD_KIND_METADATA,
    "FileAttributeStorageTableV2": trigger.RECORD_KIND_FILE_ATTRIBUTE,
    "AssetLinksStorageTableV2.2": trigger.RECORD_KIND_LINK,
    "AssetLinksMetadataStorageTable": trigger.RECORD_KIND_LINK_METADATA,
}


def _table(sample):
    return sample["body"]["eventSourceARN"].split(":table/")[1].split("/stream/")[0]


@pytest.fixture(scope="module")
def relay():
    with patch("boto3.client", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location("snsQueuing_topic_samples", SNS_QUEUING)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestTheSamplesAreThePublishedMessages:

    def test_the_fixture_holds_both_kinds(self):
        assert STREAM_SAMPLES and S3_SAMPLES
        assert {_table(s) for s in STREAM_SAMPLES} == set(EXPECTED_KIND)

    @pytest.mark.parametrize("sample", STREAM_SAMPLES, ids=lambda s: s["name"])
    def test_the_relay_publishes_each_stream_sample_unchanged(self, relay, sample):
        published = json.loads(json.dumps(relay.relayed_record(sample["body"]), default=str))
        assert published == sample["body"]
        # An INSERT or MODIFY sample is what the relay leaves after dropping the old image.
        if sample["body"]["eventName"] != "REMOVE":
            with_old = {**sample["body"], "dynamodb": {**sample["body"]["dynamodb"],
                                                       "OldImage": {"x": {"S": "old"}}}}
            assert relay.relayed_record(with_old) == sample["body"]

    @pytest.mark.parametrize("sample", S3_SAMPLES, ids=lambda s: s["name"])
    def test_each_s3_sample_carries_the_records_the_trigger_unwraps(self, sample):
        records = s3_records_from_indexer_message(sample["body"])
        assert records and all(r["eventSource"] == "aws:s3" for r in records)
        assert set(sample["body"]) == {"Records", "ASSET_BUCKET_NAME", "ASSET_BUCKET_PREFIX"}


@pytest.mark.unit
class TestTheTriggerRoutesEverySample:

    @pytest.mark.parametrize("sample", STREAM_SAMPLES, ids=lambda s: s["name"])
    def test_a_stream_sample_reaches_the_handler_of_its_table(self, sample):
        assert trigger.stream_record_kind(sample["body"]["dynamodb"]) == EXPECTED_KIND[_table(sample)]
        with patch.object(trigger, "_process_stream_record") as asset, \
                patch.object(trigger, "_process_indexed_change") as indexed, \
                patch.object(trigger, "_process_file_event") as file_event, \
                patch.object(trigger.logger, "info") as info:
            trigger._dispatch_event(sample["body"], batch=MagicMock())
        assert asset.call_count + indexed.call_count == 1
        file_event.assert_not_called()
        assert all("Unrecognized" not in str(c.args[0]) for c in info.call_args_list)

    @pytest.mark.parametrize("sample", S3_SAMPLES, ids=lambda s: s["name"])
    def test_an_s3_sample_reaches_the_file_event_path(self, sample):
        with patch.object(trigger, "_process_file_event") as file_event, \
                patch.object(trigger, "_process_stream_record") as asset, \
                patch.object(trigger, "_process_indexed_change") as indexed:
            trigger._dispatch_event(sample["body"], batch=MagicMock())
        file_event.assert_called_once()
        asset.assert_not_called()
        indexed.assert_not_called()
