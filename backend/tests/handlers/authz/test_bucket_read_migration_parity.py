# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The v2.6 to v2.7 migration's `bucketReadConstraints` step decides as the authorizer does (issue #1978).

The step (infra/deploymentDataMigration/v2.6_to_v2.7/upgrade/bucket_read_constraints.py) gives a role the
shipped templates' bucket read only when the role's own stored constraints allow one of the three API
routes that read a bucket registration from v2.7: GET `/buckets`, POST `/database` (create) and PUT
`/database/<databaseId>` (update) for a database the deployment holds. It re-implements that decision
without the backend, so it is pinned here against the real policy builder and Casbin enforcer, reading the
same stored (denormalized, wire-format) items through the real GroupPermissionsIndex /
UserPermissionsIndex read path: for every constraint shape below, and for a role imported from each
shipped template before 2.7.0 (its constraints without the bucket read), the step's answer for each route
equals the enforcer's. The item the step writes is the one a template import writes, and the authorizer
reads it as GET on every registration and nothing else.

The migration tree carries no committed tests (infra Development Rule 6); this file tests the step from
the backend's side because the decision it must match lives here.
"""

import copy
import importlib.util
import json
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest
from boto3.dynamodb.types import TypeSerializer

import backend.backend.handlers.authz as authz_module
from backend.backend.handlers.auth import authConstraintsTemplateService as template_service
from tests.handlers.authz import shipped_constraints

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
STEP_MODULE = (REPO_ROOT / "infra" / "deploymentDataMigration" / "v2.6_to_v2.7" / "upgrade"
               / "bucket_read_constraints.py")
TEMPLATE_VALUES = {"DATABASE_ID": "db-a", "TAG_VALUE": "locked", "ALLOWED_GROUPS": ".*"}
ROLE = "r"
USER_ID = "parity-user"
ROUTE = {"object__type": "api", "route__path": "/buckets"}
# The databases the deployment holds in these cases: the update route is decided for each one's path.
DATABASES = ["db-a", "db-b"]
# route -> (action, the request paths the authorizer decides for it)
ROUTE_REQUESTS = {
    "GET /buckets": ("GET", ["/buckets"]),
    "POST /database": ("POST", ["/database"]),
    "PUT /database/{databaseId}": ("PUT", [f"/database/{database_id}" for database_id in DATABASES]),
}
_SERIALIZER = TypeSerializer()


def _load_step():
    spec = importlib.util.spec_from_file_location("bucket_read_constraints", STEP_MODULE)
    module = importlib.util.module_from_spec(spec)
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no __pycache__ in the migration tree
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


step = _load_step()


def _api(criteria_and=(), criteria_or=(), permissions=(("GET", "allow"),), name="c", role=ROLE, user=None):
    """An api constraint in the API format the constraint service and the template import store."""
    principal = {"userId": user} if user else {"groupId": role}
    entries = [dict(principal, id=f"{name}-{i}", permission=action, permissionType=effect)
               for i, (action, effect) in enumerate(permissions)]
    return {
        "identifier": name, "name": name, "description": name, "objectType": "api",
        "criteriaAnd": list(criteria_and), "criteriaOr": list(criteria_or),
        "groupPermissions": [] if user else entries, "userPermissions": entries if user else [],
        "dateCreated": "2026-01-01T00:00:00+00:00", "createdBy": "SYSTEM_USER", "modifiedBy": "SYSTEM_USER",
    }


def _route(operator, value, field="route__path"):
    return {"field": field, "operator": operator, "value": value}


def _stored_items(constraints):
    """The wire-format items the table holds for these API-format constraints."""
    items = []
    for constraint in constraints:
        for item in template_service._transform_to_denormalized_format(constraint):
            items.append({key: _SERIALIZER.serialize(value) for key, value in item.items()})
    return items


class _ConstraintsTable:
    """The constraints table's two principal indexes, as the authorizer queries them."""

    def __init__(self, items):
        self.items = items

    def query(self, **kwargs):
        index = kwargs["IndexName"]
        key = {"GroupPermissionsIndex": "groupId", "UserPermissionsIndex": "userId"}[index]
        value = next(iter(kwargs["ExpressionAttributeValues"].values()))["S"]
        return {"Items": [copy.deepcopy(i) for i in self.items if i.get(key, {}).get("S") == value]}


