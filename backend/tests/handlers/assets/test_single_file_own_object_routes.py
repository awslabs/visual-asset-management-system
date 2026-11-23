# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #1846: the stream, auxiliary stream, download, and upload routes reach a
single-file asset's own object at ``/{file name}`` and by its own key, and nothing outside the asset.

The asset's location is the object ``p1/model.glb``. Without a companion entry ``p1/model.glb/model.glb`` the
object is listed at ``/model.glb``; with one, that entry keeps the path and every route behaves as before. The
companion check is one ``ListObjectVersions`` call (``MaxKeys=1``) on the asset bucket. Folder assets make no such
call and are unchanged.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level import so the real backend.backend.handlers.assets package is in sys.modules first
from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from backend.tests.handlers.assets import test_streamAsset_range_header_casing as asset_stream  # noqa: E402
from backend.tests.handlers.assets import (  # noqa: E402
    test_streamAuxiliaryPreviewAsset_range_header_casing as aux_stream,
)
from backend.tests.handlers.assets.test_downloadAsset_key_containment import _wire as _download_wire  # noqa: E402
from backend.tests.handlers.assets.test_downloadAsset_bulk import _load as _load_download  # noqa: E402

LOCATION = "p1/model.glb"
COPY = "p1/model.glb/model.glb"


def _versions_listing(companion):
    """The ListObjectVersions answer for the companion check: the entry, or a longer sibling only."""
    if companion:
        return {"Versions": [{"Key": COPY, "VersionId": "v1", "IsLatest": True}]}
    return {"Versions": [{"Key": COPY + ".previewFile.png", "VersionId": "v9", "IsLatest": True}]}


def _asset(location=LOCATION):
    return {"databaseId": "db1", "assetId": "asset1", "isDistributable": True,
            "bucketId": "bucket-1", "assetLocation": {"Key": location}}


# --------------------------------------------------------------------------- stream

def _stream_event(proxy, method):
    event = asset_stream._rest_event({"Accept": "*/*"}, method=method)
    event["pathParameters"]["proxy"] = proxy
    event["path"] = f"/database/db1/assets/asset1/download/stream/{proxy}"
    event["requestContext"]["path"] = event["path"]
    return event


def _stream(proxy, method, companion=False, location=LOCATION):
    m = asset_stream._load()
    mock_s3 = asset_stream._wire(m)
    m.get_asset_details = MagicMock(return_value=_asset(location))
    mock_s3.head_object.return_value = {"ContentType": "model/gltf-binary", "ContentLength": 1024}
    mock_s3.list_object_versions.return_value = _versions_listing(companion)
    response = m.lambda_handler(_stream_event(proxy, method), MagicMock())
    return response, mock_s3


@pytest.mark.unit
class TestStream:
    @pytest.mark.parametrize("method,call", [("GET", "get_object"), ("HEAD", "head_object")])
    @pytest.mark.parametrize("proxy", ["model.glb", "p1/model.glb"])
    def test_the_object_streams_by_its_path_and_its_own_key(self, method, call, proxy):
        response, mock_s3 = _stream(proxy, method)
        assert response["statusCode"] in (200, 307)
        assert getattr(mock_s3, call).call_args.kwargs["Key"] == LOCATION
        assert mock_s3.list_object_versions.call_args.kwargs == {
            "Bucket": "test-bucket", "Prefix": COPY, "MaxKeys": 1}

    @pytest.mark.parametrize("proxy,key", [("model.glb", COPY), ("p1/model.glb", "p1/model.glb/p1/model.glb")])
    def test_with_a_companion_entry_every_path_keeps_its_earlier_key(self, proxy, key):
        response, mock_s3 = _stream(proxy, "GET", companion=True)
        assert mock_s3.get_object.call_args.kwargs["Key"] == key

    @pytest.mark.parametrize("proxy", ["p1/model.glb.bak", "p1/model.glbx/a.txt", "proj/other/secret.txt"])
    def test_a_neighbour_or_another_assets_key_resolves_inside_this_asset(self, proxy):
        response, mock_s3 = _stream(proxy, "GET")
        assert mock_s3.get_object.call_args.kwargs["Key"] == "p1/model.glb/" + proxy

    def test_a_traversal_is_refused_before_any_s3_read(self):
        response, mock_s3 = _stream("sub/../../other.glb", "GET")
        assert response["statusCode"] == 400
        assert not mock_s3.get_object.called and not mock_s3.head_object.called

    def test_control_a_folder_asset_streams_as_before_without_a_companion_check(self):
        response, mock_s3 = _stream("x.glb", "GET", location="proj/a/")
        assert mock_s3.get_object.call_args.kwargs["Key"] == "proj/a/x.glb"
        assert not mock_s3.list_object_versions.called


# ------------------------------------------------------------------ auxiliary stream

def _aux(proxy, location=LOCATION):
    m = aux_stream._load()
    mock_s3 = aux_stream._wire(m)
    m.get_asset_details = MagicMock(return_value=_asset(location))
    mock_s3.head_object.return_value = {"ContentType": "application/json", "ContentLength": 20}
    event = aux_stream._rest_event({"Accept": "*/*"})
    event["httpMethod"] = "HEAD"
    event["requestContext"]["httpMethod"] = "HEAD"
    event["pathParameters"]["proxy"] = proxy
    event["path"] = f"/database/db1/assets/asset1/auxiliaryPreviewAssets/stream/{proxy}"
    event["requestContext"]["path"] = event["path"]
    response = m.lambda_handler(event, MagicMock())
    return response, mock_s3


