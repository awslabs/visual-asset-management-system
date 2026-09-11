# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset-link CREATE checks page on the PRESENCE of LastEvaluatedKey.

Both gates ``create_asset_link`` runs before it writes are reads of the ``fromAssetGSI``:
``check_existing_relationship`` (duplicate alias, bidirectional pair) and the descendant walk in
``detect_cycle_in_parent_child``. A single query returns at most one 1 MB page, and DynamoDB applies
the ``relationshipType`` FilterExpression AFTER that page is read -- so a partition mixing ``related``
and ``parentChild`` rows returns zero Items with a LastEvaluatedKey present. A gate that reads only
``response['Items']`` therefore treats a node with children as a leaf and admits a link that closes a
genuine parent-child cycle, which no consumer can repair: ``build_child_tree`` copes by dropping the
re-entrant branch, so the hierarchy view reports a wrong tree with no error.

The loop must end on the key being ABSENT rather than on its value being falsy -- both because absence
is DynamoDB's only end-of-set signal, and because the presence form is the only one that stays finite
against an under-stubbed reader. Several tests in ``test_assetLinksService.py`` patch
``asset_links_table`` with a bare MagicMock, so the value form would hang the suite rather than fail
it. See ``tests/pagingStub``.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.assetLinks import createAssetLink as cal
from backend.tests.pagingStub import BareMockReader, Pager, PagingLoopDidNotTerminate

MOD = "backend.backend.handlers.assetLinks.createAssetLink"

FROM_KEY_ATTR = "fromAssetDatabaseId:fromAssetId"

PARENT = "asset-parent"
CHILD = "asset-child"
DB = "test-database-1"
GRANDCHILD_ON_PAGE_2 = "asset-grandchild-on-page-2"


def _link(link_id, from_asset_id, to_asset_id, relationship_type=None, alias_id=None):
    """A links-table row as the fromAssetGSI projects it (its projection is the default, ALL)."""
    row = {
        "assetLinkId": link_id,
        FROM_KEY_ATTR: f"{DB}:{from_asset_id}",
        "fromAssetDatabaseId": DB,
        "fromAssetId": from_asset_id,
        "toAssetDatabaseId:toAssetId": f"{DB}:{to_asset_id}",
        "toAssetDatabaseId": DB,
        "toAssetId": to_asset_id,
        "relationshipType": relationship_type or cal.RelationshipType.PARENT_CHILD,
    }
    if alias_id is not None:
        row["assetLinkAliasId"] = alias_id
    return row


def _condition_values(condition):
    """The ``{attributeName: value}`` a KeyConditionExpression compares on.

    ``repr()`` of a boto3 condition carries no values, so routing has to read the expression tree.
    """
    expression = condition.get_expression()
    if expression["operator"] == "AND":
        values = {}
        for sub in expression["values"]:
            values.update(_condition_values(sub))
        return values
    attribute, value = expression["values"]
    return {attribute.name: value}


def _routed_on_from_key(default=None, **pagers):
    """Serve each GSI partition key its own shared ``Pager``, keyed on the asset it reads from.

    Routing on the key the read is actually keyed to -- rather than on call order -- keeps every
    assertion at "the cursor was threaded", which is what the shared Pager guarantees.
    """
    def reader(**kwargs):
        from_key = _condition_values(kwargs["KeyConditionExpression"])[FROM_KEY_ATTR]
        asset_id = from_key.split(":", 1)[1]
        pager = pagers.get(asset_id)
        if pager is None:
            if default is not None:
                return default
            raise PagingLoopDidNotTerminate(
                f"unrouted read from {from_key!r}; routed assets are {sorted(pagers)}")
        return pager(**kwargs)
    return reader


@pytest.fixture
def links_table():
    """The links table the create-time gates read; page sequences are set per test."""
    table = MagicMock()
    with patch(f"{MOD}.asset_links_table", table):
        yield table


def _create_event(relationship_type="parentChild", alias_id=None):
    body = {
        "fromAssetId": PARENT,
        "fromAssetDatabaseId": DB,
        "toAssetId": CHILD,
        "toAssetDatabaseId": DB,
        "relationshipType": relationship_type,
    }
    if alias_id is not None:
        body["assetLinkAliasId"] = alias_id
    return {
        "body": json.dumps(body),
        "requestContext": {"http": {"method": "POST", "path": "/asset-links"}},
        "headers": {"Authorization": "Bearer test-token"},
    }


@pytest.fixture
def authorized_create():
    """Everything create_asset_link checks apart from the paged gates, all passing."""
    with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["test-token"]}), \
            patch(f"{MOD}.CasbinEnforcer") as enforcer, \
            patch(f"{MOD}.validate_assets_exist", return_value=True), \
            patch(f"{MOD}.check_asset_permissions", return_value=True):
        enforcer.return_value.enforceAPI.return_value = True
        yield


