# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The EDH tables' resource-name keys live in the add-on lane and resolve like every other handler table.

``AddonResourceKeys`` (``common/addonResourceNames.py``) declares the session table and the AMI recipe
table under ``addon/dynamoTables/``, mirrored by ``infra/common/addonResourceParamKeys.ts``. An EDH handler
resolves them with ``get_table_name`` at import, so the break-glass environment override is what lets the
tests collect without an SSM sweep.
"""

import os

import boto3
import pytest
from moto import mock_aws

PREFIX = "/vams-test/resourceNames"
# Each EDH key: its attribute, SSM key suffix and break-glass environment variable.
EDH_KEYS = (
    ("EDH_SESSION_STORAGE_TABLE", "addon/dynamoTables/edhSessionStorage", "EDH_SESSION_STORAGE_TABLE_NAME"),
    ("EDH_AMI_RECIPE_STORAGE_TABLE", "addon/dynamoTables/edhAmiRecipeStorage",
     "EDH_AMI_RECIPE_STORAGE_TABLE_NAME"),
)


@pytest.fixture
def rn(monkeypatch):
    """The resourceNames module with the prefix set, the EDH overrides cleared and its caches emptied."""
    monkeypatch.setenv("VAMS_RESOURCE_PARAM_PREFIX", PREFIX)
    for _attribute, _param_key, env_var in EDH_KEYS:
        monkeypatch.delenv(env_var, raising=False)
    import common.resourceNames as resourceNames
    resourceNames._cache = {}
    resourceNames._cache_fetched_at = 0.0
    resourceNames._ssm_client = None
    resourceNames._missing_keys = {}
    return resourceNames


@pytest.fixture
def keys():
    from common.addonResourceNames import AddonResourceKeys
    return AddonResourceKeys


@pytest.mark.unit
class TestEdhResourceKeys:
    @pytest.mark.parametrize("attribute, param_key, env_var", EDH_KEYS)
    def test_declares_the_add_on_key_and_its_env_alias(self, keys, attribute, param_key, env_var):
        key = getattr(keys, attribute)
        assert key.param_key == param_key
        assert key.env_var_names == (env_var,)

    @pytest.mark.parametrize("attribute, _param_key, _env_var", EDH_KEYS)
    def test_the_key_is_not_a_core_key(self, rn, attribute, _param_key, _env_var):
        assert not hasattr(rn.ResourceKeys, attribute)
        # Control: the core class is the real one.
        assert hasattr(rn.ResourceKeys, "ASSET_STORAGE_TABLE")

    @pytest.mark.parametrize("attribute, _param_key, env_var", EDH_KEYS)
    def test_env_override_wins_without_reaching_ssm(self, rn, keys, monkeypatch, attribute, _param_key, env_var):
        monkeypatch.setenv(env_var, "edh-override")
        monkeypatch.setattr(
            rn, "_refresh_cache",
            lambda: (_ for _ in ()).throw(RuntimeError("ssm must not be swept")),
        )
        assert rn.get_table_name(getattr(keys, attribute)) == "edh-override"

    @mock_aws
    @pytest.mark.parametrize("attribute, param_key, _env_var", EDH_KEYS)
    def test_resolves_from_ssm_when_no_override(self, rn, keys, attribute, param_key, _env_var):
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        ssm.put_parameter(Name=f"{PREFIX}/{param_key}", Value="ssm-edh-table", Type="String")
        assert rn.get_table_name(getattr(keys, attribute)) == "ssm-edh-table"

    @pytest.mark.parametrize("attribute, _param_key, env_var", EDH_KEYS)
    def test_the_edh_conftest_seeds_the_break_glass_name(self, keys, monkeypatch, attribute, _param_key, env_var):
        # The EDH modules resolve these tables at import, so collection is satisfied by the env override
        # the EDH conftest seeds rather than by an SSM sweep. The process environment is read as the
        # conftests left it (no `rn`, which clears the alias).
        import common.resourceNames as resourceNames

        seeded = os.environ.get(env_var)
        assert seeded
        monkeypatch.setattr(
            resourceNames, "_refresh_cache",
            lambda: (_ for _ in ()).throw(RuntimeError("ssm must not be swept")),
        )
        assert resourceNames.get_table_name(getattr(keys, attribute)) == seeded
