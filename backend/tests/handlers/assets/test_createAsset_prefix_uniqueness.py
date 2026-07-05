# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for create_asset prefix-uniqueness handling.

For API-driven creation (s3ExternalGenerated=False) an existing S3 prefix is a
conflict and must be rejected. For bucket-sync auto-creation
(s3ExternalGenerated=True) the prefix existing is the trigger — files were
placed directly in S3 — so creation binds onto the prefix (after ownership
check) and must not write a folder marker over the existing files.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_createAsset_conditional_put import _load


def _request_model(m):
    return m.CreateAssetRequestModel(
        databaseId="testdb1",
        assetId="asset-1",
        assetName="asset-1",
        description="prefix uniqueness test",
        isDistributable=True,
        tags=[],
    )


def _wire_create(m):
    """Stub everything create_asset touches up to the prefix branch."""
    m.asset_table = MagicMock()
    m.asset_table.get_item.return_value = {}  # no existing asset record
    m.database_table = MagicMock()
    m.database_table.get_item.return_value = {"Item": {"databaseId": "testdb1"}}
    m.get_default_bucket_details = MagicMock(return_value={
        "bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""
    })
    m.assert_existing_key_not_owned = MagicMock()
    m.create_prefix_folder = MagicMock()
    m.create_initial_version_record = MagicMock(return_value="v0")
    m.create_sns_topic_for_asset = MagicMock(return_value="arn:sns")
    m.save_asset_details = MagicMock()
    m.update_asset_count = MagicMock()
    m.validate_tags_exist = MagicMock(return_value=True)
    m.verify_all_required_tags_satisfied = MagicMock(return_value=True)


@pytest.mark.unit
class TestPrefixUniqueness:
    def test_api_create_rejects_existing_prefix(self):
        m = _load()
        _wire_create(m)
        m.check_s3_prefix_exists = MagicMock(return_value=True)
        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["user1"]})
        m.create_prefix_folder.assert_not_called()

    def test_s3_external_binds_to_existing_prefix(self):
        m = _load()
        _wire_create(m)
        m.check_s3_prefix_exists = MagicMock(return_value=True)
        response = m.create_asset(_request_model(m), {"tokens": ["SYSTEM_USER"]}, True)
        assert response.assetId == "asset-1"
        # Ownership check ran; no folder marker written over existing files
        m.assert_existing_key_not_owned.assert_called_once_with("b1", "asset-1/")
        m.create_prefix_folder.assert_not_called()

    def test_s3_external_rejects_owned_prefix(self):
        m = _load()
        _wire_create(m)
        m.check_s3_prefix_exists = MagicMock(return_value=True)
        m.assert_existing_key_not_owned.side_effect = m.VAMSGeneralErrorResponse(
            "location owned by another asset")
        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["SYSTEM_USER"]}, True)
        m.save_asset_details.assert_not_called()

    @pytest.mark.parametrize("s3_external", [False, True])
    def test_fresh_prefix_creates_folder_marker(self, s3_external):
        m = _load()
        _wire_create(m)
        m.check_s3_prefix_exists = MagicMock(return_value=False)
        response = m.create_asset(
            _request_model(m),
            {"tokens": ["SYSTEM_USER" if s3_external else "user1"]},
            s3_external,
        )
        assert response.assetId == "asset-1"
        m.create_prefix_folder.assert_called_once_with("bucket", "asset-1/")
