# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The scoped tag listing pages on the PRESENCE of LastEvaluatedKey.

``GET /tags?databaseId=X`` and ``?scope=global`` read one partition of the composite tag table
through ``_query_all_in_partition``. A single query returns at most 1 MB, so the loop has to page or
the listing silently truncates and reports the short list as complete -- a tag that exists is
reported as absent.

The loop must end on the key being ABSENT rather than on its value being falsy: DynamoDB omits the
key on the final page, and the value form spins forever against an under-stubbed reader instead of
failing, which is a timeout that names no test. ``get_tags`` wraps everything in ``except
Exception``, so the shared reader raises a ``BaseException`` that arm cannot swallow. See
``tests/pagingStub``.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.tags.tagService import get_tags
from backend.tests.pagingStub import BareMockReader, Pager

MOD = "backend.backend.handlers.tags.tagService"
CLAIMS = {"tokens": ["u"]}

SCOPE = "factory-db"


def _tag(name):
    return {"databaseId": SCOPE, "tagName": name, "description": "d", "tagTypeName": "Custom"}


@pytest.fixture
def tag_table():
    """The tag table, with the tag-type lookup and Casbin stubbed to allow everything."""
    table = MagicMock()
    enforcer = MagicMock()
    enforcer.enforce.return_value = True
    with patch(f"{MOD}.tag_table", table), \
            patch(f"{MOD}.get_tag_types", return_value=[]), \
            patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
            patch(f"{MOD}.claims_and_roles", CLAIMS):
        yield table


@pytest.mark.unit
class TestScopedTagListingPaging:
    def test_a_tag_on_a_later_page_is_listed(self, tag_table):
        """The defect the paging exists for, on the user-facing scoped listing."""
        pager = Pager(
            {"Items": [_tag("OnPageOne")],
             "LastEvaluatedKey": {"databaseId": SCOPE, "tagName": "OnPageOne"}},
            {"Items": [_tag("OnPageTwo")],
             "LastEvaluatedKey": {"databaseId": SCOPE, "tagName": "OnPageTwo"}},
            {"Items": [_tag("OnPageThree")]},
            name="_query_all_in_partition",
        )
        tag_table.query.side_effect = pager

        result = get_tags({"databaseId": SCOPE})

        assert {tag["tagName"] for tag in result["Items"]} == \
            {"OnPageOne", "OnPageTwo", "OnPageThree"}
        # Asserted over the set of cursors rather than over read counts, so an extra read passes.
        pager.assert_paged_to_exhaustion()

    def test_the_partition_condition_survives_every_continuation(self, tag_table):
        """A continuation that dropped the key condition would read other scopes' tags."""
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"databaseId": SCOPE, "tagName": "x"}},
            {"Items": [_tag("OnPageTwo")]},
            name="_query_all_in_partition",
        )
        tag_table.query.side_effect = pager

        get_tags({"databaseId": SCOPE})

        assert all("KeyConditionExpression" in call for call in pager.calls), pager.calls
        assert all(call["KeyConditionExpression"]._values[1] == SCOPE
                   for call in pager.calls), pager.calls

    def test_the_global_scope_listing_pages_too(self, tag_table):
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"databaseId": "GLOBAL", "tagName": "x"}},
            {"Items": [{"databaseId": "GLOBAL", "tagName": "GlobalOnPageTwo",
                        "description": "d", "tagTypeName": "System"}]},
            name="_query_all_in_partition",
        )
        tag_table.query.side_effect = pager

        result = get_tags({"scope": "global"})

        assert [tag["tagName"] for tag in result["Items"]] == ["GlobalOnPageTwo"]
        pager.assert_paged_to_exhaustion()

    def test_a_single_page_listing_returns_its_tags(self, tag_table):
        """Positive control for the termination test: the listing is not always empty."""
        tag_table.query.return_value = {"Items": [_tag("OnlyPage")]}

        assert [tag["tagName"] for tag in get_tags({"databaseId": SCOPE})["Items"]] == ["OnlyPage"]

    def test_terminates_against_an_under_stubbed_reader(self, tag_table):
        """A bare Mock page is what an under-specified fixture hands the loop.

        The capped reader turns the non-terminating form into a failure with a message instead of a
        hang; ``get_tags``'s ``except Exception`` arm cannot swallow it.
        """
        tag_table.query.side_effect = BareMockReader(name="_query_all_in_partition")

        assert get_tags({"databaseId": SCOPE})["Items"] == []
