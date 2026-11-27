# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The single asset link GET and the asset link PUT decide Tier 2 on both ends before either is missing.

`GET /asset-links/single/{assetLinkId}` and `PUT /asset-links/{assetLinkId}`: a caller refused either
linked asset gets the same 403 and body whichever ends exist. A missing end is decided on the link's
stored ids for it. Only a caller granted both ends is told that a linked asset no longer exists (400,
as before). A link id that names no link still answers "Asset link not found" first, since the link
names no asset until it is read.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.assetLinks import assetLinksService as svc

_OPEN = "dbopen"      # a database the caller is granted
_CLOSED = "dbclosed"  # a database the caller is refused
_LINK = "3f8c1a2e-5b7d-4e6f-9a0b-1c2d3e4f5a6b"
_FROM = (_OPEN, "from-asset")
_TO = (_CLOSED, "to-asset")
_METHODS = ["GET", "PUT"]
AUTHENTICATED = {"tokens": ["some-user"], "roles": ["role"], "mfaEnabled": False}


class _Table:
    def __init__(self, key_fields, *rows):
        self.key_fields = key_fields
        self.rows = {tuple(row[f] for f in key_fields): dict(row) for row in rows}
        self.update_item = MagicMock()
        self.put_item = MagicMock()

    def get_item(self, Key):
        row = self.rows.get(tuple(Key[f] for f in self.key_fields))
        return {"Item": dict(row)} if row else {}


def _asset(end):
    return {"databaseId": end[0], "assetId": end[1], "assetName": end[1], "tags": []}


def _link():
    return {"assetLinkId": _LINK, "fromAssetDatabaseId": _FROM[0], "fromAssetId": _FROM[1],
            "toAssetDatabaseId": _TO[0], "toAssetId": _TO[1], "relationshipType": "related", "tags": []}


class _Run:
    def __init__(self, monkeypatch, assets, granted=(_OPEN,), links=(_link(),)):
        self.granted = set(granted)
        self.decided = []
        enforcer = MagicMock()
        enforcer.return_value.enforceAPI.return_value = True
        enforcer.return_value.enforce.side_effect = self._decide
        self.links = _Table(("assetLinkId",), *links)
        monkeypatch.setattr(svc, "asset_links_table", self.links)
        monkeypatch.setattr(svc, "asset_storage_table", _Table(("databaseId", "assetId"), *assets))
        monkeypatch.setattr(svc, "CasbinEnforcer", enforcer)
        monkeypatch.setattr(svc, "request_to_claims", lambda event: AUTHENTICATED)

    def _decide(self, obj, action):
        self.decided.append((dict(obj), action))
        return obj.get("databaseId") in self.granted

    def call(self, method):
        path = f"/asset-links/single/{_LINK}" if method == "GET" else f"/asset-links/{_LINK}"
        event = {
            "requestContext": {"http": {"method": method, "path": path}},
            "pathParameters": {"assetLinkId": _LINK},
            "queryStringParameters": None,
            "body": json.dumps({"tags": ["t"]}) if method == "PUT" else None,
        }
        return svc.lambda_handler(event, MagicMock())

    def wrote(self):
        return self.links.update_item.called or self.links.put_item.called


def _answer(response):
    return response["statusCode"], json.loads(response["body"]).get("message")


