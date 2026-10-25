# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The shipped compliance templates grant schema binding through `complianceBinding`, and read-only
access to the database and its assets.

Binding or unbinding a compliance schema (`PUT`/`DELETE /compliance/bind/{databaseId}[/{assetId}]`)
is authorized on three Tier-2 objects (complianceSchemaBindingService): the database's
`complianceBinding` object with the binding action, the `complianceSchema` being bound, and a `GET`
of the target `database` or `asset` row. So `compliance-admin.json` needs `complianceBinding` for its
database and only `GET` on the `database` and `asset` objects.

The read-only grant matters beyond the template itself. Tier 1 and Tier 2 are each evaluated across
every role a user holds, and the Database User template's Tier-1 route constraints are not
database-specific. A `PUT`/`DELETE` on `database`/`asset` carried by a compliance role would
therefore let a user who also holds Database User for another database update this database and
update or archive its assets through the ordinary asset routes. The cross-role tests below pin that
the combination grants no such write.

Each template is run through the import's own validation (`ImportConstraintsTemplateRequestModel`),
substitution and conversion (`substitute_variables`, `build_constraint_data`), then through the real
policy generation and Casbin evaluation, with only the two table reads stubbed.
"""

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from aws_lambda_powertools.utilities.parser import parse

import backend.backend.handlers.authz as authz_module  # noqa: E402
from backend.backend.handlers.auth import authConstraintsTemplateService as svc  # noqa: E402
from backend.backend.models.roleConstraints import ImportConstraintsTemplateRequestModel  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "documentation" / "permissionsTemplates"

USER_ID = "template-user"
DB_A = "db-a"
DB_B = "db-b"
SCHEMA = "schema-1"
ALL_ACTIONS = ("GET", "PUT", "POST", "DELETE")
COMPLIANCE_TEMPLATES = ("compliance-admin.json", "compliance-readonly.json")


def _template(template_name):
    return json.loads((TEMPLATES_DIR / template_name).read_text(encoding="utf-8"))


def _role_constraints(template_name, role_name, database_id):
    """The constraints an import of the template stores for `role_name`, in the shape the policy
    builder reads."""
    constraints = [
        {
            "name": constraint["name"],
            "description": constraint["description"],
            "objectType": constraint["objectType"],
            "criteriaAnd": constraint.get("criteriaAnd", []),
            "criteriaOr": constraint.get("criteriaOr", []),
            "groupPermissions": constraint.get("groupPermissions", []),
        }
        for constraint in _template(template_name)["constraints"]
    ]
    values = {"ROLE_NAME": role_name, "DATABASE_ID": database_id, "TAG_VALUE": "locked"}
    substituted = svc.substitute_variables(constraints, values)
    return [svc.build_constraint_data(constraint, role_name, {"tokens": [USER_ID]})
            for constraint in substituted]


@pytest.fixture
def roles_enforcer():
    """Build the real CasbinEnforcer for a user holding every role in `roles` ({role name:
    (template, databaseId)}). The per-user caches are cleared on every build and restored
    afterwards; the denial audit writer is replaced so a denied check writes nothing."""
    state = {"user_roles": [], "constraints": []}
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map

    def build(roles):
        state["user_roles"] = [{"userId": USER_ID, "roleName": name} for name in roles]
        state["constraints"] = [
            constraint
            for name, (template_name, database_id) in roles.items()
            for constraint in _role_constraints(template_name, name, database_id)
        ]
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        return authz_module.CasbinEnforcer(
            {"tokens": [USER_ID], "roles": list(roles), "mfaEnabled": True})

    with patch.object(
        authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
        side_effect=lambda *_args, **_kwargs: state["user_roles"],
    ), patch.object(
        authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
        side_effect=lambda role_names: state["constraints"],
    ), patch.object(authz_module, "log_authorization", MagicMock()):
        try:
            yield build
        finally:
            authz_module.casbin_user_policy_map = previous_policy_map
            authz_module.casbin_user_enforcer_map = previous_enforcer_map


# The Tier-2 objects as the binding service builds them (pinned by its own tests).
def _binding(database_id):
    return {"object__type": "complianceBinding", "databaseId": database_id}


def _schema(name=SCHEMA):
    return {"object__type": "complianceSchema", "complianceSchemaName": name}


def _database(database_id):
    return {"object__type": "database", "databaseId": database_id, "description": "probe"}


def _asset(database_id):
    return {"object__type": "asset", "databaseId": database_id, "assetId": "probe.glb",
            "assetName": "probe", "assetType": ".glb", "tags": []}


@pytest.mark.unit
class TestTheComplianceAdminTemplate:

    def test_the_template_passes_the_import_validation(self):
        """`complianceBinding` with a `databaseId` criterion is accepted by the import."""
        body = dict(_template("compliance-admin.json"),
                    variableValues={"ROLE_NAME": "compliance-admin", "DATABASE_ID": DB_A})
        model = parse(body, model=ImportConstraintsTemplateRequestModel)
        assert "complianceBinding" in [c.objectType for c in model.constraints]

    @pytest.mark.parametrize("template_name", COMPLIANCE_TEMPLATES)
    def test_the_database_and_asset_constraints_are_get_only(self, template_name):
        """The template file itself: every `database` and `asset` constraint allows GET and
        nothing else, and carries no deny."""
        grants = [
            (c["objectType"], [(p["action"], p["type"]) for p in c["groupPermissions"]])
            for c in _template(template_name)["constraints"]
            if c["objectType"] in ("database", "asset")
        ]
        assert sorted(object_type for object_type, _ in grants) == ["asset", "database"]
        for object_type, permissions in grants:
            assert permissions == [("GET", "allow")], (template_name, object_type)

    @pytest.mark.parametrize("template_name", COMPLIANCE_TEMPLATES)
    def test_the_role_reads_but_cannot_write_the_database_or_its_assets(
            self, roles_enforcer, template_name):
        enforcer = roles_enforcer({"compliance": (template_name, DB_A)})
        for target in (_database(DB_A), _asset(DB_A)):
            assert enforcer.enforce(target, "GET") is True, target
            for action in ("PUT", "POST", "DELETE"):
                assert enforcer.enforce(target, action) is False, (target, action)

    def test_the_admin_role_passes_every_bind_and_unbind_check_on_its_database(
            self, roles_enforcer):
        enforcer = roles_enforcer({"compliance": ("compliance-admin.json", DB_A)})
        for action in ("PUT", "DELETE"):
            assert enforcer.enforce(_binding(DB_A), action) is True, action
            assert enforcer.enforce(_schema(), action) is True, action
        assert enforcer.enforce(_database(DB_A), "GET") is True
        assert enforcer.enforce(_asset(DB_A), "GET") is True

    def test_the_admin_role_cannot_bind_in_another_database(self, roles_enforcer):
        enforcer = roles_enforcer({"compliance": ("compliance-admin.json", DB_A)})
        for action in ALL_ACTIONS:
            assert enforcer.enforce(_binding(DB_B), action) is False, action

    def test_the_readonly_role_cannot_bind(self, roles_enforcer):
        enforcer = roles_enforcer({"compliance": ("compliance-readonly.json", DB_A)})
        for action in ("PUT", "DELETE"):
            assert enforcer.enforce(_binding(DB_A), action) is False, action


def _api(route_path):
    """The Tier-1 object `enforceAPI` builds for a request on `route_path`."""
    return {"object__type": "api", "route__path": route_path}


def _api_route_criteria(template_name):
    """Every (operator, value, allowed actions) route criterion of the template's `api`
    constraints."""
    criteria = []
    for constraint in _template(template_name)["constraints"]:
        if constraint["objectType"] != "api":
            continue
        actions = sorted(p["action"] for p in constraint["groupPermissions"] if p["type"] == "allow")
        for criterion in constraint.get("criteriaAnd", []) + constraint.get("criteriaOr", []):
            if criterion["field"] == "route__path":
                criteria.append((criterion["operator"], criterion["value"], actions))
    return criteria


@pytest.mark.unit
class TestTheComplianceAdminListsItsDatabase:
    """The Databases page lists databases through `GET /database`, and the database's compliance
    page is linked from that list. Compliance Admin grants that one route by its exact path: a
    `/database` prefix would also open every `GET /database/...` asset and file route, and
    `/buckets` stays an administrator route (test_buckets_scoping)."""

    def test_the_template_grants_get_on_the_exact_database_list_path(self):
        database_criteria = [c for c in _api_route_criteria("compliance-admin.json")
                             if c[1].startswith("/database")]
        assert database_criteria == [("equals", "/database", ["GET"])]

    def test_the_template_grants_no_buckets_route(self):
        assert not [c for c in _api_route_criteria("compliance-admin.json")
                    if c[1].startswith("/buckets")]

    def test_the_role_may_call_the_list_route_and_no_route_below_it(self, roles_enforcer):
        enforcer = roles_enforcer({"compliance": ("compliance-admin.json", DB_A)})
        # Positive control: the exact list path is allowed, so the denials below are about the
        # path and not about the role matching nothing.
        assert enforcer.enforce(_api("/database"), "GET") is True
        for action in ("PUT", "POST", "DELETE"):
            assert enforcer.enforce(_api("/database"), action) is False, action
        for path in ("/database/x/assets", f"/database/{DB_A}", f"/database/{DB_A}/assets",
                     "/database/", "/databases", "/buckets"):
            assert enforcer.enforce(_api(path), "GET") is False, path

    def test_the_list_returns_only_the_role_database(self, roles_enforcer):
        """The listing filters each row on the `database` object; the role reads its own."""
        enforcer = roles_enforcer({"compliance": ("compliance-admin.json", DB_A)})
        assert enforcer.enforce(_database(DB_A), "GET") is True
        assert enforcer.enforce(_database(DB_B), "GET") is False


@pytest.mark.unit
class TestDatabaseRolesDoNotBind:
    """The database templates grant writes on the database and its assets, and no binding."""

    @pytest.mark.parametrize("template_name", ("database-admin.json", "database-user.json"))
    def test_a_database_role_cannot_bind_in_its_own_database(self, roles_enforcer, template_name):
        enforcer = roles_enforcer({"data": (template_name, DB_A)})
        # Control: the role does hold the writes an old bind check asked for.
        assert enforcer.enforce(_asset(DB_A), "PUT") is True
        for action in ("PUT", "DELETE"):
            assert enforcer.enforce(_binding(DB_A), action) is False, action

    def test_database_admin_plus_compliance_admin_elsewhere_cannot_bind_in_the_admin_database(
            self, roles_enforcer):
        """Compliance Admin's schema grant is deployment-wide, so a binding scoped only by the
        target's write permission would have reached database A; the binding object keeps it to
        database B."""
        enforcer = roles_enforcer({"data": ("database-admin.json", DB_A),
                                   "compliance": ("compliance-admin.json", DB_B)})
        assert enforcer.enforce(_schema(), "PUT") is True
        assert enforcer.enforce(_database(DB_A), "PUT") is True
        for action in ("PUT", "DELETE"):
            assert enforcer.enforce(_binding(DB_A), action) is False, action
            assert enforcer.enforce(_binding(DB_B), action) is True, action


@pytest.mark.unit
class TestComplianceAdminGrantsNoDataWrite:
    """Database User for database A plus Compliance Admin for database B: the compliance role adds
    no write on database B or its assets."""

    def test_the_combined_roles_cannot_write_the_compliance_database_or_its_assets(
            self, roles_enforcer):
        enforcer = roles_enforcer({"data": ("database-user.json", DB_A),
                                   "compliance": ("compliance-admin.json", DB_B)})
        for action in ("PUT", "POST", "DELETE"):
            assert enforcer.enforce(_database(DB_B), action) is False, action
            assert enforcer.enforce(_asset(DB_B), action) is False, action

    def test_control_each_role_keeps_its_own_grants(self, roles_enforcer):
        enforcer = roles_enforcer({"data": ("database-user.json", DB_A),
                                   "compliance": ("compliance-admin.json", DB_B)})
        assert enforcer.enforce(_asset(DB_A), "PUT") is True
        assert enforcer.enforce(_asset(DB_A), "DELETE") is True
        assert enforcer.enforce(_asset(DB_B), "GET") is True
        assert enforcer.enforce(_binding(DB_B), "PUT") is True
