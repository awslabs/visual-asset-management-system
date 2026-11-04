# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""SNS topic names reject dots; asset IDs may contain them. Pin the sanitize."""

from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_createAsset_conditional_put import _load


@pytest.mark.unit
class TestSnsTopicNameSanitizesAssetId:
    def test_dot_in_asset_id_becomes_hyphen_in_topic_name(self):
        m = _load(fresh=True)
        client = MagicMock()
        client.create_topic.return_value = {"TopicArn": "arn:sns:t"}
        m.sns_client = client

        arn = m.create_sns_topic_for_asset("testdb1", "x-r.1")

        assert arn == "arn:sns:t"
        client.create_topic.assert_called_once_with(Name="AssetTopictestdb1-x-r-1")

    def test_space_and_dot_are_sanitized(self):
        m = _load(fresh=True)
        client = MagicMock()
        client.create_topic.return_value = {"TopicArn": "arn:sns:t"}
        m.sns_client = client

        m.create_sns_topic_for_asset("testdb1", "Smoke Pump.v2")

        client.create_topic.assert_called_once_with(
            Name="AssetTopictestdb1-Smoke-Pump-v2")

    def test_already_safe_id_unchanged(self):
        m = _load(fresh=True)
        client = MagicMock()
        client.create_topic.return_value = {"TopicArn": "arn:sns:t"}
        m.sns_client = client

        m.create_sns_topic_for_asset("testdb1", "asset_id-1")

        client.create_topic.assert_called_once_with(
            Name="AssetTopictestdb1-asset_id-1")
