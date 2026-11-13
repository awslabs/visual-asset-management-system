# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A quarantined asset still serves its preview images (#1495), and nothing else.

With `app.compliance.quarantineBlocksDownload` on, the asset preview image (`downloadType:
assetPreview`) and a file's `{file}.previewFile.{ext}` companion with an allowed preview extension,
each at its current version, are served through the download route and both stream methods, after
Tier 1 and Tier 2 authorization as before, and the served preview keeps its download audit record.
The base file, a key that carries the marker in a folder name, a companion with a disallowed
extension, a pinned version of a companion, a traversal or encoded key, a bulk download, export and
the auxiliary viewer data stay refused. The preview test is on the resolved key, not the request.
"""

import json
from unittest.mock import MagicMock

import pytest

import common.compliance.quarantineGuard as guard
from backend.tests.handlers.assets import test_downloadAsset_quarantine_guard as dl
from backend.tests.handlers.assets import test_streamAsset_quarantine_guard as st
from backend.tests.quarantineRefusalBody import assert_is_the_quarantine_refusal

COMPANION = "/model.glb.previewFile.png"


def _download_event(key=None, keys=None, version_id=None):
    event = dl._event("assetFile", keys=keys)
    if key is not None:
        body = json.loads(event["body"])
        body["key"] = key
        if version_id:
            body["versionId"] = version_id
        event["body"] = json.dumps(body)
    return event


def _download(event, **wire):
    m = dl._load()
    s3, enforcer = dl._wire(m, **wire)
    table = dl._state_table(dl._quarantined())
    with dl._guard_enabled(table):
        response = m.lambda_handler(event, MagicMock())
    return m, s3, enforcer, table, response


def _stream_event(method, proxy, query=None):
    event = st._rest_event(method)
    event["pathParameters"]["proxy"] = proxy
    event["path"] = event["requestContext"]["path"] = (
        f"/database/{st._DB}/assets/{st._ASSET}/download/stream/{proxy}")
    event["queryStringParameters"] = query
    return event


def _stream(event, **wire):
    m = st._load()
    s3, enforcer = st._wire(m, **wire)
    table = st._state_table(st._quarantined())
    with st._guard_enabled(table):
        response = m.lambda_handler(event, MagicMock())
    return m, s3, enforcer, table, response


@pytest.mark.unit
class TestTheDownloadRouteServesPreviews:

    def test_the_asset_preview_image_is_served_and_audited(self):
        m, s3, _enforcer, table, response = _download(dl._event("assetPreview"))
        assert response["statusCode"] == 200, response
        body = json.loads(response["body"])
        assert (body["downloadUrl"], body["downloadType"]) == (dl._SIGNED_URL, "assetPreview")
        table.get_item.assert_not_called()
        m.log_file_download.assert_called_once()

    def test_a_preview_companion_is_served_and_audited(self):
        m, s3, _enforcer, table, response = _download(_download_event(COMPANION))
        assert response["statusCode"] == 200, response
        assert json.loads(response["body"])["downloadUrl"] == dl._SIGNED_URL
        assert s3.generate_presigned_url.call_args.kwargs["Params"]["Key"] == \
            f"{dl._ASSET}/model.glb.previewFile.png"
        table.get_item.assert_not_called()
        m.log_file_download.assert_called_once()

    @pytest.mark.parametrize("key", [
        "/model.glb",
        "/renders.previewFile.png/model.glb",
        "/model.glb.previewFile.exe",
        "/model.glb.previewFile.png.zip",
        "/.previewFile.png",
        "/model.glb%2epreviewFile.png",
        "/model.glb.PREVIEWFILE.png",
    ], ids=["base-file", "marker-in-folder", "disallowed-extension", "extension-not-last",
            "no-base-name", "encoded", "marker-case"])
    def test_any_other_key_is_refused(self, key):
        m, s3, _enforcer, table, response = _download(_download_event(key))
        assert_is_the_quarantine_refusal(response)
        table.get_item.assert_called_once()
        s3.head_object.assert_not_called()
        s3.generate_presigned_url.assert_not_called()
        m.log_file_download.assert_not_called()

    def test_a_traversal_through_a_companion_name_is_refused(self):
        """The request model refuses the `..` segment before the guard; were it to pass, the
        resolved key's `..` segment would still not be a preview (`TestThePreviewTest`)."""
        m, s3, _enforcer, _table, response = _download(
            _download_event("/model.glb.previewFile.png/../model.glb"))
        assert response["statusCode"] == 400
        s3.head_object.assert_not_called()
        s3.generate_presigned_url.assert_not_called()
        m.log_file_download.assert_not_called()

    def test_a_pinned_version_of_a_companion_is_refused(self):
        m, s3, _enforcer, table, response = _download(_download_event(COMPANION, version_id="v1"))
        assert_is_the_quarantine_refusal(response)
        s3.generate_presigned_url.assert_not_called()

    def test_a_bulk_download_of_previews_only_is_refused(self):
        m, s3, _enforcer, table, response = _download(
            _download_event(keys=[COMPANION, "/part.stl.previewFile.jpg"]))
        assert_is_the_quarantine_refusal(response)
        s3.generate_presigned_url.assert_not_called()
        m.log_file_download_bulk.assert_not_called()

    @pytest.mark.parametrize("event", [
        dl._event("assetPreview"), _download_event(COMPANION)], ids=["asset-preview", "companion"])
    def test_a_denied_caller_gets_403_first(self, event):
        m, s3, _enforcer, table, response = _download(event, object_allowed=False)
        assert response["statusCode"] == 403
        assert "quarantin" not in response["body"].lower()
        table.get_item.assert_not_called()
        s3.generate_presigned_url.assert_not_called()

    def test_an_asset_preview_location_without_an_image_extension_is_guarded(self):
        m = dl._load()
        s3, _enforcer = dl._wire(m)
        asset = m.get_asset_details.return_value
        asset["previewLocation"] = {"Key": f"{dl._ASSET}/model.glb"}
        table = dl._state_table(dl._quarantined())
        with dl._guard_enabled(table):
            response = m.lambda_handler(dl._event("assetPreview"), MagicMock())
        assert_is_the_quarantine_refusal(response)
        s3.generate_presigned_url.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("method", ["HEAD", "GET"])
