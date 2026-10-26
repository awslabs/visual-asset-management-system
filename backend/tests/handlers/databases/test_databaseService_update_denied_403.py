# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`PUT /database/{databaseId}` answers an object-level denial with 403, not 400 (issue #785).

A caller may hold the api route (Tier 1) and still be denied `PUT` on the database object itself
(Tier 2) -- the shipped Database User template grants exactly that combination. The update was
refused and nothing was written, but the handler mapped the denial to `general_error` (400), so the
web UI, `vamscli` and the MCP server reported a permission refusal as an invalid request.

Each denial is paired with the authorized case that must still answer 200, because "403" alone is
satisfied by a handler that refuses everything, and each case asserts the table write as well as
the status, because a 403 that still wrote would be worse than the original defect.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.databases import databaseService as svc

DATABASE_ID = "factory-db"

AUTHENTICATED = {"tokens": ["some-user"], "roles": ["database-user"], "mfaEnabled": False}
NO_IDENTITY = {"tokens": [], "roles": [], "mfaEnabled": False}


class _Enforcer:
    """Allows the api route (Tier 1) and answers the object check (Tier 2) with `object_verdict`."""

    object_verdict = True
    object_calls = []

    def __init__(self, claims_and_roles):
        pass

    def enforceAPI(self, event):
        return True

    def enforce(self, obj, act):
        _Enforcer.object_calls.append((obj.get("object__type"), obj.get("databaseId"), act))
        return _Enforcer.object_verdict


def _rest_put_event(body=None):
    """A REST API (v1) proxy event for `PUT /database/{databaseId}`."""
    return {
        "httpMethod": "PUT",
        "path": f"/database/{DATABASE_ID}",
        "resource": "/database/{databaseId}",
        "requestContext": {"requestId": "test-request-id", "authorizer": {}},
        "pathParameters": {"databaseId": DATABASE_ID},
        "queryStringParameters": None,
        "headers": {"Authorization": "Bearer test-token"},
        "body": json.dumps(body if body is not None else {"description": "unchanged"}),
    }


@pytest.fixture
def table(monkeypatch, real_to_update_expr):
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {"databaseId": DATABASE_ID, "description": "before", "defaultBucketId": "bucket-1"}
    }
    resource = MagicMock()
    resource.Table.return_value = table
    monkeypatch.setattr(svc, "dynamodb", resource)
    monkeypatch.setattr(svc, "to_update_expr", real_to_update_expr, raising=False)
    return table


@pytest.fixture
def enforcer(monkeypatch):
    _Enforcer.object_verdict = True
    _Enforcer.object_calls = []
    monkeypatch.setattr(svc, "CasbinEnforcer", _Enforcer)
    return _Enforcer


def _claims(monkeypatch, claims):
    monkeypatch.setattr(svc, "request_to_claims", lambda event: claims)


@pytest.mark.unit
class TestDatabaseUpdateDenialIs403:
    def test_authorized_update_answers_200_and_writes(self, monkeypatch, table, enforcer):
        """Positive control: the permitted case still updates."""
        _claims(monkeypatch, AUTHENTICATED)

        response = svc.lambda_handler(_rest_put_event(), MagicMock())

        assert response["statusCode"] == 200
        table.update_item.assert_called_once()
        assert ("database", DATABASE_ID, "PUT") in enforcer.object_calls

    def test_object_level_denial_answers_403_and_does_not_write(self, monkeypatch, table, enforcer):
        _claims(monkeypatch, AUTHENTICATED)
        enforcer.object_verdict = False

        response = svc.lambda_handler(_rest_put_event(), MagicMock())

        assert response["statusCode"] == 403
        assert json.loads(response["body"]) == {"message": "Not Authorized"}
        table.update_item.assert_not_called()
        table.put_item.assert_not_called()
        assert ("database", DATABASE_ID, "PUT") in enforcer.object_calls

    def test_missing_identity_at_the_update_answers_403_and_does_not_write(self, table, enforcer):
        """The service's own empty-token guard maps to 403 as well. Tier 1 already refuses an empty
        token list in `lambda_handler`, so the guard is reached through the update handler."""
        response = svc.update_database_handler(
            _rest_put_event(), {"databaseId": DATABASE_ID}, {"description": "unchanged"}, NO_IDENTITY
        )

        assert response["statusCode"] == 403
        table.update_item.assert_not_called()
        assert enforcer.object_calls == []

    def test_a_missing_database_is_not_reported_as_a_denial(self, monkeypatch, table, enforcer):
        """Negative control on the mapping: other update failures keep their own status."""
        _claims(monkeypatch, AUTHENTICATED)
        table.get_item.return_value = {}

        response = svc.lambda_handler(_rest_put_event(), MagicMock())

        assert response["statusCode"] == 400
        assert "Database not found" in json.loads(response["body"])["message"]
        table.update_item.assert_not_called()
