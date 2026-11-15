# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#870: an asset link's metadata reads with showArchived when one end is archived.

An archived asset's links stay keyed by its original ids, so its relationships panel lists them
with showArchived. Opening one of those links read the link's metadata, which looked both ends up
in the live partition only and answered 400 "Asset not found". With showArchived=true the GET also
resolves an archived end; POST, PUT and DELETE never do.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.metadata import metadataService

_LINK = "link-1"
_PATH = f"/asset-links/{_LINK}/metadata"
_INVALID = "showArchived parameter must be a valid boolean value (true/false)"
_LINK_ROW = {"assetLinkId": _LINK, "fromAssetDatabaseId": "db1", "fromAssetId": "arch",
             "toAssetDatabaseId": "db2", "toAssetId": "live"}


class _Table:
    def __init__(self, *rows):
        self.rows = {(r["databaseId"], r["assetId"]): dict(r) for r in rows}
        self.keys_read = []

    def get_item(self, Key):
        self.keys_read.append((Key["databaseId"], Key["assetId"]))
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": dict(row)} if row else {}


def _assets():
    return _Table(
        {"databaseId": "db1#deleted", "assetId": "arch", "status": "archived"},
        {"databaseId": "db2", "assetId": "live"},
    )


def _event(method, query=None, body=None):
    return {
        "requestContext": {"http": {"method": method, "path": _PATH}},
        "pathParameters": {"assetLinkId": _LINK},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }


def _call(table, event, authorized=True):
    authorized_objects = []

    def _authorize(entity, action, claims):
        authorized_objects.append(dict(entity))
        return authorized(entity) if callable(authorized) else authorized

    links = MagicMock()
    links.get_item.return_value = {"Item": dict(_LINK_ROW)}
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = True
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value.build_full_result.return_value = {
        "Items": []}
    with patch.object(metadataService, "asset_storage_table", table), \
            patch.object(metadataService, "asset_links_table", links), \
            patch.object(metadataService, "CasbinEnforcer", enforcer), \
            patch.object(metadataService, "check_entity_authorization", _authorize), \
            patch.object(metadataService, "dynamodb_client", client), \
            patch.object(metadataService, "get_database_config", return_value={}):
        return metadataService.lambda_handler(event, MagicMock()), authorized_objects


def _message(response):
    return json.loads(response["body"]).get("message")


@pytest.mark.unit
class TestAssetLinkMetadataRead:
    def test_archived_end_without_the_flag_is_not_found(self):
        response, _ = _call(_assets(), _event("GET"))
        assert response["statusCode"] == 400
        assert "Asset not found" in _message(response)

    def test_archived_end_with_the_flag_reads_the_metadata(self):
        response, authorized = _call(_assets(), _event("GET", {"showArchived": "true"}))
        assert response["statusCode"] == 200, response
        # Both ends are authorized; the archived one as its stored archived record
        assert [o["databaseId"] for o in authorized] == ["db1#deleted", "db2"]

    def test_live_ends_are_read_once_with_the_flag(self):
        table = _Table({"databaseId": "db1", "assetId": "arch"},
                       {"databaseId": "db2", "assetId": "live"})
        response, _ = _call(table, _event("GET", {"showArchived": "true"}))
        assert response["statusCode"] == 200
        assert table.keys_read == [("db1", "arch"), ("db2", "live")]

    def test_refused_caller_is_refused(self):
        response, _ = _call(_assets(), _event("GET", {"showArchived": "true"}), authorized=False)
        assert response["statusCode"] == 403

    def test_invalid_flag_is_refused(self):
        table = _assets()
        response, _ = _call(table, _event("GET", {"showArchived": "maybe"}))
        assert response["statusCode"] == 400
        assert _INVALID in _message(response)
        assert table.keys_read == []


def _live_only(entity):
    """A role granted live assets only: an archived record's stored id ends in "#deleted"."""
    return not entity.get("databaseId", "").endswith("#deleted")


@pytest.mark.unit
class TestAssetLinkMetadataRefusedMatchesMissing:
    """With showArchived, both ends are authorized before either's existence is reported."""

    def test_a_refused_archived_end_answers_as_a_missing_end(self):
        query = {"showArchived": "true"}
        refused, _ = _call(_assets(), _event("GET", query), authorized=_live_only)
        missing, authorized = _call(_Table({"databaseId": "db2", "assetId": "live"}),
                                    _event("GET", query), authorized=_live_only)
        assert (refused["statusCode"], _message(refused)) == (missing["statusCode"], _message(missing))
        assert refused["statusCode"] == 403
        assert authorized[0]["databaseId"] == "db1#deleted"

    def test_an_allowed_caller_learns_an_end_is_missing(self):
        response, _ = _call(_Table({"databaseId": "db2", "assetId": "live"}),
                            _event("GET", {"showArchived": "true"}))
        assert response["statusCode"] == 400
        assert "Asset not found" in _message(response)

    def test_without_the_flag_existence_is_still_checked_first(self):
        response, authorized = _call(_Table({"databaseId": "db2", "assetId": "live"}), _event("GET"),
                                     authorized=_live_only)
        assert response["statusCode"] == 400
        assert "Asset not found" in _message(response)
        assert authorized == []


_ITEM = {"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}


@pytest.mark.unit
class TestAssetLinkMetadataWritesStayRefused:
    @pytest.mark.parametrize("method,body", [
        ("POST", {"metadata": [_ITEM]}),
        ("PUT", {"metadata": [_ITEM], "updateType": "update"}),
        ("DELETE", {"metadataKeys": ["k"]}),
    ], ids=["post", "put", "delete"])
    def test_write_with_an_archived_end_is_refused(self, method, body):
        table = _assets()
        response, _ = _call(table, _event(method, {"showArchived": "true"}, body))
        assert response["statusCode"] == 400, response
        assert "Asset not found" in _message(response)
        assert ("db1#deleted", "arch") not in table.keys_read
