# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The viewer data of a nested asset's file is not served through the containing asset's path (#1207).

A file under another asset's stored location, nested in this asset's, is that asset's file whatever its
database, and so is its viewer data. The auxiliary stream route (GET and HEAD) answers a path that resolves to
such a file's data as it answers a missing file (404), for a nested asset of the same database (which reads its
data at the same auxiliary key) as much as for one of another database. The containing asset's own data is
served as before, and before the location index is ready (or for a row without the index attributes) nothing
is nested, as before the index.
"""

import sys
from unittest.mock import MagicMock

import pytest

from tests.common.fileAttributionFakes import AttributionAssetTable, asset_row  # noqa: E402
from tests.handlers.assets.test_streamAuxiliaryPreviewAsset_range_header_casing import (  # noqa: E402
    _load, _rest_event, _wire,
)

al = sys.modules['common.assetLocations']

BUCKET = "vams-assets"
OUTER = dict(asset_row("db-a", "outer", "reg-1", "p/outer/", BUCKET), isDistributable=True)
OTHER_DB = asset_row("db-b", "inner", "reg-1", "p/outer/inner/", BUCKET)
SAME_DB = asset_row("db-a", "kit", "reg-1", "p/outer/kit/", BUCKET)
OWN = "own.e57/preview/r/octree.bin"
NESTED = ["inner/i1.e57/preview/r/octree.bin", "kit/k.e57/preview/r/octree.bin"]
# A GET is answered with a redirect to the object (this deployment's delivery mode), a HEAD with 200
SERVED = (200, 307)


@pytest.fixture(autouse=True)
def _ready(monkeypatch):
    al.clear_nested_locations_cache()
    monkeypatch.setattr(al, "index_ready", lambda name: True)
    yield
    al.clear_nested_locations_cache()


@pytest.fixture
def handler(monkeypatch):
    m = _load()
    mock_s3 = _wire(m)
    mock_s3.head_object.return_value = {"ContentType": "application/octet-stream", "ContentLength": 2048}
    monkeypatch.setattr(m, "get_asset_details", MagicMock(side_effect=lambda db, asset: dict(OUTER)))
    monkeypatch.setattr(m, "asset_table", AttributionAssetTable([OUTER, OTHER_DB, SAME_DB]))
    return m, mock_s3


def _call(m, path, method="GET"):
    event = _rest_event({"Range": "bytes=0-99"} if method == "GET" else {})
    event["httpMethod"] = method
    event["requestContext"]["httpMethod"] = method
    event["pathParameters"] = {"databaseId": "db-a", "assetId": "outer", "proxy": path}
    return m.lambda_handler(event, None)


@pytest.mark.unit
class TestViewerDataThroughTheContainingAsset:
    @pytest.mark.parametrize("path", NESTED, ids=["another-database", "same-database"])
    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_a_nested_assets_data_is_not_found(self, handler, path, method):
        m, mock_s3 = handler
        assert _call(m, path, method)["statusCode"] == 404
        mock_s3.get_object.assert_not_called()
        mock_s3.head_object.assert_not_called()
        mock_s3.generate_presigned_url.assert_not_called()

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_the_containing_assets_own_data_is_served(self, handler, method):
        m, mock_s3 = handler
        assert _call(m, OWN, method)["statusCode"] in SERVED
        called = mock_s3.get_object if method == "GET" else mock_s3.head_object
        assert called.call_args.kwargs["Key"] == "db-a/p/outer/" + OWN

    def test_before_the_index_is_ready_the_data_is_served_as_before(self, handler, monkeypatch):
        m, mock_s3 = handler
        monkeypatch.setattr(al, "index_ready", lambda name: False)
        assert _call(m, NESTED[0])["statusCode"] in SERVED
        assert mock_s3.get_object.call_args.kwargs["Key"] == "db-a/p/outer/" + NESTED[0]

    def test_a_row_without_the_index_attributes_is_served_as_before(self, handler, monkeypatch):
        m, mock_s3 = handler
        legacy = {k: v for k, v in OUTER.items() if k != al.LOCATION_BUCKET_ATTRIBUTE}
        monkeypatch.setattr(m, "get_asset_details", MagicMock(return_value=legacy))
        assert _call(m, NESTED[1])["statusCode"] in SERVED
        assert m.asset_table.reads == []
