"""The remote server's own token verification (``remote_auth.TokenVerifier``) and its configuration.

The rules follow the VAMS API authorizer: RS256 signed by the issuer's keys, ``iss`` and ``exp``
required, the token issued to one of this server's configured clients, and a user named. These tests
run the verifier directly against the fake issuer of ``tests/remote_support.py``; the HTTP gate in
front of it is covered by ``tests/test_remote_http.py``.
"""

import logging
import time

import pytest

from vams_mcp import remote_auth
from vams_mcp.config import ConfigError
from vams_mcp.remote_auth import RemoteAuthSettings, TokenVerifier, VerificationUnavailable

from .remote_support import OTHER_CLIENT_ID, REMOTE_CLIENT_ID, FakeDeployment, SigningKey


@pytest.fixture
def cognito():
    with FakeDeployment("cognito") as fake:
        yield fake


@pytest.fixture
def external():
    with FakeDeployment("external") as fake:
        yield fake


def _verifier(fake, **overrides):
    data = fake.settings_dict("https://mcp.example.com/mcp")
    data.update(overrides)
    return TokenVerifier(RemoteAuthSettings.from_dict(data))


# --- Cognito -----------------------------------------------------------------------------------------


def test_a_cognito_access_token_of_the_remote_client_verifies(cognito):
    identity = _verifier(cognito).verify(cognito.token("alice"))
    assert (identity.user_id, identity.client_id) == ("alice", REMOTE_CLIENT_ID)
    assert identity.expires_at > time.time()


def test_an_identity_never_shows_its_token(cognito):
    token = cognito.token("alice")
    identity = _verifier(cognito).verify(token)
    assert identity.token == token
    assert token not in repr(identity) and token not in str(identity)


@pytest.mark.parametrize("label, overrides", [
    ("client not configured", {"client_id": OTHER_CLIENT_ID}),
    ("machine to machine", {"user": None}),
    ("refresh token use", {"extra": {"token_use": "refresh"}}),
    ("no token_use", {"drop": ("token_use",)}),
    ("expired", {"expires_in": -1}),
    ("not yet valid", {"extra": {"nbf": int(time.time()) + 600}}),
    ("no issuer", {"drop": ("iss",)}),
    ("another issuer", {"issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Other"}),
])
def test_cognito_tokens_that_are_refused(cognito, label, overrides):
    assert _verifier(cognito).verify(cognito.token(**{"user": "alice", **overrides})) is None, label


def test_an_id_token_is_checked_against_its_aud(cognito):
    verifier = _verifier(cognito)
    assert verifier.verify(cognito.token("alice", token_use="id")).user_id == "alice"
    assert verifier.verify(cognito.token("alice", token_use="id", client_id=OTHER_CLIENT_ID)) is None


def test_another_algorithm_is_refused_before_any_key_lookup(cognito):
    import jwt as pyjwt

    hs = pyjwt.encode({"username": "alice", "exp": int(time.time()) + 60}, "secret" * 8, algorithm="HS256",
                      headers={"kid": "key-1"})
    verifier = _verifier(cognito)
    assert verifier.verify(hs) is None
    assert cognito.jwks_fetches == 0


def test_an_oversized_token_is_refused_unparsed(cognito):
    assert _verifier(cognito).verify("a" * (remote_auth.MAX_TOKEN_BYTES + 1)) is None
    assert cognito.jwks_fetches == 0


# --- External IdP ------------------------------------------------------------------------------------


def test_an_external_token_of_a_configured_audience_verifies(external):
    identity = _verifier(external).verify(external.token("alice", extra={"sub": "alice"}))
    assert (identity.user_id, identity.client_id) == ("alice", REMOTE_CLIENT_ID)


def test_an_external_token_with_several_audiences_matches_the_configured_one(external):
    token = external.token("alice", extra={"aud": ["api://other", REMOTE_CLIENT_ID]})
    assert _verifier(external).verify(token).client_id == REMOTE_CLIENT_ID


@pytest.mark.parametrize("label, extra", [
    ("Auth0 client credentials", {"gty": "client-credentials"}),
    ("Entra ID app-only", {"idtyp": "app"}),
    ("Keycloak service account", {"preferred_username": "service-account-mcp"}),
    ("identity is its own client", {"sub": "client-abc", "azp": "client-abc"}),
    ("wrong audience", {"aud": "api://other"}),
])
def test_external_machine_tokens_and_other_audiences_are_refused(external, label, extra):
    assert _verifier(external).verify(external.token("alice", extra=extra)) is None, label


