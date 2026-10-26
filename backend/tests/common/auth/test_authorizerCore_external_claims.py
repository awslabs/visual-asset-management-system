# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""External OAuth: which claim carries the audience, and which claim is the user id.

Issue #788: the authorizer compared only ``aud`` with ``lambdaAuthorizorJWTAudience``, so an IdP
whose access tokens carry ``client_id`` and no ``aud`` (an Amazon Cognito user pool, for one) was
refused on every request. ``lambdaAuthorizorJWTAudienceClaims`` (environment ``JWT_AUDIENCE_CLAIMS``)
names the claims compared with the audience, in order: the first one present in the token must
equal it, and a token carrying none of them is refused. The default ``["aud"]`` is the old check.

Issue #816: the user id. With ``lambdaAuthorizorJWTUserIdClaim`` (environment ``JWT_USER_ID_CLAIM``)
not set, the authorizer keeps its chain (``cognito:username``, then ``username``, then ``sub``); set,
it uses only that claim and refuses a token without it. Either way the resolved id is what the
authorizer passes to the handlers as ``vams:tokens``, so ``request_to_claims`` and every handler see
the identity the roles were looked up for.

Cognito mode ignores both settings. Signature, issuer and expiry are verified as before.

Durable (root CLAUDE.md Rule 13).
"""

import base64
import importlib
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from unittest.mock import patch

from backend.backend.common.auth import authorizerCore as core
from backend.backend.handlers.auth import request_to_claims

ISSUER = "https://idp.example.com"
AUDIENCE = "vams-client"
KID = "claims-kid"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_b64url(value):
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


_NUMBERS = _PRIVATE_KEY.public_key().public_numbers()
JWK = {"kid": KID, "kty": "RSA", "alg": "RS256", "use": "sig",
       "n": _int_b64url(_NUMBERS.n), "e": _int_b64url(_NUMBERS.e)}


def _claims(drop=(), **overrides):
    now = int(time.time())
    claims = {"sub": "sub-1", "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 600}
    claims.update(overrides)
    for name in drop:
        claims.pop(name, None)
    return claims


def _token(drop=(), key=_PRIVATE_KEY, **overrides):
    return jwt.encode(_claims(drop, **overrides), key, algorithm="RS256", headers={"kid": KID})


def _access_token(**overrides):
    """An access token shaped like an Amazon Cognito user pool's: client_id, no aud."""
    key = overrides.pop("key", _PRIVATE_KEY)
    claims = {"client_id": AUDIENCE, "token_use": "access", **overrides}
    return _token(drop=("aud",), key=key, **claims)


@pytest.fixture(autouse=True)
def _external_idp():
    # create=True: the settings did not exist before #788/#816, so the tests run (and fail on
    # their assertions) against the code that predates them.
    with patch.object(core, "JWT_ISSUER_URL", ISSUER), \
         patch.object(core, "JWT_AUDIENCE", AUDIENCE), \
         patch.object(core, "JWT_AUDIENCE_CLAIMS", ["aud"], create=True), \
         patch.object(core, "JWT_USER_ID_CLAIM", "", create=True), \
         patch.object(core, "get_external_keys", return_value=[JWK]):
        core._user_roles_cache.clear()
        yield
        core._user_roles_cache.clear()


def _audience_claims(value):
    return patch.object(core, "JWT_AUDIENCE_CLAIMS", value, create=True)


def _user_id_claim(value):
    return patch.object(core, "JWT_USER_ID_CLAIM", value, create=True)


# ---------------------------------------------------------------------------------------------
# Audience claims (#788)
# ---------------------------------------------------------------------------------------------


