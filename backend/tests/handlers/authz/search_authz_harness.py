# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared harness for the search permission-filter tests: search.py loaded by path over the real authz
module, and real policies for one caller built from constraint fixtures."""

import contextlib
import copy
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import backend.backend.handlers.authz as authz_module

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_SEARCH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "search", "search.py"
)

USER_ID = "filter-user"
ROLE = "searcher"
OTHER_ROLE = "second"
CLAIMS = {"tokens": [USER_ID], "roles": [], "mfaEnabled": True}
ARCHIVED = "#deleted"


def _boto_client(name, *args, **kwargs):
    client = MagicMock()
    if name == "ssm":
        client.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}
    return client


def load_search_module(module_name):
    """search.py loaded by path with the real authz module and the asset-table helpers stubbed."""
    names = ("handlers.auth", "handlers.authz", "common.dynamodb")
    saved = {name: sys.modules.get(name) for name in names}
    sys.modules["handlers.authz"] = authz_module
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": [USER_ID]})
    sys.modules["handlers.auth"] = auth_stub
    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    dynamodb_stub.batch_get_items = MagicMock(return_value=([], []))
    sys.modules["common.dynamodb"] = dynamodb_stub
    try:
        with patch("boto3.client", side_effect=_boto_client), patch("boto3.resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location(module_name, os.path.abspath(_SEARCH_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
    return module


@contextlib.contextmanager
def policies(constraints, roles):
    """Real policy text and enforcer for USER_ID holding ``roles``, built from ``constraints``."""
    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}
    try:
        with patch.object(
            authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
            return_value=[{"userId": USER_ID, "roleName": role} for role in roles],
        ), patch.object(
            authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
            return_value=copy.deepcopy(list(constraints)),
        ):
            yield
    finally:
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}


def constraint(cid, criteria_and=(), criteria_or=(), effect="allow", group=ROLE, permission="GET",
               object_type="asset", users=()):
    return {
        "constraintId": cid,
        "objectType": object_type,
        "criteriaAnd": list(criteria_and),
        "criteriaOr": list(criteria_or),
        "groupPermissions": [{"groupId": group, "permission": permission, "permissionType": effect}]
        if group else [],
        "userPermissions": [{"userId": u, "permission": permission, "permissionType": effect} for u in users],
    }


def crit(field, operator, value):
    return {"field": field, "operator": operator, "value": value}


ALLOW_ALL = constraint("allow-all", [crit("databaseId", "contains", ".*")])
