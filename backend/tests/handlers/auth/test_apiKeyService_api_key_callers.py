# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A caller that authenticated with an API key can neither create an API key nor change a key's expiration
(issue #433), in the administrative and the self-service scope alike.

Without the rule a leaked key could keep itself alive indefinitely: each key it mints gets a fresh
expiration window, and it could move its own expiration out or, on the administrative route, clear it.
Reads, description changes, deactivation and deletes stay open to an API key, so a key can still list and
revoke itself. A user signed in through the deployment's identity provider is unaffected, on an Amazon
Cognito deployment and on an external OAuth IdP deployment alike, and the narrower temporary session key
refusal (test_apiKeyService_temporary_session_key_callers.py) still answers first for a session key.

The requests carry the authorizer context API Gateway passes to the handler (the REST API's flat
``requestContext.authorizer`` map, as ``authorizerCore.authenticate_request`` builds it for each kind of
credential), read by the real ``request_to_claims``, so the tests decide on the same ``vams:authMethod``
marking a deployed request carries. That an identity provider's token cannot itself carry the API-key
marking is covered in tests/common/auth/test_authorizerCore_reserved_claims.py."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

os.environ.setdefault('API_KEY_STORAGE_TABLE_NAME', 'test-api-key-table')
os.environ.setdefault('USER_ROLES_STORAGE_TABLE_NAME', 'test-user-roles-table')

from backend.backend.common.constants import TEMPORARY_SESSION_KEY_NAME_PREFIX
from backend.backend.handlers import auth as auth_module
from backend.backend.handlers.auth import apiKeyService
from backend.backend.handlers.auth.apiKeyService import lambda_handler

USER = "automation-user"
OWN_KEY_ID = "11111111-1111-4111-8111-111111111111"
OTHER_KEY_ID = "22222222-2222-4222-8222-222222222222"
SESSION_KEY_ID = "33333333-3333-4333-8333-333333333333"
MISSING_KEY_ID = "44444444-4444-4444-8444-444444444444"
SESSION_REFUSAL = "API keys cannot be created or updated with a temporary session key"
# The messages are part of the contract: api/auth.md quotes them and the CLI prints them.
API_KEY_CALLER_CREATE_REFUSAL = ("API keys cannot be created with an API key. "
                                 "Sign in through your identity provider to create one.")
API_KEY_CALLER_EXPIRATION_REFUSAL = ("An API key's expiration cannot be changed with an API key. "
                                     "Sign in through your identity provider to change it.")

_NOW = datetime.now(timezone.utc)
CREATED_AT = (_NOW - timedelta(days=1)).isoformat()
LATER = (_NOW + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
SOONER = (_NOW + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
STORED_EXPIRY = (_NOW + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

ADMIN_CREATE = ("/auth/api-keys", {"apiKeyName": "renewed", "userId": USER, "description": "x"})
USER_CREATE = ("/auth/user/api-keys", {"apiKeyName": "renewed", "description": "x", "expiresAt": LATER})
CREATE_ROUTES = [ADMIN_CREATE, USER_CREATE]
ADMIN_KEY = "/auth/api-keys/{id}"
USER_KEY = "/auth/user/api-keys/{id}"
SCOPES = ["admin scope", "user scope"]


def _roles_context(user):
    return {"vams:tokens": json.dumps([user]), "vams:roles": json.dumps(["admin"])}


def _api_key_context(api_key_id):
    """What the authorizer passes for a verified API key (authorizerCore.verify_api_key)."""
    context = {"sub": USER, "cognito:username": USER, "vams:apiKeyId": api_key_id, "vams:authMethod": "apiKey"}
    return dict(_roles_context(USER), **context)


def _jwt_context(token_claims):
    """What the authorizer passes for a verified token: its claims, then the vams: keys it sets itself."""
    context = {"vams:externalAttributes": "[]", "vams:mfaEnabled": "false"}
    return dict(token_claims, **_roles_context(USER), **context)


API_KEY_CALLER = _api_key_context(OWN_KEY_ID)
SESSION_KEY_CALLER = _api_key_context(SESSION_KEY_ID)
COGNITO_USER = _jwt_context({
    "sub": "9d1c7e3a-0000-4000-8000-000000000001", "username": USER, "token_use": "access",
    "client_id": "exampleappclient", "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE"})
EXTERNAL_IDP_USER = _jwt_context({
    "sub": USER, "aud": "vams-web", "iss": "https://idp.example.com/oauth2/default",
    "iat": str(int(_NOW.timestamp())), "exp": str(int(_NOW.timestamp()) + 600)})
IDP_USERS = [COGNITO_USER, EXTERNAL_IDP_USER]
IDP_USER_IDS = ["Amazon Cognito user", "external OAuth IdP user"]


def _record(api_key_id, name):
    return {"apiKeyId": api_key_id, "apiKeyName": name, "apiKeyHash": "h", "description": "", "userId": USER,
            "createdBy": USER, "createdAt": CREATED_AT, "updatedAt": CREATED_AT, "expiresAt": STORED_EXPIRY,
            "isActive": "true"}


RECORDS = {
    OWN_KEY_ID: _record(OWN_KEY_ID, "ci-rotation"),
    OTHER_KEY_ID: _record(OTHER_KEY_ID, "nightly-export"),
    SESSION_KEY_ID: _record(SESSION_KEY_ID, f"{TEMPORARY_SESSION_KEY_NAME_PREFIX}edh-probe"),
}


def _real_to_update_expr(record, op="SET"):
    """The update-expression helper as the deployed module has it (the suite binds a mock at import)."""
    keys = list(record)
    names = [f"#f{n}" for n in range(len(keys))]
    values = [f":v{n}" for n in range(len(keys))]
    expression = f"{op} " + ", ".join(f"{name} = {value}" for name, value in zip(names, values))
    return dict(zip(names, keys)), {value: record[key] for key, value in zip(keys, values)}, expression


def _event(method, path, authorizer, body=None, api_key_id=None):
    """A REST API proxy event as API Gateway builds it from the authorizer's Allow."""
    event = {"httpMethod": method, "path": path, "resource": path,
             "requestContext": {"identity": {"sourceIp": "198.51.100.7"},
                                "authorizer": dict(authorizer, principalId=USER)},
             "headers": {"authorization": "Bearer <redacted>"}, "queryStringParameters": {}}
    if api_key_id:
        event["pathParameters"] = {"apiKeyId": api_key_id}
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _run(monkeypatch, authorizer, method, path, body=None, api_key_id=None):
    """One request through lambda_handler, its claims read by the real request_to_claims."""
    table = MagicMock()
    table.get_item.side_effect = lambda **kwargs: (
        {"Item": dict(RECORDS[kwargs["Key"]["apiKeyId"]])} if kwargs["Key"]["apiKeyId"] in RECORDS else {})
    # Empty pages: no key name is taken, and a listing answers an empty page.
    table.scan.return_value = {"Items": []}
    table.query.return_value = {"Items": []}
    roles = MagicMock()
    roles.query.return_value = {"Items": [{"userId": USER, "roleName": "admin"}]}
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    audit = MagicMock()
    monkeypatch.setattr(apiKeyService, "request_to_claims", auth_module.request_to_claims)
    monkeypatch.setattr(apiKeyService, "AUTH_METHOD_API_KEY", auth_module.AUTH_METHOD_API_KEY)
    monkeypatch.setattr(apiKeyService, "CasbinEnforcer", MagicMock(return_value=enforcer))
    monkeypatch.setattr(apiKeyService, "log_auth_changes", audit)
    monkeypatch.setattr(apiKeyService, "log_action", MagicMock())
    monkeypatch.setattr(apiKeyService, "to_update_expr", _real_to_update_expr)
    monkeypatch.setattr(apiKeyService, "api_key_table", table)
    monkeypatch.setattr(apiKeyService, "user_roles_table", roles)
    response = lambda_handler(_event(method, path, authorizer, body, api_key_id), {})
    return response, table, audit


def _message(response):
    return json.loads(response["body"])["message"]


def _looked_up(table):
    return [call.kwargs["Key"]["apiKeyId"] for call in table.get_item.call_args_list]


def _written_fields(table):
    names = table.update_item.call_args.kwargs["ExpressionAttributeNames"]
    values = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    return {field: values[f":v{name[2:]}"] for name, field in names.items()}


@pytest.mark.unit
class TestTheCallersReachTheHandlerAsTheirMethod:
    """Control: the contexts above are read by request_to_claims as the credential each stands for."""

    @pytest.mark.parametrize("authorizer,method", [(API_KEY_CALLER, "apiKey"), (COGNITO_USER, "jwt"),
                                                   (EXTERNAL_IDP_USER, "jwt")],
                             ids=["API key", "Amazon Cognito user", "external OAuth IdP user"])
    def test_the_auth_method(self, authorizer, method):
        claims = auth_module.request_to_claims(_event("GET", "/auth/user/api-keys", authorizer))
        assert claims["authMethod"] == method
        assert claims["tokens"] == [USER]


@pytest.mark.unit
class TestAnApiKeyCannotCreateAKey:
    @pytest.mark.parametrize("path,body", CREATE_ROUTES, ids=SCOPES)
    def test_a_create_is_refused_and_nothing_is_written(self, monkeypatch, path, body):
        response, table, audit = _run(monkeypatch, API_KEY_CALLER, "POST", path, body)
        assert response["statusCode"] == 403
        assert _message(response) == API_KEY_CALLER_CREATE_REFUSAL
        table.put_item.assert_not_called()
        audit.assert_not_called()

    def test_a_create_for_another_user_is_refused(self, monkeypatch):
        response, table, _audit = _run(monkeypatch, API_KEY_CALLER, "POST", ADMIN_CREATE[0],
                                       dict(ADMIN_CREATE[1], userId="someone-else"))
        assert response["statusCode"] == 403
        table.put_item.assert_not_called()


@pytest.mark.unit
class TestAnApiKeyCannotChangeAnExpiration:
    @pytest.mark.parametrize("path,key_id,body", [
        (ADMIN_KEY, OWN_KEY_ID, {"expiresAt": LATER}),
        (USER_KEY, OWN_KEY_ID, {"expiresAt": LATER}),
        (ADMIN_KEY, OTHER_KEY_ID, {"expiresAt": ""}),
        (USER_KEY, OTHER_KEY_ID, {"expiresAt": SOONER}),
        (USER_KEY, OWN_KEY_ID, {"expiresAt": LATER, "description": "rotated", "isActive": "true"}),
    ], ids=["admin scope extends its own key", "user scope extends its own key",
            "admin scope clears another key's expiration", "user scope shortens another key",
            "an extension sent with other fields"])
    def test_the_change_is_refused_and_nothing_is_written(self, monkeypatch, path, key_id, body):
        response, table, audit = _run(monkeypatch, API_KEY_CALLER, "PUT", path.format(id=key_id), body,
                                      api_key_id=key_id)
        assert response["statusCode"] == 403
        assert _message(response) == API_KEY_CALLER_EXPIRATION_REFUSAL
        table.update_item.assert_not_called()
        audit.assert_not_called()
        # Only the caller's own record was read (for the session-key check); the target was not.
        assert _looked_up(table) == [OWN_KEY_ID]

    @pytest.mark.parametrize("path", [ADMIN_KEY, USER_KEY], ids=SCOPES)
    def test_a_key_that_does_not_exist_gets_the_same_refusal(self, monkeypatch, path):
        response, _table, _audit = _run(monkeypatch, API_KEY_CALLER, "PUT", path.format(id=MISSING_KEY_ID),
                                        {"expiresAt": LATER}, api_key_id=MISSING_KEY_ID)
        assert response["statusCode"] == 403
        assert _message(response) == API_KEY_CALLER_EXPIRATION_REFUSAL


@pytest.mark.unit
class TestAnApiKeyCanStillReadAndRevoke:
    @pytest.mark.parametrize("path,key_id", [("/auth/user/api-keys", None), ("/auth/api-keys", None),
                                             (USER_KEY.format(id=OWN_KEY_ID), OWN_KEY_ID)],
                             ids=["its user's keys", "every key", "one key"])
    def test_reads(self, monkeypatch, path, key_id):
        response, _table, _audit = _run(monkeypatch, API_KEY_CALLER, "GET", path, api_key_id=key_id)
        assert response["statusCode"] == 200

    @pytest.mark.parametrize("path", [ADMIN_KEY, USER_KEY], ids=SCOPES)
    def test_it_deactivates_itself(self, monkeypatch, path):
        response, table, _audit = _run(monkeypatch, API_KEY_CALLER, "PUT", path.format(id=OWN_KEY_ID),
                                       {"isActive": "false"}, api_key_id=OWN_KEY_ID)
        assert response["statusCode"] == 200
        assert table.update_item.call_args.kwargs["Key"] == {"apiKeyId": OWN_KEY_ID}
        assert _written_fields(table)["isActive"] == "false"
        assert "expiresAt" not in _written_fields(table)

    def test_it_changes_a_description(self, monkeypatch):
        response, table, _audit = _run(monkeypatch, API_KEY_CALLER, "PUT", USER_KEY.format(id=OTHER_KEY_ID),
                                       {"description": "owned by the data team"}, api_key_id=OTHER_KEY_ID)
        assert response["statusCode"] == 200
        assert _written_fields(table)["description"] == "owned by the data team"

    def test_it_deletes_itself(self, monkeypatch):
        response, table, _audit = _run(monkeypatch, API_KEY_CALLER, "DELETE", USER_KEY.format(id=OWN_KEY_ID),
                                       api_key_id=OWN_KEY_ID)
        assert response["statusCode"] == 200
        table.delete_item.assert_called_once_with(Key={"apiKeyId": OWN_KEY_ID})


@pytest.mark.unit
@pytest.mark.parametrize("authorizer", IDP_USERS, ids=IDP_USER_IDS)
class TestAnIdentityProviderUserIsUnaffected:
    @pytest.mark.parametrize("path,body", CREATE_ROUTES, ids=SCOPES)
    def test_it_creates_a_key(self, monkeypatch, authorizer, path, body):
        response, table, audit = _run(monkeypatch, authorizer, "POST", path, body)
        assert response["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["apiKeyName"] == "renewed"
        assert audit.call_args.args[1] == "apiKeyCreate"

    @pytest.mark.parametrize("path", [ADMIN_KEY, USER_KEY], ids=SCOPES)
    def test_it_extends_a_key(self, monkeypatch, authorizer, path):
        response, table, _audit = _run(monkeypatch, authorizer, "PUT", path.format(id=OWN_KEY_ID),
                                       {"expiresAt": LATER}, api_key_id=OWN_KEY_ID)
        assert response["statusCode"] == 200
        assert _written_fields(table)["expiresAt"] == LATER

    def test_it_clears_an_expiration_on_the_admin_route(self, monkeypatch, authorizer):
        response, table, _audit = _run(monkeypatch, authorizer, "PUT", ADMIN_KEY.format(id=OTHER_KEY_ID),
                                       {"expiresAt": ""}, api_key_id=OTHER_KEY_ID)
        assert response["statusCode"] == 200
        assert _written_fields(table)["expiresAt"] == ""


@pytest.mark.unit
class TestTheTemporarySessionKeyRefusalStillHolds:
    @pytest.mark.parametrize("path,body", CREATE_ROUTES, ids=SCOPES)
    def test_a_session_key_create_gets_the_session_key_refusal(self, monkeypatch, path, body):
        response, table, _audit = _run(monkeypatch, SESSION_KEY_CALLER, "POST", path, body)
        assert response["statusCode"] == 403
        assert _message(response) == SESSION_REFUSAL
        table.put_item.assert_not_called()

    def test_a_session_key_cannot_make_the_update_an_ordinary_key_may_make(self, monkeypatch):
        response, table, _audit = _run(monkeypatch, SESSION_KEY_CALLER, "PUT", USER_KEY.format(id=OTHER_KEY_ID),
                                       {"description": "changed"}, api_key_id=OTHER_KEY_ID)
        assert response["statusCode"] == 403
        assert _message(response) == SESSION_REFUSAL
        table.update_item.assert_not_called()
