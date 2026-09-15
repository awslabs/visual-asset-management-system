# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The two paged reads in tagTypeService end on the PRESENCE of LastEvaluatedKey.

* the scoped tag-type listing (``GET /tag-types?databaseId=X`` / ``?scope=global``) reads one
  partition through ``_query_all_in_partition``; a truncated page reports a tag type that exists as
  absent;
* the delete path scans the tag table for references, and a reference beyond the first page would let
  a tag type in use be deleted, orphaning every tag that names it.

Both loops must end on the key being ABSENT rather than on its value being falsy: DynamoDB omits the
key on the final page, and the value form spins forever against an under-stubbed reader instead of
failing -- a timeout that names no test. ``get_tag_types`` wraps everything in ``except Exception``,
so the shared reader raises a ``BaseException`` that arm cannot swallow. See ``tests/pagingStub``.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.tagTypes import tagTypeService
from backend.backend.handlers.tagTypes.tagTypeService import delete_tag_type
from backend.backend.handlers.tagTypes.tagTypeService import get_tag_types as list_tag_types
from backend.tests.pagingStub import BareMockReader, Pager

MOD = "backend.backend.handlers.tagTypes.tagTypeService"
CLAIMS = {"tokens": ["u"]}
TagTypeServiceError = tagTypeService.VAMSGeneralErrorResponse

SCOPE = "factory-db"
LIST_PARAMS = {"maxItems": 100, "pageSize": 100, "startingToken": None}


def _tag_type(name, scope=SCOPE):
    return {"databaseId": scope, "tagTypeName": name, "description": "d", "required": "False"}


@pytest.fixture
def tag_type_table():
    """The tag-type table, with the tag association scan and Casbin stubbed to allow everything."""
    table = MagicMock()
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value.build_full_result.return_value = \
        {"Items": []}
    enforcer = MagicMock()
    enforcer.enforce.return_value = True
    with patch(f"{MOD}.tag_type_table", table), \
            patch(f"{MOD}.dynamodb_client", client), \
            patch(f"{MOD}.CasbinEnforcer", return_value=enforcer):
        yield table


@pytest.mark.unit
class TestScopedTagTypeListingPaging:
    def test_a_tag_type_on_a_later_page_is_listed(self, tag_type_table):
        """The defect the paging exists for, on the user-facing scoped listing."""
        pager = Pager(
            {"Items": [_tag_type("OnPageOne")],
             "LastEvaluatedKey": {"databaseId": SCOPE, "tagTypeName": "OnPageOne"}},
            {"Items": [_tag_type("OnPageTwo")],
             "LastEvaluatedKey": {"databaseId": SCOPE, "tagTypeName": "OnPageTwo"}},
            {"Items": [_tag_type("OnPageThree")]},
            name="_query_all_in_partition",
        )
        tag_type_table.query.side_effect = pager

        result = list_tag_types({**LIST_PARAMS, "databaseId": SCOPE}, CLAIMS)

        assert {tt["tagTypeName"] for tt in result["Items"]} == \
            {"OnPageOne", "OnPageTwo", "OnPageThree"}
        # Asserted over the set of cursors rather than over read counts, so an extra read passes.
        pager.assert_paged_to_exhaustion()

    def test_the_partition_condition_survives_every_continuation(self, tag_type_table):
        """A continuation that dropped the key condition would read other scopes' tag types."""
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"databaseId": SCOPE, "tagTypeName": "x"}},
            {"Items": [_tag_type("OnPageTwo")]},
            name="_query_all_in_partition",
        )
        tag_type_table.query.side_effect = pager

        list_tag_types({**LIST_PARAMS, "databaseId": SCOPE}, CLAIMS)

        assert all("KeyConditionExpression" in call for call in pager.calls), pager.calls
        assert all(call["KeyConditionExpression"]._values[1] == SCOPE
                   for call in pager.calls), pager.calls

    def test_the_global_scope_listing_pages_too(self, tag_type_table):
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"databaseId": "GLOBAL", "tagTypeName": "x"}},
            {"Items": [_tag_type("GlobalOnPageTwo", scope="GLOBAL")]},
            name="_query_all_in_partition",
        )
        tag_type_table.query.side_effect = pager

        result = list_tag_types({**LIST_PARAMS, "scope": "global"}, CLAIMS)

        assert [tt["tagTypeName"] for tt in result["Items"]] == ["GlobalOnPageTwo"]
        pager.assert_paged_to_exhaustion()

    def test_a_single_page_listing_returns_its_tag_types(self, tag_type_table):
        """Positive control for the termination test: the listing is not always empty."""
        tag_type_table.query.return_value = {"Items": [_tag_type("OnlyPage")]}

        result = list_tag_types({**LIST_PARAMS, "databaseId": SCOPE}, CLAIMS)

        assert [tt["tagTypeName"] for tt in result["Items"]] == ["OnlyPage"]

    def test_terminates_against_an_under_stubbed_reader(self, tag_type_table):
        """The capped reader turns the non-terminating form into a failure instead of a hang."""
        tag_type_table.query.side_effect = BareMockReader(name="_query_all_in_partition")

        result = list_tag_types({**LIST_PARAMS, "databaseId": SCOPE}, CLAIMS)

        assert result["Items"] == []


