# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

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

_cached_guard_module = None


def _load():
    """Load the real sqsBucketSync module by file path with boto3 stubbed.

    The mock `handlers.indexing` package registered by the root conftest shadows
    the real package, so a normal `import` cannot reach the real module. Load it
    directly from its file path. boto3 is stubbed so its import-time
    client/resource calls succeed without AWS access.
    """
    global _cached_guard_module
    if _cached_guard_module is not None:
        return _cached_guard_module

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
    _cached_guard_module = module
    return module


from botocore.exceptions import ClientError


@pytest.mark.unit
class TestObjectStillExists:
    def test_returns_true_when_object_present(self):
        m = _load()
        m.s3_client = MagicMock()
        m.s3_client.head_object.return_value = {"ContentLength": 10}
        assert m.object_still_exists("bucket", "db/asset1/file.glb") is True

    def test_returns_false_on_404(self):
        m = _load()
        m.s3_client = MagicMock()
        err = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        m.s3_client.head_object.side_effect = err
        assert m.object_still_exists("bucket", "db/asset1/file.glb") is False

    def test_returns_false_on_405_delete_marker(self):
        # Latest version is a delete marker -> HeadObject returns 405 MethodNotAllowed.
        # A 405 means archived/gone regardless of encoding, so no +/space fallback
        # retry is attempted (single head_object call).
        m = _load()
        m.s3_client = MagicMock()
        err = ClientError({"Error": {"Code": "405"}}, "HeadObject")
        m.s3_client.head_object.side_effect = err
        assert m.object_still_exists("bucket", "db/asset1/file.glb") is False
        assert m.s3_client.head_object.call_count == 1

    def test_returns_true_on_unexpected_error_fail_open(self):
        # An unexpected S3 error must NOT suppress legitimate creation (fail open).
        m = _load()
        m.s3_client = MagicMock()
        err = ClientError({"Error": {"Code": "500"}}, "HeadObject")
        m.s3_client.head_object.side_effect = err
        assert m.object_still_exists("bucket", "db/asset1/file.glb") is True

    def test_returns_true_when_alt_encoding_exists(self):
        # First head_object 404s on the delivered key shape, but the object exists
        # under the alternative encoding. A '+' in the filename triggers the two
        # shapes ('+' vs space). The object is a legitimate new file and must NOT
        # be treated as gone.
        m = _load()
        m.s3_client = MagicMock()
        err = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        m.s3_client.head_object.side_effect = [err, {"ContentLength": 10}]
        assert m.object_still_exists("bucket", "db/asset1/BACC66K41F158AM+---.CATPart") is True
        assert m.s3_client.head_object.call_count == 2

    def test_returns_false_when_both_encodings_404(self):
        # Both the delivered key and its alternative encoding 404 -> genuinely gone.
        m = _load()
        m.s3_client = MagicMock()
        err = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        m.s3_client.head_object.side_effect = [err, err]
        assert m.object_still_exists("bucket", "db/asset1/BACC66K41F158AM+---.CATPart") is False
        assert m.s3_client.head_object.call_count == 2


@pytest.mark.unit
class TestProcessS3RecordCreateGuard:
    def _record(self, key="db/x-asset-1/file.glb", bucket="asset-bucket"):
        return {"s3": {"bucket": {"name": bucket}, "object": {"key": key}},
                "eventName": "ObjectCreated:Put"}

    def _wire_create_branch(self, m, object_exists):
        # Configure module so process_s3_record reaches the create branch.
        m.asset_bucket_name = "asset-bucket"
        m.asset_bucket_prefix = "db/"
        m.RESERVED_S3_PREFIX_FOLDERS = set()
        m.get_bucket_id = MagicMock(return_value="bucket-1")
        m.extract_asset_id_from_key = MagicMock(return_value="x-asset-1")
        m.validate_asset_id = MagicMock(return_value=True)
        m.lookup_asset = MagicMock(return_value=None)          # no live asset
        m.lookup_archived_asset = MagicMock(return_value=None)  # not archived
        m.get_or_create_database_for_bucket = MagicMock(return_value="db-1")
        m.create_new_asset = MagicMock(return_value="x-asset-1")
        m.object_still_exists = MagicMock(return_value=object_exists)
        # Neutralize post-create work so the test focuses on the guard.
        m.update_s3_metadata = MagicMock(return_value=True)
        m.update_asset_type = MagicMock(return_value=True)

    def test_skips_creation_when_object_gone(self):
        # Asset (re)creation is skipped, but the record must still be forwarded
        # to the indexers so OpenSearch and other registered indexers can
        # reconcile their records for the now-deleted file.
        m = _load()
        self._wire_create_branch(m, object_exists=False)
        success, should_index, message = m.process_s3_record(self._record())
        m.create_new_asset.assert_not_called()
        assert success is True and should_index is True

    def test_creates_when_object_present(self):
        m = _load()
        self._wire_create_branch(m, object_exists=True)
        m.process_s3_record(self._record())
        m.create_new_asset.assert_called_once()
