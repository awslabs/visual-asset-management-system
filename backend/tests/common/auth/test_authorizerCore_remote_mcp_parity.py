# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The API authorizer and the remote MCP server's verifier give the same answer for an external IdP.

Runs the shared fixture file ``tools/VamsMCP/tests/parity/external_token_cases.json`` (token claims, the
two claim settings, and the user each must resolve to or None for a refusal) through
``verify_external_jwt``. ``tools/VamsMCP/tests/test_remote_auth_parity.py`` runs the same file through
the MCP server's ``TokenVerifier``. The audiences in the file are listed as additional audiences here
(the web application's own audience is the primary one), the configuration a remote MCP client's
audience uses.

Durable (root CLAUDE.md Rule 13).
"""

import base64
import json
import time
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.backend.common.auth import authorizerCore as core

_FILE = Path(__file__).resolve().parents[4] / "tools" / "VamsMCP" / "tests" / "parity" / "external_token_cases.json"
_DOCUMENT = json.loads(_FILE.read_text(encoding="utf-8"))
KID = "parity-kid"
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_b64url(value):
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


_NUMBERS = _PRIVATE_KEY.public_key().public_numbers()
JWK = {"kid": KID, "kty": "RSA", "alg": "RS256", "use": "sig",
       "n": _int_b64url(_NUMBERS.n), "e": _int_b64url(_NUMBERS.e)}


@pytest.mark.parametrize("case", _DOCUMENT["cases"], ids=lambda case: case["name"])
def test_the_authorizer_gives_the_recorded_outcome(case):
    now = int(time.time())
    claims = {"iss": _DOCUMENT["issuer"], "iat": now, "exp": now + 600, **case["claims"]}
    token = jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})
    with patch.object(core, "AUTH_MODE", "external"), \
         patch.object(core, "JWT_ISSUER_URL", _DOCUMENT["issuer"]), \
         patch.object(core, "JWT_AUDIENCE", _DOCUMENT["webAudience"]), \
         patch.object(core, "JWT_ADDITIONAL_AUDIENCES", _DOCUMENT["audiences"]), \
         patch.object(core, "JWT_AUDIENCE_CLAIMS", case.get("audienceClaims", ["aud"])), \
         patch.object(core, "JWT_USER_ID_CLAIM", case.get("userIdClaim", "")), \
         patch.object(core, "get_external_keys", return_value=[JWK]):
        verified = core.verify_external_jwt(token)
        user = core.resolve_user_id(verified) if verified else None
    assert user == case["expectUser"]
