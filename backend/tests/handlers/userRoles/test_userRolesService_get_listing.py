# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The user-role listing tolerates a row stored without `createdOn`, and orders totally.

`get_user_roles` scans the whole user-roles table and groups it by userId. Every writer in this
repo sets `createdOn` -- both create paths and the CDK auth-defaults seeds -- but rows without it
exist in the field (operator-inserted, or a pre-V2 vintage table), and the grouping loop read the
attribute directly. One such row therefore failed the request for EVERY caller: the read is a
whole-table scan feeding one shared loop, so there is no partial result and no in-product
workaround, and role assignment is how VAMS grants access.

Three properties are pinned, and they are separable defects:

* **A row without the attribute does not fail the listing.** The absent value takes a default.
* **The default is a CONSTANT.** `createdOn` is also the pagination token -- `NextToken` on the
  way out, `startingToken` on the way back in -- and the resume loop DISCARDS every item whose
  value does not equal the token. A default computed per request (a timestamp of now) therefore
  matches nothing on the next page and the page comes back EMPTY, trading a loud failure for
  silent data loss. The default is asserted to be identical across two separate requests rather
  than asserted to equal a literal, because it is the stability that matters.
* **The default is not `None`.** A bare `.get("createdOn")` -- the idiom the sibling
  `roleService.py` uses -- is not sufficient here, because roleService never sorts by the value
  and this listing does: `sorted` raises `TypeError: '<' not supported between instances of 'str'
  and 'NoneType'` on a mixed page, which converts one failure into another. A row carrying an
  explicit DynamoDB NULL exercises that path.

Separately, the ordering must be TOTAL. `createdOn` is not unique -- the grouping keeps the first
row's value per userId and nothing stops two users sharing a timestamp -- and a single-key sort is
merely stable, so tied users come back in whatever order the scan happened to return them. Across
two requests that order can differ, which by itself skips or repeats users at a page boundary.
`TestOrderingIsTotal` presents the same two tied users in both scan orders and requires one
answer; that case is independent of `createdOn` being absent and fails on its own.

Every case is paired with a positive control, because a listing that returned nothing at all
would satisfy several of the assertions above on its own.

One consequence of the constant default is recorded rather than fixed, as an `xfail` at the end
of `TestPagingOverALegacyRow`: the default is falsy and the token IS the value, so a page
boundary landing inside the defaulted rows emits an empty `NextToken` that reads as
end-of-list. Carrying a token past it means keying pagination on `userId` instead, which
changes the API contract.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

from boto3.dynamodb.types import TypeDeserializer

from backend.backend.handlers.userRoles import userRolesService


_TOKENS = ["tester"]

#: Distinct, ordered timestamps. Spelled out rather than generated so the expected page
#: boundaries below can be read off the fixture.
_T1 = "2026-01-01T00:00:01"
_T2 = "2026-01-01T00:00:02"
_T3 = "2026-01-01T00:00:03"


def _row(user_id, role_name, created_on="__omit__"):
    """One raw, DynamoDB-typed user-role row as the low-level scan returns it.

    `created_on="__omit__"` omits the attribute entirely -- the legacy shape. `None` stores an
    explicit DynamoDB NULL, which deserializes to Python `None`.
    """
    row = {"userId": {"S": user_id}, "roleName": {"S": role_name}}
    if created_on is None:
        row["createdOn"] = {"NULL": True}
    elif created_on != "__omit__":
        row["createdOn"] = {"S": created_on}
    return row


class _AllowAllEnforcer:
    """A CasbinEnforcer stand-in that permits every object, and counts what it was asked.

    The count is what keeps these tests non-vacuous: it proves the scan stub was consumed and
    the grouping loop actually ran over every raw row, rather than the listing having been
    short-circuited somewhere above it.
    """

    calls = 0

    def __init__(self, claims_and_roles):
        pass

    def enforce(self, obj, action):
        type(self).calls += 1
        return True

    def enforceAPI(self, event):
        return True


def _event(max_items=100, starting_token=None):
    params = {"maxItems": str(max_items), "pageSize": "1000"}
    if starting_token is not None:
        params["startingToken"] = starting_token
    return {
        "requestContext": {"http": {"method": "GET", "path": "/user-roles"}},
        "pathParameters": None,
        "queryStringParameters": params,
        "headers": {"authorization": "Bearer test-token"},
    }


def _list_user_roles(rows, max_items=100, starting_token=None):
    """Run the GET request handler against a scanned page of `rows`.

    Returns (statusCode, the listing payload). The handler wraps the result in the legacy
    `{"message": ...}` envelope, which is unwrapped here so the assertions read against the
    listing itself.
    """
    paginate_result = MagicMock()
    paginate_result.build_full_result.return_value = {"Items": list(rows)}
    paginator = MagicMock()
    paginator.paginate.return_value = paginate_result
    client = MagicMock()
    client.get_paginator.return_value = paginator

    _AllowAllEnforcer.calls = 0
    saved_claims = userRolesService.claims_and_roles
    with patch.object(userRolesService, "dynamodb_client", client), \
            patch.object(userRolesService, "CasbinEnforcer", _AllowAllEnforcer):
        userRolesService.claims_and_roles = {"tokens": list(_TOKENS)}
        try:
            response = userRolesService.handle_get_request(
                _event(max_items=max_items, starting_token=starting_token)
            )
        finally:
            userRolesService.claims_and_roles = saved_claims

    body = json.loads(response["body"])
    return response["statusCode"], body.get("message")


