# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The tagTypeService denials survive to the HTTP response, and the unscoped listing filters too.

`test_tagType_authorization_fail_closed.py` pins the two authorization properties at the
business-function level: `get_tag_types` appends only when `enforce()` passes, and `delete_tag_type`
authorizes before it reads anything. Neither property reaches a caller on its own, and two links
between the function and the wire have no coverage:

* **The status code.** `delete_tag_type` raises `VAMSGeneralErrorResponse(..., status_code=403)` and
  `handle_delete_request` forwards it as `general_error(body=..., status_code=v.status_code)`. That
  keyword is the only thing between a `403` and `general_error`'s `400` default, and a `400` is what
  the CLI reads as `InvalidTagTypeDataError` / `TagTypeInUseError` rather than as an authorization
  failure. Every handler-level test in `test_tagTypeService.py` begins with `pytest.skip`, so
  nothing exercises `lambda_handler` for this route.
* **The unscoped listing branch.** `get_tag_types` selects rows three ways -- a `databaseId`
  partition query, the GLOBAL partition, and a paginated scan for `?scope=all` or no scope at all.
  The fail-closed tests always pass `databaseId`, so the scan branch feeding the deserializer is
  never walked.

Both classes assert across the three table states an unauthorized caller must not be able to tell
apart (absent / present-and-unreferenced / present-and-in-use), and pair each denial with the
authorized arm that must still receive the real outcome.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.tagTypes import tagTypeService as svc

TAG_TYPE_NAME = "Custom"
DATABASE_ID = "factory-db"

AUTHENTICATED = {"tokens": ["some-user"], "roles": ["admin"], "mfaEnabled": False}
NO_IDENTITY = {"tokens": [], "roles": [], "mfaEnabled": False}

SCOPED_ROW = {
    "databaseId": DATABASE_ID,
    "tagTypeName": TAG_TYPE_NAME,
    "description": "per-database vocabulary",
    "required": "False",
}
GLOBAL_ROW = {
    "databaseId": "GLOBAL",
    "tagTypeName": TAG_TYPE_NAME,
    "description": "shared vocabulary",
    "required": "False",
}
REFERENCING_TAG = {
    "databaseId": DATABASE_ID,
    "tagName": "EquipID",
    "tagTypeName": TAG_TYPE_NAME,
}

# The three states the pre-authorization reference check used to answer differently.
TABLE_STATES = {
    "absent": (None, []),
    "exists_unreferenced": (SCOPED_ROW, []),
    "exists_and_in_use": (SCOPED_ROW, [REFERENCING_TAG]),
}


class _UnexpectedRead(BaseException):
    """A mis-routed table read.

    Derives from `BaseException` deliberately: `get_tag_types` wraps its whole body in
    `except Exception` and re-raises as a retrieval error, which would turn a stub routed to the
    wrong table into a message about DynamoDB instead of about the stub.
    """


class _Enforcer:
    """Stands in for CasbinEnforcer. Tier 1 allows the route; Tier 2 answers `object_verdict`."""

    object_verdict = True
    objects = []

    def __init__(self, claims_and_roles):
        self.claims_and_roles = claims_and_roles

    def enforceAPI(self, event):
        return True

    def enforce(self, obj, act):
        _Enforcer.objects.append((dict(obj), act))
        return _Enforcer.object_verdict

    @classmethod
    def reset(cls, object_verdict=True):
        cls.object_verdict = object_verdict
        cls.objects = []

    @classmethod
    def decisions(cls):
        """The SET of identifying (object, action) tuples Casbin was asked for.

        A set asserted with `in`, so a handler that authorizes the same object twice, or an
        additional one, stays green while a handler that drops a check disappears from it.
        """
        return {
            (o.get("object__type"), o.get("databaseId"), o.get("tagTypeName"), act)
            for o, act in cls.objects
        }


def _delete_event(database_id=DATABASE_ID, tag_type_name=TAG_TYPE_NAME):
    """A DELETE event. `database_id=None` sends no query string at all -- the shape a REST request
    with no parameters delivers, and the one that defaults the scope to GLOBAL."""
    return {
        "requestContext": {
            "http": {"method": "DELETE", "path": f"/tag-types/{tag_type_name}"}
        },
        "pathParameters": {"tagTypeId": tag_type_name},
        "queryStringParameters": {"databaseId": database_id} if database_id else None,
    }


