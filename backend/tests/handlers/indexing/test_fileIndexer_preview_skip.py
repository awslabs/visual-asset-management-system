# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests: preview files (`.previewFile.*`) must never be indexed as
standalone documents via the DynamoDB metadata-stream path.

The reindexer "touches" the AssetFileMetadata table to trigger indexing, which
fires `fileIndexer.handle_metadata_stream`. Unlike the S3-event path, that path
does not rewrite a preview file to its base file, so it must skip preview files
outright (otherwise OpenSearch ends up with standalone `.previewFile.` docs).
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# fileIndexer reads several env vars and calls SSM at import time. Set the env
# here, and stub boto3 (SSM get_parameter, DynamoDB, S3, OpenSearch) in the
# `fileIndexer` fixture below so import succeeds deterministically without
# network/AWS access. The import is deferred into the fixture (rather than at
# module load) so the root conftest autouse fixture has already registered the
# mock `common`/`common.indexing` packages in sys.modules first.
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


# Absolute path to the real fileIndexer module file.
_FILE_INDEXER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing", "fileIndexer.py"
)


@pytest.fixture
def fileIndexer():
    """Load the real fileIndexer module by file path with boto3 stubbed.

    The mock `handlers` and `common` packages registered by the root conftest
    autouse fixture shadow the real packages, so a normal `import` cannot reach
    the real fileIndexer. We load it directly from its file path instead. Its
    dependency submodules (`common.constants`, `common.validators`,
    `common.indexing.geoLocation`, `handlers.auth`, `handlers.authz`,
    `models.*`) are already wired into sys.modules by the autouse fixture, so
    the module's top-level imports resolve. boto3 is stubbed so the module's
    import-time SSM/client/resource calls succeed without AWS access.
    """
    # The conftest autouse fixture registers handlers.auth/handlers.authz as
    # empty MockModules. fileIndexer needs CasbinEnforcer and request_to_claims
    # at import time, so provide minimal stubs (saved/restored around the load).
    saved = {name: sys.modules.get(name) for name in ("handlers.auth", "handlers.authz")}

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["mock_token"]})
    sys.modules["handlers.auth"] = auth_stub

    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "fileIndexer_under_test", os.path.abspath(_FILE_INDEXER_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


def _metadata_stream_record(composite_key, event_name="INSERT"):
    """Build a DynamoDB metadata-table stream record with the given composite key."""
    image = {"databaseId:assetId:filePath": {"S": composite_key}}
    record = {"eventName": event_name, "dynamodb": {}}
    if event_name == "REMOVE":
        record["dynamodb"]["Keys"] = image
    else:
        record["dynamodb"]["NewImage"] = image
    return record


@pytest.mark.unit
class TestMetadataStreamPreviewSkip:
    def test_insert_preview_file_is_skipped(self, fileIndexer):
        record = _metadata_stream_record("db-1:asset-1:/photo.e57.previewFile.gif", "INSERT")
        with patch.object(fileIndexer, "process_file_index_request") as proc:
            result = fileIndexer.handle_metadata_stream(record)
        assert result.operation == "skip"
        assert "preview" in result.message.lower()
        # Must never reach indexing for a preview file.
        proc.assert_not_called()

    def test_modify_preview_file_is_skipped(self, fileIndexer):
        record = _metadata_stream_record("db-1:asset-1:/sub/model.obj.previewFile.png", "MODIFY")
        with patch.object(fileIndexer, "process_file_index_request") as proc:
            result = fileIndexer.handle_metadata_stream(record)
        assert result.operation == "skip"
        proc.assert_not_called()

    def test_remove_preview_file_is_skipped(self, fileIndexer):
        record = _metadata_stream_record("db-1:asset-1:/photo.e57.previewFile.gif", "REMOVE")
        with patch.object(fileIndexer, "process_file_index_request") as proc:
            result = fileIndexer.handle_metadata_stream(record)
        assert result.operation == "skip"
        proc.assert_not_called()

    def test_regular_file_is_not_skipped_as_preview(self, fileIndexer):
        # A normal base file must NOT be caught by the preview-file guard. It
        # proceeds past that guard into the asset/bucket lookups (mocked away
        # here via get_asset_details returning None -> a generic, non-preview
        # skip). The key assertion is that it is not skipped *as a preview*.
        record = _metadata_stream_record("db-1:asset-1:/photo.e57", "INSERT")
        with patch.object(fileIndexer, "get_asset_details", return_value=None), \
             patch.object(fileIndexer, "process_file_index_request") as proc:
            result = fileIndexer.handle_metadata_stream(record)
        assert "preview" not in result.message.lower()
        proc.assert_not_called()  # short-circuited at asset lookup, not at preview guard