def _user_ids(payload):
    return [item["userId"] for item in payload["Items"]]


@pytest.mark.unit
class TestALegacyRowDoesNotFailTheListing:
    """One row without `createdOn` must not remove the page for everybody."""

    def test_missing_attribute_is_tolerated(self):
        rows = [
            _row("u-legacy", "admin"),
            _row("u-a", "editor", _T1),
            _row("u-b", "viewer", _T2),
        ]

        status, payload = _list_user_roles(rows)

        assert status == 200, (
            f"one user-role row without createdOn failed the whole listing: {payload}"
        )
        assert set(_user_ids(payload)) == {"u-legacy", "u-a", "u-b"}, (
            f"the legacy row was dropped rather than defaulted: {payload}"
        )
        assert _AllowAllEnforcer.calls == len(rows), (
            "the grouping loop did not run over every scanned row, so this test does not "
            "exercise the path it claims to"
        )

    def test_explicit_null_is_tolerated(self):
        """A bare `.get()` default of None would raise TypeError sorting against a str."""
        rows = [
            _row("u-null", "admin", None),
            _row("u-a", "editor", _T1),
        ]

        status, payload = _list_user_roles(rows)

        assert status == 200, (
            f"a createdOn stored as NULL failed the listing; the default must not be None: "
            f"{payload}"
        )
        assert set(_user_ids(payload)) == {"u-null", "u-a"}

    def test_every_row_missing_the_attribute_is_tolerated(self):
        """A pre-V2 vintage table has the attribute on no row at all.

        The rows are presented in DESCENDING userId order, so the expected order below is the
        reverse of the scan order. A single-key sort over values that are all equal is merely
        stable and would return them as scanned, which makes the ordering assertion real
        rather than one the fixture satisfies by accident.
        """
        rows = [_row("u-b", "viewer"), _row("u-a", "admin")]

        status, payload = _list_user_roles(rows)

        assert status == 200, f"a table with no createdOn anywhere failed the listing: {payload}"
        assert _user_ids(payload) == ["u-a", "u-b"], (
            "with every value tied the order must still be total, by userId; got "
            f"{_user_ids(payload)} which is the scan order"
        )

    def test_the_legacy_fixture_row_really_lacks_the_attribute(self):
        """Negative control: the row the cases above rely on genuinely omits `createdOn`.

        Every assertion in this class rests on the fixture reproducing the field shape, and a
        fixture that quietly carried the attribute would let them all pass while exercising
        nothing. This reads the attribute the way the grouping loop used to and requires the
        failure the field report carried.
        """
        deserializer = TypeDeserializer()
        legacy = {k: deserializer.deserialize(v) for k, v in _row("u-legacy", "admin").items()}

        assert "createdOn" not in legacy
        with pytest.raises(KeyError):
            legacy["createdOn"]

    def test_a_fully_populated_listing_still_works(self):
        """Positive control: the ordinary case is unchanged, grouped and ordered by createdOn."""
        rows = [
            _row("u-b", "viewer", _T2),
            _row("u-a", "admin", _T1),
            _row("u-a", "editor", _T1),
        ]

        status, payload = _list_user_roles(rows)

        assert status == 200, f"an ordinary listing was refused: {payload}"
        assert _user_ids(payload) == ["u-a", "u-b"], (
            f"createdOn ordering was not preserved: {payload}"
        )
        assert payload["Items"][0]["roleName"] == ["admin", "editor"], (
            f"the two rows for u-a were not grouped: {payload}"
        )


@pytest.mark.unit
class TestTheDefaultIsConstant:
    """A computed default would make the pagination token unusable on the next request."""

    def test_two_requests_report_the_same_value_for_a_legacy_row(self):
        rows = [_row("u-legacy", "admin"), _row("u-a", "editor", _T1)]

        status_one, first = _list_user_roles(rows)
        status_two, second = _list_user_roles(rows)

        assert status_one == 200 and status_two == 200, (
            f"the listing failed with a legacy row present: {first} / {second}"
        )
        first_value = first["Items"][0]["createdOn"]
        second_value = second["Items"][0]["createdOn"]
        assert first_value == second_value, (
            "createdOn for a legacy row differed between two identical requests; a token "
            "handed out for one page would match nothing on the next and the page would come "
            f"back empty ({first_value!r} then {second_value!r})"
        )

    def test_the_populated_value_is_passed_through_unchanged(self):
        """Positive control: defaulting must not rewrite a value that IS stored."""
        _, payload = _list_user_roles([_row("u-a", "admin", _T1)])

        assert payload["Items"][0]["createdOn"] == _T1


