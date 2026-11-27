# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #2026: download URLs save the file under its own name.

A presigned GET answers with the headers the object was stored with, so a file written straight to
a bucket with a viewable type (image/png, application/pdf, text/plain) opened in the browser tab
when the web app's Download File followed the URL, and a stored ``Content-Disposition`` named the
saved file. The POST download URLs (single file, bulk and prefix) and the URLs an asset export
returns now sign ``ResponseContentDisposition`` as an attachment named by the key's final segment
(RFC 6266, with an RFC 8187 ``filename*`` for any name), through
``common.s3.presigned_get_object_params(..., attachment=True)``. The stream routes and the asset
preview URL keep serving inline, because the viewers render from them.

The shipped ``common/s3.py`` is loaded by file path (the root conftest swaps in the mock) and is
patched into each handler, so every handler test exercises the real helper; the mock is held to the
same output separately.
"""

import importlib.util
import os
import sys
from email.message import Message
from email.utils import collapse_rfc2231_value
from urllib.parse import parse_qs, urlsplit

import boto3
import pytest
from unittest.mock import MagicMock

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-asset-auxiliary-bucket")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

# The real handler loaders the other download suites use (see test_presigned_svg_response_overrides);
# the streamAsset loader comes from the Range-casing suite, which every supported branch carries.
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
)
from tests.handlers.assets.test_downloadAsset_bulk import _load as _load_download  # noqa: E402
from tests.handlers.assets.test_downloadAsset_key_containment import _wire as _wire_download_module  # noqa: E402
from tests.handlers.assets.test_streamAsset_range_header_casing import (  # noqa: E402
    _load as _load_stream,
    _wire as _wire_stream,
)
from tests.handlers.assets.test_streamAuxiliaryPreviewAsset_range_header_casing import (  # noqa: E402
    _load as _load_aux_stream,
    _wire as _wire_aux_stream,
)

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

# (file name, the exact Content-Disposition a download URL signs for it)
NAMED = [
    ("photo.png",
     "attachment; filename=\"photo.png\"; filename*=UTF-8''photo.png"),
    ("Grüße ✓.txt",
     "attachment; filename=\"Gr__e _.txt\"; filename*=UTF-8''Gr%C3%BC%C3%9Fe%20%E2%9C%93.txt"),
    ('report, "final".pdf',
     "attachment; filename=\"report, \\\"final\\\".pdf\"; "
     "filename*=UTF-8''report%2C%20%22final%22.pdf"),
    ("drawing.svg",
     "attachment; filename=\"drawing.svg\"; filename*=UTF-8''drawing.svg"),
    ("back\\slash.json",
     "attachment; filename=\"back\\\\slash.json\"; filename*=UTF-8''back%5Cslash.json"),
    ("semi;colon.mp4",
     "attachment; filename=\"semi;colon.mp4\"; filename*=UTF-8''semi%3Bcolon.mp4"),
]
NAMES = [name for name, _ in NAMED]
# Names the handler tests drive through the request model (a path the API accepts).
HANDLER_NAMES = ["photo.png", "Grüße ✓.txt", 'report, "final".pdf', "drawing.svg"]


def _shipped_s3():
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    spec = importlib.util.spec_from_file_location(
        "_shipped_common_s3_attachment", os.path.join(BACKEND, "common", "s3.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _filename_params(disposition):
    """(extended filename* values decoded, plain filename values unquoted), parsed by the stdlib."""
    message = Message()
    message["Content-Disposition"] = disposition
    assert message.get_content_disposition() == "attachment"
    values = [v for k, v in message.get_params(header="content-disposition")[1:] if k == "filename"]
    extended = [collapse_rfc2231_value(v) for v in values if isinstance(v, tuple)]
    plain = [v for v in values if not isinstance(v, tuple)]
    return extended, plain


def _decoded_filename(disposition):
    """The file name a client following RFC 6266 section 4.3 reads: filename* over filename."""
    extended, plain = _filename_params(disposition)
    return (extended or plain or [None])[0]


def _fallback_filename(disposition):
    """The plain filename="..." value, for a client that does not read filename*."""
    return (_filename_params(disposition)[1] or [None])[0]


def _signed_params(mock_s3):
    assert mock_s3.generate_presigned_url.called, "no presigned URL was generated"
    call = mock_s3.generate_presigned_url.call_args
    assert call.args[0] == "get_object"
    return call.kwargs["Params"]


@pytest.mark.unit
class TestAttachmentContentDisposition:
    """common.s3.attachment_content_disposition, the encoding."""

    @pytest.mark.parametrize("name,expected", NAMED, ids=[n for n, _ in NAMED])
    def test_the_header_names_the_file(self, name, expected):
        s3 = _shipped_s3()
        value = s3.attachment_content_disposition(f"db1/asset1/sub/{name}")
        assert value == expected
        assert _decoded_filename(value) == name
        # The fallback unquotes to the name with each character outside printable ASCII as "_".
        assert _fallback_filename(value) == "".join(ch if " " <= ch <= "~" else "_" for ch in name)

    @pytest.mark.parametrize("name", NAMES + ["line\r\nbreak.txt", "tab\there.bin", "\u202ertl.txt"])
    def test_the_header_is_plain_printable_ascii(self, name):
        value = _shipped_s3().attachment_content_disposition(f"a/{name}")
        assert value.isascii()
        assert all(" " <= ch <= "~" for ch in value), value
        assert _decoded_filename(value) == name

    @pytest.mark.parametrize("key", ["asset1/folder/", "", None])
    def test_a_key_naming_no_file_gets_a_bare_attachment(self, key):
        assert _shipped_s3().attachment_content_disposition(key) == "attachment"

    def test_a_key_with_no_folder_is_named_whole(self):
        assert _decoded_filename(_shipped_s3().attachment_content_disposition("top.glb")) == "top.glb"

    def test_a_255_byte_name_fits_fully_escaped(self):
        s3 = _shipped_s3()
        name = "€" * 85  # 85 three-byte characters: a 255-byte name, every byte escaped
        value = s3.attachment_content_disposition(f"a/{name}")
        assert len(value) <= s3.ATTACHMENT_DISPOSITION_MAX_LENGTH
        assert _decoded_filename(value) == name

    def test_a_name_over_the_bound_gets_a_bare_attachment(self):
        s3 = _shipped_s3()
        assert s3.attachment_content_disposition("a/" + "€" * 200) == "attachment"


@pytest.mark.unit
class TestPresignedParams:
    """presigned_get_object_params with and without attachment."""

    @pytest.mark.parametrize("name,expected", NAMED, ids=[n for n, _ in NAMED])
    def test_a_download_url_carries_the_named_attachment(self, name, expected):
        s3 = _shipped_s3()
        params = {"Bucket": "b", "Key": f"asset1/{name}", "VersionId": "v1"}
        signed = s3.presigned_get_object_params(params, attachment=True)
        assert signed["ResponseContentDisposition"] == expected
        assert {k: signed[k] for k in params} == params
        # The caller's dict is not mutated.
        assert "ResponseContentDisposition" not in params

    def test_a_download_url_of_an_svg_keeps_the_svg_type(self):
        signed = _shipped_s3().presigned_get_object_params({"Bucket": "b", "Key": "a/Plan.SVG"}, attachment=True)
        assert signed["ResponseContentType"] == "image/svg+xml"
        assert _decoded_filename(signed["ResponseContentDisposition"]) == "Plan.SVG"

    @pytest.mark.parametrize("name", ["photo.png", "doc.pdf", "clip.mp4", "notes.txt", "data.json"])
    def test_a_url_without_attachment_is_unchanged(self, name):
        params = {"Bucket": "b", "Key": f"asset1/{name}"}
        assert _shipped_s3().presigned_get_object_params(params) == params

    def test_botocore_signs_the_disposition_into_the_url(self):
        s3 = _shipped_s3()
        params = s3.presigned_get_object_params({"Bucket": "b", "Key": 'asset1/Grüße "x", y.txt'},
                                                attachment=True)
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="AKIDEXAMPLE",
                              aws_secret_access_key="example-secret")
        url = client.generate_presigned_url("get_object", Params=params, ExpiresIn=60)
        query = parse_qs(urlsplit(url).query)
        assert query["response-content-disposition"] == [params["ResponseContentDisposition"]]
        assert _decoded_filename(query["response-content-disposition"][0]) == 'Grüße "x", y.txt'

    def test_the_test_mock_applies_the_same_rule(self):
        mock = sys.modules["common.s3"]
        shipped = _shipped_s3()
        assert mock.ATTACHMENT_DISPOSITION_MAX_LENGTH == shipped.ATTACHMENT_DISPOSITION_MAX_LENGTH
        keys = [f"x/{name}" for name in NAMES] + ["x/a.svg", "x/folder/", "", "x/" + "€" * 200,
                                                  "x/line\nbreak.txt"]
        for key in keys:
            assert mock.attachment_content_disposition(key) == shipped.attachment_content_disposition(key)
            for attachment in (False, True):
                params = {"Bucket": "b", "Key": key}
                assert (mock.presigned_get_object_params(params, attachment=attachment)
                        == shipped.presigned_get_object_params(params, attachment=attachment))


def _parse_download(body):
    from aws_lambda_powertools.utilities.parser import parse
    from models.assetsV3 import DownloadAssetRequestModel
    return parse(body, model=DownloadAssetRequestModel)


@pytest.fixture
def download(monkeypatch):
    m = _wire_download_module(_load_download(), "asset1/")
    monkeypatch.setattr(m, "presigned_get_object_params", _shipped_s3().presigned_get_object_params)
    return m


@pytest.mark.unit
class TestDownloadAssetUrls:
    """POST /download assetFile: the web app's Download File, the CLI and the connectors."""

    @pytest.mark.parametrize("name", HANDLER_NAMES)
    def test_single_file_url_is_a_named_attachment(self, download, name):
        download.download_asset_file("db1", "asset1",
                                     _parse_download({"downloadType": "assetFile", "key": f"/{name}"}))
        params = _signed_params(download.s3)
        assert params["Key"] == f"asset1/{name}"
        assert _decoded_filename(params["ResponseContentDisposition"]) == name

    def test_a_version_pinned_url_is_a_named_attachment(self, download):
        download.download_asset_file("db1", "asset1", _parse_download(
            {"downloadType": "assetFile", "key": "/photo.png", "versionId": "v7"}))
        params = _signed_params(download.s3)
        assert params["VersionId"] == "v7"
        assert _decoded_filename(params["ResponseContentDisposition"]) == "photo.png"

    def test_bulk_urls_are_each_named_by_their_own_key(self, download):
        download.download_asset_files_bulk("db1", "asset1", _parse_download(
            {"downloadType": "assetFile", "keys": [f"/sub/{name}" for name in HANDLER_NAMES]}))
        by_key = {c.kwargs["Params"]["Key"]: c.kwargs["Params"]
                  for c in download.s3.generate_presigned_url.call_args_list}
        assert set(by_key) == {f"asset1/sub/{name}" for name in HANDLER_NAMES}
        for name in HANDLER_NAMES:
            assert _decoded_filename(by_key[f"asset1/sub/{name}"]["ResponseContentDisposition"]) == name
        assert by_key["asset1/sub/drawing.svg"]["ResponseContentType"] == "image/svg+xml"
        assert "ResponseContentType" not in by_key["asset1/sub/photo.png"]

    def test_a_folder_prefix_url_is_a_bare_attachment(self, download):
        download.download_asset_file("db1", "asset1",
                                     _parse_download({"downloadType": "assetFile", "key": "/okdir/"}))
        assert _signed_params(download.s3)["ResponseContentDisposition"] == "attachment"

    @pytest.mark.parametrize("preview_key", ["previews/asset1/thumb.png", "previews/asset1/thumb.jpg"])
    def test_the_asset_preview_url_stays_inline(self, download, preview_key):
        download.get_asset_details = MagicMock(return_value={
            "databaseId": "db1", "assetId": "asset1", "isDistributable": True,
            "bucketId": "bucket-1", "assetLocation": {"Key": "asset1/"},
            "previewLocation": {"Key": preview_key},
        })
        download.download_asset_preview("db1", "asset1", _parse_download({"downloadType": "assetPreview"}))
        params = _signed_params(download.s3)
        assert params["Key"] == preview_key
        assert "ResponseContentDisposition" not in params