@pytest.mark.unit
class TestCycleWalkPaging:
    """``detect_cycle_in_parent_child`` -- the descendant walk that guards the acyclic invariant."""

    def test_a_cycle_closed_by_a_child_on_a_later_page_is_detected(self, links_table):
        """The primary defect: the child that closes the cycle sits past the first page.

        Page one is the shape DynamoDB actually returns for a mixed partition -- zero Items after the
        relationshipType filter, with a LastEvaluatedKey still present. A gate reading only
        ``response['Items']`` sees CHILD as a leaf and reports no cycle.
        """
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "l-page-1"}},
            {"Items": [_link("l-page-2", CHILD, PARENT)]},
            name="cycle walk from CHILD",
        )
        links_table.query.side_effect = _routed_on_from_key(**{CHILD: pager})

        assert cal.detect_cycle_in_parent_child(PARENT, DB, CHILD, DB) is True
        pager.assert_paged_to_exhaustion()

    def test_a_cycle_two_hops_deep_across_pages_is_detected(self, links_table):
        """Every node the walk visits must be paged, not just the first.

        CHILD's children arrive over two pages, and the row that closes the cycle hangs off the
        grandchild -- so a gate that paged only the entry read would still miss it.
        """
        child_pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "c-page-1"}},
            {"Items": [_link("c-page-2", CHILD, GRANDCHILD_ON_PAGE_2)]},
            name="cycle walk from CHILD",
        )
        grandchild_pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "g-page-1"}},
            {"Items": [_link("g-page-2", GRANDCHILD_ON_PAGE_2, PARENT)]},
            name="cycle walk from GRANDCHILD",
        )
        links_table.query.side_effect = _routed_on_from_key(
            **{CHILD: child_pager, GRANDCHILD_ON_PAGE_2: grandchild_pager})

        assert cal.detect_cycle_in_parent_child(PARENT, DB, CHILD, DB) is True
        child_pager.assert_paged_to_exhaustion()
        grandchild_pager.assert_paged_to_exhaustion()

    def test_an_acyclic_multi_page_walk_reports_no_cycle(self, links_table):
        """Positive control on the paging itself: reading every page must not invent a cycle.

        A "fix" that simply returned True would satisfy the two tests above; it fails here.
        """
        pager = Pager(
            {"Items": [_link("l1", CHILD, "unrelated-a")], "LastEvaluatedKey": {"assetLinkId": "l1"}},
            {"Items": [_link("l2", CHILD, "unrelated-b")]},
            name="acyclic walk from CHILD",
        )
        # The unrelated descendants are leaves, so they answer from the default page.
        links_table.query.side_effect = _routed_on_from_key(default={"Items": []}, **{CHILD: pager})

        assert cal.detect_cycle_in_parent_child(PARENT, DB, CHILD, DB) is False
        pager.assert_paged_to_exhaustion()

    def test_the_walk_reads_the_from_asset_gsi_filtered_to_parent_child(self, links_table):
        """The read shape the walk depends on, which the page sequences above do not constrain.

        A stub answers whatever it is asked, so dropping the ``relationshipType`` filter would feed
        ``related`` rows into the descendant walk and report cycles that do not exist, and dropping
        the index would key the read against the base table -- neither visible in any assertion on
        the pages themselves.
        """
        pager = Pager({"Items": []}, name="cycle walk from CHILD")
        links_table.query.side_effect = _routed_on_from_key(**{CHILD: pager})

        cal.detect_cycle_in_parent_child(PARENT, DB, CHILD, DB)

        read = pager.calls[0]
        assert read["IndexName"] == "fromAssetGSI"
        assert _condition_values(read["KeyConditionExpression"]) == {FROM_KEY_ATTR: f"{DB}:{CHILD}"}
        assert _condition_values(read["FilterExpression"]) == {
            "relationshipType": cal.RelationshipType.PARENT_CHILD}

    def test_the_walk_terminates_against_an_under_stubbed_reader(self, links_table):
        """A fixture that stubs the table but not its pages must not hang the run.

        ``test_assetLinksService.py`` patches ``asset_links_table`` with a bare MagicMock, so the
        value form of the loop would run this suite past its timeout naming no test.
        """
        links_table.query.side_effect = BareMockReader(name="cycle walk")

        assert cal.detect_cycle_in_parent_child(PARENT, DB, CHILD, DB) is False


