# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A create event whose object no longer exists is finished, not failed.

S3 delivers `ObjectCreated:*` and `ObjectRemoved:*` events independently, so a file
copied and then moved or deleted within seconds can reach the file indexer after it
is gone. Its `HeadObject` then answers 404 on every receive: reporting the record as
failed only redrives it until the source queue dead-letters it, which raises the
indexer DLQ alarm for a search index that is already correct. The object's own
removal event is what updates its search document, so these tests also run that
event after the skipped create and check the document is gone.
"""

import importlib.util
import json
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
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing", "fileIndexer.py"
)

BUCKET = "asset-bucket"
ASSET_ID = "x8aa59cfe-0000-4000-8000-000000000001"
DATABASE_ID = "db1"
S3_METADATA = {"assetid": ASSET_ID, "databaseid": DATABASE_ID}


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
                "fileIndexer_stale_create_under_test", os.path.abspath(_FILE_INDEXER_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


def _client_error(code, op="HeadObject"):
    return ClientError({"Error": {"Code": code}}, op)


class FakeVersionedBucket:
    """The S3 reads the file indexer makes, answered from per-key version stacks.

    Each key holds its versions oldest first; the last entry is the current one. A
    current delete marker makes HeadObject without a VersionId answer 404, as S3 does.
    """

    def __init__(self):
        self.keys = {}
        self._next_version = 0

    def _version_id(self):
        self._next_version += 1
        return f"v{self._next_version}"

    def put(self, key, metadata=S3_METADATA):
        self.keys.setdefault(key, []).append(
            {"VersionId": self._version_id(), "IsDeleteMarker": False, "Metadata": dict(metadata)})

    def archive(self, key):
        self.keys[key].append({"VersionId": self._version_id(), "IsDeleteMarker": True})

    def purge(self, key):
        self.keys.pop(key, None)

    def head_object(self, Bucket, Key, VersionId=None):
        stack = self.keys.get(Key, [])
        if VersionId is not None:
            entry = next((v for v in stack if v["VersionId"] == VersionId), None)
            if entry is None:
                raise _client_error("404")
            if entry["IsDeleteMarker"]:
                raise _client_error("405")
        else:
            if not stack or stack[-1]["IsDeleteMarker"]:
                raise _client_error("404")
            entry = stack[-1]
        return {
            "Metadata": dict(entry["Metadata"]),
            "ContentLength": 5,
            "LastModified": datetime(2026, 11, 12, tzinfo=timezone.utc),
            "ETag": '"etag"',
            "VersionId": entry["VersionId"],
        }

    def list_object_versions(self, Bucket, Prefix, MaxKeys=1000, **kwargs):
        versions, markers = [], []
        for key in sorted(k for k in self.keys if k.startswith(Prefix)):
            stack = self.keys[key]
            for position, entry in reversed(list(enumerate(stack))):
                row = {
                    "Key": key,
                    "VersionId": entry["VersionId"],
                    "IsLatest": position == len(stack) - 1,
                    "LastModified": datetime(2026, 11, 12, 0, 0, position, tzinfo=timezone.utc),
                }
                (markers if entry["IsDeleteMarker"] else versions).append(row)
        return {"Versions": versions[:MaxKeys], "DeleteMarkers": markers[:MaxKeys], "IsTruncated": False}

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000, **kwargs):
        return {"Contents": []}


@pytest.fixture
def harness(fileIndexer):
    """The indexer wired to a fake versioned bucket and an in-memory file index.

    The index maps (databaseId, assetId, filePath) to the document's archived flag; it
    is written only through index_file_document and delete_file_document, the module's
    two OpenSearch writes on these paths.
    """
    bucket = FakeVersionedBucket()
    index = {}

    def index_doc(document):
        index[(document.str_databaseid, document.str_assetid, document.str_key)] = document.bool_archived
        return True

    def delete_doc(database_id, asset_id, file_path):
        index.pop((database_id, asset_id, file_path), None)
        return True

    fileIndexer.s3_client = bucket
    fileIndexer.index_file_document = MagicMock(side_effect=index_doc)
    fileIndexer.delete_file_document = MagicMock(side_effect=delete_doc)
    fileIndexer.get_asset_details_any_state = MagicMock(return_value=(
        {"assetId": ASSET_ID, "assetName": "asset", "bucketId": "b1",
         "assetLocation": {"Key": f"{ASSET_ID}/"}}, False))
    fileIndexer.get_bucket_details = MagicMock(return_value={
        "bucketId": "b1", "bucketName": BUCKET, "baseAssetsPrefix": "/"})
    fileIndexer.get_file_metadata = MagicMock(return_value=({}, {}))
    fileIndexer.resolve_registered_bucket_prefix = MagicMock(return_value="")
    fileIndexer.lookup_database_id_for_permanent_delete = MagicMock(return_value=(DATABASE_ID, True))
    fileIndexer.logger = MagicMock()
    return types.SimpleNamespace(module=fileIndexer, bucket=bucket, index=index)


def _s3_record(event_name, path):
    return {
        "eventSource": "aws:s3",
        "eventName": event_name,
        "s3": {"bucket": {"name": BUCKET}, "object": {"key": f"{ASSET_ID}{path}"}},
    }


def _sns_envelope(message):
    return {"Type": "Notification", "Message": json.dumps(message)}


def _indexer_sqs_message(message_id, *s3_records):
    """One file indexer queue message as bucket sync forwards it: an SNS notification
    whose message carries one bucket-sync SQS record per S3 record."""
    forwarded = {
        "Records": [
            {"eventSource": "aws:sqs", "body": json.dumps(_sns_envelope({"Records": [record]}))}
            for record in s3_records
        ],
        "ASSET_BUCKET_NAME": BUCKET,
        "ASSET_BUCKET_PREFIX": "/",
    }
    return {"messageId": message_id, "eventSource": "aws:sqs",
            "body": json.dumps(_sns_envelope(forwarded))}


def _run(harness, *messages):
    return harness.module.lambda_handler({"Records": list(messages)}, MagicMock())


def _logged(harness, level):
    return " ".join(str(c) for c in getattr(harness.module.logger, level).call_args_list)


@pytest.mark.unit
class TestCreateEventForAMissingObject:
    @pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
    def test_is_a_skip_logged_at_info(self, harness, code):
        harness.module.s3_client = MagicMock()
        harness.module.s3_client.head_object.side_effect = _client_error(code)

        result = harness.module.handle_s3_notification(_s3_record("ObjectCreated:Copy", "/b.txt"))

        assert result.success is True
        assert result.operation == "skip"
        harness.module.index_file_document.assert_not_called()
        assert f"{ASSET_ID}/b.txt" in _logged(harness, "info")
        harness.module.logger.error.assert_not_called()
        harness.module.logger.exception.assert_not_called()

    @pytest.mark.parametrize("code", ["AccessDenied", "403", "SlowDown", "InternalError"])
    def test_any_other_s3_error_still_fails_the_record(self, harness, code):
        harness.module.s3_client = MagicMock()
        harness.module.s3_client.head_object.side_effect = _client_error(code)

        result = harness.module.handle_s3_notification(_s3_record("ObjectCreated:Put", "/b.txt"))

        assert result.success is False
        harness.module.index_file_document.assert_not_called()

    def test_an_existing_object_is_still_indexed(self, harness):
        harness.bucket.put(f"{ASSET_ID}/a.txt")

        result = harness.module.handle_s3_notification(_s3_record("ObjectCreated:Put", "/a.txt"))

        assert result.success is True and result.operation == "index"
        assert harness.index == {(DATABASE_ID, ASSET_ID, "/a.txt"): False}


@pytest.mark.unit
class TestCopyThenMove:
    """`file copy /a.txt /b.txt` then `file move /b.txt /c.txt` then `file delete /c.txt`,
    all before the indexer reads the two copy events, which arrive in one message."""

    def _after_the_operations(self, harness):
        harness.bucket.put(f"{ASSET_ID}/a.txt")
        harness.index[(DATABASE_ID, ASSET_ID, "/a.txt")] = False

    def test_the_message_is_not_reported_for_redrive(self, harness):
        self._after_the_operations(harness)

        response = _run(harness, _indexer_sqs_message(
            "m-copies",
            _s3_record("ObjectCreated:Copy", "/b.txt"),
            _s3_record("ObjectCreated:Copy", "/c.txt"),
        ))

        assert response["batchItemFailures"] == []
        assert harness.index == {(DATABASE_ID, ASSET_ID, "/a.txt"): False}

    def test_the_removal_events_that_follow_leave_only_the_source(self, harness):
        self._after_the_operations(harness)

        response = _run(
            harness,
            _indexer_sqs_message("m-copies",
                                 _s3_record("ObjectCreated:Copy", "/b.txt"),
                                 _s3_record("ObjectCreated:Copy", "/c.txt")),
            _indexer_sqs_message("m-move-source", _s3_record("ObjectRemoved:Delete", "/b.txt")),
            _indexer_sqs_message("m-delete", _s3_record("ObjectRemoved:Delete", "/c.txt")),
        )

        assert response["batchItemFailures"] == []
        assert harness.index == {(DATABASE_ID, ASSET_ID, "/a.txt"): False}


@pytest.mark.unit
class TestCreateThenDelete:
    """A file whose document is already in the index (an earlier event for the key was
    indexed) is removed by its delete event, whichever order the two events arrive in."""

    def _indexed_then_permanently_deleted(self, harness):
        harness.index[(DATABASE_ID, ASSET_ID, "/b.txt")] = False
        harness.bucket.purge(f"{ASSET_ID}/b.txt")

    def test_create_first_then_delete_removes_the_document(self, harness):
        self._indexed_then_permanently_deleted(harness)

        response = _run(
            harness,
            _indexer_sqs_message("m-create", _s3_record("ObjectCreated:Put", "/b.txt")),
            _indexer_sqs_message("m-delete", _s3_record("ObjectRemoved:Delete", "/b.txt")),
        )

        assert response["batchItemFailures"] == []
        assert harness.index == {}
        harness.module.delete_file_document.assert_called_once_with(DATABASE_ID, ASSET_ID, "/b.txt")

    def test_delete_first_then_create_does_not_bring_the_document_back(self, harness):
        self._indexed_then_permanently_deleted(harness)

        response = _run(
            harness,
            _indexer_sqs_message("m-delete", _s3_record("ObjectRemoved:Delete", "/b.txt")),
            _indexer_sqs_message("m-create", _s3_record("ObjectCreated:Put", "/b.txt")),
        )

        assert response["batchItemFailures"] == []
        assert harness.index == {}
        harness.module.index_file_document.assert_not_called()


@pytest.mark.unit
class TestCreateThenArchive:
    """A current delete marker also makes HeadObject answer 404. The skipped create
    leaves the document to the marker's own event, which indexes it as archived."""

    def test_the_marker_event_still_indexes_the_archived_document(self, harness):
        harness.bucket.put(f"{ASSET_ID}/b.txt")
        harness.bucket.archive(f"{ASSET_ID}/b.txt")

        response = _run(
            harness,
            _indexer_sqs_message("m-create", _s3_record("ObjectCreated:Put", "/b.txt")),
            _indexer_sqs_message("m-archive", _s3_record("ObjectRemoved:DeleteMarkerCreated", "/b.txt")),
        )

        assert response["batchItemFailures"] == []
        assert harness.index == {(DATABASE_ID, ASSET_ID, "/b.txt"): True}
        harness.module.index_file_document.assert_called_once()
