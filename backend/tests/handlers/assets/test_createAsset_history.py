# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests that create_asset writes an asset lifecycle history record."""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Env vars createAsset requires at import time
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-versions-table")
os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-bucket")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("TAG_TYPES_STORAGE_TABLE_NAME", "test-tag-types-table")
os.environ.setdefault("TAG_STORAGE_TABLE_NAME", "test-tag-table")
os.environ.setdefault("ASSET_HISTORY_STORAGE_TABLE_NAME", "test-asset-history-table")
os.environ.setdefault("AWS_REGION", "us-east-1")

# Absolute path to the real createAsset module file
_CREATE_ASSET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "createAsset.py"
)

_cached_module = None


def _load():
    """Load the real createAsset module by file path with boto3 stubbed."""
    global _cached_module
    if _cached_module is not None:
        return _cached_module

    stub_names = (
        "handlers.assets.assetCount",
        "handlers.authz",
        "handlers.auth"
    )
    saved = {name: sys.modules.get(name) for name in stub_names}

    asset_count_stub = types.ModuleType("handlers.assets.assetCount")
    asset_count_stub.update_asset_count = MagicMock()
    sys.modules["handlers.assets.assetCount"] = asset_count_stub

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock()
    sys.modules["handlers.auth"] = auth_stub

    # models.assetsV3 imports bucket_existing_key_pattern from common.validators;
    # the mock validators module doesn't define it. Add it for the load.
    validators_mod = sys.modules.get("common.validators")
    added_pattern = False
    if validators_mod is not None and not hasattr(validators_mod, "bucket_existing_key_pattern"):
        validators_mod.bucket_existing_key_pattern = r'^[a-zA-Z0-9._\-/]{1,1024}$'
        added_pattern = True

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "createAsset_history_under_test", os.path.abspath(_CREATE_ASSET_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
        if added_pattern and validators_mod is not None:
            delattr(validators_mod, "bucket_existing_key_pattern")
    _cached_module = module
    return module


@pytest.mark.unit
class TestCreateAssetHistory:
    def _prepare(self, m):
        """Stub collaborators so create_asset() reaches the history hook."""
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {}
        m.database_table = MagicMock()
        m.database_table.get_item.return_value = {"Item": {"databaseId": "testdb1"}}
        m.validate_tags_exist = MagicMock(return_value=True)
        m.verify_all_required_tags_satisfied = MagicMock(return_value=True)
        m.get_default_bucket_details = MagicMock(return_value={
            "bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""
        })
        m.check_s3_prefix_exists = MagicMock(return_value=False)
        m.create_prefix_folder = MagicMock()
        m.create_sns_topic_for_asset = MagicMock(return_value="arn:sns:topic")
        m.create_initial_version_record = MagicMock(return_value="0")
        m.save_asset_details = MagicMock()
        m.update_asset_count = MagicMock()
        m.write_asset_history_record = MagicMock()

    def test_api_create_writes_create_record(self):
        m = _load()
        self._prepare(m)
        request = m.CreateAssetRequestModel(
            databaseId="testdb1", assetName="Asset One",
            description="test description", isDistributable=True, tags=[]
        )
        m.create_asset(request, {"tokens": ["user1"]}, False)

        m.write_asset_history_record.assert_called_once()
        args = m.write_asset_history_record.call_args[0]
        assert args[0] == "testdb1"
        assert args[2] == m.CHANGE_SOURCE_CREATE
        assert args[3] == "user1"
        assert args[4]["assetName"] == "Asset One"

    def test_bucket_sync_create_writes_create_direct_record(self):
        m = _load()
        self._prepare(m)
        request = m.CreateAssetRequestModel(
            databaseId="testdb1", assetName="Asset One",
            description="test description", isDistributable=True, tags=[]
        )
        m.create_asset(request, {"tokens": ["SYSTEM_USER"]}, True)

        args = m.write_asset_history_record.call_args[0]
        assert args[2] == m.CHANGE_SOURCE_CREATE_DIRECT
        assert args[3] == "SYSTEM_USER"
