# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the S3 object key bound in the file indexer.

S3 allows an object key of up to 1024 UTF-8 bytes, so a file under a deep
folder tree is a legitimate key and must reach the file index (otherwise it is
permanently missing from search, with only a log line to show for it). The
bound still exists: a key beyond S3's own limit is refused, and because S3
measures its limit in bytes, a multi-byte path that satisfies a character count
is refused too.

The document `_id` (`databaseId#assetId#filePath`) carries a second, smaller
ceiling -- OpenSearch refuses an `_id` over 512 bytes -- so these tests also
cover the id derivation in bytes, and assert the index and delete paths address
the same document for a path long enough to cross that ceiling.

Path traversal is deliberately NOT relaxed here: `filePath` still runs through
the RELATIVE_FILE_PATH validator, which rejects any path containing '..'.

Guards FIX-021 (S2-BACKEND-095): a 256-character cap on the S3 object key excludes legitimate
deep-tree files from the index, with only a log line to show for it.
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

# S3's object key limit, in UTF-8 bytes.
S3_KEY_LIMIT_BYTES = 1024
# OpenSearch's document _id limit, in UTF-8 bytes.
OPENSEARCH_ID_LIMIT_BYTES = 512

DATABASE_ID = "db-1"
ASSET_ID = "asset-1"
BUCKET_NAME = "test-bucket"


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

    `common.validators.validate` is the REAL dispatcher (registered by the root
    conftest), so the validation assertions below exercise the real rules.
    """
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


def _deep_relative_path(min_chars, file_name="model.glb"):
    """Asset-relative path of at least `min_chars` characters ending in a file name."""
    segments = []
    while len("/" + "".join(segments) + file_name) < min_chars:
        segments.append("nested-folder/")
    return "/" + "".join(segments) + file_name


def _index_request(fileIndexer, file_path, s3_key=None):
    return fileIndexer.FileIndexRequest(
        databaseId=DATABASE_ID,
        assetId=ASSET_ID,
        filePath=file_path,
        bucketName=BUCKET_NAME,
        s3Key=s3_key if s3_key is not None else f"{ASSET_ID}{file_path}",
        operation="index",
    )


def _patched_lookups(fileIndexer, indexed=True):
    """Patch every lookup around process_file_index_request's validation step.

    Returns the patchers as a context-manager list plus the index_file_document
    mock, so a test can assert whether indexing was actually reached.
    """
    index_mock = MagicMock(return_value=indexed)
    patchers = [
        patch.object(
            fileIndexer,
            "get_asset_details_any_state",
            return_value=({"bucketId": "bucket-1", "assetName": "an asset", "tags": []}, False),
        ),
        patch.object(
            fileIndexer,
            "get_bucket_details",
            return_value={
                "bucketId": "bucket-1",
                "bucketName": BUCKET_NAME,
                "baseAssetsPrefix": "/",
            },
        ),
        patch.object(fileIndexer, "get_file_metadata", return_value=({}, {})),
        patch.object(
            fileIndexer,
            "get_s3_file_info",
            return_value=({"size": 12, "lastModified": None, "etag": "etag", "versionId": "v1"}, False),
        ),
        patch.object(fileIndexer, "find_preview_file_key", return_value=""),
        patch.object(fileIndexer, "index_file_document", index_mock),
    ]
    return patchers, index_mock


def _run_index_request(fileIndexer, request):
    patchers, index_mock = _patched_lookups(fileIndexer)
    for p in patchers:
        p.start()
    try:
        result = fileIndexer.process_file_index_request(request)
    finally:
        for p in patchers:
            p.stop()
    return result, index_mock


@pytest.mark.unit
class TestS3KeyBound:
    """Pure-function tests on the key bound."""

    def test_key_of_300_characters_is_accepted(self, fileIndexer):
        key = "a" * 300
        assert fileIndexer.validate_s3_key("s3Key", key) == (True, "")

    def test_key_at_the_s3_limit_is_accepted(self, fileIndexer):
        key = "a" * S3_KEY_LIMIT_BYTES
        valid, message = fileIndexer.validate_s3_key("s3Key", key)
        assert valid, message

    def test_key_beyond_the_s3_limit_is_rejected(self, fileIndexer):
        key = "a" * (S3_KEY_LIMIT_BYTES + 1)
        valid, message = fileIndexer.validate_s3_key("s3Key", key)
        assert not valid
        assert "s3Key" in message

    def test_multibyte_key_within_the_character_limit_is_rejected_on_bytes(self, fileIndexer):
        # 600 three-byte characters: 600 characters (under a 1024-character
        # count) but 1800 bytes (over S3's limit).
        key = "グ" * 600
        assert len(key) <= S3_KEY_LIMIT_BYTES
        assert len(key.encode("utf-8")) > S3_KEY_LIMIT_BYTES
        valid, _ = fileIndexer.validate_s3_key("s3Key", key)
        assert not valid

    def test_empty_key_is_rejected(self, fileIndexer):
        assert fileIndexer.validate_s3_key("s3Key", "")[0] is False
        assert fileIndexer.validate_s3_key("s3Key", None)[0] is False


@pytest.mark.unit
class TestProcessFileIndexRequestKeyLength:
    """The bound as the indexing path applies it."""

    def test_ordinary_short_key_is_indexed(self, fileIndexer):
        # Positive control: a broken validator that rejects everything cannot
        # pass this test.
        request = _index_request(fileIndexer, "/folder/model.glb")
        result, index_mock = _run_index_request(fileIndexer, request)
        assert result.success is True
        assert result.operation == "index"
        index_mock.assert_called_once()

    def test_deep_path_over_256_characters_is_indexed(self, fileIndexer):
        file_path = _deep_relative_path(300)
        request = _index_request(fileIndexer, file_path)
        assert len(request.s3Key) > 256
        assert len(request.s3Key.encode("utf-8")) <= S3_KEY_LIMIT_BYTES

        result, index_mock = _run_index_request(fileIndexer, request)
        assert result.success is True
        assert result.operation == "index"
        index_mock.assert_called_once()

    def test_key_over_the_s3_byte_limit_is_not_indexed(self, fileIndexer):
        # A multi-byte path passes the model's character bound and is still
        # refused on bytes, so the bound survives the raised cap.
        file_path = "/" + "グ" * 600 + "/model.glb"
        request = _index_request(fileIndexer, file_path)
        assert len(request.s3Key.encode("utf-8")) > S3_KEY_LIMIT_BYTES

        result, index_mock = _run_index_request(fileIndexer, request)
        assert result.success is False
        assert result.operation == "validation_error"
        index_mock.assert_not_called()

    def test_path_traversal_is_still_rejected(self, fileIndexer):
        # The '..' guard lives on filePath (RELATIVE_FILE_PATH) and is not
        # relaxed by the key bound.
        request = _index_request(fileIndexer, "/a/../../other-asset/model.glb")
        result, index_mock = _run_index_request(fileIndexer, request)
        assert result.success is False
        assert result.operation == "validation_error"
        index_mock.assert_not_called()


@pytest.mark.unit
class TestFileDocumentId:
    """The `_id` ceiling, measured in bytes."""

    def test_short_components_keep_the_plain_id(self, fileIndexer):
        # Documents that already fit are addressed exactly as before, so
        # existing index entries stay reachable.
        doc_id = fileIndexer.build_file_document_id(DATABASE_ID, ASSET_ID, "/folder/model.glb")
        assert doc_id == f"{DATABASE_ID}#{ASSET_ID}#/folder/model.glb"

    def test_id_stays_within_the_limit_at_maximum_components(self, fileIndexer):
        # databaseId at the ID validator's 63-character maximum, assetId at the
        # ASSET_ID pattern's 255-character maximum, filePath at the model's
        # 1024-character maximum.
        database_id = "d" * 63
        asset_id = "a" * 255
        file_path = _deep_relative_path(1024)[:1024]

        plain = f"{database_id}#{asset_id}#{file_path}"
        assert len(plain.encode("utf-8")) > OPENSEARCH_ID_LIMIT_BYTES

        doc_id = fileIndexer.build_file_document_id(database_id, asset_id, file_path)
        assert len(doc_id.encode("utf-8")) <= OPENSEARCH_ID_LIMIT_BYTES

    def test_multibyte_path_id_is_bounded_in_bytes(self, fileIndexer):
        # 300 three-byte characters: a character count would wave this through.
        file_path = "/" + "グ" * 300 + "/model.glb"
        plain = f"{DATABASE_ID}#{ASSET_ID}#{file_path}"
        assert len(plain) <= OPENSEARCH_ID_LIMIT_BYTES
        assert len(plain.encode("utf-8")) > OPENSEARCH_ID_LIMIT_BYTES

        doc_id = fileIndexer.build_file_document_id(DATABASE_ID, ASSET_ID, file_path)
        assert len(doc_id.encode("utf-8")) <= OPENSEARCH_ID_LIMIT_BYTES
        # Truncation must not leave a partial character behind.
        assert "�" not in doc_id

    def test_long_paths_differing_in_one_character_get_distinct_ids(self, fileIndexer):
        long_path = _deep_relative_path(700)
        first = fileIndexer.build_file_document_id(DATABASE_ID, ASSET_ID, long_path + "a")
        second = fileIndexer.build_file_document_id(DATABASE_ID, ASSET_ID, long_path + "b")
        assert first != second

    def test_index_and_delete_derive_the_same_id_for_a_long_path(self, fileIndexer):
        # Without this, a long-path document could be indexed under one id and
        # searched for deletion under another, leaving an undeletable orphan.
        file_path = _deep_relative_path(700)
        document = fileIndexer.FileDocumentModel(
            str_key=file_path,
            str_databaseid=DATABASE_ID,
            str_assetid=ASSET_ID,
        )

        client = MagicMock()
        client.index.return_value = {"result": "created"}
        manager = MagicMock()
        manager.is_available.return_value = True
        manager.get_client.return_value = client

        with patch.object(fileIndexer, "opensearch_manager", manager):
            assert fileIndexer.index_file_document(document) is True
            assert fileIndexer.delete_file_document(DATABASE_ID, ASSET_ID, file_path) is True

        indexed_id = client.index.call_args.kwargs["id"]
        deleted_id = client.delete.call_args.kwargs["id"]
        assert indexed_id == deleted_id
        assert len(indexed_id.encode("utf-8")) <= OPENSEARCH_ID_LIMIT_BYTES
        # This path is long enough that the plain id would not have fit.
        assert indexed_id != f"{DATABASE_ID}#{ASSET_ID}#{file_path}"


@pytest.mark.unit
class TestS3NotificationPath:
    """The production entry point: S3 event -> process_file_index_request."""

    def test_deep_key_from_an_s3_event_reaches_indexing(self, fileIndexer):
        file_path = _deep_relative_path(300)
        s3_key = f"{ASSET_ID}{file_path}"
        assert len(s3_key) > 256

        record = {
            "eventName": "ObjectCreated:Put",
            "s3": {
                "bucket": {"name": BUCKET_NAME},
                "object": {"key": s3_key},
            },
        }
        head_object = MagicMock(
            return_value={
                "Metadata": {
                    fileIndexer.ASSET_ID_METADATA_KEY: ASSET_ID,
                    fileIndexer.DATABASE_ID_METADATA_KEY: DATABASE_ID,
                }
            }
        )

        patchers, index_mock = _patched_lookups(fileIndexer)
        patchers.append(patch.object(fileIndexer.s3_client, "head_object", head_object))
        for p in patchers:
            p.start()
        try:
            result = fileIndexer.handle_s3_notification(record)
        finally:
            for p in patchers:
                p.stop()

        assert result.success is True
        assert result.operation == "index"
        index_mock.assert_called_once()
        # The document is addressed by the full deep path.
        assert file_path in result.documentId


@pytest.mark.unit
class TestNeighbouringLimitsUnchanged:
    """Guards against the key bound being widened where it should not be."""

    def test_string_256_validator_still_caps_at_256(self):
        from common.validators import validate

        at_limit = validate({"versionId": {"value": "v" * 256, "validator": "STRING_256"}})
        assert at_limit[0] is True

        over_limit = validate({"versionId": {"value": "v" * 257, "validator": "STRING_256"}})
        assert over_limit[0] is False

    def test_opensearch_field_name_truncation_still_255(self):
        from models.indexing import _sanitize_field_name

        assert len(_sanitize_field_name("f" * 300)) == 255
