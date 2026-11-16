# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pin collision-safe SNS topic naming for asset IDs."""

import hashlib
import importlib

import pytest

snsTopicNames = importlib.import_module("common.snsTopicNames")


def _digest(asset_id: str) -> str:
    return hashlib.sha256(asset_id.encode('utf-8')).hexdigest()[:16]


@pytest.mark.unit
class TestAssetSnsTopicName:
    def test_already_safe_id_includes_digest(self):
        assert snsTopicNames.asset_sns_topic_name("db1", "asset_id-1") == (
            f"AssetTopicdb1-asset_id-1-{_digest('asset_id-1')}")

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
            f"AssetTopicdb1-a-b-{_digest('a-b')}",
            f"AssetTopicdb1-a-b-{_digest('a b')}",
        }

    def test_name_stays_within_sns_limit(self):
        asset_id = "a." + ("b" * 300)
        name = snsTopicNames.asset_sns_topic_name("db1", asset_id)
        assert len(name) <= snsTopicNames.SNS_TOPIC_NAME_MAX_LEN
        assert name.startswith("AssetTopicdb1-")
        assert name.endswith(_digest(asset_id))
        assert all(c.isalnum() or c in "_-" for c in name)

    @pytest.mark.parametrize("digest_length", [10, 16])
    def test_safe_id_matching_a_hashed_id_has_its_own_topic(self, digest_length):
        asset_id = "a.b"
        digest = hashlib.sha256(asset_id.encode('utf-8')).hexdigest()[:digest_length]
        chosen_id = f"a-b-{digest}"

        assert snsTopicNames.asset_sns_topic_name("db1", asset_id) != (
            snsTopicNames.asset_sns_topic_name("db1", chosen_id))

    def test_long_safe_ids_with_shared_prefix_keep_distinct_digest_suffixes(self):
        ids = ("a" * 243 + "x", "a" * 243 + "y")
        names = [snsTopicNames.asset_sns_topic_name("db1", asset_id) for asset_id in ids]

        assert names[0] != names[1]
        for asset_id, name in zip(ids, names):
            assert len(name) == 256
            assert name.endswith(f"-{_digest(asset_id)}")

    def test_maximum_database_id_preserves_digest_at_the_limit(self):
        asset_id = "x" * 256
        name = snsTopicNames.asset_sns_topic_name("d" * 63, asset_id)

        assert len(name) == 256
        assert name.startswith(f"AssetTopic{'d' * 63}-")
        assert name.endswith(f"-{_digest(asset_id)}")