@pytest.mark.unit
class TestAssetExportUrls:
    """Asset export ``generatePresignedUrls``: per-file download URLs."""

    @pytest.mark.parametrize("name", HANDLER_NAMES)
    def test_export_url_is_a_named_attachment(self, monkeypatch, name):
        m = _load_asset_export_service()
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://signed.example/x"
        monkeypatch.setattr(m, "s3_client", mock_s3)
        monkeypatch.setattr(m, "presigned_get_object_params", _shipped_s3().presigned_get_object_params)
        assert m.generate_presigned_url("test-bucket", f"asset1/{name}", "v1") == "https://signed.example/x"
        params = _signed_params(mock_s3)
        assert params["VersionId"] == "v1"
        assert _decoded_filename(params["ResponseContentDisposition"]) == name


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
class TestStreamUrlsStayInline:
    """The stream routes' 307 redirects: the viewers render from these, so no attachment is signed."""

    @pytest.mark.parametrize("proxy", ["scans/photo.png", "docs/doc.pdf", "clips/clip.mp4", "notes.txt"])
    def test_stream_redirect_has_no_disposition(self, monkeypatch, proxy):
        m = _load_stream()
        mock_s3 = _wire_stream(m)
        monkeypatch.setattr(m, "presigned_get_object_params", _shipped_s3().presigned_get_object_params)
        response = m.lambda_handler(
            _stream_event(proxy, "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}",
                          "download/stream"),
            MagicMock(),
        )
        assert response["statusCode"] == 307
        assert "ResponseContentDisposition" not in _signed_params(mock_s3)

    def test_stream_redirect_of_an_svg_keeps_the_unnamed_svg_override(self, monkeypatch):
        m = _load_stream()
        mock_s3 = _wire_stream(m)
        monkeypatch.setattr(m, "presigned_get_object_params", _shipped_s3().presigned_get_object_params)
        response = m.lambda_handler(
            _stream_event("drawings/plan.svg", "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}",
                          "download/stream"),
            MagicMock(),
        )
        assert response["statusCode"] == 307
        assert _signed_params(mock_s3)["ResponseContentDisposition"] == "attachment"

    def test_auxiliary_preview_redirect_has_no_disposition(self, monkeypatch):
        m = _load_aux_stream()
        mock_s3 = _wire_aux_stream(m)
        monkeypatch.setattr(m, "presigned_get_object_params", _shipped_s3().presigned_get_object_params)
        response = m.lambda_handler(
            _stream_event(
                "scans/pump.e57/preview/r/octree.bin",
                "/database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}",
                "auxiliaryPreviewAssets/stream",
            ),
            MagicMock(),
        )
        assert response["statusCode"] == 307
        assert "ResponseContentDisposition" not in _signed_params(mock_s3)
