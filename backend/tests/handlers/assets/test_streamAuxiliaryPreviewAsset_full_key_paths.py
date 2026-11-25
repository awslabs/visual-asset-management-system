# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The auxiliary preview stream reads a file's viewer data by its full S3 key as well as its asset path (#1855).

The web viewers name a file by the ``key`` the file list returns, its full S3 key. The auxiliary preview stream
joined that key under the asset's location a second time, so every asset whose location is not ``{assetId}/``
(a bucket registered under a base prefix, an asset created on an existing folder) and every companion entry of
a single-file asset found no viewer data. The route now reads these path forms:

-   the file's full key, for folder and single-file locations, including a location key with a ``preview``
    folder segment and a file in a subfolder named like the location;
-   the asset-relative path and the auxiliary key ``{databaseId}/{location}/...``, as before.

A leading ``/`` marks the file's path in the asset, the rule the download route uses:
``/{location}x`` is the file ``{location}{location}x`` of a subfolder named like the location, never the full key
``{location}x``. Search results send such paths; reading them as full keys showed another file's point cloud.

Every resolved key stays under ``{databaseId}/{location}/``, so it is ``{databaseId}/`` plus a key the asset's
location holds: another asset's key, a neighbour that only shares the location's characters and another
database's auxiliary key resolve inside this asset, never to that data. The route still authorizes before any S3
read and still refuses traversal forms with a message that echoes no input.
"""

import sys
from unittest.mock import MagicMock

import pytest

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
        # A folder location with a folder named preview
        ("scans/preview/", "scans/preview/cloud.laz"),
        # A file in a subfolder named preview
        ("a1/", "a1/preview/cloud.laz"),
    ], ids=["asset-id-folder", "asset-id-subfolder", "base-prefix", "custom-root", "preview-folder",
            "preview-subfolder"])
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

    def test_the_folder_marker_names_no_file(self):
        """``{location}/preview/...`` names no file by its full key; it keeps its asset-relative reading."""
        assert _resolve("a1/", f"a1/{DATA}") == f"db1/a1/a1/{DATA}"


@pytest.mark.unit
class TestFullKeyOfASingleFileAssetsFile:
    @pytest.mark.parametrize("location,path,expected", [
        # The object itself and a companion entry, each by its full key
        ("p1/model.laz", f"p1/model.laz/{DATA}", f"db1/p1/model.laz/{DATA}"),
        ("p1/model.laz", f"p1/model.laz/sub/x.laz/{DATA}", f"db1/p1/model.laz/sub/x.laz/{DATA}"),
        # A location key with a preview folder segment: the file part is at least the location key
        ("scans/preview/site.laz", f"scans/preview/site.laz/{DATA}", f"db1/scans/preview/site.laz/{DATA}"),
        ("scans/preview/site.laz", f"scans/preview/site.laz/a/x.laz/{DATA}",
         f"db1/scans/preview/site.laz/a/x.laz/{DATA}"),
    ], ids=["object", "companion", "preview-segment-object", "preview-segment-companion"])
    def test_the_full_key_reads_the_files_viewer_data(self, location, path, expected):
        assert _resolve(location, path) == expected

    @pytest.mark.parametrize("path,expected", [
        (f"sub/x.laz/{DATA}", f"db1/p1/model.laz/sub/x.laz/{DATA}"),
        # Without an asset-bucket read, /{file name} keeps its earlier reading: the entry {file key}/{file name}
        (f"model.laz/{DATA}", f"db1/p1/model.laz/model.laz/{DATA}"),
        (f"db1/p1/model.laz/{DATA}", f"db1/p1/model.laz/{DATA}"),
    ], ids=["companion-path", "file-name-path", "auxiliary-key"])
    def test_control_the_asset_relative_path_and_the_auxiliary_key_read_as_before(self, path, expected):
        assert _resolve("p1/model.laz", path) == expected

    def test_an_own_preview_is_not_read_by_its_full_key(self):
        """``{file key}.previewFile.{ext}`` lies outside ``{file key}/``: it keeps its asset-relative reading, so
        no viewer data key outside ``{databaseId}/{file key}/`` is reached."""
        path = f"p1/model.laz.previewFile.png/{DATA}"
        assert _resolve("p1/model.laz", path) == f"db1/p1/model.laz/{path}"


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
        ("a1/", f"a1/../a2/cloud.laz/{DATA}"),
        ("a1/", f"../a2/cloud.laz/{DATA}"),
        ("p1/model.laz", f"p1/model.laz/../other.laz/{DATA}"),
        ("", f"a1/cloud.laz/{DATA}"),
        ("/", f"a1/cloud.laz/{DATA}"),
    ], ids=["traversal-after-location", "traversal", "single-file-traversal", "no-location", "bucket-root"])
    def test_a_path_that_names_no_file_of_the_asset_is_refused(self, location, path):
        assert _resolve(location, path) is None


@pytest.mark.unit
class TestALeadingSlashMarksThePathInTheAsset:
    """A path with a leading ``/`` is read only as the file's path in the asset."""

    @pytest.mark.parametrize("location,path,key", [
        # A subfolder named like the location: /{location}x is {location}{location}x, not {location}x
        ("a1/", "/a1/cloud.laz", "a1/a1/cloud.laz"),
        ("vams/a1/", "/vams/a1/cloud.laz", "vams/a1/vams/a1/cloud.laz"),
        ("custom/path/", "/custom/path/cloud.laz", "custom/path/custom/path/cloud.laz"),
        # A single-file asset's companion entry whose path spells the file's key
        ("p1/model.laz", "/p1/model.laz/x.laz", "p1/model.laz/p1/model.laz/x.laz"),
        ("p1/model.laz", "/p1/model.laz", "p1/model.laz/p1/model.laz"),
    ], ids=["asset-id-folder", "base-prefix", "custom-root", "single-file-companion", "single-file-key-path"])
    def test_a_path_that_starts_with_the_location_key_is_a_path_in_the_asset(self, location, path, key):
        assert _resolve(location, f"{path}/{DATA}") == f"db1/{key}/{DATA}"

    @pytest.mark.parametrize("location,full_key", [
        ("a1/", "a1/cloud.laz"),
        ("vams/a1/", "vams/a1/cloud.laz"),
        ("custom/path/", "custom/path/cloud.laz"),
        ("p1/model.laz", "p1/model.laz/x.laz"),
        ("p1/model.laz", "p1/model.laz"),
    ])
    def test_control_the_same_text_without_the_slash_is_the_full_key(self, location, full_key):
        assert _resolve(location, f"{full_key}/{DATA}") == f"db1/{full_key}/{DATA}"

    @pytest.mark.parametrize("location,path,key", [
        ("a1/", "/db1/a1/cloud.laz", "a1/db1/a1/cloud.laz"),
        ("p1/model.laz", "/db1/p1/model.laz", "p1/model.laz/db1/p1/model.laz"),
    ])
    def test_a_path_in_the_asset_is_never_the_auxiliary_key(self, location, path, key):
        assert _resolve(location, f"{path}/{DATA}") == f"db1/{key}/{DATA}"

    @pytest.mark.parametrize("location,relative,full_key", [
        ("a1/", "/cloud.laz", "a1/cloud.laz"),
        ("vams/a1/", "/scans/cloud.laz", "vams/a1/scans/cloud.laz"),
        ("p1/model.laz", "/sub/x.laz", "p1/model.laz/sub/x.laz"),
    ])
    def test_control_an_ordinary_path_in_the_asset_reads_as_before(self, location, relative, full_key):
        assert _resolve(location, f"{relative}/{DATA}") == f"db1/{full_key}/{DATA}"


