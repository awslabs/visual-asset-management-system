# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The authorizer sets every ``vams:`` context key itself and derives the identity from the username claim.

A verified token's claims are copied into the authorizer context that API Gateway hands every handler,
except for keys under the reserved ``vams:`` prefix (matched without regard to case). Those are the
authorizer's own output: ``vams:tokens`` names the same username the authorizer resolved the roles and
the MFA state for (``cognito:username``, then ``username``, then ``sub``), ``vams:externalAttributes`` is
empty, and a verified token that names no username is denied.

These tests run the real REST authorizer handler, ``authenticate_request`` and ``verify_external_jwt``
(real RS256 verification; only the key-set fetch, the role read and the MFA hook are replaced), then
hand the policy context, as API Gateway delivers it, to the real ``request_to_claims``. The Casbin
decisions for the same tokens are covered in ``tests/handlers/auth/test_authorizer_identity_decisions.py``.

They also pin what does not change: Amazon Cognito ID and access tokens (pre-token-generation trigger
v1 and v2 shapes, federated user names included) keep the identity the trigger wrote, an API-key
caller keeps the context the authorizer builds from the key record, and an external token with no
``vams:`` claims keeps its claims and identity.
"""

import base64
import json
import sys
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.backend.handlers.auth import apiGatewayAuthorizerRest as rest
from backend.backend.handlers.auth import pretokengenv1, pretokengenv2, request_to_claims

# The authorizer module object the REST handler actually calls.
core = sys.modules[rest.authenticate_request.__module__]

ISSUER = "https://idp.example.com"
AUDIENCE = "vams-web"
KID = "test-kid"
METHOD_ARN = "arn:aws:execute-api:us-east-1:123456789012:api1/api/POST/auth/user/api-keys"
SOURCE_IP = "203.0.113.7"

CALLER = "user-a"
OTHER_USER = "user-b"
# The role table as the authorizer reads it: the caller holds no role, the other user holds admin.
ROLES_BY_USER = {CALLER: [], OTHER_USER: ["admin"]}
# The reserved-prefix keys the authorizer writes into a token caller's context.
AUTHORIZER_SET_JWT_KEYS = {"vams:tokens", "vams:roles", "vams:mfaEnabled", "vams:externalAttributes"}

_SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64url_int(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


_NUMBERS = _SIGNING_KEY.public_key().public_numbers()
JWK = {"kid": KID, "kty": "RSA", "alg": "RS256", "use": "sig",
       "n": _b64url_int(_NUMBERS.n), "e": _b64url_int(_NUMBERS.e)}


def _external_token(drop=(), **claims):
    now = int(time.time())
    body = {"sub": CALLER, "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 600}
    body.update(claims)
    for name in drop:
        body.pop(name, None)
    return jwt.encode(body, _SIGNING_KEY, algorithm="RS256", headers={"kid": KID})


def _authorizer_event(token):
    return {"type": "REQUEST", "methodArn": METHOD_ARN,
            "headers": {"Authorization": f"Bearer {token}"},
            "requestContext": {"identity": {"sourceIp": SOURCE_IP}}}


def _authorize(token):
    return rest.lambda_handler(_authorizer_event(token), None)


def _handler_event(policy):
    """The integration event API Gateway builds from an Allow: the policy context plus principalId."""
    authorizer = dict(policy["context"])
    authorizer["principalId"] = policy["principalId"]
    return {"httpMethod": "POST", "path": "/auth/user/api-keys", "resource": "/auth/user/api-keys",
            "requestContext": {"identity": {"sourceIp": SOURCE_IP}, "authorizer": authorizer},
            "headers": {}, "body": "{}"}


def _effect(policy):
    return policy["policyDocument"]["Statement"][0]["Effect"]


@pytest.fixture
def role_lookups(monkeypatch):
    """Common authorizer stubs; returns the list of user ids the role lookup was asked for."""
    looked_up = []
    monkeypatch.setattr(core, "ALLOWED_IP_RANGES", [])
    monkeypatch.setattr(core, "_lookup_user_roles",
                        lambda user_id: looked_up.append(user_id) or list(ROLES_BY_USER.get(user_id, [])))
    monkeypatch.setattr(core, "resolve_mfa_enabled", lambda *args, **kwargs: False)
    monkeypatch.setattr(rest, "log_authorization_gateway", lambda *args, **kwargs: None)
    return looked_up


@pytest.fixture
def external_idp(monkeypatch, role_lookups):
    monkeypatch.setattr(core, "AUTH_MODE", "external")
    monkeypatch.setattr(core, "JWT_ISSUER_URL", ISSUER)
    monkeypatch.setattr(core, "JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [], raising=False)
    monkeypatch.setattr(core, "get_external_keys", lambda *args, **kwargs: [JWK])
    return role_lookups


@pytest.mark.unit
class TestExternalTokenReservedClaims:

    def test_a_vams_tokens_claim_does_not_change_the_caller_identity(self, external_idp):
        policy = _authorize(_external_token(**{"vams:tokens": json.dumps([OTHER_USER])}))

        assert _effect(policy) == "Allow"
        context = policy["context"]
        assert context["vams:tokens"] == json.dumps([CALLER])
        assert json.loads(context["vams:roles"]) == []
        assert external_idp == [CALLER]

        claims_and_roles = request_to_claims(_handler_event(policy))
        assert claims_and_roles["tokens"] == [CALLER]

    @pytest.mark.parametrize("claim_name", [
        "vams:tokens", "VAMS:tokens", "Vams:Tokens", "vams:roles", "vams:mfaEnabled", "vams:authMethod",
        "vams:apiKeyId", "vams:externalAttributes", "vams:originVerifyDigest", "vams:notYetDefined",
    ])
    def test_no_reserved_prefix_claim_value_reaches_the_context(self, external_idp, claim_name):
        marker = json.dumps(["claim-value-from-token"])
        policy = _authorize(_external_token(**{claim_name: marker}))

        assert _effect(policy) == "Allow"
        assert marker not in policy["context"].values()
        if claim_name not in AUTHORIZER_SET_JWT_KEYS:
            assert claim_name not in policy["context"]

    def test_credential_type_claims_do_not_reach_the_handler(self, external_idp):
        policy = _authorize(_external_token(**{"vams:authMethod": "apiKey", "vams:apiKeyId": "key-of-another-user"}))

        assert "vams:authMethod" not in policy["context"]
        assert "vams:apiKeyId" not in policy["context"]
        assert request_to_claims(_handler_event(policy))["tokens"] == [CALLER]

    def test_external_attributes_are_set_by_the_authorizer(self, external_idp):
        policy = _authorize(_external_token(**{"vams:externalAttributes": json.dumps(["attribute:value"])}))

        assert policy["context"]["vams:externalAttributes"] == "[]"
        assert request_to_claims(_handler_event(policy))["externalAttributes"] == []

    def test_a_list_valued_identity_claim_is_ignored(self, external_idp):
        policy = _authorize(_external_token(**{"vams:tokens": [OTHER_USER]}))

        assert _effect(policy) == "Allow"
        assert request_to_claims(_handler_event(policy))["tokens"] == [CALLER]

    def test_roles_and_mfa_state_come_from_the_authorizer(self, external_idp):
        policy = _authorize(_external_token(**{"vams:roles": json.dumps(["admin"]), "vams:mfaEnabled": "true"}))

        assert json.loads(policy["context"]["vams:roles"]) == []
        assert policy["context"]["vams:mfaEnabled"] == "false"
        claims_and_roles = request_to_claims(_handler_event(policy))
        assert claims_and_roles["roles"] == []
        assert claims_and_roles["mfaEnabled"] is False

    @pytest.mark.parametrize("claims, drop", [
        ({"vams:tokens": json.dumps([OTHER_USER]), "email": "a@example.com"}, ("sub",)),
        ({"sub": "   ", "vams:tokens": json.dumps([OTHER_USER])}, ()),
        ({"upn": "a@example.com"}, ("sub",)),
    ], ids=["no-sub-with-vams-tokens", "blank-sub", "upn-only"])
    def test_a_token_naming_no_username_is_denied(self, external_idp, claims, drop):
        token = _external_token(drop=drop, **claims)

        result = core.authenticate_request(_authorizer_event(token), fronted="none")
        assert result["authorized"] is False
        assert result["context"] is None
        policy = rest.lambda_handler(_authorizer_event(token), None)
        assert _effect(policy) == "Deny"
        assert "context" not in policy
        assert external_idp == []

    def test_a_token_without_reserved_claims_keeps_its_claims_and_identity(self, external_idp):
        policy = _authorize(_external_token(preferred_username="Alice", email="a@example.com"))

        assert _effect(policy) == "Allow"
        context = policy["context"]
        assert context["sub"] == CALLER
        assert context["preferred_username"] == "Alice"
        assert context["email"] == "a@example.com"
        assert context["iss"] == ISSUER
        assert policy["principalId"] == CALLER
        claims_and_roles = request_to_claims(_handler_event(policy))
        assert claims_and_roles["tokens"] == [CALLER]

    def test_a_username_claim_takes_precedence_over_sub(self, external_idp):
        policy = _authorize(_external_token(username="alice", **{"vams:tokens": json.dumps([OTHER_USER])}))

        assert policy["context"]["vams:tokens"] == json.dumps(["alice"])
        assert external_idp == ["alice"]
        assert request_to_claims(_handler_event(policy))["tokens"] == ["alice"]


def _cognito_trigger_claims(trigger, user_name, token):
    event = {"userName": user_name, "request": {"userAttributes": {"email": "a@example.com"}}, "response": {}}
    response = trigger.lambda_handler(event, None)["response"]
    if trigger is pretokengenv1:
        # The v1 trigger customizes the ID token only.
        return response["claimsOverrideDetails"]["claimsToAddOrOverride"] if token == "id" else {}
    details = response["claimsAndScopeOverrideDetails"]
    key = "idTokenGeneration" if token == "id" else "accessTokenGeneration"
    return details[key]["claimsToAddOrOverride"]


def _cognito_claims(trigger, user_name, token):
    """The verified claims of a Cognito token as the user pool issues it with the trigger's claims added."""
    now = int(time.time())
    base = {"sub": "0c2d1f9e-1111-2222-3333-444455556666",
            "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example",
            "iat": now, "exp": now + 600, "auth_time": now}
    if token == "id":
        base.update({"token_use": "id", "cognito:username": user_name, "aud": "web-client", "email": "a@example.com"})
    else:
        base.update({"token_use": "access", "username": user_name, "client_id": "web-client",
                     "scope": "openid email"})
    base.update(_cognito_trigger_claims(trigger, user_name, token))
    return base


