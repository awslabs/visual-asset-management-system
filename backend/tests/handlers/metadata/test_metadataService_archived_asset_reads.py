# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#870: an archived asset's metadata reads with showArchived; every metadata write stays refused.

Archiving moves the asset record to "<databaseId>#deleted" while its metadata stays keyed by the
original "<databaseId>:<assetId>:<filePath>". The GET routes looked the record up in the live
partition only and answered 400 "Asset not found". With showArchived=true the asset and file
metadata GETs (current and version snapshot) also resolve the archived record and read the
original keys; POST, PUT and DELETE never do.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.metadata import metadataService

_DB = "db1"
_ASSET = "asset-1"
_BASE = f"/database/{_DB}/assets/{_ASSET}"
_INVALID = "showArchived parameter must be a valid boolean value (true/false)"


class _Table:
    def __init__(self, *rows):
        self.rows = {(r["databaseId"], r["assetId"]): dict(r) for r in rows}
        self.keys_read = []

    def get_item(self, Key):
        self.keys_read.append((Key["databaseId"], Key["assetId"]))
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": dict(row)} if row else {}


def _archived(with_status=True):
    row = {"databaseId": f"{_DB}#deleted", "assetId": _ASSET, "bucketId": "b1",
           "assetLocation": {"Key": f"{_ASSET}/"}}
    if with_status:
        row["status"] = "archived"
    return row


def _live():
    return {"databaseId": _DB, "assetId": _ASSET, "bucketId": "b1",
            "assetLocation": {"Key": f"{_ASSET}/"}}


