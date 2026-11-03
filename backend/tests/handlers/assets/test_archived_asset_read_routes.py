# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#870: an archived asset's files and versions read with showArchived; every write stays refused.

Archiving rewrites the asset record under "<databaseId>#deleted" and leaves its versions, file
versions and metadata keyed by the original database id. The file and version read routes looked
the record up in the live partition only, so an archived asset's page answered 400 "Asset not
found in database" on every panel. With showArchived=true a read route also resolves the archived
record; without it, and on every write route, the archived asset stays not found.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402
from backend.backend.handlers.assets import assetVersions  # noqa: F401,E402

_DB = "db1"
_ASSET = "asset-1"
_BASE = f"/database/{_DB}/assets/{_ASSET}"
_NOT_FOUND = "Asset not found in database"
_INVALID = "showArchived parameter must be a valid boolean value (true/false)"


def _archived_row(with_status=True):
    row = {
        "databaseId": f"{_DB}#deleted",
        "assetId": _ASSET,
        "assetName": "archived asset",
        "bucketId": "bucket-1",
        "assetLocation": {"Key": f"{_ASSET}/"},
        "currentVersionId": "1",
        "archivedAt": "2026-10-27T00:00:00",
        "archivedBy": "user1",
    }
    if with_status:
        row["status"] = "archived"
    return row


def _live_row():
    return {
        "databaseId": _DB,
        "assetId": _ASSET,
        "assetName": "live asset",
        "bucketId": "bucket-1",
        "assetLocation": {"Key": f"{_ASSET}/"},
        "currentVersionId": "1",
    }


class _Table:
    """An asset table answering get_item from the rows it holds, recording each key read."""

    def __init__(self, *rows):
        self.rows = {(r["databaseId"], r["assetId"]): dict(r) for r in rows}
        self.keys_read = []

    def get_item(self, Key):
        self.keys_read.append((Key["databaseId"], Key["assetId"]))
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": dict(row)} if row else {}


def _event(method, path, path_params, query=None, body=None):
    return {
        "path": path,
        "httpMethod": method,
        "requestContext": {"identity": {"sourceIp": "10.0.0.1"},
                           "authorizer": {"cognito:username": "alice"}},
        "pathParameters": dict(path_params),
        "queryStringParameters": dict(query) if query else None,
        "headers": {},
        "body": json.dumps(body) if body is not None else None,
    }


def _allowing_enforcer():
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = True
    enforcer.return_value.enforce.return_value = True
    return enforcer


def _message(response):
    return json.loads(response["body"]).get("message")


