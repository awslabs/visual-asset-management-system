# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The single asset link GET and the asset link PUT decide Tier 2 on both ends before either is missing.

A caller refused either linked asset gets the same 403 and body whether or not the linked assets
still exist: each end is decided on its record, or on the link's stored ids when the asset is gone.
Only a caller allowed both ends is told "One or both linked assets no longer exist" (400). A link
id that names no link still answers "Asset link not found" first, since the link names no asset
until it is read.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.assetLinks import assetLinksService

_LINK = "11111111-2222-3333-4444-555555555555"
_OPEN = "open-db"      # a database the caller is granted
_CLOSED = "closed-db"  # a database the caller is refused
_FROM = (_OPEN, "from-asset")
_TO = (_CLOSED, "to-asset")


def _link_row():
    return {"assetLinkId": _LINK, "fromAssetDatabaseId": _FROM[0], "fromAssetId": _FROM[1],
            "toAssetDatabaseId": _TO[0], "toAssetId": _TO[1], "relationshipType": "related", "tags": []}


class _AssetTable:
    def __init__(self, *ends):
        self.rows = {end: {"databaseId": end[0], "assetId": end[1], "assetName": end[1]} for end in ends}

    def get_item(self, Key):
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": dict(row)} if row else {}


class _LinkTable:
    def __init__(self, *rows):
        self.rows = {row["assetLinkId"]: dict(row) for row in rows}
        self.update_item = MagicMock()

    def get_item(self, Key):
        row = self.rows.get(Key["assetLinkId"])
        return {"Item": dict(row)} if row else {}


def _event(method):
    if method == "GET":
        return {"pathParameters": {"assetLinkId": _LINK}, "queryStringParameters": None,
                "requestContext": {"http": {"method": "GET", "path": f"/asset-links/single/{_LINK}"}}}
    return {"pathParameters": {"assetLinkId": _LINK}, "queryStringParameters": None,
            "body": json.dumps({"tags": ["t1"]}),
            "requestContext": {"http": {"method": "PUT", "path": f"/asset-links/{_LINK}"}}}


def _call(method, ends=(), links=(), granted=(_OPEN,)):
    decided = []

    def _enforce(obj, act):
        decided.append((dict(obj), act))
        return obj.get("databaseId") in granted

    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = True
    enforcer.return_value.enforce.side_effect = _enforce
    link_table = _LinkTable(*links)
    with patch.object(assetLinksService, "asset_storage_table", _AssetTable(*ends)), \
            patch.object(assetLinksService, "asset_links_table", link_table), \
            patch.object(assetLinksService, "CasbinEnforcer", enforcer), \
            patch.object(assetLinksService, "log_action", MagicMock()), \
            patch.object(assetLinksService, "request_to_claims",
                         return_value={"tokens": ["alice"], "roles": []}):
        response = assetLinksService.lambda_handler(_event(method), MagicMock())
    return response, decided, link_table


def _answer(response):
    return response["statusCode"], json.loads(response["body"]).get("message")


_METHODS = ["GET", "PUT"]


@pytest.mark.unit
class TestLinkEndsAuthorizedBeforeExistence:
    @pytest.mark.parametrize("method", _METHODS)
    @pytest.mark.parametrize("present", [
        ("to missing", (_FROM,)), ("from missing", (_TO,)), ("both missing", ()),
    ], ids=lambda p: p[0])
    def test_a_caller_refused_one_end_gets_the_same_answer_whichever_end_exists(self, method, present):
        _label, ends = present
        both, _, both_links = _call(method, ends=(_FROM, _TO), links=[_link_row()])
        response, decided, links = _call(method, ends=ends, links=[_link_row()])
        assert both["statusCode"] == 403, both
        assert _answer(response) == _answer(both)
        assert _CLOSED in {obj["databaseId"] for obj, _act in decided}
        both_links.update_item.assert_not_called()
        links.update_item.assert_not_called()

    @pytest.mark.parametrize("method", _METHODS)
    def test_the_missing_end_is_decided_on_the_links_stored_ids(self, method):
        _response, decided, _ = _call(method, ends=(_FROM,), links=[_link_row()])
        assert ({"databaseId": _CLOSED, "assetId": "to-asset", "object__type": "asset"}, method) in decided

    @pytest.mark.parametrize("method", _METHODS)
    def test_a_caller_allowed_both_ends_is_told_an_end_no_longer_exists(self, method):
        response, decided, links = _call(method, ends=(_FROM,), links=[_link_row()], granted=(_OPEN, _CLOSED))
        assert _answer(response) == (400, "One or both linked assets no longer exist")
        assert [obj["databaseId"] for obj, _act in decided] == [_OPEN, _CLOSED]
        links.update_item.assert_not_called()

    @pytest.mark.parametrize("method", _METHODS)
    def test_a_link_id_that_names_no_link_is_not_found(self, method):
        response, decided, _ = _call(method, ends=(_FROM, _TO), granted=(_OPEN, _CLOSED))
        assert _answer(response) == (400, "Asset link not found")
        assert decided == []

    def test_a_caller_allowed_both_ends_reads_the_link(self):
        response, decided, _ = _call("GET", ends=(_FROM, _TO), links=[_link_row()], granted=(_OPEN, _CLOSED))
        assert response["statusCode"] == 200, response
        assert json.loads(response["body"])["assetLink"]["assetLinkId"] == _LINK
        assert [(obj["assetName"], act) for obj, act in decided] == [("from-asset", "GET"), ("to-asset", "GET")]

    def test_a_caller_allowed_both_ends_updates_the_link(self):
        response, decided, links = _call("PUT", ends=(_FROM, _TO), links=[_link_row()], granted=(_OPEN, _CLOSED))
        assert response["statusCode"] == 200, response
        links.update_item.assert_called_once()
        assert [act for _obj, act in decided] == ["PUT", "PUT"]
