# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Each compliance API call writes the VAMS action record its route-method is registered with.

Every compliance route-method is in the audit action registry (``common/auditActions.py``). The
handlers open the request's audit context and record the route's action with ``log_action`` once
the service function answered: a 2xx as a success, a 403 as a denial (written to the authorization
log), anything else as a failure with a generic reason. A call the API-level check refuses, or that
raises, records the route's own action (``log_action`` without an action name) as denied or failed.
"""

import importlib
import json
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import claims_for, rest_event
from common.apiRoutes import COMPLIANCE_ROUTES
from common.compliance import routeAudit
from handlers.compliance import (complianceAuditService, complianceCascadeService,
                                 complianceEvaluateService, complianceQuarantineService,
                                 complianceSchemaBindingService, complianceSchemaService)

MODULES = {module.__name__.rsplit(".", 1)[-1]: module for module in (
    complianceAuditService, complianceCascadeService, complianceEvaluateService,
    complianceQuarantineService, complianceSchemaBindingService, complianceSchemaService)}

REGISTRY = importlib.import_module("common.auditActions")

SCHEMA_BODY = {"schemaFormat": "vams-rules-v1", "rules": {}}

# (module, method, route template, concrete path, path parameters, request body, the service
# function the branch calls, the list key a read response carries)
CALLS = [
    ("complianceSchemaService", "GET", "/compliance/schemas", "/compliance/schemas", None, None,
     "list_schemas", "schemas"),
    ("complianceSchemaService", "POST", "/compliance/schemas", "/compliance/schemas", None,
     {"schemaName": "cad-quality", "schemaBody": SCHEMA_BODY}, "register_schema", None),
    ("complianceSchemaService", "GET", "/compliance/schemas/{schemaName}", "/compliance/schemas/s1",
     {"schemaName": "s1"}, None, "get_schema", None),
    ("complianceSchemaService", "PUT", "/compliance/schemas/{schemaName}", "/compliance/schemas/s1",
     {"schemaName": "s1"}, {"description": "new"}, "update_schema", None),
    ("complianceSchemaService", "DELETE", "/compliance/schemas/{schemaName}", "/compliance/schemas/s1",
     {"schemaName": "s1"}, None, "delete_schema", None),
    ("complianceSchemaBindingService", "GET", "/compliance/bind/{databaseId}", "/compliance/bind/db1",
     {"databaseId": "db1"}, None, "get_bindings", "assetOverrides"),
    ("complianceSchemaBindingService", "PUT", "/compliance/bind/{databaseId}", "/compliance/bind/db1",
     {"databaseId": "db1"}, {"schemaName": "cad-quality"}, "bind_schema_to_database", None),
    ("complianceSchemaBindingService", "DELETE", "/compliance/bind/{databaseId}", "/compliance/bind/db1",
     {"databaseId": "db1"}, None, "unbind_schema_from_database", None),
    ("complianceSchemaBindingService", "PUT", "/compliance/bind/{databaseId}/{assetId}",
     "/compliance/bind/db1/a1", {"databaseId": "db1", "assetId": "a1"}, {"schemaName": "cad-quality"},
     "bind_schema_to_asset", None),
    ("complianceSchemaBindingService", "DELETE", "/compliance/bind/{databaseId}/{assetId}",
     "/compliance/bind/db1/a1", {"databaseId": "db1", "assetId": "a1"}, None,
     "unbind_schema_from_asset", None),
    ("complianceEvaluateService", "POST", "/compliance/evaluate/{databaseId}/{assetId}",
     "/compliance/evaluate/db1/a1", {"databaseId": "db1", "assetId": "a1"}, {}, "evaluate_asset", None),
    ("complianceEvaluateService", "POST", "/compliance/evaluate/{databaseId}", "/compliance/evaluate/db1",
     {"databaseId": "db1"}, {}, "evaluate_database", None),
    ("complianceEvaluateService", "POST", "/compliance/sweep/{schemaName}", "/compliance/sweep/s1",
     {"schemaName": "s1"}, {}, "sweep_schema", None),
    ("complianceEvaluateService", "GET", "/compliance/evaluations/{databaseId}/{assetId}",
     "/compliance/evaluations/db1/a1", {"databaseId": "db1", "assetId": "a1"}, None, "get_evaluations",
     "evaluations"),
    ("complianceEvaluateService", "GET", "/compliance/state/{databaseId}/{assetId}",
     "/compliance/state/db1/a1", {"databaseId": "db1", "assetId": "a1"}, None, "get_compliance_state", None),
    ("complianceEvaluateService", "GET", "/compliance/state/{databaseId}", "/compliance/state/db1",
     {"databaseId": "db1"}, None, "get_database_compliance_overview", "assets"),
    ("complianceQuarantineService", "GET", "/compliance/quarantine", "/compliance/quarantine", None, None,
     "list_quarantined", "quarantinedAssets"),
    ("complianceQuarantineService", "POST", "/compliance/quarantine/{databaseId}/{assetId}/release",
     "/compliance/quarantine/db1/a1/release", {"databaseId": "db1", "assetId": "a1"}, {},
     "release_quarantine", None),
    ("complianceQuarantineService", "POST", "/compliance/quarantine/{databaseId}/{assetId}/exception",
     "/compliance/quarantine/db1/a1/exception", {"databaseId": "db1", "assetId": "a1"},
     {"reason": "waiver"}, "grant_exception", None),
    ("complianceQuarantineService", "DELETE", "/compliance/quarantine/{databaseId}/{assetId}/exception",
     "/compliance/quarantine/db1/a1/exception", {"databaseId": "db1", "assetId": "a1"}, None,
     "revoke_exception", None),
    ("complianceCascadeService", "GET", "/compliance/cascades", "/compliance/cascades", None, None,
     "list_pending_cascades", "cascades"),
    ("complianceCascadeService", "POST", "/compliance/cascades", "/compliance/cascades", None,
     {"databaseId": "db1", "assetId": "a1"}, "create_cascade", None),
    ("complianceCascadeService", "GET", "/compliance/cascades/{cascadeId}", "/compliance/cascades/c1",
     {"cascadeId": "c1"}, None, "get_cascade", None),
    ("complianceCascadeService", "POST", "/compliance/cascades/{cascadeId}/approve",
     "/compliance/cascades/c1/approve", {"cascadeId": "c1"}, {}, "approve_cascade", None),
    ("complianceCascadeService", "POST", "/compliance/cascades/{cascadeId}/reject",
     "/compliance/cascades/c1/reject", {"cascadeId": "c1"}, {}, "reject_cascade", None),
    ("complianceAuditService", "GET", "/compliance/audit", "/compliance/audit", None, None,
     "query_audit", "entries"),
    ("complianceAuditService", "GET", "/compliance/audit/{databaseId}/{assetId}",
     "/compliance/audit/db1/a1", {"databaseId": "db1", "assetId": "a1"}, None, "get_asset_audit", "entries"),
]

CALL_IDS = [f"{method} {template}" for _m, method, template, *_rest in CALLS]


def _response(status, list_key=None):
    body = {"message": "ok"}
    if list_key:
        body[list_key] = [{"id": "x"}, {"id": "y"}]
    return {"statusCode": status, "body": json.dumps(body)}


@contextmanager
def _invoked(module_name, service_function, response=None, side_effect=None, api_allowed=True):
    """Patch one compliance handler module so its entrypoint runs the real dispatch with the service
    function stubbed. Yields the recorded log_action calls and the audit contexts opened."""
    module = MODULES[module_name]
    records, contexts = [], []

    @contextmanager
    def _audit_request(event, lambda_context=None, **_kwargs):
        contexts.append(lambda_context)
        yield None

    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = api_allowed
    stub = MagicMock(return_value=response, side_effect=side_effect)
    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "request_to_claims", claims_for("alice")))
        stack.enter_context(patch.object(module, "CasbinEnforcer", enforcer))
        stack.enter_context(patch.object(module, "audit_request", _audit_request))
        stack.enter_context(patch.object(
            module, "log_action", lambda event, action=None, **kwargs: records.append((action, kwargs))))
        stack.enter_context(patch.object(module, service_function, stub))
        if hasattr(module, "flush_denials"):
            stack.enter_context(patch.object(module, "flush_denials"))
        yield SimpleNamespace(module=module, records=records, contexts=contexts, stub=stub)


def _call(module, method, path, path_params, body):
    return module.lambda_handler(rest_event(method, path, path_params=path_params, body=body),
                                 SimpleNamespace(aws_request_id="lambda-1"))


@pytest.mark.unit
def test_the_table_covers_every_compliance_route_method():
    covered = {(template, method) for _m, method, template, *_rest in CALLS}
    expected = {(route.path, method) for route in COMPLIANCE_ROUTES for method in route.methods}
    assert covered == expected


@pytest.mark.unit
@pytest.mark.parametrize("call", CALLS, ids=CALL_IDS)
def test_a_successful_call_records_its_registered_action(call):
    module_name, method, template, path, path_params, body, function, list_key = call
    expected = REGISTRY.audit_spec_for_route(template, method)
    assert expected is not None, (template, method)
    with _invoked(module_name, function, response=_response(200, list_key)) as run:
        response = _call(run.module, method, path, path_params, body)
    assert response["statusCode"] == 200
    run.stub.assert_called_once()
    assert run.contexts == [run.contexts[0]] and run.contexts[0].aws_request_id == "lambda-1", \
        "the entrypoint opens one audit context with the Lambda context"
    assert len(run.records) == 1, run.records
    action, kwargs = run.records[0]
    assert action == expected.action
    assert kwargs["outcome"] == "success" and "reason" not in kwargs
    if expected.kind == "read" and list_key:
        assert kwargs["item_count"] == 2
    if expected.kind == "read":
        assert "changes" not in kwargs, "a read records what was asked, never what it set"


@pytest.mark.unit
@pytest.mark.parametrize("status,outcome,reason", [
    (403, "denied", "forbidden"), (404, "failed", "notFound"), (400, "failed", "validation"),
    (409, "failed", "conflict"), (500, "failed", "internal")])
@pytest.mark.parametrize("call", CALLS, ids=CALL_IDS)
def test_a_refused_or_failed_answer_records_its_outcome(call, status, outcome, reason):
    module_name, method, template, path, path_params, body, function, list_key = call
    with _invoked(module_name, function, response=_response(status, list_key)) as run:
        response = _call(run.module, method, path, path_params, body)
    assert response["statusCode"] == status
    assert [(action, kwargs["outcome"], kwargs["reason"]) for action, kwargs in run.records] == [
        (REGISTRY.audit_spec_for_route(template, method).action, outcome, reason)]


@pytest.mark.unit
@pytest.mark.parametrize("call", CALLS, ids=CALL_IDS)
def test_an_api_level_refusal_records_the_routes_action_as_denied(call):
    module_name, method, _template, path, path_params, body, function, _key = call
    with _invoked(module_name, function, response=_response(200), api_allowed=False) as run:
        response = _call(run.module, method, path, path_params, body)
    assert response["statusCode"] == 403
    run.stub.assert_not_called()
    assert run.records == [(None, {"outcome": "denied", "reason": "forbidden"})]


@pytest.mark.unit
@pytest.mark.parametrize("call", CALLS, ids=CALL_IDS)
def test_a_call_that_raises_records_the_routes_action_as_failed(call):
    module_name, method, _template, path, path_params, body, function, _key = call
    with _invoked(module_name, function, side_effect=RuntimeError("boom")) as run:
        response = _call(run.module, method, path, path_params, body)
    assert response["statusCode"] == 500
    assert run.records == [(None, {"outcome": "failed", "reason": "internal"})]


@pytest.mark.unit
def test_a_schema_body_is_never_recorded():
    """The rule document is caller-authored content: the create and update records name the schema
    and the fields set, and say only whether the body changed."""
    marker = {"schemaFormat": "vams-rules-v1", "rules": {"body-marker": {}}}
    for method, path, params, body, function in (
            ("POST", "/compliance/schemas", None, {"schemaName": "cad-quality", "schemaBody": marker},
             "register_schema"),
            ("PUT", "/compliance/schemas/cad-quality", {"schemaName": "cad-quality"}, {"schemaBody": marker},
             "update_schema")):
        with _invoked("complianceSchemaService", function, response=_response(200)) as run:
            _call(run.module, method, path, params, body)
        (_action, kwargs), = run.records
        assert "body-marker" not in json.dumps(kwargs, default=str), method
        assert kwargs["schemaName"] == "cad-quality"


@pytest.mark.unit
def test_a_continuation_token_is_recorded_as_sent_never_its_value():
    assert routeAudit.request_parameters({"maxItems": "50", "startingToken": "secret-token", "text": ""},
                                         ("maxItems", "startingToken", "text")) == {
        "maxItems": "50", "startingTokenSent": True}


@pytest.mark.unit
def test_with_read_records_off_a_read_response_is_not_parsed_to_count(monkeypatch):
    """app.auditLogging.recordReads false: log_action writes no successful read record, so the read
    response body is not parsed for its item count either."""
    monkeypatch.setenv("AUDIT_RECORD_READS", "false")
    assert routeAudit.response_count(_response(200, "entries"), "entries") is None
    monkeypatch.setenv("AUDIT_RECORD_READS", "true")
    assert routeAudit.response_count(_response(200, "entries"), "entries") == 2
