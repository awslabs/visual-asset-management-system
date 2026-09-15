# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`get_all_asset_versions` reads the complete version set, and one odd record does not empty it.

The helper issued a single un-paginated `asset_versions_table.query`, so once an asset's version
records outgrew one 1 MB page the tail was invisible: `resolve_asset_version_id_from_alias` reported
a real alias as missing, `assetFiles.get_file_info` omitted archived versions from its filter, and
`mark_assetVersion_as_current` left `isCurrentVersion` set on versions it never saw.

It also sorted on `int(x.get('assetVersionId', '0').replace('v', ''))`, which raises on any id that
is not a number, and the enclosing bare `except Exception` turned that into an empty list -- so ONE
odd record made every caller behave as though the asset had no versions at all. `mark_assetVersion_as_current`
separately subscripted `version['isCurrentVersion']`, so a record written without that attribute
raised `KeyError`, was swallowed, and left the new version never flagged as current while the
function's `False` return went unchecked.

The load-bearing assertions are that the LATER page's versions are returned and that the WELL-FORMED
records survive an odd sibling. "It did not raise" is satisfied equally by the empty list the defect
produced.
"""

import pytest
from unittest.mock import MagicMock, patch

# Module-scope import so the real `backend.backend.handlers` package is in sys.modules before the
# root conftest's autouse fixture installs its non-package placeholder (see S27-TEST-001).
from backend.backend.handlers.assets import assetVersions as _assetVersions
from backend.tests.pagingStub import Pager

DB = "db1"
ASSET = "asset-1"
PK = f"{DB}:{ASSET}"


@pytest.fixture
def av():
    return _assetVersions


def _version(version_id, alias="", archived=False, is_current=False):
    return {
        "databaseId": DB, "databaseId:assetId": PK, "assetId": ASSET,
        "assetVersionId": version_id, "dateCreated": "2026-01-01T00:00:00",
        "comment": f"c{version_id}", "description": "d", "createdBy": "alice",
        "isCurrentVersion": is_current, "isArchived": archived, "versionAlias": alias,
    }


def _two_page_pager():
    """Versions 5 and 4 on page one, 3 (aliased RC1) on page two."""
    return Pager(
        {"Items": [_version("5"), _version("4")],
         "LastEvaluatedKey": {"databaseId:assetId": PK, "assetVersionId": "4"}},
        {"Items": [_version("3", alias="RC1", archived=True)]},
        name="assetVersions.get_all_asset_versions",
    )


@pytest.mark.unit
class TestGetAllAssetVersionsPagesToExhaustion:
    """The tail of the version set is what every caller of this helper depends on."""

    def _run(self, av, pager):
        table = MagicMock()
        table.query.side_effect = pager
        with patch.object(av, "asset_versions_table", table):
            return av.get_all_asset_versions(DB, ASSET)

    def test_the_later_pages_versions_are_returned(self, av):
        pager = _two_page_pager()
        versions = self._run(av, pager)

        assert [v["assetVersionId"] for v in versions] == ["5", "4", "3"], (
            "the version set was truncated to the first page, so a version that exists reads as "
            f"absent; got {[v.get('assetVersionId') for v in versions]}")

    def test_every_page_cursor_is_resumed_from(self, av):
        pager = _two_page_pager()
        self._run(av, pager)
        # Asserted over the SET of cursors rather than over a read count, so an extra read passes.
        pager.assert_paged_to_exhaustion()

    def test_an_alias_on_a_later_page_still_resolves(self, av):
        """The consequence a user reaches: download/stream by assetVersionIdAlias."""
        pager = _two_page_pager()
        table = MagicMock()
        table.query.side_effect = pager
        with patch.object(av, "asset_versions_table", table):
            resolved = av.resolve_asset_version_id_from_alias(DB, ASSET, "RC1")

        assert resolved == "3"


@pytest.mark.unit
class TestOneOddRecordDoesNotEmptyTheSet:
    """A version id that is not a number must cost only its own position in the ordering."""

    def _run(self, av, items):
        table = MagicMock()
        table.query.return_value = {"Items": items}
        with patch.object(av, "asset_versions_table", table):
            return av.get_all_asset_versions(DB, ASSET)

    def test_the_numeric_versions_are_still_returned(self, av):
        """The distinguishing assertion: the sort raised, the bare except returned [], and every
        caller then behaved as though the asset had no versions."""
        versions = self._run(av, [_version("2"), _version("rc-1"), _version("1")])

        ids = [v["assetVersionId"] for v in versions]
        assert "2" in ids and "1" in ids, (
            f"one non-numeric version id discarded the whole version set; got {ids}")

    def test_the_odd_record_is_kept_and_ordered_last(self, av):
        """Dropping it would hide a record an operator has to be able to see."""
        versions = self._run(av, [_version("2"), _version("rc-1"), _version("1")])

        ids = [v["assetVersionId"] for v in versions]
        assert ids == ["2", "1", "rc-1"]


@pytest.mark.unit
class TestMarkAssetVersionAsCurrent:
    """The flag write must survive a record that predates the flag, and report a miss."""

    def _run(self, av, versions, new_version_id):
        table = MagicMock()
        logger = MagicMock()
        with patch.object(av, "asset_versions_table", table), \
                patch.object(av, "logger", logger), \
                patch.object(av, "get_all_asset_versions", return_value=versions):
            result = av.mark_assetVersion_as_current(DB, ASSET, new_version_id)
        return result, table, logger

    @staticmethod
    def _flag_updates(table):
        """assetVersionId -> the isCurrentVersion value written for it."""
        return {
            call.kwargs["Key"]["assetVersionId"]:
                call.kwargs["ExpressionAttributeValues"][":is_current"]
            for call in table.update_item.call_args_list
        }

    def test_a_record_without_the_flag_does_not_block_the_new_current(self, av):
        """The legacy record is listed FIRST, so the KeyError it raised aborted the loop before
        the target version was ever reached."""
        legacy = _version("9")
        del legacy["isCurrentVersion"]
        target = _version("8", is_current=False)

        result, table, _logger = self._run(av, [legacy, target], "8")

        assert self._flag_updates(table).get("8") is True, (
            "the version being made current was never flagged, because a sibling record written "
            "without isCurrentVersion aborted the loop")
        assert result is True

    def test_a_record_without_the_flag_is_not_rewritten(self, av):
        """It already reads as not current, so there is nothing to write."""
        legacy = _version("9")
        del legacy["isCurrentVersion"]
        result, table, _logger = self._run(av, [legacy, _version("8")], "8")

        assert "9" not in self._flag_updates(table)
        assert result is True

    def test_a_target_absent_from_the_listing_is_still_flagged_current(self, av):
        """The listing is eventually consistent, so it can miss a version record written moments
        earlier. Leaving that version unflagged makes the version list report one version as
        current while the asset record points at another, permanently and with a 200 returned."""
        result, table, _logger = self._run(av, [_version("5", is_current=True)], "6")

        assert self._flag_updates(table).get("6") is True, (
            "the asset's new current version was left flagged not-current because the eventually "
            "consistent version listing did not yet carry its record")
        assert result is False, (
            "the incomplete listing must still be reported, since the previously current "
            "version's flag could not be relied on to have been cleared")

    def test_a_missing_target_version_is_reported_not_silently_accepted(self, av):
        """get_all_asset_versions collapses a transient read failure to [], which made this a
        no-op that still returned True to a caller that never checked it."""
        result, _table, logger = self._run(av, [], "9")

        assert result is False, (
            "no version was flagged as current and the call still reported success")
        assert logger.error.called, (
            "the anomaly was not logged at error level, so it is invisible to an operator")


@pytest.mark.unit
class TestLegitimateCasesStillWork:
    """Positive control. Without it, a helper that returns nothing at all passes every
    assertion above about odd records and missing flags."""

    def test_a_single_page_is_returned_newest_first(self, av):
        table = MagicMock()
        table.query.return_value = {"Items": [_version("1"), _version("3"), _version("2")]}
        with patch.object(av, "asset_versions_table", table):
            versions = av.get_all_asset_versions(DB, ASSET)

        assert [v["assetVersionId"] for v in versions] == ["3", "2", "1"]

    def test_a_v_prefixed_id_still_sorts_by_its_number(self, av):
        table = MagicMock()
        table.query.return_value = {"Items": [_version("v1"), _version("v10"), _version("v2")]}
        with patch.object(av, "asset_versions_table", table):
            versions = av.get_all_asset_versions(DB, ASSET)

        assert [v["assetVersionId"] for v in versions] == ["v10", "v2", "v1"]

    def test_only_the_two_records_that_change_are_written(self, av):
        table = MagicMock()
        with patch.object(av, "asset_versions_table", table), \
                patch.object(av, "get_all_asset_versions", return_value=[
                    _version("3", is_current=False),
                    _version("2", is_current=True),
                    _version("1", is_current=False),
                ]):
            result = av.mark_assetVersion_as_current(DB, ASSET, "3")

        updates = TestMarkAssetVersionAsCurrent._flag_updates(table)
        assert updates == {"3": True, "2": False}
        assert result is True

    def test_an_unambiguous_alias_on_the_first_page_still_resolves(self, av):
        table = MagicMock()
        table.query.return_value = {"Items": [_version("2", alias="RC2"), _version("1")]}
        with patch.object(av, "asset_versions_table", table):
            assert av.resolve_asset_version_id_from_alias(DB, ASSET, "RC2") == "2"
