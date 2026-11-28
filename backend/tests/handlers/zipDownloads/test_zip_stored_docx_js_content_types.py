# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download as Zip accepts a Word document (.docx) and a JavaScript file (.js) stored with their real
content types, and still refuses a macro-enabled Word document.

The create heads every selected file and runs the shared blocklist helper on its key and reported
content type, and checks a selected folder's members by name; the download URL route rechecks each
member's current object; and the request manifest hands the archive job the deployment's merged
lists, which the job applies to every member's key and type before it writes it (pinned in the
container's tests/test_runner.py). Each is driven here with the real helper and the real merged lists from
``common/constants.py`` in place of the harness stand-in, which blocks only ``.exe``.
"""

import importlib.util
import json
import os
from unittest.mock import MagicMock

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
JS_TYPE = "application/javascript"
DOCM_TYPE = "application/vnd.ms-word.document.macroEnabled.12"


def _real_constants():
    spec = importlib.util.spec_from_file_location(
        "constants_for_zip_docx_js", os.path.join(h.BACKEND, "common", "constants.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real_blocklist_helper(constants):
    """validateUnallowedFileExtensionAndContentType from the real source, bound to ``constants``."""
    s3_path = os.path.join(h.BACKEND, "common", "s3.py")
    with open(s3_path, encoding="utf-8") as f:
        source = f.read()
    start = source.index("def validateUnallowedFileExtensionAndContentType(")
    end = source.index("\ndef validateS3AssetExtensionsAndContentType(", start)
    namespace = {"os": os, "logger": MagicMock(),
                 "UNALLOWED_MIME_LIST": constants.UNALLOWED_MIME_LIST,
                 "UNALLOWED_FILE_EXTENSION_LIST": constants.UNALLOWED_FILE_EXTENSION_LIST}
    exec(compile(source[start:end], s3_path, "exec"), namespace)
    return namespace["validateUnallowedFileExtensionAndContentType"]


@pytest.fixture
def env(monkeypatch):
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    constants = _real_constants()
    monkeypatch.setattr(e.svc, "validateUnallowedFileExtensionAndContentType", _real_blocklist_helper(constants))
    monkeypatch.setattr(e.svc, "UNALLOWED_MIME_LIST", constants.UNALLOWED_MIME_LIST)
    monkeypatch.setattr(e.svc, "UNALLOWED_FILE_EXTENSION_LIST", constants.UNALLOWED_FILE_EXTENSION_LIST)
    e.s3.add("xasset1/docs/report.docx", size=100, content_type=DOCX_TYPE)
    e.s3.add("xasset1/web/viewer.js", size=10, content_type=JS_TYPE)
    return e


def _create(e, selection):
    h.AUTH.user = "alice"
    return e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"},
        body={"selection": selection}), None)


def _manifest(e):
    row = next(iter(e.zip.items.values()))
    return json.loads(e.s3.puts[(h.AUX, row["manifestKey"])])


class TestCreate:
    @pytest.mark.parametrize("selection", [
        pytest.param([{"type": "file", "relativePath": "/docs/report.docx"},
                      {"type": "file", "relativePath": "/web/viewer.js"}], id="files"),
        pytest.param([{"type": "folder", "relativePath": "/docs/"},
                      {"type": "folder", "relativePath": "/web/"}], id="folders"),
    ])
    def test_docx_and_js_files_are_accepted(self, env, selection):
        response = _create(env, selection)
        assert response["statusCode"] == 200, h.body_of(response)
        assert h.body_of(response)["progress"]["filesTotal"] == 2

    def test_the_manifest_hands_the_archive_job_lists_without_the_two_types(self, env):
        assert _create(env, [{"type": "file", "relativePath": "/docs/report.docx"}])["statusCode"] == 200
        blocked = _manifest(env)["blockedContentTypes"]
        assert DOCX_TYPE not in blocked and JS_TYPE not in blocked
        # Control: the macro-enabled Word type is still in the list the job applies.
        assert DOCM_TYPE.lower() in blocked
        assert ".docm" in _manifest(env)["blockedExtensions"]

    @pytest.mark.parametrize("key, content_type, selection", [
        pytest.param("xasset1/docs/macro.docm", "application/octet-stream",
                     {"type": "file", "relativePath": "/docs/macro.docm"}, id="docm-extension"),
        pytest.param("xasset1/docs/renamed.docx", DOCM_TYPE,
                     {"type": "file", "relativePath": "/docs/renamed.docx"}, id="macro-enabled-type"),
        # A folder's members are checked by name at create (the listing reports no content type);
        # the archive job checks each member's type against the manifest lists.
        pytest.param("xasset1/docs/macro.docm", "application/octet-stream",
                     {"type": "folder", "relativePath": "/docs/"}, id="docm-extension-in-folder"),
    ])
    def test_a_macro_enabled_word_document_refuses_the_job(self, env, key, content_type, selection):
        env.s3.add(key, size=5, content_type=content_type)
        response = _create(env, [selection])
        assert response["statusCode"] == 400
        assert env.zip.items == {} and env.lam.calls == []


class TestDownloadUrlRecheck:
    def test_a_docx_or_js_member_is_still_downloadable(self, env):
        assert env.svc._current_member_ok(h.ASSET_BUCKET, "xasset1/docs/report.docx") is True
        assert env.svc._current_member_ok(h.ASSET_BUCKET, "xasset1/web/viewer.js") is True

    def test_a_member_whose_current_object_became_macro_enabled_is_not(self, env):
        env.s3.add("xasset1/docs/report.docx", size=100, version="v2", content_type=DOCM_TYPE)
        assert env.svc._current_member_ok(h.ASSET_BUCKET, "xasset1/docs/report.docx") is False
