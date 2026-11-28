# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A stored Word document (.docx) or JavaScript file (.js) is served with its real content type.

Every path that hands out file content runs ``common.s3.validateUnallowedFileExtensionAndContentType``
on the file's key and the content type Amazon S3 reports for it. A file uploaded through VAMS is stored
as ``application/octet-stream``, but a pipeline output, a file in an external bucket or a file written
straight to an asset bucket keeps its real type. The core MIME list does not hold the Word document
type or ``application/javascript``, so such files download, stream and export like any other file. The
macro-enabled Word document stays refused, by its ``.docm`` extension and by its
``application/vnd.ms-word.document.macroEnabled.12`` type, on a key with any extension.

The helper under test is the real one from ``common/s3.py``, loaded from source with the real merged
lists (the conftest stand-in always allows). Each surface is driven through its own handler code with
that helper patched in: the single-file and prefix download, the GET of both stream routes, and the
export's per-file download URL. Zip downloads and workflow outputs are covered in
``tests/handlers/zipDownloads/test_zip_stored_docx_js_content_types.py`` and
``tests/handlers/workflows/test_processOutput_stored_docx_js_content_types.py``.
"""

import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

# Env vars downloadAsset requires at import time (mirrors test_downloadAsset_bulk).
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

from backend.tests.handlers.assets import test_streamAsset_range_header_casing as asset_stream  # noqa: E402
from backend.tests.handlers.assets import (  # noqa: E402
    test_streamAuxiliaryPreviewAsset_range_header_casing as aux_stream,
)
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
)
from tests.handlers.assets.test_assetExportService_blocklist import (  # noqa: E402
    _PREFIX as _EXPORT_PREFIX,
    _S3 as _ExportS3,
    _audited_paths,
    _row,
    _run as _run_export,
)
from tests.handlers.assets.test_assetExportService_download_controls import (  # noqa: E402
    _SIGNED_URL,
    _Spies,
    _urls,
)
from tests.handlers.assets.test_downloadAsset_bulk import _load as _load_download  # noqa: E402
from tests.handlers.assets.test_downloadAsset_key_containment import (  # noqa: E402
    SLASHED_BASE,
    _parse,
    _signed_keys,
    _wire as _wire_download,
)

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
JS_TYPE = "application/javascript"
# The spelling S3 reports for a macro-enabled Word document; the helper lower-cases it.
DOCM_TYPE = "application/vnd.ms-word.document.macroEnabled.12"
REFUSAL = "Unallowed file extension or content type in asset file"
# Both stream routes answer with this prefix ("... in asset file", "... in auxiliary preview file").
STREAM_REFUSAL_PREFIX = "Unallowed file extension or content type in "

# (asset-relative path, stored content type)
SERVED = [
    pytest.param("docs/report.docx", DOCX_TYPE, id="docx"),
    pytest.param("web/viewer.js", JS_TYPE, id="js"),
]
REFUSED = [
    pytest.param("docs/macro.docm", "application/octet-stream", id="docm-extension"),
    pytest.param("docs/renamed.docx", DOCM_TYPE, id="macro-enabled-type"),
]

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")


def _real_blocklist_helper():
    """validateUnallowedFileExtensionAndContentType from the real source, with the real merged lists.

    tests/conftest.py replaces ``common.s3`` with a stand-in that always allows and ``common.constants``
    with a MagicMock, so the helper is executed from its own source segment with the lists read from
    the real constants module by path, as tests/common/test_s3_blocklist_case_insensitive.py does.
    """
    spec = importlib.util.spec_from_file_location(
        "constants_for_docx_js_served", os.path.join(_BACKEND, "common", "constants.py"))
    constants = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(constants)
    s3_path = os.path.join(_BACKEND, "common", "s3.py")
    with open(s3_path, encoding="utf-8") as f:
        source = f.read()
    start = source.index("def validateUnallowedFileExtensionAndContentType(")
    end = source.index("\ndef validateS3AssetExtensionsAndContentType(", start)
    namespace = {
        "os": os,
        "logger": MagicMock(),
        "UNALLOWED_MIME_LIST": constants.UNALLOWED_MIME_LIST,
        "UNALLOWED_FILE_EXTENSION_LIST": constants.UNALLOWED_FILE_EXTENSION_LIST,
    }
    exec(compile(source[start:end], s3_path, "exec"), namespace)
    return namespace["validateUnallowedFileExtensionAndContentType"]


@pytest.fixture(scope="module")
def helper():
    return _real_blocklist_helper()


@pytest.mark.unit
class TestTheHelper:
    @pytest.mark.parametrize("path, content_type", SERVED)
    def test_a_docx_or_js_file_with_its_real_type_is_allowed(self, helper, path, content_type):
        assert helper(f"{SLASHED_BASE}{path}", content_type) is True

    @pytest.mark.parametrize("path, content_type", REFUSED)
    def test_a_macro_enabled_word_document_is_refused(self, helper, path, content_type):
        assert helper(f"{SLASHED_BASE}{path}", content_type) is False


def _download(monkeypatch, helper, path, content_type):
    m = _wire_download(_load_download(), SLASHED_BASE)
    monkeypatch.setattr(m, "validateUnallowedFileExtensionAndContentType", helper)
    m.s3.head_object.return_value = {"ContentType": content_type, "ContentLength": 10}
    m.download_asset_file("db1", "asset1", _parse({"downloadType": "assetFile", "key": f"/{path}"}))
    return m


@pytest.mark.unit
class TestDownload:
    @pytest.mark.parametrize("path, content_type", SERVED)
    def test_the_file_gets_a_download_url(self, monkeypatch, helper, path, content_type):
        m = _download(monkeypatch, helper, path, content_type)
        assert _signed_keys(m) == [f"{SLASHED_BASE}{path}"]

    @pytest.mark.parametrize("path, content_type", REFUSED)
    def test_a_macro_enabled_word_document_gets_none(self, monkeypatch, helper, path, content_type):
        with pytest.raises(Exception) as excinfo:
            _download(monkeypatch, helper, path, content_type)
        assert REFUSAL in str(excinfo.value)

    def test_a_folder_of_docx_and_js_files_passes_the_prefix_check(self, helper):
        de = sys.modules["common.downloadEligibility"]
        types = {f"{SLASHED_BASE}docs/report.docx": DOCX_TYPE, f"{SLASHED_BASE}web/viewer.js": JS_TYPE}
        client = MagicMock()
        client.head_object.side_effect = lambda Bucket, Key, **_: {"ContentType": types[Key]}

        def check(extra=None):
            types.update(extra or {})
            listing = MagicMock(return_value=[{"Key": key} for key in types])
            return de.validate_prefix_content_types(
                client, "test-bucket", SLASHED_BASE, list_objects=listing, is_allowed=helper)

        assert check() is True
        # Control: one macro-enabled document in the same folder refuses the whole prefix.
        assert check({f"{SLASHED_BASE}docs/renamed.docx": DOCM_TYPE}) is False


def _stream_event(module, proxy):
    """The module's REST-shaped GET event for the ``proxy`` path."""
    event = module._rest_event({"Accept": "*/*"})
    old_proxy = event["pathParameters"]["proxy"]
    event["pathParameters"]["proxy"] = proxy
    for holder in (event, event["requestContext"]):
        holder["path"] = holder["path"].replace(old_proxy, proxy)
    return event


