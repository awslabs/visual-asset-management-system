# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The file indexer attributes S3 events by asset location, not by the stamp metadata.

A file belongs to the asset whose stored location contains it, in the same bucket: the record's
binding from bucket sync, then the location index, then (before the index is ready) the stamp and the
first folder with the bucket and containment checks. Covered here: direct writes without a stamp,
custom-root files on create, archive and permanent delete, a stamp copied in S3 into another asset's
folder, a binding from another bucket, single-file locations, and the sync-ledger `indexedAt` write.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from tests.common.fileAttributionFakes import (
    AttributionAssetTable,
    AttributionBucketsTable,
    asset_row,
    registration,
)

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

fa = sys.modules['common.fileAttribution']
al = sys.modules['common.assetLocations']

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}

_FILE_INDEXER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "osSemanticSearch", "osFileIndexer.py"
)

BUCKET = "asset-bucket"
REGISTRATIONS = [registration("reg-1", BUCKET, "/"), registration("reg-2", "other-bucket", "/")]
CUSTOM = asset_row("db1", "custom", "reg-1", "projects/building-a/", BUCKET)
NORMAL = asset_row("db1", "a1", "reg-1", "a1/", BUCKET, indexed=False)
OTHER = asset_row("db2", "b1", "reg-1", "b1/", BUCKET, indexed=False)
SINGLE = asset_row("db2", "one", "reg-1", "projects/model.glb", BUCKET)


