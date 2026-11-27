# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File metadata and attributes by path follow the file routes' path rule (#1943).

In an asset ``A1`` stored at ``A1/`` with a subfolder named like the asset, the CLI names the subfolder file by
its path ``/A1/cloud.las`` and the web's file panel by its full key ``A1/A1/cloud.las``. The request models
added a leading ``/`` to every path and the records were keyed after dropping a leading ``/{assetId}/``, so
both forms read and wrote the root file's records (``/cloud.las``). The models now keep the caller's form, a
leading ``/`` marks a path in the asset, and a value without one is the full key when the asset holds it:
both forms of one file key the same records, and the root file's records stay its own. Each case runs the
real route handler, request model and service function, which keys the path once the asset is authorized;
only the tables, S3 and the authorization decision are stubbed, and the case reads the key each record query
used.
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
        "GET": (metadataService.handle_file_metadata_get, lambda value: {
            "queryStringParameters": {"filePath": value, "type": "metadata"}}),
        "POST": (metadataService.handle_file_metadata_post, lambda value: {
            "body": json.dumps({"filePath": value, "type": "metadata", "metadata": [ITEM]})}),
        "PUT": (metadataService.handle_file_metadata_put, lambda value: {
            "body": json.dumps({"filePath": value, "type": "metadata", "metadata": [ITEM]})}),
        "DELETE": (metadataService.handle_file_metadata_delete, lambda value: {
            "body": json.dumps({"filePath": value, "type": "metadata", "metadataKeys": ["k"]})}),
    }


def _keyed_path(method, location, value):
    """Run one file-metadata route and return the path its record queries were keyed by."""
    handler, request = _handlers()[method]
    event = {"pathParameters": {"databaseId": "db1", "assetId": "A1"}, "queryStringParameters": None,
             "body": None, **request(value)}
    table = MagicMock()
    table.get_item.return_value = {"Item": _asset(location)}
    s3 = MagicMock()
    s3.list_object_versions.return_value = {"Versions": []}
    keys = []

    class _Pages:
        def paginate(self, **kwargs):
            keys.append(kwargs["ExpressionAttributeValues"][":pkValue"]["S"])
            result = MagicMock()
            result.build_full_result.return_value = {"Items": []}
            return result

    client = MagicMock()
    client.get_paginator.side_effect = lambda _name: _Pages()
    client.batch_write_item.return_value = {"UnprocessedItems": {}}
    checked = MagicMock(return_value=True)
    with patch.object(metadataService, "asset_storage_table", table), \
            patch.object(metadataService, "get_bucket_details", return_value={"bucketName": "b"}), \
            patch.object(metadataService, "s3", s3), \
            patch.object(metadataService, "dynamodb_client", client), \
            patch.object(metadataService, "claims_and_roles", {"tokens": ["tester"], "roles": []}), \
            patch.object(metadataService, "check_entity_authorization", return_value=True), \
            patch.object(metadataService, "validate_file_exists", checked), \
            patch.object(metadataService, "get_aggregated_schemas", return_value=[]), \
            patch.object(metadataService, "get_database_config", return_value={}):
        result = handler(event)
    assert result["statusCode"] == 200, result
    assert keys, "the route ran no record query"
    paths = {key.split(":", 2)[2] for key in keys}
    assert len(paths) == 1, paths
    if checked.called:
        # A write checks the file it keys (create and update)
        assert checked.call_args.args[2] in paths
    return paths.pop()


@pytest.mark.unit
class TestBothFormsOfOneFileKeyItsOwnRecords:
    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
    @pytest.mark.parametrize("location,value,keyed", CASES)
    def test_the_route_keys_the_records_by_the_files_listed_path(self, method, location, value, keyed):
        assert _keyed_path(method, location, value) == keyed
