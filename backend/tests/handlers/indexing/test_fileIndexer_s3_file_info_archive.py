# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The file indexer reads an archived file as archived.

`get_s3_file_info` HEADs the key's current version. When that version is a delete marker (an
archived file), HeadObject answers 404, and a HEAD response has no body, so botocore reports the
HTTP status as the error code: `'404'`, not `'NoSuchKey'`. An archive check keyed on `'NoSuchKey'`
alone never runs, the file reads as `(None, False)` (neither present nor archived), and
`process_file_index_request` skips it as a successful no-op. A metadata or attribute delete on an
archived file (the file-metadata DELETE route does not require the file to exist) therefore never
reaches the index, and the document keeps the removed key.

The archive decision belongs to `common.s3.is_object_version_archived` (backend/CLAUDE.md Rule 14).
tests/conftest.py registers a double of `common.s3` that calls the client it is passed, so the fake
client below answers for the shared helper as well as for the handler.

The fake answers the way S3 does: HeadObject without a version id is 404 when the current version is
a delete marker or no version exists, HeadObject on a delete-marker version is 405, and
ListObjectVersions returns one key's entries newest first with `IsLatest` on the current one.
"""

import importlib.util
import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

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
    """Load the real fileIndexer module by file path with boto3 stubbed
    (same pattern as test_fileIndexer_archive_lifecycle)."""
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
                "fileIndexer_s3_file_info_under_test", os.path.abspath(_FILE_INDEXER_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


KEY = "a1/part.stp"
ASSET = {"assetId": "a1", "assetName": "Part", "bucketId": "b1",
         "assetLocation": {"Key": "a1/"}}
BUCKET = {"bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""}

LIVE = [
    {"VersionId": "v2", "marker": False},
    {"VersionId": "v1", "marker": False},
]
ARCHIVED = [{"VersionId": "dm1", "marker": True}] + LIVE


def _client_error(code, op="HeadObject"):
    return ClientError({"Error": {"Code": code}}, op)


def _fake_s3(history, head_error=None, missing_code="404"):
    """An S3 client over one key's version history, given newest first.

    Each entry is ``{"VersionId": ..., "marker": bool}``. ``missing_code`` is the error code
    HeadObject reports for a key whose current version is a delete marker or absent.
    """
    client = MagicMock()

    def head(entry):
        return {
            "ContentLength": 7,
            "LastModified": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "ETag": f'"etag-{entry["VersionId"]}"',
            "VersionId": entry["VersionId"],
            "ContentType": "model/step",
            "Metadata": {"assetid": "a1", "databaseid": "db1",
                         "reviewer": f"reviewer-{entry['VersionId']}"},
        }

    def head_object(Bucket, Key, VersionId=None):
        if head_error:
            raise _client_error(head_error)
        if Key != KEY:
            raise _client_error(missing_code)
        if VersionId is None:
            if not history or history[0]["marker"]:
                raise _client_error(missing_code)
            return head(history[0])
        for entry in history:
            if entry["VersionId"] == VersionId:
                if entry["marker"]:
                    raise _client_error("405")
                return head(entry)
        raise _client_error("404")

    def list_object_versions(Bucket, Prefix, MaxKeys=None, **_):
        rows = []
        if KEY.startswith(Prefix):
            for position, entry in enumerate(history):
                rows.append((entry, {
                    "Key": KEY,
                    "VersionId": entry["VersionId"],
                    "IsLatest": position == 0,
                    "LastModified": datetime(2026, 9, 1, 0, 0, len(history) - position,
                                             tzinfo=timezone.utc),
                }))
        if MaxKeys is not None:
            rows = rows[:MaxKeys]
        return {
            "Versions": [dict(row, Size=7, ETag=f'"etag-{row["VersionId"]}"')
                         for entry, row in rows if not entry["marker"]],
            "DeleteMarkers": [row for entry, row in rows if entry["marker"]],
        }

    client.head_object.side_effect = head_object
    client.list_object_versions.side_effect = list_object_versions
    return client


@pytest.mark.unit
class TestGetS3FileInfoArchiveState:
    def test_archived_key_reads_as_archived_from_the_newest_version(self, fileIndexer):
        with patch.object(fileIndexer, "s3_client", _fake_s3(ARCHIVED)):
            info, archived = fileIndexer.get_s3_file_info("bucket", KEY)
        assert archived is True, "a HeadObject 404 on a delete-marker key was not read as an archive"
        assert info["versionId"] == "v2"
        assert info["size"] == 7
        # The archived document keeps the searchable S3 metadata of the version it describes
        assert info["s3_reviewer"] == "reviewer-v2"

    @pytest.mark.parametrize("missing_code", ["404", "NoSuchKey", "NotFound"])
    def test_every_not_found_spelling_reaches_the_archive_check(self, fileIndexer, missing_code):
        with patch.object(fileIndexer, "s3_client",
                          _fake_s3(ARCHIVED, missing_code=missing_code)):
            info, archived = fileIndexer.get_s3_file_info("bucket", KEY)
        assert archived is True
        assert info["versionId"] == "v2"

    def test_newest_version_lookup_reads_one_bounded_page(self, fileIndexer):
        """The newest-version lookup is capped at one listing page, so a short key that prefixes
        many sibling keys cannot page through their versions."""
        with patch.object(fileIndexer, "s3_client", _fake_s3(ARCHIVED)), \
                patch.object(fileIndexer, "list_all_object_versions",
                             wraps=fileIndexer.list_all_object_versions) as lookup:
            fileIndexer.get_s3_file_info("bucket", KEY)
        assert lookup.call_count == 1
        assert lookup.call_args.kwargs["max_keys"] == fileIndexer.S3_VERSIONS_PAGE_SIZE

    def test_live_key_reads_as_live(self, fileIndexer):
        """Control for the archived arm."""
        with patch.object(fileIndexer, "s3_client", _fake_s3(LIVE)):
            info, archived = fileIndexer.get_s3_file_info("bucket", KEY)
        assert archived is False
        assert info["versionId"] == "v2"
        assert info["contentType"] == "model/step"

    def test_key_with_no_version_reads_as_absent(self, fileIndexer):
        """Control: a permanently deleted key is neither live nor archived."""
        with patch.object(fileIndexer, "s3_client", _fake_s3([])):
            assert fileIndexer.get_s3_file_info("bucket", KEY) == (None, False)

    def test_other_head_error_stays_best_effort(self, fileIndexer):
        """Control: an error that says nothing about the key keeps the (None, False) answer
        callers already handle, rather than raising into them."""
        with patch.object(fileIndexer, "s3_client", _fake_s3(ARCHIVED, head_error="503")):
            assert fileIndexer.get_s3_file_info("bucket", KEY) == (None, False)


def _metadata_remove_record():
    return {
        "eventName": "REMOVE",
        "dynamodb": {"Keys": {
            "databaseId:assetId:filePath": {"S": "db1:a1:/part.stp"},
            "metadataKey": {"S": "material"},
        }},
    }


def _index_after_metadata_remove(m, history):
    """Run the metadata-stream REMOVE path; return (response, documents sent to the index)."""
    with patch.object(m, "s3_client", _fake_s3(history)), \
            patch.object(m, "get_asset_details", return_value=ASSET), \
            patch.object(m, "get_bucket_details", return_value=BUCKET), \
            patch.object(m, "get_file_metadata", return_value=({"finish": "matte"}, {})), \
            patch.object(m, "find_preview_file_key", return_value=""), \
            patch.object(m, "index_file_document", return_value=True) as index:
        result = m.handle_metadata_stream(_metadata_remove_record())
    return result, [call.args[0] for call in index.call_args_list]


@pytest.mark.unit
class TestArchivedFileMetadataChangeReachesTheIndex:
    def test_metadata_remove_on_archived_file_is_indexed_as_archived(self, fileIndexer):
        result, documents = _index_after_metadata_remove(fileIndexer, ARCHIVED)
        assert len(documents) == 1, "the archived file's metadata change never reached the index"
        document = documents[0]
        assert document.bool_archived is True
        assert document.str_s3_version_id == "v2"
        assert document.num_filesize == 7
        assert document.MD_["finish"] == "matte"
        assert result.success is True and result.operation == "index"

    def test_metadata_remove_on_live_file_is_indexed_live(self, fileIndexer):
        """Control for the archived arm."""
        result, documents = _index_after_metadata_remove(fileIndexer, LIVE)
        assert len(documents) == 1
        assert documents[0].bool_archived is False
        assert documents[0].str_s3_version_id == "v2"
        assert result.success is True and result.operation == "index"
