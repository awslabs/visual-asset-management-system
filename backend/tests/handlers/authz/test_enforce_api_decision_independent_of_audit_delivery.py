# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The API-level authorization decision does not depend on how its audit record is delivered.

``CasbinEnforcer.enforceAPI`` computes the decision, then hands it to ``log_authorization_api``. Inside a
request audit context an allowed record is buffered for the request's flush; outside one it is written at
once. These tests drive the REAL ``enforceAPI`` with an enforcer built by the production factory from a
real policy, wired to the REAL ``customLogging/auditLogging.py`` (loaded by path, since this directory's
conftest installs the audit mocks), and require the same decision for every route with and without a
context, and when the audit delivery itself fails. They also check the record took the delivery path the
context implies, so the comparison is not made against an audit path that never ran.
"""

import importlib.util
import os
import sys
import threading
import types
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

for _log_type, _env in (("auditActions", "AUDIT_LOG_ACTIONS"), ("auditAuthorization", "AUDIT_LOG_AUTHORIZATION"),
                        ("auditFileUpload", "AUDIT_LOG_FILEUPLOAD"), ("auditFileDownload", "AUDIT_LOG_FILEDOWNLOAD"),
                        ("auditFileDownloadStreamed", "AUDIT_LOG_FILEDOWNLOAD_STREAMED"),
                        ("auditAuthOther", "AUDIT_LOG_AUTHOTHER"), ("auditAuthChanges", "AUDIT_LOG_AUTHCHANGES"),
                        ("auditErrors", "AUDIT_LOG_ERRORS"), ("auditAuthentication", "AUDIT_LOG_AUTHENTICATION")):
    os.environ.setdefault(_env, f"test-{_log_type}")

import backend.backend.handlers.authz as authz  # noqa: E402
from backend.backend.handlers.authz import CasbinEnforcer, CasbinEnforcerService  # noqa: E402

_AUDIT_LOGGING_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "customLogging", "auditLogging.py"))
_MOCKS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mocks"))

USER_ID = "decision-user"
CLAIMS = {"tokens": [USER_ID], "roles": ["roleA"], "mfaEnabled": True}

# (method, path): allowed under /database, denied on the asset delete route, and no line for /roles.
ROUTES = [
    ("GET", "/database/db1/assets/a1"),
    ("PUT", "/database/db1/assets/a1/metadata"),
    ("DELETE", "/database/db1/assets/a1/delete"),
    ("GET", "/roles"),
]


def _stub_module(name, path=None, **attributes):
    module = types.ModuleType(name)
    if path is not None:
        module.__path__ = [path]
    module.__dict__.update(attributes)
    return module


@pytest.fixture(scope="module")
def real_audit():
    """The production audit module, under its own name so the directory's mocks stay in place.

    Its imports are resolved against the real ``common`` package and small stand-ins for the claims
    and logger modules, installed only while it loads: what other test files left in ``sys.modules``
    (the repo-wide mocks, or another file's stubs) does not decide what it imports, and the modules
    are restored afterwards.
    """
    backend_dir = os.path.dirname(os.path.dirname(_AUDIT_LOGGING_PATH))
    logger_spec = importlib.util.spec_from_file_location(
        "vams_mock_logger_for_decision_test", os.path.join(_MOCKS_DIR, "customLogging", "logger.py"))
    mock_logger = importlib.util.module_from_spec(logger_spec)
    logger_spec.loader.exec_module(mock_logger)
    stand_ins = {
        "common": _stub_module("common", path=os.path.join(backend_dir, "common")),
        "handlers": _stub_module("handlers", path=os.path.join(backend_dir, "handlers")),
        "handlers.auth": _stub_module("handlers.auth", request_to_claims=lambda event: dict(CLAIMS),
                                      AUTH_METHOD_API_KEY="apiKey", AUTH_METHOD_JWT="jwt"),
        "customLogging": _stub_module("customLogging", path=os.path.join(backend_dir, "customLogging")),
        "customLogging.logger": mock_logger,
    }
    with patch.dict(sys.modules):
        for name in [name for name in sys.modules if name.startswith("common.")]:
            del sys.modules[name]
        sys.modules.update(stand_ins)
        spec = importlib.util.spec_from_file_location("vams_real_audit_logging_for_decision_test",
                                                      _AUDIT_LOGGING_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class _AlreadyExists(Exception):
    pass


class RecordingClient:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.exceptions = SimpleNamespace(ResourceAlreadyExistsException=_AlreadyExists)

    def create_log_stream(self, **kwargs):
        return {}

    def put_log_events(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
        return {}

    def messages(self):
        return [event["message"] for call in self.calls for event in call["logEvents"]]


@pytest.fixture
def clients(real_audit, monkeypatch):
    module_client, flush_client = RecordingClient(), RecordingClient()
    monkeypatch.setattr(real_audit, "cloudwatch_logs", module_client)
    monkeypatch.setattr(real_audit, "audit_flush_client", flush_client)
    monkeypatch.setattr(real_audit, "_created_log_streams", {})
    monkeypatch.setattr(real_audit, "request_to_claims", lambda event: dict(CLAIMS))
    monkeypatch.setattr(authz, "request_to_claims", lambda event: dict(CLAIMS))
    monkeypatch.setattr(authz, "log_authorization_api", real_audit.log_authorization_api)
    monkeypatch.setattr(authz, "log_authorization", real_audit.log_authorization)
    return SimpleNamespace(module=module_client, flush=flush_client)


def _policy_text():
    """Policy text from the REAL generator: roleA may call /database routes, except asset deletes."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    policies = [
        {"constraintId": "api-allow", "objectType": "api",
         "criteriaAnd": [{"field": "route__path", "operator": "starts_with", "value": "/database"}],
         "criteriaOr": [], "userPermissions": [],
         "groupPermissions": [{"groupId": "roleA", "permission": action, "permissionType": "allow"}
                              for action in ("GET", "PUT", "POST", "DELETE")]},
        {"constraintId": "api-deny", "objectType": "api",
         "criteriaAnd": [{"field": "route__path", "operator": "contains", "value": "/assets/.*/delete"}],
         "criteriaOr": [], "userPermissions": [],
         "groupPermissions": [{"groupId": "roleA", "permission": "DELETE", "permissionType": "deny"}]},
    ]
    user_roles = [{"userId": USER_ID, "roleName": "roleA"}]
    with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table", return_value=user_roles), \
         patch.object(CasbinEnforcerService, "_read_policies_batch_optimized", return_value=policies):
        return svc._create_policy_text_helper()


