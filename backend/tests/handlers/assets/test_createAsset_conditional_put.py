# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

# Env vars createAsset requires at import time
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-versions-table")
os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-bucket")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("TAG_TYPES_STORAGE_TABLE_NAME", "test-tag-types-table")
os.environ.setdefault("TAG_STORAGE_TABLE_NAME", "test-tag-table")
os.environ.setdefault("AWS_REGION", "us-east-1")

# Absolute path to the real createAsset module file
_CREATE_ASSET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "createAsset.py"
)

_cached_module = None


def _load():
    """Load the real createAsset module by file path with boto3 stubbed.

    The mock handlers package registered by the root conftest shadows the real
    package, so a normal import cannot reach the real module. Load it directly
    from its file path with all heavy dependencies stubbed.
    """
    global _cached_module
    if _cached_module is not None:
        return _cached_module

    # Create stub modules for handlers that createAsset imports
    stub_names = (
        "handlers.assets.assetCount",
        "handlers.authz",
        "handlers.auth"
    )
    saved = {name: sys.modules.get(name) for name in stub_names}

    # Stub assetCount
    asset_count_stub = types.ModuleType("handlers.assets.assetCount")
    asset_count_stub.update_asset_count = MagicMock()
    sys.modules["handlers.assets.assetCount"] = asset_count_stub

    # Stub handlers.authz with CasbinEnforcer
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    # Stub handlers.auth with request_to_claims
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
                "createAsset_under_test", os.path.abspath(_CREATE_ASSET_PATH)
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
class TestSaveAssetDetailsConditional:
    def test_put_item_uses_condition_expression(self):
        m = _load()
        m.asset_table = MagicMock()
        m.save_asset_details({"databaseId": "db1", "assetId": "a1"})
        _, kwargs = m.asset_table.put_item.call_args
        # Must not silently overwrite an existing asset record.
        assert "ConditionExpression" in kwargs

    def test_conditional_failure_raises_general_error(self):
        m = _load()
        m.asset_table = MagicMock()
        m.asset_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )
        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.save_asset_details({"databaseId": "db1", "assetId": "a1"})