# ---------------------------------------------------------------------------
# The shared lookup and the parameter parser
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGetAssetRecord:
    def _helper(self):
        import importlib.util
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "common", "dynamodb.py")
        spec = importlib.util.spec_from_file_location("_real_dynamodb_for_870", os.path.abspath(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.get_asset_record

    def test_a_live_asset_is_read_once_with_or_without_the_flag(self):
        get_asset_record = self._helper()
        for flag in (False, True):
            table = _Table(_live_row())
            assert get_asset_record(table, _DB, _ASSET, include_archived=flag)["assetName"] == "live asset"
            assert table.keys_read == [(_DB, _ASSET)]

    def test_an_archived_asset_is_not_found_without_the_flag(self):
        table = _Table(_archived_row())
        assert self._helper()(table, _DB, _ASSET) is None
        assert table.keys_read == [(_DB, _ASSET)]

    def test_an_archived_asset_resolves_with_the_flag_and_keeps_its_stored_id(self):
        table = _Table(_archived_row())
        record = self._helper()(table, _DB, _ASSET, include_archived=True)
        assert record["databaseId"] == f"{_DB}#deleted"
        assert record["status"] == "archived"
        assert table.keys_read == [(_DB, _ASSET), (f"{_DB}#deleted", _ASSET)]

    def test_a_legacy_archived_row_without_status_reads_as_archived(self):
        table = _Table(_archived_row(with_status=False))
        assert self._helper()(table, _DB, _ASSET, include_archived=True)["status"] == "archived"

    def test_a_stored_archived_id_is_not_suffixed_twice(self):
        table = _Table()
        assert self._helper()(table, f"{_DB}#deleted", _ASSET, include_archived=True) is None
        assert table.keys_read == [(f"{_DB}#deleted", _ASSET)]

    def test_a_missing_asset_is_none_with_the_flag(self):
        assert self._helper()(_Table(), _DB, _ASSET, include_archived=True) is None


@pytest.mark.unit
class TestParseShowArchived:
    @pytest.mark.parametrize("value,expected", [
        (None, False), ("", False), ("false", False), ("FALSE", False), ("0", False), ("no", False),
        ("true", True), ("True", True), ("1", True), ("yes", True), (True, True), (False, False),
    ])
    def test_accepted_values(self, value, expected):
        from common.validators import parse_show_archived
        params = {} if value is None else {"showArchived": value}
        assert parse_show_archived(params) is expected

    @pytest.mark.parametrize("value", ["maybe", "2", "on"])
    def test_other_values_are_refused(self, value):
        from common.validators import parse_show_archived
        assert parse_show_archived({"showArchived": value}) is None

    def test_no_query_parameters(self):
        from common.validators import parse_show_archived
        assert parse_show_archived(None) is False


@pytest.mark.unit
class TestAssetGetStillResolvesArchived:
    """The asset GET delegates to the shared lookup; its behaviour is unchanged."""

    def test_get_asset_details(self):
        from backend.tests.handlers.assets.test_assetService_history import _load
        assetService = _load()
        table = _Table(_archived_row(with_status=False))
        with patch.object(assetService, "asset_table", table):
            assert assetService.get_asset_details(_DB, _ASSET) is None
            assert assetService.get_asset_details(_DB, _ASSET, showArchived=True)["status"] == "archived"


# ---------------------------------------------------------------------------
# assetFiles: listFiles / fileInfo
# ---------------------------------------------------------------------------

def _list_files(table, query, enforcer=None):
    current = MagicMock()
    current.return_value.dict.return_value = {"items": []}
    with patch.object(assetFiles, "asset_table", table), \
            patch.object(assetFiles, "CasbinEnforcer", enforcer or _allowing_enforcer()), \
            patch.object(assetFiles, "get_default_bucket_details",
                         return_value={"bucketName": "bucket"}), \
            patch.object(assetFiles, "list_asset_files_current", current):
        response = assetFiles.lambda_handler(
            _event("GET", f"{_BASE}/listFiles", {"databaseId": _DB, "assetId": _ASSET}, query),
            MagicMock())
    return response, current


@pytest.mark.unit
class TestListFiles:
    def test_archived_asset_without_the_flag_is_not_found(self):
        response, current = _list_files(_Table(_archived_row()), None)
        assert response["statusCode"] == 400
        assert _NOT_FOUND in _message(response)
        current.assert_not_called()

    def test_archived_asset_with_the_flag_lists_its_archived_files(self):
        response, current = _list_files(_Table(_archived_row()), {"showArchived": "true"})
        assert response["statusCode"] == 200
        _db, _asset, asset, _bucket, _key, request_model = current.call_args.args
        assert asset["databaseId"] == f"{_DB}#deleted"
        assert request_model.includeArchived is True

    def test_legacy_archived_row_lists_its_archived_files(self):
        response, current = _list_files(_Table(_archived_row(with_status=False)), {"showArchived": "1"})
        assert response["statusCode"] == 200
        assert current.call_args.args[5].includeArchived is True

    @pytest.mark.parametrize("query", [None, {"showArchived": "true"}], ids=["no-flag", "flag"])
    def test_live_asset_listing_is_unchanged(self, query):
        table = _Table(_live_row())
        response, current = _list_files(table, query)
        assert response["statusCode"] == 200
        assert current.call_args.args[5].includeArchived is False
        assert table.keys_read == [(_DB, _ASSET)]

    def test_live_asset_keeps_include_archived_on_request(self):
        response, current = _list_files(_Table(_live_row()),
                                        {"showArchived": "true", "includeArchived": "true"})
        assert current.call_args.args[5].includeArchived is True

    def test_missing_asset_with_the_flag_is_not_found(self):
        response, current = _list_files(_Table(), {"showArchived": "true"})
        assert response["statusCode"] == 400
        assert _NOT_FOUND in _message(response)

    def test_archived_asset_refused_by_tier2_is_not_listed(self):
        enforcer = _allowing_enforcer()
        enforcer.return_value.enforce.return_value = False
        response, current = _list_files(_Table(_archived_row()), {"showArchived": "true"}, enforcer)
        assert response["statusCode"] == 400
        assert "Not authorized" in _message(response)
        current.assert_not_called()
        evaluated = enforcer.return_value.enforce.call_args.args[0]
        assert evaluated["databaseId"] == f"{_DB}#deleted"
        assert evaluated["object__type"] == "asset"

    def test_invalid_flag_is_refused(self):
        response, current = _list_files(_Table(_archived_row()), {"showArchived": "maybe"})
        assert response["statusCode"] == 400
        assert _message(response) == _INVALID
        current.assert_not_called()


@pytest.mark.unit
class TestFileInfo:
    def _invoke(self, query):
        info = MagicMock()
        info.return_value.dict.return_value = {"fileName": "a.txt"}
        with patch.object(assetFiles, "CasbinEnforcer", _allowing_enforcer()), \
                patch.object(assetFiles, "get_file_info", info):
            response = assetFiles.lambda_handler(
                _event("GET", f"{_BASE}/fileInfo", {"databaseId": _DB, "assetId": _ASSET}, query),
                MagicMock())
        return response, info

    def test_the_flag_reaches_the_lookup(self):
        response, info = self._invoke({"filePath": "/a.txt", "showArchived": "true"})
        assert response["statusCode"] == 200
        assert info.call_args.kwargs["show_archived"] is True

    def test_without_the_flag_the_lookup_is_live_only(self):
        response, info = self._invoke({"filePath": "/a.txt"})
        assert info.call_args.kwargs["show_archived"] is False

    def test_invalid_flag_is_refused(self):
        response, info = self._invoke({"filePath": "/a.txt", "showArchived": "x"})
        assert response["statusCode"] == 400
        info.assert_not_called()

    def test_get_file_info_resolves_the_archived_record(self):
        table = _Table(_archived_row())
        sentinel = RuntimeError("stop after the lookup")
        with patch.object(assetFiles, "asset_table", table), \
                patch.object(assetFiles, "CasbinEnforcer", _allowing_enforcer()), \
                patch.object(assetFiles, "get_asset_s3_location", side_effect=sentinel) as location:
            with pytest.raises(RuntimeError):
                assetFiles.get_file_info(_DB, _ASSET, "/a.txt", False, {"tokens": ["alice"]},
                                         show_archived=True)
        assert location.call_args.args[0]["databaseId"] == f"{_DB}#deleted"

    def test_get_file_info_without_the_flag_is_not_found(self):
        with patch.object(assetFiles, "asset_table", _Table(_archived_row())), \
                patch.object(assetFiles, "CasbinEnforcer", _allowing_enforcer()):
            with pytest.raises(Exception) as raised:
                assetFiles.get_file_info(_DB, _ASSET, "/a.txt", False, {"tokens": ["alice"]})
        assert _NOT_FOUND in str(raised.value)


# Every assetFiles write route, sent with showArchived=true against an archived asset.
_FILE_WRITE_ROUTES = [
    ("moveFile", "POST", f"{_BASE}/moveFile", {"sourcePath": "/a.txt", "destinationPath": "/b.txt"}),
    ("copyFile", "POST", f"{_BASE}/copyFile", {"sourcePath": "/a.txt", "destinationPath": "/b.txt"}),
    ("unarchiveFile", "POST", f"{_BASE}/unarchiveFile", {"filePath": "/a.txt"}),
    ("createFolder", "POST", f"{_BASE}/createFolder", {"relativeKey": "newdir/"}),
    ("archiveFile", "DELETE", f"{_BASE}/archiveFile", {"filePath": "/a.txt"}),
    ("deleteFile", "DELETE", f"{_BASE}/deleteFile", {"filePath": "/a.txt", "confirmPermanentDelete": True}),
    ("deleteAssetPreview", "DELETE", f"{_BASE}/deleteAssetPreview", None),
    ("deleteAuxiliaryPreviewAssetFiles", "DELETE", f"{_BASE}/deleteAuxiliaryPreviewAssetFiles",
     {"filePath": "/a.txt"}),
    ("revertFileVersion", "POST", f"{_BASE}/revertFileVersion/v1", {"filePath": "/a.txt"}),
    ("setPrimaryFile", "PUT", f"{_BASE}/setPrimaryFile", {"filePath": "/a.txt", "primaryType": "primary"}),
]


@pytest.mark.unit
class TestFileWritesStayRefused:
    @pytest.mark.parametrize("route", _FILE_WRITE_ROUTES, ids=[r[0] for r in _FILE_WRITE_ROUTES])
    def test_write_on_an_archived_asset_is_refused(self, route):
        _id, method, path, body = route
        params = {"databaseId": _DB, "assetId": _ASSET}
        if _id == "revertFileVersion":
            params["versionId"] = "v1"
        table = _Table(_archived_row())
        s3 = MagicMock()
        with patch.object(assetFiles, "asset_table", table), \
                patch.object(assetFiles, "CasbinEnforcer", _allowing_enforcer()), \
                patch.object(assetFiles, "s3_client", s3):
            response = assetFiles.lambda_handler(
                _event(method, path, params, {"showArchived": "true"}, body), MagicMock())
        assert response["statusCode"] == 400, response
        assert _NOT_FOUND in _message(response)
        assert (f"{_DB}#deleted", _ASSET) not in table.keys_read
        for write in ("put_object", "delete_object", "copy_object", "delete_objects"):
            getattr(s3, write).assert_not_called()


# ---------------------------------------------------------------------------
# assetVersions: getVersions / getVersion, and the version writes
# ---------------------------------------------------------------------------

def _v2(event):
    """The HTTP API (v2) shape; assetVersions reads requestContext.http directly."""
    event["requestContext"]["http"] = {"method": event["httpMethod"], "path": event["path"]}
    return event


def _versions_call(method, path, params, query, table, body=None):
    client = MagicMock()
    client.query.return_value = {"Items": []}
    # The suite's mock safeLogger has no .debug(), which lambda_handler calls
    with patch.object(assetVersions, "asset_table", table), \
            patch.object(assetVersions, "CasbinEnforcer", _allowing_enforcer()), \
            patch.object(assetVersions, "dynamodb_client", client), \
            patch.object(assetVersions, "logger", MagicMock()):
        return assetVersions.lambda_handler(_v2(_event(method, path, params, query, body)), MagicMock())


@pytest.mark.unit
class TestVersionReads:
    _params = {"databaseId": _DB, "assetId": _ASSET}

    def test_get_versions_without_the_flag_is_not_found(self):
        response = _versions_call("GET", f"{_BASE}/getVersions", self._params, None,
                                  _Table(_archived_row()))
        assert response["statusCode"] == 400
        assert _NOT_FOUND in _message(response)

    def test_get_versions_with_the_flag_reads_the_original_key(self):
        client = MagicMock()
        client.query.return_value = {"Items": []}
        with patch.object(assetVersions, "asset_table", _Table(_archived_row())), \
                patch.object(assetVersions, "CasbinEnforcer", _allowing_enforcer()), \
                patch.object(assetVersions, "dynamodb_client", client), \
                patch.object(assetVersions, "logger", MagicMock()):
            response = assetVersions.lambda_handler(
                _v2(_event("GET", f"{_BASE}/getVersions", self._params, {"showArchived": "true"})),
                MagicMock())
        assert response["statusCode"] == 200, response
        query = client.query.call_args.kwargs
        assert query["ExpressionAttributeValues"][":pk"] == {"S": f"{_DB}:{_ASSET}"}

    def test_get_versions_of_a_live_asset_is_unchanged(self):
        table = _Table(_live_row())
        response = _versions_call("GET", f"{_BASE}/getVersions", self._params,
                                  {"showArchived": "true"}, table)
        assert response["statusCode"] == 200
        assert table.keys_read == [(_DB, _ASSET)]

    def test_get_version_without_the_flag_is_not_found(self):
        params = dict(self._params, assetVersionId="1")
        response = _versions_call("GET", f"{_BASE}/getVersion/1", params, None, _Table(_archived_row()))
        assert response["statusCode"] == 400
        assert _NOT_FOUND in _message(response)

    def test_get_version_with_the_flag_resolves_the_archived_record(self):
        params = dict(self._params, assetVersionId="1")
        table = _Table(_archived_row())
        response = _versions_call("GET", f"{_BASE}/getVersion/1", params, {"showArchived": "true"}, table)
        assert (f"{_DB}#deleted", _ASSET) in table.keys_read
        assert _NOT_FOUND not in (_message(response) or ""), response

    def test_get_version_invalid_flag_is_refused(self):
        params = dict(self._params, assetVersionId="1")
        response = _versions_call("GET", f"{_BASE}/getVersion/1", params, {"showArchived": "x"},
                                  _Table(_archived_row()))
        assert response["statusCode"] == 400
        assert _message(response) == _INVALID


_VERSION_WRITE_ROUTES = [
    ("createVersion", "POST", f"{_BASE}/createVersion", {}, {"useLatestFiles": True, "comment": "c"}),
    ("revertAssetVersion", "POST", f"{_BASE}/revertAssetVersion/1", {"assetVersionId": "1"}, {}),
    ("updateVersion", "PUT", f"{_BASE}/assetversions/1", {"assetVersionId": "1"}, {"comment": "c"}),
    ("archiveVersion", "POST", f"{_BASE}/assetversions/1/archive", {"assetVersionId": "1"}, {}),
    ("unarchiveVersion", "POST", f"{_BASE}/assetversions/1/unarchive", {"assetVersionId": "1"}, {}),
]


@pytest.mark.unit
class TestVersionWritesStayRefused:
    @pytest.mark.parametrize("route", _VERSION_WRITE_ROUTES, ids=[r[0] for r in _VERSION_WRITE_ROUTES])
    def test_write_on_an_archived_asset_is_refused(self, route):
        _id, method, path, extra_params, body = route
        params = dict({"databaseId": _DB, "assetId": _ASSET}, **extra_params)
        table = _Table(_archived_row())
        response = _versions_call(method, path, params, {"showArchived": "true"}, table, body)
        assert response["statusCode"] in (400, 404), response
        assert (f"{_DB}#deleted", _ASSET) not in table.keys_read
