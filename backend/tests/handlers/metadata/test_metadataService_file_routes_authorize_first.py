# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File metadata and attribute reads and deletes check permission on the asset before they look up the file.

GET (including a read of an asset version) and DELETE read the asset, check the caller's permission on it, and
only then resolve the path the file is listed at (one ``ListObjectVersions`` for a single-file asset). A caller
without permission therefore receives ``403`` with no Amazon S3 read. POST and PUT resolve the path inside the
service function, directly before the file check, so the file check and the records keep using the path the
file is listed at. An allowed caller is unchanged on every route: a missing file still answers ``400`` on POST
and PUT, and records are keyed by the path the file is listed at.
"""

import contextlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.metadata import metadataService


LOCATION = "p1/model.glb"
LISTED_PATH = "/model.glb"
FULL_KEY_PATH = "p1/model.glb"
PATH_PARAMETERS = {"databaseId": "db1", "assetId": "asset1"}
LISTED_RECORD_KEY = "db1:asset1:" + LISTED_PATH


def _asset():
    return {"databaseId": "db1", "assetId": "asset1", "bucketId": "bucket-1", "assetLocation": {"Key": LOCATION}}


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    modules = [
        sys.modules.get("common.metadataSchemaValidation"),
        sys.modules.get("backend.backend.common.metadataSchemaValidation"),
    ]
    for module in modules:
        if module is not None:
            module._schema_cache.clear()
    yield
    for module in modules:
        if module is not None:
            module._schema_cache.clear()


class _Harness:
    """The module globals a file metadata route touches, with every Amazon S3 and asset read counted."""

    def __init__(self, allowed, file_exists=True, caller="user1"):
        self.s3 = MagicMock()
        # A single-file asset with no companion entry: its object is listed at '/model.glb'.
        self.s3.list_object_versions.return_value = {"Versions": []}
        if file_exists:
            self.s3.head_object.return_value = {}
        else:
            self.s3.head_object.side_effect = ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

        self.asset_table = MagicMock()
        self.asset_table.get_item.return_value = {"Item": _asset()}
        self.database_table = MagicMock()
        self.database_table.get_item.return_value = {"Item": {"databaseId": "db1"}}

        page_iterator = MagicMock()
        page_iterator.build_full_result.return_value = {"Items": []}
        paginator = MagicMock()
        paginator.paginate.return_value = page_iterator
        self.paginator = paginator
        self.client = MagicMock()
        self.client.get_paginator.return_value = paginator
        self.client.query.return_value = {"Items": []}

        self.file_table = MagicMock()
        self.file_table.get_item.return_value = {"Item": {"metadataKey": "k"}}

        enforcer = MagicMock()
        enforcer.enforce.return_value = allowed
        self.enforcer_cls = MagicMock(return_value=enforcer)
        self.batch_write = MagicMock()
        self.claims = {"tokens": [caller], "roles": [], "mfaEnabled": False}
        self._stack = contextlib.ExitStack()

    def __enter__(self):
        for target, replacement in (
            ("s3", self.s3),
            ("asset_storage_table", self.asset_table),
            ("database_storage_table", self.database_table),
            ("dynamodb_client", self.client),
            ("asset_file_metadata_table", self.file_table),
            ("file_attribute_table", self.file_table),
            ("CasbinEnforcer", self.enforcer_cls),
            ("batch_write_with_retry", self.batch_write),
            ("claims_and_roles", self.claims),
        ):
            self._stack.enter_context(patch.object(metadataService, target, replacement))
        self._stack.enter_context(
            patch.object(metadataService, "get_bucket_details", return_value={"bucketName": "b"}))
        self._stack.enter_context(patch.object(metadataService, "validate_asset_version_exists"))
        # An asset version's snapshot holds the record of the file at its listed path and of one other file.
        version_items = [
            SimpleNamespace(filePath=path, type="metadata", metadataKey=key, metadataValue="v",
                            metadataValueType="string")
            for path, key in ((LISTED_PATH, "k"), ("/other.glb", "other"))
        ]
        self._stack.enter_context(
            patch.object(metadataService, "get_asset_metadata_version", return_value=version_items))
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)

    def keyed_paths(self):
        """The composite keys the route queried the file's records by."""
        return [call.kwargs["ExpressionAttributeValues"][":pkValue"]["S"]
                for call in self.paginator.paginate.call_args_list]


def _get(file_path, asset_version_id=None):
    query = {"filePath": file_path, "type": "metadata"}
    if asset_version_id:
        query["assetVersionId"] = asset_version_id
    return metadataService.handle_file_metadata_get(
        {"pathParameters": dict(PATH_PARAMETERS), "queryStringParameters": query})


