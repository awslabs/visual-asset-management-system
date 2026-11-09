# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""SNS topic names reject dots; asset IDs may contain them. Pin the sanitize."""

import hashlib
from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_createAsset_conditional_put import _load


def _digest(asset_id: str) -> str:
    return hashlib.sha256(asset_id.encode('utf-8')).hexdigest()[:16]


@pytest.mark.unit
class TestSnsTopicNameSanitizesAssetId:
    def test_dot_in_asset_id_becomes_hyphen_in_topic_name(self):
        m = _load(fresh=True)
        client = MagicMock()
        client.create_topic.return_value = {"TopicArn": "arn:sns:t"}
        m.sns_client = client
        asset_id = "x-r.1"

        arn = m.create_sns_topic_for_asset("testdb1", asset_id)

        assert arn == "arn:sns:t"
        client.create_topic.assert_called_once_with(
            Name=f"AssetTopictestdb1-x-r-1-{_digest(asset_id)}")

    def test_space_and_dot_are_sanitized(self):
        m = _load(fresh=True)
        client = MagicMock()
        client.create_topic.return_value = {"TopicArn": "arn:sns:t"}
        m.sns_client = client
        asset_id = "Smoke Pump.v2"

        m.create_sns_topic_for_asset("testdb1", asset_id)

        client.create_topic.assert_called_once_with(
            Name=f"AssetTopictestdb1-Smoke-Pump-v2-{_digest(asset_id)}")

    def test_already_safe_id_includes_digest(self):
        m = _load(fresh=True)
        client = MagicMock()
        client.create_topic.return_value = {"TopicArn": "arn:sns:t"}
        m.sns_client = client

        m.create_sns_topic_for_asset("testdb1", "asset_id-1")

        client.create_topic.assert_called_once_with(
            Name=f"AssetTopictestdb1-asset_id-1-{_digest('asset_id-1')}")

    def test_sanitized_ids_that_would_collide_get_distinct_names(self):
        m = _load(fresh=True)
        client = MagicMock()
        client.create_topic.return_value = {"TopicArn": "arn:sns:t"}
        m.sns_client = client

        m.create_sns_topic_for_asset("testdb1", "a.b")
        m.create_sns_topic_for_asset("testdb1", "a-b")
        m.create_sns_topic_for_asset("testdb1", "a b")

        names = [call.kwargs["Name"] for call in client.create_topic.call_args_list]
        assert names[0] == f"AssetTopictestdb1-a-b-{_digest('a.b')}"
        assert names[1] == f"AssetTopictestdb1-a-b-{_digest('a-b')}"
        assert names[2] == f"AssetTopictestdb1-a-b-{_digest('a b')}"
        assert len(set(names)) == 3