@pytest.mark.unit
class TestDefaultAudienceClaimIsUnchanged:
    """["aud"] is the check every existing deployment runs; its outcomes must not move."""

    def test_matching_aud_verifies(self):
        assert core.verify_external_jwt(_token()) is not None

    def test_aud_list_containing_the_audience_verifies(self):
        assert core.verify_external_jwt(_token(aud=["other", AUDIENCE])) is not None

    @pytest.mark.parametrize("aud", ["other-client", ["other-a", "other-b"], "", [], [AUDIENCE, 7], 7],
                             ids=["wrong", "list-without", "empty", "empty-list", "non-string-item",
                                  "number"])
    def test_a_mismatching_or_malformed_aud_is_refused(self, aud):
        assert core.verify_external_jwt(_token(aud=aud)) is None

    def test_a_token_without_aud_is_refused(self):
        """#788 behaviour is kept by default: client_id is not consulted."""
        assert core.verify_external_jwt(_access_token()) is None


@pytest.mark.unit
class TestAudOrClientId:
    """["aud", "client_id"]: aud when present, else client_id."""

    def test_an_access_token_whose_client_id_equals_the_audience_verifies(self):
        with _audience_claims(["aud", "client_id"]):
            claims = core.verify_external_jwt(_access_token())
        assert claims is not None
        assert claims["client_id"] == AUDIENCE

    def test_a_wrong_client_id_is_refused(self):
        with _audience_claims(["aud", "client_id"]):
            assert core.verify_external_jwt(_access_token(client_id="other-client")) is None

    def test_aud_is_checked_first_when_present(self):
        """A mismatching aud is not rescued by a matching client_id."""
        with _audience_claims(["aud", "client_id"]):
            token = _token(aud="other-client", client_id=AUDIENCE)
            assert core.verify_external_jwt(token) is None

    def test_a_matching_aud_verifies_whatever_client_id_says(self):
        with _audience_claims(["aud", "client_id"]):
            assert core.verify_external_jwt(_token(client_id="other-client")) is not None

    def test_a_token_with_neither_claim_is_refused(self):
        with _audience_claims(["aud", "client_id"]):
            assert core.verify_external_jwt(_token(drop=("aud",))) is None

    @pytest.mark.parametrize("client_id", [[AUDIENCE], 7, None, ""],
                             ids=["list", "number", "null", "empty"])
    def test_a_client_id_that_is_not_the_audience_string_is_refused(self, client_id):
        with _audience_claims(["aud", "client_id"]):
            assert core.verify_external_jwt(_access_token(client_id=client_id)) is None

    def test_order_is_honoured_client_id_first(self):
        """["client_id", "aud"]: client_id decides when present, even against a matching aud."""
        with _audience_claims(["client_id", "aud"]):
            assert core.verify_external_jwt(_token(client_id="other-client")) is None
            assert core.verify_external_jwt(_token()) is not None

    @pytest.mark.parametrize("overrides", [
        {"iss": "https://other-idp.example.com"},
        {"exp": int(time.time()) - 60},
    ], ids=["wrong-issuer", "expired"])
    def test_issuer_and_expiry_are_still_verified(self, overrides):
        with _audience_claims(["aud", "client_id"]):
            assert core.verify_external_jwt(_access_token(**overrides)) is None

    def test_the_signature_is_still_verified(self):
        with _audience_claims(["aud", "client_id"]):
            assert core.verify_external_jwt(_access_token()) is not None  # control
            assert core.verify_external_jwt(_access_token(key=_OTHER_KEY)) is None

    def test_an_unparseable_setting_refuses_every_token(self):
        """A malformed JWT_AUDIENCE_CLAIMS parses to None; nothing is accepted on it."""
        with _audience_claims(None):
            assert core.verify_external_jwt(_token()) is None


@pytest.mark.unit
class TestParseAudienceClaims:
    @pytest.mark.parametrize("raw", [None, ""], ids=["unset", "empty"])
    def test_unset_is_the_default(self, raw):
        assert core.parse_jwt_audience_claims(raw) == ["aud"]

    @pytest.mark.parametrize("raw,expected", [
        ('["aud"]', ["aud"]),
        ('["aud", "client_id"]', ["aud", "client_id"]),
        ('["client_id"]', ["client_id"]),
        ('["client_id", "aud"]', ["client_id", "aud"]),
    ])
    def test_valid_lists_parse(self, raw, expected):
        assert core.parse_jwt_audience_claims(raw) == expected

    @pytest.mark.parametrize("raw", ['[]', '["azp"]', '["aud", "aud"]', '"aud"', 'aud', '[1]',
                                     '{"aud": 1}', '["AUD"]'])
    def test_invalid_values_are_refused(self, raw):
        assert core.parse_jwt_audience_claims(raw) is None