# The route end to end: a folder asset in a bucket registered under a base prefix
OUTER = {
    "databaseId": "db-a", "assetId": "outer", "isDistributable": True,
    "bucketId": "bucket-1", "assetLocation": {"Key": "vams/outer/"},
}
OWN_FULL_KEY = "vams/outer/own.e57/preview/r/octree.bin"
SERVED = (200, 307)


@pytest.fixture
def handler():
    m = _load()
    mock_s3 = _wire(m)
    mock_s3.head_object.return_value = {"ContentType": "application/octet-stream", "ContentLength": 2048}
    m.get_asset_details = MagicMock(side_effect=lambda db, asset: dict(OUTER))
    return m, mock_s3


def _call(m, path, method="GET"):
    event = _rest_event({"Range": "bytes=0-99"} if method == "GET" else {})
    event["httpMethod"] = method
    event["requestContext"]["httpMethod"] = method
    event["pathParameters"] = {"databaseId": "db-a", "assetId": "outer", "proxy": path}
    return m.lambda_handler(event, None)


def _read_key(mock_s3, method):
    called = mock_s3.get_object if method == "GET" else mock_s3.head_object
    return called.call_args.kwargs["Key"]


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
        assert _read_key(mock_s3, method) == "db-a/" + OWN_FULL_KEY

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_a_subfolder_named_like_the_location_is_served_by_its_path_in_the_asset(self, handler, method):
        """Search results send /{location}x for the file {location}{location}x."""
        m, mock_s3 = handler
        assert _call(m, "/" + OWN_FULL_KEY, method)["statusCode"] in SERVED
        assert _read_key(mock_s3, method) == "db-a/vams/outer/" + OWN_FULL_KEY

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_control_the_asset_relative_path_is_served_the_same_data(self, handler, method):
        m, mock_s3 = handler
        assert _call(m, "own.e57/preview/r/octree.bin", method)["statusCode"] in SERVED
        assert _read_key(mock_s3, method) == "db-a/" + OWN_FULL_KEY

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