@pytest.mark.unit
class TestAuxiliaryStream:
    def test_the_objects_viewer_data_is_read_from_its_own_preview_folder(self):
        response, mock_s3 = _aux("p1/model.glb/preview/PotreeViewer/metadata.json")
        assert mock_s3.head_object.call_args.kwargs["Key"] == "db1/p1/model.glb/preview/PotreeViewer/metadata.json"

    def test_a_companion_entrys_path_keeps_its_earlier_key(self):
        response, mock_s3 = _aux("sub/x.e57/preview/PotreeViewer/metadata.json")
        expected = "db1/p1/model.glb/sub/x.e57/preview/PotreeViewer/metadata.json"
        assert mock_s3.head_object.call_args.kwargs["Key"] == expected

    def test_control_a_folder_asset_is_unchanged(self):
        response, mock_s3 = _aux("x.e57/preview/PotreeViewer/metadata.json", location="proj/a/")
        assert mock_s3.head_object.call_args.kwargs["Key"] == "db1/proj/a/x.e57/preview/PotreeViewer/metadata.json"


# -------------------------------------------------------------------------- download

def _download(raw_key, companion=False, location=LOCATION):
    m = _download_wire(_load_download(), location)
    m.s3.list_object_versions.return_value = _versions_listing(companion)
    url, _ = m.resolve_and_sign_file_key("db1", "asset1", "test-bucket", location, raw_key)
    return url


@pytest.mark.unit
class TestDownload:
    @pytest.mark.parametrize("raw_key", ["/model.glb", "p1/model.glb", None])
    def test_the_object_downloads_by_its_path_its_key_or_no_key(self, raw_key):
        assert _download(raw_key) == "https://signed.example/" + LOCATION

    def test_with_a_companion_entry_the_path_names_the_companion(self):
        assert _download("/model.glb", companion=True) == "https://signed.example/" + COPY
        assert _download("p1/model.glb", companion=True) == "https://signed.example/" + LOCATION

    @pytest.mark.parametrize("raw_key", ["p1/model.glb.bak", "p1/model.glbx/a.txt"])
    def test_a_neighbour_is_refused(self, raw_key):
        from models.common import VAMSGeneralErrorResponse
        with pytest.raises(VAMSGeneralErrorResponse):
            _download(raw_key)

    def test_a_traversal_is_refused(self):
        from models.common import VAMSGeneralErrorResponse
        with pytest.raises(VAMSGeneralErrorResponse):
            _download("/sub/../../other.glb")

    def test_control_a_folder_asset_is_unchanged(self):
        assert _download("/x.txt", location="proj/a/") == "https://signed.example/proj/a/x.txt"


# ---------------------------------------------------------------------------- upload

@pytest.mark.unit
class TestUploadTargets:
    def _key(self, relative_key, companion=False, location=LOCATION):
        from backend.backend.handlers.assets import uploadFile as uf
        s3 = MagicMock()
        s3.list_object_versions.return_value = _versions_listing(companion)
        with patch.object(uf, "s3", s3):
            paths = uf.upload_paths_for("b", {"assetLocation": {"Key": location}}, "", "asset1")
            return uf.upload_key_for(paths, relative_key), paths

    def test_an_upload_at_the_file_name_writes_a_new_version_of_the_object(self):
        key, paths = self._key("/model.glb")
        assert key == LOCATION and paths.names_object(key)

    def test_an_upload_at_any_other_path_goes_into_the_companion_folder(self):
        assert self._key("/new.glb")[0] == "p1/model.glb/new.glb"
        assert self._key("/sub/new.glb")[0] == "p1/model.glb/sub/new.glb"

    def test_with_a_companion_entry_the_file_name_writes_the_companion(self):
        assert self._key("/model.glb", companion=True)[0] == COPY

    def test_a_traversal_is_refused(self):
        from models.common import VAMSGeneralErrorResponse
        with pytest.raises(VAMSGeneralErrorResponse):
            self._key("/sub/../../other.glb")

    def test_control_a_folder_asset_is_unchanged(self):
        assert self._key("/x.txt", location="proj/a/")[0] == "proj/a/x.txt"

    def test_a_workflow_output_at_the_objects_path_is_refused_and_nothing_is_written(self):
        from backend.tests.handlers.assets.test_uploadFile_preview_extension_external import (
            _complete_external_upload_env, _external_request, UPLOAD_ID)
        with _complete_external_upload_env("assetFile") as (uf, mocks):
            mocks["s3"].list_object_versions.return_value = _versions_listing(False)
            with patch.object(uf, "get_asset_details", return_value=_asset()):
                response = uf.complete_external_upload(
                    UPLOAD_ID, _external_request("assetFile", "/model.glb"), {})
        result = [r for r in response.fileResults if r.relativeKey == "/model.glb"][0]
        assert result.success is False and result.error == uf.OWN_FILE_OUTPUT_REFUSAL
        mocks["copy"].assert_not_called()
