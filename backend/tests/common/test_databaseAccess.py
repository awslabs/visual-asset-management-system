# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The database pre-filter every search route applies before touching OpenSearch or the vector store.

Loaded by file path with boto3 stubbed: the module builds its DynamoDB clients and resolves the database
table name at import (backend/CLAUDE.md Rules 6 and 10), and the root conftest shadows
``common.databaseAccess`` with a mock stand-in for the handlers that do not exercise it.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "backend", "common", "databaseAccess.py"
)


class _EnforcerSpy:
    """Records every document handed to enforce() and grants access to one database."""

    documents = []
    granted = "smoke-db"

    def __init__(self, claims_and_roles):
        self.claims_and_roles = claims_and_roles

    def enforce(self, document, action):
        _EnforcerSpy.documents.append(dict(document))
        return document.get("databaseId") == _EnforcerSpy.granted


@pytest.fixture
def loaded():
    """(module, boto3.client calls) for the real module loaded by path."""
    client_calls = []

    def _client(name, *args, **kwargs):
        client_calls.append((name, kwargs))
        return MagicMock()

    saved = sys.modules.get("handlers.authz")
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = _EnforcerSpy
    sys.modules["handlers.authz"] = authz_stub
    _EnforcerSpy.documents = []
    try:
        with patch("boto3.client", side_effect=_client), patch("boto3.resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location("databaseAccess_under_test", os.path.abspath(_MODULE_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        if saved is not None:
            sys.modules["handlers.authz"] = saved
    return module, client_calls


def _row(database_id):
    return {"databaseId": {"S": database_id}, "description": {"S": "a database"}}


def _stub_scan(module, pages):
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    module.dynamodb_client.get_paginator.return_value = paginator
    return paginator


CLAIMS = {"tokens": ["user"], "roles": ["role"]}


@pytest.mark.unit
class TestModuleLevelWiring:
    def test_clients_carry_the_shared_retry_config(self, loaded):
        module, calls = loaded
        assert [name for name, _ in calls] == ["dynamodb"]
        assert calls[0][1]["config"] is module.retry_config

    def test_table_name_resolves_through_the_env_override(self, loaded):
        module, _ = loaded
        assert module.database_storage_table_name == os.environ["DATABASE_STORAGE_TABLE_NAME"]

    def test_the_class_binds_the_module_enforcer(self, loaded):
        module, _ = loaded
        assert module.CasbinEnforcer is _EnforcerSpy

    def test_the_patch_points_are_plain_module_attributes(self, loaded):
        """The four names the NLP search handler's tests patch on this module (registry §3.3)."""
        module, _ = loaded
        assert {"dynamodb_client", "database_storage_table_name", "CasbinEnforcer", "logger"} <= set(vars(module))


@pytest.mark.unit
class TestGetAccessibleDatabases:
    def test_returns_the_databases_casbin_grants_and_types_rows_as_databases(self, loaded):
        module, _ = loaded
        _stub_scan(module, [{"Items": [_row("smoke-db"), _row("other-db")]}])
        assert module.DatabaseAccessManager.get_accessible_databases(CLAIMS) == ["smoke-db"]
        assert [d["object__type"] for d in _EnforcerSpy.documents] == ["database", "database"]
        assert _EnforcerSpy.documents[0]["description"] == "a database"  # deserialized, not raw

    def test_no_tokens_means_no_enforcement_and_no_access(self, loaded):
        module, _ = loaded
        _stub_scan(module, [{"Items": [_row("smoke-db")]}])
        assert module.DatabaseAccessManager.get_accessible_databases({"tokens": []}) == []
        assert _EnforcerSpy.documents == []

    def test_scan_excludes_archived_partitions_by_default(self, loaded):
        module, _ = loaded
        paginator = _stub_scan(module, [{"Items": []}])
        module.DatabaseAccessManager.get_accessible_databases(CLAIMS)
        kwargs = paginator.paginate.call_args.kwargs
        assert kwargs["TableName"] == module.database_storage_table_name
        assert kwargs["ScanFilter"]["databaseId"]["ComparisonOperator"] == "NOT_CONTAINS"
        assert kwargs["PaginationConfig"] == {"PageSize": 100, "MaxItems": 10000}

    def test_show_deleted_scans_the_archived_partitions(self, loaded):
        module, _ = loaded
        paginator = _stub_scan(module, [{"Items": []}])
        module.DatabaseAccessManager.get_accessible_databases(CLAIMS, show_deleted=True, max_databases=7)
        kwargs = paginator.paginate.call_args.kwargs
        assert kwargs["ScanFilter"]["databaseId"]["ComparisonOperator"] == "CONTAINS"
        assert kwargs["PaginationConfig"]["MaxItems"] == 7

    def test_max_databases_caps_the_result(self, loaded):
        module, _ = loaded
        _EnforcerSpy.granted = "smoke-db"
        _stub_scan(module, [{"Items": [_row("smoke-db"), _row("smoke-db"), _row("smoke-db")]}])
        assert module.DatabaseAccessManager.get_accessible_databases(CLAIMS, max_databases=2) == ["smoke-db", "smoke-db"]

    def test_a_scan_failure_degrades_to_no_access(self, loaded):
        module, _ = loaded
        module.dynamodb_client.get_paginator.side_effect = RuntimeError("boom")
        assert module.DatabaseAccessManager.get_accessible_databases(CLAIMS) == []


@pytest.mark.unit
class TestGetAccessibleDatabase:
    def test_granted_database_is_returned(self, loaded):
        module, _ = loaded
        module.database_storage_table = MagicMock()
        module.database_storage_table.get_item.return_value = {"Item": {"databaseId": "smoke-db"}}
        assert module.DatabaseAccessManager.get_accessible_database("smoke-db", CLAIMS) == "smoke-db"
        assert _EnforcerSpy.documents == [{"databaseId": "smoke-db", "object__type": "database"}]

    def test_ungranted_database_is_denied(self, loaded):
        module, _ = loaded
        module.database_storage_table = MagicMock()
        module.database_storage_table.get_item.return_value = {"Item": {"databaseId": "other-db"}}
        assert module.DatabaseAccessManager.get_accessible_database("other-db", CLAIMS) is None

    def test_missing_row_is_denied_without_enforcement(self, loaded):
        module, _ = loaded
        module.database_storage_table = MagicMock()
        module.database_storage_table.get_item.return_value = {}
        assert module.DatabaseAccessManager.get_accessible_database("gone", CLAIMS) is None
        assert _EnforcerSpy.documents == []
