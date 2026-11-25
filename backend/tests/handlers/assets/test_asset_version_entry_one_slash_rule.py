# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A version entry's leading `/` is the path marker: exactly one is removed, so `//x` and `/x` are two files.

A file's path in the asset starts with one `/`: `/x` is the key `{location}x`, and `//x` the key `{location}/x`,
whose first segment after the location is empty. A version stores `x` for the first (a legacy row from a caller's
file list may hold `/x`) and `//x` for the second. Normalizing with every leading `/` removed read `//x` as `x`,
so the two files shared one version entry, one count and one label. Every place that stores, collapses, counts
or hands out a version entry now removes exactly one. The owner-approved refusals stay: one file named with and
without the slash in one request, and a path made only of `/`. The tables are moto tables with the deployment's
key schema.
"""

from unittest.mock import MagicMock, patch

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.handlers.assets import assetFiles as af  # noqa: E402
from backend.backend.handlers.assets import assetVersions as av  # noqa: E402
from tests.handlers.assets import test_asset_version_entry_keys as entry_keys  # noqa: E402
from tests.handlers.assets.test_asset_version_entry_keys import (  # noqa: E402,F401
    _EXPORT_ASSET, _EXPORT_DB, _EXPORT_PREFIX, ASSET, DB, PREFIX, _versions_table, aws,
)
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
)

# One version holding the two files ('x' is {location}x, '//x' is {location}/x) and a legacy row
ROWS = [(DB, ASSET, "1", "x.txt", "v-plain"), (DB, ASSET, "1", "//x.txt", "v-empty-segment"),
        (DB, ASSET, "1", "/y.txt", "v-legacy")]


def _modules():
    return {"assetVersions": av, "assetFiles": af, "assetExportService": _load_asset_export_service()}


@pytest.mark.unit
class TestTheRule:
    """The three handler modules' copies of the rule agree on one case table."""

    CASES = [("x", "x", "x"), ("/x", "x", "x"), ("//x", "/x", "//x"), ("///x", "//x", "///x"),
             ("a/b", "a/b", "a/b"), ("/a//b", "a//b", "a//b")]

    @pytest.mark.parametrize("module", ["assetVersions", "assetFiles", "assetExportService"])
    def test_exactly_one_leading_slash_is_removed(self, module):
        m = _modules()[module]
        assert [(k, m.version_entry_key(k), m.stored_version_entry_key(k)) for k, _, _ in self.CASES] == self.CASES

    @pytest.mark.parametrize("module", ["assetVersions", "assetFiles", "assetExportService"])
    def test_a_key_made_only_of_slashes_names_no_file(self, module):
        m = _modules()[module]
        assert [k for k in ("", "/", "//", "///", "x", "//x") if m.names_no_file(k)] == ["", "/", "//", "///"]

    @pytest.mark.parametrize("module", ["assetVersions", "assetFiles", "assetExportService"])
    def test_the_collapse_keeps_x_and_double_slash_x_apart(self, module):
        rows = [{"fileKey": k} for k in ("//x", "/x", "x", "//", "/")]
        kept = [row["fileKey"] for row in _modules()[module].collapse_version_entries(rows)]
        assert kept == ["//x", "x"]


@pytest.mark.unit
class TestSaving:
    def test_the_two_files_are_two_rows_and_a_legacy_spelling_is_stored_plain(self, aws, monkeypatch):
        table = _versions_table([])
        monkeypatch.setattr(av, "asset_file_versions_table", table)
        entries = [{"relativeKey": k, "versionId": v} for k, v in (("/x", "v-1"), ("//x", "v-2"), ("///y", "v-3"))]
        assert av.save_asset_file_versions(DB, ASSET, "2", entries) is True
        stored = sorted((i["fileKey"], i["versionId"]) for i in table.scan()["Items"])
        assert stored == [("///y", "v-3"), ("//x", "v-2"), ("x", "v-1")]