@pytest.mark.unit
class TestParseUserIdClaim:
    @pytest.mark.parametrize("raw", [None, "", "   "], ids=["unset", "empty", "blank"])
    def test_unset_is_empty(self, raw):
        assert core.parse_jwt_user_id_claim(raw) == ""

    @pytest.mark.parametrize("raw", ["username", "cognito:username", "email", "preferred_username",
                                     "custom:employee.id", "oid"])
    def test_valid_claim_names_parse(self, raw):
        assert core.parse_jwt_user_id_claim(raw) == raw

    @pytest.mark.parametrize("raw", ["user name", "user/name", "name$", "name\n"])
    def test_invalid_claim_names_are_refused(self, raw):
        assert core.parse_jwt_user_id_claim(raw) is None


@pytest.mark.unit
class TestEnvironmentNames:
    """The variables infra/lib/lambdaBuilder/authFunctions.ts sets are the ones read at load."""

    def test_the_module_reads_jwt_audience_claims_and_jwt_user_id_claim(self, monkeypatch):
        monkeypatch.setenv("JWT_AUDIENCE_CLAIMS", '["aud", "client_id"]')
        monkeypatch.setenv("JWT_USER_ID_CLAIM", "email")
        try:
            importlib.reload(core)
            assert core.JWT_AUDIENCE_CLAIMS == ["aud", "client_id"]
            assert core.JWT_USER_ID_CLAIM == "email"
        finally:
            monkeypatch.delenv("JWT_AUDIENCE_CLAIMS")
            monkeypatch.delenv("JWT_USER_ID_CLAIM")
            importlib.reload(core)
        assert core.JWT_AUDIENCE_CLAIMS == ["aud"]
        assert core.JWT_USER_ID_CLAIM == ""


# ---------------------------------------------------------------------------------------------
# User id (#816)
# ---------------------------------------------------------------------------------------------


def _jwt_event():
    return {
        "headers": {"Authorization": "Bearer sometoken"},
        "path": "/database/db1/assets",
        "requestContext": {"identity": {"sourceIp": "203.0.113.7"}},
    }


def _authenticate(claims, mode="external"):
    """authenticate_request with verification stubbed to return `claims`; records who the
    roles and the MFA hook were resolved for."""
    seen = {}

    def _roles(user_id):
        seen["roles_for"] = user_id
        return ["admin"]

    def _mfa(username, _claims, _event):
        seen["mfa_for"] = username
        return False

    verifier = "verify_external_jwt" if mode == "external" else "verify_cognito_jwt"
    with patch.object(core, "AUTH_MODE", mode), \
         patch.object(core, "ALLOWED_IP_RANGES", []), \
         patch.object(core, "IGNORED_PATHS", []), \
         patch.object(core, "_lookup_user_roles", side_effect=_roles), \
         patch.object(core, "resolve_mfa_enabled", side_effect=_mfa), \
         patch.object(core, verifier, return_value=claims):
        result = core.authenticate_request(_jwt_event(), fronted="none")
    return result, seen


def _handler_tokens(context):
    """What a handler sees: request_to_claims over the REST authorizer context."""
    event = {"requestContext": {"authorizer": dict(context), "http": {"method": "GET"}}}
    return request_to_claims(event)["tokens"]


ALL_NAMES = {"cognito:username": "cognito-name", "username": "plain-name", "sub": "sub-1",
             "email": "first.last@example.com"}


