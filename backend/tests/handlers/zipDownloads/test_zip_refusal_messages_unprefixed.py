# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1638: a refused Download as Zip answers the sentence the zip download pages quote, without the
"VAMS General Error: " prefix that ``VAMSGeneralErrorResponse`` puts on its ``str()``, and with the status
code it had. The web, the CLI and the MCP tools show the API's ``message`` as they receive it, so the bare
sentence is what a user reads. The refusals raised by the shared helpers (download eligibility, asset
versions, the request-body parse) answer the same way on the zip routes."""

import json
import sys
import types

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

PREFIX = "VAMS General Error"


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    e.s3.add("xasset1/a.glb.previewFile.png", size=7, version="vp1", content_type="image/png")
    return e


def _create(e, body, raw_body=None):
    h.AUTH.user = "alice"
    event = h.api_event("POST", h.asset_path("db1", "asset1"), {"databaseId": "db1", "assetId": "asset1"},
                        body=body)
    if raw_body is not None:
        event["body"] = raw_body
    return e.svc.lambda_handler(event, None)


def _answer(response):
    return response["statusCode"], h.body_of(response)["message"]


class TestCreateRefusals:
    @pytest.mark.parametrize("setup,body,status,message", [
        (None, {"selection": [{"type": "file", "relativePath": "/a.glb.previewFile.png"}]}, 400,
         "The selection has no files"),
        (lambda e: e.s3.add("xasset1/run.exe", size=5), {"selection": [{"type": "file", "relativePath": "/run.exe"}]},
         400, "Unallowed file extension or content type in asset file"),
        (None, {"selection": [{"type": "file", "relativePath": "/never.glb"}]}, 400, "File not found in S3"),
        (lambda e: e.s3.add("xasset1/old.glb", metadata={"vams-status": "archived"}),
         {"selection": [{"type": "file", "relativePath": "/old.glb"}]}, 400,
         "The selection includes an archived or deleted item. Remove it from the selection, or restore it first."),
        (None, {"selection": [{"type": "folder", "relativePath": "/empty/"}]}, 400,
         "A selected folder is empty or does not exist"),
        (lambda e: h.put_asset(e, distributable=False), {"selection": [{"type": "file", "relativePath": "/a.glb"}]},
         400, "Asset not distributable"),
    ], ids=["previews-only", "blocked-type", "missing-file", "archived", "empty-folder", "not-distributable"])
    def test_a_refused_create_answers_the_bare_sentence_with_its_status(self, env, setup, body, status, message):
        if setup:
            setup(env)
        assert _answer(_create(env, body)) == (status, message)
        assert env.zip.items == {} and env.lam.calls == []

    def test_a_deleted_version_keeps_its_410(self, env, monkeypatch):
        env.s3.add("xasset1/gone.glb", version="dm", delete_marker=True)
        fake = types.ModuleType("handlers.assets.assetVersions")
        fake.validate_asset_version_exists = lambda db, a, v: True
        fake.get_asset_file_versions = lambda db, a, v, **_kw: {"files": [
            {"relativeKey": "/gone.glb", "versionId": "dm", "size": 1}]}
        fake.resolve_asset_version_id_from_alias = lambda db, a, alias: alias
        monkeypatch.setitem(sys.modules, "handlers.assets.assetVersions", fake)
        status, message = _answer(_create(env, {"selection": [{"type": "asset", "relativePath": "/"}],
                                                "assetVersionId": "3"}))
        assert status == 410
        assert message == "File version has been archived and cannot be downloaded"

    def test_an_unparseable_body_answers_without_the_prefix(self, env):
        status, message = _answer(_create(env, None, raw_body="{not json"))
        assert status == 400 and not message.startswith(PREFIX)

    def test_no_create_refusal_carries_the_prefix(self, env):
        # Control for the parametrised cases: the base class's str() still carries it.
        error = env.svc.VAMSGeneralErrorResponse("The selection has no files")
        assert str(error) == "VAMS General Error: The selection has no files"
        assert error.user_message == "The selection has no files"
        assert env.svc._refusal_message(error) == "The selection has no files"


class TestUrlRefusal:
    def test_a_job_on_an_asset_no_longer_distributable_answers_the_bare_sentence(self, env):
        item = h.body_of(_create(env, {"selection": [{"type": "file", "relativePath": "/a.glb"}]}))
        h.put_asset(env, distributable=False)
        h.AUTH.user = "alice"
        response = env.svc.lambda_handler(h.api_event(
            "POST", h.asset_path("db1", "asset1", f"/{item['requestId']}/url"),
            {"databaseId": "db1", "assetId": "asset1", "requestId": item["requestId"]}), None)
        assert _answer(response) == (400, "Asset not distributable")


def test_the_quarantine_refusal_is_unchanged():
    import common.compliance.quarantineGuard as guard
    assert str(guard.QuarantineBlockedResponse()) == guard.QUARANTINE_BLOCK_MESSAGE
    assert json.dumps(guard.QUARANTINE_BLOCK_MESSAGE).find(PREFIX) < 0
