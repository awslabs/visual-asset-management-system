# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Composed key-rotation and cache-bound behaviour in the authorizer.

``test_authorizerCore_jwks.py`` and ``test_authorizerCore_cache_bounds.py`` assert each half of
these properties in isolation: the refetch-on-``kid``-miss tests patch ``get_cognito_keys`` /
``get_external_keys`` out, so they never reach the rate limiter, and the rate-limit tests call
those functions directly, so they never run through a verification. The halves can therefore
both pass while the composition does not — a refetch wired past the rate limit turns every
unknown ``kid`` into a JWKS request, and a rate limit that never lets go turns a real rotation
back into an hour of denials.

Each property below is asserted through the real cache, over a stream rather than a single call:

* An unknown ``kid`` arriving repeatedly costs at most one JWKS fetch per key set per interval,
  and that fetch still carries a timeout. The interval is anchored by every fetch the module
  makes, the ordinary cache-miss one included -- seeding an entry cannot show that.
* A rotated ``kid`` resolves through the real key cache, which then carries the new key.
* A flood of entries that have already expired does not evict a live one from either
  module-level cache, and a flood of entries that have NOT expired -- where the cap can only be
  held by dropping something live -- still leaves a valid identity authorized on its next
  lookup.

Every "it was bounded" assertion is paired with the control that the ordinary path still works:
a known ``kid`` verifies with no fetch at all, an entry older than the interval does refetch, and
a live cache entry is still served after the flood. Without those, refusing to ever refetch and
refusing to ever cache would both pass.
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
COGNITO_BASE = "https://cognito-idp.test"
ISSUER = "https://idp.example.com"
EXTERNAL_CACHE_KEY = f"external_jwks:{ISSUER}/jwks"

# Old enough that a kid miss is allowed to force a refetch, young enough that the entry's own
# hour-long expiry has not lapsed -- the state a warm container is in when a rotation happens.
AGED = core.JWKS_MIN_REFETCH_INTERVAL_SECONDS + 1


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


class _CountingTable:
    """A DynamoDB table stand-in that counts reads and returns a fixed page."""

    def __init__(self, items=None):
        self._items = items if items is not None else []
        self.query_count = 0

    def query(self, **kwargs):
        self.query_count += 1
        return {"Items": self._items}


@pytest.fixture(autouse=True)
def _clear_caches():
    for cache in (core.keys_cache, core.jwks_uri_cache,
                  core._api_key_cache, core._user_roles_cache):
        cache.clear()
    yield
    for cache in (core.keys_cache, core.jwks_uri_cache,
                  core._api_key_cache, core._user_roles_cache):
        cache.clear()


def _seed(cache_key, keys, age_seconds=0.0):
    """Cache a key set as though it had been fetched age_seconds ago."""
    core._store_jwks_keys(cache_key, keys, time.time() - age_seconds)


def _resolved_issuer():
    """Pre-resolve the JWKS URI so the external tests exercise the key cache, not discovery."""
    core.jwks_uri_cache[ISSUER] = {"jwks_uri": f"{ISSUER}/jwks",
                                   "expiry": time.time() + 3600}


def _jws_header(kid):
    token_obj = MagicMock()
    token_obj.protected = {"kid": kid}
    return token_obj


def _decoded_claims():
    decoded = MagicMock()
    decoded.claims = {
        "exp": time.time() + 3600,
        "aud": "app-client",
        "iss": f"{COGNITO_BASE}/{POOL}",
        "token_use": "id",
        "sub": "u1",
    }
    return decoded


