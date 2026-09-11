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

from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_DIRECT,
    VAMS_CHANGE_SOURCE_UPLOAD,
)

# Absolute path to the real sqsBucketSync module file.
_SQS_BUCKET_SYNC_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing", "sqsBucketSync.py"
)

_cached_module = None


def _load():
    """Load the real sqsBucketSync module by file path with boto3 stubbed.

    The mock `handlers.indexing` package registered by the root conftest shadows
    the real package, so a normal `import` cannot reach the real module. Load it
    directly from its file path. boto3 is stubbed so its import-time
    client/resource calls succeed without AWS access.
    """
    global _cached_module
    if _cached_module is not None:
        return _cached_module

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
    _cached_module = module
    return module


@pytest.mark.unit
class TestBuildHistoryRecord:
    def test_direct_when_no_change_source(self):
        m = _load()
        rec = m.build_history_record(
            database_id="db1", asset_id="a1", relative_file_path="m/x.glb",
            version_id="v1", s3_metadata={}, s3_last_modified="2026-06-09T00:00:00Z",
        )
        assert rec["databaseId:assetId:filePath"] == "db1:a1:/m/x.glb"
        assert rec["versionId"] == "v1"
        assert rec["databaseId:assetId"] == "db1:a1"
        assert rec["databaseId"] == "db1"
        assert rec["assetId"] == "a1"
        assert rec["filePath"] == "/m/x.glb"
        assert rec["changeSource"] == VAMS_CHANGE_SOURCE_DIRECT
        assert "changeUserId" not in rec

    def test_upload_change_maps_metadata_to_columns(self):
        m = _load()
        rec = m.build_history_record(
            database_id="db1", asset_id="a1", relative_file_path="m/x.glb",
            version_id="null",
            s3_metadata={
                VAMS_CHANGE_SOURCE_METADATA_KEY: VAMS_CHANGE_SOURCE_UPLOAD,
                VAMS_CHANGE_USER_ID_METADATA_KEY: "alice",
            },
            s3_last_modified="2026-06-09T00:00:00Z",
        )
        assert rec["changeSource"] == VAMS_CHANGE_SOURCE_UPLOAD
        assert rec["changeUserId"] == "alice"
        assert rec["versionId"] == "null"

    def test_blank_provenance_values_not_written(self):
        m = _load()
        rec = m.build_history_record(
            database_id="db1", asset_id="a1", relative_file_path="x.glb",
            version_id="v1",
            s3_metadata={
                VAMS_CHANGE_SOURCE_METADATA_KEY: "fileMove",
                VAMS_CHANGE_USER_ID_METADATA_KEY: "bob",
                # workflow id present but empty -> should be skipped
                "vams-changeworkflowid": "",
                "vams-changeassetidfrom": "a0",
                "vams-changedatabaseidfrom": "db0",
                "vams-changeassetfilepathfrom": "old/x.glb",
                "vams-changeassetfileversionfrom": "srcver-9",
            },
            s3_last_modified="t",
        )
        assert rec["changeSource"] == "fileMove"
        assert rec["changeUserId"] == "bob"
        assert rec["changeAssetIdFrom"] == "a0"
        assert rec["changeDatabaseIdFrom"] == "db0"
        assert rec["changeAssetFilePathFrom"] == "old/x.glb"
        assert rec["changeAssetFileVersionFrom"] == "srcver-9"
        assert "changeWorkflowId" not in rec  # blank string skipped
