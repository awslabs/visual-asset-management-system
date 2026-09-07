# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset-link reads page on the PRESENCE of LastEvaluatedKey.

A single query returns at most one 1 MB page, so the relationship listing, the child-tree walk and
the alias-uniqueness check all have to page to exhaustion or they drop links (an incomplete tree, and
a duplicate alias slipping past the uniqueness check).

The loop must end on the key being ABSENT rather than on its value being falsy. DynamoDB omits the
key on the final page, so presence is the accurate contract -- and it is the only form that stays
finite against an under-stubbed reader, which is the difference between a diagnosable failure and a
run that hangs past its timeout naming no test. See ``tests/pagingStub``.
"""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.assetLinks import assetLinksService as als
from backend.tests.pagingStub import BareMockReader, Pager, RoutedPager

MOD = "backend.backend.handlers.assetLinks.assetLinksService"
CLAIMS = {"tokens": ["u"], "roles": ["someRole"]}

CHILD_ON_PAGE_2 = "child-on-page-2"


def _link(link_id, to_asset_id):
    return {
        "assetLinkId": link_id,
        "fromAssetDatabaseId": "db1",
        "fromAssetId": "asset-1",
        "toAssetDatabaseId": "db1",
        "toAssetId": to_asset_id,
        "relationshipType": als.RelationshipType.PARENT_CHILD,
        "assetLinkAliasId": "",
    }


def _asset(asset_id):
    return {"databaseId": "db1", "assetId": asset_id, "assetName": f"Name of {asset_id}"}


@pytest.fixture
def links_table():
    """The links table plus the asset lookups the listing needs, all authorized."""
    table = MagicMock()
    with ExitStack() as stack:
        stack.enter_context(patch(f"{MOD}.asset_links_table", table))
        stack.enter_context(
            patch(f"{MOD}.get_asset_details", side_effect=lambda a, d: _asset(a)))
        stack.enter_context(patch(f"{MOD}.check_asset_permission", return_value=True))
        stack.enter_context(patch(
            f"{MOD}.batch_get_asset_details",
            side_effect=lambda keys: ({f"{d}:{a}": _asset(a) for d, a in keys}, [])))
        yield table


@pytest.mark.unit
class TestQueryAllItemsHelperPaging:
    """The shared helper every asset-link read goes through."""

    def test_every_page_cursor_is_resumed_from(self):
        pager = Pager(
            {"Items": [{"assetLinkId": "l1"}], "LastEvaluatedKey": {"assetLinkId": "l1"}},
            {"Items": [{"assetLinkId": "l2"}], "LastEvaluatedKey": {"assetLinkId": "l2"}},
            {"Items": [{"assetLinkId": "l3"}]},
            name="assetLinks.query_all_items",
        )
        table = MagicMock()
        table.query.side_effect = pager

        items = als.query_all_items(table, IndexName="fromAssetGSI")

        assert [item["assetLinkId"] for item in items] == ["l1", "l2", "l3"]
        # Asserted over the set of cursors rather than over read counts, so an extra read passes.
        pager.assert_paged_to_exhaustion()

    def test_the_index_and_condition_survive_every_continuation(self):
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "l1"}},
            {"Items": [{"assetLinkId": "l2"}]},
            name="assetLinks.query_all_items",
        )
        table = MagicMock()
        table.query.side_effect = pager

        als.query_all_items(table, IndexName="fromAssetGSI", KeyConditionExpression="k")

        assert all(call["IndexName"] == "fromAssetGSI" for call in pager.calls), pager.calls
        assert all(call["KeyConditionExpression"] == "k" for call in pager.calls), pager.calls

    def test_terminates_against_an_under_stubbed_reader(self):
        table = MagicMock()
        table.query.side_effect = BareMockReader(name="assetLinks.query_all_items")

        assert als.query_all_items(table, IndexName="fromAssetGSI") == []


@pytest.mark.unit
class TestRelationshipListingPaging:
    """The production caller: GET /assets/{databaseId}/{assetId}/links."""

    def test_a_child_link_on_a_later_page_reaches_the_response(self, links_table):
        """The defect the paging exists for, on the user-facing listing.

        The two GSI reads are routed by IndexName so each is served its own page sequence -- the
        assertion stays on "the cursor is threaded" rather than on call order.
        """
        routed = RoutedPager(
            "IndexName",
            fromAssetGSI=Pager(
                {"Items": [], "LastEvaluatedKey": {"assetLinkId": "l-page-1"}},
                {"Items": [_link("l-page-2", CHILD_ON_PAGE_2)]},
                name="fromAssetGSI",
            ),
            toAssetGSI=Pager({"Items": []}, name="toAssetGSI"),
        )
        links_table.query.side_effect = routed

        response = als.get_asset_links_for_asset("asset-1", "db1", False, CLAIMS)

        assert [child.assetId for child in response.children] == [CHILD_ON_PAGE_2]
        routed.assert_paged_to_exhaustion()

    def test_a_single_page_child_link_is_listed(self, links_table):
        """Positive control: the listing does return children when the reader serves one page."""
        links_table.query.side_effect = RoutedPager(
            "IndexName",
            fromAssetGSI=Pager({"Items": [_link("l1", "child-1")]}, name="fromAssetGSI"),
            toAssetGSI=Pager({"Items": []}, name="toAssetGSI"),
        )

        response = als.get_asset_links_for_asset("asset-1", "db1", False, CLAIMS)

        assert [child.assetId for child in response.children] == ["child-1"]

    def test_the_listing_terminates_against_an_under_stubbed_reader(self, links_table):
        """A fixture that stubs the table but not its pages must not hang the run.

        ``get_asset_links_for_asset`` re-raises, so the capped reader surfaces here as a failure
        with an explanation rather than as a timeout.
        """
        links_table.query.side_effect = BareMockReader(name="assetLinks listing")

        response = als.get_asset_links_for_asset("asset-1", "db1", False, CLAIMS)

        assert response.children == []
        assert response.parents == []
        assert response.related == []
