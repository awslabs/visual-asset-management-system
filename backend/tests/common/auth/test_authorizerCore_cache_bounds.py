# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Size bounds on the authorizer's module-level caches (S2-BACKEND-171).

Both caches are keyed on a value the caller supplies. Any request whose Authorization header
starts with ``vams_`` reaches the API-key lookup, which caches the result — including the
"no such key" answer — under the SHA-256 of the presented key. A stream of distinct random
keys therefore adds an entry per request that can never be hit again, for the life of the warm
container, and the DynamoDB query still runs for each one, so the negative caching buys nothing
in that case.

The bound is what makes the growth finite. These tests assert the cap holds under a flood while
the cache still does its job for a repeated key — a cache that simply stopped caching would
satisfy the cap and none of the controls.
"""

import pytest
from unittest.mock import patch

from backend.backend.common.auth import authorizerCore as core


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
    core._api_key_cache.clear()
    core._user_roles_cache.clear()
    yield
    core._api_key_cache.clear()
    core._user_roles_cache.clear()


@pytest.mark.unit
class TestCacheStoreBound:
    def test_a_stream_of_distinct_keys_never_exceeds_the_cap(self):
        cache = type(core._api_key_cache)()
        for i in range(50):
            core._cache_store(cache, f"k{i}", {"record": None, "expiry": 1e12}, 10)
            assert len(cache) <= 10

    def test_the_newest_entry_is_always_the_one_kept(self):
        cache = type(core._api_key_cache)()
        for i in range(50):
            core._cache_store(cache, f"k{i}", {"record": i, "expiry": 1e12}, 10)
        assert cache["k49"]["record"] == 49
        # Eviction is oldest-first, so the earliest keys are the ones gone.
        assert "k0" not in cache

    def test_expired_entries_are_dropped_before_live_ones(self):
        cache = type(core._api_key_cache)()
        for i in range(9):
            core._cache_store(cache, f"stale{i}", {"record": i, "expiry": 1}, 10)
        core._cache_store(cache, "live", {"record": "live", "expiry": 1e12}, 10)

        core._cache_store(cache, "new", {"record": "new", "expiry": 1e12}, 10)

        assert len(cache) <= 10
        assert cache["live"]["record"] == "live"
        assert cache["new"]["record"] == "new"

    def test_reinserting_a_key_does_not_grow_the_cache(self):
        cache = type(core._api_key_cache)()
        for _ in range(50):
            core._cache_store(cache, "same", {"record": None, "expiry": 1e12}, 10)
        assert len(cache) == 1


@pytest.mark.unit
class TestApiKeyCacheBound:
    def test_a_flood_of_distinct_hashes_stays_within_the_cap(self):
        table = _CountingTable([])
        flood = core.API_KEY_CACHE_MAX_ENTRIES * 3

        with patch.object(core, "_get_api_key_table", return_value=table):
            for i in range(flood):
                assert core._lookup_api_key_by_hash(f"hash-{i}") is None

        # Every unknown key really was a fresh lookup: caching the not-found answer buys
        # nothing against a stream of distinct keys, which is what the entries cost memory for.
        assert table.query_count >= flood
        assert len(core._api_key_cache) <= core.API_KEY_CACHE_MAX_ENTRIES

    def test_the_cache_still_serves_a_repeated_key(self):
        """Control for the cap: bounding the cache must not disable it.

        This is the case the negative caching was written for — a client retrying one revoked
        key — and it is the only case in which caching a not-found answer saves a query.
        """
        table = _CountingTable([])

        with patch.object(core, "_get_api_key_table", return_value=table):
            core._lookup_api_key_by_hash("repeated-hash")
            core._lookup_api_key_by_hash("repeated-hash")

        assert table.query_count == 1

    def test_a_found_record_is_still_returned_after_a_flood(self):
        """Second control: the cap does not interfere with a real key's lookup."""
        record = {"apiKeyId": "k1", "userId": "u1", "isActive": "true"}
        empty = _CountingTable([])
        found = _CountingTable([record])

        with patch.object(core, "_get_api_key_table", return_value=empty):
            for i in range(core.API_KEY_CACHE_MAX_ENTRIES * 2):
                core._lookup_api_key_by_hash(f"hash-{i}")

        with patch.object(core, "_get_api_key_table", return_value=found):
            assert core._lookup_api_key_by_hash("real-hash") == record
        assert len(core._api_key_cache) <= core.API_KEY_CACHE_MAX_ENTRIES


@pytest.mark.unit
class TestUserRolesCacheBound:
    def test_many_distinct_users_stay_within_the_cap(self):
        table = _CountingTable([{"roleName": "admin"}])

        with patch.object(core, "_get_user_roles_table", return_value=table):
            for i in range(core.USER_ROLES_CACHE_MAX_ENTRIES * 2):
                assert core._lookup_user_roles(f"user-{i}") == ["admin"]

        assert len(core._user_roles_cache) <= core.USER_ROLES_CACHE_MAX_ENTRIES

    def test_roles_are_still_cached_per_user(self):
        """Control: the bound must not turn every request into a table read."""
        table = _CountingTable([{"roleName": "admin"}])

        with patch.object(core, "_get_user_roles_table", return_value=table):
            core._lookup_user_roles("u1")
            core._lookup_user_roles("u1")

        assert table.query_count == 1
