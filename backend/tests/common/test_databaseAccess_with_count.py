# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""DatabaseAccessManager.get_accessible_databases_with_count: (accessible ids, live rows scanned).

The module is loaded by file path with boto3 patched and ``handlers.authz`` stubbed, so the Casbin
decision per row is scripted and the scan is a fake paginator.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "common", "databaseAccess.py")


@pytest.fixture
def access_module():
    saved = sys.modules.get("handlers.authz")
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    try:
        with patch("boto3.client", return_value=MagicMock()), patch("boto3.resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location("databaseAccess_with_count_under_test", os.path.abspath(_MODULE_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        if saved is not None:
            sys.modules["handlers.authz"] = saved
    return module


def _row(database_id):
    return {"databaseId": {"S": database_id}, "description": {"S": "a database"}}


def _stub_scan(module, pages):
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    module.dynamodb_client.get_paginator.return_value = paginator
    return paginator


def test_with_count_returns_ids_and_scanned(access_module):
    _stub_scan(access_module, [{"Items": [_row("a"), _row("b")]}, {"Items": [_row("c")]}])
    enforcer = access_module.CasbinEnforcer.return_value
    enforcer.enforce.side_effect = lambda obj, *a, **k: obj["databaseId"] != "b"
    manager = access_module.DatabaseAccessManager()

    ids, scanned = manager.get_accessible_databases_with_count({"tokens": ["t"]})

    assert ids == ["a", "c"] and scanned == 3


def test_get_accessible_databases_is_the_first_element(access_module):
    _stub_scan(access_module, [{"Items": [_row("a"), _row("b"), _row("c")]}])
    enforcer = access_module.CasbinEnforcer.return_value
    enforcer.enforce.side_effect = lambda obj, *a, **k: obj["databaseId"] != "b"
    manager = access_module.DatabaseAccessManager()

    assert manager.get_accessible_databases({"tokens": ["t"]}) == ["a", "c"]


def test_no_tokens_counts_rows_but_grants_none(access_module):
    _stub_scan(access_module, [{"Items": [_row("a"), _row("b")]}])
    manager = access_module.DatabaseAccessManager()

    ids, scanned = manager.get_accessible_databases_with_count({"tokens": []})

    assert ids == [] and scanned == 2
    assert not access_module.CasbinEnforcer.called


def test_scan_failure_is_empty_and_zero(access_module):
    paginator = MagicMock()
    paginator.paginate.side_effect = RuntimeError("scan failed")
    access_module.dynamodb_client.get_paginator.return_value = paginator
    manager = access_module.DatabaseAccessManager()

    assert manager.get_accessible_databases_with_count({"tokens": ["t"]}) == ([], 0)


def test_show_deleted_flips_the_scan_filter_operator(access_module):
    paginator = _stub_scan(access_module, [{"Items": []}])
    manager = access_module.DatabaseAccessManager()

    manager.get_accessible_databases_with_count({"tokens": ["t"]}, show_deleted=True)

    assert paginator.paginate.call_count >= 1
    scan_filter = paginator.paginate.call_args.kwargs["ScanFilter"]
    assert scan_filter["databaseId"]["ComparisonOperator"] == "CONTAINS"
