# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every metadata route decides Tier 2 before it reports whether the asset or database exists.

A caller who may call a route (Tier 1) but is refused the asset or database (Tier 2) gets the same
403 and body whether or not it exists, so the route cannot tell them which ids exist. Only an
authorized caller is told that an asset (400 "Asset not found") or a database (400 "Database not
found") is missing. An asset link's two ends are both decided before either is reported missing.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.metadata import metadataService

_OPEN = "dbopen"      # a database the caller is granted
_CLOSED = "dbclosed"  # a database the caller is refused
_ASSET = "asset-1"
_LINK = "link-1"
_ITEM = {"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}


class _Table:
    """A DynamoDB table stub whose get_item finds rows by the named key fields."""

    def __init__(self, key_fields, *rows):
        self.key_fields = key_fields
        self.rows = {tuple(row[f] for f in key_fields): dict(row) for row in rows}

    def get_item(self, Key):
        row = self.rows.get(tuple(Key[f] for f in self.key_fields))
        return {"Item": dict(row)} if row else {}


def _asset(database_id, asset_id=_ASSET):
    return {"databaseId": database_id, "assetId": asset_id, "bucketId": "b1",
            "assetLocation": {"Key": f"{asset_id}/"}}


def _database(database_id):
    return {"databaseId": database_id}


def _event(method, path, path_parameters, query=None, body=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_parameters,
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }


class _Run:
    """Runs lambda_handler with the tables, S3 and both authorization tiers stubbed.

    Tier 1 always passes. Tier 2 grants an object when its databaseId is in ``granted``, through
    both check_entity_authorization and the enforcer the multi-action (REPLACE_ALL) check uses.
    """

    def __init__(self, assets=(), databases=(), links=(), granted=(_OPEN,)):
        self.asset_table = _Table(("databaseId", "assetId"), *assets)
        self.database_table = _Table(("databaseId",), *databases)
        self.link_table = _Table(("assetLinkId",), *links)
        self.granted = set(granted)
        self.decided = []
        self.queries = []
        self.client = MagicMock()
        self.client.get_paginator.side_effect = self._paginator
        self.client.batch_write_item.return_value = {"UnprocessedItems": {}}
        self.s3 = MagicMock()
        self.s3.head_object.return_value = {}

    def _paginator(self, _name):
        run = self

        class _Pages:
            def paginate(self, **kwargs):
                run.queries.append(kwargs)
                result = MagicMock()
                result.build_full_result.return_value = {"Items": []}
                return result

        return _Pages()

    def _decide(self, entity, *_args):
        self.decided.append(dict(entity))
        return entity.get("databaseId") in self.granted

    def call(self, event):
        enforcer = MagicMock()
        enforcer.return_value.enforceAPI.return_value = True
        enforcer.return_value.enforce.side_effect = self._decide
        nested = MagicMock()
        nested.holder.return_value = None
        with patch.object(metadataService, "asset_storage_table", self.asset_table), \
                patch.object(metadataService, "database_storage_table", self.database_table), \
                patch.object(metadataService, "asset_links_table", self.link_table), \
                patch.object(metadataService, "asset_links_metadata_table", MagicMock(get_item=lambda **_: {})), \
                patch.object(metadataService, "database_metadata_table", MagicMock(get_item=lambda **_: {})), \
                patch.object(metadataService, "asset_file_metadata_table", MagicMock(get_item=lambda **_: {})), \
                patch.object(metadataService, "file_attribute_table", MagicMock(get_item=lambda **_: {})), \
                patch.object(metadataService, "CasbinEnforcer", enforcer), \
                patch.object(metadataService, "check_entity_authorization", self._decide), \
                patch.object(metadataService, "dynamodb_client", self.client), \
                patch.object(metadataService, "s3", self.s3), \
                patch.object(metadataService, "log_action", MagicMock()), \
                patch.object(metadataService, "get_bucket_details", return_value={"bucketName": "bkt"}), \
                patch.object(metadataService, "nested_locations_of_asset", return_value=nested), \
                patch.object(metadataService, "get_aggregated_schemas", return_value=[]), \
                patch.object(metadataService, "get_database_config", return_value={}), \
                patch.object(metadataService, "validate_asset_version_exists", return_value=True), \
                patch.object(metadataService, "get_asset_metadata_version", return_value=[]):
            return metadataService.lambda_handler(event, MagicMock())


def _answer(response):
    return response["statusCode"], json.loads(response["body"]).get("message")


# Every asset-scoped metadata route: (id, method, route suffix, query, body)
_ASSET_ROUTES = [
    ("asset-get", "GET", "/metadata", {}, None),
    ("asset-version-get", "GET", "/metadata", {"assetVersionId": "1"}, None),
    ("asset-post", "POST", "/metadata", None, {"metadata": [_ITEM]}),
    ("asset-put", "PUT", "/metadata", None, {"metadata": [_ITEM], "updateType": "update"}),
    ("asset-replace-all", "PUT", "/metadata", None, {"metadata": [_ITEM], "updateType": "replace_all"}),
    ("asset-delete", "DELETE", "/metadata", None, {"metadataKeys": ["k"]}),
    ("file-get", "GET", "/metadata/file", {"filePath": "/a.txt", "type": "metadata"}, None),
    ("file-attribute-get", "GET", "/metadata/file", {"filePath": "/a.txt", "type": "attribute"}, None),
    ("file-version-get", "GET", "/metadata/file",
     {"filePath": "/a.txt", "type": "metadata", "assetVersionId": "1"}, None),
    ("file-post", "POST", "/metadata/file", None, {"filePath": "/a.txt", "type": "metadata", "metadata": [_ITEM]}),
    ("file-put", "PUT", "/metadata/file", None,
     {"filePath": "/a.txt", "type": "metadata", "metadata": [_ITEM], "updateType": "update"}),
    ("file-replace-all", "PUT", "/metadata/file", None,
     {"filePath": "/a.txt", "type": "metadata", "metadata": [_ITEM], "updateType": "replace_all"}),
    ("file-delete", "DELETE", "/metadata/file", None,
     {"filePath": "/a.txt", "type": "metadata", "metadataKeys": ["k"]}),
]
_ASSET_ROUTE_IDS = [route[0] for route in _ASSET_ROUTES]


def _asset_event(route, database_id):
    _id, method, suffix, query, body = route
    path = f"/database/{database_id}/assets/{_ASSET}{suffix}"
    return _event(method, path, {"databaseId": database_id, "assetId": _ASSET}, query, body)


@pytest.mark.unit
class TestAssetRoutes:
    @pytest.mark.parametrize("route", _ASSET_ROUTES, ids=_ASSET_ROUTE_IDS)
    def test_a_refused_caller_gets_the_same_answer_for_a_missing_asset(self, route):
        existing = _Run(assets=[_asset(_CLOSED)]).call(_asset_event(route, _CLOSED))
        missing_run = _Run()
        missing = missing_run.call(_asset_event(route, _CLOSED))
        assert existing["statusCode"] == 403, existing
        assert _answer(missing) == _answer(existing)
        assert missing_run.decided[0] == {"databaseId": _CLOSED, "assetId": _ASSET, "object__type": "asset"}
        assert missing_run.queries == []
        missing_run.client.batch_write_item.assert_not_called()

    @pytest.mark.parametrize("route", _ASSET_ROUTES, ids=_ASSET_ROUTE_IDS)
    def test_an_authorized_caller_is_told_the_asset_is_missing(self, route):
        run = _Run()
        response = run.call(_asset_event(route, _OPEN))
        assert _answer(response)[0] == 400
        assert "Asset not found" in _answer(response)[1]
        assert run.queries == []
        run.s3.head_object.assert_not_called()

    @pytest.mark.parametrize("route", _ASSET_ROUTES, ids=_ASSET_ROUTE_IDS)
    def test_an_authorized_caller_on_an_existing_asset_succeeds(self, route):
        run = _Run(assets=[_asset(_OPEN)])
        response = run.call(_asset_event(route, _OPEN))
        assert response["statusCode"] == 200, response
        assert run.decided[0]["object__type"] == "asset"
        assert run.decided[0]["bucketId"] == "b1"


_LINK_ROUTES = [
    ("link-get", "GET", None),
    ("link-post", "POST", {"metadata": [_ITEM]}),
    ("link-put", "PUT", {"metadata": [_ITEM], "updateType": "update"}),
    ("link-replace-all", "PUT", {"metadata": [_ITEM], "updateType": "replace_all"}),
    ("link-delete", "DELETE", {"metadataKeys": ["k"]}),
]
_LINK_ROUTE_IDS = [route[0] for route in _LINK_ROUTES]
_FROM = (_OPEN, "from-asset")
_TO = (_CLOSED, "to-asset")


def _link_row(from_end=_FROM, to_end=_TO):
    return {"assetLinkId": _LINK, "fromAssetDatabaseId": from_end[0], "fromAssetId": from_end[1],
            "toAssetDatabaseId": to_end[0], "toAssetId": to_end[1]}


def _link_event(route):
    _id, method, body = route
    return _event(method, f"/asset-links/{_LINK}/metadata", {"assetLinkId": _LINK}, None, body)


@pytest.mark.unit
class TestAssetLinkRoutes:
    """Both ends are decided before either end is reported missing."""

    @pytest.mark.parametrize("route", _LINK_ROUTES, ids=_LINK_ROUTE_IDS)
    @pytest.mark.parametrize("present", [
        ("both", True, True), ("from missing", False, True),
        ("to missing", True, False), ("both missing", False, False),
    ], ids=lambda p: p[0])
    def test_a_caller_refused_one_end_gets_the_same_answer_whichever_end_exists(self, route, present):
        _label, from_exists, to_exists = present
        both = _Run(assets=[_asset(*_FROM), _asset(*_TO)], links=[_link_row()]).call(_link_event(route))
        assets = ([_asset(*_FROM)] if from_exists else []) + ([_asset(*_TO)] if to_exists else [])
        run = _Run(assets=assets, links=[_link_row()])
        response = run.call(_link_event(route))
        assert both["statusCode"] == 403, both
        assert _answer(response) == _answer(both)
        assert run.queries == []
        run.client.batch_write_item.assert_not_called()

    @pytest.mark.parametrize("route", _LINK_ROUTES, ids=_LINK_ROUTE_IDS)
    def test_a_caller_granted_both_ends_is_told_an_end_is_missing(self, route):
        run = _Run(assets=[_asset(*_FROM)], links=[_link_row()], granted=(_OPEN, _CLOSED))
        response = run.call(_link_event(route))
        assert _answer(response)[0] == 400
        assert "Asset not found" in _answer(response)[1]
        # Both ends are decided, the missing to end on its stand-in, before the answer
        assert list(dict.fromkeys(o["databaseId"] for o in run.decided)) == [_OPEN, _CLOSED]
        assert run.queries == []

    @pytest.mark.parametrize("route", _LINK_ROUTES, ids=_LINK_ROUTE_IDS)
    def test_a_caller_granted_both_existing_ends_succeeds(self, route):
        run = _Run(assets=[_asset(*_FROM), _asset(*_TO)], links=[_link_row()], granted=(_OPEN, _CLOSED))
        response = run.call(_link_event(route))
        assert response["statusCode"] == 200, response


_DATABASE_ROUTES = [
    ("database-get", "GET", None),
    ("database-post", "POST", {"metadata": [_ITEM]}),
    ("database-put", "PUT", {"metadata": [_ITEM], "updateType": "update"}),
    ("database-replace-all", "PUT", {"metadata": [_ITEM], "updateType": "replace_all"}),
    ("database-delete", "DELETE", {"metadataKeys": ["k"]}),
]
_DATABASE_ROUTE_IDS = [route[0] for route in _DATABASE_ROUTES]


def _database_event(route, database_id):
    _id, method, body = route
    return _event(method, f"/database/{database_id}/metadata", {"databaseId": database_id}, None, body)


@pytest.mark.unit
class TestDatabaseRoutes:
    @pytest.mark.parametrize("route", _DATABASE_ROUTES, ids=_DATABASE_ROUTE_IDS)
    def test_a_refused_caller_gets_the_same_answer_for_a_missing_database(self, route):
        existing = _Run(databases=[_database(_CLOSED)]).call(_database_event(route, _CLOSED))
        missing_run = _Run()
        missing = missing_run.call(_database_event(route, _CLOSED))
        assert existing["statusCode"] == 403, existing
        assert _answer(missing) == _answer(existing)
        assert missing_run.decided[0] == {"databaseId": _CLOSED, "object__type": "database"}
        assert missing_run.queries == []
        missing_run.client.batch_write_item.assert_not_called()

    @pytest.mark.parametrize("route", _DATABASE_ROUTES, ids=_DATABASE_ROUTE_IDS)
    def test_an_authorized_caller_is_told_the_database_is_missing(self, route):
        run = _Run()
        response = run.call(_database_event(route, _OPEN))
        assert _answer(response)[0] == 400
        assert "Database not found" in _answer(response)[1]
        assert run.queries == []

    @pytest.mark.parametrize("route", _DATABASE_ROUTES, ids=_DATABASE_ROUTE_IDS)
    def test_an_authorized_caller_on_an_existing_database_succeeds(self, route):
        run = _Run(databases=[_database(_OPEN)])
        response = run.call(_database_event(route, _OPEN))
        assert response["statusCode"] == 200, response
        assert run.decided[0]["object__type"] == "database"