def _verify_stream(kid, served_keys, attempts):
    """Verify a token carrying kid `attempts` times against the real Cognito key cache.

    Returns the results and the urlopen spy, so a test can assert both the outcome and how
    many JWKS requests the stream cost.
    """
    urlopen = MagicMock(return_value=_FakeHttpResponse({"keys": served_keys}))

    with patch.object(core, "USER_POOL_ID", POOL), \
         patch.object(core, "APP_CLIENT_ID", "app-client"), \
         patch.object(core, "COGNITO_BASE_URL", COGNITO_BASE), \
         patch.object(core.joserfc_jws, "extract_compact", return_value=_jws_header(kid)), \
         patch.object(core.joserfc_jwk, "import_key", return_value="public-key"), \
         patch.object(core.joserfc_jwt, "decode", return_value=_decoded_claims()), \
         patch.object(core.urllib.request, "urlopen", urlopen):
        results = [core.verify_cognito_jwt("header.payload.signature")
                   for _ in range(attempts)]

    return results, urlopen


@pytest.mark.unit
class TestUnknownKidCannotDriveJwksTraffic:
    """The kid is caller-supplied, so the refetch it triggers has to stay rate limited."""

    def test_a_stream_of_unknown_kids_costs_one_fetch(self):
        _seed(COGNITO_CACHE_KEY, [OLD_KEY], age_seconds=AGED)

        results, urlopen = _verify_stream("ghost-kid", [OLD_KEY], attempts=5)

        assert all(r is None for r in results)
        assert urlopen.call_count == 1

    def test_the_forced_refetch_still_carries_the_fetch_timeout(self):
        """The timeout has to hold on the refetch path, not only on the first fetch."""
        _seed(COGNITO_CACHE_KEY, [OLD_KEY], age_seconds=AGED)

        _, urlopen = _verify_stream("ghost-kid", [OLD_KEY], attempts=1)

        assert urlopen.call_args[1].get("timeout") == core.JWKS_FETCH_TIMEOUT_SECONDS

    def test_a_known_kid_verifies_without_any_fetch(self):
        """Control: the ordinary path is still served entirely from the cache."""
        _seed(COGNITO_CACHE_KEY, [OLD_KEY], age_seconds=AGED)

        results, urlopen = _verify_stream("old-kid", [OLD_KEY], attempts=5)

        assert [r["sub"] for r in results] == ["u1"] * 5
        urlopen.assert_not_called()

    def test_a_rotated_kid_is_accepted_on_the_refetch(self):
        """Control for the rate limit: it bounds the refetch, it does not prevent it."""
        _seed(COGNITO_CACHE_KEY, [OLD_KEY], age_seconds=AGED)

        results, urlopen = _verify_stream("new-kid", [OLD_KEY, NEW_KEY], attempts=1)

        assert results[0]["sub"] == "u1"
        assert urlopen.call_count == 1
        assert core._key_for_kid(core.keys_cache[COGNITO_CACHE_KEY]["keys"], "new-kid")

    def test_an_ordinary_fetch_starts_the_rate_limit_window(self):
        """The interval is anchored on every fetch, the ordinary one included.

        Every other test here seeds the entry with an age of its own, so a fetch path that
        recorded no fetch time would satisfy all of them while leaving each unknown kid free
        to force a request. The sequence below never seeds: a cold container fetches on the
        first request it serves, and a kid the resulting set does not carry -- which is what
        a rotation looks like seconds later, and is indistinguishable from a random kid at
        that point -- has to be denied without a second fetch.
        """
        results, urlopen = _verify_stream("old-kid", [OLD_KEY], attempts=1)
        assert results[0]["sub"] == "u1"
        assert urlopen.call_count == 1

        results, urlopen = _verify_stream("new-kid", [OLD_KEY, NEW_KEY], attempts=3)
        assert all(r is None for r in results)
        urlopen.assert_not_called()

        # Control: the window releases rather than latching, so the rotation is still picked
        # up -- the whole point of the bound is that it delays the refetch, not that it
        # cancels it.
        core.keys_cache[COGNITO_CACHE_KEY]["fetched"] = time.time() - AGED
        results, urlopen = _verify_stream("new-kid", [OLD_KEY, NEW_KEY], attempts=1)
        assert results[0]["sub"] == "u1"
        assert urlopen.call_count == 1


