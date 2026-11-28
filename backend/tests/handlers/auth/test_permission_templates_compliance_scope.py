# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Database-scoped compliance administration, through the shipped templates and the real enforcer.

Each template is imported the way the import route does it (substitution and conversion), turned
into policy by the real builder and enforced by the production enforcer (`CasbinEnforcer`, whose
service routes each request to its object type's lines). The matrix covers, per template: the
database's own schemas, another database's schemas, GLOBAL schemas, cascades of its own and of
another database, binding and evaluation. The handler tests run the compliance services against
that same enforcer (tests/handlers/compliance/test_complianceTemplateRolesEndToEnd.py).
"""

import pytest

from backend.tests.handlers.auth.test_permission_templates_compliance_binding import (  # noqa: F401
    DB_A, DB_B, USER_ID, roles_enforcer,
)
CASCADE_ID = "5b1e2c3d-0000-4000-8000-000000000001"

GLOBAL = "GLOBAL"


def _schema(scope, name="s1"):
    return {"object__type": "complianceSchema", "complianceSchemaName": name, "databaseId": scope}


def _cascade(database_id):
    return {"object__type": "complianceCascade", "cascadeId": CASCADE_ID, "databaseId": database_id}


def _binding(database_id):
    return {"object__type": "complianceBinding", "databaseId": database_id}


def _evaluation(database_id, state=""):
    return {"object__type": "complianceEvaluation", "databaseId": database_id,
            "complianceState": state}


WRITES = ("PUT", "POST", "DELETE")

# template -> (own-database schema actions, GLOBAL schema actions, own cascade actions,
#              own binding actions, own evaluation actions); other databases get nothing.
DATABASE_SCOPED = {
    "compliance-admin.json": ({"GET", "PUT", "POST", "DELETE"}, {"GET"}, {"GET", "POST"},
                              {"GET", "PUT", "DELETE"}, {"GET", "POST", "DELETE"}),
    "database-admin.json": ({"GET", "PUT", "POST", "DELETE"}, {"GET"}, {"GET", "POST"},
                            {"GET", "PUT", "DELETE"}, {"GET", "POST", "DELETE"}),
    "compliance-readonly.json": ({"GET"}, {"GET"}, {"GET"}, set(), {"GET"}),
    "database-user.json": ({"GET"}, {"GET"}, {"GET"}, set(), {"GET"}),
}
ALL_ACTIONS = ("GET", "PUT", "POST", "DELETE")


@pytest.mark.unit
@pytest.mark.parametrize("template_name", sorted(DATABASE_SCOPED))
class TestADatabaseScopedTemplate:

    def _enforcer(self, roles_enforcer, template_name):
        return roles_enforcer({"role": (template_name, DB_A)})

    def test_the_role_is_enforced_by_the_object_type_routed_enforcer(
            self, roles_enforcer, template_name):
        from backend.backend.handlers.authz import ObjectTypeRoutingEnforcer
        enforcer = self._enforcer(roles_enforcer, template_name)
        assert type(enforcer.service_object._enforcer) is ObjectTypeRoutingEnforcer

    def test_the_databases_own_schemas(self, roles_enforcer, template_name):
        enforcer = self._enforcer(roles_enforcer, template_name)
        allowed = DATABASE_SCOPED[template_name][0]
        for action in ALL_ACTIONS:
            assert enforcer.enforce(_schema(DB_A), action) is (action in allowed), action

    def test_global_schemas_are_read_and_bindable_not_writable(self, roles_enforcer, template_name):
        enforcer = self._enforcer(roles_enforcer, template_name)
        assert enforcer.enforce(_schema(GLOBAL), "GET") is True
        for action in WRITES:
            assert enforcer.enforce(_schema(GLOBAL), action) is False, action

    def test_another_databases_schemas_are_denied(self, roles_enforcer, template_name):
        enforcer = self._enforcer(roles_enforcer, template_name)
        for action in ALL_ACTIONS:
            assert enforcer.enforce(_schema(DB_B), action) is False, action

    def test_cascades_of_its_own_database_only(self, roles_enforcer, template_name):
        enforcer = self._enforcer(roles_enforcer, template_name)
        allowed = DATABASE_SCOPED[template_name][2]
        for action in ALL_ACTIONS:
            assert enforcer.enforce(_cascade(DB_A), action) is (action in allowed), action
            assert enforcer.enforce(_cascade(DB_B), action) is False, action

    def test_binding_and_evaluation_in_its_own_database_only(self, roles_enforcer, template_name):
        enforcer = self._enforcer(roles_enforcer, template_name)
        binding, evaluation = DATABASE_SCOPED[template_name][3], DATABASE_SCOPED[template_name][4]
        for action in ALL_ACTIONS:
            assert enforcer.enforce(_binding(DB_A), action) is (action in binding), action
            assert enforcer.enforce(_evaluation(DB_A), action) is (action in evaluation), action
            assert enforcer.enforce(_binding(DB_B), action) is False, action
            assert enforcer.enforce(_evaluation(DB_B), action) is False, action

    def test_a_check_without_a_database_is_denied(self, roles_enforcer, template_name):
        enforcer = self._enforcer(roles_enforcer, template_name)
        assert enforcer.enforce({"object__type": "complianceSchema",
                                 "complianceSchemaName": "s1"}, "GET") is False
        assert enforcer.enforce({"object__type": "complianceCascade",
                                 "cascadeId": CASCADE_ID}, "GET") is False


@pytest.mark.unit
class TestTheComplianceGlobalAdminTemplate:

    def _enforcer(self, roles_enforcer):
        return roles_enforcer({"central": ("compliance-global-admin.json", "")})

    def test_the_template_passes_the_import_validation(self):
        from aws_lambda_powertools.utilities.parser import parse
        from backend.backend.models.roleConstraints import ImportConstraintsTemplateRequestModel
        from backend.tests.handlers.auth.test_permission_templates_compliance_binding import (
            _template)
        body = dict(_template("compliance-global-admin.json"),
                    variableValues={"ROLE_NAME": "compliance-central"})
        parse(body, model=ImportConstraintsTemplateRequestModel)

    def test_manages_global_schemas(self, roles_enforcer):
        enforcer = self._enforcer(roles_enforcer)
        for action in ALL_ACTIONS:
            assert enforcer.enforce(_schema(GLOBAL), action) is True, action

    @pytest.mark.parametrize("database_id", [DB_A, DB_B])
    def test_reads_every_databases_compliance_and_writes_none(self, roles_enforcer, database_id):
        enforcer = self._enforcer(roles_enforcer)
        for obj in (_schema(database_id), _cascade(database_id), _evaluation(database_id),
                    {"object__type": "database", "databaseId": database_id}):
            assert enforcer.enforce(obj, "GET") is True, obj
            for action in WRITES:
                assert enforcer.enforce(obj, action) is False, (obj, action)
        for action in ALL_ACTIONS:
            assert enforcer.enforce(_binding(database_id), action) is False, action
        for action in ALL_ACTIONS:
            assert enforcer.enforce({"object__type": "asset", "databaseId": database_id,
                                     "assetName": "a", "assetType": ".glb", "tags": []},
                                    action) is False, action

    def test_its_routes_allow_schema_writes_and_compliance_reads_only(self, roles_enforcer):
        enforcer = self._enforcer(roles_enforcer)

        def api(path):
            return {"object__type": "api", "route__path": path}
        for action in ("POST", "PUT", "DELETE"):
            assert enforcer.enforce(api("/compliance/schemas"), action) is True, action
            for path in ("/compliance/bind/db-a", "/compliance/evaluate/db-a",
                         "/compliance/sweep/s1", "/compliance/cascades/x/approve",
                         "/compliance/quarantine/db-a/a/release", "/database/db-a"):
                assert enforcer.enforce(api(path), action) is False, (path, action)
        assert enforcer.enforce(api("/compliance/audit"), "GET") is True


@pytest.mark.unit
class TestDatabaseUserStaysReadOnlyForCompliance:

    def test_its_routes_grant_no_compliance_write(self, roles_enforcer):
        enforcer = roles_enforcer({"user": ("database-user.json", DB_A)})
        api = {"object__type": "api", "route__path": "/compliance/evaluate/db-a"}
        assert enforcer.enforce(dict(api), "GET") is True
        for action in WRITES:
            for path in ("/compliance/evaluate/db-a", "/compliance/bind/db-a",
                         "/compliance/quarantine/db-a/a/release", "/compliance/cascades",
                         "/compliance/schemas", "/compliance/sweep/s1"):
                assert enforcer.enforce({"object__type": "api", "route__path": path},
                                        action) is False, (path, action)
