# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""JWKS caching and fetching in the authorizer (S2-BACKEND-072, S2-BACKEND-123).

Two properties of the key cache decide what an issuer signing-key rotation costs:

* A ``kid`` the cached key set does not carry is what a rotation looks like from inside a warm
  container. Returning "key not found" leaves every validly signed new-kid token denied until
  the entry's own hour-long TTL lapses, so the miss forces one refetch — rate limited, because
  the kid is caller-supplied and must not become a way to drive JWKS traffic.
* Each entry carries its own expiry. A single module-wide expiry scalar is rewritten by a fetch
  under any cache key, so refetching one issuer's keys extends the freshness window of a
  different, already stale entry and serves it past its own TTL.

Separately, the fetch itself is bounded: ``urlopen`` with no ``timeout`` inherits the
process-wide default socket timeout, which is ``None``, so a black-holed endpoint blocks the
authorizer for its whole Lambda timeout instead of failing fast.

Every "it refetched" assertion is paired with a control that the cache is still used on the
ordinary path, so a change that simply stopped caching would not pass.
"""

import json
import time
import pytest
from unittest.mock import MagicMock, patch

from backend.backend.common.auth import authorizerCore as core

OLD_KEY = {"kid": "old-kid", "kty": "RSA", "n": "abc", "e": "AQAB"}
NEW_KEY = {"kid": "new-kid", "kty": "RSA", "n": "def", "e": "AQAB"}

REGION = "us-east-1"
POOL = "us-east-1_pool"
COGNITO_CACHE_KEY = f"cognito:{REGION}:{POOL}"
ISSUER = "https://idp.example.com"


class _FakeHttpResponse:
    """urlopen's context-manager response."""

    def __init__(self, payload, code=200):
        self._payload = json.dumps(payload).encode("utf-8")
        self._code = code

    def getcode(self):
        return self._code

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture(autouse=True)
def _clear_jwks_caches():
    core.keys_cache.clear()
    core.jwks_uri_cache.clear()
    yield
    core.keys_cache.clear()
    core.jwks_uri_cache.clear()


def _seed(cache_key, keys, age_seconds=0.0):
    """Cache a key set as though it had been fetched age_seconds ago."""
    core._store_jwks_keys(cache_key, keys, time.time() - age_seconds)


def _urlopen_serving(keys):
    """A urlopen spy that serves a JWKS document containing keys."""
    return MagicMock(return_value=_FakeHttpResponse({"keys": keys}))


@pytest.mark.unit
class TestCognitoKeyRefetchOnKidMiss:
    def test_force_refresh_bypasses_a_still_fresh_entry(self):
        _seed(COGNITO_CACHE_KEY, [OLD_KEY],
              age_seconds=core.JWKS_MIN_REFETCH_INTERVAL_SECONDS + 1)
        urlopen = _urlopen_serving([NEW_KEY])

        with patch.object(core, "COGNITO_BASE_URL", "https://cognito-idp.test"), \
             patch.object(core.urllib.request, "urlopen", urlopen):
            keys = core.get_cognito_keys(REGION, POOL, force_refresh=True)

        assert keys == [NEW_KEY]
        assert core.keys_cache[COGNITO_CACHE_KEY]["keys"] == [NEW_KEY]

    def test_the_ordinary_path_still_serves_the_cached_entry(self):
        """Control: the refetch is reached only through the miss path."""
        _seed(COGNITO_CACHE_KEY, [OLD_KEY])
        urlopen = _urlopen_serving([NEW_KEY])

        with patch.object(core, "COGNITO_BASE_URL", "https://cognito-idp.test"), \
             patch.object(core.urllib.request, "urlopen", urlopen):
            keys = core.get_cognito_keys(REGION, POOL)

        assert keys == [OLD_KEY]
        urlopen.assert_not_called()

    def test_a_refetch_is_rate_limited_while_the_entry_is_young(self):
        _seed(COGNITO_CACHE_KEY, [OLD_KEY], age_seconds=0)
        urlopen = _urlopen_serving([NEW_KEY])

        with patch.object(core, "COGNITO_BASE_URL", "https://cognito-idp.test"), \
             patch.object(core.urllib.request, "urlopen", urlopen):
            keys = core.get_cognito_keys(REGION, POOL, force_refresh=True)

        # An unknown kid cannot drive one JWKS request per request.
        assert keys == [OLD_KEY]
        urlopen.assert_not_called()

    def test_an_expired_entry_is_refetched_without_being_forced(self):
        _seed(COGNITO_CACHE_KEY, [OLD_KEY])
        core.keys_cache[COGNITO_CACHE_KEY]["expiry"] = 0
        urlopen = _urlopen_serving([NEW_KEY])

        with patch.object(core, "COGNITO_BASE_URL", "https://cognito-idp.test"), \
             patch.object(core.urllib.request, "urlopen", urlopen):
            assert core.get_cognito_keys(REGION, POOL) == [NEW_KEY]