@pytest.mark.unit
class TestUserIdChainWhenNotSet:
    """Not set: today's chain, one test per step, and handlers get the same value."""

    @pytest.mark.parametrize("claims,expected", [
        (ALL_NAMES, "cognito-name"),
        ({"username": "plain-name", "sub": "sub-1"}, "plain-name"),
        ({"sub": "sub-1"}, "sub-1"),
    ], ids=["cognito:username", "username", "sub"])
    def test_the_chain_resolves_the_same_id_for_roles_mfa_and_handlers(self, claims, expected):
        result, seen = _authenticate(dict(claims))

        assert result["authorized"] is True
        assert seen["roles_for"] == expected
        assert seen["mfa_for"] == expected
        assert json.loads(result["context"]["vams:tokens"]) == [expected]
        assert _handler_tokens(result["context"]) == [expected]

    def test_a_vams_tokens_claim_in_the_token_does_not_override_the_authorizer(self):
        """The authorizer's id is the one roles were read for; a token-supplied vams:tokens
        claim would otherwise make handlers act as a different user than the authorizer saw."""
        claims = dict(ALL_NAMES, **{"vams:tokens": '["someone-else"]'})
        result, seen = _authenticate(claims)

        assert seen["roles_for"] == "cognito-name"
        assert _handler_tokens(result["context"]) == ["cognito-name"]


@pytest.mark.unit
class TestUserIdClaimWhenSet:
    def test_only_the_configured_claim_is_used(self):
        with _user_id_claim("email"):
            result, seen = _authenticate(dict(ALL_NAMES))

        assert result["authorized"] is True
        assert seen["roles_for"] == "first.last@example.com"
        assert seen["mfa_for"] == "first.last@example.com"
        assert _handler_tokens(result["context"]) == ["first.last@example.com"]

    def test_username_can_be_named_explicitly(self):
        with _user_id_claim("username"):
            result, seen = _authenticate(dict(ALL_NAMES))
        assert seen["roles_for"] == "plain-name"
        assert _handler_tokens(result["context"]) == ["plain-name"]

    @pytest.mark.parametrize("value", [None, "", 7, ["first.last@example.com"]],
                             ids=["null", "empty", "number", "list"])
    def test_a_token_without_a_usable_value_is_refused(self, value):
        claims = dict(ALL_NAMES)
        if value is None:
            claims.pop("email")
        else:
            claims["email"] = value
        with _user_id_claim("email"):
            result, seen = _authenticate(claims)

        assert result["authorized"] is False
        assert result["context"] is None
        assert "roles_for" not in seen

    def test_an_unparseable_setting_refuses_every_token(self):
        with _user_id_claim(None):
            result, _ = _authenticate(dict(ALL_NAMES))
        assert result["authorized"] is False


@pytest.mark.unit
class TestVerifyRefusesAMissingUserIdClaim:
    def test_verification_refuses_and_logs_the_claim_name_not_the_token(self):
        token = _token(username="plain-name")
        with _user_id_claim("email"), patch.object(core.logger, "error") as log_error:
            assert core.verify_external_jwt(token) is None

        logged = " ".join(str(call.args[0]) for call in log_error.call_args_list)
        assert "email" in logged
        assert token not in logged
        assert token.split(".")[1] not in logged

    def test_verification_accepts_a_token_with_the_claim(self):
        with _user_id_claim("email"):
            claims = core.verify_external_jwt(_token(email="first.last@example.com"))
        assert claims is not None


@pytest.mark.unit
class TestCognitoModeIgnoresBothSettings:
    def test_user_id_claim_and_audience_claims_do_not_apply(self):
        claims = {"cognito:username": "cognito-name", "sub": "sub-1", "email": "e@example.com",
                  "vams:tokens": '["cognito-name"]'}
        with _user_id_claim("email"), _audience_claims(None):
            result, seen = _authenticate(dict(claims), mode="cognito")

        assert result["authorized"] is True
        assert seen["roles_for"] == "cognito-name"
        # The pre-token-generation trigger's vams:tokens claim passes through as before.
        assert result["context"]["vams:tokens"] == '["cognito-name"]'
