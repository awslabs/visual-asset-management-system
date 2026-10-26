# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The stream relay keeps the old image of a REMOVE record and drops it from INSERT and MODIFY.

The asset-link and metadata tables stream both images so that a REMOVE carries the deleted row
(the compliance trigger reads its assets, and whether a workflow execution deleted it, from there).
Nothing reads an INSERT or MODIFY record's old image, and keeping it would double every metadata
update's message toward the SNS size limit."""

import importlib.util
import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:test-indexer-topic")

_SNS_QUEUING_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing", "snsQueuing.py")


@pytest.fixture
def sns_queuing():
    with patch("boto3.client", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location(
            "snsQueuing_old_image_under_test", os.path.abspath(_SNS_QUEUING_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    module.sns_client.publish.return_value = {"MessageId": "m-1"}
    return module


def _record(event_name, with_old=True, with_new=True):
    data = {"Keys": {"assetLinkId": {"S": "l-1"}}, "SequenceNumber": "1",
            "ApproximateCreationDateTime": 1772366400}
    if with_new:
        data["NewImage"] = {"assetLinkId": {"S": "l-1"}, "toAssetId": {"S": "new"}}
    if with_old:
        data["OldImage"] = {"assetLinkId": {"S": "l-1"}, "toAssetId": {"S": "old"}}
    return {"eventID": "e-1", "eventName": event_name, "eventSource": "aws:dynamodb",
            "dynamodb": data}


def _published(module):
    return [json.loads(call.kwargs["Message"]) for call in module.sns_client.publish.call_args_list]


@pytest.mark.unit
class TestOldImageRelay:

    @pytest.mark.parametrize("event_name", ["INSERT", "MODIFY"])
    def test_an_insert_or_modify_is_published_without_its_old_image(self, sns_queuing, event_name):
        sns_queuing.lambda_handler({"Records": [_record(event_name)]}, MagicMock())
        (message,) = _published(sns_queuing)
        assert "OldImage" not in message["dynamodb"]
        assert message["dynamodb"]["NewImage"]["toAssetId"] == {"S": "new"}
        assert message["dynamodb"]["ApproximateCreationDateTime"] == 1772366400

    def test_a_remove_keeps_its_old_image(self, sns_queuing):
        sns_queuing.lambda_handler({"Records": [_record("REMOVE", with_new=False)]}, MagicMock())
        (message,) = _published(sns_queuing)
        assert message["dynamodb"]["OldImage"]["toAssetId"] == {"S": "old"}

    def test_a_record_of_a_new_image_table_is_published_unchanged(self, sns_queuing):
        record = _record("MODIFY", with_old=False)
        assert sns_queuing.relayed_record(record) is record

    def test_the_delivered_record_is_not_modified_in_place(self, sns_queuing):
        record = _record("MODIFY")
        sns_queuing.relayed_record(record)
        assert "OldImage" in record["dynamodb"]