class TestTheStreamRoutesServePreviewCompanions:

    def test_a_preview_companion_is_served_and_audited(self, method):
        m, s3, _enforcer, table, response = _stream(
            _stream_event(method, "model.glb.previewFile.png"))
        assert response["statusCode"] == st._SUCCESS[method], response
        table.get_item.assert_not_called()
        if method == "GET":
            m.log_file_download_streamed.assert_called_once()

    @pytest.mark.parametrize("proxy", [
        "scans/pump.glb", "renders.previewFile.png/pump.glb", "pump.glb.previewFile.exe"],
        ids=["base-file", "marker-in-folder", "disallowed-extension"])
    def test_any_other_key_is_refused(self, method, proxy):
        m, s3, _enforcer, table, response = _stream(_stream_event(method, proxy))
        assert_is_the_quarantine_refusal(response)
        assert not st._s3_touched(s3)
        m.log_file_download_streamed.assert_not_called()

    def test_a_traversal_through_a_companion_name_is_refused(self, method):
        m, s3, _enforcer, _table, response = _stream(
            _stream_event(method, "pump.glb.previewFile.png/../pump.glb"))
        assert response["statusCode"] == 400
        assert not st._s3_touched(s3)
        m.log_file_download_streamed.assert_not_called()

    def test_a_pinned_version_of_a_companion_is_refused(self, method):
        m, s3, _enforcer, table, response = _stream(
            _stream_event(method, "model.glb.previewFile.png", query={"versionId": "v1"}))
        assert_is_the_quarantine_refusal(response)
        assert not st._s3_touched(s3)

    def test_a_denied_caller_gets_403_first(self, method):
        m, s3, _enforcer, table, response = _stream(
            _stream_event(method, "model.glb.previewFile.png"), object_allowed=False)
        assert response["statusCode"] == 403
        table.get_item.assert_not_called()
        assert not st._s3_touched(s3)


@pytest.mark.unit
class TestThePreviewTest:

    @pytest.mark.parametrize("key,expected", [
        ("a1/model.glb.previewFile.png", True),
        ("a1/sub/model.glb.previewFile.JPEG", True),
        ("a1/model.glb.previewFile.svg", True),
        ("a1/model.glb", False),
        ("a1/x.previewFile.png/model.glb", False),
        ("a1/model.glb.previewFile.exe", False),
        ("a1/m.previewFile.previewFile.png", False),
        ("a1/.previewFile.png", False),
        ("a1/../a2/model.glb.previewFile.png", False),
        ("a1/./model.glb.previewFile.png", False),
        ("a1//model.glb.previewFile.png", False),
        ("a1/model.glb.previewFile.png/", False),
        ("a1/model%2Eglb.previewFile.png", False),
        ("a1\\model.glb.previewFile.png", False),
        ("", False),
        (None, False),
    ])
    def test_a_file_preview_companion(self, key, expected):
        assert guard.is_preview_object(key) is expected

    @pytest.mark.parametrize("key,expected", [
        ("previews/a1/thumb.png", True), ("previews/a1/thumb.GIF", True),
        ("previews/a1/thumb.glb", False), ("previews/a1/png", False),
        ("previews/a1/../a2/thumb.png", False)])
    def test_the_asset_preview_image(self, key, expected):
        assert guard.is_preview_object(key, asset_preview=True) is expected

    def test_a_preview_key_skips_the_state_read_only_while_the_block_is_on(self):
        table = MagicMock()
        table.get_item.return_value = {"Item": {"complianceState": "quarantined"}}
        with dl._guard_enabled(table):
            guard.check_quarantine_block("db1", "a1", object_key="a1/m.glb.previewFile.png")
            table.get_item.assert_not_called()
            with pytest.raises(guard.QuarantineBlockedResponse):
                guard.check_quarantine_block("db1", "a1", object_key="a1/m.glb")
            with pytest.raises(guard.QuarantineBlockedResponse):
                guard.check_quarantine_block("db1", "a1")
