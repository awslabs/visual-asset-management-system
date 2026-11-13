# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Roles stored before compliance schemas and cascades carried a database keep their meaning.

`databaseId` became a constraint field of the `complianceSchema` (the schema's scope: a database id
or GLOBAL) and `complianceCascade` (the parent asset's database) object types, and every check now
passes it. A constraint stored before then has no `databaseId` criterion. The policy builder emits
one clause per criterion, so such a constraint puts no condition on the field and matches every
scope: an existing compliance role created from the earlier templates keeps exactly its earlier
reach after the upgrade -- no migration is needed, and it does not fail closed. A role re-imported
from the current templates is scoped to its database.

The other direction is pinned too: a constraint that does carry `databaseId` denies a check that
does not pass one (the request's field defaults to the empty string), so a call site that forgot
the field would fail closed rather than open.

Real policy generation and Casbin evaluation; only the two table reads are stubbed.
"""

from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module  # noqa: E402
from backend.backend.handlers.auth import authConstraintsTemplateService  # noqa: E402,F401
from backend.tests.handlers.authz import shipped_constraints  # noqa: E402

USER_ID = "existing-role-user"
ROLE = "compliance-admin-old"


def _criterion(field, operator, value):
    return {"field": field, "operator": operator, "value": value, "id": f"{field}-{operator}"}


def _constraint(name, object_type, criteria, actions):
    return {
        "constraintId": name, "name": name, "description": name, "objectType": object_type,
        "criteriaAnd": criteria, "criteriaOr": [],
        "groupPermissions": [{"groupId": ROLE, "permission": action, "permissionType": "allow",
                              "id": f"{name}-{action}"} for action in actions],
        "userPermissions": [],
    }


# The two constraints the earlier compliance-admin template stored for these object types.
EARLIER_COMPLIANCE_ADMIN = [
    _constraint("old-schemas", "complianceSchema",
                [_criterion("complianceSchemaName", "contains", ".*")],
                ("GET", "PUT", "POST", "DELETE")),
    _constraint("old-cascades", "complianceCascade",
                [_criterion("cascadeId", "contains", ".*")], ("GET", "POST")),
]

SCOPED = [
    _constraint("scoped-schemas", "complianceSchema", [_criterion("databaseId", "equals", "db1")],
                ("GET",)),
    _constraint("scoped-cascades", "complianceCascade",
                [_criterion("databaseId", "equals", "db1")], ("GET",)),
]


@pytest.fixture
def enforcer_for():
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map
    state = {"constraints": []}

    def build(constraints):
        state["constraints"] = constraints
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        return authz_module.CasbinEnforcer({"tokens": [USER_ID], "roles": [ROLE], "mfaEnabled": True})

    with patch.object(authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
                      side_effect=lambda *_a, **_k: [{"userId": USER_ID, "roleName": ROLE}]), \
            patch.object(authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
                         side_effect=lambda role_names: state["constraints"]), \
            patch.object(authz_module, "log_authorization", MagicMock()):
        try:
            yield build
        finally:
            authz_module.casbin_user_policy_map = previous_policy_map
            authz_module.casbin_user_enforcer_map = previous_enforcer_map


def _schema(scope):
    return {"object__type": "complianceSchema", "complianceSchemaName": "s1", "databaseId": scope}


def _cascade(database_id):
    return {"object__type": "complianceCascade", "cascadeId": "c1", "databaseId": database_id}


@pytest.mark.unit
class TestAnEarlierRoleKeepsItsReach:

    @pytest.mark.parametrize("scope", ["GLOBAL", "db1", "db2"])
    @pytest.mark.parametrize("action", ["GET", "PUT", "POST", "DELETE"])
    def test_schemas_of_every_scope(self, enforcer_for, scope, action):
        assert enforcer_for(EARLIER_COMPLIANCE_ADMIN).enforce(_schema(scope), action) is True

    @pytest.mark.parametrize("database_id", ["db1", "db2"])
    @pytest.mark.parametrize("action", ["GET", "POST"])
    def test_cascades_of_every_database(self, enforcer_for, database_id, action):
        assert enforcer_for(EARLIER_COMPLIANCE_ADMIN).enforce(_cascade(database_id), action) is True

    def test_and_nothing_it_did_not_have(self, enforcer_for):
        enforcer = enforcer_for(EARLIER_COMPLIANCE_ADMIN)
        assert enforcer.enforce(_cascade("db1"), "DELETE") is False
        assert enforcer.enforce({"object__type": "complianceBinding", "databaseId": "db1"},
                                "PUT") is False


@pytest.mark.unit
class TestTheSeededAdminRole:
    """The admin seed's single allow-all criterion per object type (`complianceSchemaName .*`,
    `cascadeId .*`) puts no condition on `databaseId`, so it covers every scope with no seed
    change."""

    def _admin(self, enforcer_for):
        constraints = shipped_constraints.seeded_constraints("admin")
        for constraint in constraints:
            for permission in constraint["groupPermissions"]:
                permission["groupId"] = ROLE
        return enforcer_for(constraints)

    @pytest.mark.parametrize("scope", ["GLOBAL", "db1", "db2"])
    def test_every_schema_scope(self, enforcer_for, scope):
        enforcer = self._admin(enforcer_for)
        for action in ("GET", "PUT", "POST", "DELETE"):
            assert enforcer.enforce(_schema(scope), action) is True, action

    @pytest.mark.parametrize("database_id", ["db1", "db2"])
    def test_every_cascade_database(self, enforcer_for, database_id):
        enforcer = self._admin(enforcer_for)
        for action in ("GET", "POST"):
            assert enforcer.enforce(_cascade(database_id), action) is True, action


@pytest.mark.unit
class TestAScopedRole:

    def test_matches_its_database_only(self, enforcer_for):
        enforcer = enforcer_for(SCOPED)
        assert enforcer.enforce(_schema("db1"), "GET") is True
        assert enforcer.enforce(_cascade("db1"), "GET") is True
        for denied in (_schema("db2"), _schema("GLOBAL"), _cascade("db2")):
            assert enforcer.enforce(denied, "GET") is False, denied

    @pytest.mark.parametrize("obj", [
        {"object__type": "complianceSchema", "complianceSchemaName": "s1"},
        {"object__type": "complianceCascade", "cascadeId": "c1"},
    ], ids=["schema", "cascade"])
    def test_fails_closed_on_a_check_that_passes_no_database(self, enforcer_for, obj):
        assert enforcer_for(SCOPED).enforce(obj, "GET") is False
