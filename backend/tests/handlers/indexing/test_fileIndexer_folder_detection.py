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


def _stream_record(event_name, composite_key):
    """A metadata/attribute table stream record. REMOVE carries Keys, the others
    carry NewImage."""
    field = "Keys" if event_name == "REMOVE" else "NewImage"
    return {
        "eventName": event_name,
        "eventSource": "aws:dynamodb",
        "dynamodb": {field: {"databaseId:assetId:filePath": {"S": composite_key}}},
    }


@pytest.mark.unit
class TestMetadataStreamExtensionLessFile:
    """The other two `is_folder_path` call sites.

    `handle_metadata_stream` applies the same test in its REMOVE branch and in its
    INSERT/MODIFY branch, and each returns `operation='skip'` before reaching
    `process_file_index_request`. Metadata written against an extension-less file
    therefore never reached the index either -- a path the
    `process_file_index_request` tests above do not exercise.
    """

    def _run(self, m, event_name, file_path):
        from models.indexing import IndexOperationResponse
        forwarded = IndexOperationResponse(
            success=True, message="File document indexed",
            indexName="file-index", operation="index",
        )
        with patch.object(m, "get_asset_details",
                          return_value={"assetId": "a1", "bucketId": "b1",
                                        "assetLocation": {"Key": "a1/"}}), \
                patch.object(m, "get_bucket_details",
                             return_value={"bucketName": "bucket",
                                           "baseAssetsPrefix": "", "bucketId": "b1"}), \
                patch.object(m, "process_file_index_request",
                             return_value=forwarded) as processor:
            result = m.handle_metadata_stream(
                _stream_record(event_name, f"db1:a1:{file_path}"))
        return result, processor

    @pytest.mark.parametrize("event_name", ["INSERT", "MODIFY", "REMOVE"])
    def test_extension_less_file_metadata_is_forwarded(self, fileIndexer, event_name):
        result, processor = self._run(fileIndexer, event_name, "/LICENSE")
        assert result.operation == "index"
        assert processor.called, (
            "the metadata stream classified the extension-less file as a folder "
            "and never re-indexed it"
        )
        assert processor.call_args.args[0].filePath == "/LICENSE"

    @pytest.mark.parametrize("event_name", ["INSERT", "MODIFY", "REMOVE"])
    def test_folder_metadata_is_still_skipped(self, fileIndexer, event_name):
        """Positive control: folder-level metadata must still be skipped on every
        branch, so the fix did not turn folder records into file documents."""
        result, processor = self._run(fileIndexer, event_name, "/folder/")
        assert result.operation == "skip"
        assert not processor.called


@pytest.mark.unit
class TestFileExtensionFromBasename:
    """`extract_file_extension` reads the extension from the basename.

    It splits on the last dot, so a dot anywhere in the path used to be taken as
    the extension delimiter: `/folder.v2/LICENSE` yielded `str_fileext` of
    `'v2/license'`. That combination — an extension-less basename below a folder
    whose name contains a dot — only reaches this function now that folder-ness is
    decided from the trailing slash, so the two belong together.
    """

    @pytest.mark.parametrize("file_path", [
        "/folder.v2/LICENSE",
        "/data.raw/Dockerfile",
        "/deep.dir/sub/README",
        "/sim.output/manifest",
    ])
    def test_dotted_parent_folder_is_not_an_extension(self, fileIndexer, file_path):
        assert fileIndexer.extract_file_extension(file_path) is None

    @pytest.mark.parametrize("file_path,expected", [
        ("/model.glb", "glb"),
        ("/folder.v2/model.GLB", "glb"),
        ("/a/b/part.stp", "stp"),
        ("/archive.tar.gz", "gz"),
        ("/.gitignore", "gitignore"),
        ("/LICENSE", None),
        ("/folder/", None),
        ("/folder.with.dots/", None),
    ])
    def test_real_extensions_are_unchanged(self, fileIndexer, file_path, expected):
        """Positive control: every path whose basename carries a real extension
        still resolves to that extension, case-folded — including inside a dotted
        folder and for a multi-part suffix — so scoping to the basename did not
        empty out `str_fileext`."""
        assert fileIndexer.extract_file_extension(file_path) == expected


@pytest.mark.unit
class TestIndexedDocumentFileExtension:
    """The two halves of S2-BACKEND-097 as one indexed document.

    Asserts on the document handed to OpenSearch rather than on the helper, so it
    covers both that the extension-less file is no longer skipped and that the
    `str_fileext` it lands with is usable by the extension facet.
    """

    def _indexed_document(self, m, file_path):
        captured = {}

        def capture(document):
            captured["doc"] = document
            return True

        with patch.object(m, "get_asset_details_any_state",
                          return_value=({"assetId": "a1", "bucketId": "b1",
                                         "assetName": "Asset One", "tags": [],
                                         "assetLocation": {"Key": "a1/"}}, False)), \
                patch.object(m, "get_bucket_details",
                             return_value={"bucketName": "bucket",
                                           "baseAssetsPrefix": "", "bucketId": "b1"}), \
                patch.object(m, "get_file_metadata", return_value=({}, {})), \
                patch.object(m, "get_s3_file_info",
                             return_value=({"versionId": "v1", "size": 10}, False)), \
                patch.object(m, "find_preview_file_key", return_value=""), \
                patch.object(m, "index_file_document", side_effect=capture):
            result = m.process_file_index_request(_index_request(m, file_path))
        return result, captured.get("doc")

    def test_extension_less_file_in_a_dotted_folder_has_no_extension(self, fileIndexer):
        result, doc = self._indexed_document(fileIndexer, "/folder.v2/LICENSE")
        assert result.operation == "index"
        assert doc is not None, "the extension-less file never reached OpenSearch"
        assert doc.str_fileext is None, (
            f"str_fileext is {doc.str_fileext!r}; the parent folder's suffix was "
            "indexed as the file extension and pollutes the extension facet"
        )

    def test_extensioned_file_in_a_dotted_folder_keeps_its_extension(self, fileIndexer):
        """Positive control: the same dotted folder, with a real extension."""
        result, doc = self._indexed_document(fileIndexer, "/folder.v2/model.glb")
        assert result.operation == "index"
        assert doc is not None
        assert doc.str_fileext == "glb"
