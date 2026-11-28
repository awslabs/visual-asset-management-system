# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unarchive decides a missing asset as it decides an archived one.

``unarchive_asset`` reads the asset from the archived partition (``<databaseId>#deleted``) and then the
live one, and authorizes before it reports existence or archive state. When neither read finds a record,
the request's identifiers stand in for it. That stand-in carried the live database id, so a caller whose
rule names only the live database was allowed on a missing id and told ``400`` "Asset not found", while an
archived id, decided on its stored ``<databaseId>#deleted`` record, answered ``403``: the status reported
whether an archived asset existed. The stand-in now carries the archived id, as the permanent delete's
does, so such a caller gets ``403`` for both.

Only the missing case changes. A found record is decided on itself: an archived asset on its
``<databaseId>#deleted`` id, a live one on its live id, which still answers ``400`` "Asset is not
archived". A caller allowed on archived assets, which is what an unarchive needs to succeed, still learns
the id is unknown. The shipped roles that reach this route (the seeded ``admin`` role and the
``database-admin`` template) hold that permission, and ``database-user`` is refused the route at Tier 1
(``tests/handlers/auth/test_permission_templates_unarchive_stand_in.py``).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.handlers.assets.test_assetService_existence_disclosure import _invoke, _wire
from tests.handlers.assets.test_assetService_update_tag_scope import _existing_asset

_DB = "db-a"
_ARCHIVED_DB = f"{_DB}#deleted"
_ASSET = "asset-1"
_USER = "u1"
_BODY = {"confirmUnarchive": True}

ASSET_NOT_FOUND = "Asset not found"
NOT_ARCHIVED = "Asset is not archived. Only archived assets can be unarchived."


def _archived_asset():
    asset = _existing_asset(_ARCHIVED_DB)
    asset["status"] = "archived"
    return asset


def _live_asset():
    return _existing_asset(_DB)


_ASSETS = {"missing": lambda: None, "archived": _archived_asset, "live": _live_asset}


class _DatabaseScopedEnforcer:
    """Allows an action only on the listed database ids, as a constraint naming them would.

    An archived record lives under "<databaseId>#deleted" and is decided on that id and on the
    database id together, so it is allowed only when both are listed.
    """

    def __init__(self, allowed_databases):
        self.allowed = set(allowed_databases)
        self.constructions = []
        self.calls = []

    def factory(self, claims_and_roles):
        self.constructions.append(claims_and_roles)
        return self

    def enforce(self, obj, action):
        self.calls.append({"object": dict(obj), "action": action})
        database_id = str(obj.get("databaseId", ""))
        return {database_id, database_id.replace("#deleted", "")} <= self.allowed


def _unarchive(asset, enforcer_factory, tokens=(_USER,)):
    """One unarchive request through handle_put_request, with ``asset`` stored (or None)."""
    m, _, undo = _wire(None, tokens=tokens)
    stored = dict(asset) if asset else None
    # Each partition read finds the record only in the partition it is stored under.
    m.get_asset_details = MagicMock(
        side_effect=lambda database_id, asset_id, showArchived=False: (
            dict(stored) if stored and stored["databaseId"] == database_id else None
        )
    )
    m.asset_table.get_item.return_value = {}
    enforcer_patch = patch.object(m, "CasbinEnforcer", enforcer_factory)
    enforcer_patch.start()
    try:
        response = _invoke(m, "handle_put_request", "PUT", "/unarchiveAsset", _BODY)
    finally:
        enforcer_patch.stop()
        undo()
    return response, m


def _message(response):
    return json.loads(response["body"])["message"]


@pytest.mark.unit
class TestACallerAllowedOnlyOnLiveAssets:
    """A rule that names the live database and not its archived records."""

    @pytest.mark.parametrize("label", ["missing", "archived"])
    def test_a_missing_id_is_refused_as_an_archived_one_is(self, label):
        enforcer = _DatabaseScopedEnforcer({_DB})
        response, m = _unarchive(_ASSETS[label](), enforcer.factory)

        assert response["statusCode"] == 403, (
            f"{label} asset: answered {response['statusCode']} to a caller allowed only on the live "
            f"database, so a missing id can be told from an archived one: {response}"
        )
        assert _message(response) == "Not Authorized"
        m.asset_table.put_item.assert_not_called()
        m.asset_table.delete_item.assert_not_called()

    def test_the_missing_asset_is_decided_on_the_archived_database_id(self):
        enforcer = _DatabaseScopedEnforcer({_DB})
        _unarchive(None, enforcer.factory)

        evaluated = {(c["action"], c["object"].get("databaseId"), c["object"].get("assetId"))
                     for c in enforcer.calls}
        assert ("PUT", _ARCHIVED_DB, _ASSET) in evaluated, sorted(evaluated)
        assert ("PUT", _DB, _ASSET) not in evaluated, sorted(evaluated)

    def test_a_live_asset_is_still_decided_on_its_own_record(self):
        """A found live asset is evaluated on its live id, so the caller is told it is not archived."""
        enforcer = _DatabaseScopedEnforcer({_DB})
        response, m = _unarchive(_live_asset(), enforcer.factory)

        assert response["statusCode"] == 400, response
        assert _message(response).endswith(NOT_ARCHIVED), response
        assert {c["object"]["databaseId"] for c in enforcer.calls} == {_DB}
        m.asset_table.put_item.assert_not_called()


@pytest.mark.unit
class TestACallerAllowedOnArchivedAssets:
    """Positive controls: refusing everyone would pass the class above on its own."""

    def test_a_missing_id_is_still_reported_missing(self):
        enforcer = _DatabaseScopedEnforcer({_DB, _ARCHIVED_DB})
        response, m = _unarchive(None, enforcer.factory)

        assert response["statusCode"] == 400, response
        assert _message(response).endswith(ASSET_NOT_FOUND), response
        assert enforcer.calls, "the not-found answer was produced with nothing authorized"
        m.asset_table.put_item.assert_not_called()

    def test_an_archived_asset_is_unarchived(self):
        enforcer = _DatabaseScopedEnforcer({_DB, _ARCHIVED_DB})
        response, m = _unarchive(_archived_asset(), enforcer.factory)

        assert response["statusCode"] == 200, response
        m.asset_table.put_item.assert_called_once()
        assert m.asset_table.put_item.call_args.kwargs["Item"]["databaseId"] == _DB

    def test_a_live_asset_is_reported_not_archived(self):
        enforcer = _DatabaseScopedEnforcer({_DB, _ARCHIVED_DB})
        response, _ = _unarchive(_live_asset(), enforcer.factory)

        assert response["statusCode"] == 400, response
        assert _message(response).endswith(NOT_ARCHIVED), response


@pytest.mark.unit
class TestAnEmptyTokenListDeniesFirst:

    @pytest.mark.parametrize("label", sorted(_ASSETS))
    def test_no_identity_is_refused_without_consulting_casbin(self, label):
        enforcer = _DatabaseScopedEnforcer({_DB, _ARCHIVED_DB})
        response, m = _unarchive(_ASSETS[label](), enforcer.factory, tokens=())

        assert response["statusCode"] == 403, response
        assert enforcer.constructions == [], "CasbinEnforcer was constructed for an empty token list"
        assert enforcer.calls == []
        m.asset_table.put_item.assert_not_called()