@pytest.mark.unit
class TestOrderingIsTotal:
    """Two users sharing a timestamp must come back in one order, whatever the scan returned."""

    _TIED = "2026-02-02T00:00:00"

    def test_a_timestamp_tie_is_broken_deterministically(self):
        forwards = [_row("u-b", "viewer", self._TIED), _row("u-a", "admin", self._TIED)]
        backwards = list(reversed(forwards))

        _, from_forwards = _list_user_roles(forwards)
        _, from_backwards = _list_user_roles(backwards)

        assert _user_ids(from_forwards) == _user_ids(from_backwards), (
            "two users sharing a createdOn were returned in scan order, so the ordering is "
            f"only stable and not total: {_user_ids(from_forwards)} vs "
            f"{_user_ids(from_backwards)}"
        )

    def test_distinct_timestamps_still_order_by_createdOn(self):
        """Positive control: the tie-break must not displace the primary ordering."""
        rows = [_row("u-z", "viewer", _T1), _row("u-a", "admin", _T2)]

        _, payload = _list_user_roles(rows)

        assert _user_ids(payload) == ["u-z", "u-a"], (
            f"userId was applied ahead of createdOn rather than as the tie-break: {payload}"
        )


@pytest.mark.unit
class TestPagingOverALegacyRow:
    """The page-1 token must reach page 2 with the legacy row in the walk."""

    _ROWS = [
        _row("u-legacy", "admin"),
        _row("u-a", "editor", _T1),
        _row("u-b", "viewer", _T2),
        _row("u-c", "viewer", _T3),
    ]

    def test_the_token_walk_covers_every_user_exactly_once(self):
        status_one, page_one = _list_user_roles(self._ROWS, max_items=2)

        assert status_one == 200, f"page 1 failed with a legacy row present: {page_one}"
        assert "NextToken" in page_one, (
            f"four users over a page of two produced no NextToken: {page_one}"
        )

        status_two, page_two = _list_user_roles(
            self._ROWS, max_items=2, starting_token=page_one["NextToken"]
        )

        assert status_two == 200, f"page 2 failed: {page_two}"
        assert page_two["Items"], (
            "page 2 came back empty for the token page 1 handed out; the pagination token is "
            "not stable across requests"
        )

        walked = _user_ids(page_one) + _user_ids(page_two)
        assert walked == ["u-legacy", "u-a", "u-b", "u-c"], (
            f"the token walk skipped or repeated a user: {walked}"
        )

    def test_the_page_boundary_is_identical_between_requests(self):
        status_one, first = _list_user_roles(self._ROWS, max_items=2)
        status_two, second = _list_user_roles(self._ROWS, max_items=2)

        assert status_one == 200 and status_two == 200, (
            f"page 1 failed with a legacy row present: {first} / {second}"
        )
        assert first["NextToken"] == second["NextToken"], (
            "two identical page-1 requests handed out different NextTokens: "
            f"{first['NextToken']!r} then {second['NextToken']!r}"
        )
        assert _user_ids(first) == _user_ids(second)

    def test_paging_a_fully_populated_table_still_works(self):
        """Positive control: paging is unchanged when no row is missing createdOn."""
        rows = [
            _row("u-a", "admin", _T1),
            _row("u-b", "viewer", _T2),
            _row("u-c", "viewer", _T3),
        ]

        _, page_one = _list_user_roles(rows, max_items=2)
        assert _user_ids(page_one) == ["u-a", "u-b"], f"unexpected page 1: {page_one}"

        _, page_two = _list_user_roles(
            rows, max_items=2, starting_token=page_one["NextToken"]
        )
        assert _user_ids(page_two) == ["u-c"], f"unexpected page 2: {page_two}"

    @pytest.mark.xfail(
        reason="the pagination token IS createdOn, so a page boundary that lands inside the "
               "defaulted rows emits an empty NextToken, which every consumer reads as "
               "end-of-list; carrying a token past it means keying pagination on userId, "
               "which changes the API contract"
    )
    def test_a_boundary_inside_the_defaulted_rows_still_carries_a_token(self):
        """The one place the constant default is visible: it is falsy, and the token is the value.

        Reachable only when MORE grouped users lack `createdOn` than `maxItems` admits -- the
        total ordering puts every defaulted row on page 1, and `maxItems` defaults to 30000 --
        so it is recorded here rather than worked around. Without it the listing stops early
        and reports completion, which is quieter than the failure it replaced.
        """
        rows = [_row(f"u-{i}", "admin") for i in range(5)]

        status, page_one = _list_user_roles(rows, max_items=2)

        assert status == 200, f"page 1 failed: {page_one}"
        assert page_one.get("NextToken"), (
            "three of five users are unreachable: the NextToken is empty, which the web "
            "listing loop and this handler's own resume guard both read as end-of-list"
        )
