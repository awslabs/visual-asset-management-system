# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`PUT` and `DELETE /database/{databaseId}` decide Tier 2 before they report whether the database exists.

A caller who may call the route (Tier 1) but is refused the database (Tier 2) gets the same 403 and body
whether or not the database exists. The decision for a missing database is made on the requested id.
An authorized caller is still told a missing database is not found, with the answer each route gave
before (update `400` "Database not found", delete `404` "Record not found"), and nothing is written.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.databases import databaseService as svc

_OPEN = "dbopen"      # a database the caller is granted
_CLOSED = "dbclosed"  # a database the caller is refused
AUTHENTICATED = {"tokens": ["some-user"], "roles": ["database-user"], "mfaEnabled": False}


class _Enforcer:
    """Allows the api route (Tier 1); grants an object (Tier 2) when its databaseId is in ``granted``."""

    granted = {_OPEN}
    decided = []

    def __init__(self, claims_and_roles):
        pass

    def enforceAPI(self, event):
        return True

    def enforce(self, obj, act):
        _Enforcer.decided.append((dict(obj), act))
        return obj.get("databaseId") in _Enforcer.granted


def _event(method, database_id, body=None):
    """A REST API (v1) proxy event for `/database/{databaseId}`."""
    return {
        "httpMethod": method,
        "path": f"/database/{database_id}",
        "resource": "/database/{databaseId}",
        "requestContext": {"requestId": "test-request-id", "authorizer": {}},
        "pathParameters": {"databaseId": database_id},
        "queryStringParameters": None,
        "headers": {"Authorization": "Bearer test-token"},
        "body": json.dumps(body) if body is not None else None,
    }


@pytest.fixture(autouse=True)
def _update_expression(monkeypatch, real_to_update_expr):
    monkeypatch.setattr(svc, "to_update_expr", real_to_update_expr, raising=False)


class _Run:
    def __init__(self, monkeypatch, *database_ids):
        self.rows = {database_id: {"databaseId": database_id, "description": "d"} for database_id in database_ids}
        self.table = MagicMock()
        self.table.get_item.side_effect = lambda Key: (
            {"Item": dict(self.rows[Key["databaseId"]])} if Key["databaseId"] in self.rows else {})
        resource = MagicMock()
        resource.Table.return_value = self.table
        monkeypatch.setattr(svc, "dynamodb", resource)
        monkeypatch.setattr(svc, "CasbinEnforcer", _Enforcer)
        monkeypatch.setattr(svc, "request_to_claims", lambda event: AUTHENTICATED)
        self.content_checks = []
        for name in ("check_workflows", "check_pipelines", "check_assets"):
            monkeypatch.setattr(svc, name, lambda database_id, name=name: self.content_checks.append(name) or False)
        _Enforcer.decided = []

    def call(self, method, database_id):
        body = {"description": "after"} if method == "PUT" else None
        return svc.lambda_handler(_event(method, database_id, body), MagicMock())

    def wrote(self):
        return bool(self.table.update_item.called or self.table.put_item.called or self.table.delete_item.called)


def _answer(response):
    return response["statusCode"], json.loads(response["body"]).get("message")


@pytest.mark.unit
@pytest.mark.parametrize("method", ["PUT", "DELETE"])
class TestDatabaseUpdateAndDelete:
    def test_a_refused_caller_gets_the_same_answer_for_a_missing_database(self, monkeypatch, method):
        existing_run = _Run(monkeypatch, _CLOSED)
        existing = existing_run.call(method, _CLOSED)
        assert existing["statusCode"] == 403, existing
        assert not existing_run.wrote()

        missing_run = _Run(monkeypatch)
        missing = missing_run.call(method, _CLOSED)
        assert _answer(missing) == _answer(existing)
        assert _Enforcer.decided == [({"databaseId": _CLOSED, "object__type": "database"}, method)]
        assert not missing_run.wrote()
        assert missing_run.content_checks == []

    def test_an_authorized_caller_is_told_the_database_is_missing(self, monkeypatch, method):
        run = _Run(monkeypatch)
        response = run.call(method, _OPEN)
        expected = (400, "VAMS General Error: Database not found") if method == "PUT" else (404, "Record not found")
        assert _answer(response) == expected
        assert _Enforcer.decided == [({"databaseId": _OPEN, "object__type": "database"}, method)]
        assert not run.wrote()
        assert run.content_checks == []

    def test_an_authorized_caller_on_an_existing_database_succeeds(self, monkeypatch, method):
        run = _Run(monkeypatch, _OPEN)
        response = run.call(method, _OPEN)
        assert response["statusCode"] == 200, response
        decided, action = _Enforcer.decided[0]
        assert (decided["object__type"], decided["description"], action) == ("database", "d", method)
        assert run.wrote()
