# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Additional token audiences for the API authorizer (issue #1328).

The authorizer accepts tokens issued to the web app client (Cognito) or to the configured
JWT_AUDIENCE (external IdP), plus an operator-configured list of extra audiences, for example an
app client registered for a remote MCP server. These tests sign real RS256 tokens (joserfc for
Cognito, PyJWT for the external IdP) and run them through the real verifiers with only the JWKS
fetch replaced, so they hold:

* the primary audience still verifies, with and without additional audiences configured;
* a token issued to a configured additional client verifies, as an ID token ('aud') and as an
  access token ('client_id', no 'aud', 'username' but no 'cognito:username' or 'email');
* an unknown audience, a wrong issuer with an accepted audience, an expired token and a wrong
  token_use are denied whichever audiences are configured;
* with the variables unset the accepted set is exactly the primary audience (old behaviour);
* the environment parsing fails closed (malformed JSON, non-list, wildcard, blank entries);
* the accepted audience is reported for the authorization audit record and identity is
  resolved from an access token.
"""

import json
import time
from unittest.mock import patch

import jwt
import pytest
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

from backend.backend.common.auth import authorizerCore as core

KID = "test-kid"
POOL_ID = "us-east-1_TESTPOOL"
COGNITO_BASE = "https://cognito-idp.us-east-1.amazonaws.com"
COGNITO_ISSUER = f"{COGNITO_BASE}/{POOL_ID}"
WEB_CLIENT = "webclient000000000000000001"
MCP_CLIENT = "mcpclient000000000000000002"
OTHER_CLIENT = "otherclient00000000000000003"

EXTERNAL_ISSUER = "https://idp.example.com"
EXTERNAL_AUDIENCE = "vams-web"
EXTERNAL_EXTRA = "vams-mcp"

_KEY = RSAKey.generate_key(2048, parameters={"kid": KID, "alg": "RS256", "use": "sig"})
_PUBLIC_JWK = _KEY.as_dict(private=False)


def _cognito_token(kind="id", client=WEB_CLIENT, **overrides):
    now = int(time.time())
    claims = {
        "sub": "11111111-2222-3333-4444-555555555555",
        "iss": COGNITO_ISSUER,
        "token_use": kind,
        "iat": now,
        "exp": now + 600,
    }
    if kind == "id":
        claims.update({"aud": client, "cognito:username": "alice", "email": "alice@example.com"})
    else:
        # Cognito access tokens carry client_id and username, no aud / cognito:username / email
        claims.update({"client_id": client, "username": "alice", "scope": "openid"})
    claims.update(overrides)
    return joserfc_jwt.encode({"alg": "RS256", "kid": KID}, claims, _KEY)


def _external_token(**overrides):
    now = int(time.time())
    claims = {"sub": "user-1", "iss": EXTERNAL_ISSUER, "aud": EXTERNAL_AUDIENCE,
              "iat": now, "exp": now + 600}
    claims.update(overrides)
    pem = _KEY.as_pem(private=True)
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def cognito(monkeypatch):
    monkeypatch.setattr(core, "AUTH_MODE", "cognito")
    monkeypatch.setattr(core, "USER_POOL_ID", POOL_ID)
    monkeypatch.setattr(core, "APP_CLIENT_ID", WEB_CLIENT)
    monkeypatch.setattr(core, "COGNITO_BASE_URL", COGNITO_BASE)
    monkeypatch.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [], raising=False)
    monkeypatch.setattr(core, "get_cognito_keys", lambda *a, **k: [_PUBLIC_JWK])
    return monkeypatch


@pytest.fixture
def external(monkeypatch):
    monkeypatch.setattr(core, "AUTH_MODE", "external")
    monkeypatch.setattr(core, "JWT_ISSUER_URL", EXTERNAL_ISSUER)
    monkeypatch.setattr(core, "JWT_AUDIENCE", EXTERNAL_AUDIENCE)
    monkeypatch.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [], raising=False)
    monkeypatch.setattr(core, "get_external_keys", lambda *a, **k: [_PUBLIC_JWK])
    return monkeypatch


@pytest.mark.unit
class TestCognitoAudiences:
    @pytest.mark.parametrize("token_use", ["id", "access"])
    def test_the_web_client_is_accepted_with_nothing_configured(self, cognito, token_use):
        assert core.verify_cognito_jwt(_cognito_token(token_use)) is not None

    @pytest.mark.parametrize("token_use", ["id", "access"])
    def test_another_client_is_denied_with_nothing_configured(self, cognito, token_use):
        # Unset = the behaviour before #1328: only the web client is accepted.
        assert core.verify_cognito_jwt(_cognito_token(token_use, client=MCP_CLIENT)) is None

    @pytest.mark.parametrize("token_use", ["id", "access"])
    def test_a_configured_additional_client_is_accepted(self, cognito, token_use):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        claims = core.verify_cognito_jwt(_cognito_token(token_use, client=MCP_CLIENT))
        assert claims is not None
        assert claims["sub"] == "11111111-2222-3333-4444-555555555555"

    @pytest.mark.parametrize("token_use", ["id", "access"])
    def test_the_web_client_stays_accepted_when_additional_clients_are_configured(self, cognito, token_use):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        assert core.verify_cognito_jwt(_cognito_token(token_use)) is not None

    @pytest.mark.parametrize("token_use", ["id", "access"])
    def test_an_unlisted_client_is_denied(self, cognito, token_use):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        assert core.verify_cognito_jwt(_cognito_token(token_use, client=OTHER_CLIENT)) is None

    def test_a_wrong_issuer_is_denied_for_an_accepted_client(self, cognito):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        token = _cognito_token("id", client=MCP_CLIENT,
                               iss=f"{COGNITO_BASE}/us-east-1_OTHERPOOL")
        assert core.verify_cognito_jwt(token) is None

    def test_an_expired_token_is_denied_for_an_accepted_client(self, cognito):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        token = _cognito_token("access", client=MCP_CLIENT, exp=int(time.time()) - 60)
        assert core.verify_cognito_jwt(token) is None

    def test_a_wrong_token_use_is_denied_for_an_accepted_client(self, cognito):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        assert core.verify_cognito_jwt(
            _cognito_token("id", client=MCP_CLIENT, **{"token_use": "refresh"})) is None

    def test_a_list_valued_audience_is_denied(self, cognito):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        assert core.verify_cognito_jwt(
            _cognito_token("id", aud=[WEB_CLIENT, MCP_CLIENT])) is None

    def test_a_token_signed_by_another_key_is_denied_for_an_accepted_client(self, cognito):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        other = RSAKey.generate_key(2048, parameters={"kid": KID})
        now = int(time.time())
        token = joserfc_jwt.encode(
            {"alg": "RS256", "kid": KID},
            {"aud": MCP_CLIENT, "iss": COGNITO_ISSUER, "token_use": "id", "exp": now + 600,
             "sub": "x"},
            other,
        )
        assert core.verify_cognito_jwt(token) is None


@pytest.mark.unit
class TestExternalAudiences:
    def test_the_configured_audience_is_accepted_with_nothing_extra(self, external):
        assert core.verify_external_jwt(_external_token()) is not None

    def test_an_extra_audience_is_denied_with_nothing_extra(self, external):
        assert core.verify_external_jwt(_external_token(aud=EXTERNAL_EXTRA)) is None

    def test_a_configured_additional_audience_is_accepted(self, external):
        external.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [EXTERNAL_EXTRA])
        assert core.verify_external_jwt(_external_token(aud=EXTERNAL_EXTRA)) is not None

    def test_the_configured_audience_stays_accepted(self, external):
        external.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [EXTERNAL_EXTRA])
        assert core.verify_external_jwt(_external_token()) is not None

    def test_an_unlisted_audience_is_denied(self, external):
        external.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [EXTERNAL_EXTRA])
        assert core.verify_external_jwt(_external_token(aud="someone-else")) is None

    def test_a_wrong_issuer_is_denied_for_an_accepted_audience(self, external):
        external.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [EXTERNAL_EXTRA])
        token = _external_token(aud=EXTERNAL_EXTRA, iss="https://other-idp.example.com")
        assert core.verify_external_jwt(token) is None

    def test_an_expired_token_is_denied_for_an_accepted_audience(self, external):
        external.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [EXTERNAL_EXTRA])
        token = _external_token(aud=EXTERNAL_EXTRA, exp=int(time.time()) - 60)
        assert core.verify_external_jwt(token) is None


@pytest.mark.unit
class TestAudienceEnvironmentParsing:
    def test_unset_is_empty(self):
        assert core.parse_additional_audiences(None, "X") == []
        assert core.parse_additional_audiences("", "X") == []

    def test_a_json_list_is_parsed_and_deduplicated(self):
        raw = json.dumps([MCP_CLIENT, OTHER_CLIENT, MCP_CLIENT])
        assert core.parse_additional_audiences(raw, "X") == [MCP_CLIENT, OTHER_CLIENT]

    @pytest.mark.parametrize("raw", ["not json", json.dumps("a-string"), json.dumps({"a": 1})])
    def test_a_malformed_value_fails_closed(self, raw):
        assert core.parse_additional_audiences(raw, "X") == []

    @pytest.mark.parametrize("entry", ["*", "abc*", "", "   ", 5, None])
    def test_an_inexact_entry_is_dropped(self, entry):
        assert core.parse_additional_audiences(json.dumps([entry, MCP_CLIENT]), "X") == [MCP_CLIENT]

    def test_the_accepted_cognito_set_with_nothing_configured_is_the_web_client(self, cognito):
        assert core.accepted_cognito_client_ids() == [WEB_CLIENT]

    def test_the_accepted_external_set_with_nothing_configured_is_the_audience(self, external):
        assert core.accepted_external_audiences() == [EXTERNAL_AUDIENCE]


def _event(token):
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
        "methodArn": "arn:aws:execute-api:us-east-1:123456789012:api/api/GET/database",
    }


@pytest.mark.unit
class TestAuthenticateRequestAudit:
    @pytest.fixture(autouse=True)
    def _no_side_lookups(self, monkeypatch):
        monkeypatch.setattr(core, "ALLOWED_IP_RANGES", [])
        monkeypatch.setattr(core, "_lookup_user_roles", lambda user: ["role-a"] if user == "alice" else [])
        monkeypatch.setattr(core, "resolve_mfa_enabled", lambda *a, **k: False)

    def test_an_additional_client_access_token_resolves_identity_and_reports_the_client(self, cognito):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        result = core.authenticate_request(_event(_cognito_token("access", client=MCP_CLIENT)),
                                           fronted="none")
        assert result["authorized"] is True
        assert result["tokenAudience"] == MCP_CLIENT
        # The roles were read for the access token's 'username' claim.
        assert json.loads(result["context"]["vams:roles"]) == ["role-a"]
        assert result["context"]["username"] == "alice"

    def test_a_web_client_id_token_reports_the_web_client(self, cognito):
        result = core.authenticate_request(_event(_cognito_token("id")), fronted="none")
        assert result["authorized"] is True
        assert result["tokenAudience"] == WEB_CLIENT

    def test_an_external_additional_audience_is_reported(self, external):
        external.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [EXTERNAL_EXTRA])
        token = _external_token(aud=["unrelated", EXTERNAL_EXTRA])
        result = core.authenticate_request(_event(token), fronted="none")
        assert result["authorized"] is True
        assert result["tokenAudience"] == EXTERNAL_EXTRA

    def test_an_unlisted_client_is_unauthenticated(self, cognito):
        result = core.authenticate_request(_event(_cognito_token("access", client=OTHER_CLIENT)),
                                           fronted="none")
        assert result["authorized"] is False
        assert result["failure"] == core.FAILURE_UNAUTHENTICATED


def _m2m_access_token(client=MCP_CLIENT):
    """A Cognito client_credentials access token: no username, sub is the app client id."""
    now = int(time.time())
    claims = {"sub": client, "client_id": client, "iss": COGNITO_ISSUER, "token_use": "access",
              "scope": "vams/api", "iat": now, "exp": now + 600}
    return joserfc_jwt.encode({"alg": "RS256", "kid": KID}, claims, _KEY)


@pytest.mark.unit
class TestMachineToMachineTokensAreRejected:
    def test_a_listed_client_m2m_access_token_is_rejected(self, cognito):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        assert core.verify_cognito_jwt(_m2m_access_token()) is None

    def test_an_m2m_access_token_is_unauthenticated_in_authenticate_request(self, cognito, monkeypatch):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        monkeypatch.setattr(core, "ALLOWED_IP_RANGES", [])
        result = core.authenticate_request(_event(_m2m_access_token()), fronted="none")
        assert result["authorized"] is False
        assert result["failure"] == core.FAILURE_UNAUTHENTICATED

    def test_a_blank_username_access_token_is_rejected(self, cognito):
        cognito.setattr(core, "ADDITIONAL_APP_CLIENT_IDS", [MCP_CLIENT])
        assert core.verify_cognito_jwt(_cognito_token("access", client=MCP_CLIENT, username="  ")) is None

    @pytest.mark.parametrize("kind", ["id", "access"])
    def test_web_client_user_tokens_are_unchanged(self, cognito, kind):
        assert core.verify_cognito_jwt(_cognito_token(kind)) is not None

    def test_an_external_additional_audience_token_without_a_user_is_rejected(self, external):
        external.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [EXTERNAL_EXTRA])
        assert core.verify_external_jwt(_external_token(aud=EXTERNAL_EXTRA, sub="")) is None

    @pytest.mark.parametrize("claim", ["client_id", "azp", "cid"])
    def test_an_external_additional_audience_m2m_token_is_rejected(self, external, claim):
        external.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [EXTERNAL_EXTRA])
        token = _external_token(aud=EXTERNAL_EXTRA, sub="svc-client", **{claim: "svc-client"})
        assert core.verify_external_jwt(token) is None

    def test_an_external_additional_audience_user_token_is_accepted(self, external):
        external.setattr(core, "JWT_ADDITIONAL_AUDIENCES", [EXTERNAL_EXTRA])
        token = _external_token(aud=EXTERNAL_EXTRA, sub="user-1", azp="mcp-client")
        assert core.verify_external_jwt(token) is not None

    def test_the_primary_external_audience_is_not_subject_to_the_user_check(self, external):
        # The web app's own audience keeps the existing behaviour, whatever the client claims say.
        token = _external_token(sub="svc-client", client_id="svc-client")
        assert core.verify_external_jwt(token) is not None
