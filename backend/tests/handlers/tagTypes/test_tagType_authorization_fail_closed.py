# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed authorization in tagTypeService.

S2-BACKEND-144 -- `get_tag_types` carried an `else: # No authorization required, add all` arm, so an
empty token list returned every tag type in every database scope together with its tag names. Rule 4
grants the list-filtering exception only because appending an item **only when** `enforce()` passes is
fail-closed by construction; that `else` inverted it. `TestListingFailsClosed` pins the empty result
and pairs it with the authorized listing that must still return rows.

S2-BACKEND-145 -- `delete_tag_type` ran the existence check (404), the tag-type name GSI query and a
fully paged `tag_table.scan()` in-use check (400) **before** the Tier-2 `enforce()` (403). A caller
holding the DELETE route but no `tagType` constraint could therefore tell 404 from 400 from 403 and
learn which tag types exist, and which are in use, in scopes it cannot read -- driving a whole-table
scan per probe. `TestDeleteAuthorizesBeforeDisclosingAnything` asserts the property rather than one
spelling: an unauthorized caller gets the same 403 for every table state, and neither the lookup nor
the scan is reached at all.

## Why these tests assert who was consulted

The root conftest replaces `handlers.authz` with a stand-in whose `CasbinEnforcer` is a MagicMock,
whose `enforce()` returns a truthy Mock -- so a test written against the verdict alone can pass for
the wrong reason. `_EnforcerSpy` records every construction and every call, so the empty-token cases
assert what actually matters: Casbin was never consulted, and no table was read.
"""

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.tagTypes import tagTypeService as svc

# The module under test loads its own copy of models.common, so the raised class is that module's
# object -- referencing any other one makes pytest.raises() miss.
VAMSGeneralErrorResponse = svc.VAMSGeneralErrorResponse

TAG_TYPE_NAME = "Custom"
DATABASE_ID = "factory-db"

AUTHENTICATED = {"tokens": ["some-user"], "roles": ["admin"], "mfaEnabled": False}
NO_IDENTITY = {"tokens": [], "roles": [], "mfaEnabled": False}

GLOBAL_ROW = {
    "databaseId": "GLOBAL",
    "tagTypeName": "System",
    "description": "shared vocabulary",
    "required": "False",
}
SCOPED_ROW = {
    "databaseId": DATABASE_ID,
    "tagTypeName": TAG_TYPE_NAME,
    "description": "per-database vocabulary",
    "required": "False",
}


def _decision(action, database_id=DATABASE_ID, tag_type_name=TAG_TYPE_NAME):
    """The identifying tuple that `spy.decisions()` must contain for `action`.

    A tagType is authorized on tagTypeName + databaseId (CONSTRAINT_OBJECT_TYPE_FIELDS), so naming
    the object__type, scope and name pins that the right object was authorized -- which is the
    property. How many times it was authorized is not.
    """
    return ("tagType", database_id, tag_type_name, action)


class _EnforcerSpy:
    """Stands in for CasbinEnforcer, recording every construction and every enforce() call."""

    constructions = []
    calls = []
    verdict = True

    def __init__(self, claims_and_roles):
        _EnforcerSpy.constructions.append(claims_and_roles)

    def enforce(self, obj, act):
        _EnforcerSpy.calls.append((dict(obj), act))
        return _EnforcerSpy.verdict

    @classmethod
    def reset(cls, verdict=True):
        cls.constructions = []
        cls.calls = []
        cls.verdict = verdict

    @classmethod
    def decisions(cls):
        """The SET of (object, action) decisions Casbin was asked for.

        A set of identifying tuples rather than a list, and asserted with `in` / `<=` rather than
        `==`: a handler that authorizes the same object twice (defence in depth) or authorizes an
        additional object is strictly safer and must not turn a test red, while a handler that drops
        a check disappears from the set and does. Pinning the call list `== [...]` inverts both -- it
        fails on the safer handler, and is satisfied by the wrong object being authorized.
        """
        return {
            (doc.get("object__type"), doc.get("databaseId"), doc.get("tagTypeName"), act)
            for doc, act in cls.calls
        }


@pytest.fixture
def spy():
    _EnforcerSpy.reset()
    original = svc.CasbinEnforcer
    svc.CasbinEnforcer = _EnforcerSpy
    try:
        yield _EnforcerSpy
    finally:
        svc.CasbinEnforcer = original


@pytest.mark.unit
class TestListingFailsClosed:
    """S2-BACKEND-144."""

    def _tables(self, rows):
        tag_type_table = MagicMock()
        tag_type_table.query.return_value = {"Items": [dict(r) for r in rows]}
        dynamodb_client = MagicMock()
        # The tag-association lookup pages a scan; return no tags.
        dynamodb_client.get_paginator.return_value.paginate.return_value.build_full_result.return_value = {
            "Items": []
        }
        return tag_type_table, dynamodb_client

    def _list(self, claims, rows):
        tag_type_table, dynamodb_client = self._tables(rows)
        with patch.object(svc, "tag_type_table", tag_type_table), patch.object(
            svc, "dynamodb_client", dynamodb_client
        ):
            return svc.get_tag_types(
                {
                    "maxItems": 100,
                    "pageSize": 100,
                    "startingToken": None,
                    "databaseId": DATABASE_ID,
                },
                claims,
            )

    def test_authorized_listing_returns_rows(self, spy):
        """Positive control: an empty result must not be the answer for everybody."""
        result = self._list(AUTHENTICATED, [SCOPED_ROW])

        assert [t["tagTypeName"] for t in result["Items"]] == [TAG_TYPE_NAME]
        assert _decision("GET") in spy.decisions()

    def test_empty_tokens_returns_no_tag_types(self, spy):
        result = self._list(NO_IDENTITY, [SCOPED_ROW, GLOBAL_ROW])

        assert result["Items"] == []
        # The property: with no identity the enforcer is never built, so nothing can be appended.
        assert spy.constructions == []
        assert spy.calls == []

    def test_denied_listing_returns_no_tag_types(self, spy):
        """Negative control for the filter itself: a Casbin denial must also drop the row."""
        spy.reset(verdict=False)
        result = self._list(AUTHENTICATED, [SCOPED_ROW, GLOBAL_ROW])

        assert result["Items"] == []
        # Both rows were dropped by a decision, not by being skipped: each one's own (object, action)
        # must appear. Containment, so a handler that also authorized something else stays green.
        assert {
            _decision("GET"),
            _decision("GET", database_id="GLOBAL", tag_type_name="System"),
        } <= spy.decisions()


def _tag_type_table(stored_row):
    table = MagicMock()
    table.get_item.return_value = {"Item": dict(stored_row)} if stored_row else {}
    table.query.return_value = {"Items": [dict(stored_row)] if stored_row else []}
    return table


def _tag_table(tag_rows):
    table = MagicMock()
    table.scan.return_value = {"Items": [dict(r) for r in tag_rows]}
    return table


# The three table states an unauthorized caller used to be able to tell apart.
TABLE_STATES = {
    "absent": (None, []),
    "exists_unreferenced": (SCOPED_ROW, []),
    "exists_and_in_use": (
        SCOPED_ROW,
        [{"databaseId": DATABASE_ID, "tagName": "EquipID", "tagTypeName": TAG_TYPE_NAME}],
    ),
}


@pytest.mark.unit
class TestDeleteAuthorizesBeforeDisclosingAnything:
    """S2-BACKEND-145."""

    def _delete(self, claims, state):
        stored_row, tag_rows = TABLE_STATES[state]
        tag_type_table = _tag_type_table(stored_row)
        tag_table = _tag_table(tag_rows)
        with patch.object(svc, "tag_type_table", tag_type_table), patch.object(
            svc, "tag_table", tag_table
        ):
            try:
                result = svc.delete_tag_type(TAG_TYPE_NAME, claims, DATABASE_ID)
                return result, None, tag_type_table, tag_table
            except VAMSGeneralErrorResponse as raised:
                return None, raised, tag_type_table, tag_table

    @pytest.mark.parametrize("state", sorted(TABLE_STATES))
    def test_empty_tokens_denies_identically_and_reads_nothing(self, spy, state):
        _result, raised, tag_type_table, tag_table = self._delete(NO_IDENTITY, state)

        assert raised is not None and raised.status_code == 403
        # Same response for every table state: no existence or in-use signal.
        assert TAG_TYPE_NAME not in str(raised)
        # Nothing was read, so nothing could be disclosed and no scan was amplified.
        tag_type_table.get_item.assert_not_called()
        tag_type_table.query.assert_not_called()
        tag_table.scan.assert_not_called()
        tag_type_table.delete_item.assert_not_called()
        # The property for an absent identity: the enforcer is not consulted at all.
        assert spy.constructions == []
        assert spy.calls == []

    @pytest.mark.parametrize("state", sorted(TABLE_STATES))
    def test_unauthorized_caller_denies_identically_and_reads_nothing(self, spy, state):
        spy.reset(verdict=False)
        _result, raised, tag_type_table, tag_table = self._delete(AUTHENTICATED, state)

        assert raised is not None and raised.status_code == 403
        assert TAG_TYPE_NAME not in str(raised)
        tag_type_table.get_item.assert_not_called()
        tag_type_table.query.assert_not_called()
        tag_table.scan.assert_not_called()
        tag_type_table.delete_item.assert_not_called()
        # Casbin was asked, and asked about the right object.
        assert _decision("DELETE") in spy.decisions()

    def test_empty_token_denials_are_indistinguishable_across_table_states(self, spy):
        """The oracle itself: one message and one status code for every state."""
        seen = set()
        for state in TABLE_STATES:
            _result, raised, _tt, _t = self._delete(NO_IDENTITY, state)
            seen.add((raised.status_code, str(raised)))
        assert len(seen) == 1

    def test_unauthorized_denials_are_indistinguishable_across_table_states(self, spy):
        seen = set()
        for state in TABLE_STATES:
            spy.reset(verdict=False)
            _result, raised, _tt, _t = self._delete(AUTHENTICATED, state)
            seen.add((raised.status_code, str(raised)))
        assert len(seen) == 1


@pytest.mark.unit
class TestDeleteStillWorksForAnAuthorizedCaller:
    """Positive controls. The assertions above are all about denial, which a function that denied
    everything would satisfy; these pin that an authorized caller still gets each real outcome."""

    def _delete(self, state, scope=DATABASE_ID, name=TAG_TYPE_NAME):
        stored_row, tag_rows = TABLE_STATES[state]
        tag_type_table = _tag_type_table(stored_row)
        tag_table = _tag_table(tag_rows)
        with patch.object(svc, "tag_type_table", tag_type_table), patch.object(
            svc, "tag_table", tag_table
        ):
            try:
                return svc.delete_tag_type(name, AUTHENTICATED, scope), None, tag_type_table
            except VAMSGeneralErrorResponse as raised:
                return None, raised, tag_type_table

    def test_unreferenced_tag_type_is_deleted(self, spy):
        result, raised, tag_type_table = self._delete("exists_unreferenced")

        assert raised is None
        assert result.success is True
        assert tag_type_table.delete_item.call_args.kwargs["Key"] == {
            "databaseId": DATABASE_ID,
            "tagTypeName": TAG_TYPE_NAME,
        }

    def test_referenced_tag_type_is_still_blocked(self, spy):
        result, raised, tag_type_table = self._delete("exists_and_in_use")

        assert result is None
        assert raised.status_code == 400
        assert "in use" in str(raised)
        tag_type_table.delete_item.assert_not_called()

    def test_missing_tag_type_still_reports_not_found(self, spy):
        result, raised, tag_type_table = self._delete("absent")

        assert result is None
        assert raised.status_code == 404
        tag_type_table.delete_item.assert_not_called()


class _ScopeBoundEnforcer:
    """Stands in for CasbinEnforcer holding one scope's tagType DELETE grant.

    Models the two behaviours that decide this: `enforce()` reduces the document to the fields valid
    for its own object__type (tagType -> tagTypeName + databaseId, per CONSTRAINT_OBJECT_TYPE_FIELDS)
    and defaults an absent constraint field to an empty value, so a rule scoped to one databaseId can
    never match another.
    """

    GRANTED_SCOPE = DATABASE_ID

    def __init__(self, claims_and_roles):
        self.claims_and_roles = claims_and_roles

    def enforce(self, obj, act):
        return obj.get("object__type") == "tagType" and obj.get("databaseId") == self.GRANTED_SCOPE


@pytest.mark.unit
class TestDeleteStaysBoundToTheRequestedScope:
    """Authorizing before the record is loaded must not loosen the scope binding.

    The lookup keys on (databaseId, tagTypeName), so the requested scope IS the stored scope of
    whatever the delete can reach -- these pin that, rather than assuming it: a role granted one
    database's tag types must not reach another's, and the row that is deleted must be the one that
    was authorized.
    """

    def _delete(self, scope):
        tag_type_table = _tag_type_table(dict(SCOPED_ROW, databaseId=scope))
        tag_table = _tag_table([])
        original = svc.CasbinEnforcer
        svc.CasbinEnforcer = _ScopeBoundEnforcer
        try:
            with patch.object(svc, "tag_type_table", tag_type_table), patch.object(
                svc, "tag_table", tag_table
            ):
                try:
                    return svc.delete_tag_type(TAG_TYPE_NAME, AUTHENTICATED, scope), None, tag_type_table
                except VAMSGeneralErrorResponse as raised:
                    return None, raised, tag_type_table
        finally:
            svc.CasbinEnforcer = original

    def test_granted_scope_is_deleted(self):
        result, raised, tag_type_table = self._delete(DATABASE_ID)

        assert raised is None and result.success is True
        assert tag_type_table.delete_item.call_args.kwargs["Key"]["databaseId"] == DATABASE_ID

    def test_another_scope_is_denied(self):
        """Negative control: the same name in a scope the role has no grant on must be refused."""
        result, raised, tag_type_table = self._delete("hospital-db")

        assert result is None and raised.status_code == 403
        tag_type_table.get_item.assert_not_called()
        tag_type_table.delete_item.assert_not_called()

    def test_absent_database_id_authorizes_against_the_global_scope(self):
        """The default scope must be the one authorized, not an empty value.

        Asserted as a set with an explicit "no empty scope" clause rather than as the exact call
        list: authorizing the stored row a second time is strictly safer and must stay green, while
        dropping the check empties `seen` and turns this red.
        """
        seen = []

        class _Spy(_ScopeBoundEnforcer):
            def enforce(self, obj, act):
                seen.append(dict(obj))
                return True

        tag_type_table = _tag_type_table(dict(SCOPED_ROW, databaseId="GLOBAL"))
        original = svc.CasbinEnforcer
        svc.CasbinEnforcer = _Spy
        try:
            with patch.object(svc, "tag_type_table", tag_type_table), patch.object(
                svc, "tag_table", _tag_table([])
            ):
                svc.delete_tag_type(TAG_TYPE_NAME, AUTHENTICATED)
        finally:
            svc.CasbinEnforcer = original

        scopes = {d.get("databaseId") for d in seen}
        assert "GLOBAL" in scopes
        assert not scopes & {None, ""}