def _post(file_path):
    body = {"filePath": file_path, "type": "metadata", "metadata": [{"metadataKey": "k", "metadataValue": "v"}]}
    return metadataService.handle_file_metadata_post(
        {"pathParameters": dict(PATH_PARAMETERS), "body": json.dumps(body)})


def _put(file_path, update_type="update"):
    body = {"filePath": file_path, "type": "metadata", "updateType": update_type,
            "metadata": [{"metadataKey": "k", "metadataValue": "v"}]}
    return metadataService.handle_file_metadata_put(
        {"pathParameters": dict(PATH_PARAMETERS), "body": json.dumps(body)})


def _delete(file_path):
    body = {"filePath": file_path, "type": "metadata", "metadataKeys": ["k"]}
    return metadataService.handle_file_metadata_delete(
        {"pathParameters": dict(PATH_PARAMETERS), "body": json.dumps(body)})


ROUTES = [
    pytest.param(_get, id="get"),
    pytest.param(lambda path: _get(path, asset_version_id="2"), id="get-version"),
    pytest.param(_post, id="post"),
    pytest.param(_put, id="put-update"),
    pytest.param(lambda path: _put(path, update_type="replace_all"), id="put-replace-all"),
    pytest.param(_delete, id="delete"),
]

CURRENT_ROUTES = [route for route in ROUTES if route.id != "get-version"]
READS_AND_DELETES = [route for route in ROUTES if route.id in ("get", "get-version", "delete")]

WRITES = [
    pytest.param(_post, id="post"),
    pytest.param(_put, id="put-update"),
    pytest.param(lambda path: _put(path, update_type="replace_all"), id="put-replace-all"),
]


@pytest.mark.unit
class TestADeniedCallerIsRefusedBeforeTheFileIsRead:
    @pytest.mark.parametrize("route", READS_AND_DELETES)
    @pytest.mark.parametrize("file_path", [LISTED_PATH, FULL_KEY_PATH])
    def test_403_with_no_s3_read_and_one_asset_read(self, route, file_path):
        with _Harness(allowed=False) as harness:
            response = route(file_path)

        assert response["statusCode"] == 403
        harness.s3.list_object_versions.assert_not_called()
        harness.s3.head_object.assert_not_called()
        assert harness.asset_table.get_item.call_count == 1
        assert harness.keyed_paths() == []


@pytest.mark.unit
class TestAnAllowedCallerIsUnchanged:
    @pytest.mark.parametrize("route", WRITES)
    def test_control_a_missing_file_still_answers_400(self, route):
        with _Harness(allowed=True, file_exists=False) as harness:
            response = route(LISTED_PATH)

        assert response["statusCode"] == 400
        assert "File not found in S3" in json.loads(response["body"])["message"]
        harness.s3.head_object.assert_called_once()
        harness.batch_write.assert_not_called()

    @pytest.mark.parametrize("route", CURRENT_ROUTES)
    @pytest.mark.parametrize("file_path", [LISTED_PATH, FULL_KEY_PATH])
    def test_control_the_request_succeeds_keyed_by_the_listed_path(self, route, file_path):
        with _Harness(allowed=True, caller="SYSTEM_USER") as harness:
            response = route(file_path)

        assert response["statusCode"] == 200, response["body"]
        keyed = set(harness.keyed_paths())
        assert keyed or harness.batch_write.call_args_list, "the route read or wrote no record"
        assert keyed <= {LISTED_RECORD_KEY}
        for call in harness.batch_write.call_args_list:
            for request in call.args[1]:
                item = (request.get("PutRequest") or {}).get("Item") or (request.get("DeleteRequest") or {}).get("Key")
                assert item["databaseId:assetId:filePath"]["S"] == LISTED_RECORD_KEY

    @pytest.mark.parametrize("route", WRITES)
    def test_control_a_write_checks_the_files_own_object(self, route):
        with _Harness(allowed=True, caller="SYSTEM_USER") as harness:
            response = route(FULL_KEY_PATH)

        assert response["statusCode"] == 200, response["body"]
        assert harness.s3.head_object.call_args.kwargs["Key"] == LOCATION
        assert harness.batch_write.called

    @pytest.mark.parametrize("file_path", [LISTED_PATH, FULL_KEY_PATH])
    def test_control_a_version_read_returns_the_listed_files_records(self, file_path):
        with _Harness(allowed=True) as harness:
            response = _get(file_path, asset_version_id="2")

        assert response["statusCode"] == 200, response["body"]
        keys = [item["metadataKey"] for item in json.loads(response["body"])["metadata"]]
        assert keys == ["k"]
        harness.s3.head_object.assert_not_called()
