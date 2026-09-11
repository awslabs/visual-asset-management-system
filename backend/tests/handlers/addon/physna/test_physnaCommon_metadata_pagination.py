# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-014: Physna metadata reads must page the DynamoDB query to exhaustion.

``get_asset_metadata`` and ``get_file_metadata`` issue exactly one ``query`` against
``DatabaseIdAssetIdFilePathIndex`` with no ``LastEvaluatedKey`` loop, so any asset or
file whose metadata exceeds one 1 MB DynamoDB page syncs a silent subset to Physna --
no error, no log, the Physna record simply misses fields. The sibling loop already
exists in ``physnaAssetSync._list_vams_file_paths``.

The controls pin the three ways a paging fix goes wrong: hoisting one cursor across
``get_file_metadata``'s two independent table queries (which terminates early or
re-queries the wrong table), filtering only the first page's items (which
reintroduces ``REINDEX_METADATA_RECORD`` rows from page 2 into the Physna payload),
and looping without a bound inside a per-file sync loop, where a real asset has
thousands of files.
"""

from unittest.mock import MagicMock

import pytest

# Module-level import ensures the real `backend.backend.handlers` package is populated
# in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.addon.physna import physnaCommon as _pc  # noqa: F401

# Guards against a runaway loop: the stub raises once the call count is implausible
# for any real page size, so an unbounded loop surfaces as a failure, not a hang.
_RUNAWAY_CALL_LIMIT = 500


def _meta_item(key, value="v", value_type="string"):
    return {"metadataKey": key, "metadataValue": value, "metadataValueType": value_type}


def _attr_item(key, value="v", value_type="string"):
    return {"attributeKey": key, "attributeValue": value, "attributeValueType": value_type}


def _pages(*pages):
    """Turn item lists into query responses, chaining LastEvaluatedKey between them."""
    responses = []
    for index, items in enumerate(pages):
        response = {"Items": list(items)}
        if index < len(pages) - 1:
            response["LastEvaluatedKey"] = {"cursor": f"page{index + 1}"}
        responses.append(response)
    return responses


@pytest.fixture
def physna(monkeypatch):
    from backend.backend.handlers.addon.physna import physnaCommon as pc
    metadata_table = MagicMock()
    attribute_table = MagicMock()
    monkeypatch.setattr(pc, "asset_file_metadata_table", metadata_table)
    monkeypatch.setattr(pc, "file_attribute_table", attribute_table)
    return pc, metadata_table, attribute_table


@pytest.mark.unit
class TestGetAssetMetadataPagination:
    """FIX-014 -- asset-level metadata."""

    def test_returns_keys_from_every_page(self, physna):
        """FIX-014: asset metadata beyond the first page must reach the Physna payload."""
        pc, metadata_table, _ = physna
        metadata_table.query.side_effect = _pages(
            [_meta_item("onPage1")],
            [_meta_item("onPage2")],
        )

        result = pc.get_asset_metadata("db1", "a1")

        assert sorted(result.keys()) == ["onPage1", "onPage2"]

    def test_second_query_carries_the_previous_last_evaluated_key(self, physna):
        """FIX-014: the cursor must be threaded, not re-issued from the start."""
        pc, metadata_table, _ = physna
        metadata_table.query.side_effect = _pages(
            [_meta_item("onPage1")],
            [_meta_item("onPage2")],
        )

        pc.get_asset_metadata("db1", "a1")

        assert metadata_table.query.call_count == 2
        assert metadata_table.query.call_args_list[1].kwargs.get("ExclusiveStartKey") == {
            "cursor": "page1"
        }

    def test_single_page_issues_exactly_one_query(self, physna):
        """Control: the loop must terminate and must not re-read page 1.

        A fix that loops on a stale cursor passes the multi-page tests while
        double-reading every single-page asset.
        """
        pc, metadata_table, _ = physna
        metadata_table.query.side_effect = _pages([_meta_item("only")])

        result = pc.get_asset_metadata("db1", "a1")

        assert list(result.keys()) == ["only"]
        assert metadata_table.query.call_count == 1

    def test_loop_is_bounded_when_the_cursor_never_clears(self, physna):
        """Control: a per-file sync loop must not page without a bound.

        The stub returns a LastEvaluatedKey forever. A bounded implementation stops;
        an unbounded one trips the runaway limit and fails here instead of exhausting
        the sync Lambda's duration in production.
        """
        pc, metadata_table, _ = physna
        calls = {"n": 0}

        def _never_ending(**kwargs):
            calls["n"] += 1
            if calls["n"] > _RUNAWAY_CALL_LIMIT:
                raise RuntimeError(
                    f"get_asset_metadata issued more than {_RUNAWAY_CALL_LIMIT} queries; "
                    f"the paging loop has no cap"
                )
            return {"Items": [_meta_item(f"k{calls['n']}")],
                    "LastEvaluatedKey": {"cursor": calls["n"]}}

        metadata_table.query.side_effect = _never_ending

        pc.get_asset_metadata("db1", "a1")

        assert calls["n"] <= _RUNAWAY_CALL_LIMIT


@pytest.mark.unit
class TestGetFileMetadataPagination:
    """FIX-014 -- per-file metadata AND attributes, which paginate independently."""

    def test_metadata_pages_while_attributes_do_not(self, physna):
        """FIX-014: two pages of metadata, one of attributes -- both complete.

        A shared cursor hoisted across the two loops fails this while passing a
        same-shape test where both tables have the same page count.
        """
        pc, metadata_table, attribute_table = physna
        metadata_table.query.side_effect = _pages(
            [_meta_item("m1")], [_meta_item("m2")])
        attribute_table.query.side_effect = _pages([_attr_item("a1")])

        metadata, attributes = pc.get_file_metadata("db1", "a1", "/f.glb")

        assert sorted(metadata.keys()) == ["m1", "m2"]
        assert sorted(attributes.keys()) == ["a1"]
        assert metadata_table.query.call_count == 2
        assert attribute_table.query.call_count == 1

    def test_attributes_page_while_metadata_does_not(self, physna):
        """FIX-014: the reverse split -- one page of metadata, two of attributes."""
        pc, metadata_table, attribute_table = physna
        metadata_table.query.side_effect = _pages([_meta_item("m1")])
        attribute_table.query.side_effect = _pages(
            [_attr_item("a1")], [_attr_item("a2")])

        metadata, attributes = pc.get_file_metadata("db1", "a1", "/f.glb")

        assert sorted(metadata.keys()) == ["m1"]
        assert sorted(attributes.keys()) == ["a1", "a2"]
        assert metadata_table.query.call_count == 1
        assert attribute_table.query.call_count == 2

    def test_single_page_each_issues_one_query_each(self, physna):
        """Control: the terminating case for both tables."""
        pc, metadata_table, attribute_table = physna
        metadata_table.query.side_effect = _pages([_meta_item("m1")])
        attribute_table.query.side_effect = _pages([_attr_item("a1")])

        metadata, attributes = pc.get_file_metadata("db1", "a1", "/f.glb")

        assert list(metadata.keys()) == ["m1"]
        assert list(attributes.keys()) == ["a1"]
        assert metadata_table.query.call_count == 1
        assert attribute_table.query.call_count == 1


@pytest.mark.unit
class TestRowFiltersRunOnEveryPage:
    """FIX-014 -- the row filters must run per page, not only on page 1."""

    def test_excluded_and_empty_rows_on_page_one_are_filtered(self, physna):
        """Control: proves the assertion below is about filtering, not about page 2.

        Passes today, so a page-2 filtering failure cannot be confused with the
        filter itself being broken.
        """
        pc, metadata_table, _ = physna
        from backend.backend.common.dynamoDbMetadataKeys import REINDEX_METADATA_RECORD_KEY
        metadata_table.query.side_effect = _pages([
            _meta_item("keepme"),
            _meta_item(REINDEX_METADATA_RECORD_KEY),
            _meta_item("blankvalue", value=""),
        ])

        result = pc.get_asset_metadata("db1", "a1")

        assert list(result.keys()) == ["keepme"]

    def test_excluded_and_empty_rows_on_page_two_are_filtered(self, physna):
        """FIX-014: system and empty-value rows on page 2 must not reach the payload."""
        pc, metadata_table, _ = physna
        from backend.backend.common.dynamoDbMetadataKeys import REINDEX_METADATA_RECORD_KEY
        metadata_table.query.side_effect = _pages(
            [_meta_item("onPage1")],
            [
                _meta_item("onPage2"),
                _meta_item(REINDEX_METADATA_RECORD_KEY),
                _meta_item("blankvalue", value=""),
            ],
        )

        result = pc.get_asset_metadata("db1", "a1")

        # The legitimate page-2 key must arrive (the paging half) AND the system /
        # empty rows on that page must be dropped (the filtering half).
        assert sorted(result.keys()) == ["onPage1", "onPage2"], (
            f"page-2 rows were not read and filtered; got {sorted(result.keys())}"
        )
        assert REINDEX_METADATA_RECORD_KEY not in result
        assert "blankvalue" not in result