@pytest.mark.unit
class TestReading:
    def test_the_version_helper_hands_out_each_file_in_its_stored_form(self, aws, monkeypatch):
        monkeypatch.setattr(av, "asset_file_versions_table", _versions_table(ROWS))
        files = av.get_asset_file_versions(DB, ASSET, "1")["files"]
        assert sorted((f["relativeKey"], f["versionId"]) for f in files) == [
            ("//x.txt", "v-empty-segment"), ("x.txt", "v-plain"), ("y.txt", "v-legacy")]

    def test_the_file_helper_hands_out_each_file_in_its_stored_form(self, aws, monkeypatch):
        monkeypatch.setattr(af, "asset_version_files_table", _versions_table(ROWS))
        files = af.get_asset_file_versions(DB, ASSET, "1", None)["files"]
        assert sorted((f["relativeKey"], f["versionId"]) for f in files) == [
            ("//x.txt", "v-empty-segment"), ("x.txt", "v-plain"), ("y.txt", "v-legacy")]

    @pytest.mark.parametrize("requested, expected", [("x.txt", "v-plain"), ("/x.txt", "v-empty-segment"),
                                                     ("y.txt", "v-legacy")])
    def test_the_single_file_lookup_reads_its_own_row(self, aws, monkeypatch, requested, expected):
        """The lookup takes the key after the location: 'x.txt' for {location}x.txt, '/x.txt' for {location}/x.txt."""
        monkeypatch.setattr(af, "asset_version_files_table", _versions_table(ROWS))
        [entry] = af.get_asset_file_versions(DB, ASSET, "1", requested)["files"]
        assert entry["versionId"] == expected

    def test_the_export_helper_hands_out_each_file_in_its_stored_form(self, aws, monkeypatch):
        m = _load_asset_export_service()
        with patch.object(m, "asset_file_versions_table", _versions_table(ROWS)):
            files = m.get_asset_file_versions(DB, ASSET, "1")["files"]
        assert sorted((f["relativeKey"], f["versionId"]) for f in files) == [
            ("//x.txt", "v-empty-segment"), ("x.txt", "v-plain"), ("y.txt", "v-legacy")]

    def test_a_legacy_slashed_row_still_names_the_plain_key(self, aws, monkeypatch):
        monkeypatch.setattr(av, "asset_file_versions_table", _versions_table(ROWS))
        files = {f["versionId"]: f["relativeKey"] for f in av.get_asset_file_versions(DB, ASSET, "1")["files"]}
        assert av.object_key_for(PREFIX, files["v-legacy"]) == f"{PREFIX}y.txt"

    @pytest.mark.parametrize("requested, expected", [("x.txt", "v-plain"), ("/x.txt", "v-plain"),
                                                     ("//x.txt", "v-empty-segment"), ("/y.txt", "v-legacy")])
    def test_a_pinned_lookup_resolves_its_own_file(self, aws, monkeypatch, requested, expected):
        monkeypatch.setattr(av, "asset_file_versions_table", _versions_table(ROWS))
        with patch.object(av, "validate_asset_version_exists"):
            assert av.resolve_file_version_from_asset_version(DB, ASSET, "1", requested) == expected

    def test_file_info_labels_the_plain_file_with_its_own_rows_only(self, aws, monkeypatch):
        """{location}x.txt is in versions 1 and 2; version 3 holds only {location}/x.txt (stored '//x.txt'),
        whose row names an S3 version id equal to one of x.txt's, so a row read as x.txt's would add v3."""
        versions = entry_keys.TestFileInfo()._info(monkeypatch, [
            (DB, ASSET, "1", "/x.txt", "v-1"), (DB, ASSET, "2", "x.txt", "v-2"),
            (DB, ASSET, "3", "//x.txt", "v-1")], file_path="/x.txt")
        assert versions["v-2"] == (False, [{"id": "2", "label": "v2 (RC1)"}])
        assert versions["v-1"][1] == [{"id": "1", "label": "v1"}]


@pytest.mark.unit
class TestCounting:
    def test_the_per_version_count_counts_the_two_files(self, aws, monkeypatch):
        monkeypatch.setattr(av, "asset_file_versions_table", _versions_table(ROWS + [(DB, ASSET, "1", "/x.txt", "v")]))
        assert av.get_asset_version_file_count(DB, ASSET, "1") == 3

    def test_the_tally_counts_the_two_files(self, aws, monkeypatch):
        monkeypatch.setattr(av, "asset_file_versions_table", _versions_table(ROWS + [(DB, ASSET, "1", "/x.txt", "v")]))
        assert av.tally_asset_version_file_counts(DB, ASSET) == {"1": 3}

    def test_the_shared_count(self):
        assert av.count_version_files(["x", "/x", "//x", "/", "//", "", None]) == 2

    def test_a_revert_counts_the_two_files(self, aws, monkeypatch):
        monkeypatch.setattr(av, "asset_file_versions_table", _versions_table(ROWS))
        request = av.RevertAssetVersionRequestModel(assetVersionId="1", comment="back")
        with patch.object(av, "get_asset_with_permissions", return_value={"assetId": ASSET}), \
                patch.object(av, "get_asset_s3_location", return_value=("bucket", PREFIX)), \
                patch.object(av, "nested_locations_for", return_value=MagicMock(keep=lambda key: True)), \
                patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "1"}), \
                patch.object(av, "reserve_next_asset_version", return_value="2"), \
                patch.object(av, "does_file_version_exist", return_value=True), \
                patch.object(av, "copy_s3_object_version", side_effect=lambda sb, sk, sv, db, dk: f"new-{sv}"), \
                patch.object(av, "delete_assetAuxiliary_files"), \
                patch.object(av, "save_asset_metadata_version", return_value=True), \
                patch.object(av, "stamp_asset_version_provenance"), \
                patch.object(av, "finalize_asset_version"), \
                patch.object(av, "send_subscription_email"):
            response = av.revert_asset_version(DB, ASSET, request, {"tokens": ["alice"]})
        assert response.message == "Successfully reverted to version 1 with 3 files"


@pytest.mark.unit
class TestTheRequestRules:
    def _model(self, *keys):
        return av.CreateAssetVersionRequestModel(
            comment="c", files=[{"relativeKey": k, "versionId": f"v-{i}"} for i, k in enumerate(keys)])

    def test_the_two_files_in_one_request_are_accepted(self):
        assert [f.relativeKey for f in self._model("/x", "//x").files] == ["/x", "//x"]

    @pytest.mark.parametrize("keys", [("x", "/x"), ("//x", "//x")])
    def test_one_file_named_twice_is_still_refused(self, keys):
        with pytest.raises(ValidationError, match="Duplicate relative keys are not allowed"):
            self._model(*keys)

    @pytest.mark.parametrize("key", ["/", "//", "///"])
    def test_a_path_made_only_of_slashes_is_still_refused(self, key):
        with pytest.raises(ValidationError, match="relativeKey must name a file"):
            self._model(key)
