# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import os
import re
from pathlib import Path

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


# Canonical SSM key registry that ResourceKeys in common/resourceNames.py mirrors.
CANONICAL_KEYS_TS = (
    Path(__file__).resolve().parents[3] / "infra" / "common" / "resourceParamKeys.ts"
)

# Categories only deployment and data-migration tooling resolves (through
# infra/deploymentDataMigration/tools/ssm_resource_lookup.py), so no handler mirrors them.
TOOLING_ONLY_PREFIXES = ("lambdaFunctions/",)

# Deprecated tables whose only consumer is a migration script. Named one by one rather than by
# the dynamoTables/legacy/ prefix: a legacy key added upstream must be classified deliberately,
# not excused for sitting under that prefix.
TOOLING_ONLY_LEGACY_KEYS = {
    "dynamoTables/legacy/assetVersionsStorageV1",
    "dynamoTables/legacy/assetFileVersionsStorageV1",
    "dynamoTables/legacy/assetLinksStorage",
    "dynamoTables/legacy/metadataStorage",
    "dynamoTables/legacy/metadataSchemaStorage",
}

# Legacy keys the mirror carries. No handler resolves either one -- the per-database tag
# namespacing migration reads them through ssm_resource_lookup.py -- so in ResourceKeys they are
# declared but unused. They stay mirrored because dropping them is a three-way contract change
# (registry, mirror, migration lookup) and an owner decision, not a cleanup.
MIRRORED_LEGACY_KEYS = {
    "dynamoTables/legacy/tagStorage",
    "dynamoTables/legacy/tagTypeStorage",
}

# A `name: "value"` entry in a TypeScript object literal.
TS_ASSIGNMENT = re.compile(r'[A-Za-z][A-Za-z0-9_]*\s*:\s*"([^"]+)"')
# A param key suffix: two or more '/'-joined alphanumeric segments.
PARAM_KEY_SHAPE = re.compile(r"^[A-Za-z0-9]+(?:/[A-Za-z0-9]+)+$")


def _canonical_param_keys():
    """Every param key published by infra/common/resourceParamKeys.ts.

    Reads the registry's values without naming its categories, so a category added upstream (a
    `sqsQueues` block, say) is compared against the mirror rather than skipped.
    """
    source = CANONICAL_KEYS_TS.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"//[^\n]*", "", source)
    return {v for v in TS_ASSIGNMENT.findall(source) if PARAM_KEY_SHAPE.match(v)}


def _canonical_categories(keys):
    """Leading path segment of each key -- the registry's category names."""
    return {key.split("/", 1)[0] for key in keys}


def _is_tooling_only(key):
    return key.startswith(TOOLING_ONLY_PREFIXES) or key in TOOLING_ONLY_LEGACY_KEYS


def _mirror_keys(rn):
    """Every ResourceParamKey declared on ResourceKeys."""
    return [v for v in vars(rn.ResourceKeys).values() if isinstance(v, rn.ResourceParamKey)]


@pytest.mark.unit
class TestConstantsCompleteness:
    def test_all_keys_have_param_key_and_env_names(self, rn):
        keys = _mirror_keys(rn)
        assert keys
        assert all(k.param_key and k.env_var_names for k in keys)

    def test_param_keys_are_unique(self, rn):
        keys = _mirror_keys(rn)
        # Checked on its own: a duplicated constant leaves the set comparison below intact.
        assert len({k.param_key for k in keys}) == len(keys)

    def test_canonical_registry_reads_as_a_multi_category_union(self):
        canonical = _canonical_param_keys()
        # Control for one side of the comparison below: a parser that stops matching, or one that
        # reads a single category, would make the set equality trivially satisfiable.
        assert len(canonical) > 50
        categories = _canonical_categories(canonical)
        assert len(categories) >= 4
        assert {"dynamoTables", "s3Buckets", "cloudwatchLogGroups"} <= categories
        assert TOOLING_ONLY_PREFIXES[0].rstrip("/") in categories
        # Named samples across the mirrored categories, so the floor above cannot be met by one
        # block of the registry alone.
        assert {
            "dynamoTables/assetStorage",
            "s3Buckets/assetAuxiliary",
            "cloudwatchLogGroups/auditErrors",
        } <= canonical

    def test_mirrors_the_canonical_param_key_registry(self, rn):
        canonical = _canonical_param_keys()
        mirrored = {k.param_key for k in _mirror_keys(rn)}
        # A parser or an attribute filter that stops matching would compare two empty sets and
        # report success, so floor both sides before comparing them.
        assert len(canonical) > 50
        assert len(mirrored) > 50
        assert mirrored == {key for key in canonical if not _is_tooling_only(key)}

    def test_omitted_keys_are_tooling_only_and_still_published(self, rn):
        canonical = _canonical_param_keys()
        omitted = {key for key in canonical if _is_tooling_only(key)}
        # A stale omission entry (renamed or deleted upstream) is itself drift.
        assert TOOLING_ONLY_LEGACY_KEYS <= canonical
        assert MIRRORED_LEGACY_KEYS <= canonical
        # Every prefix-based exclusion must still cover a live key, or it excuses nothing.
        for prefix in TOOLING_ONLY_PREFIXES:
            assert any(key.startswith(prefix) for key in canonical)
        assert omitted
        assert omitted < canonical
        assert not omitted & {k.param_key for k in _mirror_keys(rn)}

    def test_mirrors_both_legacy_tag_keys(self, rn):
        # Pinned rather than derived: no handler resolves them (see MIRRORED_LEGACY_KEYS), so
        # removing them is a deliberate registry + mirror + ssm_resource_lookup.py change.
        assert MIRRORED_LEGACY_KEYS <= {k.param_key for k in _mirror_keys(rn)}