def test_an_external_token_naming_no_user_is_refused(external):
    assert _verifier(external).verify(external.token(None)) is None


# --- Keys --------------------------------------------------------------------------------------------


def test_keys_are_fetched_once_and_cached(cognito):
    verifier = _verifier(cognito)
    for _ in range(5):
        assert verifier.verify(cognito.token("alice")) is not None
    assert cognito.jwks_fetches == 1


def test_a_rotated_key_is_picked_up_with_one_refetch(cognito):
    verifier = _verifier(cognito)
    assert verifier.verify(cognito.token("alice")) is not None
    rotated = SigningKey("key-2")
    cognito.keys.append(rotated)
    verifier._last_attempt -= remote_auth.JWKS_REFETCH_MIN_SECONDS + 1
    assert verifier.verify(cognito.token("alice", key=rotated)) is not None
    assert cognito.jwks_fetches == 2


def test_unknown_key_ids_cannot_drive_a_fetch_per_request(cognito):
    verifier = _verifier(cognito)
    assert verifier.verify(cognito.token("alice")) is not None
    for index in range(5):
        assert verifier.verify(cognito.token("alice", key=SigningKey(f"bogus-{index}"))) is None
    assert cognito.jwks_fetches == 1


def test_keys_that_cannot_be_read_raise_and_back_off(cognito):
    cognito.jwks_status = 503
    verifier = _verifier(cognito)
    for _ in range(3):
        with pytest.raises(VerificationUnavailable):
            verifier.verify(cognito.token("alice"))
    assert cognito.jwks_fetches == 1  # the failure backs off instead of fetching per request
    with pytest.raises(VerificationUnavailable):
        verifier.prefetch()


def test_rejections_are_logged_as_a_category_only(cognito, caplog):
    caplog.set_level(logging.DEBUG)
    token = cognito.token("alice", client_id=OTHER_CLIENT_ID)
    assert _verifier(cognito).verify(token) is None
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "rejected a bearer token: audience" in logged
    assert token not in logged and OTHER_CLIENT_ID not in logged


# --- Settings ----------------------------------------------------------------------------------------


def _settings(**overrides):
    data = {
        "version": 1, "apiUrl": "https://api.example.com/api", "resourceUrl": "https://mcp.example.com/mcp",
        "provider": "cognito", "issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_A",
        "jwksUri": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_A/.well-known/jwks.json",
        "audiences": [REMOTE_CLIENT_ID], "scopes": ["openid"],
    }
    data.update(overrides)
    return RemoteAuthSettings.from_dict(data)


def test_the_metadata_url_inserts_the_well_known_path_after_the_host():
    settings = _settings()
    assert settings.metadata_url == "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
    assert settings.protected_resource_metadata() == {
        "resource": "https://mcp.example.com/mcp",
        "authorization_servers": ["https://cognito-idp.us-east-1.amazonaws.com/us-east-1_A"],
        "bearer_methods_supported": ["header"],
        "resource_name": "VAMS",
        "scopes_supported": ["openid"],
    }


@pytest.mark.parametrize("overrides, fragment", [
    ({"version": 2}, "version"),
    ({"provider": "saml"}, "provider"),
    ({"audiences": []}, "audiences"),
    ({"audiences": [f"c{i}" for i in range(11)]}, "audiences"),
    ({"audiences": [" padded"]}, "audiences"),
    ({"resourceUrl": "https://mcp.example.com/other"}, "/mcp"),
    ({"resourceUrl": "http://mcp.example.com/mcp"}, "https"),
    ({"jwksUri": "http://keys.example.com/jwks.json"}, "https"),
    ({"issuer": "ftp://issuer"}, "https"),
    ({"apiUrl": "https://api.example.com/api?x=1"}, "query"),
    ({"scopes": ["two words"]}, "scopes"),
])
def test_invalid_settings_stop_the_server(overrides, fragment):
    with pytest.raises(ConfigError, match=fragment):
        _settings(**overrides)


def test_plain_http_is_accepted_for_loopback_only():
    settings = _settings(resourceUrl="http://127.0.0.1:8000/mcp", jwksUri="http://localhost:9/jwks.json")
    assert settings.resource_url == "http://127.0.0.1:8000/mcp"


def test_a_missing_file_names_the_setup_command(tmp_path):
    with pytest.raises(ConfigError, match="setup-remote"):
        RemoteAuthSettings.load(str(tmp_path / "absent.json"))


def test_settings_round_trip_through_the_file_format():
    settings = _settings()
    assert RemoteAuthSettings.from_dict(settings.to_dict()) == settings