def _stream(module, proxy_prefix, monkeypatch, helper, path, content_type):
    m = module._load()
    mock_s3 = module._wire(m)
    monkeypatch.setattr(m, "validateUnallowedFileExtensionAndContentType", helper)
    mock_s3.get_object.return_value["ContentType"] = content_type
    mock_s3.get_object.return_value["ResponseMetadata"]["HTTPHeaders"]["content-type"] = content_type
    return m.lambda_handler(_stream_event(module, f"{proxy_prefix}{path}"), MagicMock())


# (harness module, proxy prefix): an auxiliary preview path names a file under a "preview/" folder.
_STREAMS = [pytest.param(asset_stream, "", id="streamAsset"),
            pytest.param(aux_stream, "scans/pump.e57/preview/", id="streamAuxiliaryPreviewAsset")]


@pytest.mark.unit
class TestStream:
    @pytest.mark.parametrize("module, proxy_prefix", _STREAMS)
    @pytest.mark.parametrize("path, content_type", SERVED)
    def test_the_file_is_streamed(self, monkeypatch, helper, module, proxy_prefix, path, content_type):
        response = _stream(module, proxy_prefix, monkeypatch, helper, path, content_type)
        assert response["statusCode"] in (200, 206, 307), response
        assert STREAM_REFUSAL_PREFIX not in (response.get("body") or "")

    @pytest.mark.parametrize("module, proxy_prefix", _STREAMS)
    @pytest.mark.parametrize("path, content_type", REFUSED)
    def test_a_macro_enabled_word_document_is_refused(self, monkeypatch, helper, module, proxy_prefix, path,
                                                      content_type):
        response = _stream(module, proxy_prefix, monkeypatch, helper, path, content_type)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"].startswith(STREAM_REFUSAL_PREFIX)


@pytest.mark.unit
class TestExport:
    def test_docx_and_js_files_are_signed_and_the_macro_enabled_ones_withheld(self, helper):
        names = {"report.docx": DOCX_TYPE, "viewer.js": JS_TYPE,
                 "macro.docm": "application/octet-stream", "renamed.docx": DOCM_TYPE}
        m = _load_asset_export_service()
        spies = _Spies()

        entry = _run_export(m, spies, [_row(name) for name in names], _ExportS3(names),
                            validate=helper, generatePresignedUrls=True)

        assert _urls(entry) == {
            "/report.docx": _SIGNED_URL,
            "/viewer.js": _SIGNED_URL,
            "/macro.docm": None,
            "/renamed.docx": None,
        }, _urls(entry)
        assert sorted(_audited_paths(spies)) == [f"{_EXPORT_PREFIX}report.docx", f"{_EXPORT_PREFIX}viewer.js"]
