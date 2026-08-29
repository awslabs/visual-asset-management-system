# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Folder detection in the file indexer.

Guards S2-BACKEND-097: `is_folder_path` returned True for any path whose
basename had no dot, so `LICENSE`, `Dockerfile`, `Makefile`, `README` and every
extension-less data export were classified as folders. Each returned
`operation='skip'` with `success=True`, so the file was never added to the file
index, never retried, and no error surfaced -- permanently invisible to file
search.

Folder-ness is now decided from the key shape (a trailing '/'), which is what S3
itself uses for a folder marker.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}

_FILE_INDEXER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "backend", "handlers", "indexing", "fileIndexer.py",
)


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


@pytest.fixture
def fileIndexer():
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
                "fileIndexer_folder_under_test", os.path.abspath(_FILE_INDEXER_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


@pytest.mark.unit
class TestIsFolderPath:
    @pytest.mark.parametrize("file_path", [
        "/LICENSE",
        "/Dockerfile",
        "/Makefile",
        "/docs/README",
        "/exports/scan_2026_08",
        "/CHANGELOG",
    ])
    def test_extension_less_file_is_not_a_folder(self, fileIndexer, file_path):
        assert fileIndexer.is_folder_path(file_path) is False

    @pytest.mark.parametrize("file_path", [
        "/",
        "/folder/",
        "/folder/nested/",
        "/folder.with.dots/",
    ])
    def test_trailing_slash_is_a_folder(self, fileIndexer, file_path):
        """Positive control: folder detection still works, including for a
        folder name that contains dots."""
        assert fileIndexer.is_folder_path(file_path) is True

    @pytest.mark.parametrize("file_path", ["/model.glb", "/a/b/part.stp"])
    def test_ordinary_files_are_not_folders(self, fileIndexer, file_path):
        assert fileIndexer.is_folder_path(file_path) is False


def _index_request(module, file_path):
    from models.indexing import FileIndexRequest
    return FileIndexRequest(
        databaseId="db1", assetId="a1", filePath=file_path,
        bucketName="bucket", s3Key="a1" + file_path, operation="index",
    )


@pytest.mark.unit
class TestExtensionLessFileIsIndexed:
    """The user-visible half: the request must reach the index, not be skipped."""

    def _run(self, m, file_path):
        with patch.object(m, "get_asset_details_any_state",
                          return_value=({"assetId": "a1", "bucketId": "b1",
                                         "assetLocation": {"Key": "a1/"}}, False)), \
                patch.object(m, "get_bucket_details",
                             return_value={"bucketName": "bucket",
                                           "baseAssetsPrefix": "", "bucketId": "b1"}), \
                patch.object(m, "get_file_metadata", return_value=({}, {})), \
                patch.object(m, "get_s3_file_info",
                             return_value=({"versionId": "v1", "size": 10}, False)), \
                patch.object(m, "build_file_document", return_value=MagicMock()), \
                patch.object(m, "index_file_document", return_value=True) as indexer:
            result = m.process_file_index_request(_index_request(m, file_path))
        return result, indexer

    def test_license_file_is_indexed(self, fileIndexer):
        result, indexer = self._run(fileIndexer, "/LICENSE")
        assert result.operation == "index"
        assert result.success is True
        assert indexer.called, "the extension-less file never reached OpenSearch"

    def test_folder_marker_is_still_skipped(self, fileIndexer):
        """Positive control: a real folder key must not be indexed."""
        result, indexer = self._run(fileIndexer, "/folder/")
        assert result.operation == "skip"
        assert not indexer.called
