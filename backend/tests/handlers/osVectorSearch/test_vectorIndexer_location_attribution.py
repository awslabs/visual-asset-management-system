# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The vector indexer finds a file's asset by location, so custom-root files resolve without the stamp.

A file belongs to the asset whose stored location contains it in the same bucket (the record's binding,
the location index, then before the index is ready the stamp and the first folder, each checked). An
archive or a permanent delete of a custom-root file therefore reaches that asset's vector items, a
direct write with a binding needs no stamp, and an `embedding.ready` for a single-file asset reads the
asset's own object rather than a key under it.
"""

import io
import json
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.common.fileAttributionFakes import (
    AttributionAssetTable,
    AttributionBucketsTable,
    asset_row,
    registration,
)
from tests.handlers.osVectorSearch.test_vectorIndexer_embedding_ready import DETAIL, DOCUMENT
from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, load_handler

fa = sys.modules['common.fileAttribution']
al = sys.modules['common.assetLocations']

BUCKET = "assets"
REGISTRATIONS = [registration("bucket-guid", BUCKET, "/"), registration("other-guid", "other", "/")]
CUSTOM = asset_row("db1", "custom", "bucket-guid", "projects/building-a/", BUCKET)
NORMAL = asset_row("db1", "a1", "bucket-guid", "a1/", BUCKET, indexed=False)
OTHER = asset_row("db2", "b1", "bucket-guid", "b1/", BUCKET, indexed=False)
SINGLE = asset_row("db2", "one", "bucket-guid", "projects/model.glb", BUCKET)


def _not_found():
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"},
                        "ResponseMetadata": {"HTTPStatusCode": 404, "HTTPHeaders": {}}}, "HeadObject")


def _record(event_name, key, binding=None, bucket=BUCKET):
    record = {"eventSource": "aws:s3", "eventName": event_name,
              "s3": {"bucket": {"name": bucket}, "object": {"key": key, "versionId": "v2"}}}
    if binding:
        record["vamsBinding"] = binding
    return record


@pytest.fixture
def indexer(monkeypatch):
    m = load_handler("vectorIndexer")
    m.vector_store = FakeVectorStore()
    m.s3_client = MagicMock()
    fa.clear_registration_cache()
    al.clear_location_cache()
    return m


def _wire(m, monkeypatch, rows, ready=False, head=None):
    monkeypatch.setattr(fa, "index_ready", lambda table_name: ready)
    m.asset_storage_table = AttributionAssetTable(rows)
    m.s3_asset_buckets_table = AttributionBucketsTable(REGISTRATIONS)
    if head is None:
        m.s3_client.head_object.side_effect = _not_found()
    else:
        m.s3_client.head_object.return_value = head


@pytest.mark.unit
class TestCustomRootLifecycle:
    def test_archiving_a_custom_root_file_archives_its_items(self, indexer, monkeypatch):
        _wire(indexer, monkeypatch, [CUSTOM], ready=True)
        outcome = indexer.handle_s3_record(
            _record("ObjectRemoved:DeleteMarkerCreated", "projects/building-a/floor/plan.glb"), BUCKET, "/")
        assert outcome.ok and outcome.action == "archived"
        assert ("set_archived_for_file", "db1:custom", "/floor/plan.glb", True) in indexer.vector_store.calls

    def test_permanently_deleting_a_custom_root_file_deletes_its_items(self, indexer, monkeypatch):
        _wire(indexer, monkeypatch, [CUSTOM], ready=True)
        indexer.s3_client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
        outcome = indexer.handle_s3_record(_record("ObjectRemoved:Delete", "projects/building-a/x.glb"), BUCKET, "/")
        assert outcome.ok and outcome.action == "deleted"
        assert indexer.vector_store.calls and indexer.vector_store.calls[0][:3] == ("delete_file", "db1:custom",
                                                                                   "/x.glb")

    def test_a_direct_write_with_a_binding_needs_no_stamp(self, indexer, monkeypatch):
        _wire(indexer, monkeypatch, [CUSTOM], head={"VersionId": "v2", "Metadata": {}})
        binding = {"databaseId": "db1", "assetId": "custom", "bucketId": "bucket-guid",
                   "relativePath": "/x.glb", "resolvedBy": "locationIndex"}
        outcome = indexer.handle_s3_record(_record("ObjectCreated:Put", "projects/building-a/x.glb", binding),
                                           BUCKET, "/")
        assert outcome.action == "created"
        assert ("set_archived_for_file", "db1:custom", "/x.glb", False) in indexer.vector_store.calls

    def test_an_archived_custom_root_asset_still_resolves(self, indexer, monkeypatch):
        archived = asset_row("db1", "custom", "bucket-guid", "projects/building-a/", BUCKET, archived=True)
        _wire(indexer, monkeypatch, [archived], ready=True)
        outcome = indexer.handle_s3_record(
            _record("ObjectRemoved:DeleteMarkerCreated", "projects/building-a/x.glb"), BUCKET, "/")
        assert outcome.action == "archived"
        assert ("set_archived_for_file", "db1:custom", "/x.glb", True) in indexer.vector_store.calls


@pytest.mark.unit
class TestStampAndBindingChecks:
    def test_a_file_copied_into_another_assets_folder_belongs_to_that_asset(self, indexer, monkeypatch):
        _wire(indexer, monkeypatch, [NORMAL, OTHER], head={"VersionId": "v2",
                                                          "Metadata": {"databaseid": "db1", "assetid": "a1"}})
        indexer.handle_s3_record(_record("ObjectCreated:Copy", "b1/part.glb"), BUCKET, "/")
        assert ("set_archived_for_file", "db2:b1", "/part.glb", False) in indexer.vector_store.calls
        assert not [c for c in indexer.vector_store.calls if c[1] == "db1:a1"]

    def test_a_binding_from_another_bucket_is_not_used(self, indexer, monkeypatch):
        foreign = asset_row("db9", "z9", "other-guid", "z9/", "other")
        _wire(indexer, monkeypatch, [foreign], ready=True, head={"VersionId": "v2", "Metadata": {}})
        binding = {"databaseId": "db9", "assetId": "z9", "bucketId": "other-guid",
                   "relativePath": "/x.glb", "resolvedBy": "fastPath"}
        outcome = indexer.handle_s3_record(_record("ObjectCreated:Put", "z9/x.glb", binding), BUCKET, "/")
        assert outcome.action == "ignore"
        assert indexer.vector_store.calls == []

    def test_a_normal_asset_resolves_as_before(self, indexer, monkeypatch):
        _wire(indexer, monkeypatch, [NORMAL], head={"VersionId": "v2",
                                                   "Metadata": {"databaseid": "db1", "assetid": "a1"}})
        indexer.handle_s3_record(_record("ObjectCreated:Put", "a1/models/part.glb"), BUCKET, "/")
        assert ("set_archived_for_file", "db1:a1", "/models/part.glb", False) in indexer.vector_store.calls

    def test_a_neighbour_of_a_single_file_asset_is_not_attributed(self, indexer, monkeypatch):
        _wire(indexer, monkeypatch, [SINGLE], ready=True, head={"VersionId": "v2", "Metadata": {}})
        outcome = indexer.handle_s3_record(_record("ObjectCreated:Put", "projects/model.glb.bak"), BUCKET, "/")
        assert outcome.action == "ignore"
        assert indexer.vector_store.calls == []


@pytest.mark.unit
class TestSingleFileEmbeddingReady:
    def test_embedding_ready_reads_the_single_file_assets_own_object(self, indexer, monkeypatch):
        _wire(indexer, monkeypatch, [SINGLE], head={"VersionId": "v2", "Metadata": {}})
        indexer.s3_client.head_object.side_effect = None
        indexer.s3_client.list_objects_v2.return_value = {"Contents": []}
        document = {**DOCUMENT, "databaseId": "db2", "assetId": "one", "filePath": "/model.glb"}
        detail = {**DETAIL, "databaseId": "db2", "assetId": "one", "filePath": "/model.glb"}
        indexer.s3_client.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(json.dumps(document).encode())}
        indexer.handle_embedding_ready(dict(detail))
        heads = {c.kwargs.get("Key") for c in indexer.s3_client.head_object.call_args_list}
        assert "projects/model.glb" in heads
        assert "projects/model.glb/model.glb" not in heads
