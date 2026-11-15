# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""API key create and update refuse a caller whose own credential is a temporary session key, and only a
Lambda cross-call may create a key under the reserved name prefix.

A temporary session key is a short-lived API key a backend component mints for one session through a
Lambda cross-call, named with ``TEMPORARY_SESSION_KEY_NAME_PREFIX``. It carries its user's roles, so a
user whose roles reach the key routes could otherwise mint a key that never expires, or extend the session
key itself, with it. The refusal is narrow: it recognises the key by the name prefix, so ordinary API-key
automation that manages keys and every JWT caller are unaffected, and reads and deletes stay open to a
temporary session key. An API-key caller whose key cannot be identified is refused; one whose record
cannot be read is answered as an internal error rather than as a session key. The prefix is reserved: a
create that names a key under it is answered 400 unless it arrives as a Lambda cross-call, which API
Gateway cannot produce, so no user can create a key that would be treated as a session key."""

import ast
import json
import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault('API_KEY_STORAGE_TABLE_NAME', 'test-api-key-table')
os.environ.setdefault('USER_ROLES_STORAGE_TABLE_NAME', 'test-user-roles-table')

from backend.backend.common.constants import TEMPORARY_SESSION_KEY_NAME_PREFIX
from backend.backend.handlers import auth as auth_module
from backend.backend.handlers.auth import apiKeyService
from backend.backend.handlers.auth.apiKeyService import lambda_handler

USER = "session-user"
SESSION_KEY_ID = "11111111-1111-4111-8111-111111111111"
ORDINARY_KEY_ID = "22222222-2222-4222-8222-222222222222"
TARGET_KEY_ID = "33333333-3333-4333-8333-333333333333"
UNKNOWN_KEY_ID = "44444444-4444-4444-8444-444444444444"
# A temporary session key as a component would mint it: the reserved prefix, then a part of its own.
PROBE_SESSION_KEY_NAME = f"{TEMPORARY_SESSION_KEY_NAME_PREFIX}probe-3f2a9c1e4b7d4e0a9d2b6c1f8a7e5b30"
CREATE_BODY = {"apiKeyName": "forever", "userId": USER, "description": "x"}
USER_CREATE_BODY = {"apiKeyName": "mine", "description": "x", "expiresAt": "2027-01-01T00:00:00Z"}
UPDATE_BODY = {"description": "changed"}
REFUSAL = "API keys cannot be created or updated with a temporary session key"
RESERVED_NAME_REFUSAL = ("API key names starting with the temporary session key prefix are reserved. "
                         "Please choose a different name.")
CREATE_ROUTES = [("/auth/api-keys", CREATE_BODY), ("/auth/user/api-keys", USER_CREATE_BODY)]
CREATE_ROUTE_IDS = ["admin scope", "user scope"]
_API_KEY_SERVICE_SOURCE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "auth",
                                       "apiKeyService.py")


def _claims(auth_method, api_key_id=None):
    return {"tokens": [USER], "roles": ["admin"], "mfaEnabled": False, "authMethod": auth_method,
            "apiKeyId": api_key_id}


SESSION_KEY_CALLER = _claims("apiKey", SESSION_KEY_ID)
ORDINARY_KEY_CALLER = _claims("apiKey", ORDINARY_KEY_ID)
JWT_CALLER = _claims("jwt")


def _record(api_key_id, name):
    return {"apiKeyId": api_key_id, "apiKeyName": name, "apiKeyHash": "h", "description": "", "userId": USER,
            "createdBy": USER, "createdAt": "2026-09-08T10:00:00+00:00", "updatedAt": "2026-09-08T10:00:00+00:00",
            "expiresAt": "2026-09-08T20:00:00Z", "isActive": "true"}


RECORDS = {
    SESSION_KEY_ID: _record(SESSION_KEY_ID, PROBE_SESSION_KEY_NAME),
    ORDINARY_KEY_ID: _record(ORDINARY_KEY_ID, "ci-deploy"),
    TARGET_KEY_ID: _record(TARGET_KEY_ID, "someone-elses"),
}


def _real_to_update_expr(record, op="SET"):
    """The update-expression helper as the deployed module has it (the suite binds a mock at import)."""
    keys = list(record)
    names = [f"#f{n}" for n in range(len(keys))]
    values = [f":v{n}" for n in range(len(keys))]
    expression = f"{op} " + ", ".join(f"{name} = {value}" for name, value in zip(names, values))
    return dict(zip(names, keys)), {value: record[key] for key, value in zip(keys, values)}, expression


def _event(method, path, body=None, api_key_id=None, cross_call=False):
    if cross_call:
        event = {"requestContext": {"http": {"method": method, "path": path}},
                 "lambdaCrossCall": {"userName": USER, "mfaEnabled": False}}
    else:
        event = {"requestContext": {"http": {"method": method, "path": path},
                                    "authorizer": {"jwt": {"claims": {"vams:tokens": json.dumps([USER])}}}},
                 "headers": {"authorization": "Bearer test-token"}}
    if api_key_id:
        event["pathParameters"] = {"apiKeyId": api_key_id}
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _run(monkeypatch, claims, method, path, body=None, api_key_id=None, get_item_error=None, cross_call=False):
    """One request through lambda_handler with the caller's claims and the key table's records doubled."""
    table = MagicMock()
    if get_item_error is not None:
        table.get_item.side_effect = get_item_error
    else:
        table.get_item.side_effect = lambda **kwargs: (
            {"Item": dict(RECORDS[kwargs["Key"]["apiKeyId"]])} if kwargs["Key"]["apiKeyId"] in RECORDS else {})
    table.scan.return_value = {"Items": []}
    table.query.return_value = {"Items": []}
    roles = MagicMock()
    roles.query.return_value = {"Items": [{"userId": USER, "roleName": "admin"}]}
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    audit = MagicMock()
    monkeypatch.setattr(apiKeyService, "request_to_claims", lambda event: dict(claims))
    # The suite stands a mock in for handlers.auth at import, so the module's copy of the API-key method
    # constant is bound to the real one, as request_to_claims is bound to the claims under test.
    monkeypatch.setattr(apiKeyService, "AUTH_METHOD_API_KEY", auth_module.AUTH_METHOD_API_KEY)
    monkeypatch.setattr(apiKeyService, "CasbinEnforcer", MagicMock(return_value=enforcer))
    monkeypatch.setattr(apiKeyService, "log_auth_changes", audit)
    monkeypatch.setattr(apiKeyService, "to_update_expr", _real_to_update_expr)
    monkeypatch.setattr(apiKeyService, "api_key_table", table)
    monkeypatch.setattr(apiKeyService, "user_roles_table", roles)
    response = lambda_handler(_event(method, path, body, api_key_id, cross_call), {})
    return response, table, audit


def _message(response):
    return json.loads(response["body"])["message"]


def _looked_up(table):
    """The key ids the handler read records for, in order."""
    return [call.kwargs["Key"]["apiKeyId"] for call in table.get_item.call_args_list]


@pytest.mark.unit
class TestATemporarySessionKeyCannotCreateOrExtendAKey:
    @pytest.mark.parametrize("path,body", CREATE_ROUTES, ids=CREATE_ROUTE_IDS)
    def test_a_create_is_refused_before_anything_is_written(self, monkeypatch, path, body):
        response, table, audit = _run(monkeypatch, SESSION_KEY_CALLER, "POST", path, body)
        assert response["statusCode"] == 403
        assert _message(response) == REFUSAL
        table.put_item.assert_not_called()
        audit.assert_not_called()

    @pytest.mark.parametrize("path", ["/auth/api-keys/{id}", "/auth/user/api-keys/{id}"], ids=CREATE_ROUTE_IDS)
    def test_an_update_is_refused_before_anything_is_written(self, monkeypatch, path):
        response, table, audit = _run(monkeypatch, SESSION_KEY_CALLER, "PUT", path.format(id=SESSION_KEY_ID),
                                      UPDATE_BODY, api_key_id=SESSION_KEY_ID)
        assert response["statusCode"] == 403
        assert _message(response) == REFUSAL
        table.update_item.assert_not_called()
        audit.assert_not_called()

    def test_the_session_key_is_recognised_by_the_name_of_the_record_the_claims_identify(self, monkeypatch):
        response, table, _audit = _run(monkeypatch, SESSION_KEY_CALLER, "POST", "/auth/api-keys", CREATE_BODY)
        assert response["statusCode"] == 403
        assert _looked_up(table) == [SESSION_KEY_ID]

    def test_reads_stay_open_to_a_session_key(self, monkeypatch):
        response, _table, _audit = _run(monkeypatch, SESSION_KEY_CALLER, "GET", "/auth/api-keys")
        assert response["statusCode"] == 200

    def test_deletes_stay_open_to_a_session_key(self, monkeypatch):
        # A session key that can only revoke is a narrower credential than one refused nothing but writes;
        # revoking keys is what the component that minted it does with it.
        response, table, _audit = _run(monkeypatch, SESSION_KEY_CALLER, "DELETE", f"/auth/api-keys/{TARGET_KEY_ID}",
                                       api_key_id=TARGET_KEY_ID)
        assert response["statusCode"] == 200
        table.delete_item.assert_called_once_with(Key={"apiKeyId": TARGET_KEY_ID})

    @pytest.mark.parametrize("claims", [_claims("apiKey", None), _claims("apiKey", UNKNOWN_KEY_ID)],
                             ids=["no key id in the claims", "no record for the key id"])
    def test_an_api_key_caller_whose_key_cannot_be_identified_is_refused(self, monkeypatch, claims):
        response, table, _audit = _run(monkeypatch, claims, "POST", "/auth/api-keys", CREATE_BODY)
        assert response["statusCode"] == 403
        assert _message(response) == REFUSAL
        table.put_item.assert_not_called()

    def test_a_key_record_that_cannot_be_read_is_an_internal_error_not_a_refusal(self, monkeypatch):
        # A transient on the caller's own record says nothing about the credential: the caller is not
        # sent chasing a session key it does not hold, and nothing is written.
        response, table, _audit = _run(monkeypatch, ORDINARY_KEY_CALLER, "POST", "/auth/api-keys", CREATE_BODY,
                                       get_item_error=RuntimeError("table unavailable"))
        assert response["statusCode"] == 500
        assert _message(response) != REFUSAL
        assert "session key" not in _message(response)
        table.put_item.assert_not_called()


@pytest.mark.unit
class TestEveryOtherCallerIsUnaffected:
    def test_an_ordinary_api_key_creates_a_key(self, monkeypatch):
        response, table, audit = _run(monkeypatch, ORDINARY_KEY_CALLER, "POST", "/auth/api-keys", CREATE_BODY)
        assert response["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["apiKeyName"] == "forever"
        assert audit.call_args.args[1] == "apiKeyCreate"
        # The caller's own record was consulted once, and nothing else was read by id.
        assert _looked_up(table) == [ORDINARY_KEY_ID]

    def test_an_ordinary_api_key_updates_a_key(self, monkeypatch):
        response, table, _audit = _run(monkeypatch, ORDINARY_KEY_CALLER, "PUT", f"/auth/api-keys/{TARGET_KEY_ID}",
                                       UPDATE_BODY, api_key_id=TARGET_KEY_ID)
        assert response["statusCode"] == 200
        table.update_item.assert_called_once()
        assert table.update_item.call_args.kwargs["Key"] == {"apiKeyId": TARGET_KEY_ID}

    def test_a_jwt_caller_creates_a_key_without_any_record_lookup(self, monkeypatch):
        response, table, _audit = _run(monkeypatch, JWT_CALLER, "POST", "/auth/api-keys", CREATE_BODY)
        assert response["statusCode"] == 200
        table.put_item.assert_called_once()
        assert _looked_up(table) == []

    def test_a_jwt_caller_updates_a_key_reading_only_the_target(self, monkeypatch):
        response, table, _audit = _run(monkeypatch, JWT_CALLER, "PUT", f"/auth/api-keys/{TARGET_KEY_ID}",
                                       UPDATE_BODY, api_key_id=TARGET_KEY_ID)
        assert response["statusCode"] == 200
        assert set(_looked_up(table)) == {TARGET_KEY_ID}

    def test_a_jwt_caller_creates_a_user_scoped_key(self, monkeypatch):
        response, table, _audit = _run(monkeypatch, JWT_CALLER, "POST", "/auth/user/api-keys", USER_CREATE_BODY)
        assert response["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["userId"] == USER


@pytest.mark.unit
class TestTheSessionKeyPrefixIsReserved:
    @pytest.mark.parametrize("path,body", CREATE_ROUTES, ids=CREATE_ROUTE_IDS)
    @pytest.mark.parametrize("name", [PROBE_SESSION_KEY_NAME, TEMPORARY_SESSION_KEY_NAME_PREFIX + "x",
                                      "  " + PROBE_SESSION_KEY_NAME],
                             ids=["a session key name", "the prefix and one character", "padded"])
    def test_a_user_cannot_create_a_key_under_the_prefix(self, monkeypatch, path, body, name):
        response, table, audit = _run(monkeypatch, JWT_CALLER, "POST", path, dict(body, apiKeyName=name))
        assert response["statusCode"] == 400
        assert _message(response) == RESERVED_NAME_REFUSAL
        table.put_item.assert_not_called()
        audit.assert_not_called()

    def test_the_refusal_names_no_part_of_the_submitted_name(self, monkeypatch):
        response, _table, _audit = _run(monkeypatch, JWT_CALLER, "POST", "/auth/api-keys",
                                        dict(CREATE_BODY, apiKeyName=PROBE_SESSION_KEY_NAME))
        assert "probe" not in _message(response)

    @pytest.mark.parametrize("path,body", CREATE_ROUTES, ids=CREATE_ROUTE_IDS)
    def test_an_ordinary_api_key_cannot_create_one_either(self, monkeypatch, path, body):
        response, table, _audit = _run(monkeypatch, ORDINARY_KEY_CALLER, "POST", path,
                                       dict(body, apiKeyName=PROBE_SESSION_KEY_NAME))
        assert response["statusCode"] == 400
        assert _message(response) == RESERVED_NAME_REFUSAL
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("path,body", CREATE_ROUTES, ids=CREATE_ROUTE_IDS)
    def test_a_lambda_cross_call_creates_a_key_under_the_prefix(self, monkeypatch, path, body):
        response, table, audit = _run(monkeypatch, JWT_CALLER, "POST", path,
                                      dict(body, apiKeyName=PROBE_SESSION_KEY_NAME), cross_call=True)
        assert response["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["apiKeyName"] == PROBE_SESSION_KEY_NAME
        assert audit.call_args.args[1] == "apiKeyCreate"

    @pytest.mark.parametrize("name", ["temporary-session", "temporary-sessions-x", "my-temporary-session-key",
                                      "Temporary-Session-x"],
                             ids=["the prefix without its separator", "a longer word", "the prefix inside",
                                  "another letter case"])
    def test_a_name_that_does_not_start_with_the_prefix_is_accepted(self, monkeypatch, name):
        # Control for the refusals above: the reservation is the prefix the refusal recognises a session
        # key by, compared the same way.
        response, table, _audit = _run(monkeypatch, JWT_CALLER, "POST", "/auth/user/api-keys",
                                       dict(USER_CREATE_BODY, apiKeyName=name))
        assert response["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["apiKeyName"] == name


@pytest.mark.unit
class TestThePrefix:
    def test_the_prefix_is_the_reserved_temporary_session_one(self):
        assert TEMPORARY_SESSION_KEY_NAME_PREFIX == "temporary-session-"
        assert RECORDS[SESSION_KEY_ID]["apiKeyName"].startswith(TEMPORARY_SESSION_KEY_NAME_PREFIX)
        assert not RECORDS[ORDINARY_KEY_ID]["apiKeyName"].startswith(TEMPORARY_SESSION_KEY_NAME_PREFIX)

    def test_the_handler_reads_the_api_key_method_from_the_claims_module(self):
        # The binding in _run stands for this import: the handler compares the claims' authMethod with
        # the constant request_to_claims writes.
        with open(os.path.abspath(_API_KEY_SERVICE_SOURCE), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = {(node.module, alias.name) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names}
        assert ("handlers.auth", "AUTH_METHOD_API_KEY") in imported
        assert auth_module.AUTH_METHOD_API_KEY == "apiKey"
