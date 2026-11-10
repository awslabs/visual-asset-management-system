# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A write that lands after a later delete or archive leaves no document for the removed record (#789).

The indexers run in concurrent invocations with nothing ordering one record's events, and the
OpenSearch client re-sends a write that timed out. The reported sequence: the index write of an
asset's INSERT event timed out and was re-sent; meanwhile the asset was deleted (or archived) and its
REMOVE event deleted the document; the re-sent write then completed 1 ms later and recreated the
document. The fake OpenSearch client below runs the later event inside the earlier event's write,
before that write lands, which is that interleaving. The DynamoDB table and S3 are small in-memory
fakes, so the re-read the indexer makes after its write sees what the later event changed.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-asset-versions-table")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "test-links-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "serverless")

_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "osSemanticSearch")

DB = "late-db"
ASSET = "asset1"
BUCKET = "late-bucket"
FILE_PATH = "/part.glb"
S3_KEY = f"{ASSET}{FILE_PATH}"


def _boto_client(name, *args, **kwargs):
    client = MagicMock()
    if name == "ssm":
        client.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}
    return client


def _load(file_name, module_name):
    saved = {name: sys.modules.get(name) for name in ("handlers.auth", "handlers.authz")}
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["mock_token"]})
    sys.modules["handlers.auth"] = auth_stub
    try:
        with patch("boto3.client", side_effect=_boto_client), patch("boto3.resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location(module_name, os.path.abspath(os.path.join(_DIR, file_name)))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


class _Rows:
    """The asset table: rows keyed (databaseId, assetId). ``fail_consistent`` makes a strongly
    consistent read raise, as a throttled or failed read would."""

    def __init__(self):
        self.rows = {}
        self.fail_consistent = False

    def get_item(self, Key, ConsistentRead=False, **_):
        if ConsistentRead and self.fail_consistent:
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "GetItem")
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": dict(row)} if row else {}

    def put(self, database_id, asset_id):
        self.rows[(database_id, asset_id)] = {"databaseId": database_id, "assetId": asset_id,
                                              "assetName": "Asset One", "bucketId": "b1",
                                              "assetLocation": {"Key": f"{asset_id}/"}}

    def move(self, source, target):
        self.rows[target] = dict(self.rows.pop(source), databaseId=target[0])


class _Index:
    """OpenSearch documents by id. ``during_next_write`` runs inside the next index call, before the
    document is stored: the later event that completes while the earlier write is still in flight."""

    def __init__(self):
        self.docs = {}
        self.during_next_write = None

    def index(self, index=None, id=None, body=None, **_):
        hook, self.during_next_write = self.during_next_write, None
        if hook:
            hook()
        result = "updated" if id in self.docs else "created"
        self.docs[id] = body
        return {"result": result}

    def delete(self, index=None, id=None, **_):
        return {"result": "deleted" if self.docs.pop(id, None) is not None else "not_found"}


#######################
# Asset indexer
#######################


@pytest.fixture
def asset_world():
    module = _load("osAssetIndexer.py", "assetIndexer_late_write_under_test")
    rows, index = _Rows(), _Index()
    patches = [
        patch.object(module, "asset_storage_table", rows),
        patch.object(module, "get_bucket_details",
                     return_value={"bucketId": "b1", "bucketName": BUCKET, "baseAssetsPrefix": ""}),
        patch.object(module, "get_asset_metadata", return_value={}),
        patch.object(module, "get_asset_version_info", return_value={}),
        patch.object(module, "get_asset_relationship_flags", return_value={}),
        patch.object(module.opensearch_manager, "get_client", return_value=index),
        patch.object(module.opensearch_manager, "is_available", return_value=True),
    ]
    for p in patches:
        p.start()
    try:
        yield module, rows, index
    finally:
        for p in patches:
            p.stop()


def _asset_event(module, database_id, operation):
    return module.process_asset_index_request(
        module.AssetIndexRequest(databaseId=database_id, assetId=ASSET, operation=operation)
    )


