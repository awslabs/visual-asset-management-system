# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The file indexer's S3 delete-event branch reads a full page of a key's version history, and
the archived document it writes keeps the file's S3 metadata.

An ``ObjectRemoved`` event is classified from the key's version listing: archived (the current
version is a delete marker and versions remain), live again (versions remain and the current
version is not a delete marker), or permanently deleted (no version remains). ListObjectVersions
returns one page, capped by ``MaxKeys``, with a key's entries newest first, and a DELETE without a
version id on a key whose current version is already a delete marker adds another marker. A key
can therefore carry more delete markers above its newest version than a ten-entry page holds, and
a branch that reads only such a page takes an archived file for a permanently deleted one and
removes its document. The listing belongs to ``common.s3.list_all_object_versions``, capped at one
``S3_VERSIONS_PAGE_SIZE`` page (backend/CLAUDE.md Rule 14).

``index_file_document`` replaces the stored document, so the archived document keeps the file's
searchable S3 metadata (``MD_.s3_*``, including the primary type ``s3_vams-primarytype``) only when
the file information it is built from carries it, as the HeadObject response of the version does.

tests/conftest.py registers a double of ``common.s3`` whose ``list_all_object_versions`` makes one
uncapped call on the client it is passed, so the fake client below answers for it. The fake answers
the way S3 does: ListObjectVersions returns one key's entries newest first with ``IsLatest`` on the
current one and honours ``MaxKeys``, HeadObject without a version id is 404 when the current
version is a delete marker, and HeadObject on a delete-marker version is 405.
"""

import importlib.util
import inspect
import os
import sys
import types
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

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
    "backend", "handlers", "osSemanticSearch", "osFileIndexer.py",
)


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


@pytest.fixture
def fileIndexer():
    """Load the real fileIndexer module by file path with boto3 stubbed
    (same pattern as test_osFileIndexer_archive_lifecycle)."""
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
                "fileIndexer_s3_delete_versions_under_test", os.path.abspath(_FILE_INDEXER_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


BUCKET_NAME = "bucket"
KEY = "a1/file.txt"
ASSET = {"assetId": "a1", "assetName": "Part", "bucketId": "b1",
         "assetLocation": {"Key": "a1/"}}
BUCKET = {"bucketId": "b1", "bucketName": BUCKET_NAME, "baseAssetsPrefix": ""}
METADATA = {"assetid": "a1", "databaseid": "db1", "vams-primarytype": "model", "author": "x"}

# Version histories of KEY, newest first. An id beginning "dm" is a delete marker.
STACKED = [f"dm{n}" for n in range(10, 0, -1)] + ["v1"]
ARCHIVED = ["dm1", "v1"]
ARCHIVED_OVER_VERSIONS = ["dm1", "v2", "v1"]
MARKERS_ONLY = ["dm2", "dm1"]
LIVE = ["v2", "dm1", "v1"]


def _is_marker(version_id):
    return version_id.startswith("dm")


def _client_error(code, op="HeadObject"):
    return ClientError({"Error": {"Code": code}}, op)


def _fake_s3(history):
    """An S3 client over the version history of KEY, given newest first.

    ListObjectVersions honours ``MaxKeys``, which caps entries rather than keys, and reports
    ``IsTruncated`` when the cap cuts the history.
    """
    client = MagicMock()

    def last_modified(version_id):
        return datetime(2026, 9, 1, 0, 0, len(history) - history.index(version_id),
                        tzinfo=timezone.utc)

    def head(version_id):
        return {
            "ContentLength": 7,
            "LastModified": last_modified(version_id),
            "ETag": f'"etag-{version_id}"',
            "VersionId": version_id,
            "ContentType": "model/step",
            "Metadata": dict(METADATA),
        }

    def head_object(Bucket, Key, VersionId=None):
        if Key != KEY:
            raise _client_error("404")
        if VersionId is None:
            if not history or _is_marker(history[0]):
                raise _client_error("404")
            return head(history[0])
        if VersionId not in history:
            raise _client_error("404")
        if _is_marker(VersionId):
            raise _client_error("405")
        return head(VersionId)

    def list_object_versions(Bucket, Prefix, MaxKeys=None, **_):
        rows = []
        if KEY.startswith(Prefix):
            rows = [{"Key": KEY, "VersionId": version_id, "IsLatest": position == 0,
                     "LastModified": last_modified(version_id)}
                    for position, version_id in enumerate(history)]
        truncated = MaxKeys is not None and len(rows) > MaxKeys
        if MaxKeys is not None:
            rows = rows[:MaxKeys]
        return {
            "Versions": [dict(row, Size=7, ETag=f'"etag-{row["VersionId"]}"')
                         for row in rows if not _is_marker(row["VersionId"])],
            "DeleteMarkers": [row for row in rows if _is_marker(row["VersionId"])],
            "IsTruncated": truncated,
        }

    client.head_object.side_effect = head_object
    client.list_object_versions.side_effect = list_object_versions
    return client


def _delete_event():
    return {
        "eventName": "ObjectRemoved:DeleteMarkerCreated",
        "s3": {"bucket": {"name": BUCKET_NAME}, "object": {"key": KEY}},
    }


def _run(m, history, asset_is_archived=False):
    """Run handle_s3_notification on a delete event for KEY over `history`.

    Returns (result, run), where run carries the fake client, the listing spy and the stubs.
    """
    client = _fake_s3(history)
    listing = MagicMock(wraps=sys.modules["common.s3"].list_all_object_versions)
    index = MagicMock(return_value=True)
    delete_one = MagicMock(return_value=True)
    delete_by_path = MagicMock(return_value=1)
    process = MagicMock(return_value=m.IndexOperationResponse(
        success=True, message="ok", indexName="idx", operation="index"))
    # The stored-location reads of the permanent-delete branch find no record
    tables = MagicMock()
    tables.query.return_value = {"Items": []}
    with ExitStack() as stack:
        for name, value in (
            ("s3_client", client),
            ("list_all_object_versions", listing),
            ("get_asset_details_any_state", MagicMock(return_value=(ASSET, asset_is_archived))),
            ("get_bucket_details", MagicMock(return_value=BUCKET)),
            ("get_file_metadata", MagicMock(return_value=({}, {}))),
            ("find_preview_file_key", MagicMock(return_value="")),
            ("index_file_document", index),
            ("delete_file_document", delete_one),
            ("delete_file_documents_by_asset_and_path", delete_by_path),
            ("resolve_registered_bucket_prefix", MagicMock(return_value="")),
            ("lookup_database_id_for_permanent_delete", MagicMock(return_value=("db1", True))),
            ("process_file_index_request", process),
            ("asset_storage_table", tables),
            ("s3_asset_buckets_table", tables),
        ):
            stack.enter_context(patch.object(m, name, value))
        result = m.handle_s3_notification(_delete_event())
    return result, types.SimpleNamespace(client=client, listing=listing, index=index,
                                         delete_one=delete_one, delete_by_path=delete_by_path,
                                         process=process)


@pytest.mark.unit
class TestDeleteEventReadsAFullPageOfVersionHistory:
    def test_archived_file_under_ten_delete_markers_stays_indexed_as_archived(self, fileIndexer):
        result, run = _run(fileIndexer, STACKED)
        assert run.index.call_count == 1, \
            "the archived file's document was removed as permanently deleted, not indexed as archived"
        document = run.index.call_args.args[0]
        assert document.bool_archived is True
        assert document.str_s3_version_id == "v1"
        assert run.delete_one.call_count == 0
        assert run.delete_by_path.call_count == 0
        assert result.success is True and result.operation == "index"
        # The history is read through the shared paging helper, one S3_VERSIONS_PAGE_SIZE page,
        # with the handler's client
        signature = inspect.signature(sys.modules["common.s3"].list_all_object_versions)
        listed = [signature.bind(*c.args, **c.kwargs).arguments for c in run.listing.call_args_list]
        assert [(a["bucket"], a["prefix"], a.get("client"), a.get("max_keys")) for a in listed] == \
            [(BUCKET_NAME, KEY, run.client, sys.modules["common.s3"].S3_VERSIONS_PAGE_SIZE)]
        capped = [c.kwargs["MaxKeys"] for c in run.client.list_object_versions.call_args_list
                  if c.kwargs.get("MaxKeys") is not None and c.kwargs["MaxKeys"] < len(STACKED)]
        assert capped == [], "the delete branch read a capped ListObjectVersions page"


@pytest.mark.unit
class TestArchivedDocumentKeepsS3Metadata:
    def test_archived_document_carries_the_versions_searchable_s3_metadata(self, fileIndexer):
        result, run = _run(fileIndexer, ARCHIVED)
        assert run.index.call_count == 1
        document = run.index.call_args.args[0]
        assert document.bool_archived is True
        md = getattr(document, "MD_", None) or {}
        assert md.get("s3_vams-primarytype") == "model", \
            "the archived document dropped the file's S3 metadata"
        assert md.get("s3_author") == "x"
        assert "s3_assetid" not in md and "s3_databaseid" not in md
        # Described by the version the delete marker covers
        assert document.str_s3_version_id == "v1"
        assert document.num_filesize == 7
        assert document.str_etag == "etag-v1"
        assert document.date_lastmodified == datetime(2026, 9, 1, 0, 0, 1, tzinfo=timezone.utc).isoformat()
        assert result.success is True and result.operation == "index"


@pytest.mark.unit
class TestDeleteEventClassificationControls:
    def test_marker_over_versions_is_indexed_archived_from_the_newest_version(self, fileIndexer):
        result, run = _run(fileIndexer, ARCHIVED_OVER_VERSIONS)
        assert run.index.call_count == 1
        document = run.index.call_args.args[0]
        assert document.bool_archived is True
        assert document.str_s3_version_id == "v2"
        assert run.delete_one.call_count == 0 and run.delete_by_path.call_count == 0
        assert result.operation == "index"

    def test_markers_without_a_version_are_a_permanent_delete(self, fileIndexer):
        result, run = _run(fileIndexer, MARKERS_ONLY)
        assert run.index.call_count == 0
        assert run.delete_one.call_args_list == [call("db1", "a1", "/file.txt")]
        assert result.operation == "delete"

    def test_live_current_version_is_reindexed_live(self, fileIndexer):
        """The live object outranks an asset record still in the archived partition."""
        result, run = _run(fileIndexer, LIVE, asset_is_archived=True)
        assert run.process.call_count == 1
        request = run.process.call_args.args[0]
        assert request.isArchived is False
        assert request.operation == "index"
        assert request.filePath == "/file.txt" and request.s3Key == KEY
        assert run.index.call_count == 0
        assert run.delete_one.call_count == 0 and run.delete_by_path.call_count == 0
