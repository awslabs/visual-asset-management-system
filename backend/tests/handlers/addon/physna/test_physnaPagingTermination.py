# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Physna DynamoDB pagers must page on key PRESENCE and must be bounded.

Three readers drove their loop from the *value* of ``LastEvaluatedKey``
(``last_evaluated = response.get(...)``; ``if not last_evaluated: break``):
``physnaAssetSync._query_asset_index_all_pages``,
``physnaAssetSync._list_vams_file_paths`` and
``physnaCommon._query_metadata_index_all_pages``. Two consequences:

* ``MagicMock.get()`` answers every key with a truthy child, so a reader stubbed with
  a bare mock never terminates on the value form. ``in`` is answered False, so the
  presence form does. That is not only a test-harness property -- omitting the key
  entirely on the final page is DynamoDB's actual contract.
* ``_list_vams_file_paths`` had no page bound at all, while the sibling reader of the
  same table, GSI and key stops at ``_ASSET_INDEX_QUERY_MAX_PAGES``.

The pages come from the shared ``tests/pagingStub`` helpers, which serve on
``ExclusiveStartKey`` rather than on call order -- so an extra or repeated read does not fail
these tests -- and refuse to serve more reads than the case needs, so a loop that fails to
advance fails with a message instead of hanging. The one local stub is the runaway reader
below, which no shared helper expresses: it must keep offering pages past the loop's OWN page
cap for the bound to be provable.
"""

from unittest.mock import MagicMock

import pytest

# Module-level imports ensure the real `backend.backend.handlers` package is populated
# in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.addon.physna import physnaAssetSync as _pas  # noqa: F401
from backend.backend.handlers.addon.physna import physnaCommon as _pc  # noqa: F401
from backend.tests.pagingStub import BareMockReader, Pager

DB = "db-1"
ASSET = "asset-1"
COMPOSITE = f"{DB}:{ASSET}"


def _meta_row(relative, key, value="v"):
    return {
        "databaseId:assetId:filePath": f"{DB}:{ASSET}:{relative}",
        "metadataKey": key,
        "metadataValue": value,
        "metadataValueType": "string",
    }


def _three_pages(name):
    """Rows split across three pages, the last one carrying no cursor."""
    return Pager(
        {"Items": [_meta_row("/a.step", "k1")], "LastEvaluatedKey": {"cursor": "page1"}},
        {"Items": [_meta_row("/b.stl", "k2")], "LastEvaluatedKey": {"cursor": "page2"}},
        {"Items": [_meta_row("/c.iges", "k3")]},
        name=name,
    )


@pytest.mark.unit
class TestAssetIndexReader:
    """``physnaAssetSync._query_asset_index_all_pages``."""

    def test_rows_from_the_last_page_are_returned(self):
        from backend.backend.handlers.addon.physna import physnaAssetSync

        table = MagicMock()
        pager = _three_pages("asset index query")
        table.query.side_effect = pager

        items = physnaAssetSync._query_asset_index_all_pages(table, COMPOSITE)

        assert {item["metadataKey"] for item in items} == {"k1", "k2", "k3"}
        # Every cursor the pager handed out was sent back, so the final page was reached.
        pager.assert_paged_to_exhaustion()

    def test_a_single_page_terminates(self):
        """Control: the terminating case must not re-read page 1 forever.

        A response that omits ``LastEvaluatedKey`` ends the walk, so no read carries a
        cursor -- that, and the walk returning at all, is what this case proves. Asserted on
        cursor PRESENCE rather than on a read count or an exact cursor sequence: the stub's
        own ``max_reads`` bound already turns a walk that does not terminate into a failure,
        so a count here would restate the harness, and it would also fail a strictly safer
        reader that took one extra cursor-free read.
        """
        from backend.backend.handlers.addon.physna import physnaAssetSync

        table = MagicMock()
        pager = Pager(
            {"Items": [_meta_row("/a.step", "only")]},
            name="asset index query",
            max_reads=2,
        )
        table.query.side_effect = pager

        items = physnaAssetSync._query_asset_index_all_pages(table, COMPOSITE)

        assert [item["metadataKey"] for item in items] == ["only"]
        assert pager.calls, "the reader issued no query, so the assertion below is vacuous"
        assert pager.resumed_from == [], (
            f"a walk over a single page must send no ExclusiveStartKey; it resumed from "
            f"{pager.resumed_from!r}"
        )

    def test_a_bare_mock_response_terminates(self):
        from backend.backend.handlers.addon.physna import physnaAssetSync

        table = MagicMock()
        reader = BareMockReader(name="asset index query", max_reads=2)
        table.query.side_effect = reader

        items = physnaAssetSync._query_asset_index_all_pages(table, COMPOSITE)

        # A bare mock yields no Items, so the walk ending is the whole result. The reader
        # raises past its cap, which is what makes "it ended" an assertion rather than a hang.
        assert items == []
        assert reader.calls, "the reader was never consulted"


@pytest.mark.unit
class TestVamsFilePathListing:
    """``physnaAssetSync._list_vams_file_paths`` -- the reader that had no bound."""

    def test_paths_from_the_last_page_are_returned(self, monkeypatch):
        from backend.backend.handlers.addon.physna import physnaAssetSync, physnaCommon

        table = MagicMock()
        pager = _three_pages("VAMS file path listing")
        table.query.side_effect = pager
        monkeypatch.setattr(physnaCommon, "asset_file_metadata_table", table)

        paths = physnaAssetSync._list_vams_file_paths(DB, ASSET)

        assert paths == {"/a.step", "/b.stl", "/c.iges"}
        pager.assert_paged_to_exhaustion()

    def test_the_asset_level_row_is_excluded(self, monkeypatch):
        """Control: proves the filtering above is filtering, not a paging artefact."""
        from backend.backend.handlers.addon.physna import physnaAssetSync, physnaCommon

        table = MagicMock()
        pager = Pager(
            {"Items": [_meta_row("/", "assetLevel")], "LastEvaluatedKey": {"cursor": "p1"}},
            {"Items": [_meta_row("/a.step", "k1")]},
            name="VAMS file path listing",
        )
        table.query.side_effect = pager
        monkeypatch.setattr(physnaCommon, "asset_file_metadata_table", table)

        assert physnaAssetSync._list_vams_file_paths(DB, ASSET) == {"/a.step"}

    def test_a_bare_mock_response_terminates(self, monkeypatch):
        from backend.backend.handlers.addon.physna import physnaAssetSync, physnaCommon

        table = MagicMock()
        reader = BareMockReader(name="VAMS file path listing", max_reads=2)
        table.query.side_effect = reader
        monkeypatch.setattr(physnaCommon, "asset_file_metadata_table", table)

        paths = physnaAssetSync._list_vams_file_paths(DB, ASSET)

        assert paths == set()
        assert reader.calls, "the reader was never consulted"

    def test_the_read_is_bounded_when_the_cursor_never_clears(self, monkeypatch):
        """A cursor that always advances must still stop at the page cap.

        The listing is consumed as a lower bound -- it selects which files get a
        metadata PATCH and seeds upload candidates, while the delete decision is
        taken against the S3 version state -- so stopping early defers work rather
        than presenting a file as absent from VAMS.

        The reader is local rather than a shared ``Pager`` because the bound can only fail if
        the stub is willing to serve MORE reads than the loop's own cap allows; a stub capped
        below it trips first and the assertion becomes unfailable.
        """
        from backend.backend.handlers.addon.physna import physnaAssetSync, physnaCommon

        cap = physnaAssetSync._ASSET_INDEX_QUERY_MAX_PAGES
        runaway = cap * 5
        calls = {"n": 0}

        def _never_ending(**_kwargs):
            calls["n"] += 1
            if calls["n"] > runaway:
                raise AssertionError(
                    f"the listing issued more than {runaway} queries; its paging "
                    f"loop has no bound"
                )
            return {
                "Items": [_meta_row(f"/f{calls['n']}.step", "k")],
                "LastEvaluatedKey": {"cursor": calls["n"]},
            }

        table = MagicMock()
        table.query.side_effect = _never_ending
        monkeypatch.setattr(physnaCommon, "asset_file_metadata_table", table)

        paths = physnaAssetSync._list_vams_file_paths(DB, ASSET)

        assert calls["n"] <= cap, f"the page cap did not stop the loop; {calls['n']} reads"
        assert paths, "the pages that were read must still be returned"


@pytest.mark.unit
class TestMetadataIndexReader:
    """``physnaCommon._query_metadata_index_all_pages``."""

    def test_rows_from_the_last_page_are_returned(self):
        from backend.backend.handlers.addon.physna import physnaCommon

        table = MagicMock()
        pager = _three_pages("metadata index query")
        table.query.side_effect = pager

        items = physnaCommon._query_metadata_index_all_pages(table, f"{COMPOSITE}:/a.step")

        assert {item["metadataKey"] for item in items} == {"k1", "k2", "k3"}
        pager.assert_paged_to_exhaustion()

    def test_a_bare_mock_response_terminates(self):
        from backend.backend.handlers.addon.physna import physnaCommon

        table = MagicMock()
        reader = BareMockReader(name="metadata index query", max_reads=2)
        table.query.side_effect = reader

        items = physnaCommon._query_metadata_index_all_pages(table, f"{COMPOSITE}:/a.step")

        assert items == []
        assert reader.calls, "the reader was never consulted"
