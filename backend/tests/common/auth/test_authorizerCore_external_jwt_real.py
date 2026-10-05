# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""External-IdP token verification through real PyJWT and real RSA keys.

The other authorizer tests patch ``get_unverified_header`` or ``verify_external_jwt`` itself, so
they pass against any PyJWT that still exports the names ``authorizerCore`` imports. These tests
sign real tokens and run them through ``verify_external_jwt`` with only the JWKS fetch replaced,
so they hold the behaviour of the installed PyJWT on the path VAMS uses:

* a compliant RS256 token from the configured issuer and audience verifies;
* a wrong audience, a wrong issuer, an expired token and an HS256 token keyed with the issuer's
  public key (algorithm confusion) are each denied;
* a token whose signature segment carries characters outside the base64url alphabet is denied
  rather than decoded leniently;
* a token whose signature segment carries correct ``=`` padding still verifies, so an IdP that
  pads its segments is not locked out.
"""

import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from unittest.mock import patch

from backend.backend.common.auth import authorizerCore as core

ISSUER = "https://idp.example.com"
AUDIENCE = "vams-client"
KID = "test-kid"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_PEM = _PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_b64url(value):
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


_NUMBERS = _PRIVATE_KEY.public_key().public_numbers()
JWK = {"kid": KID, "kty": "RSA", "alg": "RS256", "use": "sig",
       "n": _int_b64url(_NUMBERS.n), "e": _int_b64url(_NUMBERS.e)}


def _claims(**overrides):
    now = int(time.time())
    claims = {"sub": "user-1", "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 600}
    claims.update(overrides)
    return claims


def _rs256(**overrides):
    return jwt.encode(_claims(**overrides), _PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})


@pytest.fixture(autouse=True)
def _external_idp():
    with patch.object(core, "JWT_ISSUER_URL", ISSUER), \
         patch.object(core, "JWT_AUDIENCE", AUDIENCE), \
         patch.object(core, "get_external_keys", return_value=[JWK]):
        yield


@pytest.mark.unit
class TestExternalJwtVerification:
    def test_a_compliant_rs256_token_verifies(self):
        claims = core.verify_external_jwt(_rs256())

        assert claims is not None
        assert claims["sub"] == "user-1"

    @pytest.mark.parametrize("overrides", [
        {"aud": "another-client"},
        {"iss": "https://other-idp.example.com"},
        {"exp": int(time.time()) - 60},
    ], ids=["wrong-audience", "wrong-issuer", "expired"])
    def test_a_token_failing_a_registered_claim_check_is_denied(self, overrides):
        assert core.verify_external_jwt(_rs256(**overrides)) is None

    def test_an_hs256_token_keyed_with_the_issuer_public_key_is_denied(self):
        header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
        payload = _b64url(json.dumps(_claims()).encode())
        signing_input = f"{header}.{payload}".encode("ascii")
        signature = _b64url(hmac.new(_PUBLIC_PEM, signing_input, hashlib.sha256).digest())

        assert core.verify_external_jwt(f"{header}.{payload}.{signature}") is None

    def test_a_signature_segment_with_non_alphabet_characters_is_denied(self):
        assert core.verify_external_jwt(_rs256() + "!!!!") is None

    def test_a_correctly_padded_signature_segment_verifies(self):
        token = _rs256()
        signature = token.rsplit(".", 1)[1]
        padded = token + "=" * (-len(signature) % 4)

        # A 2048-bit RS256 signature is 256 bytes, so its unpadded segment needs two '='.
        assert padded != token
        assert core.verify_external_jwt(padded) is not None