def _boto_client(name, *args, **kwargs):
    return _ssm_stub if name == "ssm" else MagicMock()


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
        with patch("boto3.client", side_effect=_boto_client), patch("boto3.resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location(
                "fileIndexer_location_attribution_under_test", os.path.abspath(_FILE_INDEXER_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    fa.clear_registration_cache()
    al.clear_location_cache()
    return module


def _wire(module, monkeypatch, rows, ready=False):
    monkeypatch.setattr(fa, "index_ready", lambda table_name: ready)
    module.asset_storage_table = AttributionAssetTable(rows)
    module.s3_asset_buckets_table = AttributionBucketsTable(REGISTRATIONS)
    module.s3_client = MagicMock()
    captured = {}

    def capture(request):
        captured["request"] = request
        return module.IndexOperationResponse(success=True, message="ok", indexName="idx", operation="index")

    module.process_file_index_request = capture
    module.delete_file_document = MagicMock(return_value=True)
    module.delete_file_documents_by_asset_and_path = MagicMock(return_value=0)
    module.index_file_document = MagicMock(return_value=True)
    module.get_file_metadata = MagicMock(return_value=({}, {}))
    module.reconcile_written_file_document = MagicMock(return_value=None)
    return captured


def _record(key, event="ObjectCreated:Put", binding=None, bucket=BUCKET):
    record = {"eventName": event, "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
              "ASSET_BUCKET_NAME": bucket, "ASSET_BUCKET_PREFIX": "/"}
    if binding:
        record["vamsBinding"] = binding
    return record


def _binding(database_id, asset_id, bucket_id="reg-1", relative_path="/x"):
    return {"databaseId": database_id, "assetId": asset_id, "bucketId": bucket_id,
            "relativePath": relative_path, "resolvedBy": "locationIndex"}


def _request_identity(captured):
    request = captured["request"]
    return request.databaseId, request.assetId, request.filePath


@pytest.mark.unit
class TestCreatedEvents:
    def test_a_direct_write_without_a_stamp_is_indexed_from_its_binding(self, fileIndexer, monkeypatch):
        captured = _wire(fileIndexer, monkeypatch, [CUSTOM])
        fileIndexer.s3_client.head_object.return_value = {"Metadata": {}}
        result = fileIndexer.handle_s3_notification(
            _record("projects/building-a/floor/plan.e57", binding=_binding("db1", "custom")))
        assert result.operation == "index"
        assert _request_identity(captured) == ("db1", "custom", "/floor/plan.e57")

    def test_a_custom_root_file_without_binding_is_indexed_once_the_index_answers(self, fileIndexer, monkeypatch):
        captured = _wire(fileIndexer, monkeypatch, [CUSTOM], ready=True)
        fileIndexer.s3_client.head_object.return_value = {"Metadata": {}}
        fileIndexer.handle_s3_notification(_record("projects/building-a/x.obj"))
        assert _request_identity(captured) == ("db1", "custom", "/x.obj")

    def test_a_normal_asset_is_indexed_as_before_from_its_stamp(self, fileIndexer, monkeypatch):
        captured = _wire(fileIndexer, monkeypatch, [NORMAL])
        fileIndexer.s3_client.head_object.return_value = {"Metadata": {"databaseid": "db1", "assetid": "a1"}}
        fileIndexer.handle_s3_notification(_record("a1/sub/model.glb"))
        assert _request_identity(captured) == ("db1", "a1", "/sub/model.glb")
        assert captured["request"].isArchived is False

    def test_a_file_copied_into_another_assets_folder_is_indexed_under_that_asset(self, fileIndexer, monkeypatch):
        # S3 copied the object with a1's stamp into b1's folder.
        captured = _wire(fileIndexer, monkeypatch, [NORMAL, OTHER])
        fileIndexer.s3_client.head_object.return_value = {"Metadata": {"databaseid": "db1", "assetid": "a1"}}
        fileIndexer.handle_s3_notification(_record("b1/model.glb"))
        assert _request_identity(captured) == ("db2", "b1", "/model.glb")

    def test_a_stamp_naming_an_asset_that_does_not_hold_the_key_is_not_used(self, fileIndexer, monkeypatch):
        captured = _wire(fileIndexer, monkeypatch, [NORMAL])
        fileIndexer.s3_client.head_object.return_value = {"Metadata": {"databaseid": "db1", "assetid": "a1"}}
        result = fileIndexer.handle_s3_notification(_record("loose/model.glb"))
        assert result.operation == "skip"
        assert "request" not in captured

    def test_a_binding_to_a_registration_of_another_bucket_is_not_used(self, fileIndexer, monkeypatch):
        foreign = asset_row("db9", "z9", "reg-2", "z9/", "other-bucket")
        captured = _wire(fileIndexer, monkeypatch, [foreign], ready=True)
        fileIndexer.s3_client.head_object.return_value = {"Metadata": {}}
        result = fileIndexer.handle_s3_notification(_record("z9/model.glb", binding=_binding("db9", "z9", "reg-2")))
        assert result.operation == "skip"
        assert "request" not in captured

    @pytest.mark.parametrize("key,path", [
        ("projects/model.glb", "/model.glb"),
        ("projects/model.glb/textures/a.png", "/textures/a.png"),
    ])
    def test_a_single_file_asset_indexes_its_object_and_companion_files(self, fileIndexer, monkeypatch, key, path):
        captured = _wire(fileIndexer, monkeypatch, [SINGLE])
        fileIndexer.s3_client.head_object.return_value = {"Metadata": {}}
        fileIndexer.handle_s3_notification(_record(key))
        assert _request_identity(captured) == ("db2", "one", path)

    def test_a_neighbour_of_a_single_file_asset_is_not_indexed_under_it(self, fileIndexer, monkeypatch):
        captured = _wire(fileIndexer, monkeypatch, [SINGLE], ready=True)
        fileIndexer.s3_client.head_object.return_value = {"Metadata": {"databaseid": "db2", "assetid": "one"}}
        result = fileIndexer.handle_s3_notification(_record("projects/model.glb.bak"))
        assert result.operation == "skip"
        assert "request" not in captured


@pytest.mark.unit
class TestArchiveAndDelete:
    def test_an_archived_custom_root_file_is_indexed_archived_without_a_stamp(self, fileIndexer, monkeypatch):
        _wire(fileIndexer, monkeypatch, [CUSTOM], ready=True)
        key = "projects/building-a/x.obj"
        fileIndexer.s3_client.list_object_versions.return_value = {
            "Versions": [{"Key": key, "VersionId": "v1", "LastModified": 1}],
            "DeleteMarkers": [{"Key": key, "VersionId": "m1", "IsLatest": True}],
        }
        fileIndexer.s3_client.head_object.return_value = {"Metadata": {}, "VersionId": "v1"}
        built = {}

        def build(request, *args, **kwargs):
            built["request"] = request
            return MagicMock()

        fileIndexer.build_file_document = build
        result = fileIndexer.handle_s3_notification(_record(key, event="ObjectRemoved:DeleteMarkerCreated"))
        assert result.operation == "index"
        assert (built["request"].databaseId, built["request"].assetId, built["request"].filePath) == \
            ("db1", "custom", "/x.obj")
        assert built["request"].isArchived is True

    def test_a_permanently_deleted_custom_root_file_leaves_search(self, fileIndexer, monkeypatch):
        _wire(fileIndexer, monkeypatch, [CUSTOM], ready=True)
        fileIndexer.s3_client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
        result = fileIndexer.handle_s3_notification(
            _record("projects/building-a/x.obj", event="ObjectRemoved:Delete"))
        assert result.operation == "delete"
        fileIndexer.delete_file_document.assert_any_call("db1", "custom", "/x.obj")

    def test_a_permanently_deleted_key_outside_its_first_folders_asset_deletes_none_of_its_documents(
            self, fileIndexer, monkeypatch):
        # Asset "projects" is stored elsewhere; "projects/x.obj" was never one of its files.
        elsewhere = asset_row("db1", "projects", "reg-1", "library/projects/", BUCKET, indexed=False)
        _wire(fileIndexer, monkeypatch, [elsewhere])
        fileIndexer.s3_client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
        fileIndexer.handle_s3_notification(_record("projects/x.obj", event="ObjectRemoved:Delete"))
        assert not fileIndexer.delete_file_document.called
        assert not fileIndexer.delete_file_documents_by_asset_and_path.called

    def test_a_permanently_deleted_normal_file_is_removed_as_before(self, fileIndexer, monkeypatch):
        _wire(fileIndexer, monkeypatch, [NORMAL])
        fileIndexer.s3_client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
        result = fileIndexer.handle_s3_notification(_record("a1/sub/m.glb", event="ObjectRemoved:Delete"))
        assert result.operation == "delete"
        fileIndexer.delete_file_document.assert_any_call("db1", "a1", "/sub/m.glb")


@pytest.mark.unit
class TestIndexedAt:
    def _index(self, fileIndexer, version_id="v7", update_error=None):
        fileIndexer.get_asset_details_any_state = MagicMock(return_value=(NORMAL, False))
        fileIndexer.get_bucket_details = MagicMock(return_value={"bucketId": "reg-1", "bucketName": BUCKET,
                                                                 "baseAssetsPrefix": ""})
        fileIndexer.get_file_metadata = MagicMock(return_value=({}, {}))
        fileIndexer.get_s3_file_info = MagicMock(return_value=({"versionId": version_id}, False))
        fileIndexer.build_file_document = MagicMock(return_value=MagicMock())
        fileIndexer.index_file_document = MagicMock(return_value=True)
        fileIndexer.reconcile_written_file_document = MagicMock(return_value=None)
        fileIndexer.asset_file_version_history_table = MagicMock()
        if update_error:
            fileIndexer.asset_file_version_history_table.update_item.side_effect = update_error
        request = fileIndexer.FileIndexRequest(databaseId="db1", assetId="a1", filePath="/sub/m.glb",
                                               bucketName=BUCKET, s3Key="a1/sub/m.glb", operation="index")
        return fileIndexer.process_file_index_request(request)

    def test_the_indexed_version_gets_indexed_at_on_its_ledger_row(self, fileIndexer):
        result = self._index(fileIndexer)
        assert result.success is True
        update = fileIndexer.asset_file_version_history_table.update_item
        assert update.called
        kwargs = update.call_args.kwargs
        assert kwargs["Key"] == {"databaseId:assetId:filePath": "db1:a1:/sub/m.glb", "versionId": "v7"}
        assert "indexedAt" in kwargs["UpdateExpression"]
        assert "attribute_exists" in kwargs["ConditionExpression"]

    def test_a_version_without_a_ledger_row_is_still_indexed(self, fileIndexer):
        error = ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        result = self._index(fileIndexer, update_error=error)
        assert result.success is True and result.operation == "index"

    def test_an_unversioned_object_writes_no_ledger_update(self, fileIndexer):
        result = self._index(fileIndexer, version_id="null")
        assert result.success is True
        assert not fileIndexer.asset_file_version_history_table.update_item.called
