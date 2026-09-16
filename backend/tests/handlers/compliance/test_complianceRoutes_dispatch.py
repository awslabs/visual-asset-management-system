# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every method+path pair in `COMPLIANCE_ROUTES` reaches exactly one business function of exactly one
compliance handler, and every other method on the same path is refused with "Method not allowed".

Driven by the route registry rather than by a transcribed path list, and pinned to the registry's
size: a route added, removed or renamed in `common/apiRoutes.py` fails here until the dispatch table
below -- and the handler -- follow it. The per-handler suites cover what each function does; this one
covers only that the right function is the one that runs."""

import importlib
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, CASCADE_ID, DB, SCHEMA, USER, body_of, claims_for, enforcer, rest_event,
)
from handlers.compliance import (
    complianceAuditService,
    complianceCascadeService,
    complianceEvaluateService,
    complianceQuarantineService,
    complianceSchemaBindingService,
    complianceSchemaService,
)
from models.common import success

# Resolved through the import system rather than as an attribute of `common`: the harness replaces
# `common` with a MagicMock, whose auto-created attributes shadow the real submodule on
# `import common.apiRoutes as ...`, while `import_module` returns the registered real module.
api_routes = importlib.import_module("common.apiRoutes")

ALL_METHODS = ("GET", "POST", "PUT", "DELETE")

PLACEHOLDERS = {"{schemaName}": SCHEMA, "{databaseId}": DB, "{assetId}": ASSET,
                "{cascadeId}": CASCADE_ID}

# Bodies the request models of the mutating routes accept; the function behind them is patched, so
# the body only has to parse.
BODIES = {
    ("POST", "/compliance/schemas"): {"schemaName": SCHEMA, "schemaBody": {"type": "object"}},
    ("PUT", "/compliance/bind/{databaseId}"): {"schemaName": SCHEMA},
    ("PUT", "/compliance/bind/{databaseId}/{assetId}"): {"schemaName": SCHEMA},
    ("POST", "/compliance/quarantine/{databaseId}/{assetId}/exception"): {"reason": "reviewed"},
    ("POST", "/compliance/cascades"): {"databaseId": DB, "assetId": ASSET},
}

# (route constant, method) -> (handler module, business function).
DISPATCH = {
    (api_routes.API_COMPLIANCE_SCHEMAS, "GET"): (complianceSchemaService, "list_schemas"),
    (api_routes.API_COMPLIANCE_SCHEMAS, "POST"): (complianceSchemaService, "register_schema"),
    (api_routes.API_COMPLIANCE_SCHEMA_BY_NAME, "GET"): (complianceSchemaService, "get_schema"),
    (api_routes.API_COMPLIANCE_SCHEMA_BY_NAME, "PUT"): (complianceSchemaService, "update_schema"),
    (api_routes.API_COMPLIANCE_SCHEMA_BY_NAME, "DELETE"): (complianceSchemaService, "delete_schema"),
    (api_routes.API_COMPLIANCE_BIND_DATABASE, "GET"): (complianceSchemaBindingService, "get_bindings"),
    (api_routes.API_COMPLIANCE_BIND_DATABASE, "PUT"): (
        complianceSchemaBindingService, "bind_schema_to_database"),
    (api_routes.API_COMPLIANCE_BIND_DATABASE, "DELETE"): (
        complianceSchemaBindingService, "unbind_schema_from_database"),
    (api_routes.API_COMPLIANCE_BIND_ASSET, "PUT"): (
        complianceSchemaBindingService, "bind_schema_to_asset"),
    (api_routes.API_COMPLIANCE_BIND_ASSET, "DELETE"): (
        complianceSchemaBindingService, "unbind_schema_from_asset"),
    (api_routes.API_COMPLIANCE_EVALUATE_ASSET, "POST"): (complianceEvaluateService, "evaluate_asset"),
    (api_routes.API_COMPLIANCE_SWEEP_SCHEMA, "POST"): (complianceEvaluateService, "sweep_schema"),
    (api_routes.API_COMPLIANCE_EVALUATIONS_ASSET, "GET"): (
        complianceEvaluateService, "get_evaluations"),
    (api_routes.API_COMPLIANCE_STATE_ASSET, "GET"): (
        complianceEvaluateService, "get_compliance_state"),
    (api_routes.API_COMPLIANCE_STATE_DATABASE, "GET"): (
        complianceEvaluateService, "get_database_compliance_overview"),
    (api_routes.API_COMPLIANCE_QUARANTINE, "GET"): (complianceQuarantineService, "list_quarantined"),
    (api_routes.API_COMPLIANCE_QUARANTINE_RELEASE, "POST"): (
        complianceQuarantineService, "release_quarantine"),
    (api_routes.API_COMPLIANCE_QUARANTINE_EXCEPTION, "POST"): (
        complianceQuarantineService, "grant_exception"),
    (api_routes.API_COMPLIANCE_QUARANTINE_EXCEPTION, "DELETE"): (
        complianceQuarantineService, "revoke_exception"),
    (api_routes.API_COMPLIANCE_CASCADES, "GET"): (complianceCascadeService, "list_pending_cascades"),
    (api_routes.API_COMPLIANCE_CASCADES, "POST"): (complianceCascadeService, "create_cascade"),
    (api_routes.API_COMPLIANCE_CASCADE_BY_ID, "GET"): (complianceCascadeService, "get_cascade"),
    (api_routes.API_COMPLIANCE_CASCADE_APPROVE, "POST"): (complianceCascadeService, "approve_cascade"),
    (api_routes.API_COMPLIANCE_CASCADE_REJECT, "POST"): (complianceCascadeService, "reject_cascade"),
    (api_routes.API_COMPLIANCE_AUDIT, "GET"): (complianceAuditService, "query_audit"),
    (api_routes.API_COMPLIANCE_AUDIT_ASSET, "GET"): (complianceAuditService, "get_asset_audit"),
}

HANDLERS = {module for module, _ in DISPATCH.values()}


def _concrete(route):
    """The route template with its placeholders filled, and the matching pathParameters."""
    path = route.path
    params = {}
    for placeholder, value in PLACEHOLDERS.items():
        if placeholder in path:
            path = path.replace(placeholder, value)
            params[placeholder.strip("{}")] = value
    return path, params or None


def _invoke(module, event):
    """Run one handler with an allowing identity and every business function of every compliance
    handler patched to report which one ran."""
    patches = [patch.object(module, "request_to_claims", claims_for(USER)),
               patch.object(module, "CasbinEnforcer", return_value=enforcer())]
    for handler in HANDLERS:
        for target, name in DISPATCH.values():
            if target is handler:
                patches.append(patch.object(
                    handler, name,
                    return_value=success(body={"reached": f"{handler.__name__}.{name}"})))
    for p in patches:
        p.start()
    try:
        return module.lambda_handler(event, MagicMock())
    finally:
        for p in reversed(patches):
            p.stop()


def _pair_id(pair):
    route, method = pair
    return f"{method} {route.path}"


@pytest.mark.unit
class TestTheDispatchTableMatchesTheRegistry:

    def test_every_registry_pair_has_exactly_one_dispatch_entry(self):
        registry_pairs = {(route, method) for route in api_routes.COMPLIANCE_ROUTES
                          for method in route.methods}
        assert set(DISPATCH) == registry_pairs
        assert len(api_routes.COMPLIANCE_ROUTES) == 18
        assert len(registry_pairs) == 26

    def test_every_route_is_served_by_one_handler(self):
        handlers_by_route = {}
        for (route, _), (module, _) in DISPATCH.items():
            handlers_by_route.setdefault(route, set()).add(module)
        assert all(len(modules) == 1 for modules in handlers_by_route.values())


@pytest.mark.unit
class TestEveryPairReachesItsFunction:

    @pytest.mark.parametrize("pair", sorted(DISPATCH, key=_pair_id), ids=_pair_id)
    def test_the_pair_reaches_its_function(self, pair):
        route, method = pair
        module, function_name = DISPATCH[pair]
        path, params = _concrete(route)
        body = BODIES.get((method, route.path), {} if method in ("POST", "PUT") else None)
        response = _invoke(module, rest_event(method, path, params, body=body))
        assert response["statusCode"] == 200, response
        assert body_of(response) == {"reached": f"{module.__name__}.{function_name}"}

    @pytest.mark.parametrize("route", api_routes.COMPLIANCE_ROUTES, ids=lambda r: r.path)
    def test_the_other_methods_on_the_path_are_refused(self, route):
        module = next(module for (r, _), (module, _) in DISPATCH.items() if r is route)
        path, params = _concrete(route)
        for method in ALL_METHODS:
            if method in route.methods:
                continue
            response = _invoke(module, rest_event(method, path, params, body={}))
            assert response["statusCode"] == 400, (method, path, response)
            assert body_of(response)["message"] == "Method not allowed", (method, path)

    @pytest.mark.parametrize("module", sorted(HANDLERS, key=lambda m: m.__name__),
                             ids=lambda m: m.__name__.rsplit(".", 1)[-1])
    def test_a_path_outside_the_registry_is_refused(self, module):
        for method in ALL_METHODS:
            response = _invoke(module, rest_event(method, "/compliance/nothing-here", body={}))
            assert response["statusCode"] == 400, (method, response)
            assert body_of(response)["message"] == "Method not allowed"

    @pytest.mark.parametrize("module", sorted(HANDLERS, key=lambda m: m.__name__),
                             ids=lambda m: m.__name__.rsplit(".", 1)[-1])
    def test_a_rest_event_with_null_parameters_does_not_crash(self, module):
        """The REST "no params" shape (Rule 16): `pathParameters` and `queryStringParameters` are
        JSON null. Every collection-level GET the handler serves must answer, not 500."""
        collection_routes = [route for (route, method), (target, _) in DISPATCH.items()
                             if target is module and method == "GET" and "{" not in route.path]
        for route in collection_routes:
            response = _invoke(module, rest_event("GET", route.path))
            assert response["statusCode"] == 200, (route.path, response)
        response = _invoke(module, rest_event("GET", "/compliance/nothing-here"))
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Method not allowed"
