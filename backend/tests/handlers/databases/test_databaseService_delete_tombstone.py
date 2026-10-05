# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`delete_database` moves the record to `<databaseId>#deleted` without the Casbin type marker.

The handler adds `object__type` to the record it authorizes. The tombstone written afterwards must
not carry it, the same rule `update_database` follows for the live record.
"""

import pytest
from unittest.mock import MagicMock

from backend.backend.handlers.databases import databaseService as svc

DATABASE_ID = "factory-db"
AUTHENTICATED = {"tokens": ["some-user"], "roles": ["admin"], "mfaEnabled": False}


class _Enforcer:
    """Stands in for CasbinEnforcer, recording a copy of every document it is asked about."""

    calls = []
    verdict = True

    def __init__(self, claims_and_roles):
        pass

    def enforce(self, obj, act):
        # Copy: the handler keeps mutating the same dict after the check.
        _Enforcer.calls.append((dict(obj), act))
        return _Enforcer.verdict


@pytest.fixture
def database_table(monkeypatch):
    _Enforcer.calls = []
    _Enforcer.verdict = True
    monkeypatch.setattr(svc, "CasbinEnforcer", _Enforcer)
    for check in ("check_workflows", "check_pipelines", "check_assets"):
        monkeypatch.setattr(svc, check, lambda database_id: False)
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {"databaseId": DATABASE_ID, "description": "kept", "defaultBucketId": "bucket-1"}
    }
    resource = MagicMock()
    resource.Table.return_value = table
    monkeypatch.setattr(svc, "dynamodb", resource)
    return table


@pytest.mark.unit
class TestDeleteDatabaseTombstone:
    def test_tombstone_does_not_store_the_casbin_type_marker(self, database_table):
        result = svc.delete_database(DATABASE_ID, AUTHENTICATED)

        assert result.statusCode == 200
        database_table.put_item.assert_called_once()
        tombstone = database_table.put_item.call_args.kwargs["Item"]
        assert tombstone["databaseId"] == DATABASE_ID + "#deleted"
        assert tombstone["description"] == "kept"
        assert "object__type" not in tombstone
        database_table.delete_item.assert_called_once_with(Key={"databaseId": DATABASE_ID})

    def test_the_delete_is_still_authorized_as_a_database(self, database_table):
        svc.delete_database(DATABASE_ID, AUTHENTICATED)

        assert [
            (doc.get("object__type"), doc.get("databaseId"), act) for doc, act in _Enforcer.calls
        ] == [("database", DATABASE_ID, "DELETE")]

    def test_a_denied_delete_writes_nothing(self, database_table):
        _Enforcer.verdict = False

        result = svc.delete_database(DATABASE_ID, AUTHENTICATED)

        assert result.statusCode == 403
        database_table.put_item.assert_not_called()
        database_table.delete_item.assert_not_called()
