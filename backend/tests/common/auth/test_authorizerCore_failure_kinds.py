# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Failure kind of every unauthorized authenticate_request result (issue #1212).

The REST authorizer answers FAILURE_UNAUTHENTICATED with a 401 and everything else with a
403 Deny, so the kind decides whether a client is told to sign in again. The rule:

* the credential itself did not authenticate -> unauthenticated: a missing or malformed
  header, a JWT that is malformed, expired, badly signed, has the wrong audience, issuer or
  token_use, or names an unknown kid; an API key that is unknown, disabled, expired, has an
  unreadable expiry or no userId (all alike, so the status does not reveal which keys exist);
* anything else -> forbidden: a blocked IP, an API key whose user has no roles, a
  misconfigured AUTH_MODE or verifier, a JWKS / discovery outage, an API key table outage.

Every denial below is paired with a control that a good credential still authorizes, so a
change that made everything one kind would not pass.
"""
import base64
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from unittest.mock import patch

from backend.backend.common.auth import authorizerCore as core

UNAUTH = core.FAILURE_UNAUTHENTICATED
FORBID = core.FAILURE_FORBIDDEN

ISSUER = "https://idp.example.com"
AUDIENCE = "vams-client"
KID = "test-kid"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64url_int(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


_NUMBERS = _PRIVATE_KEY.public_key().public_numbers()
JWK = {"kid": KID, "kty": "RSA", "alg": "RS256", "use": "sig",
       "n": _b64url_int(_NUMBERS.n), "e": _b64url_int(_NUMBERS.e)}


def _token(key=_PRIVATE_KEY, kid=KID, **overrides):
    now = int(time.time())
    claims = {"sub": "user-1", "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 600}
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def _evt(auth="Bearer good.jwt.token", source_ip="203.0.113.7"):
    headers = {"Authorization": auth} if auth is not None else {}
    return {
        "methodArn": "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/GET/database",
        "requestContext": {"identity": {"sourceIp": source_ip}},
        "headers": headers,
    }


def _auth(evt):
    return core.authenticate_request(evt, fronted="none")


@pytest.fixture(autouse=True)
def _quiet_side_lookups():
    # Role and MFA resolution run after a token verifies; neither decides the failure kind.
    with patch.object(core, "_lookup_user_roles", return_value=["admin"]), \
         patch.object(core, "resolve_mfa_enabled", return_value=False):
        yield


@pytest.fixture
def external_mode():
    with patch.object(core, "AUTH_MODE", "external"), \
         patch.object(core, "JWT_ISSUER_URL", ISSUER), \
         patch.object(core, "JWT_AUDIENCE", AUDIENCE), \
         patch.object(core, "get_external_keys", return_value=[JWK]):
        yield


@pytest.mark.unit
class TestHeaderAndJwtKinds:
    @pytest.mark.parametrize("auth", [None, "", "Basic dXNlcjpwYXNz", "Bearer", "token-without-scheme"])
    def test_missing_or_malformed_header_is_unauthenticated(self, external_mode, auth):
        res = _auth(_evt(auth=auth))
        assert res["authorized"] is False
        assert res["failure"] == UNAUTH

    def test_valid_external_token_authorizes(self, external_mode):
        res = _auth(_evt(auth=f"Bearer {_token()}"))
        assert res["authorized"] is True
        assert "failure" not in res

    @pytest.mark.parametrize("token_factory", [
        lambda: _token(exp=int(time.time()) - 60),          # expired
        lambda: _token(key=_OTHER_KEY),                     # bad signature
        lambda: _token(aud="someone-else"),                 # wrong audience
        lambda: _token(iss="https://evil.example.com"),     # wrong issuer
        lambda: _token(kid="ghost-kid"),                    # unknown kid (after refetch)
        lambda: "not.a.jwt",                                # malformed
    ], ids=["expired", "bad-signature", "wrong-aud", "wrong-iss", "unknown-kid", "malformed"])
    def test_rejected_external_token_is_unauthenticated(self, external_mode, token_factory):
        with patch.object(core, "_jwks_refetch_is_rate_limited", return_value=True):
            res = _auth(_evt(auth=f"Bearer {token_factory()}"))
        assert res["authorized"] is False
        assert res["failure"] == UNAUTH
        assert res["reason"] == "Token verification failed"

    def test_rejected_cognito_token_is_unauthenticated(self):
        with patch.object(core, "AUTH_MODE", "cognito"), \
             patch.object(core, "verify_cognito_jwt", return_value=None):
            res = _auth(_evt())
        assert res["failure"] == UNAUTH

    @pytest.mark.parametrize("claims_over", [
        {"exp": 1},                       # expired
        {"aud": "other-client"},          # wrong audience
        {"iss": "https://wrong/pool"},    # wrong issuer
        {"token_use": "refresh"},         # wrong token_use
    ], ids=["expired", "wrong-aud", "wrong-iss", "wrong-token-use"])
    def test_cognito_claim_rejections_return_none_not_unavailable(self, claims_over):
        claims = {"sub": "u1", "exp": int(time.time()) + 600, "aud": "app-client",
                  "iss": "https://cognito-idp.test/pool-1", "token_use": "id"}
        claims.update(claims_over)

        class _Decoded:
            pass

        decoded = _Decoded()
        decoded.claims = claims

        class _Header:
            protected = {"kid": KID}

        with patch.object(core, "USER_POOL_ID", "pool-1"), \
             patch.object(core, "APP_CLIENT_ID", "app-client"), \
             patch.object(core, "COGNITO_BASE_URL", "https://cognito-idp.test"), \
             patch.object(core.joserfc_jws, "extract_compact", return_value=_Header()), \
             patch.object(core, "get_cognito_keys", return_value=[{"kid": KID}]), \
             patch.object(core.joserfc_jwk, "import_key", return_value="public-key"), \
             patch.object(core.joserfc_jwt, "decode", return_value=decoded):
            assert core.verify_cognito_jwt("h.p.s") is None


@pytest.mark.unit
class TestOutageAndConfigurationKinds:
    def test_external_jwks_outage_is_forbidden(self):
        with patch.object(core, "AUTH_MODE", "external"), \
             patch.object(core, "JWT_ISSUER_URL", ISSUER), \
             patch.object(core, "JWT_AUDIENCE", AUDIENCE), \
             patch.object(core, "get_external_keys", side_effect=RuntimeError("connect timeout")):
            res = _auth(_evt(auth=f"Bearer {_token()}"))
        assert res["authorized"] is False
        assert res["failure"] == FORBID
        assert res["reason"] == "Token verification unavailable"

    def test_external_jwks_outage_on_the_kid_refetch_is_forbidden(self):
        calls = []

        def _keys(issuer, force_refresh=False):
            calls.append(force_refresh)
            if force_refresh:
                raise RuntimeError("connect timeout")
            return [JWK]

        with patch.object(core, "AUTH_MODE", "external"), \
             patch.object(core, "JWT_ISSUER_URL", ISSUER), \
             patch.object(core, "JWT_AUDIENCE", AUDIENCE), \
             patch.object(core, "get_external_keys", side_effect=_keys):
            res = _auth(_evt(auth=f"Bearer {_token(kid='rotated-kid')}"))
        assert calls == [False, True]
        assert res["failure"] == FORBID

    def test_cognito_jwks_outage_is_forbidden(self):
        class _Header:
            protected = {"kid": KID}

        with patch.object(core, "AUTH_MODE", "cognito"), \
             patch.object(core, "USER_POOL_ID", "pool-1"), \
             patch.object(core, "APP_CLIENT_ID", "app-client"), \
             patch.object(core, "COGNITO_BASE_URL", "https://cognito-idp.test"), \
             patch.object(core.joserfc_jws, "extract_compact", return_value=_Header()), \
             patch.object(core, "get_cognito_keys", side_effect=OSError("network unreachable")):
            res = _auth(_evt())
        assert res["failure"] == FORBID

    def test_malformed_token_does_not_reach_the_key_fetch(self):
        # Control for the outage case: a malformed token is rejected before any key-set
        # fetch, so an outage cannot hide behind it and it stays a 401.
        with patch.object(core, "AUTH_MODE", "cognito"), \
             patch.object(core, "USER_POOL_ID", "pool-1"), \
             patch.object(core, "APP_CLIENT_ID", "app-client"), \
             patch.object(core, "COGNITO_BASE_URL", "https://cognito-idp.test"), \
             patch.object(core, "get_cognito_keys", side_effect=OSError("unreachable")) as keys:
            res = _auth(_evt(auth="Bearer garbage"))
        assert res["failure"] == UNAUTH
        keys.assert_not_called()

    @pytest.mark.parametrize("missing", ["USER_POOL_ID", "APP_CLIENT_ID", "COGNITO_BASE_URL"])
    def test_missing_cognito_configuration_is_forbidden(self, missing):
        settings = {"USER_POOL_ID": "pool-1", "APP_CLIENT_ID": "app-client",
                    "COGNITO_BASE_URL": "https://cognito-idp.test"}
        settings[missing] = None
        with patch.object(core, "AUTH_MODE", "cognito"), \
             patch.object(core, "USER_POOL_ID", settings["USER_POOL_ID"]), \
             patch.object(core, "APP_CLIENT_ID", settings["APP_CLIENT_ID"]), \
             patch.object(core, "COGNITO_BASE_URL", settings["COGNITO_BASE_URL"]):
            res = _auth(_evt())
        assert res["failure"] == FORBID

    @pytest.mark.parametrize("missing", ["JWT_ISSUER_URL", "JWT_AUDIENCE"])
    def test_missing_external_configuration_is_forbidden(self, missing):
        settings = {"JWT_ISSUER_URL": ISSUER, "JWT_AUDIENCE": AUDIENCE}
        settings[missing] = None
        with patch.object(core, "AUTH_MODE", "external"), \
             patch.object(core, "JWT_ISSUER_URL", settings["JWT_ISSUER_URL"]), \
             patch.object(core, "JWT_AUDIENCE", settings["JWT_AUDIENCE"]):
            res = _auth(_evt(auth=f"Bearer {_token()}"))
        assert res["failure"] == FORBID

    @pytest.mark.parametrize("mode", ["", "saml", "COGNITO-ish"])
    def test_invalid_auth_mode_is_forbidden(self, mode):
        with patch.object(core, "AUTH_MODE", mode):
            res = _auth(_evt())
        assert res["failure"] == FORBID

    def test_blocked_ip_is_forbidden_even_without_a_header(self):
        with patch.object(core, "ALLOWED_IP_RANGES", [["203.0.113.0", "203.0.113.255"]]):
            res = _auth(_evt(auth=None, source_ip="198.51.100.9"))
        assert res["failure"] == FORBID
        assert res["reason"] == "IP address not authorized"


class _RolesTable:
    def __init__(self, items):
        self._items = items

    def query(self, **kwargs):
        return {"Items": self._items}


def _record(**overrides):
    rec = {"apiKeyId": "k1", "isActive": "true", "userId": "svc-user",
           "expiresAt": "2999-12-31T23:59:59Z"}
    rec.update(overrides)
    return rec


def _api_key_auth(record=None, roles=("admin",), lookup_error=False, raw="vams_abc123"):
    lookup = {"side_effect": core.ApiKeyLookupUnavailableError("down")} if lookup_error \
        else {"return_value": record}
    # The JWT fallback for an unknown key runs in cognito mode with real config and a real
    # (rejected) parse, not a patched verifier.
    with patch.object(core, "AUTH_MODE", "cognito"), \
         patch.object(core, "USER_POOL_ID", "pool-1"), \
         patch.object(core, "APP_CLIENT_ID", "app-client"), \
         patch.object(core, "COGNITO_BASE_URL", "https://cognito-idp.test"), \
         patch.object(core, "_get_user_roles_table",
                      return_value=_RolesTable([{"roleName": r} for r in roles])), \
         patch.object(core, "_lookup_api_key_by_hash", **lookup), \
         patch.object(core, "_lookup_user_roles", return_value=list(roles)):
        return _auth(_evt(auth=raw))


@pytest.mark.unit
class TestApiKeyKinds:
    def test_valid_key_authorizes(self):
        res = _api_key_auth(_record())
        assert res["authorized"] is True

    @pytest.mark.parametrize("raw", ["vams_abc123", "Bearer vams_abc123"])
    def test_unknown_key_is_unauthenticated(self, raw):
        res = _api_key_auth(None, raw=raw)
        assert res["failure"] == UNAUTH

    @pytest.mark.parametrize("record", [
        _record(isActive="false"),
        _record(expiresAt="2020-01-01T00:00:00Z"),
        _record(expiresAt="not-a-date"),
        _record(userId=""),
    ], ids=["disabled", "expired", "unreadable-expiry", "no-user-id"])
    def test_unusable_key_is_unauthenticated_like_an_unknown_one(self, record):
        res = _api_key_auth(record)
        unknown = _api_key_auth(None)
        assert res["failure"] == UNAUTH
        # No existence leak: the client-visible outcome is the same as for a key that
        # does not exist (the reason is internal only).
        assert res["failure"] == unknown["failure"]
        assert res["authorized"] == unknown["authorized"]

    def test_key_whose_user_has_no_roles_is_forbidden(self):
        res = _api_key_auth(_record(), roles=())
        assert res["failure"] == FORBID

    def test_api_key_table_outage_is_forbidden(self):
        res = _api_key_auth(lookup_error=True)
        assert res["failure"] == FORBID

    def test_user_roles_table_unavailable_is_forbidden(self):
        with patch.object(core, "_get_user_roles_table", return_value=None):
            res = core.verify_api_key("vams_abc123")
        assert res["denied"] is True
        assert res["failure"] == FORBID

    def test_unexpected_error_is_forbidden(self):
        with patch.object(core, "_get_user_roles_table", return_value=_RolesTable([])), \
             patch.object(core, "_lookup_api_key_by_hash", side_effect=KeyError("boom")):
            res = core.verify_api_key("vams_abc123")
        assert res["denied"] is True
        assert res["failure"] == FORBID


@pytest.mark.unit
class TestApiKeyLookupOutage:
    """_lookup_api_key_by_hash tells "not found" (None) apart from "could not look"."""

    def setup_method(self):
        core._api_key_cache.clear()

    def teardown_method(self):
        core._api_key_cache.clear()

    def test_missing_table_raises(self):
        with patch.object(core, "_get_api_key_table", return_value=None):
            with pytest.raises(core.ApiKeyLookupUnavailableError):
                core._lookup_api_key_by_hash("h1")

    def test_query_error_without_a_cached_entry_raises(self):
        class _Broken:
            def query(self, **kwargs):
                raise RuntimeError("ProvisionedThroughputExceededException")

        with patch.object(core, "_get_api_key_table", return_value=_Broken()):
            with pytest.raises(core.ApiKeyLookupUnavailableError):
                core._lookup_api_key_by_hash("h1")

    def test_query_error_with_a_cached_entry_serves_it(self):
        record = _record()
        core._api_key_cache["h1"] = {"record": record, "expiry": 0}

        class _Broken:
            def query(self, **kwargs):
                raise RuntimeError("throttled")

        with patch.object(core, "_get_api_key_table", return_value=_Broken()):
            assert core._lookup_api_key_by_hash("h1") == record

    def test_not_found_still_returns_none(self):
        with patch.object(core, "_get_api_key_table", return_value=_RolesTable([])):
            assert core._lookup_api_key_by_hash("h2") is None


@pytest.mark.unit
def test_failure_kind_strings_are_stable():
    # The REST handler compares against these values; a rename would silently turn every
    # 401 back into a Deny.
    assert core.FAILURE_UNAUTHENTICATED == "unauthenticated"
    assert core.FAILURE_FORBIDDEN == "forbidden"
    assert json.dumps(core._denied("r", UNAUTH)) == json.dumps(
        {"authorized": False, "context": None, "reason": "r", "failure": UNAUTH})