@pytest.mark.unit
class TestExistingRelationshipPaging:
    """``check_existing_relationship`` -- the duplicate-alias and bidirectional gates."""

    def test_a_duplicate_parent_child_alias_on_a_later_page_is_found(self, links_table):
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "l-page-1"}},
            {"Items": [_link("l-page-2", PARENT, CHILD, alias_id="alias-1")]},
            name="alias uniqueness, PARENT->CHILD",
        )
        links_table.query.side_effect = _routed_on_from_key(
            **{PARENT: pager, CHILD: Pager({"Items": []}, name="reverse pair read")})

        found = cal.check_existing_relationship(
            PARENT, DB, CHILD, DB, cal.RelationshipType.PARENT_CHILD, "alias-1")

        assert found is True
        pager.assert_paged_to_exhaustion()

    def test_a_reverse_parent_child_row_on_a_later_page_is_found(self, links_table):
        """The bidirectional gate: CHILD->PARENT already exists, on page two of the reverse read."""
        reverse = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "r-page-1"}},
            {"Items": [_link("r-page-2", CHILD, PARENT)]},
            name="reverse pair read, CHILD->PARENT",
        )
        links_table.query.side_effect = _routed_on_from_key(
            **{PARENT: Pager({"Items": []}, name="forward pair read"), CHILD: reverse})

        found = cal.check_existing_relationship(
            PARENT, DB, CHILD, DB, cal.RelationshipType.PARENT_CHILD, None)

        assert found is True
        reverse.assert_paged_to_exhaustion()

    def test_a_related_link_on_a_later_page_is_found(self, links_table):
        forward = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "l-page-1"}},
            {"Items": [_link("l-page-2", PARENT, CHILD,
                             relationship_type=cal.RelationshipType.RELATED)]},
            name="related, PARENT->CHILD",
        )
        links_table.query.side_effect = _routed_on_from_key(
            **{PARENT: forward, CHILD: Pager({"Items": []}, name="related, CHILD->PARENT")})

        found = cal.check_existing_relationship(
            PARENT, DB, CHILD, DB, cal.RelationshipType.RELATED, None)

        assert found is True
        forward.assert_paged_to_exhaustion()

    def test_a_related_pair_with_no_match_across_pages_does_not_block_the_link(self, links_table):
        """Positive control on the ``related`` arm, which the test above narrows.

        Both pages of the forward read are empty after the filter, so the first ``related`` link
        between the pair must still be allowed once every page has been read.
        """
        forward = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "l-page-1"}},
            {"Items": []},
            name="related, PARENT->CHILD",
        )
        links_table.query.side_effect = _routed_on_from_key(
            **{PARENT: forward, CHILD: Pager({"Items": []}, name="related, CHILD->PARENT")})

        found = cal.check_existing_relationship(
            PARENT, DB, CHILD, DB, cal.RelationshipType.RELATED, None)

        assert found is False
        forward.assert_paged_to_exhaustion()

    def test_a_single_page_with_no_match_reports_no_existing_relationship(self, links_table):
        """Positive control: a first link between two assets is still allowed through the gate."""
        links_table.query.side_effect = _routed_on_from_key(default={"Items": []})

        found = cal.check_existing_relationship(
            PARENT, DB, CHILD, DB, cal.RelationshipType.PARENT_CHILD, None)

        assert found is False

    def test_a_different_alias_on_a_later_page_does_not_block_the_link(self, links_table):
        """Positive control on the paging: reading page two must not over-reject.

        The row on page two carries a DIFFERENT alias, which the alias-uniqueness contract permits.
        """
        forward = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "l-page-1"}},
            {"Items": [_link("l-page-2", PARENT, CHILD, alias_id="alias-other")]},
            name="alias uniqueness, PARENT->CHILD",
        )
        links_table.query.side_effect = _routed_on_from_key(
            **{PARENT: forward, CHILD: Pager({"Items": []}, name="reverse pair read")})

        found = cal.check_existing_relationship(
            PARENT, DB, CHILD, DB, cal.RelationshipType.PARENT_CHILD, "alias-1")

        assert found is False
        forward.assert_paged_to_exhaustion()

    def test_the_gate_terminates_against_an_under_stubbed_reader(self, links_table):
        links_table.query.side_effect = BareMockReader(name="existing relationship gate")

        assert cal.check_existing_relationship(
            PARENT, DB, CHILD, DB, cal.RelationshipType.PARENT_CHILD, None) is False


@pytest.mark.unit
class TestCreateAssetLinkResponse:
    """The wire outcome: POST /asset-links must refuse the cycle rather than write it."""

    def test_a_cycle_closed_by_a_child_on_a_later_page_is_rejected(
            self, links_table, authorized_create):
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"assetLinkId": "l-page-1"}},
            {"Items": [_link("l-page-2", CHILD, PARENT)]},
            name="cycle walk from CHILD",
        )
        with patch(f"{MOD}.check_existing_relationship", return_value=False):
            links_table.query.side_effect = _routed_on_from_key(**{CHILD: pager})

            response = cal.lambda_handler(_create_event(), {})

        assert response["statusCode"] == 400
        assert "cycle" in json.loads(response["body"])["message"]
        links_table.put_item.assert_not_called()

    def test_an_acyclic_link_is_still_created(self, links_table, authorized_create):
        """Positive control on the route: the legitimate create still returns 200 and writes."""
        with patch(f"{MOD}.check_existing_relationship", return_value=False):
            links_table.query.side_effect = _routed_on_from_key(default={"Items": []})

            response = cal.lambda_handler(_create_event(), {})

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "Asset link created successfully"
        # Split from an exact pin: that the row was written is the claim, and a duplicate
        # write would be a real defect, so both are stated. A retry that wrote the same row
        # twice used to fail this as loudly as writing nothing.
        assert links_table.put_item.called, 'the asset link row was never written'
        assert links_table.put_item.call_count <= 1, 'the link row was written more than once'