@pytest.fixture
def enforcer_for():
    """A real CasbinEnforcer for USER_ID holding `roles`, reading `items` through the real batch read."""
    state = {"roles": []}
    table = _ConstraintsTable([])
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map

    def build(items, roles):
        state["roles"] = [{"userId": USER_ID, "roleName": name} for name in roles]
        table.items = items
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        return authz_module.CasbinEnforcer({"tokens": [USER_ID], "roles": list(roles), "mfaEnabled": True})

    with patch.object(
        authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
        side_effect=lambda *_args, **_kwargs: state["roles"],
    ), patch.object(authz_module, "_dynamodb_client", table), \
            patch.object(authz_module, "log_authorization", MagicMock()):
        try:
            yield build
        finally:
            authz_module.casbin_user_policy_map = previous_policy_map
            authz_module.casbin_user_enforcer_map = previous_enforcer_map


def _api_items(items):
    return [item for item in items if item["objectType"]["S"] == "api"]


# Of the shipped templates, only database-admin.json grants a database route (it also grants GET /buckets).
EXPECTED_TEMPLATE_DATABASE_ROUTES = {"database-admin.json": {"POST /database", "PUT /database/{databaseId}"}}


# (case id, the enforcer's answer, role constraints): every operator, value lists, AND with OR, other
# methods, deny lines, unusable fields and operators, and an unset permission type.
ALL_METHODS = [("GET", "allow"), ("PUT", "allow"), ("POST", "allow"), ("DELETE", "allow")]
ROLE_CASES = [
    ("equals", True, [_api([_route("equals", "/buckets")])]),
    ("equals-trailing-slash", False, [_api([_route("equals", "/buckets/")])]),
    ("equals-regex-wildcard", True, [_api([_route("equals", ".*")])]),
    ("starts-with", True, [_api([], [_route("starts_with", "/buckets")])]),
    ("starts-with-shorter", True, [_api([_route("starts_with", "/bucket")])]),
    ("starts-with-other", False, [_api([_route("starts_with", "/database")])]),
    ("starts-with-list", True, [_api([_route("starts_with", ["/assets", "/buck"])])]),
    ("contains", True, [_api([_route("contains", "bucket")])]),
    ("does-not-contain", False, [_api([_route("does_not_contain", "bucket")])]),
    ("does-not-contain-other", True, [_api([_route("does_not_contain", "database")])]),
    ("ends-with", True, [_api([_route("ends_with", "kets")])]),
    ("is-one-of-substring", True, [_api([_route("is_one_of", "/buckets")])]),
    ("is-one-of-other", False, [_api([_route("is_one_of", "/x")])]),
    ("is-one-of-list", True, [_api([_route("is_one_of", ["/x", "/buckets"])])]),
    ("is-not-one-of-other", True, [_api([_route("is_not_one_of", "/database")])]),
    ("is-not-one-of-list", False, [_api([_route("is_not_one_of", ["/x", "/buckets"])])]),
    ("and-both-hold", True, [_api([_route("starts_with", "/"), _route("ends_with", "s")])]),
    ("and-one-fails", False, [_api([_route("starts_with", "/"), _route("equals", "/x")])]),
    ("and-with-or-holds", True,
     [_api([_route("starts_with", "/")], [_route("equals", "/x"), _route("equals", "/buckets")])]),
    ("and-with-or-fails", False, [_api([_route("starts_with", "/")], [_route("equals", "/x")])]),
    ("post-only", False, [_api([_route("equals", "/buckets")], permissions=[("POST", "allow")])]),
    ("all-methods", True, [_api([], [_route("starts_with", "/buckets")], permissions=ALL_METHODS)]),
    ("deny-same-line", False,
     [_api([_route("equals", "/buckets")], permissions=[("GET", "allow"), ("GET", "deny")])]),
    ("deny-other-constraint", False,
     [_api([_route("starts_with", "/")], name="allow-all"),
      _api([_route("starts_with", "/b")], permissions=[("GET", "deny")], name="deny-b")]),
    ("deny-other-method", True,
     [_api([_route("starts_with", "/")], name="allow-all"),
      _api([_route("equals", "/buckets")], permissions=[("POST", "deny")], name="deny-post")]),
    ("deny-only", False, [_api([_route("equals", "/buckets")], permissions=[("GET", "deny")])]),
    ("unusable-field", False, [_api([_route("equals", "/buckets", field="databaseId")])]),
    ("unusable-and-usable-or", True,
     [_api([_route("equals", "/x", field="databaseId")], [_route("equals", "/buckets")])]),
    ("unknown-operator", False, [_api([{"field": "route__path", "operator": "regex", "value": "/buckets"}])]),
    ("no-criteria", False, [_api([], [])]),
    ("unset-permission-type", True, [_api([_route("equals", "/buckets")], permissions=[("GET", None)])]),
    ("unexpected-permission-type", False, [_api([_route("equals", "/buckets")], permissions=[("GET", "ALLOW")])]),
]


