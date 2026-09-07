# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the assetHistory lookup handler (GET .../assetHistory)."""

import base64
import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_HISTORY_STORAGE_TABLE_NAME", "test-asset-history-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")

_ASSET_HISTORY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "assetHistory.py"
)

_cached_module = None


def _load():
    """Load the real assetHistory module by file path with boto3 stubbed."""
    global _cached_module
    if _cached_module is not None:
        return _cached_module

    stub_names = ("handlers.authz", "handlers.auth")
    saved = {name: sys.modules.get(name) for name in stub_names}

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["tester"]})
    sys.modules["handlers.auth"] = auth_stub

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "assetHistory_under_test", os.path.abspath(_ASSET_HISTORY_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
    _cached_module = module
    return module


def _event(db="db1", aid="a1", query=None):
    return {
        "requestContext": {"http": {
            "method": "GET",
            "path": f"/database/{db}/assets/{aid}/assetHistory",
        }},
        "pathParameters": {"databaseId": db, "assetId": aid},
        "queryStringParameters": query or {},
        "headers": {"authorization": "Bearer test"},
    }


def _authorize(m, allowed=True):
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.return_value = allowed
    m.CasbinEnforcer = MagicMock(return_value=enforcer)
    m.request_to_claims = MagicMock(return_value={"tokens": ["u1"], "roles": []})
    return enforcer


@pytest.mark.unit
class TestAssetHistoryHandler:
    def _items(self):
        return [
            {"databaseId:assetId": "db1:a1", "historyRecordId": "2026-07-05T02:00:00Z#bbbbbbbb",
             "databaseId": "db1", "assetId": "a1", "recordDate": "2026-07-05T02:00:00Z",
             "changeSource": "edit", "changeUserId": "u1", "assetSnapshot": {"assetName": "B"}},
            {"databaseId:assetId": "db1:a1", "historyRecordId": "2026-07-05T01:00:00Z#aaaaaaaa",
             "databaseId": "db1", "assetId": "a1", "recordDate": "2026-07-05T01:00:00Z",
             "changeSource": "create", "changeUserId": "u1", "assetSnapshot": {"assetName": "A"}},
        ]

    def test_happy_path_newest_first(self):
        m = _load()
        _authorize(m)
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {"Item": {"databaseId": "db1", "assetId": "a1"}}
        m.history_table = MagicMock()
        m.history_table.query.return_value = {"Items": self._items()}

        response = m.lambda_handler(_event(), MagicMock())

        assert response["statusCode"] == 200
        query_kwargs = m.history_table.query.call_args[1]
        assert query_kwargs["ScanIndexForward"] is False
        assert query_kwargs["Limit"] == 100
        body = json.loads(response["body"])
        assert [r["changeSource"] for r in body["Items"]] == ["edit", "create"]
        assert body["Items"][0]["assetSnapshot"] == {"assetName": "B"}
        assert body.get("NextToken") is None

    def test_pagination_token_round_trip(self):
        m = _load()
        _authorize(m)
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {"Item": {"databaseId": "db1", "assetId": "a1"}}
        m.history_table = MagicMock()
        last_key = {"databaseId:assetId": "db1:a1", "historyRecordId": "2026-07-05T01:00:00Z#aaaaaaaa"}
        m.history_table.query.return_value = {"Items": self._items()[:1], "LastEvaluatedKey": last_key}

        response = m.lambda_handler(_event(query={"pageSize": "1"}), MagicMock())
        body = json.loads(response["body"])
        token = body["NextToken"]
        assert json.loads(base64.b64decode(token).decode("utf-8")) == last_key
        assert m.history_table.query.call_args[1]["Limit"] == 1

        # Feed the token back; it must decode into ExclusiveStartKey
        m.history_table.query.return_value = {"Items": []}
        m.lambda_handler(_event(query={"pageSize": "1", "startingToken": token}), MagicMock())
        query_kwargs = m.history_table.query.call_args[1]
        assert query_kwargs["ExclusiveStartKey"] == last_key

    def test_404_when_asset_never_existed_or_perma_deleted(self):
        m = _load()
        _authorize(m)
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {}  # neither live nor #deleted
        m.history_table = MagicMock()

        response = m.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 404
        m.history_table.query.assert_not_called()

    def test_archived_asset_is_accessible(self):
        m = _load()
        _authorize(m)
        m.asset_table = MagicMock()
        # First get_item (live) empty; second (#deleted) returns the asset
        m.asset_table.get_item.side_effect = [
            {}, {"Item": {"databaseId": "db1#deleted", "assetId": "a1", "status": "archived"}},
        ]
        m.history_table = MagicMock()
        m.history_table.query.return_value = {"Items": []}

        response = m.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 200
        assert m.asset_table.get_item.call_args_list[1][1]["Key"]["databaseId"] == "db1#deleted"

    def test_tier2_denial_returns_403(self):
        m = _load()
        _authorize(m, allowed=False)
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {"Item": {"databaseId": "db1", "assetId": "a1"}}
        m.history_table = MagicMock()

        response = m.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 403
        m.history_table.query.assert_not_called()

    def test_tier1_denial_returns_403(self):
        m = _load()
        enforcer = _authorize(m)
        enforcer.enforceAPI.return_value = False
        m.asset_table = MagicMock()
        m.history_table = MagicMock()

        response = m.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 403
        m.asset_table.get_item.assert_not_called()

    def test_invalid_page_size_returns_400(self):
        m = _load()
        _authorize(m)
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {"Item": {"databaseId": "db1", "assetId": "a1"}}
        m.history_table = MagicMock()

        response = m.lambda_handler(_event(query={"pageSize": "0"}), MagicMock())
        assert response["statusCode"] == 400

    def test_invalid_starting_token_returns_400(self):
        m = _load()
        _authorize(m)
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {"Item": {"databaseId": "db1", "assetId": "a1"}}
        m.history_table = MagicMock()

        response = m.lambda_handler(
            _event(query={"startingToken": "not-base64-json!"}), MagicMock()
        )
        assert response["statusCode"] == 400
        m.history_table.query.assert_not_called()