@pytest.mark.unit
class TestCognitoTokensKeepTheirIdentity:

    @pytest.mark.parametrize("trigger", [pretokengenv1, pretokengenv2], ids=["v1", "v2"])
    @pytest.mark.parametrize("token", ["id", "access"])
    @pytest.mark.parametrize("user_name", ["alice", "ExampleIdP_alice@example.com"],
                             ids=["native", "federated"])
    def test_the_identity_is_the_trigger_user_name(self, monkeypatch, role_lookups, trigger, token, user_name):
        claims = _cognito_claims(trigger, user_name, token)
        monkeypatch.setattr(core, "AUTH_MODE", "cognito")
        monkeypatch.setattr(core, "verify_cognito_jwt", lambda raw_token: dict(claims))

        policy = _authorize("cognito-token")

        assert _effect(policy) == "Allow"
        context = policy["context"]
        assert context["vams:tokens"] == json.dumps([user_name])
        if "vams:tokens" in claims:
            assert context["vams:tokens"] == claims["vams:tokens"]
        assert context["vams:externalAttributes"] == "[]"
        assert role_lookups == [user_name]
        claims_and_roles = request_to_claims(_handler_event(policy))
        assert claims_and_roles["tokens"] == [user_name]
        assert claims_and_roles["externalAttributes"] == []