@pytest.mark.unit
class TestTheRouteDecision:
    @pytest.mark.parametrize("case, expected, constraints", ROLE_CASES, ids=[case for case, _, _ in ROLE_CASES])
    def test_a_role_is_allowed_the_buckets_route_exactly_when_the_enforcer_allows_it(
            self, case, expected, constraints, enforcer_for):
        items = _stored_items(constraints)
        decisions = step.decide_route(_api_items(items), step.BUCKETS_ROUTE_OBJECT, step.BUCKETS_ROUTE_ACTION)
        assert ("role", ROLE) not in decisions.unreadable
        assert enforcer_for(items, [ROLE]).enforce(ROUTE, "GET") is expected
        assert decisions.allowed(("role", ROLE)) is expected

    @pytest.mark.parametrize("allowed", [True, False])
    def test_a_user_level_grant_is_decided_as_the_enforcer_decides_it(self, allowed, enforcer_for):
        value = "/buckets" if allowed else "/database"
        items = _stored_items([_api([_route("equals", value)], user=USER_ID)])
        decisions = step.decide_route(_api_items(items), step.BUCKETS_ROUTE_OBJECT, step.BUCKETS_ROUTE_ACTION)
        assert decisions.allowed(("user", USER_ID)) is allowed
        assert enforcer_for(items, []).enforce(ROUTE, "GET") is allowed

    @pytest.mark.parametrize("template_name", shipped_constraints.template_names())
    def test_a_role_imported_from_a_template_before_2_7_is_decided_as_the_enforcer_decides_it(
            self, template_name, enforcer_for):
        # Before 2.7.0 a template carried no bucket constraint: the role an import then stored.
        constraints = [c for c in shipped_constraints.template_constraints(template_name, ROLE, TEMPLATE_VALUES)
                       if c["objectType"] != "bucket"]
        items = _stored_items(constraints)
        decisions = step.decide_route(_api_items(items), step.BUCKETS_ROUTE_OBJECT, step.BUCKETS_ROUTE_ACTION)
        assert ("role", ROLE) not in decisions.unreadable
        assert decisions.allowed(("role", ROLE)) is enforcer_for(items, [ROLE]).enforce(ROUTE, "GET")

    def test_only_the_database_admin_template_holds_the_route_so_the_template_cases_are_not_vacuous(self):
        holding = set()
        for template_name in shipped_constraints.template_names():
            constraints = shipped_constraints.template_constraints(template_name, ROLE, TEMPLATE_VALUES)
            decisions = step.decide_route(_api_items(_stored_items(constraints)), step.BUCKETS_ROUTE_OBJECT,
                                          step.BUCKETS_ROUTE_ACTION)
            if decisions.allowed(("role", ROLE)):
                holding.add(template_name)
        assert holding == {"database-admin.json"}

    @pytest.mark.parametrize("criterion", [
        _route("equals", "("),
        {"field": "route__path", "operator": "equals"},
    ])
    def test_a_constraint_it_cannot_evaluate_is_never_read_as_allowed(self, criterion):
        items = _stored_items([_api([_route("starts_with", "/")], name="allow-all"),
                               _api([criterion], permissions=[("GET", "deny")], name="broken-deny")])
        decisions = step.decide_route(_api_items(items), step.BUCKETS_ROUTE_OBJECT, step.BUCKETS_ROUTE_ACTION)
        # The allow line alone would allow the route; the step reports the role and writes nothing for it.
        assert ("role", ROLE) in decisions.unreadable


def _enforcer_routes(enforcer):
    """The routes the enforcer allows: a route is allowed when any of its request paths is."""
    return {route for route, (action, paths) in ROUTE_REQUESTS.items()
            if any(enforcer.enforce({"object__type": "api", "route__path": path}, action) for path in paths)}


def _step_routes(items, principal=("role", ROLE)):
    found = step.qualify(_api_items(items), DATABASES)
    assert principal not in found.unreadable
    return set(found.qualified_by(principal))