@pytest.fixture(scope="module")
def enforcer():
    """A CasbinEnforcer whose service is built by the production ``_create_casbin_enforcer`` path."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    svc._model_text = authz.PERMISSION_CONSTRAINT_POLICY
    svc._dateTime_Cached = datetime.now()
    svc._enforcer = None
    svc._create_casbin_enforcer(_policy_text())
    casbin_enforcer = CasbinEnforcer.__new__(CasbinEnforcer)
    casbin_enforcer.claims_and_roles = dict(CLAIMS)
    casbin_enforcer.service_object = svc
    return casbin_enforcer


def api_event(method, path):
    return {"headers": {}, "requestContext": {"requestId": "req-1", "http": {"method": method, "path": path},
                                              "authorizer": {"lambda": {}}}}


LAMBDA_CONTEXT = SimpleNamespace(aws_request_id="lambda-req", get_remaining_time_in_millis=lambda: 30000)


def _decisions(enforcer, real_audit, inside_context):
    decisions = []
    for method, path in ROUTES:
        event = api_event(method, path)
        if inside_context:
            with real_audit.audit_request(event, LAMBDA_CONTEXT):
                decisions.append(enforcer.enforceAPI(event))
        else:
            decisions.append(enforcer.enforceAPI(event))
    return decisions


@pytest.mark.unit
class TestEnforceApiDecisionIndependentOfAuditDelivery:
    def test_the_policy_gives_both_decisions(self, enforcer, real_audit, clients):
        assert _decisions(enforcer, real_audit, inside_context=False) == [True, True, False, False]

    def test_the_decision_is_the_same_with_and_without_an_audit_context(self, enforcer, real_audit, clients):
        outside = _decisions(enforcer, real_audit, inside_context=False)
        inside = _decisions(enforcer, real_audit, inside_context=True)
        assert inside == outside
        # Outside: four immediate writes. Inside: the two allowed records went through the flush and the
        # two denials were written at once, so both delivery paths really ran.
        assert len(clients.module.calls) == 4 + 2
        assert len(clients.flush.calls) == 2

    def test_the_decision_is_unchanged_when_buffered_delivery_fails(
            self, enforcer, real_audit, clients, monkeypatch):
        expected = _decisions(enforcer, real_audit, inside_context=False)

        def failing_enqueue(*args, **kwargs):
            raise RuntimeError("delivery failed")

        monkeypatch.setattr(real_audit, "_enqueue_records", failing_enqueue)
        assert _decisions(enforcer, real_audit, inside_context=True) == expected

    def test_the_decision_is_unchanged_when_the_audit_call_raises(
            self, enforcer, real_audit, clients, monkeypatch):
        expected = _decisions(enforcer, real_audit, inside_context=False)

        def failing_log(*args, **kwargs):
            raise RuntimeError("audit failed")

        monkeypatch.setattr(authz, "log_authorization_api", failing_log)
        assert _decisions(enforcer, real_audit, inside_context=True) == expected
        assert _decisions(enforcer, real_audit, inside_context=False) == expected


def _data_decisions(enforcer, real_audit, inside_context):
    decisions = []
    for method, path in ROUTES:
        obj = {"object__type": "api", "route__path": path}
        if inside_context:
            event = api_event(method, path)
            with real_audit.audit_request(event, LAMBDA_CONTEXT):
                decisions.append(enforcer.enforce(obj, method))
        else:
            decisions.append(enforcer.enforce(obj, method))
    return decisions


@pytest.mark.unit
class TestDecisionIndependentOfTheRecordContractKeys:
    """The authorization records gain the request's contract keys; the decisions do not move."""

    def test_inside_a_context_the_api_records_carry_the_keys(self, enforcer, real_audit, clients):
        _decisions(enforcer, real_audit, inside_context=True)
        messages = clients.module.messages() + clients.flush.messages()
        assert len(messages) == 4
        assert all('"outcome": ' in message and '"request": ' in message for message in messages)

    def test_the_decision_is_unchanged_when_building_the_keys_raises(
            self, enforcer, real_audit, clients, monkeypatch):
        expected = _decisions(enforcer, real_audit, inside_context=False)
        expected_data = _data_decisions(enforcer, real_audit, inside_context=False)

        def failing_keys(*args, **kwargs):
            raise RuntimeError("keys failed")

        monkeypatch.setattr(real_audit, "_authorization_keys", failing_keys)
        assert _decisions(enforcer, real_audit, inside_context=True) == expected
        assert _data_decisions(enforcer, real_audit, inside_context=True) == expected_data
        denials = [m for m in clients.module.messages() + clients.flush.messages()
                   if m.startswith("[AUTHORIZATION][authorized: False]")]
        assert len(denials) == 8, "every denial is still recorded, inside a context without the keys"

    def test_data_level_decisions_are_the_same_with_and_without_a_context(self, enforcer, real_audit, clients):
        outside = _data_decisions(enforcer, real_audit, inside_context=False)
        inside = _data_decisions(enforcer, real_audit, inside_context=True)
        assert outside == inside == [True, True, False, False]
        denials = [m for m in clients.module.messages() if m.startswith("[AUTHORIZATION][authorized: False]")]
        assert len(denials) == 4, "two denials outside and two inside, each written at once"
        assert sum('"outcome": "denied"' in m for m in denials) == 2, "the two inside carry the keys"