@pytest.mark.unit
class TestApiKeyContextIsUnchanged:

    def test_an_api_key_caller_gets_the_context_built_from_its_key(self, monkeypatch):
        monkeypatch.setattr(core, "ALLOWED_IP_RANGES", [])
        monkeypatch.setattr(core, "_get_user_roles_table", lambda: object())
        monkeypatch.setattr(core, "_lookup_api_key_by_hash", lambda key_hash: {
            "apiKeyId": "key-1", "userId": "service-user", "isActive": "true", "expiresAt": ""})
        monkeypatch.setattr(core, "_lookup_user_roles", lambda user_id: ["pipeline"])

        result = core.authenticate_request(
            {"headers": {"Authorization": "vams_examplekeyvalue"}, "methodArn": METHOD_ARN,
             "requestContext": {"identity": {"sourceIp": SOURCE_IP}}},
            fronted="none")

        assert result["authorized"] is True
        context = result["context"]
        assert set(context) == {"sub", "cognito:username", "vams:tokens", "vams:roles",
                                "vams:apiKeyId", "vams:authMethod"}
        assert context["vams:tokens"] == json.dumps(["service-user"])
        assert context["vams:apiKeyId"] == "key-1"
        assert context["vams:authMethod"] == "apiKey"
        claims_and_roles = request_to_claims(_handler_event({"context": context, "principalId": "service-user"}))
        assert claims_and_roles["tokens"] == ["service-user"]
