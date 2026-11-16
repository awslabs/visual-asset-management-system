# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""External OAuth: the audience claims (#788) and user id claim (#816) together with the
additional token audiences (#1328) and the reserved ``vams:`` claim rule.

Real RS256 tokens go through the real verifier and ``authenticate_request``; only the JWKS fetch,
the role lookup and the MFA hook are replaced. What these tests hold:

* ``lambdaAuthorizorJWTAudienceClaims`` applies to the primary audience and to the additional
  audiences alike: the first listed claim present decides, and it must name an accepted audience;
* a token accepted only through an additional audience keeps the "names a user" and machine-token
  checks, whichever claim carried the audience, and the user it must name is the one
  ``resolve_user_id`` returns (the configured user id claim when set);
* the user id is resolved once and is what the roles, the MFA hook and ``vams:tokens`` carry;
* no ``vams:`` claim of the token reaches the authorizer context, in any letter case;
* a malformed claim setting refuses (403) rather than falling back to another check.

Durable (root CLAUDE.md Rule 13).
"""

import base64
import json
import time
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.backend.common.auth import authorizerCore as core
from backend.backend.handlers.auth import request_to_claims

ISSUER = "https://idp.example.com"
WEB = "vams-web"
MCP = "vams-mcp"
KID = "combined-kid"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_b64url(value):
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


_NUMBERS = _PRIVATE_KEY.public_key().public_numbers()
JWK = {"kid": KID, "kty": "RSA", "alg": "RS256", "use": "sig",
       "n": _int_b64url(_NUMBERS.n), "e": _int_b64url(_NUMBERS.e)}


def _token(drop=(), **overrides):
    now = int(time.time())
    claims = {"sub": "sub-1", "username": "alice", "iss": ISSUER, "aud": WEB,
              "iat": now, "exp": now + 600}
    claims.update(overrides)
    for name in drop:
        claims.pop(name, None)
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})


def _client_id_token(client_id, drop=(), **overrides):
    """An access token shaped like an Amazon Cognito user pool's: client_id, no aud."""
    return _token(drop=("aud", *drop), client_id=client_id, token_use="access", **overrides)


@pytest.fixture(autouse=True)
def _external_idp():
    with patch.object(core, "AUTH_MODE", "external"), \
         patch.object(core, "JWT_ISSUER_URL", ISSUER), \
         patch.object(core, "JWT_AUDIENCE", WEB), \
         patch.object(core, "JWT_ADDITIONAL_AUDIENCES", [MCP]), \
         patch.object(core, "JWT_AUDIENCE_CLAIMS", ["aud", "client_id"]), \
         patch.object(core, "JWT_USER_ID_CLAIM", ""), \
         patch.object(core, "ALLOWED_IP_RANGES", []), \
         patch.object(core, "IGNORED_PATHS", []), \
         patch.object(core, "get_external_keys", return_value=[JWK]):
        core._user_roles_cache.clear()
        yield
        core._user_roles_cache.clear()


def _claims_setting(value):
    return patch.object(core, "JWT_AUDIENCE_CLAIMS", value)


def _user_id_claim(value):
    return patch.object(core, "JWT_USER_ID_CLAIM", value)


def _authenticate_real(token):
    """authenticate_request over the real verifier; records who roles and MFA were read for."""
    seen = {}

    def _roles(user_id):
        seen["roles_for"] = user_id
        return ["admin"]

    def _mfa(username, _claims, _event):
        seen["mfa_for"] = username
        return False

    event = {"headers": {"Authorization": f"Bearer {token}"},
             "path": "/database/db1/assets",
             "requestContext": {"identity": {"sourceIp": "203.0.113.7"}}}
    with patch.object(core, "_lookup_user_roles", side_effect=_roles), \
         patch.object(core, "resolve_mfa_enabled", side_effect=_mfa):
        result = core.authenticate_request(event, fronted="none")
    return result, seen


# ---------------------------------------------------------------------------------------------
# Audience claims compose with the additional audiences
# ---------------------------------------------------------------------------------------------


@pytest.mark.unit
class TestAudienceClaimsAcrossPrimaryAndAdditional:
    @pytest.mark.parametrize("client", [WEB, MCP], ids=["primary", "additional"])
    def test_client_id_names_either_audience(self, client):
        assert core.verify_external_jwt(_client_id_token(client)) is not None

    def test_an_unknown_client_id_is_refused(self):
        assert core.verify_external_jwt(_client_id_token("other-client")) is None

    @pytest.mark.parametrize("audience", [WEB, MCP, [MCP], ["other", MCP]],
                             ids=["primary", "additional", "list", "list-with-other"])
    def test_aud_names_either_audience(self, audience):
        assert core.verify_external_jwt(_token(aud=audience)) is not None

    def test_the_default_claims_still_read_aud_only_for_both_audiences(self):
        with _claims_setting(["aud"]):
            assert core.verify_external_jwt(_token(aud=MCP)) is not None
            assert core.verify_external_jwt(_client_id_token(MCP)) is None
            assert core.verify_external_jwt(_client_id_token(WEB)) is None

    def test_a_matching_client_id_does_not_rescue_a_mismatching_aud(self):
        """The first listed claim present decides: aud names nothing accepted, client_id does."""
        assert core.verify_external_jwt(_token(aud="other", client_id=MCP)) is None

    def test_client_id_first_decides_even_against_a_matching_aud(self):
        with _claims_setting(["client_id", "aud"]):
            assert core.verify_external_jwt(_token(aud=MCP, client_id="other")) is None
            assert core.verify_external_jwt(_token(aud=MCP)) is not None

    def test_without_additional_audiences_only_the_primary_one_is_accepted(self):
        with patch.object(core, "JWT_ADDITIONAL_AUDIENCES", []):
            assert core.verify_external_jwt(_client_id_token(WEB)) is not None
            assert core.verify_external_jwt(_client_id_token(MCP)) is None
            assert core.verify_external_jwt(_token(aud=MCP)) is None

    def test_a_list_aud_holding_a_non_string_is_refused(self):
        assert core.verify_external_jwt(_token(aud=[MCP, 7])) is None

    def test_matched_audience_prefers_the_primary_one(self):
        assert core.matched_external_audience({"aud": [MCP, WEB]}) == WEB
        assert core.matched_external_audience({"aud": [MCP]}) == MCP
        assert core.matched_external_audience({"client_id": MCP}) == MCP
        assert core.matched_external_audience({"client_id": "other"}) is None


# ---------------------------------------------------------------------------------------------
# A token accepted through an additional audience keeps the user and machine-token checks
# ---------------------------------------------------------------------------------------------


@pytest.mark.unit
class TestAdditionalAudienceChecksFollowTheAudienceClaim:
    @pytest.mark.parametrize("make", [
        lambda **kw: _client_id_token(MCP, **kw),
        lambda **kw: _token(aud=MCP, **kw),
    ], ids=["client_id", "aud"])
    def test_a_user_token_verifies(self, make):
        assert core.verify_external_jwt(make()) is not None

    @pytest.mark.parametrize("make", [
        lambda **kw: _client_id_token(MCP, **kw),
        lambda **kw: _token(aud=MCP, **kw),
    ], ids=["client_id", "aud"])
    @pytest.mark.parametrize("overrides", [
        {"gty": "client-credentials"},
        {"idtyp": "app"},
        {"preferred_username": "service-account-vams-mcp"},
        {"username": MCP, "sub": MCP, "azp": MCP},
    ], ids=["auth0", "entra", "keycloak", "identity-is-azp"])
    def test_a_machine_token_is_refused(self, make, overrides):
        assert core.verify_external_jwt(make(**overrides)) is None

    def test_a_token_naming_no_user_is_refused(self):
        assert core.verify_external_jwt(_client_id_token(MCP, drop=("username", "sub"))) is None

    def test_the_primary_audience_is_not_subject_to_the_extra_checks(self):
        """The web app's own audience keeps today's behaviour, through aud or client_id."""
        assert core.verify_external_jwt(_client_id_token(WEB, gty="client-credentials")) is not None
        assert core.verify_external_jwt(_token(aud=WEB, gty="client-credentials")) is not None

    def test_a_token_naming_both_audiences_counts_as_primary(self):
        assert core.verify_external_jwt(_token(aud=[MCP, WEB], gty="client-credentials")) is not None


@pytest.mark.unit
class TestConfiguredUserIdClaimWithAdditionalAudiences:
    def test_the_configured_claim_is_the_identity_checked_for_an_additional_audience(self):
        with _user_id_claim("email"):
            token = _client_id_token(MCP, email="alice@example.com")
            assert core.verify_external_jwt(token) is not None

    def test_a_token_without_the_configured_claim_is_refused_whichever_audience(self):
        with _user_id_claim("email"):
            assert core.verify_external_jwt(_client_id_token(MCP)) is None
            assert core.verify_external_jwt(_client_id_token(WEB)) is None

    def test_a_configured_claim_equal_to_the_client_id_is_refused_for_an_additional_audience(self):
        with _user_id_claim("email"):
            assert core.verify_external_jwt(_client_id_token(MCP, email=MCP)) is None


# ---------------------------------------------------------------------------------------------
# One resolved user id, no token vams: claim
# ---------------------------------------------------------------------------------------------


@pytest.mark.unit
class TestUserIdIsResolvedOnce:
    def test_the_default_chain_feeds_roles_mfa_tokens_and_handlers(self):
        result, seen = _authenticate_real(_client_id_token(MCP))

        assert result["authorized"] is True
        assert seen == {"roles_for": "alice", "mfa_for": "alice"}
        assert json.loads(result["context"]["vams:tokens"]) == ["alice"]
        assert json.loads(result["context"]["vams:externalAttributes"]) == []
        assert request_to_claims({"requestContext": {"authorizer": dict(result["context"])}})["tokens"] == ["alice"]

    def test_the_configured_claim_feeds_all_of_them(self):
        with _user_id_claim("email"):
            result, seen = _authenticate_real(_token(email="alice@example.com"))

        assert result["authorized"] is True
        assert seen == {"roles_for": "alice@example.com", "mfa_for": "alice@example.com"}
        assert json.loads(result["context"]["vams:tokens"]) == ["alice@example.com"]

    def test_the_audit_audience_follows_the_claim_that_admitted_the_token(self):
        result, _ = _authenticate_real(_client_id_token(MCP))
        assert result["tokenAudience"] == MCP
        result, _ = _authenticate_real(_token(aud=[MCP, WEB]))
        assert result["tokenAudience"] == WEB

    def test_a_missing_configured_claim_denies_with_a_401_and_reads_no_roles(self):
        with _user_id_claim("email"):
            result, seen = _authenticate_real(_token())
        assert result["authorized"] is False
        assert result["failure"] == core.FAILURE_UNAUTHENTICATED
        assert seen == {}


@pytest.mark.unit
class TestVamsClaimsInTheTokenAreDropped:
    INJECTED = {
        "vams:tokens": '["admin-user"]',
        "vams:roles": '["super-admin"]',
        "vams:externalAttributes": '["x"]',
        "vams:mfaEnabled": "true",
        "VAMS:Tokens": '["admin-user"]',
        "Vams:Roles": '["super-admin"]',
    }

    @pytest.mark.parametrize("make", [
        lambda **kw: _token(**kw),
        lambda **kw: _client_id_token(MCP, **kw),
    ], ids=["primary", "additional-audience"])
    def test_none_survives_and_the_authorizer_values_win(self, make):
        result, seen = _authenticate_real(make(**self.INJECTED))

        context = result["context"]
        assert result["authorized"] is True
        assert seen["roles_for"] == "alice"
        assert json.loads(context["vams:tokens"]) == ["alice"]
        assert json.loads(context["vams:roles"]) == ["admin"]
        assert json.loads(context["vams:externalAttributes"]) == []
        assert context["vams:mfaEnabled"] == "false"
        assert not [key for key in context if key.startswith(("VAMS:", "Vams:"))]

    @pytest.mark.parametrize("name", ["vams:tokens", "VAMS:Roles", "vams:anything"])
    def test_the_user_id_claim_setting_cannot_name_a_vams_claim(self, name):
        """A token cannot choose its user through the reserved prefix: such a setting is
        malformed, so the environment parses to None and every token is refused."""
        assert core.parse_jwt_user_id_claim(name) is None

    @pytest.mark.parametrize("name", ["email", "upn", "oid", "custom:login", "xvams:tokens", "vamsx"])
    def test_other_claim_names_still_parse(self, name):
        assert core.parse_jwt_user_id_claim(name) == name


@pytest.mark.unit
class TestMalformedClaimSettingsDeny:
    def test_a_malformed_audience_claims_setting_is_a_403_not_a_401(self):
        with _claims_setting(None):
            result, seen = _authenticate_real(_token())
        assert result["authorized"] is False
        assert result["failure"] == core.FAILURE_FORBIDDEN
        assert seen == {}

    def test_a_malformed_user_id_claim_setting_does_not_fall_back_to_the_chain(self):
        with _user_id_claim(None):
            result, seen = _authenticate_real(_token())
        assert result["authorized"] is False
        assert result["failure"] == core.FAILURE_FORBIDDEN
        assert seen == {}
