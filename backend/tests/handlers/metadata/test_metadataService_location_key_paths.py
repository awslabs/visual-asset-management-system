# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File metadata and attributes by path follow the file routes' path rule (#1943).

In an asset ``A1`` stored at ``A1/`` with a subfolder named like the asset, the CLI names the subfolder file by
its path ``/A1/cloud.las`` and the web's file panel by its full key ``A1/A1/cloud.las``. The request models
added a leading ``/`` to every path and the records were keyed after dropping a leading ``/{assetId}/``, so
both forms read and wrote the root file's records (``/cloud.las``). The models now keep the caller's form, a
leading ``/`` marks a path in the asset, and a value without one is the full key when the asset holds it:
both forms of one file key the same records, and the root file's records stay its own. Each case runs the
real route handler with the real request model; only the asset table, S3 and the record function are stubbed.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.metadata import metadataService

ITEM = {"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}

# (location, value, the path the records are keyed by)
CASES = [
    ("A1/", "/A1/cloud.las", "/A1/cloud.las"),
    ("A1/", "A1/A1/cloud.las", "/A1/cloud.las"),
    ("A1/", "/cloud.las", "/cloud.las"),
    ("A1/", "A1/cloud.las", "/cloud.las"),
    ("vams/B1/", "/vams/B1/x.las", "/vams/B1/x.las"),
    ("vams/B1/", "vams/B1/vams/B1/x.las", "/vams/B1/x.las"),
    ("vams/B1/", "vams/B1/x.las", "/x.las"),
    ("p1/model.glb", "/p1/model.glb/sub/x.obj", "/p1/model.glb/sub/x.obj"),
    ("p1/model.glb", "p1/model.glb/sub/x.obj", "/sub/x.obj"),
]


def _asset(location):
    return {"databaseId": "db1", "assetId": "A1", "bucketId": "b1", "assetLocation": {"Key": location}}


def _handlers():
    return {
        "GET": (metadataService.handle_file_metadata_get, "get_file_metadata", lambda value: {
            "queryStringParameters": {"filePath": value, "type": "metadata"}}),
        "POST": (metadataService.handle_file_metadata_post, "create_file_metadata", lambda value: {
            "body": json.dumps({"filePath": value, "type": "metadata", "metadata": [ITEM]})}),
        "PUT": (metadataService.handle_file_metadata_put, "update_file_metadata", lambda value: {
            "body": json.dumps({"filePath": value, "type": "metadata", "metadata": [ITEM]})}),
        "DELETE": (metadataService.handle_file_metadata_delete, "delete_file_metadata", lambda value: {
            "body": json.dumps({"filePath": value, "type": "metadata", "metadataKeys": ["k"]})}),
    }


def _keyed_path(method, location, value):
    """Run one file-metadata route and return the path the record function was handed."""
    handler, record_function, request = _handlers()[method]
    event = {"pathParameters": {"databaseId": "db1", "assetId": "A1"}, "queryStringParameters": None,
             "body": None, **request(value)}
    table = MagicMock()
    table.get_item.return_value = {"Item": _asset(location)}
    s3 = MagicMock()
    s3.list_object_versions.return_value = {"Versions": []}
    response = MagicMock()
    response.dict.return_value = {}
    recorder = MagicMock(return_value=response)
    with patch.object(metadataService, "asset_storage_table", table), \
            patch.object(metadataService, "get_bucket_details", return_value={"bucketName": "b"}), \
            patch.object(metadataService, "s3", s3), \
            patch.object(metadataService, "claims_and_roles", {"tokens": ["tester"], "roles": []}), \
            patch.object(metadataService, record_function, recorder):
        result = handler(event)
        assert result["statusCode"] == 200, result
        handed = recorder.call_args.args[2]
        # The record function resolves the path it is handed to the path the file is listed at
        return metadataService.file_record_path("db1", "A1", handed if isinstance(handed, str) else handed.filePath)


@pytest.mark.unit
class TestBothFormsOfOneFileKeyItsOwnRecords:
    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
    @pytest.mark.parametrize("location,value,keyed", CASES)
    def test_the_route_keys_the_records_by_the_files_listed_path(self, method, location, value, keyed):
        assert _keyed_path(method, location, value) == keyed
