# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download as Zip of an asset version keeps `x` and `//x` apart (#1795, one leading `/` marks a path).

An asset version can hold `x` (the key `{folder}x`) and `//x` (the key `{folder}/x`, with an empty segment after
the folder). Both used to read as `x` here, so whichever entry came later in the version's key order supplied the
version id the archive pinned for `{folder}x`, and a name sorting before `/` (`.gitignore`) took the other file's
version. Each entry is now keyed with exactly one leading `/` removed; a `//x` entry is left out, because this
route resolves a member's key from its path with every leading `/` dropped and would read `{folder}x`.
"""

import json
import sys
import types

from backend.tests.handlers.zipDownloads import zipHarness as h
from backend.tests.handlers.zipDownloads.test_zipDownloadService import _create, env  # noqa: F401


def _versions(monkeypatch, files):
    fake = types.ModuleType("handlers.assets.assetVersions")
    fake.validate_asset_version_exists = lambda db, a, v: True
    fake.get_asset_file_versions = lambda db, a, v, **_kw: {"files": files}
    fake.resolve_asset_version_id_from_alias = lambda db, a, alias: alias
    monkeypatch.setitem(sys.modules, "handlers.assets.assetVersions", fake)


def _pinned(env):  # noqa: F811
    row = next(iter(env.zip.items.values()))
    manifest = json.loads(env.s3.puts[(h.AUX, row["manifestKey"])])
    return [(p["relativePath"], p["key"], p["versionId"]) for p in manifest["pinnedFiles"]]


class TestTwoFilesOneName:
    def test_the_whole_asset_pins_the_files_own_version(self, env, monkeypatch):  # noqa: F811
        env.s3.add("xasset1/.gitignore", size=5, version="v-own")
        # Key order: '.gitignore' sorts before '//.gitignore', so the other file's entry came last
        _versions(monkeypatch, [
            {"relativeKey": ".gitignore", "versionId": "v-own", "size": 5},
            {"relativeKey": "//.gitignore", "versionId": "v-other", "size": 7},
        ])
        response = _create(env, {"selection": [{"type": "asset", "relativePath": "/"}], "assetVersionId": "3"})
        assert response["statusCode"] == 200
        assert _pinned(env) == [("/.gitignore", "xasset1/.gitignore", "v-own")]

    def test_a_selected_file_is_its_own_entry(self, env, monkeypatch):  # noqa: F811
        """Control in the other key order: the plain file's entry comes last."""
        _versions(monkeypatch, [
            {"relativeKey": "//a.glb", "versionId": "v-other", "size": 7},
            {"relativeKey": "a.glb", "versionId": "va1", "size": 100},
        ])
        response = _create(env, {"selection": [{"type": "file", "relativePath": "/a.glb"}], "assetVersionId": "3"})
        assert response["statusCode"] == 200
        assert _pinned(env) == [("/a.glb", "xasset1/a.glb", "va1")]

    def test_control_a_legacy_slashed_entry_is_still_the_file(self, env, monkeypatch):  # noqa: F811
        _versions(monkeypatch, [{"relativeKey": "/a.glb", "versionId": "va1", "size": 100}])
        response = _create(env, {"selection": [{"type": "file", "relativePath": "/a.glb"}], "assetVersionId": "3"})
        assert response["statusCode"] == 200
        assert _pinned(env) == [("/a.glb", "xasset1/a.glb", "va1")]