@pytest.mark.unit
class TestAssetWriteLandingAfterTheRemove:
    def test_a_write_landing_after_the_delete_leaves_no_document(self, asset_world):
        module, rows, index = asset_world
        rows.put(DB, ASSET)

        def delete_the_asset():
            rows.rows.pop((DB, ASSET))
            assert _asset_event(module, DB, "delete").success

        index.during_next_write = delete_the_asset
        result = _asset_event(module, DB, "index")

        assert f"{DB}#{ASSET}" not in index.docs, "the late write recreated the deleted asset's document"
        assert index.docs == {}
        assert result.success and result.operation == "delete"

    def test_a_write_landing_after_the_archive_leaves_only_the_archived_document(self, asset_world):
        module, rows, index = asset_world
        rows.put(DB, ASSET)

        def archive_the_asset():
            # Archiving moves the row to the #deleted partition: a REMOVE of the live key and an
            # INSERT of the archived one
            rows.move((DB, ASSET), (f"{DB}#deleted", ASSET))
            assert _asset_event(module, DB, "delete").success
            assert _asset_event(module, f"{DB}#deleted", "index").success

        index.during_next_write = archive_the_asset
        _asset_event(module, DB, "index")

        assert set(index.docs) == {f"{DB}#deleted#{ASSET}"}, "the live document of an archived asset stayed"
        assert index.docs[f"{DB}#deleted#{ASSET}"]["bool_archived"] is True

    def test_a_write_landing_after_an_unarchive_leaves_only_the_live_document(self, asset_world):
        module, rows, index = asset_world
        rows.put(f"{DB}#deleted", ASSET)

        def unarchive_the_asset():
            rows.move((f"{DB}#deleted", ASSET), (DB, ASSET))
            assert _asset_event(module, f"{DB}#deleted", "delete").success
            assert _asset_event(module, DB, "index").success

        index.during_next_write = unarchive_the_asset
        _asset_event(module, f"{DB}#deleted", "index")

        assert set(index.docs) == {f"{DB}#{ASSET}"}

    def test_a_write_with_nothing_in_between_stands(self, asset_world):
        module, rows, index = asset_world
        rows.put(DB, ASSET)
        result = _asset_event(module, DB, "index")

        assert set(index.docs) == {f"{DB}#{ASSET}"}
        assert result.success and result.operation == "index"

    def test_a_failed_re_read_reports_the_record_for_redrive_and_keeps_the_document(self, asset_world):
        module, rows, index = asset_world
        rows.put(DB, ASSET)
        rows.fail_consistent = True
        result = _asset_event(module, DB, "index")

        assert result.success is False
        assert set(index.docs) == {f"{DB}#{ASSET}"}

    def test_the_redrive_of_an_event_whose_asset_is_gone_removes_its_document(self, asset_world):
        """An index event processed after its asset was removed deletes the document a late write left."""
        module, rows, index = asset_world
        index.docs[f"{DB}#{ASSET}"] = {"bool_archived": False}
        result = _asset_event(module, DB, "index")

        assert index.docs == {}
        assert result.success and result.operation == "delete"


#######################
# File indexer
#######################


class _S3:
    """One key's S3 state: 'live', 'archived' (delete marker current) or 'gone'."""

    def __init__(self):
        self.state = "live"
        self.fail = False

    def head_object(self, Bucket, Key, VersionId=None, **_):
        if self.fail:
            raise ClientError({"Error": {"Code": "503"}}, "HeadObject")
        if Key == S3_KEY and self.state == "live":
            return {"ContentLength": 10, "ETag": '"e"', "VersionId": "v1", "Metadata": {}}
        raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def list_object_versions(self, Bucket, Prefix, MaxKeys=None, **_):
        if self.state == "archived" and Prefix == S3_KEY:
            return {"DeleteMarkers": [{"Key": S3_KEY, "VersionId": "dm", "IsLatest": True}],
                    "Versions": [{"Key": S3_KEY, "VersionId": "v1"}]}
        return {}


