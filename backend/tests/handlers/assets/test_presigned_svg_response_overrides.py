# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #1201: presigned downloads of an SVG file carry image/svg+xml and an attachment disposition.

An SVG upload is stored as application/octet-stream (the browser and CLI uploads send no SVG type),
and a presigned GET returns the stored type, so the Image Viewer's <img> showed nothing: Chromium
renders an SVG image only when it arrives as image/svg+xml. Every presigned GetObject URL that serves
an asset or preview file now signs ``ResponseContentType=image/svg+xml`` and
``ResponseContentDisposition=attachment`` for a key ending in ``.svg`` (any case). The attachment
disposition keeps a directly opened link a download, so no script inside the SVG runs at the
bucket's origin. Every other key is signed exactly as before.

The shipped ``common/s3.py`` helper is loaded by file path (the root conftest swaps in the mock), and
each handler path is driven through its real module with the S3 client mocked, asserting on the
``Params`` handed to ``generate_presigned_url``.
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-asset-auxiliary-bucket")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

# The real handler loaders from the suites that already drive each path. They are imported at
# module level: the assetExportService loader pulls in the assetFiles suite, whose module-level
# import must run before the root conftest's autouse fixture.
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
)
from tests.handlers.assets.test_downloadAsset_bulk import _load as _load_download  # noqa: E402
from tests.handlers.assets.test_downloadAsset_key_containment import _wire as _wire_download_module  # noqa: E402
from tests.handlers.assets.test_stream_expose_headers import (  # noqa: E402
    _load as _load_stream,
    _wire as _wire_stream,
)
from tests.handlers.assets.test_streamAuxiliaryPreviewAsset_range_header_casing import (  # noqa: E402
    _load as _load_aux_stream,
    _wire as _wire_aux_stream,
)

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

SVG_OVERRIDES = {
    "ResponseContentType": "image/svg+xml",
    "ResponseContentDisposition": "attachment",
}
OVERRIDE_KEYS = set(SVG_OVERRIDES)


