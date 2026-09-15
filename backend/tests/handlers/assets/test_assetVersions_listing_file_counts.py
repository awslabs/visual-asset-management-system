# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Listing asset versions costs one file-count read for the page, not one per version.

`get_asset_versions` read a page of up to `pageSize` version records and then called
`get_asset_version_file_count` for every one of them, each a separate `Select='COUNT'` query against
the asset-file-versions table. Listing a heavily versioned asset therefore performed 1 query plus one
per version, and the added serial round trips grow linearly with the asset's version count.

The version each file row belongs to is carried in the row's own partition key, and the
`databaseIdAssetIdIndex` GSI is keyed on `databaseId:assetId`, so ONE walk of that index tallies
every version's count. That index spans every version's files though, which on an asset holding many
files per version is far more data than the listed page needs -- so the walk is bounded and the
per-version counts remain the fallback past the bound.

The load-bearing assertion is the READ COUNT: `fileCount` was already correct before, so asserting
only the counts proves nothing. `TestFileCountsAreStillCorrect` is the control that keeps the read
count from being satisfied by a listing that reports zero files for everything.
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

# Version id -> how many file rows that version snapshotted.
FILES_PER_VERSION = {"1": 3, "2": 1, "3": 4, "4": 2, "5": 2}


@pytest.fixture
def av():
    return _assetVersions


def _typed_version(version_id):
    """A version record in the typed shape the low-level client returns."""
    return {
        "databaseId": {"S": DB},
        "databaseId:assetId": {"S": PK},
        "assetId": {"S": ASSET},
        "assetVersionId": {"S": version_id},
        "dateCreated": {"S": f"2026-01-0{version_id}T00:00:00"},
        "comment": {"S": f"c{version_id}"},
        "description": {"S": "d"},
        "createdBy": {"S": "alice"},
        "isCurrentVersion": {"BOOL": version_id == "5"},
        "isArchived": {"BOOL": False},
        "versionAlias": {"S": ""},
    }


def _gsi_rows():
    """Every file-version row of the asset, as the GSI returns them."""
    return [
        {"databaseId:assetId:assetVersionId": f"{PK}:{version_id}"}
        for version_id, count in FILES_PER_VERSION.items()
        for _ in range(count)
    ]


class FileVersionsTableStub:
    """`asset_file_versions_table` stub that separates GSI reads from per-version COUNT reads."""

    def __init__(self, gsi_pages):
        self.gsi_pages = list(gsi_pages)
        self.gsi_calls = []
        self.count_calls = []

    def query(self, **kwargs):
        if kwargs.get("IndexName"):
            self.gsi_calls.append(kwargs)
            page_index = min(len(self.gsi_calls) - 1, len(self.gsi_pages) - 1)
            return self.gsi_pages[page_index]
        self.count_calls.append(kwargs)
        return {"Count": 0}


def _list_versions(av, file_versions_table, version_ids=("5", "4", "3", "2", "1")):
    """Run get_asset_versions over one page of version records."""
    client = MagicMock()
    client.query.return_value = {"Items": [_typed_version(v) for v in version_ids]}

    with patch.object(av, "get_asset_with_permissions", return_value={
                "databaseId": DB, "assetId": ASSET, "assetName": "N"}), \
            patch.object(av, "dynamodb_client", client), \
            patch.object(av, "asset_file_versions_table", file_versions_table):
        return av.get_asset_versions(DB, ASSET, {}, {"tokens": ["alice"]})


@pytest.mark.unit
class TestVersionListingDoesNotQueryPerVersion:
    """The N+1 itself: a page of 5 versions must not cost 5 file-count queries."""

    def test_no_per_version_count_query_is_issued(self, av):
        table = FileVersionsTableStub([{"Items": _gsi_rows()}])
        _list_versions(av, table)

        assert table.count_calls == [], (
            f"the listing issued {len(table.count_calls)} per-version file-count queries, so its "
            "cost grows with the asset's version count")

    def test_the_tally_is_one_read_for_the_whole_page(self, av):
        table = FileVersionsTableStub([{"Items": _gsi_rows()}])
        _list_versions(av, table)

        assert len(table.gsi_calls) == 1, (
            f"the file-count tally took {len(table.gsi_calls)} reads for a set that fits in one page")

    def test_the_read_count_does_not_grow_with_the_version_count(self, av):
        """Two page sizes, one read budget: the distinguishing property of the fix."""
        small = FileVersionsTableStub([{"Items": _gsi_rows()}])
        _list_versions(av, small, version_ids=("1",))
        large = FileVersionsTableStub([{"Items": _gsi_rows()}])
        _list_versions(av, large, version_ids=("5", "4", "3", "2", "1"))

        small_reads = len(small.gsi_calls) + len(small.count_calls)
        large_reads = len(large.gsi_calls) + len(large.count_calls)
        assert large_reads == small_reads, (
            f"listing 5 versions cost {large_reads} reads against {small_reads} for one version")


