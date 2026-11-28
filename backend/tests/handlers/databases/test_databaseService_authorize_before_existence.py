# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`PUT` and `DELETE /database/{databaseId}` decide Tier 2 before they report a missing database.

A caller who may call the route (Tier 1) but is refused the database (Tier 2) gets the same 403 and
body whether or not the database exists: the decision is made on the record, or on the requested id
when there is none. Only an authorized caller is told the database is missing, with the answer it
got before (`PUT`: 400 "Database not found"; `DELETE`: 404 "Record not found").
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.databases import databaseService as svc

OPEN = "open-db"      # a database the caller is granted
CLOSED = "closed-db"  # a database the caller is refused
AUTHENTICATED = {"tokens": ["some-user"], "roles": ["database-user"], "mfaEnabled": False}


class _Enforcer:
    """Grants an object when its databaseId is in ``granted``, recording each decision."""

    granted = {OPEN}
    calls = []

    def __init__(self, claims_and_roles):
        pass

    def enforce(self, obj, act):
        _Enforcer.calls.append((dict(obj), act))
        return obj.get("databaseId") in _Enforcer.granted


class _Table:
    """The database table: get_item finds the stored rows; writes are recorded."""

    def __init__(self, *database_ids):
        self.rows = {database_id: {"databaseId": database_id, "description": "before"}
                     for database_id in database_ids}
        self.update_item = MagicMock()
        self.put_item = MagicMock()
        self.delete_item = MagicMock()

    def get_item(self, Key, **_kwargs):
        # The delete also reads the compliance tables through the same resource; they hold nothing
        if set(Key) != {"databaseId"}:
            return {}
        row = self.rows.get(Key["databaseId"])
        return {"Item": dict(row)} if row else {}


@pytest.fixture
def run(monkeypatch, real_to_update_expr):
    """Returns a callable that runs a handler against a database table holding ``database_ids``."""
    _Enforcer.granted = {OPEN}
    _Enforcer.calls = []
    monkeypatch.setattr(svc, "CasbinEnforcer", _Enforcer)
    monkeypatch.setattr(svc, "to_update_expr", real_to_update_expr, raising=False)
    monkeypatch.setattr(svc, "log_action", MagicMock())
    for check in ("check_workflows", "check_pipelines", "check_assets"):
        monkeypatch.setattr(svc, check, lambda database_id: False)

    def _run(method, database_id, *database_ids):
        table = _Table(*database_ids)
        resource = MagicMock()
        resource.Table.return_value = table
        monkeypatch.setattr(svc, "dynamodb", resource)
        event = {"pathParameters": {"databaseId": database_id}}
        if method == "PUT":
            response = svc.update_database_handler(event, {"databaseId": database_id},
                                                   {"description": "after"}, AUTHENTICATED)
        else:
            response = svc.delete_database_handler(event, {"databaseId": database_id}, AUTHENTICATED)
        return response, table

    return _run


def _answer(response):
    return response["statusCode"], json.loads(response["body"]).get("message")


@pytest.mark.unit
class TestDatabaseRoutesAuthorizeBeforeExistence:
    @pytest.mark.parametrize("method", ["PUT", "DELETE"])
    def test_a_refused_caller_gets_the_same_answer_for_a_missing_database(self, run, method):
        existing, existing_table = run(method, CLOSED, CLOSED)
        missing, missing_table = run(method, CLOSED)
        assert existing["statusCode"] == 403, existing
        assert _answer(missing) == _answer(existing)
        assert _Enforcer.calls[-1] == ({"databaseId": CLOSED, "object__type": "database"}, method)
        for table in (existing_table, missing_table):
            table.update_item.assert_not_called()
            table.put_item.assert_not_called()
            table.delete_item.assert_not_called()

    def test_an_authorized_update_is_told_the_database_is_missing(self, run):
        response, table = run("PUT", OPEN)
        assert _answer(response) == (400, "VAMS General Error: Database not found")
        assert _Enforcer.calls == [({"databaseId": OPEN, "object__type": "database"}, "PUT")]
        table.update_item.assert_not_called()

    def test_an_authorized_delete_is_told_the_database_is_missing(self, run):
        response, table = run("DELETE", OPEN)
        assert _answer(response) == (404, "Record not found")
        assert _Enforcer.calls == [({"databaseId": OPEN, "object__type": "database"}, "DELETE")]
        table.put_item.assert_not_called()
        table.delete_item.assert_not_called()

    def test_an_authorized_update_of_an_existing_database_writes(self, run):
        response, table = run("PUT", OPEN, OPEN)
        assert response["statusCode"] == 200, response
        table.update_item.assert_called_once()
        decided, action = _Enforcer.calls[0]
        assert (decided["description"], decided["object__type"], action) == ("before", "database", "PUT")
        assert "object__type" not in str(table.update_item.call_args)

    def test_an_authorized_delete_of_an_existing_database_deletes(self, run):
        response, table = run("DELETE", OPEN, OPEN)
        assert _answer(response) == (200, "Database deleted")
        tombstone = table.put_item.call_args.kwargs["Item"]
        assert tombstone == {"databaseId": f"{OPEN}#deleted", "description": "before"}
        table.delete_item.assert_called_once_with(Key={"databaseId": OPEN})