def _jws_header(kid):
    token_obj = MagicMock()
    token_obj.protected = {"kid": kid}
    return token_obj


def _decoded_claims():
    decoded = MagicMock()
    decoded.claims = {
        "exp": time.time() + 3600,
        "aud": "app-client",
        "iss": "https://cognito-idp.test/" + POOL,
        "token_use": "id",
        "sub": "u1",
    }
    return decoded


def _verify_with_keys(kid, keys_by_force):
    """Run verify_cognito_jwt with the key set chosen by the force_refresh flag."""
    forced = []

    def _get_keys(region, user_pool_id, force_refresh=False):
        forced.append(force_refresh)
        return keys_by_force[force_refresh]

    with patch.object(core, "USER_POOL_ID", POOL), \
         patch.object(core, "APP_CLIENT_ID", "app-client"), \
         patch.object(core, "COGNITO_BASE_URL", "https://cognito-idp.test"), \
         patch.object(core.joserfc_jws, "extract_compact", return_value=_jws_header(kid)), \
         patch.object(core, "get_cognito_keys", side_effect=_get_keys), \
         patch.object(core.joserfc_jwk, "import_key", return_value="public-key"), \
         patch.object(core.joserfc_jwt, "decode", return_value=_decoded_claims()):
        claims = core.verify_cognito_jwt("header.payload.signature")
    return claims, forced


@pytest.mark.unit
class TestVerifyCognitoJwtWiring:
    """The refetch has to be reached from the verification path, not just exist."""

    def test_a_rotated_kid_is_accepted_after_the_refetch(self):
        claims, forced = _verify_with_keys(
            "new-kid", {False: [OLD_KEY], True: [OLD_KEY, NEW_KEY]})

        assert claims["sub"] == "u1"
        assert True in forced

    def test_a_kid_present_in_the_cached_set_needs_no_refetch(self):
        claims, forced = _verify_with_keys("old-kid", {False: [OLD_KEY], True: [NEW_KEY]})

        assert claims["sub"] == "u1"
        assert not any(forced)

    def test_a_kid_missing_from_both_reads_is_denied(self):
        claims, forced = _verify_with_keys("ghost-kid", {False: [OLD_KEY], True: [OLD_KEY]})

        assert claims is None
        assert True in forced


@pytest.mark.unit
class TestExternalKeyRefetchOnKidMiss:
    def _with_issuer(self):
        """Pre-resolve the JWKS URI so these tests exercise the key cache, not discovery."""
        core.jwks_uri_cache[ISSUER] = {"jwks_uri": f"{ISSUER}/jwks",
                                       "expiry": time.time() + 3600}

    def test_force_refresh_bypasses_a_still_fresh_entry(self):
        self._with_issuer()
        cache_key = f"external_jwks:{ISSUER}/jwks"
        _seed(cache_key, [OLD_KEY], age_seconds=core.JWKS_MIN_REFETCH_INTERVAL_SECONDS + 1)

        response = MagicMock()
        response.json.return_value = {"keys": [NEW_KEY]}
        with patch.object(core.requests, "get", return_value=response):
            keys = core.get_external_keys(ISSUER, force_refresh=True)

        assert keys == [NEW_KEY]

    def test_the_ordinary_path_still_serves_the_cached_entry(self):
        self._with_issuer()
        cache_key = f"external_jwks:{ISSUER}/jwks"
        _seed(cache_key, [OLD_KEY])

        get = MagicMock()
        with patch.object(core.requests, "get", get):
            assert core.get_external_keys(ISSUER) == [OLD_KEY]
        get.assert_not_called()

    def test_signing_key_lookup_refetches_on_a_kid_miss(self):
        forced = []

        def _get_keys(issuer_url, force_refresh=False):
            forced.append(force_refresh)
            return [OLD_KEY, NEW_KEY] if force_refresh else [OLD_KEY]

        with patch.object(core, "get_external_keys", side_effect=_get_keys), \
             patch.object(core.pyjwt, "get_unverified_header",
                          return_value={"kid": "new-kid"}), \
             patch.object(core, "construct_public_key_from_jwk", return_value="pem"):
            assert core.get_signing_key_for_external_token("t.t.t", ISSUER) == "pem"

        assert True in forced

    def test_signing_key_lookup_does_not_refetch_for_a_known_kid(self):
        forced = []

        def _get_keys(issuer_url, force_refresh=False):
            forced.append(force_refresh)
            return [OLD_KEY]

        with patch.object(core, "get_external_keys", side_effect=_get_keys), \
             patch.object(core.pyjwt, "get_unverified_header",
                          return_value={"kid": "old-kid"}), \
             patch.object(core, "construct_public_key_from_jwk", return_value="pem"):
            assert core.get_signing_key_for_external_token("t.t.t", ISSUER) == "pem"

        assert not any(forced)


