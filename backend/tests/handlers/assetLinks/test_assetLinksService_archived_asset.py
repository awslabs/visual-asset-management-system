# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#870: an archived asset's relationships list with showArchived; without it, it stays not found.

Links stay keyed by the asset's original "<databaseId>:<assetId>" after archive, so only the
lookup of the queried asset needs to reach its "<databaseId>#deleted" record.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.assetLinks import assetLinksService

_DB = "db1"
_ASSET = "asset-1"
_INVALID = "showArchived parameter must be a valid boolean value (true/false)"


class _Table:
    def __init__(self, *rows):
        self.rows = {(r["databaseId"], r["assetId"]): dict(r) for r in rows}
        self.keys_read = []

    def get_item(self, Key):
        self.keys_read.append((Key["databaseId"], Key["assetId"]))
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": dict(row)} if row else {}


def _archived():
    return {"databaseId": f"{_DB}#deleted", "assetId": _ASSET, "assetName": "a", "status": "archived"}


def _live():
    return {"databaseId": _DB, "assetId": _ASSET, "assetName": "a"}


def _event(query):
    return {
        "pathParameters": {"assetId": _ASSET, "databaseId": _DB},
        "queryStringParameters": query,
        "requestContext": {"http": {"method": "GET",
                                    "path": f"/database/{_DB}/assets/{_ASSET}/asset-links"}},
    }


def _call(table, query, enforce=True):
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = True
    if callable(enforce):
        enforcer.return_value.enforce.side_effect = lambda obj, act: enforce(obj)
    else:
        enforcer.return_value.enforce.return_value = enforce
    links = MagicMock()
    links.query.return_value = {"Items": []}
    with patch.object(assetLinksService, "asset_storage_table", table), \
            patch.object(assetLinksService, "asset_links_table", links), \
            patch.object(assetLinksService, "CasbinEnforcer", enforcer), \
            patch.object(assetLinksService, "request_to_claims",
                         return_value={"tokens": ["alice"], "roles": []}):
        response = assetLinksService.lambda_handler(_event(query), MagicMock())
    return response, enforcer, links


@pytest.mark.unit
class TestArchivedAssetLinks:
    def test_without_the_flag_the_archived_asset_is_not_found(self):
        response, _, links = _call(_Table(_archived()), None)
        assert response["statusCode"] == 400
        assert "Asset not found" in json.loads(response["body"])["message"]
        links.query.assert_not_called()

    def test_with_the_flag_the_links_are_read_under_the_original_key(self):
        response, enforcer, links = _call(_Table(_archived()), {"showArchived": "true"})
        assert response["statusCode"] == 200, response
        evaluated = enforcer.return_value.enforce.call_args_list[0].args[0]
        assert evaluated["databaseId"] == f"{_DB}#deleted"
        keys = [c.kwargs["KeyConditionExpression"].get_expression()["values"][1]
                for c in links.query.call_args_list]
        assert set(keys) == {f"{_DB}:{_ASSET}"}

    def test_a_live_asset_is_unchanged_by_the_flag(self):
        table = _Table(_live())
        response, _, _ = _call(table, {"showArchived": "true"})
        assert response["statusCode"] == 200
        assert table.keys_read == [(_DB, _ASSET)]

    def test_a_refused_caller_is_refused(self):
        response, _, links = _call(_Table(_archived()), {"showArchived": "true"}, enforce=False)
        assert response["statusCode"] == 403
        links.query.assert_not_called()

    def test_invalid_flag_is_refused(self):
        table = _Table(_archived())
        response, _, _ = _call(table, {"showArchived": "maybe"})
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == _INVALID
        assert table.keys_read == []


def _live_only(obj):
    """A role granted db1's live assets only: the stored "db1#deleted" id does not match."""
    return obj.get("databaseId") == _DB


@pytest.mark.unit
class TestRefusedMatchesMissing:
    """With showArchived, Tier 2 is decided before the asset's existence is reported."""

    def _status_and_message(self, response):
        return response["statusCode"], json.loads(response["body"])["message"]

    def test_a_refused_archived_asset_answers_as_a_missing_id(self):
        refused, _, refused_links = _call(_Table(_archived()), {"showArchived": "true"}, enforce=_live_only)
        missing, enforcer, missing_links = _call(_Table(), {"showArchived": "true"}, enforce=_live_only)
        assert self._status_and_message(refused) == self._status_and_message(missing)
        assert refused["statusCode"] == 403
        assert enforcer.return_value.enforce.call_args.args[0]["databaseId"] == f"{_DB}#deleted"
        refused_links.query.assert_not_called()
        missing_links.query.assert_not_called()

    def test_an_allowed_caller_learns_a_missing_id_is_missing(self):
        response, _, _ = _call(_Table(), {"showArchived": "true"})
        assert response["statusCode"] == 400
        assert "Asset not found" in json.loads(response["body"])["message"]

    def test_without_the_flag_existence_is_still_checked_first(self):
        response, enforcer, _ = _call(_Table(), None, enforce=_live_only)
        assert response["statusCode"] == 400
        assert "Asset not found" in json.loads(response["body"])["message"]
        enforcer.return_value.enforce.assert_not_called()
