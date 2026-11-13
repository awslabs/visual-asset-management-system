# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Authorization records and auth change records in the record contract.

Inside the request audit context:

* every auth change (AUTHCHANGES) and auth other (AUTHOTHER) record keeps its prefix and its legacy
  payload, and gains the contract keys: ``action``, ``outcome``, ``entity``, ``actor``, ``request``,
  ``legacyType`` (the ``[type: ...]`` value) and what the write changed (``changes``, ``previous``),
  with the redaction rules applied, so no secret is recorded;
* a failed request on those routes records ``outcome: failed``;
* the reads of those routes record the request and ``itemCount``;
* API-level and data-level authorization records gain ``action``, ``entity``, ``outcome`` and
  ``request``, and every authorization, error, auth change and auth other record echoes a short
  request (no headers, query string or body);
* the authorizer's allowed record names the verified user;
* ``request.sourceIp`` is the CloudFront viewer only when the request carries the origin verification
  value the authorizer accepted.

Assertions read back what the writer handed the CloudWatch Logs client.
"""

import importlib
import json
from types import SimpleNamespace

import pytest

from test_audit_action_records import (
    LAMBDA_CONTEXT,
    REGISTRY,
    RecordingClient,
    _Claims,
    api_event,
    auditLogging,
    group_name,
    mask_sensitive_data,
    parse,
)

def _viewer():
    return importlib.import_module("common.auth.viewerAddress")

BODY_SECRET = "body-secret-value-123"


@pytest.fixture
def clients(monkeypatch):
    """The module client (outside a context) and the flush client (inside one), both recording."""
    monkeypatch.setattr(auditLogging, "request_to_claims", _Claims())
    monkeypatch.setattr(auditLogging, "mask_sensitive_data", mask_sensitive_data)
    monkeypatch.setattr(auditLogging, "_created_log_streams", {})
    module_client, flush_client = RecordingClient(), RecordingClient()
    monkeypatch.setattr(auditLogging, "cloudwatch_logs", module_client)
    monkeypatch.setattr(auditLogging, "audit_flush_client", flush_client)
    return SimpleNamespace(module=module_client, flush=flush_client)


def _event(path, method, body=None, path_parameters=None):
    event = api_event(path=path, method=method, body=body or {"password": BODY_SECRET, "note": "body-marker"})
    event["pathParameters"] = path_parameters or {}
    event["requestContext"]["requestTimeEpoch"] = 1763050000000
    return event


def _only(clients, log_type):
    messages = clients.flush.messages(group_name(log_type)) + clients.module.messages(group_name(log_type))
    assert len(messages) == 1, messages
    return parse(messages[0])


# Every auth change and auth other record type a handler writes: the legacy call, the route it is
# written on, and the action, entity and value sections the record must carry.
AUTH_RECORD_CASES = [
    # (function, secondary type, path, method, path parameters, legacy payload, kwargs, action, entity)
    ("log_auth_changes", "roleCreate", "/roles", "POST", {},
     {"roleName": "r1", "operation": "create", "description": "d", "mfaRequired": False},
     {"changes": {"roleName": "r1", "description": "d", "mfaRequired": False}},
     "roleCreate", {"type": "role", "id": "r1"}),
    ("log_auth_changes", "roleUpdate", "/roles", "PUT", {},
     {"roleName": "r1", "operation": "update", "mfaRequired": True},
     {"changes": {"roleName": "r1", "mfaRequired": True}},
     "roleUpdate", {"type": "role", "id": "r1"}),
    ("log_auth_changes", "roleDelete", "/roles/r1", "DELETE", {"roleId": "r1"},
     {"roleName": "r1", "operation": "delete"}, {},
     "roleDelete", {"type": "role", "id": "r1"}),
    ("log_auth_changes", "userRoleCreate", "/user-roles", "POST", {},
     {"userId": "bob", "operation": "create", "roleNames": ["r1"]},
     {"entity_id": "bob", "ids": {"targetUserId": "bob"}, "changes": {"roleNames": ["r1"]}},
     "userRoleCreate", {"type": "userRole", "id": "bob"}),
    ("log_auth_changes", "userRoleUpdate", "/user-roles", "PUT", {},
     {"userId": "bob", "operation": "update", "roleNames": ["r2"], "changed": True},
     {"entity_id": "bob", "ids": {"targetUserId": "bob"}, "changes": {"roleNames": ["r2"]}},
     "userRoleUpdate", {"type": "userRole", "id": "bob"}),
    ("log_auth_changes", "userRoleDelete", "/user-roles", "DELETE", {},
     {"userId": "bob", "operation": "delete", "changed": False},
     {"entity_id": "bob", "ids": {"targetUserId": "bob"}},
     "userRoleDelete", {"type": "userRole", "id": "bob"}),
    ("log_auth_changes", "constraintCreateUpdate", "/auth/constraints/c1", "POST", {"constraintId": "c1"},
     {"constraintId": "c1", "operation": "create", "objectType": "asset"},
     {"entity_id": "c1", "changes": {"constraintId": "c1", "objectType": "asset", "name": "n"}},
     "constraintCreate", {"type": "constraint", "id": "c1"}),
    ("log_auth_changes", "constraintCreateUpdate", "/auth/constraints/c1", "PUT", {"constraintId": "c1"},
     {"constraintId": "c1", "operation": "update", "objectType": "asset"},
     {"entity_id": "c1", "changes": {"constraintId": "c1", "objectType": "asset", "name": "n2"}},
     "constraintUpdate", {"type": "constraint", "id": "c1"}),
    ("log_auth_changes", "constraintDelete", "/auth/constraints/c1", "DELETE", {"constraintId": "c1"},
     {"constraintId": "c1", "operation": "delete"}, {"entity_id": "c1"},
     "constraintDelete", {"type": "constraint", "id": "c1"}),
    ("log_auth_changes", "constraintsTemplateImport", "/auth/constraintsTemplateImport", "POST", {},
     {"templateName": "t", "roleName": "r1", "constraintsCreated": 2, "constraintIds": ["c1", "c2"]},
     {"item_count": 2, "changes": {"templateName": "t", "variableValues": {"ROLE_NAME": "r1"},
                                   "constraintIds": ["c1", "c2"]}},
     "constraintTemplateImport", {"type": "constraint"}),
    ("log_auth_changes", "apiKeyCreate", "/auth/api-keys", "POST", {},
     {"apiKeyId": "k1", "apiKeyName": "n", "userId": "bob", "expiresAt": "", "operation": "create"},
     {"entity_id": "k1", "ids": {"targetUserId": "bob"},
      "changes": {"apiKeyName": "n", "userId": "bob", "description": "d", "expiresAt": None}},
     "apiKeyCreate", {"type": "apiKey", "id": "k1"}),
    ("log_auth_changes", "apiKeyCreate", "/auth/user/api-keys", "POST", {},
     {"apiKeyId": "k2", "apiKeyName": "n", "userId": "alice", "expiresAt": "2027-01-01T00:00:00Z",
      "operation": "create", "scope": "user"},
     {"entity_id": "k2", "ids": {"targetUserId": "alice"},
      "changes": {"apiKeyName": "n", "userId": "alice", "description": "d", "expiresAt": "2027-01-01T00:00:00Z"}},
     "apiKeyCreate", {"type": "apiKey", "id": "k2"}),
    ("log_auth_changes", "apiKeyUpdate", "/auth/api-keys/k1", "PUT", {"apiKeyId": "k1"},
     {"apiKeyId": "k1", "updatedFields": ["updatedAt", "isActive"], "operation": "update"},
     {"entity_id": "k1", "ids": {"targetUserId": "bob"}, "changes": {"isActive": False},
      "previous": {"isActive": True}},
     "apiKeyUpdate", {"type": "apiKey", "id": "k1"}),
    ("log_auth_changes", "apiKeyDelete", "/auth/user/api-keys/k2", "DELETE", {"apiKeyId": "k2"},
     {"apiKeyId": "k2", "apiKeyName": "n", "userId": "alice", "operation": "delete"},
     {"entity_id": "k2", "ids": {"targetUserId": "alice"}, "previous": {"apiKeyName": "n", "isActive": True}},
     "apiKeyDelete", {"type": "apiKey", "id": "k2"}),
    ("log_auth_changes", "cognitoUserCreate", "/user/cognito", "POST", {},
     {"userId": "carol", "operation": "create", "email": "carol@example.com", "phone": "+15555550100"},
     {"entity_id": "carol", "ids": {"targetUserId": "carol"},
      "changes": {"userId": "carol", "email": "carol@example.com", "phone": "+15555550100"}},
     "cognitoUserCreate", {"type": "user", "id": "carol"}),
    ("log_auth_changes", "cognitoUserUpdate", "/user/cognito/carol", "PUT", {"userId": "carol"},
     {"userId": "carol", "operation": "update", "email": "c2@example.com", "phone": None, "clearPhone": True},
     {"entity_id": "carol", "ids": {"targetUserId": "carol"}, "changes": {"email": "c2@example.com", "phone": ""}},
     "cognitoUserUpdate", {"type": "user", "id": "carol"}),
    ("log_auth_changes", "cognitoUserDelete", "/user/cognito/carol", "DELETE", {"userId": "carol"},
     {"userId": "carol", "operation": "delete"}, {"entity_id": "carol", "ids": {"targetUserId": "carol"}},
     "cognitoUserDelete", {"type": "user", "id": "carol"}),
    ("log_auth_changes", "cognitoUserPasswordReset", "/user/cognito/carol/resetPassword", "POST",
     {"userId": "carol"}, {"userId": "carol", "operation": "resetPassword"},
     {"entity_id": "carol", "ids": {"targetUserId": "carol"}},
     "cognitoUserPasswordReset", {"type": "user", "id": "carol"}),
    ("log_auth_other", "userProfileUpdate", "/auth/loginProfile/alice", "POST", {"userId": "alice"},
     {"userId": "alice", "operation": "create_update", "selfAccess": True},
     {"entity_id": "alice", "ids": {"targetUserId": "alice"}, "changes": {"email": "alice@example.com"}},
     "loginProfileUpdate", {"type": "user", "id": "alice"}),
    ("log_auth_other", "userProfileGet", "/auth/loginProfile/alice", "GET", {"userId": "alice"},
     {"userId": "alice", "operation": "get", "selfAccess": True},
     {"entity_id": "alice", "ids": {"targetUserId": "alice"}},
     "loginProfileGet", {"type": "user", "id": "alice"}),
]

_CASE_IDS = [f"{case[1]}-{case[3]}-{case[2]}" for case in AUTH_RECORD_CASES]


def _record(clients, case):
    function, secondary, path, method, path_parameters, legacy, kwargs, _, _ = case
    log_type = "auditAuthChanges" if function == "log_auth_changes" else "auditAuthOther"
    event = _event(path, method, path_parameters=path_parameters)
    with auditLogging.audit_request(event, LAMBDA_CONTEXT):
        getattr(auditLogging, function)(event, secondary, dict(legacy), **kwargs)
    return log_type, _only(clients, log_type)


@pytest.mark.unit
class TestAuthChangeRecords:
    @pytest.mark.parametrize("case", AUTH_RECORD_CASES, ids=_CASE_IDS)
    def test_the_record_keeps_its_prefix_and_legacy_payload_and_gains_the_contract_keys(self, clients, case):
        function, secondary, path, method, _, legacy, kwargs, action, entity = case
        label = "AUTHCHANGES" if function == "log_auth_changes" else "AUTHOTHER"
        _, (match, payload, echo) = _record(clients, case)
        assert match.group("prefix") == f"[{label}][type: {secondary}]", "prefix byte-identical"
        for key, value in legacy.items():
            assert payload.get(key, payload.get("legacy", {}).get(key)) == value, key
        assert payload["legacyType"] == secondary
        assert (payload["action"], payload["outcome"]) == (action, "success")
        assert payload["entity"] == entity
        assert payload["auditVersion"] == 1 and payload["recordId"]
        assert payload["actor"] == {"userId": "alice", "authMethod": "cognito"}
        assert payload["request"]["requestId"] == "req-1" and payload["request"]["method"] == method
        for section in ("changes", "previous"):
            expected = {k: v for k, v in (kwargs.get(section) or {}).items() if v is not None}
            assert payload.get(section, {}) == expected, section
        if "ids" in kwargs and "targetUserId" in kwargs["ids"]:
            assert payload["targetUserId"] == kwargs["ids"]["targetUserId"]
        if "item_count" in kwargs:
            assert payload["itemCount"] == kwargs["item_count"]

    @pytest.mark.parametrize("case", AUTH_RECORD_CASES, ids=_CASE_IDS)
    def test_the_echo_carries_no_header_query_or_body(self, clients, case):
        _, (_, _, echo) = _record(clients, case)
        for marker in ("body-marker", BODY_SECRET, "header-marker", "secret-token-value", "query-marker"):
            assert marker not in echo
        assert '"requestId": "req-1"' in echo

    def test_the_cognito_email_and_phone_are_recorded_in_full(self, clients):
        case = next(c for c in AUTH_RECORD_CASES if c[1] == "cognitoUserCreate")
        _, (_, payload, _) = _record(clients, case)
        assert payload["changes"] == {"userId": "carol", "email": "carol@example.com", "phone": "+15555550100"}

    @pytest.mark.parametrize("field", ["password", "Password", "apiKey", "secret", "rawKey", "clientSecret"])
    def test_a_secret_passed_in_changes_or_the_legacy_payload_is_redacted(self, clients, field):
        event = _event("/user/cognito", "POST")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_auth_changes(event, "cognitoUserCreate", {"userId": "carol", field: "s3cr3t-v"},
                                          entity_id="carol", changes={"userId": "carol", field: "s3cr3t-v"})
        message = clients.flush.messages(group_name("auditAuthChanges"))[0]
        assert "s3cr3t-v" not in message
        _, payload, _ = parse(message)
        assert field in payload["changes"], "the key stays, the value is redacted"

    def test_a_success_waits_for_the_flush_and_a_failure_is_written_at_once(self, clients):
        event = _event("/roles", "POST")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_auth_changes(event, "roleCreate", {"roleName": "r1"})
            assert clients.flush.calls == []
            auditLogging.log_auth_changes(event, "roleCreate", {"roleName": "r1"}, outcome="failed",
                                          reason="conflict")
            assert len(clients.flush.calls) == 1
        payloads = [parse(m)[1] for m in clients.flush.messages(group_name("auditAuthChanges"))]
        assert [(p["outcome"], p.get("reason")) for p in payloads] == [("failed", "conflict"), ("success", None)]

    def test_outside_a_context_the_legacy_record_is_unchanged_but_echoes_the_short_request(self, clients):
        event = _event("/roles", "POST")
        auditLogging.log_auth_changes(event, "roleCreate", {"roleName": "r1"})
        expected = (auditLogging._format_log_message(
            "[AUTHCHANGES][type: roleCreate]", {"user": "alice", "roles": ["admin"], "mfaEnabled": True},
            {"roleName": "r1"}) + auditLogging._event_suffix(auditLogging._short_echo_event(event)))
        assert clients.module.messages(group_name("auditAuthChanges")) == [expected]

    def test_a_large_legacy_payload_keeps_the_record_under_the_cap(self, clients):
        event = _event("/auth/constraintsTemplateImport", "POST")
        ids = ["constraint-%05d" % n for n in range(5000)]
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_auth_changes(event, "constraintsTemplateImport", {"constraintIds": ids},
                                          item_count=len(ids), changes={"constraintIds": ids})
        message = clients.flush.messages(group_name("auditAuthChanges"))[0]
        assert len(message.encode("utf-8")) <= auditLogging.AUDIT_RECORD_MAX_BYTES
        assert message.startswith("[AUTHCHANGES][type: constraintsTemplateImport] ")


@pytest.mark.unit
class TestAuthRouteFailures:
    @pytest.mark.parametrize("path, method, path_parameters, group, prefix_type, action", [
        ("/roles", "POST", {}, "auditAuthChanges", "roleCreate", "roleCreate"),
        ("/auth/constraints/c1", "PUT", {"constraintId": "c1"}, "auditAuthChanges", "constraintCreateUpdate",
         "constraintUpdate"),
        ("/auth/api-keys/k1", "DELETE", {"apiKeyId": "k1"}, "auditAuthChanges", "apiKeyDelete", "apiKeyDelete"),
        ("/user/cognito/carol", "PUT", {"userId": "carol"}, "auditAuthChanges", "cognitoUserUpdate",
         "cognitoUserUpdate"),
        ("/auth/constraints", "GET", {}, "auditAuthOther", "constraintList", "constraintList"),
    ])
    @pytest.mark.parametrize("status, reason", [(400, "validation"), (404, "notFound"), (500, "internal")])
    def test_a_failed_request_records_its_action_as_failed(self, clients, path, method, path_parameters, group,
                                                           prefix_type, action, status, reason):
        @auditLogging.audited_handler
        def handler(event, context):
            return {"statusCode": status}

        handler(_event(path, method, path_parameters=path_parameters), LAMBDA_CONTEXT)
        match, payload, echo = _only(clients, group)
        assert match.group("type") == prefix_type
        assert (payload["action"], payload["outcome"], payload["reason"]) == (action, "failed", reason)
        assert payload["legacyType"] == prefix_type
        assert BODY_SECRET not in echo and "body-marker" not in echo
        for key, value in path_parameters.items():
            if key in auditLogging.AUDIT_ID_FIELDS:
                assert payload[key] == value

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_denial_adds_no_failed_record(self, clients, status):
        @auditLogging.audited_handler
        def handler(event, context):
            return {"statusCode": status}

        handler(_event("/roles", "POST"), LAMBDA_CONTEXT)
        assert clients.flush.calls == [] and clients.module.calls == []

    def test_a_recorded_success_is_not_followed_by_a_failed_record(self, clients):
        @auditLogging.audited_handler
        def handler(event, context):
            auditLogging.log_auth_changes(event, "roleCreate", {"roleName": "r1"})
            return {"statusCode": 500}

        handler(_event("/roles", "POST"), LAMBDA_CONTEXT)
        payloads = [parse(m)[1] for m in clients.flush.messages(group_name("auditAuthChanges"))]
        assert [p["outcome"] for p in payloads] == ["success"]


def _echo_json(echo):
    return json.loads(echo[len(" --- [event: "):-1])


def _route_event(path, method, path_parameters, resource):
    event = _event(path, method, path_parameters=path_parameters)
    event["resource"] = resource
    event["requestContext"]["resourcePath"] = resource
    event["requestContext"]["identity"] = {"sourceIp": "203.0.113.5"}
    return event


def _expected_short_echo(event):
    expected = {"requestContext": {"requestId": "req-1", "requestTimeEpoch": 1763050000000,
                                   "resourcePath": event["resource"],
                                   "http": {"method": event["requestContext"]["http"]["method"],
                                            "path": event["requestContext"]["http"]["path"],
                                            "sourceIp": "198.51.100.7"},
                                   "identity": {"sourceIp": "203.0.113.5"}},
                "resource": event["resource"]}
    if event["pathParameters"]:
        expected["pathParameters"] = event["pathParameters"]
    return expected


@pytest.mark.unit
class TestAuthRecordEcho:
    """Auth change and auth other records inside a context echo the short request: request id and
    time, method and path, route, path parameters and connection addresses."""

    @pytest.mark.parametrize("case", AUTH_RECORD_CASES, ids=_CASE_IDS)
    def test_a_legacy_call_echoes_the_short_request(self, clients, case):
        function, secondary, path, method, path_parameters, legacy, kwargs, _, _ = case
        log_type = "auditAuthChanges" if function == "log_auth_changes" else "auditAuthOther"
        event = _route_event(path, method, path_parameters, "/route/{param}")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            getattr(auditLogging, function)(event, secondary, dict(legacy), **kwargs)
        _, _, echo = _only(clients, log_type)
        assert _echo_json(echo) == _expected_short_echo(event)

    @pytest.mark.parametrize("path, method, path_parameters, resource, group", [
        ("/roles/r1", "DELETE", {"roleId": "r1"}, "/roles/{roleId}", "auditAuthChanges"),
        ("/user/cognito/carol", "DELETE", {"userId": "carol"}, "/user/cognito/{userId}", "auditAuthChanges"),
        ("/auth/constraints/c1", "GET", {"constraintId": "c1"}, "/auth/constraints/{constraintId}",
         "auditAuthOther"),
    ])
    def test_a_failed_request_record_echoes_the_short_request(self, clients, path, method, path_parameters,
                                                             resource, group):
        @auditLogging.audited_handler
        def handler(event, context):
            return {"statusCode": 404}

        event = _route_event(path, method, path_parameters, resource)
        handler(event, LAMBDA_CONTEXT)
        _, payload, echo = _only(clients, group)
        assert payload["outcome"] == "failed"
        assert _echo_json(echo) == _expected_short_echo(event)

    def test_a_read_record_echoes_the_short_request(self, clients):
        event = _route_event("/auth/api-keys/k1", "GET", {"apiKeyId": "k1"}, "/auth/api-keys/{apiKeyId}")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, "apiKeyGet", item_count=1, apiKeyId="k1")
        _, _, echo = _only(clients, "auditAuthOther")
        assert _echo_json(echo) == _expected_short_echo(event)

    def test_an_action_record_keeps_the_compact_echo(self, clients):
        event = _route_event("/database/db1/assets/a1", "PUT", {"databaseId": "db1", "assetId": "a1"},
                             "/database/{databaseId}/assets/{assetId}")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, "assetUpdate", database_id="db1", asset_id="a1")
        _, _, echo = _only(clients, "auditActions")
        assert _echo_json(echo) == {"requestContext": {"requestId": "req-1", "http": {
            "method": "PUT", "path": "/database/db1/assets/a1"}}}


AUTH_READS = [
    ("apiKeyList", "/auth/api-keys", "GET", {}),
    ("apiKeyGet", "/auth/api-keys/k1", "GET", {"apiKeyId": "k1"}),
    ("cognitoUserList", "/user/cognito", "GET", {}),
    ("constraintGet", "/auth/constraints/c1", "GET", {"constraintId": "c1"}),
    ("constraintList", "/auth/constraints", "GET", {}),
    ("roleList", "/roles", "GET", {}),
    ("userRoleList", "/user-roles", "GET", {}),
]


@pytest.mark.unit
class TestAuthReads:
    def test_the_registry_has_no_pending_actions(self):
        assert not hasattr(REGISTRY, "AUDIT_PENDING_ACTIONS")

    @pytest.mark.parametrize("action, path, method, path_parameters", AUTH_READS, ids=[r[0] for r in AUTH_READS])
    def test_a_read_records_the_request_and_item_count(self, clients, action, path, method, path_parameters):
        event = _event(path, method, path_parameters=path_parameters)
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, action, item_count=3, request_parameters={"pageSize": 10},
                                    **{k: v for k, v in path_parameters.items()})
        match, payload, _ = _only(clients, "auditAuthOther")
        assert match.group("prefix") == f"[AUTHOTHER][type: {action}]"
        assert (payload["action"], payload["outcome"], payload["itemCount"]) == (action, "success", 3)
        assert payload["requestParameters"] == {"pageSize": 10}
        assert payload["legacyType"] == action
        assert "changes" not in payload

    def test_the_read_switch_turns_auth_reads_off(self, clients, monkeypatch):
        monkeypatch.setenv(auditLogging.AUDIT_RECORD_READS_ENV, "false")
        event = _event("/roles", "GET")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, "roleList", item_count=1)
        assert clients.flush.calls == [] and clients.module.calls == []


def _api_check(event):
    return {"action": event["requestContext"]["http"]["method"],
            "obj": {"object__type": "api", "route__path": event["requestContext"]["http"]["path"]}}


@pytest.mark.unit
class TestAuthorizationRecords:
    @pytest.mark.parametrize("authorized, outcome", [(True, "success"), (False, "denied")])
    def test_api_records_gain_action_entity_outcome_and_request(self, clients, authorized, outcome):
        event = _event("/database/db1/assets/a1", "PUT", path_parameters={"databaseId": "db1", "assetId": "a1"})
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(event, authorized, _api_check(event))
        match, payload, echo = _only(clients, "auditAuthorization")
        assert match.group("prefix") == f"[AUTHORIZATION][authorized: {authorized}]"
        assert (payload["action"], payload["outcome"]) == ("assetUpdate", outcome)
        assert payload["entity"] == {"type": "asset", "id": "a1"}
        assert payload["legacy"] == {"action": "PUT"}
        assert payload["obj"] == _api_check(event)["obj"]
        assert payload["request"]["route"] == "/database/{databaseId}/assets/{assetId}"
        assert payload["actor"]["userId"] == "alice"
        echo_json = json.loads(echo[len(" --- [event: "):-1])
        assert echo_json == {"requestContext": {
            "requestId": "req-1", "requestTimeEpoch": 1763050000000,
            "http": {"method": "PUT", "path": "/database/db1/assets/a1", "sourceIp": "198.51.100.7"}},
            "pathParameters": {"databaseId": "db1", "assetId": "a1"}}

    def test_a_data_level_denial_gains_the_keys_of_its_request(self, clients):
        event = _event("/database/db1/assets/a1", "PUT", path_parameters={"databaseId": "db1", "assetId": "a1"})
        claims_and_roles = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": True}
        check = {"action": "PUT", "obj": {"object__type": "asset", "databaseId": "db1", "assetName": "a1"}}
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization(claims_and_roles, False, check)
            assert len(clients.module.calls) + len(clients.flush.calls) == 1, "a denial is written at once"
        match, payload, echo = _only(clients, "auditAuthorization")
        assert match.group("prefix") == "[AUTHORIZATION][authorized: False]"
        assert (payload["action"], payload["outcome"]) == ("assetUpdate", "denied")
        assert payload["entity"]["type"] == "asset"
        assert payload["legacy"] == {"action": "PUT"} and payload["obj"] == check["obj"]
        assert "body-marker" not in echo and '"requestId": "req-1"' in echo

    def test_outside_a_context_a_data_level_record_is_unchanged(self, clients):
        claims_and_roles = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": True}
        auditLogging.log_authorization(claims_and_roles, False, {"action": "GET", "obj": {}})
        _, payload, echo = _only(clients, "auditAuthorization")
        assert payload == {"action": "GET", "obj": {}}
        assert echo == ' --- [event: {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": "alice"}}}}}]'

    @pytest.mark.parametrize("authorized", [True, False])
    def test_an_api_record_is_written_without_the_keys_when_building_them_fails(self, clients, monkeypatch,
                                                                                authorized):
        def failing_keys(*args, **kwargs):
            raise RuntimeError("keys failed")

        monkeypatch.setattr(auditLogging, "_authorization_keys", failing_keys)
        event = _event("/database/db1/assets/a1", "PUT", path_parameters={"databaseId": "db1", "assetId": "a1"})
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(event, authorized, _api_check(event))
        match, payload, echo = _only(clients, "auditAuthorization")
        assert match.group("prefix") == f"[AUTHORIZATION][authorized: {authorized}]"
        assert payload == _api_check(event), "the record keeps its form without the contract keys"
        assert '"requestId": "req-1"' in echo and "body-marker" not in echo

    def test_a_data_level_denial_is_written_without_the_keys_when_building_them_fails(self, clients,
                                                                                      monkeypatch):
        def failing_keys(*args, **kwargs):
            raise RuntimeError("keys failed")

        monkeypatch.setattr(auditLogging, "_authorization_keys", failing_keys)
        event = _event("/database/db1/assets/a1", "PUT", path_parameters={"databaseId": "db1", "assetId": "a1"})
        claims_and_roles = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": True}
        check = {"action": "PUT", "obj": {"object__type": "asset", "databaseId": "db1", "assetName": "a1"}}
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization(claims_and_roles, False, check)
        match, payload, _ = _only(clients, "auditAuthorization")
        assert match.group("prefix") == "[AUTHORIZATION][authorized: False]"
        assert payload == check

    def test_a_data_level_denial_falls_back_to_the_user_echo_when_the_short_echo_fails(self, clients,
                                                                                       monkeypatch):
        def failing_echo(*args, **kwargs):
            raise RuntimeError("echo failed")

        monkeypatch.setattr(auditLogging, "_short_echo_event", failing_echo)
        event = _event("/database/db1/assets/a1", "PUT", path_parameters={"databaseId": "db1", "assetId": "a1"})
        claims_and_roles = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": True}
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization(claims_and_roles, False, {"action": "PUT", "obj": {}})
        _, payload, echo = _only(clients, "auditAuthorization")
        assert payload["outcome"] == "denied"
        assert echo == ' --- [event: {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": "alice"}}}}}]'

    def test_a_large_body_does_not_reach_the_authorization_or_error_record(self, clients):
        big = {"password": BODY_SECRET, "blob": "x" * 95000}
        event = _event("/database/db1/assets/a1/metadata", "PUT", body=big,
                       path_parameters={"databaseId": "db1", "assetId": "a1"})
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(event, True, _api_check(event))
            auditLogging.log_errors(event, "validation", {"statusCode": 400})
        for log_type in ("auditAuthorization", "auditErrors"):
            message = (clients.flush.messages(group_name(log_type)) + clients.module.messages(group_name(log_type)))[0]
            assert BODY_SECRET not in message and "xxxxxxxx" not in message
            assert len(message.encode("utf-8")) < 4096, log_type

    def test_a_long_path_parameter_is_capped_in_the_echo(self, clients):
        long_path = "folder/" + "a" * 5000
        event = _event("/database/db1/assets/a1/files/x", "GET",
                       path_parameters={"databaseId": "db1", "assetId": "a1", "proxy": long_path})
        auditLogging.log_authorization_api(event, False, _api_check(event))
        _, _, echo = _only(clients, "auditAuthorization")
        echo_json = json.loads(echo[len(" --- [event: "):-1])
        assert echo_json["pathParameters"]["proxy"]["truncated"] is True
        assert echo_json["pathParameters"]["proxy"]["sha256"] == auditLogging.audit_value_sha256(long_path)


def _authorizer_event():
    return {
        "type": "REQUEST", "methodArn": "arn:aws:execute-api:us-east-1:123456789012:abc/api/GET/database",
        "path": "/database", "httpMethod": "GET", "resource": "/database",
        "headers": {"Authorization": "Bearer secret-token-value", "User-Agent": "header-marker"},
        "pathParameters": {},
        "requestContext": {"requestId": "gw-1", "requestTimeEpoch": 1763050000000,
                           "identity": {"sourceIp": "203.0.113.9"},
                           "http": {"method": "GET", "path": "/database", "sourceIp": "198.51.100.20"}},
    }


@pytest.mark.unit
class TestAuthorizerRecord:
    VERIFIED = {"sub": "0000-sub", "cognito:username": "Alice", "vams:roles": json.dumps(["admin", "viewer"]),
                "vams:mfaEnabled": "true", "iss": "https://cognito-idp.us-east-1.amazonaws.com/pool"}

    def test_an_allowed_record_names_the_verified_user(self, clients):
        auditLogging.log_authorization_gateway(_authorizer_event(), True, None, verified_context=self.VERIFIED)
        match, payload, echo = _only(clients, "auditAuthorization")
        assert match.group("user") == "Alice", "the user id the handlers resolve (cognito:username before sub)"
        assert json.loads(match.group("roles")) == ["admin", "viewer"] and match.group("mfa") == "True"
        assert payload["sourceIp"] == "198.51.100.20"
        assert "secret-token-value" not in echo and "header-marker" not in echo
        assert '"identity": {"sourceIp": "203.0.113.9"}' in echo, "the connection peer stays in the echo"

    def test_an_api_key_record_names_the_key_user(self, clients):
        verified = {"sub": "svc-user", "cognito:username": "svc-user", "vams:tokens": json.dumps(["svc-user"]),
                    "vams:roles": json.dumps(["r1"]), "vams:apiKeyId": "k1", "vams:authMethod": "apiKey"}
        auditLogging.log_authorization_gateway(_authorizer_event(), True, None, verified_context=verified)
        match, _, _ = _only(clients, "auditAuthorization")
        assert match.group("user") == "svc-user"

    def test_a_denial_names_no_user_even_with_a_context(self, clients):
        auditLogging.log_authorization_gateway(_authorizer_event(), False, "Token verification failed",
                                               verified_context=self.VERIFIED)
        match, payload, _ = _only(clients, "auditAuthorization")
        assert match.group("user") == "unknown" and match.group("roles") == "[]"
        assert payload["failureReason"] == "Token verification failed"

    def test_an_allow_without_a_context_names_no_user(self, clients):
        auditLogging.log_authorization_gateway(_authorizer_event(), True, None)
        match, _, _ = _only(clients, "auditAuthorization")
        assert match.group("user") == "unknown"


VERIFY_VALUE = "origin-verify-value-0123456789abcdef"  # pragma: allowlist secret


def _forwarded_event(header_value, digest_value, viewer="192.0.2.44:51234"):
    event = api_event(path="/database", method="GET")
    event["headers"]["x-vams-origin-verify"] = header_value
    event["headers"]["CloudFront-Viewer-Address"] = viewer
    event["requestContext"]["identity"] = {"sourceIp": "198.51.100.7"}
    if digest_value is not None:
        event["requestContext"]["authorizer"]["lambda"]["vams:originVerifyDigest"] = digest_value
    return event


@pytest.mark.unit
class TestViewerSourceIp:
    def test_a_request_with_the_accepted_value_records_the_viewer(self, clients):
        event = _forwarded_event(VERIFY_VALUE, _viewer().origin_verify_digest(VERIFY_VALUE))
        with auditLogging.audit_request(event, LAMBDA_CONTEXT) as ctx:
            assert ctx.request["sourceIp"] == "192.0.2.44"

    @pytest.mark.parametrize("header, digest", [
        ("forged-value", None),
        ("forged-value", "not-the-digest"),
        (VERIFY_VALUE, None),
        ("forged-value", "DIGEST_OF_REAL_VALUE"),
    ])
    def test_any_other_request_records_the_connection_address(self, clients, header, digest):
        if digest == "DIGEST_OF_REAL_VALUE":
            digest = _viewer().origin_verify_digest(VERIFY_VALUE)
        event = _forwarded_event(header, digest)
        with auditLogging.audit_request(event, LAMBDA_CONTEXT) as ctx:
            assert ctx.request["sourceIp"] == "198.51.100.7"

    def test_the_digest_in_a_client_header_is_not_trusted(self, clients):
        event = _forwarded_event(VERIFY_VALUE, None)
        event["headers"]["vams:originVerifyDigest"] = _viewer().origin_verify_digest(VERIFY_VALUE)
        with auditLogging.audit_request(event, LAMBDA_CONTEXT) as ctx:
            assert ctx.request["sourceIp"] == "198.51.100.7"

    def test_a_full_echo_record_never_shows_the_digest(self, clients):
        digest = _viewer().origin_verify_digest(VERIFY_VALUE)
        event = _forwarded_event(VERIFY_VALUE, digest)
        with auditLogging.audit_request(event, LAMBDA_CONTEXT) as ctx:
            auditLogging.log_file_upload(event, "db1", "a1", "dir/model.glb", False)
            assert ctx.request["sourceIp"] == "192.0.2.44", "the live event still carries the digest"
        message = (clients.flush.messages(group_name("auditFileUpload"))
                   + clients.module.messages(group_name("auditFileUpload")))[0]
        assert digest not in message
        assert '"vams:originVerifyDigest": "<redacted>"' in message
        assert event["requestContext"]["authorizer"]["lambda"]["vams:originVerifyDigest"] == digest

    def test_the_header_name_matches_the_origin_verify_module(self):
        origin_verify = importlib.import_module("common.auth.originVerify")
        assert _viewer().ORIGIN_VERIFY_HEADER_NAME == origin_verify.ORIGIN_VERIFY_HEADER_NAME

    def test_the_authorizer_adds_the_digest_only_for_an_accepted_value(self, monkeypatch):
        client_ip = importlib.import_module("common.auth.clientIp")
        monkeypatch.setattr(client_ip.originVerify, "is_valid_origin_verify_value",
                            lambda value: value == VERIFY_VALUE)
        good = {"headers": {"X-Vams-Origin-Verify": VERIFY_VALUE}}
        bad = {"headers": {"X-Vams-Origin-Verify": "forged"}}
        assert client_ip.origin_verify_audit_context(good, fronted="cloudfront") == {
            _viewer().ORIGIN_VERIFY_DIGEST_CONTEXT_KEY: _viewer().origin_verify_digest(VERIFY_VALUE)}
        assert client_ip.origin_verify_audit_context(bad, fronted="cloudfront") == {}
        assert client_ip.origin_verify_audit_context(good, fronted="none") == {}
        assert client_ip.origin_verify_audit_context(good, fronted="alb") == {}