@pytest.mark.unit
class TestExternalRotationThroughTheRealKeyCache:
    def test_a_rotated_kid_resolves_and_updates_the_cache(self):
        _resolved_issuer()
        _seed(EXTERNAL_CACHE_KEY, [OLD_KEY], age_seconds=AGED)
        response = MagicMock()
        response.json.return_value = {"keys": [OLD_KEY, NEW_KEY]}
        get = MagicMock(return_value=response)

        with patch.object(core.requests, "get", get), \
             patch.object(core.pyjwt, "get_unverified_header",
                          return_value={"kid": "new-kid"}), \
             patch.object(core, "construct_public_key_from_jwk", return_value="pem"):
            assert core.get_signing_key_for_external_token("t.t.t", ISSUER) == "pem"

        assert get.call_count == 1
        assert get.call_args[1].get("timeout") == core.JWKS_FETCH_TIMEOUT_SECONDS
        assert core._key_for_kid(core.keys_cache[EXTERNAL_CACHE_KEY]["keys"], "new-kid")

    def test_a_young_entry_is_served_instead_of_refetched(self):
        _resolved_issuer()
        _seed(EXTERNAL_CACHE_KEY, [OLD_KEY], age_seconds=0)
        get = MagicMock()

        with patch.object(core.requests, "get", get):
            assert core.get_external_keys(ISSUER, force_refresh=True) == [OLD_KEY]

        get.assert_not_called()

    def test_an_aged_entry_is_refetched_when_forced(self):
        """Control for the test above: the interval expires rather than latching."""
        _resolved_issuer()
        _seed(EXTERNAL_CACHE_KEY, [OLD_KEY], age_seconds=AGED)
        response = MagicMock()
        response.json.return_value = {"keys": [NEW_KEY]}
        get = MagicMock(return_value=response)

        with patch.object(core.requests, "get", get):
            assert core.get_external_keys(ISSUER, force_refresh=True) == [NEW_KEY]

        assert get.call_count == 1


@pytest.mark.unit
class TestExpiredEntriesAreSweptBeforeLiveOnes:
    """The sweep asserted here runs through the lookups, not on a synthetic cache.

    A flood of distinct caller-supplied keys is the case the bound exists for, and every
    entry it leaves behind is expired within seconds. Dropping those before a live entry is
    what keeps a legitimate identity's entry from being flushed out by the flood.
    """

    def test_a_live_api_key_entry_survives_a_flood_of_expired_entries(self):
        record = {"apiKeyId": "k1", "userId": "u1", "isActive": "true"}
        found = _CountingTable([record])

        with patch.object(core, "_get_api_key_table", return_value=found), \
             patch.object(core, "API_KEY_CACHE_TTL", 3600):
            assert core._lookup_api_key_by_hash("real-hash") == record

        with patch.object(core, "_get_api_key_table", return_value=_CountingTable([])), \
             patch.object(core, "API_KEY_CACHE_TTL", -1):
            for i in range(core.API_KEY_CACHE_MAX_ENTRIES * 2):
                core._lookup_api_key_by_hash(f"junk-{i}")

        assert len(core._api_key_cache) <= core.API_KEY_CACHE_MAX_ENTRIES
        assert "real-hash" in core._api_key_cache

    def test_the_live_entry_is_still_served_from_cache_after_the_flood(self):
        """Control: surviving the flood is only useful if the entry is still a cache hit."""
        record = {"apiKeyId": "k1", "userId": "u1", "isActive": "true"}
        found = _CountingTable([record])

        with patch.object(core, "_get_api_key_table", return_value=found), \
             patch.object(core, "API_KEY_CACHE_TTL", 3600):
            core._lookup_api_key_by_hash("real-hash")

        with patch.object(core, "_get_api_key_table", return_value=_CountingTable([])), \
             patch.object(core, "API_KEY_CACHE_TTL", -1):
            for i in range(core.API_KEY_CACHE_MAX_ENTRIES * 2):
                core._lookup_api_key_by_hash(f"junk-{i}")

        with patch.object(core, "_get_api_key_table", return_value=found), \
             patch.object(core, "API_KEY_CACHE_TTL", 3600):
            assert core._lookup_api_key_by_hash("real-hash") == record

        # Still one read in total: the entry was served from the cache, not re-queried.
        assert found.query_count == 1

    def test_a_live_user_roles_entry_survives_a_flood_of_expired_entries(self):
        roles_table = _CountingTable([{"roleName": "admin"}])

        with patch.object(core, "_get_user_roles_table", return_value=roles_table), \
             patch.object(core, "USER_ROLES_CACHE_TTL", 3600):
            assert core._lookup_user_roles("real-user") == ["admin"]

        with patch.object(core, "_get_user_roles_table",
                          return_value=_CountingTable([{"roleName": "other"}])), \
             patch.object(core, "USER_ROLES_CACHE_TTL", -1):
            for i in range(core.USER_ROLES_CACHE_MAX_ENTRIES * 2):
                core._lookup_user_roles(f"junk-user-{i}")

        assert len(core._user_roles_cache) <= core.USER_ROLES_CACHE_MAX_ENTRIES
        assert "real-user" in core._user_roles_cache