@pytest.mark.unit
class TestPerEntryExpiry:
    def test_a_fetch_under_one_cache_key_does_not_refresh_another_entry(self):
        """The defect a single module-wide expiry scalar produced.

        The Cognito entry is stale. Storing a different issuer's key set used to rewrite the
        one shared expiry, which made the stale entry read as fresh and served the
        pre-rotation keys for another full hour.
        """
        _seed(COGNITO_CACHE_KEY, [OLD_KEY])
        core.keys_cache[COGNITO_CACHE_KEY]["expiry"] = 0

        _seed(f"external_jwks:{ISSUER}/jwks", [NEW_KEY])

        urlopen = _urlopen_serving([NEW_KEY])
        with patch.object(core, "COGNITO_BASE_URL", "https://cognito-idp.test"), \
             patch.object(core.urllib.request, "urlopen", urlopen):
            keys = core.get_cognito_keys(REGION, POOL)

        assert keys == [NEW_KEY]
        assert urlopen.call_count >= 1

    def test_each_entry_keeps_its_own_expiry_value(self):
        _seed(COGNITO_CACHE_KEY, [OLD_KEY])
        core.keys_cache[COGNITO_CACHE_KEY]["expiry"] = 0
        _seed(f"external_jwks:{ISSUER}/jwks", [NEW_KEY])

        now = time.time()
        assert core._cached_jwks_keys(COGNITO_CACHE_KEY, now) is None
        # Control: the fresh entry is unaffected by its neighbour being stale.
        assert core._cached_jwks_keys(f"external_jwks:{ISSUER}/jwks", now) == [NEW_KEY]


@pytest.mark.unit
class TestFetchTimeouts:
    def test_cognito_jwks_fetch_passes_a_timeout(self):
        urlopen = _urlopen_serving([NEW_KEY])

        with patch.object(core, "COGNITO_BASE_URL", "https://cognito-idp.test"), \
             patch.object(core.urllib.request, "urlopen", urlopen):
            assert core.get_cognito_keys(REGION, POOL) == [NEW_KEY]

        timeout = urlopen.call_args[1].get("timeout")
        assert timeout is not None
        assert timeout == core.JWKS_FETCH_TIMEOUT_SECONDS

    def test_external_jwks_fetch_passes_a_timeout(self):
        core.jwks_uri_cache[ISSUER] = {"jwks_uri": f"{ISSUER}/jwks",
                                       "expiry": time.time() + 3600}
        response = MagicMock()
        response.json.return_value = {"keys": [NEW_KEY]}
        get = MagicMock(return_value=response)

        with patch.object(core.requests, "get", get):
            assert core.get_external_keys(ISSUER) == [NEW_KEY]

        assert get.call_args[1].get("timeout") == core.JWKS_FETCH_TIMEOUT_SECONDS

    def test_openid_discovery_passes_a_timeout(self):
        response = MagicMock()
        response.json.return_value = {"jwks_uri": f"{ISSUER}/keys"}
        get = MagicMock(return_value=response)

        with patch.object(core.requests, "get", get):
            assert core.discover_jwks_uri(ISSUER) == f"{ISSUER}/keys"

        assert get.call_args[1].get("timeout") == core.JWKS_FETCH_TIMEOUT_SECONDS

    def test_the_timeout_is_a_finite_number(self):
        # The point of the constant is that it is not None; a None timeout is the defect.
        assert isinstance(core.JWKS_FETCH_TIMEOUT_SECONDS, (int, float))
        assert core.JWKS_FETCH_TIMEOUT_SECONDS > 0
