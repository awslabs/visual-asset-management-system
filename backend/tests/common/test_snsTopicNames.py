# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pin collision-safe SNS topic naming for asset IDs."""

import hashlib
import importlib

import pytest

snsTopicNames = importlib.import_module("common.snsTopicNames")


def _digest(asset_id: str) -> str:
    return hashlib.sha256(asset_id.encode('utf-8')).hexdigest()[:10]


@pytest.mark.unit
class TestAssetSnsTopicName:
    def test_already_safe_id_keeps_historical_name(self):
        assert snsTopicNames.asset_sns_topic_name("db1", "asset_id-1") == (
            "AssetTopicdb1-asset_id-1")

    def test_disallowed_chars_replaced_and_digested(self):
        assert snsTopicNames.asset_sns_topic_name("db1", "x-r.1") == (
            f"AssetTopicdb1-x-r-1-{_digest('x-r.1')}")

    def test_colliding_sanitized_ids_are_distinct(self):
        names = {
            snsTopicNames.asset_sns_topic_name("db1", "a.b"),
            snsTopicNames.asset_sns_topic_name("db1", "a-b"),
            snsTopicNames.asset_sns_topic_name("db1", "a b"),
        }
        assert names == {
            f"AssetTopicdb1-a-b-{_digest('a.b')}",
            "AssetTopicdb1-a-b",
            f"AssetTopicdb1-a-b-{_digest('a b')}",
        }

    def test_name_stays_within_sns_limit(self):
        asset_id = "a." + ("b" * 300)
        name = snsTopicNames.asset_sns_topic_name("db1", asset_id)
        assert len(name) <= snsTopicNames.SNS_TOPIC_NAME_MAX_LEN
        assert name.startswith("AssetTopicdb1-")
        assert name.endswith(_digest(asset_id))
        assert all(c.isalnum() or c in "_-" for c in name)