CREATE, UPDATE, LIST = "POST /database", "PUT /database/{databaseId}", "GET /buckets"

# (case id, the routes the enforcer allows, role constraints): the create path, the update path of a database
# the deployment holds, one it does not hold, sub-routes, methods, deny lines, and the route template text.
DATABASE_CASES = [
    ("create-equals", {CREATE}, [_api([_route("equals", "/database")], permissions=[("POST", "allow")])]),
    ("create-get-only", set(), [_api([_route("equals", "/database")], permissions=[("GET", "allow")])]),
    ("create-and-update-starts-with", {CREATE, UPDATE},
     [_api([_route("starts_with", "/database")], permissions=ALL_METHODS)]),
    ("update-one-database", {UPDATE},
     [_api([_route("starts_with", "/database/db-a")], permissions=[("PUT", "allow")])]),
    ("update-database-not-held", set(),
     [_api([_route("starts_with", "/database/db-z")], permissions=[("PUT", "allow")])]),
    ("update-subroute-only", set(),
     [_api([_route("starts_with", "/database/db-a/assets")], permissions=[("PUT", "allow")])]),
    ("update-ends-with-id", {UPDATE}, [_api([_route("ends_with", "/db-b")], permissions=[("PUT", "allow")])]),
    ("update-route-template-text", set(),
     [_api([_route("equals", "/database/{databaseId}")], permissions=[("PUT", "allow")])]),
    ("update-deny-one-database", {UPDATE},
     [_api([_route("starts_with", "/database/")], permissions=[("PUT", "allow")], name="allow-all"),
      _api([_route("equals", "/database/db-a")], permissions=[("PUT", "deny")], name="deny-a")]),
    ("update-deny-every-database", set(),
     [_api([_route("starts_with", "/database/")], permissions=[("PUT", "allow")], name="allow-all"),
      _api([_route("starts_with", "/database/")], permissions=[("PUT", "deny")], name="deny-all")]),
    ("create-denied-update-allowed", {UPDATE},
     [_api([_route("starts_with", "/")], permissions=[("POST", "allow"), ("PUT", "allow")], name="allow-all"),
      _api([_route("equals", "/database")], permissions=[("POST", "deny")], name="deny-create")]),
    ("or-criteria", {CREATE, UPDATE},
     [_api([], [_route("equals", "/database"), _route("starts_with", "/database/db-b")],
           permissions=[("POST", "allow"), ("PUT", "allow")])]),
    ("is-not-one-of-database", {CREATE, UPDATE},
     [_api([_route("is_not_one_of", "/assets")], permissions=[("POST", "allow"), ("PUT", "allow")])]),
    # The 2.6 database-user.json api routes: one constraint per method, each `starts_with /database`. A role
    # imported from it before 2.7.0 created and updated databases on any bucket, and keeps that.
    ("v2.6-database-user-routes", {CREATE, UPDATE},
     [_api([_route("starts_with", "/database")], permissions=[(method, "allow")], name=f"routes-{method}")
      for method in ("GET", "POST", "PUT")]),
    ("buckets-and-update", {LIST, UPDATE},
     [_api([_route("is_one_of", ["/buckets", "/database/db-a"])], permissions=[("GET", "allow"), ("PUT", "allow")])]),
]


