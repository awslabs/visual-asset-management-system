# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download as Zip refuses a macro-enabled Office file by its extension and by its content type.

The create heads every selected file and runs the shared blocklist helper on its key and reported
content type; the download URL route rechecks each member's current object; and the request manifest
hands the archive job the merged lists it applies to every member (the job's matching is pinned in the
container's tests/test_runner.py). The helper and the lists are the real ones from ``common/s3.py``
and ``common/constants.py``, loaded by ``test_zip_stored_docx_js_content_types``.
"""

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h
from backend.tests.handlers.zipDownloads.test_zip_stored_docx_js_content_types import (
    _create,
    _manifest,
    _real_blocklist_helper,
    _real_constants,
)
from tests.handlers.assets.test_macro_enabled_office_types_refused import MACRO_ENABLED_TYPES, OCTET

pytestmark = pytest.mark.unit

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSB_TYPE = "application/vnd.ms-excel.sheet.binary.macroEnabled.12"
JS_TYPE = "application/javascript"


@pytest.fixture
def env(monkeypatch):
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    constants = _real_constants()
    monkeypatch.setattr(e.svc, "validateUnallowedFileExtensionAndContentType", _real_blocklist_helper(constants))
    monkeypatch.setattr(e.svc, "UNALLOWED_MIME_LIST", constants.UNALLOWED_MIME_LIST)
    monkeypatch.setattr(e.svc, "UNALLOWED_FILE_EXTENSION_LIST", constants.UNALLOWED_FILE_EXTENSION_LIST)
    e.s3.add("xasset1/office/report.docx", size=100, content_type=DOCX_TYPE)
    e.s3.add("xasset1/office/sheet.xlsx", size=100, content_type=XLSX_TYPE)
    e.s3.add("xasset1/office/deck.pptx", size=100, content_type=PPTX_TYPE)
    e.s3.add("xasset1/office/book.xlsb", size=100, content_type=XLSB_TYPE)
    e.s3.add("xasset1/web/viewer.js", size=10, content_type=JS_TYPE)
    return e


# (stored key, stored content type, selection entry)
REFUSED = [
    case
    for ext, mime in MACRO_ENABLED_TYPES
    for case in (
        pytest.param(f"xasset1/office/macro{ext}", OCTET,
                     {"type": "file", "relativePath": f"/office/macro{ext}"}, id=f"{ext[1:]}-extension"),
        pytest.param("xasset1/office/renamed.dat", mime,
                     {"type": "file", "relativePath": "/office/renamed.dat"}, id=f"{ext[1:]}-type"),
        # A folder's members are checked by name at create (the listing reports no content type).
        pytest.param(f"xasset1/office/macro{ext}", OCTET,
                     {"type": "folder", "relativePath": "/office/"}, id=f"{ext[1:]}-extension-in-folder"),
    )
]


class TestCreate:
    @pytest.mark.parametrize("key, content_type, selection", REFUSED)
    def test_a_macro_enabled_office_file_refuses_the_job(self, env, key, content_type, selection):
        env.s3.add(key, size=5, content_type=content_type)
        response = _create(env, [selection])
        assert response["statusCode"] == 400
        assert env.zip.items == {} and env.lam.calls == []

    @pytest.mark.parametrize("selection", [
        pytest.param([{"type": "file", "relativePath": "/office/report.docx"},
                      {"type": "file", "relativePath": "/office/sheet.xlsx"},
                      {"type": "file", "relativePath": "/office/deck.pptx"},
                      {"type": "file", "relativePath": "/office/book.xlsb"},
                      {"type": "file", "relativePath": "/web/viewer.js"}], id="files"),
        pytest.param([{"type": "folder", "relativePath": "/office/"},
                      {"type": "folder", "relativePath": "/web/"}], id="folders"),
    ])
    def test_macro_free_office_files_and_scripts_are_accepted(self, env, selection):
        response = _create(env, selection)
        assert response["statusCode"] == 200, h.body_of(response)
        assert h.body_of(response)["progress"]["filesTotal"] == 5

    def test_the_manifest_hands_the_archive_job_every_macro_enabled_type(self, env):
        assert _create(env, [{"type": "file", "relativePath": "/office/report.docx"}])["statusCode"] == 200
        manifest = _manifest(env)
        for ext, mime in MACRO_ENABLED_TYPES:
            assert ext in manifest["blockedExtensions"], ext
            assert mime.lower() in manifest["blockedContentTypes"], mime
        for content_type in (DOCX_TYPE, XLSX_TYPE, PPTX_TYPE, XLSB_TYPE.lower(), JS_TYPE):
            assert content_type not in manifest["blockedContentTypes"]
        assert ".xlsb" not in manifest["blockedExtensions"]


class TestDownloadUrlRecheck:
    @pytest.mark.parametrize("ext, mime", MACRO_ENABLED_TYPES)
    def test_a_member_whose_current_object_became_macro_enabled_is_not_downloadable(self, env, ext, mime):
        assert env.svc._current_member_ok(h.ASSET_BUCKET, "xasset1/office/sheet.xlsx") is True
        env.s3.add("xasset1/office/sheet.xlsx", size=100, version="v2", content_type=mime)
        assert env.svc._current_member_ok(h.ASSET_BUCKET, "xasset1/office/sheet.xlsx") is False
