# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset version keeps a file whose key has an empty segment after the location apart (#1977, Part C).

In an asset stored at ``a1/``, ``a1/x.txt`` is listed at ``/x.txt`` and ``a1//x.txt`` at ``//x.txt``. #1938 stores
version entries in the one-slash form (``x.txt`` and ``//x.txt``) and compares them with exactly one leading ``/``
removed. The latest-files snapshot still keyed each entry as its path with every leading ``/`` stripped, so an
asset holding both files gave two ``x.txt`` entries and creating a version failed with "Failed to save file
versions" (review D4). The readers that look a file up in a snapshot (the stream route's version pin, a pinned
download, file info's version list and its history key) sent the path stripped the same way, so ``//x.txt``
read ``x.txt``'s entry. Each now stores or sends the one-slash form.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common import downloadEligibility as de
from backend.backend.handlers.assets import assetFiles as af  # noqa: E402
from backend.backend.handlers.assets import assetVersions as av  # noqa: E402
from tests.handlers.assets import test_asset_version_entry_keys as entry_keys  # noqa: E402
from tests.handlers.assets.test_asset_version_entry_keys import (  # noqa: E402,F401
    ASSET, DB, PREFIX, _versions_table, aws,
)

ROWS = [(DB, ASSET, "1", "x.txt", "v-plain"), (DB, ASSET, "1", "//x.txt", "v-empty-segment"),
        (DB, ASSET, "1", "/y.txt", "v-legacy")]


def _listing(*keys):
    return {"Versions": [{"Key": key, "VersionId": f"v-{i}", "IsLatest": True, "Size": 1,
                          "LastModified": MagicMock(isoformat=lambda: "2026-11-26T00:00:00+00:00"), "ETag": '"e"'}
                         for i, key in enumerate(keys)],
            "DeleteMarkers": []}


@pytest.mark.unit
class TestTheLatestFilesSnapshot:
    def test_both_files_are_listed_in_their_stored_form(self):
        with patch.object(av, "list_all_object_versions",
                          return_value=_listing(f"{PREFIX}x.txt", f"{PREFIX}/x.txt", f"{PREFIX}sub//y.txt")):
            files = av.list_s3_files_with_versions("bucket", PREFIX)
        assert [(f["relativeKey"], f["key"]) for f in files] == [
            ("x.txt", f"{PREFIX}x.txt"), ("//x.txt", f"{PREFIX}/x.txt"), ("sub//y.txt", f"{PREFIX}sub//y.txt")]

    def test_a_version_of_both_files_saves_two_rows(self, aws, monkeypatch):
        """D4: the two entries were one sort key, and the snapshot failed."""
        table = _versions_table([])
        monkeypatch.setattr(av, "asset_file_versions_table", table)
        with patch.object(av, "list_all_object_versions",
                          return_value=_listing(f"{PREFIX}/x.txt", f"{PREFIX}x.txt")):
            files = av.list_s3_files_with_versions("bucket", PREFIX)
        assert av.save_asset_file_versions(DB, ASSET, "1", files) is True
        rows = sorted((item["fileKey"], item["versionId"]) for item in table.scan()["Items"])
        assert rows == [("//x.txt", "v-0"), ("x.txt", "v-1")]
        assert {f["key"] for f in files} == {av.object_key_for_path(PREFIX, f["relativeKey"]) for f in files}


@pytest.mark.unit
class TestTheReadersSendThePathTheSnapshotIsSearchedBy:
    @pytest.mark.parametrize("final_key, base_key, expected", [
        (f"{PREFIX}/x.txt", PREFIX, "//x.txt"),
        (f"{PREFIX}x.txt", PREFIX, "/x.txt"),
        ("p/model.glb", "p/model.glb", "/model.glb"),
        ("p/model.glb//x.bin", "p/model.glb", "//x.bin"),
    ])
    def test_a_pinned_download(self, final_key, base_key, expected):
        assert de.asset_relative_key(final_key, base_key) == expected

    @pytest.mark.parametrize("final_key, expected", [
        (f"{PREFIX}/x.txt", "v-empty-segment"), (f"{PREFIX}x.txt", "v-plain"), (f"{PREFIX}y.txt", "v-legacy")])
    def test_a_pinned_download_resolves_its_own_entry(self, aws, monkeypatch, final_key, expected):
        monkeypatch.setattr(av, "asset_file_versions_table", _versions_table(ROWS))
        with patch.object(av, "validate_asset_version_exists"):
            assert av.resolve_file_version_from_asset_version(
                DB, ASSET, "1", de.asset_relative_key(final_key, PREFIX)) == expected

    def test_file_info_lists_the_empty_segment_files_own_versions(self, aws, monkeypatch):
        """'//inner.txt' is in versions 1 and 2; version 2 also holds 'inner.txt' at another S3 version."""
        versions = entry_keys.TestFileInfo()._info(monkeypatch, [
            (DB, ASSET, "1", "//inner.txt", "v-1"), (DB, ASSET, "2", "//inner.txt", "v-2"),
            (DB, ASSET, "2", "inner.txt", "v-9")], file_path="//inner.txt")
        assert versions["v-2"] == (False, [{"id": "2", "label": "v2 (RC1)"}])
        assert versions["v-1"][1] == [{"id": "1", "label": "v1"}]

    def test_file_info_of_the_root_file_leaves_the_other_files_versions_out(self, aws, monkeypatch):
        versions = entry_keys.TestFileInfo()._info(monkeypatch, [
            (DB, ASSET, "1", "inner.txt", "v-1"), (DB, ASSET, "2", "//inner.txt", "v-2")], file_path="/inner.txt")
        assert versions["v-2"] == (True, [])
        assert versions["v-1"][1] == [{"id": "1", "label": "v1"}]

    def test_file_info_reads_the_history_under_the_listed_path(self, aws, monkeypatch):
        seen = []

        def _map(database_id, asset_id):
            class _Map(dict):
                def get(self, key, default=None):
                    seen.append(key)
                    return default
            return _Map()

        monkeypatch.setattr(af, "query_asset_version_history_map", _map)
        info = entry_keys.TestFileInfo()
        original = monkeypatch.setattr
        # _info replaces the history map with an empty dict; keep ours
        with patch.object(monkeypatch, "setattr",
                          side_effect=lambda target, name, value: None if name == "query_asset_version_history_map"
                          else original(target, name, value)):
            info._info(monkeypatch, [(DB, ASSET, "2", "//inner.txt", "v-2")], file_path="//inner.txt")
        assert seen and {path for path, _ in seen} == {"//inner.txt"}
