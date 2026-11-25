# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The per-upload staging key (#1928): ``{baseAssetsPrefix}temp-uploads/{upload tag}/{final key}``.

Each upload stages its files in its own folder, so concurrent uploads of one path never share a staged
object. The tag is a fixed 12 hex characters derived from the upload id, which bounds what the folder
costs a key against the 1,024-byte S3 limit, and the key stays under the reserved ``temp-uploads``
folder, so every consumer that skips system data (bucket sync, the indexers, the triggers) skips it.
"""

import hashlib

import pytest

from backend.backend.common.s3PathPatterns import (
    TEMPORARY_UPLOAD_PREFIX,
    key_has_reserved_segment,
    legacy_staged_upload_key,
    staged_upload_key,
    upload_staging_tag,
)

UPLOAD_ID = "y0b7f3c52-9d0e-4f6a-8a51-2c4e7d9b1a63"


@pytest.mark.unit
class TestTheStagingKey:
    def test_it_is_the_final_key_in_the_upload_folder_under_the_prefix(self):
        tag = hashlib.sha256(UPLOAD_ID.encode("utf-8")).hexdigest()[:12]
        assert staged_upload_key("", UPLOAD_ID, "asset-1/scans/pump.glb") == \
            f"temp-uploads/{tag}/asset-1/scans/pump.glb"
        assert staged_upload_key("assets/", UPLOAD_ID, "assets/asset-1/scans/pump.glb") == \
            f"assets/temp-uploads/{tag}/assets/asset-1/scans/pump.glb"

    @pytest.mark.parametrize("upload_id", [UPLOAD_ID, "y" + "9" * 200, "y\u00e9\u4e2d-id", "x"])
    def test_the_tag_is_12_hex_characters_whatever_the_upload_id(self, upload_id):
        tag = upload_staging_tag(upload_id)
        assert len(tag) == 12
        assert set(tag) <= set("0123456789abcdef")

    def test_two_uploads_of_one_path_get_different_keys(self):
        other = "y5d1e0c9a-0000-4000-8000-000000000001"
        assert staged_upload_key("", UPLOAD_ID, "a/m.glb") != staged_upload_key("", other, "a/m.glb")

    def test_the_folder_costs_a_key_13_bytes_more_than_the_per_path_key(self):
        final_key = "asset-1/scans/pump.glb"
        assert len(staged_upload_key("", UPLOAD_ID, final_key)) - len(legacy_staged_upload_key("", final_key)) == 13
        assert len(legacy_staged_upload_key("", final_key)) == len(TEMPORARY_UPLOAD_PREFIX) + len(final_key)

    @pytest.mark.parametrize("base", ["", "assets/", "deep/area/"])
    def test_the_key_is_system_data_for_every_consumer_that_skips_it(self, base):
        key = staged_upload_key(base, UPLOAD_ID, f"{base}asset-1/scans/pump.glb")
        assert key.startswith(f"{base}{TEMPORARY_UPLOAD_PREFIX}")
        assert key_has_reserved_segment(key, base)
