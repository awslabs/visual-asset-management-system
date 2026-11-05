# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""SNS topic name helpers for asset notification topics.

SNS topic names allow only [A-Za-z0-9_-] and at most 256 characters. Asset IDs
may contain dots and spaces (filename_pattern / ASSET_ID). Both asset creation
and subscription recreate the topic from (databaseId, assetId), so the name
must be built in one place.
"""

import hashlib
import re

SNS_TOPIC_NAME_MAX_LEN = 256
_DISALLOWED_TOPIC_CHARS = re.compile(r'[^a-zA-Z0-9_-]')


def sanitize_asset_id_for_sns_topic(asset_id: str) -> str:
    """Return the asset-ID segment used in an SNS topic name.

    Already-safe IDs are unchanged so existing topics keep their names. When
    replacement changes the ID, append a short digest of the raw ID so IDs that
    only differ in replaced characters (a.b vs a-b vs "a b") do not share a
    topic.
    """
    safe = _DISALLOWED_TOPIC_CHARS.sub('-', asset_id)
    if safe == asset_id:
        return safe
    digest = hashlib.sha256(asset_id.encode('utf-8')).hexdigest()[:10]
    return f"{safe}-{digest}"


def asset_sns_topic_name(database_id: str, asset_id: str) -> str:
    """Build the full SNS topic name for an asset within a database namespace."""
    prefix = f"AssetTopic{database_id}-"
    segment = sanitize_asset_id_for_sns_topic(asset_id)
    budget = SNS_TOPIC_NAME_MAX_LEN - len(prefix)
    if budget < 1:
        # databaseId alone overflows the limit; still return a legal name.
        return prefix[:SNS_TOPIC_NAME_MAX_LEN]
    if len(segment) <= budget:
        return prefix + segment

    # Prefer keeping the collision-breaking digest suffix when present.
    digest_suffix_len = 11  # '-' + 10 hex chars
    if (
        segment != asset_id
        and len(segment) >= digest_suffix_len
        and segment[-digest_suffix_len] == '-'
        and budget > digest_suffix_len
    ):
        digest_suffix = segment[-digest_suffix_len:]
        head_budget = budget - digest_suffix_len
        return prefix + segment[:head_budget] + digest_suffix
    return prefix + segment[:budget]