def _invoke_delete(event, claims, object_verdict, stored_row, tag_rows):
    tag_type_table = MagicMock()
    tag_type_table.get_item.return_value = {"Item": dict(stored_row)} if stored_row else {}
    tag_type_table.query.return_value = {"Items": [dict(stored_row)] if stored_row else []}
    tag_table = MagicMock()
    tag_table.scan.return_value = {"Items": [dict(r) for r in tag_rows]}

    _Enforcer.reset(object_verdict)
    with patch.object(svc, "request_to_claims", lambda e: dict(claims)), patch.object(
        svc, "CasbinEnforcer", _Enforcer
    ), patch.object(svc, "tag_type_table", tag_type_table), patch.object(
        svc, "tag_table", tag_table
    ):
        response = svc.lambda_handler(event, MagicMock())
    return response, tag_type_table, tag_table


def _delete_state(state, claims=AUTHENTICATED, object_verdict=False, database_id=DATABASE_ID):
    stored_row, tag_rows = TABLE_STATES[state]
    return _invoke_delete(
        _delete_event(database_id=database_id), claims, object_verdict, stored_row, tag_rows
    )


@pytest.mark.unit
class TestDeleteDenialIsAnHttp403ForEveryTableState:
    """An object-unauthorized caller holding the DELETE route gets one answer, whatever is stored."""

    @pytest.mark.parametrize("state", sorted(TABLE_STATES))
    def test_denial_is_403_and_names_nothing(self, state):
        response, tag_type_table, tag_table = _delete_state(state)

        assert response["statusCode"] == 403
        # Neither the tag type nor its scope may appear anywhere in the response.
        rendered = json.dumps(response)
        assert TAG_TYPE_NAME not in rendered
        assert DATABASE_ID not in rendered
        # Nothing was read, so no existence or in-use state could have been consulted.
        tag_type_table.get_item.assert_not_called()
        tag_type_table.query.assert_not_called()
        tag_table.scan.assert_not_called()
        tag_type_table.delete_item.assert_not_called()
        # The decision was taken, and taken about the requested object.
        assert ("tagType", DATABASE_ID, TAG_TYPE_NAME, "DELETE") in _Enforcer.decisions()

    def test_the_three_states_are_indistinguishable_on_the_wire(self):
        """The oracle itself, stated over the whole response rather than one field."""
        seen = {
            (r["statusCode"], r["body"])
            for r in (_delete_state(state)[0] for state in TABLE_STATES)
        }
        assert len(seen) == 1, seen

    @pytest.mark.parametrize("state", sorted(TABLE_STATES))
    def test_an_absent_identity_is_also_403_and_reads_nothing(self, state):
        """The Tier-1 arm. An empty token list is refused before the route handler runs, so this is
        the mitigation that keeps the pre-authorization reads unreachable from the API."""
        response, tag_type_table, tag_table = _delete_state(state, claims=NO_IDENTITY)

        assert response["statusCode"] == 403
        tag_type_table.get_item.assert_not_called()
        tag_table.scan.assert_not_called()
        tag_type_table.delete_item.assert_not_called()
        assert _Enforcer.objects == []

    def test_a_denial_in_the_global_scope_is_403_too(self):
        """Parameter variation: no `databaseId` query string, so the default GLOBAL scope is the one
        authorized. A `None` query string is also the shape a REST request with no parameters sends."""
        response, tag_type_table, _tag_table = _delete_state(
            "exists_unreferenced", database_id=None
        )

        assert response["statusCode"] == 403
        tag_type_table.get_item.assert_not_called()
        assert ("tagType", "GLOBAL", TAG_TYPE_NAME, "DELETE") in _Enforcer.decisions()


