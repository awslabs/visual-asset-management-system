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
    """Return a topic-safe asset ID with a digest of the complete raw ID."""
    safe = _DISALLOWED_TOPIC_CHARS.sub('-', asset_id)
    digest = hashlib.sha256(asset_id.encode('utf-8')).hexdigest()[:16]
    return f"{safe}-{digest}"


def asset_sns_topic_name(database_id: str, asset_id: str) -> str:
    """Build the full SNS topic name for an asset within a database namespace."""
    prefix = f"AssetTopic{database_id}-"
    segment = sanitize_asset_id_for_sns_topic(asset_id)
    digest_suffix_len = 17  # '-' + 16 hex chars
    digest_suffix = segment[-digest_suffix_len:]
    readable_name = prefix + segment[:-digest_suffix_len]
    return readable_name[:SNS_TOPIC_NAME_MAX_LEN - digest_suffix_len] + digest_suffix