def _shipped_s3():
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    spec = importlib.util.spec_from_file_location(
        "_shipped_common_s3_svg", os.path.join(BACKEND, "common", "s3.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _signed_params(mock_s3):
    assert mock_s3.generate_presigned_url.called, "no presigned URL was generated"
    call = mock_s3.generate_presigned_url.call_args
    assert call.args[0] == "get_object"
    return call.kwargs["Params"]


@pytest.mark.unit
class TestSharedHelper:
    """common.s3.presigned_get_object_params, the one place the rule lives."""

    @pytest.mark.parametrize("key", ["asset1/drawing.svg", "asset1/DRAWING.SVG", "a/b/Logo.Svg"])
    def test_svg_keys_get_the_overrides(self, key):
        s3 = _shipped_s3()
        params = {"Bucket": "b", "Key": key, "VersionId": "v1"}
        signed = s3.presigned_get_object_params(params)
        assert signed == {**params, **SVG_OVERRIDES}
        # The caller's dict is not mutated.
        assert "ResponseContentType" not in params

    @pytest.mark.parametrize("key", ["asset1/photo.png", "asset1/drawing.svg.png", "asset1/svg",
                                     "asset1/drawing.svgz", "asset1/model.glb", ""])
    def test_other_keys_are_unchanged(self, key):
        s3 = _shipped_s3()
        params = {"Bucket": "b", "Key": key}
        assert s3.presigned_get_object_params(params) == params

    def test_the_test_mock_applies_the_same_rule(self):
        mock = sys.modules["common.s3"]
        shipped = _shipped_s3()
        for key in ("x/a.svg", "x/A.SVG", "x/a.png", "x/a.svgz"):
            params = {"Bucket": "b", "Key": key}
            assert mock.presigned_get_object_params(params) == shipped.presigned_get_object_params(params)


def _wire_download(base_key="asset1/"):
    return _wire_download_module(_load_download(), base_key)


def _parse_download(body):
    from aws_lambda_powertools.utilities.parser import parse
    from models.assetsV3 import DownloadAssetRequestModel
    return parse(body, model=DownloadAssetRequestModel)


@pytest.mark.unit
class TestDownloadAssetFile:
    """POST /download assetFile -- the Image Viewer's path (resolve_and_sign_file_key)."""

    @pytest.mark.parametrize("key", ["/drawing.svg", "/DRAWING.SVG"])
    def test_svg_file_url_carries_the_overrides(self, key):
        m = _wire_download()
        m.download_asset_file("db1", "asset1", _parse_download({"downloadType": "assetFile", "key": key}))
        params = _signed_params(m.s3)
        assert {k: params[k] for k in OVERRIDE_KEYS} == SVG_OVERRIDES
        assert params["Bucket"] == "test-bucket"

    def test_non_svg_file_url_is_unchanged(self):
        m = _wire_download()
        m.download_asset_file("db1", "asset1", _parse_download({"downloadType": "assetFile", "key": "/photo.png"}))
        params = _signed_params(m.s3)
        assert not OVERRIDE_KEYS & set(params)

    def test_bulk_signs_each_key_by_its_own_extension(self):
        m = _wire_download()
        m.download_asset_files_bulk(
            "db1", "asset1", _parse_download({"downloadType": "assetFile", "keys": ["/a.svg", "/b.png"]})
        )
        by_key = {c.kwargs["Params"]["Key"]: c.kwargs["Params"] for c in m.s3.generate_presigned_url.call_args_list}
        svg = next(p for k, p in by_key.items() if k.endswith("a.svg"))
        png = next(p for k, p in by_key.items() if k.endswith("b.png"))
        assert {k: svg[k] for k in OVERRIDE_KEYS} == SVG_OVERRIDES
        assert not OVERRIDE_KEYS & set(png)


@pytest.mark.unit
class TestDownloadAssetPreview:
    """POST /download assetPreview -- the asset preview (thumbnail) image."""

    def _wire(self, preview_key):
        m = _wire_download()
        m.get_asset_details = MagicMock(return_value={
            "databaseId": "db1", "assetId": "asset1", "isDistributable": True,
            "bucketId": "bucket-1", "assetLocation": {"Key": "asset1/"},
            "previewLocation": {"Key": preview_key},
        })
        return m

    def test_svg_preview_url_carries_the_overrides(self):
        m = self._wire("previews/asset1/thumb.SVG")
        m.download_asset_preview("db1", "asset1", _parse_download({"downloadType": "assetPreview"}))
        params = _signed_params(m.s3)
        assert params["Key"] == "previews/asset1/thumb.SVG"
        assert {k: params[k] for k in OVERRIDE_KEYS} == SVG_OVERRIDES

    def test_png_preview_url_is_unchanged(self):
        m = self._wire("previews/asset1/thumb.png")
        m.download_asset_preview("db1", "asset1", _parse_download({"downloadType": "assetPreview"}))
        assert not OVERRIDE_KEYS & set(_signed_params(m.s3))


def _stream_event(proxy, resource, path_segment):
    path = f"/database/db1/assets/asset1/{path_segment}/{proxy}"
    return {
        "resource": resource,
        "path": path,
        "httpMethod": "GET",
        "headers": {"Accept": "*/*"},
        "pathParameters": {"databaseId": "db1", "assetId": "asset1", "proxy": proxy},
        "queryStringParameters": None,
        "requestContext": {"identity": {"sourceIp": "10.0.0.7"}, "path": path, "httpMethod": "GET"},
        "body": None,
    }


@pytest.mark.unit
class TestStreamAssetRedirect:
    """GET .../download/stream/{proxy+} -- the 307 redirect to the presigned URL."""

    def _run(self, proxy):
        m = _load_stream()
        mock_s3 = _wire_stream(m)
        response = m.lambda_handler(
            _stream_event(proxy, "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}",
                          "download/stream"),
            MagicMock(),
        )
        assert response["statusCode"] == 307
        return _signed_params(mock_s3)

    @pytest.mark.parametrize("proxy", ["drawings/plan.svg", "drawings/PLAN.SVG"])
    def test_svg_redirect_carries_the_overrides(self, proxy):
        params = self._run(proxy)
        assert {k: params[k] for k in OVERRIDE_KEYS} == SVG_OVERRIDES
        # The Range header stays out of the signature (see the range-casing suite).
        assert "Range" not in params

    def test_non_svg_redirect_is_unchanged(self):
        assert not OVERRIDE_KEYS & set(self._run("scans/pump.glb"))


@pytest.mark.unit
class TestStreamAuxiliaryPreviewRedirect:
    """GET .../auxiliaryPreviewAssets/stream/{proxy+} -- the 307 redirect to the presigned URL."""

    def _run(self, proxy):
        m = _load_aux_stream()
        mock_s3 = _wire_aux_stream(m)
        response = m.lambda_handler(
            _stream_event(
                proxy,
                "/database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}",
                "auxiliaryPreviewAssets/stream",
            ),
            MagicMock(),
        )
        assert response["statusCode"] == 307
        return _signed_params(mock_s3)

    def test_svg_preview_redirect_carries_the_overrides(self):
        params = self._run("drawings/plan.svg/preview/thumb.svg")
        assert {k: params[k] for k in OVERRIDE_KEYS} == SVG_OVERRIDES

    def test_non_svg_preview_redirect_is_unchanged(self):
        assert not OVERRIDE_KEYS & set(self._run("scans/pump.e57/preview/r/octree.bin"))


@pytest.mark.unit
class TestAssetExportFileUrls:
    """Asset export ``generateFileDownloadUrls`` -- per-file presigned URLs."""

    def _sign(self, key):
        m = _load_asset_export_service()
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://signed.example/x"
        original = m.s3_client
        m.s3_client = mock_s3
        try:
            assert m.generate_presigned_url("test-bucket", key, "v1") == "https://signed.example/x"
        finally:
            m.s3_client = original
        return _signed_params(mock_s3)

    def test_svg_export_url_carries_the_overrides(self):
        params = self._sign("asset1/Plan.SVG")
        assert params["VersionId"] == "v1"
        assert {k: params[k] for k in OVERRIDE_KEYS} == SVG_OVERRIDES

    def test_non_svg_export_url_is_unchanged(self):
        assert not OVERRIDE_KEYS & set(self._sign("asset1/model.glb"))
