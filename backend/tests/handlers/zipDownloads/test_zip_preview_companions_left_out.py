# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1622: a Download as Zip leaves the generated preview companions ({file}.previewFile.{ext}) out, as the
asset file list hides them and the export folds them into their base file's entry.

The create counts, head-checks and records only the files the archive will hold: a whole asset, a folder
and an asset-version pin skip the companions, an explicitly selected companion is left out like the file
list leaves it out (not refused), and a selection of nothing but companions is refused with the
container's "no files" words. The request manifest records ``includePreviewFiles: false``, the value the
zipArchive container applies (its rules.include_preview_files; missing reads as false there too).
"""

import json
import sys
import types

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

# The asset of the live report: four listed files, three of them with a generated preview companion.
LISTED = {"xasset1/a.glb": 100, "xasset1/textures/t1.png": 20, "xasset1/textures/t2.png": 30,
          "xasset1/scan.e57": 40}
COMPANIONS = {"xasset1/a.glb.previewFile.png": 7, "xasset1/textures/t1.png.previewFile.jpg": 3,
              "xasset1/scan.e57.previewFile.gif": 5}


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    for key, size in {**LISTED, **COMPANIONS}.items():
        e.s3.add(key, size=size, content_type="image/png" if "previewFile" in key else "model/gltf-binary")
    return e


def _create(e, body):
    h.AUTH.user = "alice"
    return e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path("db1", "asset1"), {"databaseId": "db1", "assetId": "asset1"}, body=body), None)


def _manifest(e):
    row = next(iter(e.zip.items.values()))
    return json.loads(e.s3.puts[(h.AUX, row["manifestKey"])])


def _pin(monkeypatch, files):
    fake = types.ModuleType("handlers.assets.assetVersions")
    fake.validate_asset_version_exists = lambda db, a, v: True
    fake.get_asset_file_versions = lambda db, a, v, **_kw: {"files": files}
    fake.resolve_asset_version_id_from_alias = lambda db, a, alias: alias
    monkeypatch.setitem(sys.modules, "handlers.assets.assetVersions", fake)


class TestCurrentFiles:
    def test_the_whole_asset_counts_the_listed_files_only(self, env):
        # Control: the bucket holds seven current objects, four listed files and three companions.
        assert len(LISTED) + len(COMPANIONS) == 7
        item = h.body_of(_create(env, {"selection": [{"type": "asset", "relativePath": "/"}]}))
        assert item["progress"]["filesTotal"] == 4
        assert item["progress"]["bytesTotal"] == sum(LISTED.values())

    def test_a_folder_counts_the_listed_files_only(self, env):
        item = h.body_of(_create(env, {"selection": [{"type": "folder", "relativePath": "/textures/"}]}))
        assert (item["progress"]["filesTotal"], item["progress"]["bytesTotal"]) == (2, 50)

    def test_the_manifest_records_that_previews_are_left_out(self, env):
        _create(env, {"selection": [{"type": "asset", "relativePath": "/"}]})
        manifest = _manifest(env)
        assert manifest["includePreviewFiles"] is False
        assert env.zd.INCLUDE_PREVIEW_FILES_KEY == "includePreviewFiles"
        assert env.zd.ZIP_DOWNLOAD_INCLUDE_PREVIEW_FILES is False

    def test_an_explicit_companion_is_left_out_beside_its_base_file(self, env):
        response = _create(env, {"selection": [
            {"type": "file", "relativePath": "/a.glb"}, {"type": "file", "relativePath": "/a.glb.previewFile.png"}]})
        assert response["statusCode"] == 200
        item = h.body_of(response)
        assert (item["progress"]["filesTotal"], item["progress"]["bytesTotal"]) == (1, 100)
        assert [e["relativePath"] for e in _manifest(env)["entries"]] == ["/a.glb"]
        # The launch names the base file only.
        launched = json.loads(env.lam.calls[0][2]["body"])["inputFiles"]
        assert [f["relativeFileKey"] for f in launched] == ["/a.glb"]

    def test_a_selection_of_companions_only_is_refused_as_empty(self, env):
        response = _create(env, {"selection": [{"type": "file", "relativePath": "/a.glb.previewFile.png"}]})
        assert response["statusCode"] == 400
        assert env.zd.MSG_NO_FILES in h.body_of(response)["message"]
        assert env.zip.items == {} and env.lam.calls == []

    def test_a_folder_of_companions_only_reads_as_empty(self, env):
        env.s3.add("xasset1/thumbs/x.glb.previewFile.png", size=2, content_type="image/png")
        response = _create(env, {"selection": [{"type": "folder", "relativePath": "/thumbs/"}]})
        assert response["statusCode"] == 400
        assert "empty or does not exist" in h.body_of(response)["message"]

    def test_the_no_files_refusal_matches_the_container(self, env):
        import os
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 4))
        path = os.path.join(repo, "backendPipelines", "utility", "zipArchive", "container",
                            "zip_archive_pipeline", "resolve.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        assert f'raise Refusal("{env.zd.MSG_NO_FILES}")' in source


class TestAssetVersionPin:
    SNAPSHOT = [{"relativeKey": "/a.glb", "versionId": "v1", "size": 100},
                {"relativeKey": "/a.glb.previewFile.png", "versionId": "v1", "size": 7},
                {"relativeKey": "/textures/t1.png", "versionId": "v1", "size": 20},
                {"relativeKey": "/textures/t1.png.previewFile.jpg", "versionId": "v1", "size": 3}]

    def test_a_pinned_whole_asset_leaves_the_companions_out(self, env, monkeypatch):
        _pin(monkeypatch, self.SNAPSHOT)
        response = _create(env, {"selection": [{"type": "asset", "relativePath": "/"}], "assetVersionId": "3"})
        assert response["statusCode"] == 200
        assert [p["relativePath"] for p in _manifest(env)["pinnedFiles"]] == ["/a.glb", "/textures/t1.png"]

    def test_a_pinned_explicit_companion_is_left_out_not_refused(self, env, monkeypatch):
        _pin(monkeypatch, self.SNAPSHOT)
        response = _create(env, {"selection": [
            {"type": "file", "relativePath": "/a.glb"}, {"type": "file", "relativePath": "/a.glb.previewFile.png"}],
            "assetVersionId": "3"})
        assert response["statusCode"] == 200
        assert [p["relativePath"] for p in _manifest(env)["pinnedFiles"]] == ["/a.glb"]

    def test_a_pinned_selection_of_companions_only_is_refused(self, env, monkeypatch):
        _pin(monkeypatch, self.SNAPSHOT)
        response = _create(env, {"selection": [{"type": "file", "relativePath": "/a.glb.previewFile.png"}],
                                 "assetVersionId": "3"})
        assert response["statusCode"] == 400
        assert env.zip.items == {}
