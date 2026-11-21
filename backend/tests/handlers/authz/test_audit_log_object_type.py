# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The `auditLog` constraint object type, scoped per audit log group (issue #1536).

Pinned here: the type and its one field are declared as core constants and served to the constraint
editor; a role built from `audit-reader.json` is allowed exactly the groups its `ALLOWED_GROUPS`
names, on the three actions, and no other group; only the seeded admin role holds the type by
default; and the routing enforcer walks only the `auditLog` lines for an `auditLog` check. Roles are
evaluated through the real policy builder and Casbin enforcer; only the two table reads are stubbed.
"""

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from aws_lambda_powertools.utilities.parser import parse

import backend.backend.handlers.authz as authz_module
from backend.backend.common import constants
from backend.backend.handlers.auth import authConstraintsTemplateService as svc
from backend.backend.models.roleConstraints import ImportConstraintsTemplateRequestModel
from tests.handlers.authz import shipped_constraints

USER_ID = "audit-user"
ACTIONS = ("GET", "PUT", "POST", "DELETE")
READER_ACTIONS = ("GET",)
TEMPLATE = "audit-reader.json"
# The registered audit routes (common/apiRoutes.py, CORE_FEATURE_AUDIT_LOG_SEARCH), with an id
# in place of {queryId}.
AUDIT_API_ROUTES = ("/audit/log-groups", "/audit/queries", "/audit/queries/q-1",
                    "/audit/queries/q-1/export")
AUDIT_WEB_ROUTE = "/auth/audit-log-search"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "documentation" / "permissionsTemplates"
TEMPLATE_PATH = TEMPLATES_DIR / TEMPLATE


def _audit(group, **extra):
    return dict({"object__type": "auditLog", "auditLogGroup": group}, **extra)


def _api(route):
    return {"object__type": "api", "route__path": route}


def _web(route):
    return {"object__type": "web", "route__path": route}


def _template_constraints(template_name, role_name, values):
    return shipped_constraints.template_constraints(
        template_name, role_name, dict(values, DATABASE_ID="db-a", TAG_VALUE="locked"))


@pytest.fixture
def build_enforcer():
    """A real CasbinEnforcer for a user holding the roles in `{role name: constraints}`."""
    state = {"user_roles": [], "constraints": []}
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map

    def build(roles):
        state["user_roles"] = [{"userId": USER_ID, "roleName": name} for name in roles]
        state["constraints"] = [c for constraints in roles.values() for c in constraints]
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


@pytest.mark.unit
class TestTheObjectTypeIsDeclared:

    def test_the_type_and_its_field_are_core_constants(self):
        assert "auditLog" in constants.CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES
        assert "auditLogGroup" in constants.CORE_PERMISSION_CONSTRAINT_FIELDS
        assert constants.CORE_CONSTRAINT_OBJECT_TYPE_FIELDS["auditLog"] == {
            "label": "Audit Log",
            "fields": [{"label": "Audit Log Group", "value": "auditLogGroup"}],
        }

    def test_the_merged_names_consumers_import_carry_it(self):
        assert "auditLog" in constants.ALLOWED_CONSTRAINT_OBJECT_TYPES
        assert constants.get_constraint_fields_for_object_type("auditLog") == ["auditLogGroup"]
        assert "auditLogGroup" in constants.PERMISSION_CONSTRAINT_FIELDS

    def test_no_other_object_type_takes_the_new_field(self):
        for object_type, entry in constants.CONSTRAINT_OBJECT_TYPE_FIELDS.items():
            if object_type != "auditLog":
                assert "auditLogGroup" not in [f["value"] for f in entry["fields"]], object_type


@pytest.mark.unit
class TestTheAuditReaderTemplate:

    def test_the_template_passes_the_import_validation_with_the_default_group_pattern(self):
        body = dict(json.loads((TEMPLATES_DIR / TEMPLATE).read_text(encoding="utf-8")),
                    variableValues={"ROLE_NAME": "audit-reader"})
        model = parse(body, model=ImportConstraintsTemplateRequestModel)
        assert [c.objectType for c in model.constraints].count("auditLog") == 1
        assert model.variableValues["ALLOWED_GROUPS"] == ".*"

    def test_the_template_grants_only_the_audit_surfaces(self):
        constraints = json.loads((TEMPLATES_DIR / TEMPLATE).read_text(encoding="utf-8"))["constraints"]
        assert sorted(c["objectType"] for c in constraints) == ["api", "api", "api", "auditLog", "web"]
        for constraint in constraints:
            assert all(p["type"] == "allow" for p in constraint["groupPermissions"])
            assert "PUT" not in [p["action"] for p in constraint["groupPermissions"]]

    def test_a_reader_of_one_group_reads_that_group_only(self, build_enforcer):
        enforcer = build_enforcer({"reader": _template_constraints(
            TEMPLATE, "reader", {"ALLOWED_GROUPS": "authorization"})})
        for action in READER_ACTIONS:
            assert enforcer.enforce(_audit("authorization"), action) is True, action
        for action in ("PUT", "POST", "DELETE"):
            assert enforcer.enforce(_audit("authorization"), action) is False, action
        for other in ("api", "authorization-extra", "preauthorization", "", "unknown-group"):
            for action in ACTIONS:
                assert enforcer.enforce(_audit(other), action) is False, (other, action)

    def test_a_reader_of_a_list_of_groups(self, build_enforcer):
        enforcer = build_enforcer({"reader": _template_constraints(
            TEMPLATE, "reader", {"ALLOWED_GROUPS": "(authorization|api)"})})
        assert enforcer.enforce(_audit("authorization"), "GET") is True
        assert enforcer.enforce(_audit("api"), "GET") is True
        assert enforcer.enforce(_audit("assets"), "GET") is False

    def test_an_unparenthesised_list_of_groups_does_not_over_grant(self, build_enforcer):
        enforcer = build_enforcer({"reader": _template_constraints(
            TEMPLATE, "reader", {"ALLOWED_GROUPS": "authorization|api"})})
        assert enforcer.enforce(_audit("authorization"), "GET") is True
        assert enforcer.enforce(_audit("api"), "GET") is True
        for other in ("authorization-extra", "apix", "xapi", "preauthorization", "assets"):
            assert enforcer.enforce(_audit(other), "GET") is False, other

    def test_the_group_value_is_wrapped_so_the_variable_cannot_escape_the_anchors(self):
        constraints = json.loads((TEMPLATE_PATH.read_text(encoding="utf-8")))["constraints"]
        audit = [c for c in constraints if c["objectType"] == "auditLog"]
        assert [c["value"] for crit in audit for c in crit["criteriaAnd"]] == ["(?:{{ALLOWED_GROUPS}})"]
        assert [p["action"] for c in audit for p in c["groupPermissions"]] == ["GET"]

    def test_the_default_pattern_reads_every_group_including_one_registered_later(self, build_enforcer):
        enforcer = build_enforcer({"reader": _template_constraints(
            TEMPLATE, "reader", {"ALLOWED_GROUPS": ".*"})})
        for group in ("authorization", "api", "a-group-added-next-year"):
            assert enforcer.enforce(_audit(group), "GET") is True, group

    def test_the_reader_gets_the_audit_routes_and_page_and_nothing_else(self, build_enforcer):
        enforcer = build_enforcer({"reader": _template_constraints(
            TEMPLATE, "reader", {"ALLOWED_GROUPS": ".*"})})
        for route in AUDIT_API_ROUTES:
            for action in READER_ACTIONS:
                assert enforcer.enforce(_api(route), action) is True, (route, action)
            assert enforcer.enforce(_api(route), "PUT") is False
        assert enforcer.enforce(_api("/auth/routes"), "POST") is True
        assert enforcer.enforce(_web(AUDIT_WEB_ROUTE), "GET") is True
        for route in ("/auditx", "/database", "/assets", "/roles", "/auth/constraints"):
            assert enforcer.enforce(_api(route), "GET") is False, route
        for target in ({"object__type": "database", "databaseId": "db-a"},
                       {"object__type": "asset", "databaseId": "db-a", "assetName": "a",
                        "assetType": ".glb", "tags": []},
                       {"object__type": "role", "roleName": "admin"}):
            for action in ACTIONS:
                assert enforcer.enforce(target, action) is False, (target["object__type"], action)

    def test_extra_object_attributes_do_not_change_the_decision(self, build_enforcer):
        enforcer = build_enforcer({"reader": _template_constraints(
            TEMPLATE, "reader", {"ALLOWED_GROUPS": "authorization"})})
        noisy = {"databaseId": "db-x", "assetName": "n", "tags": ["locked"], "userId": "u",
                 "route__path": "/audit"}
        assert enforcer.enforce(_audit("authorization", **noisy), "GET") is True
        assert enforcer.enforce(_audit("api", **noisy), "GET") is False


@pytest.mark.unit
class TestOnlyAdminHoldsTheTypeByDefault:

    def test_the_admin_seed_allows_every_group_on_every_action(self, build_enforcer):
        enforcer = build_enforcer({"admin": shipped_constraints.seeded_constraints("admin")})
        for group in ("authorization", "api", "a-group-added-later"):
            for action in ACTIONS:
                assert enforcer.enforce(_audit(group), action) is True, (group, action)

    def test_the_seed_is_one_constraint_for_admin_only(self):
        seeded = [c for c in shipped_constraints.seeded_constraints("admin")
                  if c["objectType"] == "auditLog"]
        assert [c["constraintId"] for c in seeded] == ["initial_admin_allow_auditlog"]
        assert {gp["groupId"] for c in seeded for gp in c["groupPermissions"]} == {"admin"}
        assert shipped_constraints.seeded_constraints("basicReadOnly")
        assert [c for c in shipped_constraints.seeded_constraints("basicReadOnly")
                if c["objectType"] == "auditLog"] == []

    def test_no_other_shipped_template_holds_the_type_or_the_audit_routes(self):
        for name in shipped_constraints.template_names():
            if name == TEMPLATE:
                continue
            text = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
            assert '"auditLog"' not in text, name
            assert "/audit" not in text, name

    @pytest.mark.parametrize("template_name", [n for n in shipped_constraints.template_names()
                                               if n != TEMPLATE])
    def test_a_role_built_from_another_template_gets_no_audit_access(
            self, build_enforcer, template_name):
        enforcer = build_enforcer({"tpl": _template_constraints(template_name, "tpl", {})})
        for action in ACTIONS:
            assert enforcer.enforce(_audit("authorization"), action) is False, action
        for route in AUDIT_API_ROUTES:
            for action in ACTIONS:
                assert enforcer.enforce(_api(route), action) is False, (route, action)
        assert enforcer.enforce(_web(AUDIT_WEB_ROUTE), "GET") is False

    def test_the_basic_read_only_role_gets_no_audit_access(self, build_enforcer):
        enforcer = build_enforcer({"basicReadOnly": shipped_constraints.seeded_constraints(
            "basicReadOnly")})
        for action in ACTIONS:
            assert enforcer.enforce(_audit("authorization"), action) is False, action
            assert enforcer.enforce(_api("/audit"), action) is False, action
        assert enforcer.enforce(_web(AUDIT_WEB_ROUTE), "GET") is False

    def test_a_custom_role_granting_every_other_object_type_gets_no_auditlog_access(
            self, build_enforcer):
        """A role built from object-type constraints for everything else (an admin-like custom
        role) holds nothing on `auditLog` until a constraint names it."""
        constraints = []
        for index, object_type in enumerate(
                t for t in constants.ALLOWED_CONSTRAINT_OBJECT_TYPES if t != "auditLog"):
            field = constants.get_constraint_fields_for_object_type(object_type)[0]
            constraints.append({
                "constraintId": f"custom-{index}", "objectType": object_type,
                "criteriaAnd": [{"field": field, "operator": "contains", "value": ".*"}],
                "criteriaOr": [],
                "groupPermissions": [{"groupId": "custom-admin", "permission": a,
                                      "permissionType": "allow"} for a in ACTIONS],
                "userPermissions": [],
            })
        enforcer = build_enforcer({"custom-admin": constraints})
        for action in ACTIONS:
            assert enforcer.enforce(_audit("authorization"), action) is False, action

    def test_a_deny_on_one_group_beats_the_allow_of_every_group(self, build_enforcer):
        reader = _template_constraints(TEMPLATE, "reader", {"ALLOWED_GROUPS": ".*"})
        reader.append({
            "constraintId": "deny-one", "objectType": "auditLog",
            "criteriaAnd": [{"field": "auditLogGroup", "operator": "equals", "value": "api"}],
            "criteriaOr": [],
            "groupPermissions": [{"groupId": "reader", "permission": "GET",
                                  "permissionType": "deny"}],
            "userPermissions": [],
        })
        enforcer = build_enforcer({"reader": reader})
        assert enforcer.enforce(_audit("api"), "GET") is False
        assert enforcer.enforce(_audit("authorization"), "GET") is True


@pytest.mark.unit
class TestTheRoutingEnforcerWalksOnlyTheAuditLogLines:

    def test_an_auditlog_check_walks_only_auditlog_lines_and_others_skip_them(self, build_enforcer):
        roles = {"admin": shipped_constraints.seeded_constraints("admin"),
                 "reader": _template_constraints(TEMPLATE, "reader", {"ALLOWED_GROUPS": ".*"})}
        enforcer = build_enforcer(roles)
        engine = enforcer.service_object._enforcer
        assert type(engine) is authz_module.ObjectTypeRoutingEnforcer
        routes = engine._policy_routes
        assert routes is not None and routes.shared == []
        gate = "regexMatch(r.obj.object__type, '^auditLog\\\\Z')"
        policy = engine.get_policy()
        audit_lines = [line for line in policy if line[1].startswith(f"({gate})")]
        assert audit_lines, "the auditLog lines should be in the policy"
        assert routes.by_type["auditLog"] == audit_lines
        assert len(audit_lines) < len(policy)
        for object_type, lines in routes.by_type.items():
            if object_type != "auditLog":
                assert not [line for line in lines if line in audit_lines], object_type


@pytest.mark.unit
class TestACheckWithoutAGroupKey:

    @pytest.mark.parametrize("attributes", [
        {"object__type": "auditLog"},
        {"object__type": "auditLog", "auditLogGroup": ""},
    ])
    def test_a_missing_or_empty_group_matches_an_allow_all_role_and_a_restricted_role_denies_it(
            self, build_enforcer, attributes):
        enforcer = build_enforcer({
            "wide": _template_constraints(TEMPLATE, "wide", {"ALLOWED_GROUPS": ".*"})})
        assert enforcer.enforce(attributes, "GET") is True
        restricted = build_enforcer({
            "narrow": _template_constraints(TEMPLATE, "narrow", {"ALLOWED_GROUPS": "authorization"})})
        assert restricted.enforce(attributes, "GET") is False


@pytest.mark.unit
class TestTheReaderTemplateNamesRealGroupKeys:
    """The template's examples are the keys ``GET /audit/log-groups`` lists, so a copied example works."""

    @staticmethod
    def _registry_keys():
        from common.auditLogGroups import audit_log_group_keys
        return set(audit_log_group_keys())

    @staticmethod
    def _description():
        variables = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))["variables"]
        return [v for v in variables if v["name"] == "ALLOWED_GROUPS"][0]["description"]

    def test_every_key_the_description_lists_is_a_registered_group_and_every_group_is_listed(self):
        import re
        listed = set(re.findall(r"\baudit[A-Z][A-Za-z]*\b", self._description()))
        assert listed == self._registry_keys()

    def test_the_example_patterns_grant_the_groups_they_name(self, build_enforcer):
        enforcer = build_enforcer({"reader": _template_constraints(
            TEMPLATE, "reader", {"ALLOWED_GROUPS": "(auditActions|auditErrors)"})})
        assert enforcer.enforce(_audit("auditActions"), "GET") is True
        assert enforcer.enforce(_audit("auditErrors"), "GET") is True
        assert enforcer.enforce(_audit("auditAuthorization"), "GET") is False
        single = build_enforcer({"reader": _template_constraints(
            TEMPLATE, "reader", {"ALLOWED_GROUPS": "auditFileDownload"})})
        assert single.enforce(_audit("auditFileDownload"), "GET") is True
        assert single.enforce(_audit("auditFileDownloadStreamed"), "GET") is False  # the whole key is matched

    def test_the_reader_can_read_the_route_listing_the_page_loads(self, build_enforcer):
        enforcer = build_enforcer({"reader": _template_constraints(
            TEMPLATE, "reader", {"ALLOWED_GROUPS": ".*"})})
        for route in ("/auth/routes/api/allowed", "/auth/loginProfile", "/secure-config"):
            assert enforcer.enforce(_api(route), "GET") is True, route
        assert enforcer.enforce(_api("/auth/routes"), "POST") is True
        for route in ("/auth/constraints", "/roles"):
            assert enforcer.enforce(_api(route), "PUT") is False, route
