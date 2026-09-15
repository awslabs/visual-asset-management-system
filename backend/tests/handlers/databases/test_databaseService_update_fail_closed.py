# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-143, fifth site: `update_database` must fail closed on a missing identity.

The Tier-2 check sat inside `if claims_and_roles and len(claims_and_roles["tokens"]) > 0:` with no
`else` that denies -- the shape backend/CLAUDE.md Rule 4 forbids by name -- so the whole block was
skipped and execution fell through to the `put_item` below. Two inputs reached it: an empty token
list, and the `claims_and_roles=None` default, which the truthiness test in the same condition let
through as well. Both are covered here, because a presence check on the token list alone leaves the
default-parameter path open.

## Why these tests assert who was consulted, not only the verdict

The root conftest replaces `handlers.authz` with a stand-in whose `CasbinEnforcer` is a MagicMock,
and a MagicMock's `enforce()` returns a truthy Mock, so a test written against the verdict alone can
pass for the wrong reason. `_EnforcerSpy` records every construction and every `enforce()` call, so
the missing-identity tests assert the property that matters -- Casbin was never consulted and nothing
was written. Each denial is paired with the authorized case that must still write, because "denied"
alone is satisfied by a handler that denies everything.
"""

import pytest
from unittest.mock import MagicMock

from backend.backend.handlers.databases import databaseService as svc

DATABASE_ID = "factory-db"

AUTHENTICATED = {"tokens": ["some-user"], "roles": ["admin"], "mfaEnabled": False}
NO_IDENTITY = {"tokens": [], "roles": [], "mfaEnabled": False}


def _stored_database():
    return {
        "databaseId": DATABASE_ID,
        "description": "before",
        "defaultBucketId": "bucket-1",
    }


class _EnforcerSpy:
    """Stands in for CasbinEnforcer, recording every construction and every enforce() call."""

    constructions = []
    calls = []
    verdict = True

    def __init__(self, claims_and_roles):
        _EnforcerSpy.constructions.append(claims_and_roles)

    def enforce(self, obj, act):
        # Copy: the caller keeps mutating the same dict, so a reference would record the later state.
        _EnforcerSpy.calls.append((dict(obj), act))
        return _EnforcerSpy.verdict

    @classmethod
    def reset(cls, verdict=True):
        cls.constructions = []
        cls.calls = []
        cls.verdict = verdict

    @classmethod
    def decisions(cls):
        """The SET of (object__type, databaseId, action) decisions Casbin was asked for.

        A set asserted with `in` rather than an equality on a list: a handler that authorizes the same
        object twice is strictly safer and must not turn this red, while one that drops the check
        disappears from the set and does.
        """
        return {
            (doc.get("object__type"), doc.get("databaseId"), act) for doc, act in cls.calls
        }


@pytest.fixture
def spy(monkeypatch):
    _EnforcerSpy.reset()
    monkeypatch.setattr(svc, "CasbinEnforcer", _EnforcerSpy)
    return _EnforcerSpy


@pytest.fixture
def database_table(monkeypatch):
    """The database row `update_database` reads, and the write it must not reach."""
    table = MagicMock()
    table.get_item.return_value = {"Item": _stored_database()}
    resource = MagicMock()
    resource.Table.return_value = table
    monkeypatch.setattr(svc, "dynamodb", resource)
    return table


@pytest.mark.unit
class TestUpdateDatabaseFailsClosed:
    def test_authorized_update_writes_and_consults_casbin(self, spy, database_table):
        """Positive control: the permitted case must still write, and the document Casbin receives
        must be typed `database` and carry the database it is scoped by."""
        result = svc.update_database(DATABASE_ID, {"description": "after"}, AUTHENTICATED)

        assert isinstance(result, svc.UpdateDatabaseResponseModel)
        database_table.put_item.assert_called_once()
        assert ("database", DATABASE_ID, "PUT") in spy.decisions()

    def test_empty_tokens_denies_without_consulting_casbin(self, spy, database_table):
        with pytest.raises(svc.VAMSGeneralErrorResponse, match="Access denied"):
            svc.update_database(DATABASE_ID, {"description": "after"}, NO_IDENTITY)

        database_table.put_item.assert_not_called()
        # The property: with no identity there is nothing to authorize against, so the enforcer must
        # not be built or called at all.
        assert spy.constructions == []
        assert spy.calls == []

    def test_absent_claims_denies_without_consulting_casbin(self, spy, database_table):
        """`claims_and_roles` defaults to None, so a caller that omits it must be denied too -- the
        token-list check alone never sees this path."""
        with pytest.raises(svc.VAMSGeneralErrorResponse, match="Access denied"):
            svc.update_database(DATABASE_ID, {"description": "after"})

        database_table.put_item.assert_not_called()
        assert spy.constructions == []
        assert spy.calls == []

    def test_denied_update_does_not_write(self, spy, database_table):
        """Negative control for the gate itself: a real Casbin denial must also block the write."""
        spy.reset(verdict=False)
        with pytest.raises(svc.VAMSGeneralErrorResponse, match="Access denied"):
            svc.update_database(DATABASE_ID, {"description": "after"}, AUTHENTICATED)

        database_table.put_item.assert_not_called()
        assert ("database", DATABASE_ID, "PUT") in spy.decisions()
