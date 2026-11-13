# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Authorization decisions follow the identity the authorizer derives from the token's username claim.

A token is run through the real REST authorizer (real RS256 verification of an external IdP token;
only the key-set fetch, the authorizer's role read and the MFA hook are replaced), and the policy
context API Gateway would deliver goes to the real ``request_to_claims`` and then to:

* the real ``CasbinEnforcer`` and ``CasbinEnforcerService`` policy generation and evaluation, with
  only the three table reads stubbed (role assignments per user, constraints per role and per user,
  and the roles a session without MFA may use), so the decision shows whose roles, user-level
  constraints and MFA state were evaluated;
* the real self-service API key handler (``POST /auth/user/api-keys``), with its tables and the
  Casbin decision stubbed, so the stored key shows whose key was created.

A token whose own username claim names one user and whose ``vams:`` claims name another is decided
as the first user; the same request from the other user's own token is the control.
"""

import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("API_KEY_STORAGE_TABLE_NAME", "test-api-key-table")
os.environ.setdefault("USER_ROLES_STORAGE_TABLE_NAME", "test-user-roles-table")

import jwt  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import backend.backend.handlers.authz as authz  # noqa: E402
from backend.backend.handlers.auth import apiGatewayAuthorizerRest as rest  # noqa: E402
from backend.backend.handlers.auth import apiKeyService  # noqa: E402
# tests/conftest.py replaces handlers.auth.request_to_claims; this is the real function.
from backend.backend.handlers.auth import request_to_claims  # noqa: E402

# The authorizer module object the REST handler actually calls.
core = sys.modules[rest.authenticate_request.__module__]

ISSUER = "https://idp.example.com"
AUDIENCE = "vams-web"
KID = "test-kid"
METHOD_ARN = "arn:aws:execute-api:us-east-1:123456789012:api1/api/POST/auth/user/api-keys"
SOURCE_IP = "203.0.113.7"

CALLER = "user-a"
OTHER_USER = "user-b"
OTHER_USERS_DATABASE = "db-of-user-b"

# Role assignments: the caller holds only a role that requires MFA; the other user holds admin.
ROLE_ASSIGNMENTS = {CALLER: ["mfa-admin"], OTHER_USER: ["admin"]}
MFA_REQUIRED_ROLES = {"mfa-admin"}

_ALL_API_ROUTES = {"field": "route__path", "operator": "starts_with", "value": "/"}


def _role_constraint(role_name):
    return {
        "constraintId": f"api-{role_name}",
        "objectType": "api",
        "criteriaAnd": [_ALL_API_ROUTES],
        "criteriaOr": [],
        "groupPermissions": [
            {"groupId": role_name, "permission": "GET", "permissionType": "allow"},
            {"groupId": role_name, "permission": "POST", "permissionType": "allow"},
        ],
        "userPermissions": [],
    }


# A constraint granted to the other user directly (not through a role).
_USER_CONSTRAINT = {
    "constraintId": "database-of-user-b",
    "objectType": "database",
    "criteriaAnd": [{"field": "databaseId", "operator": "equals", "value": OTHER_USERS_DATABASE}],
    "criteriaOr": [],
    "groupPermissions": [],
    "userPermissions": [{"userId": OTHER_USER, "permission": "GET", "permissionType": "allow"}],
}

_SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64url_int(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


_NUMBERS = _SIGNING_KEY.public_key().public_numbers()
JWK = {"kid": KID, "kty": "RSA", "alg": "RS256", "use": "sig",
       "n": _b64url_int(_NUMBERS.n), "e": _b64url_int(_NUMBERS.e)}


def _token(sub, **claims):
    now = int(time.time())
    body = {"sub": sub, "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 600}
    body.update(claims)
    return jwt.encode(body, _SIGNING_KEY, algorithm="RS256", headers={"kid": KID})


def _token_naming_other_user():
    return _token(CALLER, **{"vams:tokens": json.dumps([OTHER_USER]), "vams:mfaEnabled": "true",
                             "vams:roles": json.dumps(["admin"])})


@pytest.fixture
def mfa_session():
    """Whether the authorizer's MFA hook reports an MFA sign-in."""
    return {"enabled": False}


@pytest.fixture
def external_idp(monkeypatch, mfa_session):
    monkeypatch.setattr(core, "AUTH_MODE", "external")
    monkeypatch.setattr(core, "JWT_ISSUER_URL", ISSUER)
    monkeypatch.setattr(core, "JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [], raising=False)
    monkeypatch.setattr(core, "get_external_keys", lambda *args, **kwargs: [JWK])
    monkeypatch.setattr(core, "ALLOWED_IP_RANGES", [])
    monkeypatch.setattr(core, "_lookup_user_roles", lambda user_id: list(ROLE_ASSIGNMENTS.get(user_id, [])))
    monkeypatch.setattr(core, "resolve_mfa_enabled", lambda *args, **kwargs: mfa_session["enabled"])
    monkeypatch.setattr(rest, "log_authorization_gateway", lambda *args, **kwargs: None, raising=False)


def _handler_event(token, body="{}"):
    policy = rest.lambda_handler({"type": "REQUEST", "methodArn": METHOD_ARN,
                                  "headers": {"Authorization": f"Bearer {token}"},
                                  "requestContext": {"identity": {"sourceIp": SOURCE_IP}}}, None)
    assert policy["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    authorizer = dict(policy["context"], principalId=policy["principalId"])
    return {"httpMethod": "POST", "path": "/auth/user/api-keys", "resource": "/auth/user/api-keys",
            "requestContext": {"identity": {"sourceIp": SOURCE_IP}, "authorizer": authorizer},
            "headers": {}, "body": body}


@pytest.fixture
def decide():
    """Build the real CasbinEnforcer for a handler event and return its decision function."""
    previous_policy_map = authz.casbin_user_policy_map
    previous_enforcer_map = authz.casbin_user_enforcer_map

    def role_rows(service):
        return [{"userId": service._user_id, "roleName": name}
                for name in ROLE_ASSIGNMENTS.get(service._user_id, [])]

    def constraints(service, role_names):
        found = [_role_constraint(name) for name in role_names]
        if any(p["userId"] == service._user_id for p in _USER_CONSTRAINT["userPermissions"]):
            found.append(json.loads(json.dumps(_USER_CONSTRAINT)))
        return found

    def roles_without_mfa(service, role_names):
        return [{"roleName": name} for name in role_names if name not in MFA_REQUIRED_ROLES]

    def build(event):
        authz.casbin_user_policy_map = {}
        authz.casbin_user_enforcer_map = {}
        return authz.CasbinEnforcer(request_to_claims(event))

    with patch.object(authz.CasbinEnforcerService, "_read_current_user_roles_from_table",
                      autospec=True, side_effect=role_rows), \
         patch.object(authz.CasbinEnforcerService, "_read_policies_batch_optimized",
                      autospec=True, side_effect=constraints), \
         patch.object(authz.CasbinEnforcerService, "_read_mfaNotRequired_roles_from_table",
                      autospec=True, side_effect=roles_without_mfa), \
         patch.object(authz, "log_authorization", MagicMock()):
        try:
            yield build
        finally:
            authz.casbin_user_policy_map = previous_policy_map
            authz.casbin_user_enforcer_map = previous_enforcer_map


def _api(path):
    return {"object__type": "api", "route__path": path}


def _database(database_id):
    return {"object__type": "database", "databaseId": database_id}


@pytest.mark.unit
class TestDecisionsUseTheUsernameClaim:

    def test_the_enforcer_is_built_for_the_username_claim(self, external_idp, decide):
        event = _handler_event(_token_naming_other_user())

        assert decide(event).service_object._user_id == CALLER
        assert request_to_claims(event)["tokens"] == [CALLER]

    def test_another_users_roles_are_not_evaluated(self, external_idp, decide):
        enforcer = decide(_handler_event(_token_naming_other_user()))

        assert enforcer.enforce(_api("/auth/user/api-keys"), "POST") is False
        # Control: the other user's own token is allowed through the admin role.
        assert decide(_handler_event(_token(OTHER_USER))).enforce(_api("/auth/user/api-keys"), "POST") is True

    def test_another_users_constraints_are_not_evaluated(self, external_idp, decide):
        enforcer = decide(_handler_event(_token_naming_other_user()))

        assert enforcer.enforce(_database(OTHER_USERS_DATABASE), "GET") is False
        assert decide(_handler_event(_token(OTHER_USER))).enforce(_database(OTHER_USERS_DATABASE), "GET") is True

    def test_the_mfa_state_is_the_authorizers(self, external_idp, decide, mfa_session):
        # The caller's own role requires MFA; the token's vams:mfaEnabled claim does not satisfy it.
        enforcer = decide(_handler_event(_token(CALLER, **{"vams:mfaEnabled": "true"})))
        assert enforcer.enforce(_api("/auth/user/api-keys"), "POST") is False

        # Control: an MFA sign-in reported by the authorizer's hook makes the role usable.
        mfa_session["enabled"] = True
        assert decide(_handler_event(_token(CALLER))).enforce(_api("/auth/user/api-keys"), "POST") is True


@pytest.mark.unit
class TestSelfServiceApiKeyOwner:

    def _create_key(self, event):
        built_for = []

        def enforcer_factory(claims_and_roles):
            built_for.append(claims_and_roles["tokens"][0])
            enforcer = MagicMock()
            enforcer.enforceAPI.return_value = True
            return enforcer

        key_table = MagicMock()
        key_table.query.return_value = {"Items": []}
        key_table.scan.return_value = {"Items": []}
        roles_table = MagicMock()
        roles_table.query.return_value = {"Items": [{"userId": OTHER_USER, "roleName": "admin"},
                                                    {"userId": CALLER, "roleName": "mfa-admin"}]}
        with patch.object(apiKeyService, "request_to_claims", request_to_claims), \
             patch.object(apiKeyService, "CasbinEnforcer", side_effect=enforcer_factory), \
             patch.object(apiKeyService, "log_auth_changes"), \
             patch.object(apiKeyService, "api_key_table", key_table), \
             patch.object(apiKeyService, "user_roles_table", roles_table), \
             patch.object(apiKeyService, "logger", MagicMock()):
            response = apiKeyService.lambda_handler(event, {})
        return response, built_for, key_table

    def test_the_key_is_created_for_the_username_claim(self, external_idp):
        expires = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0)
        body = json.dumps({"apiKeyName": "k", "description": "d",
                           "expiresAt": expires.isoformat().replace("+00:00", "Z")})

        response, built_for, key_table = self._create_key(_handler_event(_token_naming_other_user(), body))

        assert response["statusCode"] == 200, response
        assert built_for == [CALLER]
        item = key_table.put_item.call_args[1]["Item"]
        assert item["userId"] == CALLER
        assert item["createdBy"] == CALLER
