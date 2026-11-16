"""The remote server's external-IdP verifier and the VAMS API authorizer give the same answer.

``parity/external_token_cases.json`` holds token claim sets with the two claim settings
(``lambdaAuthorizorJWTAudienceClaims``, ``lambdaAuthorizorJWTUserIdClaim``) and the user each must
resolve to, or None for a refusal. This test runs every case through ``TokenVerifier``; the backend test
``backend/tests/common/auth/test_authorizerCore_remote_mcp_parity.py`` runs the same file through
``verify_external_jwt``. Both must match the file, so the two cannot drift apart unnoticed.

The cases name only the remote clients' audiences: the authorizer also accepts the web application's own
audience, which the remote server refuses by design, so no case uses it.
"""

import json
import os
import time

import jwt as pyjwt
import pytest

from vams_mcp.remote_auth import RemoteAuthSettings, TokenVerifier

from .remote_support import SigningKey

_FILE = os.path.join(os.path.dirname(__file__), "parity", "external_token_cases.json")
with open(_FILE, encoding="utf-8") as _handle:
    _DOCUMENT = json.load(_handle)

_KEY = SigningKey("parity-kid")


def _verifier(case):
    data = {
        "version": 1,
        "apiUrl": "https://api.example.com/api",
        "resourceUrl": "https://mcp.example.com/mcp",
        "provider": "external",
        "issuer": _DOCUMENT["issuer"],
        "jwksUri": "https://idp.example.com/jwks.json",
        "audiences": _DOCUMENT["audiences"],
    }
    if "audienceClaims" in case:
        data["audienceClaims"] = case["audienceClaims"]
    if "userIdClaim" in case:
        data["userIdClaim"] = case["userIdClaim"]
    verifier = TokenVerifier(RemoteAuthSettings.from_dict(data))
    verifier._key_for = lambda kid: _KEY.private.public_key()  # the key lookup is not under test
    return verifier


@pytest.mark.parametrize("case", _DOCUMENT["cases"], ids=lambda case: case["name"])
def test_the_verifier_gives_the_recorded_outcome(case):
    now = int(time.time())
    claims = {"iss": _DOCUMENT["issuer"], "iat": now, "exp": now + 600, **case["claims"]}
    token = pyjwt.encode(claims, _KEY.private, algorithm="RS256", headers={"kid": _KEY.kid})
    identity = _verifier(case).verify(token)
    assert (identity.user_id if identity else None) == case["expectUser"]