@pytest.mark.unit
class TestDeleteReferenceScanPaging:
    """The in-use check scans the tag table; a reference beyond page one must still block the delete."""

    def _tables(self, scan_side_effect=None, scan_return=None):
        tag_type_table = MagicMock()
        tag_type_table.get_item.return_value = {
            "Item": {"databaseId": SCOPE, "tagTypeName": "Custom",
                     "description": "d", "required": "False"}
        }
        tag_type_table.query.return_value = {"Items": []}
        tag_table = MagicMock()
        if scan_side_effect is not None:
            tag_table.scan.side_effect = scan_side_effect
        else:
            tag_table.scan.return_value = scan_return
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        return tag_type_table, tag_table, enforcer

    def test_a_referencing_tag_on_a_later_page_blocks_the_delete(self):
        pager = Pager(
            {"Items": [{"databaseId": SCOPE, "tagName": "Unrelated",
                        "tagTypeName": "SomethingElse"}],
             "LastEvaluatedKey": {"databaseId": SCOPE, "tagName": "Unrelated"}},
            {"Items": [{"databaseId": SCOPE, "tagName": "EquipID", "tagTypeName": "Custom"}]},
            name="delete_tag_type reference scan",
        )
        tag_type_table, tag_table, enforcer = self._tables(scan_side_effect=pager)

        with patch(f"{MOD}.tag_type_table", tag_type_table), \
                patch(f"{MOD}.tag_table", tag_table), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer):
            with pytest.raises(TagTypeServiceError) as exc:
                delete_tag_type("Custom", CLAIMS, database_id=SCOPE)

        assert exc.value.status_code == 400
        tag_type_table.delete_item.assert_not_called()
        pager.assert_paged_to_exhaustion()

    def test_an_unreferenced_tag_type_is_still_deleted_across_pages(self):
        """Positive control for the rejection above: paging must not block every delete."""
        pager = Pager(
            {"Items": [{"databaseId": SCOPE, "tagName": "Unrelated",
                        "tagTypeName": "SomethingElse"}],
             "LastEvaluatedKey": {"databaseId": SCOPE, "tagName": "Unrelated"}},
            {"Items": [{"databaseId": "other-db", "tagName": "Elsewhere",
                        "tagTypeName": "Custom"}]},
            name="delete_tag_type reference scan",
        )
        tag_type_table, tag_table, enforcer = self._tables(scan_side_effect=pager)

        with patch(f"{MOD}.tag_type_table", tag_type_table), \
                patch(f"{MOD}.tag_table", tag_table), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer):
            delete_tag_type("Custom", CLAIMS, database_id=SCOPE)

        tag_type_table.delete_item.assert_called_once()
        pager.assert_paged_to_exhaustion()

    def test_the_reference_scan_terminates_against_an_under_stubbed_reader(self):
        """A bare Mock page must end the scan; the value form spins and hangs the run."""
        tag_type_table, tag_table, enforcer = self._tables()
        tag_table.scan.side_effect = BareMockReader(name="delete_tag_type reference scan")

        with patch(f"{MOD}.tag_type_table", tag_type_table), \
                patch(f"{MOD}.tag_table", tag_table), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer):
            delete_tag_type("Custom", CLAIMS, database_id=SCOPE)

        tag_type_table.delete_item.assert_called_once()