@pytest.mark.unit
class TestFileCountsAreStillCorrect:
    """Control. The read-count assertions above are satisfied equally by a listing that reports
    zero files for every version, which is what makes this the load-bearing half."""

    def test_every_version_reports_its_own_file_count(self, av):
        table = FileVersionsTableStub([{"Items": _gsi_rows()}])
        response = _list_versions(av, table)

        counts = {item.Version: item.fileCount for item in response.versions}
        assert counts == FILES_PER_VERSION

    def test_a_version_with_no_file_rows_reports_zero(self, av):
        table = FileVersionsTableStub([{"Items": _gsi_rows()}])
        response = _list_versions(av, table, version_ids=("9", "5"))

        counts = {item.Version: item.fileCount for item in response.versions}
        assert counts == {"9": 0, "5": FILES_PER_VERSION["5"]}

    def test_rows_of_another_asset_are_not_counted(self, av):
        """The GSI is keyed on databaseId:assetId, so a foreign row cannot appear -- but the
        version id is parsed out of a composite key, and a prefix mismatch must be ignored
        rather than counted under a mangled id."""
        rows = _gsi_rows() + [{"databaseId:assetId:assetVersionId": "db1:asset-2:5"}]
        table = FileVersionsTableStub([{"Items": rows}])
        response = _list_versions(av, table)

        counts = {item.Version: item.fileCount for item in response.versions}
        assert counts == FILES_PER_VERSION


@pytest.mark.unit
class TestTallyFallsBackWhenTheWalkIsTooLarge:
    """An asset holding many files per version must not turn the listing into a full index walk."""

    def test_the_walk_is_bounded_and_the_counts_come_from_the_per_version_queries(self, av):
        # Every GSI page carries a LastEvaluatedKey, so the walk never completes.
        endless_page = {
            "Items": _gsi_rows(),
            "LastEvaluatedKey": {"databaseId:assetId:assetVersionId": f"{PK}:1", "fileKey": "/f"},
        }
        table = FileVersionsTableStub([endless_page])
        per_version = MagicMock(return_value=7)

        with patch.object(av, "get_asset_version_file_count", per_version):
            response = _list_versions(av, table)

        assert len(table.gsi_calls) == av.FILE_COUNT_TALLY_MAX_PAGES, (
            f"the tally walked {len(table.gsi_calls)} pages against a bound of "
            f"{av.FILE_COUNT_TALLY_MAX_PAGES}")
        # A LOWER bound: the claim is that the fallback ran for every version, so an extra
        # defensive call is not the failure -- a missing one is.
        assert per_version.call_count >= 5, (
            "the abandoned tally did not fall back to the per-version counts, so the listing "
            "would report a partial count")
        assert {item.fileCount for item in response.versions} == {7}

    def test_a_partial_tally_is_never_used_for_a_count(self, av):
        """A partial walk holds real counts for some versions and short ones for others; using it
        would report a number that is wrong without being obviously wrong."""
        endless_page = {
            "Items": [{"databaseId:assetId:assetVersionId": f"{PK}:5"}],
            "LastEvaluatedKey": {"databaseId:assetId:assetVersionId": f"{PK}:5", "fileKey": "/f"},
        }
        table = FileVersionsTableStub([endless_page])

        with patch.object(av, "get_asset_version_file_count", MagicMock(return_value=11)):
            response = _list_versions(av, table)

        counts = {item.Version: item.fileCount for item in response.versions}
        assert counts == {v: 11 for v in ("5", "4", "3", "2", "1")}, (
            f"a version's count came from the abandoned partial tally; got {counts}")


@pytest.mark.unit
class TestFileCountQueryPagesToExhaustion:
    """`Select='COUNT'` is bounded by the same 1 MB scan limit as any other query."""

    def test_the_counts_of_every_page_are_summed(self, av):
        pager = Pager(
            {"Count": 900, "LastEvaluatedKey": {
                "databaseId:assetId:assetVersionId": f"{PK}:5", "fileKey": "/f900"}},
            {"Count": 40},
            name="assetVersions.get_asset_version_file_count",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch.object(av, "asset_file_versions_table", table):
            count = av.get_asset_version_file_count(DB, ASSET, "5")

        assert count == 940, (
            f"the file count stopped at the first page and reported {count} of 940")
        pager.assert_paged_to_exhaustion()

    def test_a_single_page_count_is_returned_unchanged(self, av):
        """Positive control: the common case must not double-count or loop."""
        table = MagicMock()
        table.query.return_value = {"Count": 3}

        with patch.object(av, "asset_file_versions_table", table):
            assert av.get_asset_version_file_count(DB, ASSET, "5") == 3
        assert table.query.called, 'no read happened'
        assert table.query.call_count <= 1, 'a single version count needs one read'
