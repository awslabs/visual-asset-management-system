# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Macro-enabled Office files are refused on every path that hands out file content.

The core lists in ``common/constants.py`` hold every macro-enabled Word, Excel and PowerPoint
extension and its registered MIME type, so a stored file is refused by its extension and, under any
name, by the content type Amazon S3 reports for it. The macro-free Office types (``.docx``,
``.xlsx``, ``.pptx``) and JavaScript files stay allowed.

The helper under test is the real one from ``common/s3.py`` with the real merged lists, built by
``test_stored_docx_js_content_types_served._real_blocklist_helper``. Each surface runs through its own
handler code with that helper patched in: the single-file download, the GET of both stream routes and
the export's per-file download URL. Zip downloads and workflow outputs are covered in
``tests/handlers/zipDownloads/test_zip_macro_enabled_office_types.py`` and
``tests/handlers/workflows/test_processOutput_macro_enabled_office_types.py``.
"""

import json
import os

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

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
from tests.handlers.assets.test_downloadAsset_key_containment import (  # noqa: E402
    SLASHED_BASE,
    _signed_keys,
)
from tests.handlers.assets.test_stored_docx_js_content_types_served import (  # noqa: E402
    _STREAMS,
    REFUSAL,
    STREAM_REFUSAL_PREFIX,
    _download,
    _real_blocklist_helper,
    _stream,
)

OCTET = "application/octet-stream"

# (extension, MIME type as Amazon S3 reports it: the registered spelling with a capital E)
MACRO_ENABLED_TYPES = [
    (".dotm", "application/vnd.ms-word.template.macroEnabled.12"),
    (".xlsm", "application/vnd.ms-excel.sheet.macroEnabled.12"),
    (".xltm", "application/vnd.ms-excel.template.macroEnabled.12"),
    (".xlsb", "application/vnd.ms-excel.sheet.binary.macroEnabled.12"),
    (".xlam", "application/vnd.ms-excel.addin.macroEnabled.12"),
    (".pptm", "application/vnd.ms-powerpoint.presentation.macroEnabled.12"),
    (".potm", "application/vnd.ms-powerpoint.template.macroEnabled.12"),
    (".ppsm", "application/vnd.ms-powerpoint.slideshow.macroEnabled.12"),
    (".ppam", "application/vnd.ms-powerpoint.addin.macroEnabled.12"),
    (".sldm", "application/vnd.ms-powerpoint.slide.macroEnabled.12"),
]

# (asset-relative path, stored content type): each type refused by its extension, and by its MIME
# type on a file whose name carries no blocked extension.
REFUSED = [
    case
    for ext, mime in MACRO_ENABLED_TYPES
    for case in (
        pytest.param(f"office/macro{ext}", OCTET, id=f"{ext[1:]}-extension"),
        pytest.param("office/renamed.dat", mime, id=f"{ext[1:]}-type"),
    )
]

SERVED = [
    pytest.param("office/report.docx",
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document", id="docx"),
    pytest.param("office/sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 id="xlsx"),
    pytest.param("office/deck.pptx",
                 "application/vnd.openxmlformats-officedocument.presentationml.presentation", id="pptx"),
    pytest.param("web/viewer.js", "application/javascript", id="js"),
    pytest.param("web/viewer.js", "text/javascript", id="js-text-type"),
    pytest.param("web/module.mjs", "text/javascript", id="mjs"),
]


@pytest.fixture(scope="module")
def helper():
    return _real_blocklist_helper()


@pytest.mark.unit
class TestTheHelper:
    @pytest.mark.parametrize("path, content_type", REFUSED)
    def test_a_macro_enabled_office_file_is_refused(self, helper, path, content_type):
        assert helper(f"{SLASHED_BASE}{path}", content_type) is False

    @pytest.mark.parametrize("ext, mime", MACRO_ENABLED_TYPES)
    def test_upper_case_extension_and_lower_case_type_are_refused(self, helper, ext, mime):
        assert helper(f"{SLASHED_BASE}office/MACRO{ext.upper()}", OCTET) is False
        assert helper(f"{SLASHED_BASE}office/renamed.dat", mime.lower()) is False

    @pytest.mark.parametrize("path, content_type", SERVED)
    def test_a_macro_free_office_file_or_a_script_is_allowed(self, helper, path, content_type):
        assert helper(f"{SLASHED_BASE}{path}", content_type) is True


@pytest.mark.unit
class TestDownload:
    @pytest.mark.parametrize("path, content_type", REFUSED)
    def test_a_macro_enabled_office_file_gets_no_url(self, monkeypatch, helper, path, content_type):
        with pytest.raises(Exception) as excinfo:
            _download(monkeypatch, helper, path, content_type)
        assert REFUSAL in str(excinfo.value)

    @pytest.mark.parametrize("path, content_type", SERVED)
    def test_a_macro_free_office_file_or_a_script_gets_a_url(self, monkeypatch, helper, path, content_type):
        m = _download(monkeypatch, helper, path, content_type)
        assert _signed_keys(m) == [f"{SLASHED_BASE}{path}"]


@pytest.mark.unit
class TestStream:
    @pytest.mark.parametrize("module, proxy_prefix", _STREAMS)
    @pytest.mark.parametrize("path, content_type", REFUSED)
    def test_a_macro_enabled_office_file_is_refused(self, monkeypatch, helper, module, proxy_prefix, path,
                                                    content_type):
        response = _stream(module, proxy_prefix, monkeypatch, helper, path, content_type)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"].startswith(STREAM_REFUSAL_PREFIX)

    @pytest.mark.parametrize("module, proxy_prefix", _STREAMS)
    @pytest.mark.parametrize("path, content_type", SERVED)
    def test_a_macro_free_office_file_or_a_script_is_streamed(self, monkeypatch, helper, module, proxy_prefix,
                                                             path, content_type):
        response = _stream(module, proxy_prefix, monkeypatch, helper, path, content_type)
        assert response["statusCode"] in (200, 206, 307), response
        assert STREAM_REFUSAL_PREFIX not in (response.get("body") or "")


@pytest.mark.unit
class TestExport:
    @pytest.mark.parametrize("ext, mime", MACRO_ENABLED_TYPES)
    def test_the_macro_enabled_files_are_withheld_and_the_others_signed(self, helper, ext, mime):
        names = {
            "report.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "viewer.js": "application/javascript",
            f"macro{ext}": OCTET,
            "renamed.dat": mime,
        }
        m = _load_asset_export_service()
        spies = _Spies()

        entry = _run_export(m, spies, [_row(name) for name in names], _ExportS3(names),
                            validate=helper, generatePresignedUrls=True)

        assert _urls(entry) == {
            "/report.docx": _SIGNED_URL,
            "/viewer.js": _SIGNED_URL,
            f"/macro{ext}": None,
            "/renamed.dat": None,
        }, _urls(entry)
        assert sorted(_audited_paths(spies)) == [f"{_EXPORT_PREFIX}report.docx", f"{_EXPORT_PREFIX}viewer.js"]
