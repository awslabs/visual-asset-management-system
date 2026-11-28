# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What the shipped roles decide for the objects an unarchive request evaluates.

``unarchive_asset`` authorizes PUT on the stored record, or, when no record was found, on a stand-in
built by ``asset_authorization_object``. The stand-in now carries the archived ``<databaseId>#deleted``
id (``include_archived=True``), as the permanent delete's does, so a missing id is decided as an
archived one. These tests evaluate that stand-in, the earlier live-id stand-in, and the stored archived
and live records with each shipped constraint set through the real policy generation and Casbin, with
only the two table reads stubbed:

* the seeded ``admin`` role and the ``database-admin`` template reach the unarchive route and allow PUT
  on every one of these objects, so their answers do not change (``400`` "Asset not found" for a
  missing id, ``200`` for an archived asset, ``400`` "Asset is not archived" for a live one);
* the ``database-user`` template is refused the unarchive route at Tier 1, and its asset grants name
  only the live database, so they refuse the new stand-in where they allowed the earlier one.

The handler's answers for each grant set are in
``tests/handlers/assets/test_assetService_unarchive_authorize_before_existence.py``.
"""

import re
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module  # noqa: E402
from backend.backend.common.apiRoutes import API_UNARCHIVE_ASSET, PUT  # noqa: E402
from backend.backend.common.dynamodb import asset_authorization_object  # noqa: E402
from backend.tests.handlers.authz import shipped_constraints  # noqa: E402

USER_ID = "unarchive-user"
DB = "dbX"
ASSET = "a1"


def _record(database_id, **extra):
    return {"databaseId": database_id, "assetId": ASSET, "assetName": "Part", "assetType": ".glb",
            "tags": [], **extra}


# The objects unarchive_asset hands to Casbin, by the case that produces them.
OBJECTS = {
    "missing id (stand-in now)": asset_authorization_object(None, DB, ASSET, include_archived=True),
    "missing id (stand-in before)": asset_authorization_object(None, DB, ASSET),
    "archived asset": asset_authorization_object(_record(DB + "#deleted", status="archived"), DB, ASSET),
    "live asset": asset_authorization_object(_record(DB), DB, ASSET),
}


def _seeded_admin():
    return "admin", shipped_constraints.seeded_constraints("admin")


def _template(name):
    role = f"{name.split('.')[0]}-role"
    return role, shipped_constraints.template_constraints(name, role, {"DATABASE_ID": DB})


ROLES = {
    "seeded admin": _seeded_admin,
    "database-admin template": lambda: _template("database-admin.json"),
    "database-user template": lambda: _template("database-user.json"),
}

# PUT on each object, by role. Only the database-user template's answer for a missing id changes.
EXPECTED = {
    "seeded admin": {name: True for name in OBJECTS},
    "database-admin template": {name: True for name in OBJECTS},
    "database-user template": {
        "missing id (stand-in now)": False,
        "missing id (stand-in before)": True,
        "archived asset": False,
        "live asset": True,
    },
}

REACHES_THE_ROUTE = {"seeded admin": True, "database-admin template": True, "database-user template": False}


@pytest.fixture
def enforcer_for():
    """Build the real CasbinEnforcer for a (role, constraints) pair.

    The per-user policy and enforcer caches are cleared on every build, the two table reads are the
    only stubs, and the denial audit writer is replaced. Both caches are restored afterwards.
    """
    state = {"role": None, "constraints": []}
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map

    def build(role_and_constraints):
        state["role"], state["constraints"] = role_and_constraints
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        return authz_module.CasbinEnforcer(
            {"tokens": [USER_ID], "roles": [state["role"]], "mfaEnabled": True})

    with patch.object(
        authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
        side_effect=lambda *args, **kwargs: [{"userId": USER_ID, "roleName": state["role"]}],
    ), patch.object(
        authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
        side_effect=lambda role_names: state["constraints"],
    ), patch.object(authz_module, "log_authorization", MagicMock()):
        try:
            yield build
        finally:
            authz_module.casbin_user_policy_map = previous_policy_map
            authz_module.casbin_user_enforcer_map = previous_enforcer_map


@pytest.mark.unit
class TestUnarchiveObjectsUnderTheShippedRoles:

    def test_the_stand_in_now_carries_the_archived_database_id(self):
        assert OBJECTS["missing id (stand-in now)"] == {
            "databaseId": DB + "#deleted", "assetId": ASSET, "object__type": "asset"}
        assert OBJECTS["missing id (stand-in before)"]["databaseId"] == DB

    @pytest.mark.parametrize("obj", sorted(OBJECTS))
    @pytest.mark.parametrize("role", sorted(ROLES))
    def test_put_on_each_object(self, enforcer_for, role, obj):
        enforcer = enforcer_for(ROLES[role]())
        assert enforcer.enforce(dict(OBJECTS[obj]), PUT) is EXPECTED[role][obj], f"{role}: {obj}"

    @pytest.mark.parametrize("role", sorted(ROLES))
    def test_which_roles_reach_the_unarchive_route(self, enforcer_for, role):
        enforcer = enforcer_for(ROLES[role]())
        path = re.sub(r"\{[^/{}]+\}", ASSET, API_UNARCHIVE_ASSET.path.replace("{databaseId}", DB))
        assert enforcer.enforce({"object__type": "api", "route__path": path}, PUT) is REACHES_THE_ROUTE[role]
