# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The auxiliary preview stream reads a file's viewer data by its full S3 key as well as its asset path (#1855).

The web viewers name a file by the ``key`` the file list returns, its full S3 key. The file stream route
already reads a path that starts with the asset's location key as that key; the auxiliary preview stream
joined it under the location a second time, so every asset whose location is not ``{assetId}/`` (a bucket
registered under a base prefix, an asset created on an existing folder, a former single-file asset converted
to its folder) found no viewer data. Both routes now read the same path forms:

-   the file's full key, for folder and single-file locations, including a single-file key with a ``preview``
    folder segment and a file in a subfolder named like the location;
-   the asset-relative path and the auxiliary key ``{databaseId}/{location}...``, as before.

Every resolved key stays under ``{databaseId}/`` plus a key the asset's location holds: another asset's key, a
neighbour that only shares the location's characters and another database's auxiliary key resolve inside this
asset, never to that data. The route still authorizes before any S3 read, still answers a nested asset's data
as missing, and still refuses traversal forms with a message that echoes no input.
"""

import sys
from unittest.mock import MagicMock

import pytest

from tests.common.fileAttributionFakes import AttributionAssetTable, asset_row  # noqa: E402
from tests.handlers.assets.test_streamAuxiliaryPreviewAsset_range_header_casing import (  # noqa: E402
    _load, _rest_event, _wire,
)

al = sys.modules['common.assetLocations']

DATA = "preview/PotreeViewer/metadata.json"


def _resolve(location, path, database_id="db1"):
    return _load().resolve_aux_preview_key(database_id, location, path)


@pytest.mark.unit
class TestFullKeyOfAFolderAssetsFile:
    @pytest.mark.parametrize("location,full_key", [
        ("a1/", "a1/cloud.laz"),
        ("a1/", "a1/scans/cloud.laz"),
        # A bucket registered under a base prefix
        ("vams/a1/", "vams/a1/scans/cloud.laz"),
        # An asset created on an existing folder
        ("custom/path/", "custom/path/cloud.laz"),
        # A former single-file asset converted to its parent folder: its viewer data stays where it was
        ("projects/", "projects/model.laz"),
        # A folder location with a folder named preview
        ("scans/preview/", "scans/preview/cloud.laz"),
    ], ids=["asset-id-folder", "asset-id-subfolder", "base-prefix", "custom-root", "converted", "preview-folder"])
    def test_the_full_key_reads_the_files_viewer_data(self, location, full_key):
        assert _resolve(location, f"{full_key}/{DATA}") == f"db1/{full_key}/{DATA}"

    @pytest.mark.parametrize("location,relative,full_key", [
        ("a1/", "cloud.laz", "a1/cloud.laz"),
        ("vams/a1/", "/scans/cloud.laz", "vams/a1/scans/cloud.laz"),
        ("custom/path/", "cloud.laz", "custom/path/cloud.laz"),
    ])
    def test_control_the_asset_relative_path_reads_the_same_data(self, location, relative, full_key):
        assert _resolve(location, f"{relative}/{DATA}") == f"db1/{full_key}/{DATA}"

    def test_a_file_in_a_subfolder_named_like_the_asset_is_read_by_its_full_key(self):
        """The viewers send the full key, which names this file; stripping one asset id segment did not."""
        assert _resolve("a1/", f"a1/a1/cloud.laz/{DATA}") == f"db1/a1/a1/cloud.laz/{DATA}"

    def test_control_the_auxiliary_key_is_used_as_given(self):
        assert _resolve("vams/a1/", f"db1/vams/a1/cloud.laz/{DATA}") == f"db1/vams/a1/cloud.laz/{DATA}"


@pytest.mark.unit
class TestFullKeyOfASingleFileAssetsFile:
    @pytest.mark.parametrize("location,path,expected", [
        # The object itself, a companion entry and an own preview, each by its full key
        ("p1/model.laz", f"p1/model.laz/{DATA}", f"db1/p1/model.laz/{DATA}"),
        ("p1/model.laz", f"p1/model.laz/sub/x.laz/{DATA}", f"db1/p1/model.laz/sub/x.laz/{DATA}"),
        ("p1/model.laz", f"p1/model.laz.previewFile.png/{DATA}", f"db1/p1/model.laz.previewFile.png/{DATA}"),
        # A location key with a preview folder segment: the file part is at least the location key
        ("scans/preview/site.laz", f"scans/preview/site.laz/{DATA}", f"db1/scans/preview/site.laz/{DATA}"),
        ("scans/preview/site.laz", f"scans/preview/site.laz/a/x.laz/{DATA}",
         f"db1/scans/preview/site.laz/a/x.laz/{DATA}"),
    ], ids=["object", "companion", "own-preview", "preview-segment-object", "preview-segment-companion"])
    def test_the_full_key_reads_the_files_viewer_data(self, location, path, expected):
        assert _resolve(location, path) == expected

    @pytest.mark.parametrize("path,expected", [
        (f"/model.laz/{DATA}", f"db1/p1/model.laz/{DATA}"),
        (f"sub/x.laz/{DATA}", f"db1/p1/model.laz/sub/x.laz/{DATA}"),
        (f"db1/p1/model.laz/{DATA}", f"db1/p1/model.laz/{DATA}"),
    ], ids=["object-path", "companion-path", "auxiliary-key"])
    def test_control_the_asset_relative_path_and_the_auxiliary_key_read_as_before(self, path, expected):
        assert _resolve("p1/model.laz", path) == expected

    def test_control_the_relative_path_of_an_object_whose_key_has_a_preview_segment(self):
        assert _resolve("scans/preview/site.laz", f"/site.laz/{DATA}") == f"db1/scans/preview/site.laz/{DATA}"


@pytest.mark.unit
class TestEveryResolvedKeyStaysInTheAsset:
    @pytest.mark.parametrize("location,path,inside", [
        # Another asset's full key is read as a path inside this asset, never as that asset's data
        ("vams/a1/", f"vams/a2/cloud.laz/{DATA}", "db1/vams/a1/"),
        ("a1/", f"a2/cloud.laz/{DATA}", "db1/a1/"),
        ("p1/model.laz", f"p1/other.laz/{DATA}", "db1/p1/model.laz/"),
        # A neighbour that only shares the location's characters
        ("a1/", f"a10/cloud.laz/{DATA}", "db1/a1/"),
        ("p1/model.laz", f"p1/model.laz.bak/{DATA}", "db1/p1/model.laz/"),
        ("p1/model.laz", f"p1/model.lazx/{DATA}", "db1/p1/model.laz/"),
        # Another database's auxiliary key
        ("a1/", f"db2/a1/cloud.laz/{DATA}", "db1/a1/"),
        ("p1/model.laz", f"db2/p1/model.laz/{DATA}", "db1/p1/model.laz/"),
    ])
    def test_a_path_outside_the_asset_resolves_inside_it(self, location, path, inside):
        resolved = _resolve(location, path)
        assert resolved.startswith(inside)
        assert resolved != "db1/" + path.lstrip("/")

    @pytest.mark.parametrize("location,path", [
        ("p1/model.laz", "p1/model.laz/cloud.bin"),
        ("", f"a1/cloud.laz/{DATA}"),
        ("/", f"a1/cloud.laz/{DATA}"),
    ], ids=["no-viewer-data-segment", "no-location", "bucket-root"])
    def test_a_path_that_names_no_file_of_the_asset_is_refused(self, location, path):
        module = _load()
        with pytest.raises(module.VAMSGeneralErrorResponse) as refused:
            module.resolve_aux_preview_key("db1", location, path)
        # A fixed message that echoes no part of the request
        assert str(refused.value).endswith("Requested file is not part of this asset")
        assert "cloud" not in str(refused.value)


# The route end to end: a folder asset with an asset of another database and one of the same database nested
# in it (the harness of test_viewer_data_of_nested_asset.py)
BUCKET = "vams-assets"
OUTER = dict(asset_row("db-a", "outer", "reg-1", "vams/outer/", BUCKET), isDistributable=True)
OTHER_DB = asset_row("db-b", "inner", "reg-1", "vams/outer/inner/", BUCKET)
SAME_DB = asset_row("db-a", "kit", "reg-1", "vams/outer/kit/", BUCKET)
OWN_FULL_KEY = "vams/outer/own.e57/preview/r/octree.bin"
NESTED_FULL_KEYS = ["vams/outer/inner/i1.e57/preview/r/octree.bin", "vams/outer/kit/k.e57/preview/r/octree.bin"]
SERVED = (200, 307)


@pytest.fixture
def handler(monkeypatch):
    al.clear_nested_locations_cache()
    monkeypatch.setattr(al, "index_ready", lambda name: True)
    m = _load()
    mock_s3 = _wire(m)
    mock_s3.head_object.return_value = {"ContentType": "application/octet-stream", "ContentLength": 2048}
    monkeypatch.setattr(m, "get_asset_details", MagicMock(side_effect=lambda db, asset: dict(OUTER)))
    monkeypatch.setattr(m, "asset_table", AttributionAssetTable([OUTER, OTHER_DB, SAME_DB]))
    yield m, mock_s3
    al.clear_nested_locations_cache()


def _call(m, path, method="GET"):
    event = _rest_event({"Range": "bytes=0-99"} if method == "GET" else {})
    event["httpMethod"] = method
    event["requestContext"]["httpMethod"] = method
    event["pathParameters"] = {"databaseId": "db-a", "assetId": "outer", "proxy": path}
    return m.lambda_handler(event, None)


def _no_s3_read(mock_s3):
    mock_s3.get_object.assert_not_called()
    mock_s3.head_object.assert_not_called()
    mock_s3.generate_presigned_url.assert_not_called()


@pytest.mark.unit
class TestTheRouteByFullKey:
    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_the_assets_own_data_is_served_by_its_full_key(self, handler, method):
        m, mock_s3 = handler
        assert _call(m, OWN_FULL_KEY, method)["statusCode"] in SERVED
        called = mock_s3.get_object if method == "GET" else mock_s3.head_object
        assert called.call_args.kwargs["Key"] == "db-a/" + OWN_FULL_KEY

    @pytest.mark.parametrize("path", NESTED_FULL_KEYS, ids=["another-database", "same-database"])
    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_a_nested_assets_data_is_not_found_by_its_full_key(self, handler, path, method):
        m, mock_s3 = handler
        assert _call(m, path, method)["statusCode"] == 404
        _no_s3_read(mock_s3)

    @pytest.mark.parametrize("path", [
        "../vams/other/x.e57/preview/r/octree.bin",
        "vams/outer/../other/x.e57/preview/r/octree.bin",
        "vams/outer/own.e57/preview/../../other/octree.bin",
        "vams/outer//own.e57/preview/r/octree.bin",
    ])
    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_a_traversal_form_is_refused_before_any_read(self, handler, path, method):
        m, mock_s3 = handler
        response = _call(m, path, method)
        assert response["statusCode"] == 400
        assert "vams/" not in response.get("body", "")
        _no_s3_read(mock_s3)

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_a_caller_without_access_is_refused_before_any_read(self, handler, method):
        m, mock_s3 = handler
        m.CasbinEnforcer.return_value.enforce.return_value = False
        response = _call(m, OWN_FULL_KEY, method)
        assert response["statusCode"] == 403
        _no_s3_read(mock_s3)
