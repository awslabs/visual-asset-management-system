# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import os
import pytest
import boto3
from moto import mock_aws

PREFIX = "/vams-test/resourceNames"

ALL_OVERRIDE_ENV_VARS = [
    "ASSET_STORAGE_TABLE_NAME", "DATABASE_STORAGE_TABLE_NAME", "AUTH_TABLE_NAME",
    "AUTH_ENTITIES_TABLE", "TAG_STORAGE_TABLE_NAME", "TAGS_STORAGE_TABLE_NAME",
    "S3_ASSET_AUXILIARY_BUCKET", "ASSET_AUXILIARY_BUCKET_NAME",
    "S3_ASSETAUXILIARY_STORAGE_BUCKET", "AUDIT_LOG_AUTHENTICATION",
]


@pytest.fixture
def rn(monkeypatch):
    """Fresh resourceNames module with prefix set and known overrides cleared."""
    monkeypatch.setenv("VAMS_RESOURCE_PARAM_PREFIX", PREFIX)
    for var in ALL_OVERRIDE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    import common.resourceNames as resourceNames
    # Reset module state instead of reloading (reload doesn't work with import_module_from_path)
    resourceNames._cache = {}
    resourceNames._cache_fetched_at = 0.0
    resourceNames._ssm_client = None
    return resourceNames


def _put(ssm, key, value):
    ssm.put_parameter(Name=f"{PREFIX}/{key}", Value=value, Type="String")


@pytest.mark.unit
class TestEnvOverride:
    def test_env_var_override_wins_over_ssm(self, rn, monkeypatch):
        monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "override-table")
        assert rn.get_table_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "override-table"

    def test_alias_order_first_nonempty_wins(self, rn, monkeypatch):
        monkeypatch.setenv("TAG_STORAGE_TABLE_NAME", "tag-primary")
        monkeypatch.setenv("TAGS_STORAGE_TABLE_NAME", "tag-alias")
        assert rn.get_table_name(rn.ResourceKeys.TAG_STORAGE_TABLE) == "tag-primary"

    def test_alias_fallback_to_second_name(self, rn, monkeypatch):
        monkeypatch.setenv("TAGS_STORAGE_TABLE_NAME", "tag-alias")
        assert rn.get_table_name(rn.ResourceKeys.TAG_STORAGE_TABLE) == "tag-alias"


@pytest.mark.unit
@mock_aws
class TestSsmResolution:
    def test_fetches_from_ssm_when_no_override(self, rn):
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        _put(ssm, "dynamoTables/assetStorage", "ssm-asset-table")
        assert rn.get_table_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "ssm-asset-table"

    def test_batched_fetch_caches_sibling_keys(self, rn):
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        _put(ssm, "dynamoTables/assetStorage", "a")
        _put(ssm, "dynamoTables/databaseStorage", "b")
        rn.get_table_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)
        assert rn._cache["dynamoTables/databaseStorage"] == "b"

    def test_pagination_beyond_ten_parameters(self, rn):
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        for i in range(15):
            _put(ssm, f"dynamoTables/tbl{i}", f"v{i}")
        _put(ssm, "dynamoTables/assetStorage", "paged")
        assert rn.get_table_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "paged"
        assert len(rn._cache) == 16

    def test_missing_key_after_refresh_raises(self, rn):
        boto3.client("ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        with pytest.raises(Exception):
            rn.get_table_name(rn.ResourceKeys.ASSET_STORAGE_TABLE)

    def test_ttl_expiry_triggers_refresh(self, rn):
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        _put(ssm, "dynamoTables/assetStorage", "v1")
        assert rn.get_table_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "v1"
        ssm.put_parameter(Name=f"{PREFIX}/dynamoTables/assetStorage", Value="v2",
                          Type="String", Overwrite=True)
        rn._cache_fetched_at = 0.0
        assert rn.get_table_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "v2"

    def test_stale_served_when_refresh_fails(self, rn, monkeypatch):
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        _put(ssm, "dynamoTables/assetStorage", "v1")
        assert rn.get_table_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "v1"
        rn._cache_fetched_at = 0.0
        monkeypatch.setattr(rn, "_refresh_cache", lambda: (_ for _ in ()).throw(RuntimeError("ssm down")))
        assert rn.get_table_name(rn.ResourceKeys.ASSET_STORAGE_TABLE) == "v1"


@pytest.mark.unit
class TestConstantsCompleteness:
    def test_all_keys_have_param_key_and_env_names(self, rn):
        keys = [v for k, v in vars(rn.ResourceKeys).items() if isinstance(v, rn.ResourceParamKey)]
        # 41 base resources + 10 workflow-execution V2 tables + 6 pipeline/workflow V2 tables
        # + 2 legacy tag tables (tag/tagType migration sources for per-database namespacing)
        assert len(keys) == 59
        assert all(k.param_key and k.env_var_names for k in keys)
        assert len({k.param_key for k in keys}) == 59