def _event(method, path, query=None, body=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": {"databaseId": _DB, "assetId": _ASSET},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }


class _Harness:
    """Runs lambda_handler with the asset table, the metadata query and authorization stubbed."""

    def __init__(self, table, authorized=True):
        self.table = table
        self.authorized = authorized
        self.queried_keys = []
        self.authorized_objects = []

    def _paginator(self, _name):
        harness = self

        class _Pages:
            def paginate(self, **kwargs):
                harness.queried_keys.append(kwargs["ExpressionAttributeValues"][":pkValue"]["S"])
                result = MagicMock()
                result.build_full_result.return_value = {"Items": []}
                return result

        return _Pages()

    def _authorize(self, entity, action, claims):
        self.authorized_objects.append(dict(entity))
        if callable(self.authorized):
            return self.authorized(entity)
        return self.authorized

    def call(self, event):
        enforcer = MagicMock()
        enforcer.return_value.enforceAPI.return_value = True
        client = MagicMock()
        client.get_paginator.side_effect = self._paginator
        with patch.object(metadataService, "asset_storage_table", self.table), \
                patch.object(metadataService, "CasbinEnforcer", enforcer), \
                patch.object(metadataService, "check_entity_authorization", self._authorize), \
                patch.object(metadataService, "dynamodb_client", client), \
                patch.object(metadataService, "get_database_config", return_value={}), \
                patch.object(metadataService, "validate_asset_version_exists", return_value=True), \
                patch.object(metadataService, "get_asset_metadata_version", return_value=[]):
            return metadataService.lambda_handler(event, MagicMock())


def _message(response):
    return json.loads(response["body"]).get("message")


_READS = [
    ("asset", f"{_BASE}/metadata", {}),
    ("asset-version", f"{_BASE}/metadata", {"assetVersionId": "1"}),
    ("file", f"{_BASE}/metadata/file", {"filePath": "/a.txt", "type": "metadata"}),
    ("file-attribute", f"{_BASE}/metadata/file", {"filePath": "/a.txt", "type": "attribute"}),
    ("file-version", f"{_BASE}/metadata/file", {"filePath": "/a.txt", "type": "metadata",
                                                 "assetVersionId": "1"}),
]
_READ_IDS = [r[0] for r in _READS]


@pytest.mark.unit
class TestMetadataReads:
    @pytest.mark.parametrize("read", _READS, ids=_READ_IDS)
    def test_archived_asset_without_the_flag_is_not_found(self, read):
        _id, path, query = read
        harness = _Harness(_Table(_archived()))
        response = harness.call(_event("GET", path, query))
        assert response["statusCode"] == 400
        assert "Asset not found" in _message(response)

    @pytest.mark.parametrize("read", _READS, ids=_READ_IDS)
    def test_archived_asset_with_the_flag_reads_its_metadata(self, read):
        _id, path, query = read
        harness = _Harness(_Table(_archived()))
        response = harness.call(_event("GET", path, dict(query, showArchived="true")))
        assert response["statusCode"] == 200, response
        assert harness.authorized_objects[0]["databaseId"] == f"{_DB}#deleted"
        assert harness.authorized_objects[0]["object__type"] == "asset"
        for key in harness.queried_keys:
            assert key.startswith(f"{_DB}:{_ASSET}:")

    def test_legacy_archived_row_without_status_reads(self):
        harness = _Harness(_Table(_archived(with_status=False)))
        response = harness.call(_event("GET", f"{_BASE}/metadata", {"showArchived": "1"}))
        assert response["statusCode"] == 200

    @pytest.mark.parametrize("read", _READS, ids=_READ_IDS)
    def test_live_asset_is_read_once_with_the_flag(self, read):
        _id, path, query = read
        table = _Table(_live())
        response = _Harness(table).call(_event("GET", path, dict(query, showArchived="true")))
        assert response["statusCode"] == 200
        assert table.keys_read == [(_DB, _ASSET)]

    def test_refused_caller_is_refused(self):
        harness = _Harness(_Table(_archived()), authorized=False)
        response = harness.call(_event("GET", f"{_BASE}/metadata", {"showArchived": "true"}))
        assert response["statusCode"] == 403

    @pytest.mark.parametrize("read", _READS, ids=_READ_IDS)
    def test_invalid_flag_is_refused(self, read):
        _id, path, query = read
        table = _Table(_archived())
        response = _Harness(table).call(_event("GET", path, dict(query, showArchived="maybe")))
        assert response["statusCode"] == 400
        assert _INVALID in _message(response)
        assert table.keys_read == []


_ITEM = {"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}
_WRITES = [
    ("asset-post", "POST", f"{_BASE}/metadata", {"metadata": [_ITEM]}),
    ("asset-put", "PUT", f"{_BASE}/metadata", {"metadata": [_ITEM], "updateType": "update"}),
    ("asset-delete", "DELETE", f"{_BASE}/metadata", {"metadataKeys": ["k"]}),
    ("file-post", "POST", f"{_BASE}/metadata/file",
     {"filePath": "/a.txt", "type": "metadata", "metadata": [_ITEM]}),
    ("file-put", "PUT", f"{_BASE}/metadata/file",
     {"filePath": "/a.txt", "type": "metadata", "metadata": [_ITEM], "updateType": "update"}),
    ("file-delete", "DELETE", f"{_BASE}/metadata/file",
     {"filePath": "/a.txt", "type": "metadata", "metadataKeys": ["k"]}),
]


@pytest.mark.unit
class TestMetadataWritesStayRefused:
    @pytest.mark.parametrize("write", _WRITES, ids=[w[0] for w in _WRITES])
    def test_write_on_an_archived_asset_is_refused(self, write):
        _id, method, path, body = write
        table = _Table(_archived())
        response = _Harness(table).call(_event(method, path, {"showArchived": "true"}, body))
        assert response["statusCode"] == 400, response
        assert "Asset not found" in _message(response)
        assert (f"{_DB}#deleted", _ASSET) not in table.keys_read


def _live_only(entity):
    """A role granted db1's live assets only: the stored "db1#deleted" id does not match."""
    return entity.get("databaseId") == _DB


@pytest.mark.unit
class TestRefusedMatchesMissing:
    """With showArchived, Tier 2 is decided before existence on every metadata read."""

    @pytest.mark.parametrize("read", _READS, ids=_READ_IDS)
    def test_a_refused_archived_asset_answers_as_a_missing_id(self, read):
        _id, path, query = read
        query = dict(query, showArchived="true")
        refused = _Harness(_Table(_archived()), authorized=_live_only).call(_event("GET", path, query))
        missing = _Harness(_Table(), authorized=_live_only)
        missing_response = missing.call(_event("GET", path, query))
        assert (refused["statusCode"], _message(refused)) == \
            (missing_response["statusCode"], _message(missing_response))
        assert refused["statusCode"] == 403
        assert missing.authorized_objects[0]["databaseId"] == f"{_DB}#deleted"
        assert missing.queried_keys == []

    @pytest.mark.parametrize("read", _READS, ids=_READ_IDS)
    def test_an_allowed_caller_learns_a_missing_id_is_missing(self, read):
        _id, path, query = read
        response = _Harness(_Table()).call(_event("GET", path, dict(query, showArchived="true")))
        assert response["statusCode"] == 400
        assert "Asset not found" in _message(response)

    @pytest.mark.parametrize("read", _READS, ids=_READ_IDS)
    def test_without_the_flag_existence_is_still_checked_first(self, read):
        _id, path, query = read
        harness = _Harness(_Table(), authorized=_live_only)
        response = harness.call(_event("GET", path, query))
        assert response["statusCode"] == 400
        assert "Asset not found" in _message(response)
        assert harness.authorized_objects == []
