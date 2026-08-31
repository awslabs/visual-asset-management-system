# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync must never resolve an ingested object into a soft-deleted database.

Found by running `tools/smoketest/v260/suite_permission_scoping.py` against the development
deployment: an object written straight to the asset bucket produced the log line

    Creating new asset ps6790fa-bucketsync-73c9fd60 in database tsl5-dbb#deleted

`tsl5-dbb#deleted` is a soft-deleted database — VAMS marks deletion by appending `#deleted` to the
databaseId and keeping the row. `lookup_databases()` scanned the database table filtering only on
`defaultBucketId`, with nothing excluding that suffix, and `get_or_create_database_for_bucket()` falls
back to `matching_databases[0]` when the configured default is not among the results. So an ingested
file could be written into a deleted database, where it is invisible to every list and unreachable
through the API — while the sync logged success and the object sat in the bucket looking ingested.

The rest of this module already treats the suffix as meaningful (the archived-asset lookups filter on
it in both directions), so the omission was local to this one scan.

The filter lives in `lookup_databases()` rather than at the call site because every caller wants the
same thing: a database an asset can actually be created in.
"""


import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Env vars sqsBucketSync requires at import time (set before import). DEFAULT_DATABASE_ID
# is required (the module raises if missing); the others are read optionally.
os.environ.setdefault("DEFAULT_DATABASE_ID", "test-db")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE_NAME", "test-history-table")

# Absolute path to the real sqsBucketSync module file.
_SQS_BUCKET_SYNC_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing", "sqsBucketSync.py"
)

_cached_sync_db_module = None


def _load():
    
    global _cached_sync_db_module
    if _cached_sync_db_module is not None:
        return _cached_sync_db_module

    # sqsBucketSync imports create_asset/create_database submodules that the mock
    # `handlers.assets`/`handlers.databases` packages don't provide. Stub them
    # for the load only (saved/restored) so the module's top-level imports resolve.
    stub_names = ("handlers.assets.createAsset", "handlers.assets.assetCount",
                  "handlers.databases.createDatabase")
    saved = {name: sys.modules.get(name) for name in stub_names}
    create_asset_stub = types.ModuleType("handlers.assets.createAsset")
    create_asset_stub.create_asset = MagicMock()
    sys.modules["handlers.assets.createAsset"] = create_asset_stub
    asset_count_stub = types.ModuleType("handlers.assets.assetCount")
    asset_count_stub.update_asset_count = MagicMock()
    sys.modules["handlers.assets.assetCount"] = asset_count_stub
    create_db_stub = types.ModuleType("handlers.databases.createDatabase")
    create_db_stub.create_database = MagicMock()
    sys.modules["handlers.databases.createDatabase"] = create_db_stub

    # models.assetsV3 (a transitive import of sqsBucketSync) imports
    # `bucket_existing_key_pattern` from common.validators; the mock validators
    # module the root conftest injects does not define it. Add it for the load so
    # the transitive import resolves.
    validators_mod = sys.modules.get("common.validators")
    added_pattern = False
    if validators_mod is not None and not hasattr(validators_mod, "bucket_existing_key_pattern"):
        validators_mod.bucket_existing_key_pattern = r'^[a-zA-Z0-9._\-/]{1,1024}$'
        added_pattern = True

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "sqsBucketSync_under_test", os.path.abspath(_SQS_BUCKET_SYNC_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
        if added_pattern and validators_mod is not None:
            delattr(validators_mod, "bucket_existing_key_pattern")
    _cached_sync_db_module = module
    return module




@pytest.mark.unit
class TestLookupDatabasesExcludesDeleted:
    """`lookup_databases` is the only place the suffix can be filtered once, for every caller."""

    @staticmethod
    def _module_with_scan(items):
        m = _load()
        m.dynamodb = MagicMock()
        table = MagicMock()
        table.scan.return_value = {"Items": items}
        m.dynamodb.Table.return_value = table
        # A real cache would carry results between cases and make the second assertion vacuous.
        m.database_cache = MagicMock()
        m.database_cache.get.return_value = None
        return m, table

    def test_a_soft_deleted_database_is_not_returned(self):
        # The exact shape observed live: the only row matching the bucket is a deleted database.
        m, _ = self._module_with_scan([
            {"databaseId": "tsl5-dbb#deleted", "defaultBucketId": "bucket-1"},
        ])
        assert m.lookup_databases("bucket-1") == []

    def test_a_live_database_is_still_returned(self):
        # The positive control. Without it, the assertion above is satisfied by a function that
        # returns nothing at all — which would stop bucket sync from ever finding a database.
        m, _ = self._module_with_scan([
            {"databaseId": "live-db", "defaultBucketId": "bucket-1"},
        ])
        assert [db["databaseId"] for db in m.lookup_databases("bucket-1")] == ["live-db"]

    def test_a_live_database_is_kept_when_a_deleted_one_matches_too(self):
        # The case that actually bit: both rows match the bucket, and the deleted one came back first,
        # so `matching_databases[0]` in the caller resolved to it.
        m, _ = self._module_with_scan([
            {"databaseId": "tsl5-dbb#deleted", "defaultBucketId": "bucket-1"},
            {"databaseId": "live-db", "defaultBucketId": "bucket-1"},
        ])
        assert [db["databaseId"] for db in m.lookup_databases("bucket-1")] == ["live-db"]

    def test_a_database_whose_name_merely_contains_the_marker_is_kept(self):
        # The suffix is a marker, not a substring: a database legitimately named
        # "archive#deleted-records" is live and must not be filtered out.
        m, _ = self._module_with_scan([
            {"databaseId": "archive#deleted-records", "defaultBucketId": "bucket-1"},
        ])
        assert [db["databaseId"] for db in m.lookup_databases("bucket-1")] == [
            "archive#deleted-records"
        ]

    def test_only_live_databases_are_cached(self):
        # A cached deleted database would be handed out by later lookups even after this filter,
        # because the cache is consulted before the scan.
        m, _ = self._module_with_scan([
            {"databaseId": "tsl5-dbb#deleted", "defaultBucketId": "bucket-1"},
            {"databaseId": "live-db", "defaultBucketId": "bucket-1"},
        ])
        m.lookup_databases("bucket-1")
        cached = [c.args[0] for c in m.database_cache.set.call_args_list]
        assert cached == ["database:live-db"]

    def test_a_scan_failure_still_returns_an_empty_list(self):
        # Unchanged behaviour, asserted so the filter cannot have moved the exception path.
        m, table = self._module_with_scan([])
        table.scan.side_effect = RuntimeError("scan blew up")
        assert m.lookup_databases("bucket-1") == []