@pytest.mark.unit
class TestTheDatabaseRoutes:
    @pytest.mark.parametrize("case, expected, constraints", DATABASE_CASES,
                             ids=[case for case, _, _ in DATABASE_CASES])
    def test_a_role_is_allowed_each_route_exactly_when_the_enforcer_allows_it(
            self, case, expected, constraints, enforcer_for):
        items = _stored_items(constraints)
        assert _enforcer_routes(enforcer_for(items, [ROLE])) == expected
        assert _step_routes(items) == expected

    @pytest.mark.parametrize("case, expected, constraints", ROLE_CASES, ids=[case for case, _, _ in ROLE_CASES])
    def test_the_buckets_route_cases_are_decided_the_same_way_with_the_database_routes(
            self, case, expected, constraints, enforcer_for):
        items = _stored_items(constraints)
        routes = _enforcer_routes(enforcer_for(items, [ROLE]))
        assert (LIST in routes) is expected
        assert _step_routes(items) == routes

    @pytest.mark.parametrize("value, action, route", [
        ("/database", "POST", CREATE), ("/database/db-b", "PUT", UPDATE), ("/database/db-z", "PUT", None)])
    def test_a_user_level_grant_of_a_database_route_is_decided_as_the_enforcer_decides_it(
            self, value, action, route, enforcer_for):
        items = _stored_items([_api([_route("equals", value)], permissions=[(action, "allow")], user=USER_ID)])
        expected = {route} if route else set()
        assert _enforcer_routes(enforcer_for(items, [])) == expected
        assert _step_routes(items, ("user", USER_ID)) == expected

    @pytest.mark.parametrize("template_name", shipped_constraints.template_names())
    def test_a_role_imported_from_a_template_before_2_7_is_decided_as_the_enforcer_decides_it(
            self, template_name, enforcer_for):
        constraints = [c for c in shipped_constraints.template_constraints(template_name, ROLE, TEMPLATE_VALUES)
                       if c["objectType"] != "bucket"]
        items = _stored_items(constraints)
        assert _step_routes(items) == _enforcer_routes(enforcer_for(items, [ROLE]))

    def test_the_templates_that_hold_a_database_route_so_the_template_cases_are_not_vacuous(self):
        holding = {}
        for template_name in shipped_constraints.template_names():
            constraints = shipped_constraints.template_constraints(template_name, ROLE, TEMPLATE_VALUES)
            routes = _step_routes(_stored_items(constraints)) - {LIST}
            if routes:
                holding[template_name] = routes
        assert holding == EXPECTED_TEMPLATE_DATABASE_ROUTES

    def test_the_update_route_is_decided_for_the_databases_given_and_no_other(self):
        items = _stored_items([_api([_route("starts_with", "/database/")], permissions=[("PUT", "allow")])])
        assert step.qualify(_api_items(items), []).qualified_by(("role", ROLE)) == []
        found = step.qualify(_api_items(items), ["db-b", "db-a"])
        assert found.qualified_by(("role", ROLE)) == [UPDATE]
        assert found.update_database[("role", ROLE)] == "db-b"


def _bucket(name="team-a-assets", source="static"):
    return {"object__type": "bucket", "bucketId": "b-1", "bucketName": name, "bucketRegion": "us-east-1",
            "source": source}


@pytest.mark.unit
class TestTheWrittenConstraint:
    def _template_import_item(self, role):
        document = json.loads((shipped_constraints.TEMPLATES_DIR / "database-admin.json").read_text(encoding="utf-8"))
        bucket = [c for c in document["constraints"] if c["objectType"] == "bucket"]
        substituted = template_service.substitute_variables(bucket, {"ROLE_NAME": role, "DATABASE_ID": "db-a"})
        data = template_service.build_constraint_data(substituted[0], role, {"tokens": ["SYSTEM_USER"]})
        return template_service._transform_to_denormalized_format(data)[0]

    def test_it_is_the_item_a_template_import_writes(self):
        written = {key: next(iter(value.values()))
                   for key, value in step.build_bucket_read_item("tpl", "2026-12-01T00:00:00+00:00").items()}
        imported = self._template_import_item("tpl")
        assert set(written) == set(imported)
        for key in ("groupId", "name", "description", "objectType", "createdBy", "modifiedBy"):
            assert written[key] == imported[key], key
        for key in ("criteriaAnd", "criteriaOr", "userPermissions"):
            assert json.loads(written[key]) == json.loads(imported[key]), key

        def without_ids(permissions):
            return [{k: v for k, v in p.items() if k != "id"} for p in json.loads(permissions)]

        assert without_ids(written["groupPermissions"]) == without_ids(imported["groupPermissions"])
        assert written["constraintId"] == f"{step.bucket_read_constraint_id('tpl')}#group#tpl"

    def test_the_authorizer_reads_it_as_get_on_every_registration_and_nothing_else(self, enforcer_for):
        enforcer = enforcer_for([step.build_bucket_read_item("tpl", "2026-12-01T00:00:00+00:00")], ["tpl"])
        assert enforcer.enforce(_bucket(), "GET") is True
        assert enforcer.enforce(_bucket(name="anything", source="runtime"), "GET") is True
        for action in ("PUT", "POST", "DELETE"):
            assert enforcer.enforce(_bucket(), action) is False
        assert enforcer.enforce(ROUTE, "GET") is False

    def test_the_same_role_always_gets_the_same_id_and_two_roles_never_share_one(self):
        assert step.bucket_read_constraint_id("tpl") == step.bucket_read_constraint_id("tpl")
        assert step.bucket_read_constraint_id("tpl") != step.bucket_read_constraint_id("tpl2")