@pytest.mark.unit
class TestEvictionNeverDeniesAValidIdentity:
    """The other direction of the flood: entries that have NOT expired.

    The tests above pressure the cap with entries the sweep can free. When the flood is made
    of entries that are all still within their TTL there is nothing to free, so the cap is
    held by dropping an entry that is still live -- and the entry dropped may be a legitimate
    identity's. What must hold whichever entry goes is the ANSWER: the next lookup re-reads
    the row rather than returning the not-found the flood's own entries carry. Asserting the
    answer rather than which key survived also keeps these tests valid if the eviction order
    is ever changed.
    """

    def test_an_evicted_api_key_entry_is_re_read_rather_than_denied(self):
        record = {"apiKeyId": "k1", "userId": "u1", "isActive": "true"}
        found = _CountingTable([record])

        with patch.object(core, "_get_api_key_table", return_value=found):
            assert core._lookup_api_key_by_hash("real-hash") == record

        junk = _CountingTable([])
        with patch.object(core, "_get_api_key_table", return_value=junk):
            for i in range(core.API_KEY_CACHE_MAX_ENTRIES * 2):
                core._lookup_api_key_by_hash(f"live-junk-{i}")

        # The flood really ran, and none of it was expired: a count rather than a length,
        # so the control cannot be satisfied by a sweep that happened to fire on a slow run.
        assert junk.query_count == core.API_KEY_CACHE_MAX_ENTRIES * 2
        assert len(core._api_key_cache) <= core.API_KEY_CACHE_MAX_ENTRIES

        with patch.object(core, "_get_api_key_table", return_value=found):
            assert core._lookup_api_key_by_hash("real-hash") == record

    def test_an_evicted_user_roles_entry_is_re_read_rather_than_emptied(self):
        roles_table = _CountingTable([{"roleName": "admin"}])

        with patch.object(core, "_get_user_roles_table", return_value=roles_table):
            assert core._lookup_user_roles("real-user") == ["admin"]

        junk = _CountingTable([{"roleName": "other"}])
        with patch.object(core, "_get_user_roles_table", return_value=junk):
            for i in range(core.USER_ROLES_CACHE_MAX_ENTRIES * 2):
                core._lookup_user_roles(f"live-junk-user-{i}")

        assert junk.query_count == core.USER_ROLES_CACHE_MAX_ENTRIES * 2
        assert len(core._user_roles_cache) <= core.USER_ROLES_CACHE_MAX_ENTRIES

        # An empty role list is what the API-key branch denies on, so a lost entry must come
        # back as the stored roles rather than as the empty list.
        with patch.object(core, "_get_user_roles_table", return_value=roles_table):
            assert core._lookup_user_roles("real-user") == ["admin"]