@pytest.mark.unit
@pytest.mark.parametrize("method", _METHODS)
class TestAssetLinkGetAndPut:
    @pytest.mark.parametrize("present", [
        ("from missing", False, True), ("to missing", True, False), ("both missing", False, False),
    ], ids=lambda p: p[0])
    def test_a_caller_refused_one_end_gets_the_same_answer_whichever_end_exists(self, monkeypatch, method, present):
        _label, from_exists, to_exists = present
        both_run = _Run(monkeypatch, [_asset(_FROM), _asset(_TO)])
        both = both_run.call(method)
        assert both["statusCode"] == 403, both
        assets = ([_asset(_FROM)] if from_exists else []) + ([_asset(_TO)] if to_exists else [])
        run = _Run(monkeypatch, assets)
        assert _answer(run.call(method)) == _answer(both)
        assert not run.wrote()

    def test_a_missing_end_is_decided_on_the_links_ids_for_it(self, monkeypatch, method):
        run = _Run(monkeypatch, [_asset(_FROM)])
        run.call(method)
        assert run.decided[-1] == ({"databaseId": _CLOSED, "assetId": "to-asset", "object__type": "asset"}, method)

    def test_a_caller_granted_both_ends_is_told_an_end_no_longer_exists(self, monkeypatch, method):
        run = _Run(monkeypatch, [_asset(_FROM)], granted=(_OPEN, _CLOSED))
        assert _answer(run.call(method)) == (400, "One or both linked assets no longer exist")
        assert [(o["databaseId"], a) for o, a in run.decided] == [(_OPEN, method), (_CLOSED, method)]
        assert not run.wrote()

    def test_a_link_id_that_does_not_exist_is_not_found(self, monkeypatch, method):
        run = _Run(monkeypatch, [], links=())
        assert _answer(run.call(method)) == (400, "Asset link not found")
        assert run.decided == []


@pytest.mark.unit
class TestAllowedCallers:
    def test_a_caller_granted_both_existing_ends_gets_the_link(self, monkeypatch):
        response = _Run(monkeypatch, [_asset(_FROM), _asset(_TO)], granted=(_OPEN, _CLOSED)).call("GET")
        assert response["statusCode"] == 200, response
        assert json.loads(response["body"])["assetLink"]["assetLinkId"] == _LINK

    def test_a_caller_granted_both_existing_ends_updates_the_link(self, monkeypatch):
        run = _Run(monkeypatch, [_asset(_FROM), _asset(_TO)], granted=(_OPEN, _CLOSED))
        response = run.call("PUT")
        assert response["statusCode"] == 200, response
        assert run.wrote()


# `GET /database/{databaseId}/assets/{assetId}/asset-links`: the per-asset link list decides the asset first.
_ASSET = (_CLOSED, "listed-asset")


class _ListRun(_Run):
    """The asset links list for one asset, with the link GSI queries recorded."""

    def __init__(self, monkeypatch, assets, granted=(_OPEN,)):
        super().__init__(monkeypatch, assets, granted=granted, links=())
        self.queries = []
        monkeypatch.setattr(svc, "query_all_items", lambda table, **kwargs: self.queries.append(kwargs) or [])

    def list(self, end=_ASSET):
        database_id, asset_id = end
        event = {
            "requestContext": {"http": {"method": "GET",
                                        "path": f"/database/{database_id}/assets/{asset_id}/asset-links"}},
            "pathParameters": {"databaseId": database_id, "assetId": asset_id},
            "queryStringParameters": None,
        }
        return svc.lambda_handler(event, MagicMock())


@pytest.mark.unit
class TestAssetLinksListForAnAsset:
    def test_a_refused_caller_gets_the_same_answer_for_a_missing_asset(self, monkeypatch):
        existing = _ListRun(monkeypatch, [_asset(_ASSET)]).list()
        assert existing["statusCode"] == 403, existing
        missing_run = _ListRun(monkeypatch, [])
        assert _answer(missing_run.list()) == _answer(existing)
        assert missing_run.decided == [({"databaseId": _CLOSED, "assetId": "listed-asset", "object__type": "asset"},
                                        "GET")]
        assert missing_run.queries == []

    def test_an_allowed_caller_is_told_the_asset_is_missing(self, monkeypatch):
        run = _ListRun(monkeypatch, [], granted=(_CLOSED,))
        assert _answer(run.list()) == (400, "Asset not found in database")
        assert [o["databaseId"] for o, _a in run.decided] == [_CLOSED]
        assert run.queries == []

    def test_an_allowed_caller_on_an_existing_asset_gets_its_links(self, monkeypatch):
        run = _ListRun(monkeypatch, [_asset(_ASSET)], granted=(_CLOSED,))
        response = run.list()
        assert response["statusCode"] == 200, response
        assert run.decided[0][0]["assetName"] == "listed-asset"
        assert len(run.queries) == 2