@pytest.mark.unit
class TestAnAuthorizedDeleteStillGetsEachRealOutcome:
    """Positive controls. A handler that answered 403 to everything would satisfy the class above."""

    def test_unreferenced_tag_type_is_deleted_with_200(self):
        response, tag_type_table, _tag_table = _delete_state(
            "exists_unreferenced", object_verdict=True
        )

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["success"] is True
        assert body["tagTypeName"] == TAG_TYPE_NAME
        assert tag_type_table.delete_item.call_args.kwargs["Key"] == {
            "databaseId": DATABASE_ID,
            "tagTypeName": TAG_TYPE_NAME,
        }

    def test_in_use_tag_type_is_still_refused_with_400(self):
        """The in-use signal must still reach a caller entitled to it -- authorizing first must not
        collapse every outcome into 403."""
        response, tag_type_table, _tag_table = _delete_state(
            "exists_and_in_use", object_verdict=True
        )

        assert response["statusCode"] == 400
        assert "in use" in json.loads(response["body"])["message"]
        tag_type_table.delete_item.assert_not_called()

    def test_absent_tag_type_is_still_404(self):
        response, tag_type_table, _tag_table = _delete_state("absent", object_verdict=True)

        assert response["statusCode"] == 404
        tag_type_table.delete_item.assert_not_called()

    def test_a_global_tag_type_is_deleted_from_the_global_partition(self):
        """Parameter variation: the default scope must be the partition written to, not an empty
        value, and not the scope of some same-named row in another database."""
        response, tag_type_table, _tag_table = _invoke_delete(
            _delete_event(database_id=None), AUTHENTICATED, True, GLOBAL_ROW, []
        )

        assert response["statusCode"] == 200
        assert tag_type_table.delete_item.call_args.kwargs["Key"] == {
            "databaseId": "GLOBAL",
            "tagTypeName": TAG_TYPE_NAME,
        }
        assert tag_type_table.get_item.call_args.kwargs["Key"]["databaseId"] == "GLOBAL"


def _typed(row):
    """One DynamoDB low-level item. The scan branch runs its rows through TypeDeserializer."""
    return {k: {"S": v} for k, v in row.items()}


TAG_TYPE_TABLE_NAME = "tag-type-table"
TAG_TABLE_NAME = "tag-table"


def _list_unscoped(claims, object_verdict, rows):
    """`GET /tag-types` with neither `databaseId` nor `scope` -- the paginated-scan branch.

    Both reads go through one paginator, so the pages are routed on `TableName` rather than on call
    order: a read the handler makes in a different order still resolves to the right table, and a
    read of a table nobody stubbed names itself instead of returning an empty page.
    """
    def paginate(**kwargs):
        table = kwargs.get("TableName")
        if table == TAG_TYPE_TABLE_NAME:
            body = {"Items": [_typed(r) for r in rows]}
        elif table == TAG_TABLE_NAME:
            body = {"Items": []}
        else:
            raise _UnexpectedRead(f"paginated read of unexpected TableName {table!r}")
        page = MagicMock()
        page.build_full_result.return_value = body
        return page

    client = MagicMock()
    client.get_paginator.return_value.paginate.side_effect = paginate

    _Enforcer.reset(object_verdict)
    with patch.object(svc, "dynamodb_client", client), patch.object(
        svc, "tag_type_table_name", TAG_TYPE_TABLE_NAME
    ), patch.object(svc, "tag_table_name", TAG_TABLE_NAME), patch.object(
        svc, "CasbinEnforcer", _Enforcer
    ):
        return svc.get_tag_types(
            {"maxItems": 100, "pageSize": 100, "startingToken": None}, claims
        )


@pytest.mark.unit
class TestUnscopedListingFiltersOnTheScanBranch:
    """The `?scope=all` / no-scope selection is a separate read path from the partition queries."""

    def test_authorized_listing_returns_every_scope(self):
        """Positive control: an empty list must not be the answer for everybody, and the internal
        `object__type` annotation must not be handed back to the caller."""
        result = _list_unscoped(AUTHENTICATED, True, [SCOPED_ROW, GLOBAL_ROW])

        assert {t["databaseId"] for t in result["Items"]} == {DATABASE_ID, "GLOBAL"}
        assert all("object__type" not in t for t in result["Items"])

    def test_empty_tokens_returns_no_tag_types(self):
        result = _list_unscoped(NO_IDENTITY, True, [SCOPED_ROW, GLOBAL_ROW])

        assert result["Items"] == []
        # With no identity the enforcer is never consulted, so nothing can be appended.
        assert _Enforcer.objects == []

    def test_denied_listing_returns_no_tag_types(self):
        """Negative control for the filter: each row must be dropped by its own decision."""
        result = _list_unscoped(AUTHENTICATED, False, [SCOPED_ROW, GLOBAL_ROW])

        assert result["Items"] == []
        assert {
            ("tagType", DATABASE_ID, TAG_TYPE_NAME, "GET"),
            ("tagType", "GLOBAL", TAG_TYPE_NAME, "GET"),
        } <= _Enforcer.decisions()