@pytest.fixture
def file_world():
    module = _load("osFileIndexer.py", "fileIndexer_late_write_under_test")
    rows, index, s3 = _Rows(), _Index(), _S3()

    def s3_file_info(bucket_name, s3_key):
        if s3.state == "gone":
            return None, False
        return {"size": 10, "etag": "e", "versionId": "v1"}, s3.state == "archived"

    patches = [
        patch.object(module, "asset_storage_table", rows),
        patch.object(module, "s3_client", s3),
        patch.object(module, "get_bucket_details",
                     return_value={"bucketId": "b1", "bucketName": BUCKET, "baseAssetsPrefix": ""}),
        patch.object(module, "get_file_metadata", return_value=({}, {})),
        patch.object(module, "get_s3_file_info", side_effect=s3_file_info),
        patch.object(module, "find_preview_file_key", return_value=""),
        patch.object(module.opensearch_manager, "get_client", return_value=index),
        patch.object(module.opensearch_manager, "is_available", return_value=True),
    ]
    for p in patches:
        p.start()
    try:
        rows.put(DB, ASSET)
        yield module, rows, index, s3
    finally:
        for p in patches:
            p.stop()


def _file_event(module, operation="index", archived=False, recheck=True):
    request = module.FileIndexRequest(
        databaseId=DB, assetId=ASSET, filePath=FILE_PATH, bucketName=BUCKET, s3Key=S3_KEY,
        isArchived=archived, operation=operation,
    )
    if recheck:
        return module.process_file_index_request(request)
    return module.process_file_index_request(request, recheck=False)


DOC_ID = f"{DB}#{ASSET}#{FILE_PATH}"


@pytest.mark.unit
class TestFileWriteLandingAfterTheRemove:
    def test_a_write_landing_after_the_file_is_removed_leaves_no_document(self, file_world):
        module, rows, index, s3 = file_world

        def remove_the_file():
            s3.state = "gone"
            assert _file_event(module, "delete").success

        index.during_next_write = remove_the_file
        result = _file_event(module)

        assert DOC_ID not in index.docs, "the late write recreated the removed file's document"
        assert result.success and result.operation == "delete"

    def test_a_write_landing_after_the_asset_is_deleted_leaves_no_document(self, file_world):
        module, rows, index, s3 = file_world

        def delete_the_asset():
            rows.rows.clear()
            assert _file_event(module, "delete").success

        index.during_next_write = delete_the_asset
        _file_event(module)

        assert DOC_ID not in index.docs

    def test_a_live_write_landing_after_the_archive_leaves_the_document_archived(self, file_world):
        module, rows, index, s3 = file_world

        def archive_the_file():
            s3.state = "archived"
            assert _file_event(module, archived=True).success

        index.during_next_write = archive_the_file
        _file_event(module)

        assert index.docs[DOC_ID]["bool_archived"] is True, "the archived file is listed as live"

    def test_an_archived_write_landing_after_the_unarchive_leaves_the_document_live(self, file_world):
        module, rows, index, s3 = file_world
        s3.state = "archived"

        def unarchive_the_file():
            s3.state = "live"
            assert _file_event(module).success

        index.during_next_write = unarchive_the_file
        _file_event(module, archived=True)

        assert index.docs[DOC_ID]["bool_archived"] is False

    def test_a_write_with_nothing_in_between_stands(self, file_world):
        module, rows, index, s3 = file_world
        result = _file_event(module)

        assert index.docs[DOC_ID]["bool_archived"] is False
        assert result.success and result.operation == "index"

    def test_a_failed_re_read_reports_the_record_for_redrive_and_keeps_the_document(self, file_world):
        module, rows, index, s3 = file_world

        def s3_stops_answering():
            s3.fail = True

        index.during_next_write = s3_stops_answering
        result = _file_event(module)

        assert result.success is False
        assert DOC_ID in index.docs

    def test_the_rewrite_after_a_change_is_not_rechecked_again(self, file_world):
        """The correction writes once; a state still changing is reported for redrive."""
        module, rows, index, s3 = file_world

        def archive_the_file():
            s3.state = "archived"

        index.during_next_write = archive_the_file
        result = _file_event(module, recheck=False)

        assert result.success is False
