# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File metadata and attributes are keyed by the path a file is listed at, whichever form names it (#1853).

The web's file panel names a file by its full S3 key (the file list's ``key``); the CLI names it by the path
the file list shows (``relativePath``). The file metadata routes keyed the records by the request path with
only a leading ``/{assetId}/`` dropped, so for an asset whose location is not ``{assetId}/`` (an asset
created from one object, an asset on an existing folder, a bucket with a base prefix) the two clients read
and wrote different records, and the web's create and update on a custom location answered "File not
found in S3". Each route now keys both forms by the listed path. An asset at ``{assetId}/`` is the control:
the dropped segment already gave its listed path.

The read records the keyed path on its action record, as the writes do, without another read.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.metadata import metadataService

_DB = "db1"
_ASSET = "a1"
_ROUTE = f"/database/{_DB}/assets/{_ASSET}/metadata/file"
_COMPOSITE = "databaseId:assetId:filePath"
_ITEM = {"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}

# (kind, asset location, the file's S3 key, the path the file list shows it at)
KINDS = [
    ("folder at its asset id", f"{_ASSET}/", f"{_ASSET}/sub/x.obj", "/sub/x.obj"),
    ("folder under a bucket base prefix", f"vams/{_ASSET}/", f"vams/{_ASSET}/sub/x.obj", "/sub/x.obj"),
    ("custom folder location", "projects/site-a/", "projects/site-a/sub/x.obj", "/sub/x.obj"),
    ("single-file asset, its own file", "projects/model.glb", "projects/model.glb", "/model.glb"),
    ("single-file asset, a companion file", "projects/model.glb", "projects/model.glb/sub/x.obj",
     "/sub/x.obj"),
    ("single-file asset stored under a folder named like its id", f"{_ASSET}/model.glb",
     f"{_ASSET}/model.glb/sub/x.obj", "/sub/x.obj"),
]
KIND_IDS = [kind[0] for kind in KINDS]


def _record(listed):
    """The composite key of a file's records: databaseId:assetId:filePath."""
    return ":".join((_DB, _ASSET, listed))


def _stored_row(listed, metadata_type="metadata"):
    """One stored record of a file, as the low-level DynamoDB client returns it."""
    prefix = "metadata" if metadata_type == "metadata" else "attribute"
    return {f"{prefix}Key": {"S": "k"}, _COMPOSITE: {"S": _record(listed)}, f"{prefix}Value": {"S": "v"},
            f"{prefix}ValueType": {"S": "string"}}


def _asset(location, archived=False):
    return {"databaseId": f"{_DB}#deleted" if archived else _DB, "assetId": _ASSET, "bucketId": "b1",
            "assetLocation": {"Key": location}}


def _paths_in(value, found):
    """Every composite file-path key value in a recorded call's arguments."""
    if isinstance(value, dict):
        for name, inner in value.items():
            if name in (_COMPOSITE, ":pkValue"):
                found.append(inner.get("S") if isinstance(inner, dict) else inner)
            else:
                _paths_in(inner, found)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            _paths_in(inner, found)
    return found


class _Table:
    def __init__(self, *rows):
        self.rows = {(row["databaseId"], row["assetId"]): dict(row) for row in rows}
        self.reads = 0

    def get_item(self, Key):
        self.reads += 1
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": dict(row)} if row else {}


class _RecordTable:
    """A metadata or attribute table whose get_item finds the records stored under a composite key."""

    def __init__(self, stored):
        self.stored = stored

    def get_item(self, Key):
        return {"Item": {"databaseId:assetId:filePath": Key[_COMPOSITE]}} if Key[_COMPOSITE] in self.stored else {}


class _Harness:
    """Runs lambda_handler with the asset table, S3, the metadata tables and authorization stubbed.

    ``allowed`` is the Tier-2 verdict on the asset; the route check (Tier 1) always passes.
    """

    def __init__(self, asset, existing_keys=(), snapshot=(), nested_location=None, stored=None, allowed=True):
        self.table = _Table(asset)
        self.allowed = allowed
        self.log_action = MagicMock()
        self.existing_keys = set(existing_keys)
        self.snapshot = list(snapshot)
        self.nested_location = nested_location
        self.stored = stored or {}
        self.client = MagicMock()
        self.client.get_paginator.side_effect = self._paginator
        self.client.batch_write_item.return_value = {"UnprocessedItems": {}}
        self.queries = []
        self.s3 = MagicMock()
        self.s3.head_object.side_effect = self._head

    def _paginator(self, _name):
        harness = self

        class _Pages:
            def paginate(self, **kwargs):
                harness.queries.append(kwargs)
                composite = kwargs["ExpressionAttributeValues"][":pkValue"]["S"]
                result = MagicMock()
                result.build_full_result.return_value = {
                    "Items": [dict(item) for item in harness.stored.get(composite, [])]}
                return result

        return _Pages()

    def _head(self, Bucket, Key):
        if Key in self.existing_keys:
            return {}
        raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def call(self, method, query=None, body=None):
        enforcer = MagicMock()
        enforcer.return_value.enforceAPI.return_value = True
        enforcer.return_value.enforce.return_value = self.allowed
        nested = MagicMock()
        nested.holder.side_effect = lambda key: self.nested_location if (
            self.nested_location and key.startswith(self.nested_location)) else None
        event = {
            "requestContext": {"http": {"method": method, "path": _ROUTE}},
            "pathParameters": {"databaseId": _DB, "assetId": _ASSET},
            "queryStringParameters": query,
            "body": json.dumps(body) if body is not None else None,
        }
        with patch.object(metadataService, "asset_storage_table", self.table), \
                patch.object(metadataService, "CasbinEnforcer", enforcer), \
                patch.object(metadataService, "check_entity_authorization", return_value=self.allowed), \
                patch.object(metadataService, "log_action", self.log_action), \
                patch.object(metadataService, "dynamodb_client", self.client), \
                patch.object(metadataService, "asset_file_metadata_table", _RecordTable(self.stored)), \
                patch.object(metadataService, "file_attribute_table", _RecordTable(self.stored)), \
                patch.object(metadataService, "s3", self.s3), \
                patch.object(metadataService, "get_bucket_details", return_value={"bucketName": "bkt"}), \
                patch.object(metadataService, "nested_locations_of_asset", return_value=nested), \
                patch.object(metadataService, "get_aggregated_schemas", return_value=[]), \
                patch.object(metadataService, "get_database_config", return_value={}), \
                patch.object(metadataService, "validate_asset_version_exists", return_value=True), \
                patch.object(metadataService, "get_asset_metadata_version", return_value=self.snapshot):
            return metadataService.lambda_handler(event, MagicMock())

    def record_paths(self):
        """The composite file-path keys every metadata query and write of the request used."""
        found = _paths_in([query.get("ExpressionAttributeValues", {}) for query in self.queries], [])
        for call in self.client.method_calls:
            _paths_in(call.kwargs, found)
        return found

    def written_paths(self):
        """The composite file-path keys of the records the request wrote or deleted."""
        return _paths_in([call.kwargs for call in self.client.batch_write_item.call_args_list], [])

    def checked_keys(self):
        return [call.kwargs["Key"] for call in self.s3.head_object.call_args_list]

    def logged_paths(self, action):
        """The file_path of every action record the request wrote for ``action``."""
        return [call.kwargs.get("file_path") for call in self.log_action.call_args_list
                if len(call.args) > 1 and call.args[1] == action]


def _status(response):
    return response["statusCode"], json.loads(response["body"]).get("message")


@pytest.mark.unit
class TestEveryFormKeysTheListedPath:
    """Each route keys the web's full key and the CLI's listed path by the same records."""

    @pytest.mark.parametrize("kind,location,key,listed", KINDS, ids=KIND_IDS)
    @pytest.mark.parametrize("form", ["full key", "listed path"])
    @pytest.mark.parametrize("metadata_type", ["metadata", "attribute"])
    def test_get_reads_the_listed_paths_records(self, kind, location, key, listed, form, metadata_type):
        path = "/" + key if form == "full key" else listed
        harness = _Harness(_asset(location), stored={_record(listed): [_stored_row(listed, metadata_type)]})
        response = harness.call("GET", {"filePath": path, "type": metadata_type})
        assert response["statusCode"] == 200, response
        assert harness.record_paths() == [_record(listed)]
        assert [(item["metadataKey"], item["filePath"]) for item in json.loads(response["body"])["metadata"]] \
            == [("k", listed)]

    @pytest.mark.parametrize("kind,location,key,listed", KINDS, ids=KIND_IDS)
    @pytest.mark.parametrize("form", ["full key", "listed path"])
    def test_create_checks_the_file_and_writes_the_listed_paths_records(self, kind, location, key, listed, form):
        path = "/" + key if form == "full key" else listed
        harness = _Harness(_asset(location), existing_keys={key})
        response = harness.call("POST", body={"filePath": path, "type": "metadata", "metadata": [_ITEM]})
        assert response["statusCode"] == 200, response
        assert harness.checked_keys() == [key]
        assert harness.written_paths() == [_record(listed)]
        assert set(harness.record_paths()) == {_record(listed)}

    @pytest.mark.parametrize("kind,location,key,listed", KINDS, ids=KIND_IDS)
    @pytest.mark.parametrize("form", ["full key", "listed path"])
    def test_update_checks_the_file_and_writes_the_listed_paths_records(self, kind, location, key, listed, form):
        path = "/" + key if form == "full key" else listed
        harness = _Harness(_asset(location), existing_keys={key})
        response = harness.call("PUT", body={"filePath": path, "type": "attribute", "metadata": [_ITEM],
                                             "updateType": "update"})
        assert response["statusCode"] == 200, response
        assert harness.checked_keys() == [key]
        assert harness.written_paths() == [_record(listed)]
        assert set(harness.record_paths()) == {_record(listed)}

    @pytest.mark.parametrize("kind,location,key,listed", KINDS, ids=KIND_IDS)
    @pytest.mark.parametrize("form", ["full key", "listed path"])
    def test_delete_removes_the_listed_paths_records(self, kind, location, key, listed, form):
        path = "/" + key if form == "full key" else listed
        harness = _Harness(_asset(location), stored={_record(listed): [_stored_row(listed)]})
        response = harness.call("DELETE", body={"filePath": path, "type": "metadata", "metadataKeys": ["k"]})
        assert response["statusCode"] == 200, response
        assert harness.written_paths() == [_record(listed)]
        assert set(harness.record_paths()) == {_record(listed)}

    @pytest.mark.parametrize("form", ["full key", "listed path"])
    def test_a_version_snapshot_is_read_at_the_listed_path(self, form):
        path = "/projects/site-a/sub/x.obj" if form == "full key" else "/sub/x.obj"
        row = SimpleNamespace(filePath="/sub/x.obj", type="metadata", metadataKey="k", metadataValue="v",
                              metadataValueType="string")
        harness = _Harness(_asset("projects/site-a/"), snapshot=[row])
        response = harness.call("GET", {"filePath": path, "type": "metadata", "assetVersionId": "1"})
        assert response["statusCode"] == 200, response
        assert [item["metadataKey"] for item in json.loads(response["body"])["metadata"]] == ["k"]

    @pytest.mark.parametrize("form", ["full key", "listed path"])
    def test_an_archived_asset_read_with_show_archived_keys_by_its_location(self, form):
        path = "/projects/site-a/sub/x.obj" if form == "full key" else "/sub/x.obj"
        harness = _Harness(_asset("projects/site-a/", archived=True))
        response = harness.call("GET", {"filePath": path, "type": "metadata", "showArchived": "true"})
        assert response["statusCode"] == 200, response
        assert harness.record_paths() == [_record("/sub/x.obj")]


@pytest.mark.unit
class TestRefusalsStay:
    def test_a_create_for_a_file_that_does_not_exist_is_refused(self):
        harness = _Harness(_asset("projects/site-a/"), existing_keys=set())
        response = harness.call("POST", body={"filePath": "/projects/site-a/sub/x.obj", "type": "metadata",
                                              "metadata": [_ITEM]})
        assert _status(response) == (400, "VAMS General Error: File not found in S3")
        harness.client.batch_write_item.assert_not_called()

    @pytest.mark.parametrize("file_path", ["/projects/site-a/inner/x.obj", "/inner/x.obj"])
    def test_a_nested_assets_file_is_refused_through_the_containing_asset(self, file_path):
        harness = _Harness(_asset("projects/site-a/"), existing_keys={"projects/site-a/inner/x.obj"},
                           nested_location="projects/site-a/inner/")
        response = harness.call("POST", body={"filePath": file_path, "type": "metadata", "metadata": [_ITEM]})
        assert _status(response) == (400, "VAMS General Error: File not found in S3")
        harness.client.batch_write_item.assert_not_called()

    def test_a_path_with_a_dot_dot_segment_is_refused_before_any_read(self):
        harness = _Harness(_asset("projects/site-a/"))
        response = harness.call("GET", {"filePath": "/projects/site-a/../x.obj", "type": "metadata"})
        assert response["statusCode"] == 400
        assert harness.queries == []


# (case, asset location, request path, the path the file list shows it at)
READ_FORMS = [
    ("custom location, full key", "projects/site-a/", "/projects/site-a/sub/x.obj", "/sub/x.obj"),
    ("custom location, asset id segment", "projects/site-a/", f"/{_ASSET}/sub/x.obj", "/sub/x.obj"),
    ("asset id location, full key", f"{_ASSET}/", f"/{_ASSET}/sub/x.obj", "/sub/x.obj"),
    ("single-file asset, full key", "projects/model.glb", "/projects/model.glb", "/model.glb"),
    ("listed path", "projects/site-a/", "/sub/x.obj", "/sub/x.obj"),
]


@pytest.mark.unit
class TestTheReadRecordsTheListedPath:
    """The read's action record names the path its records are keyed by, as the writes' records do."""

    @pytest.mark.parametrize("read", ["current", "version snapshot", "showArchived"])
    @pytest.mark.parametrize("case,location,path,listed", READ_FORMS, ids=[form[0] for form in READ_FORMS])
    def test_the_read_records_the_listed_path(self, read, case, location, path, listed):
        query = {"filePath": path, "type": "metadata"}
        if read == "version snapshot":
            query["assetVersionId"] = "1"
        if read == "showArchived":
            query["showArchived"] = "true"
        row = SimpleNamespace(filePath=listed, type="metadata", metadataKey="k", metadataValue="v",
                              metadataValueType="string")
        harness = _Harness(_asset(location, archived=read == "showArchived"), snapshot=[row],
                           stored={_record(listed): [_stored_row(listed)]})
        response = harness.call("GET", query)
        assert response["statusCode"] == 200, response
        assert harness.logged_paths("metadataGet") == [listed]
        assert [item["filePath"] for item in json.loads(response["body"])["metadata"]] == [listed]

    @pytest.mark.parametrize("show_archived", [False, True], ids=["current", "showArchived"])
    def test_recording_the_listed_path_adds_no_read(self, show_archived):
        query = {"filePath": "/projects/site-a/sub/x.obj", "type": "metadata"}
        if show_archived:
            query["showArchived"] = "true"
        harness = _Harness(_asset("projects/site-a/", archived=show_archived))
        response = harness.call("GET", query)
        assert response["statusCode"] == 200, response
        # One asset read (the archived one also reads the live partition first), one records query, no S3
        assert harness.table.reads == (2 if show_archived else 1)
        assert len(harness.queries) == 1
        harness.s3.head_object.assert_not_called()


@pytest.mark.unit
class TestFileRecordPath:
    """The rule on its own: which records a request path names in each kind of location."""

    def _path(self, location, file_path):
        return metadataService.file_record_path(_asset(location), _ASSET, file_path)

    @pytest.mark.parametrize("file_path", ["/model.glb/model.glb", "/projects/model.glb/model.glb"])
    def test_the_companion_entry_named_like_the_file_keeps_its_own_path(self, file_path):
        assert self._path("projects/model.glb", file_path) == "/model.glb/model.glb"

    @pytest.mark.parametrize("file_path", ["/model.glb.previewFile.png", "/projects/model.glb.previewFile.png"])
    def test_an_own_preview_is_keyed_by_its_listed_path(self, file_path):
        assert self._path("projects/model.glb", file_path) == "/model.glb.previewFile.png"

    def test_a_key_that_only_shares_the_files_characters_is_not_the_file(self):
        assert self._path("projects/model.glb", "/projects/model.glb.bak") == "/projects/model.glb.bak"

    def test_a_leading_asset_id_segment_is_still_dropped_where_the_location_does_not_hold_the_path(self):
        assert self._path(f"vams/{_ASSET}/", f"/{_ASSET}/x.obj") == "/x.obj"

    def test_an_asset_without_a_stored_location_keeps_the_dropped_segment_rule(self):
        asset = {"databaseId": _DB, "assetId": _ASSET}
        assert metadataService.file_record_path(asset, _ASSET, f"/{_ASSET}/x.obj") == "/x.obj"
        assert metadataService.file_record_path(asset, _ASSET, "/projects/x.obj") == "/projects/x.obj"

    def test_an_asset_at_the_bucket_root_holds_no_key(self):
        assert self._path("", "/projects/x.obj") == "/projects/x.obj"
