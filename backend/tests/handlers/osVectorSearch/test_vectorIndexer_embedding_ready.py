# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`vector.embedding.ready` -> one vector item.

The indexer reads the embedding document from the auxiliary bucket (an event locating it anywhere else,
or an object over `EMBEDDING_DOCUMENT_MAX_BYTES`, is dropped untouched), rejects a model/dimension
mismatch, derives `isLatest` from the key's current S3 version, `isArchived` from a current delete marker
or an asset row found only in the `{db}#deleted` partition, builds the item with the five string filter
attributes and the two flags as Python bools (the store serialises them to `"true"`/`"false"`; a string
here would be a `TypeError` in `VectorItem`, which the indexer lets fail the record), and deletes the
document. A latest WHOLE-FILE document is written through `put_latest_item` (the store's transactional
put-and-flip, scoped to the file's OTHER versions) and the key's S3 state is then read again: when a newer
version landed in between, the file's latest marks are realigned to it. Every other document — an older
version, or a segment document of any version — goes through the plain `put_item`, with no sibling query.
An empty `bucketId` in the event is resolved from the asset row (the pipeline itself never emits one); an
empty `versionId` (unversioned bucket) is stored as `null`.

A document is the file version's whole-file vector or one of its segment vectors. The six segment fields
are read from the Detail, then the document, then default to the whole-file values; an unknown kind, an
over-long key, a key carrying the sort-key separator, a kind that disagrees with its key, or a sort key
that would exceed `SORT_KEY_MAX_BYTES` is dropped before any write, and so is a document whose values
`VectorItem.__post_init__` itself refuses with a `ValueError`. A whole-file document additionally sweeps
the version's segment items of other runs (`delete_other_run_segments`); a segment document never does.
"""

import io
import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, load_handler, seeded_item

# The auxiliary bucket as common.resourceNames resolves it in tests (the root conftest's env override).
AUX_BUCKET = os.environ["S3_ASSET_AUXILIARY_BUCKET"]
DOCUMENT_KEY = "db1/a1/temp/embedding/abc.json"
WHOLE_FILE_SEGMENT = {"segmentKey": "", "segmentKind": "none", "segmentLabel": "",
                      "segmentStartMs": None, "segmentEndMs": None, "segmentCount": 0}
VIDEO_SEGMENT = {"segmentKey": "t0000083456", "segmentKind": "videoTime",
                 "segmentLabel": "00:01:23.456–00:01:33.456",
                 "segmentStartMs": 83456, "segmentEndMs": 93456, "segmentCount": 12}
TEXT_CHUNK = {"segmentKey": "c000012", "segmentKind": "textChunk", "segmentLabel": "chunk 12/200 · page 7",
              "segmentStartMs": None, "segmentEndMs": None, "segmentCount": 200}
DETAIL = {
    "schemaVersion": 1, "databaseId": "db1", "assetId": "a1", "filePath": "/models/part.glb",
    "versionId": "v2", "contentEtag": "etag-2", "bucketId": "bucket-guid", "fileClass": "mesh",
    "fileExt": "glb", "fileSize": 1234, "contentType": "model/gltf-binary",
    "embeddingModelId": "amazon.titan-embed-text-v2:0", "embeddingDimensions": 4,
    "analysisModelId": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "sourceModalities": ["render", "text"], "pipelineExecutionId": "pe-1",
    "workflowExecutionId": "we-1", "generatedAt": "2026-09-08T00:00:00+00:00",
    **WHOLE_FILE_SEGMENT,
    "documentS3Location": f"s3://{AUX_BUCKET}/{DOCUMENT_KEY}",
}
DOCUMENT = {**DETAIL, "embedding": [0.1234567891234, 0.2, 0.3, 0.4], "sourceText": "a bracket"}


def _serve_document(indexer, document):
    """Answer every get_object with a fresh body of `document` (a BytesIO is consumed by one read)."""
    indexer.s3_client.get_object.side_effect = lambda **kw: {
        "Body": io.BytesIO(json.dumps(document).encode("utf-8"))}


def _not_found(delete_marker=False):
    headers = {"x-amz-delete-marker": "true"} if delete_marker else {}
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"},
                        "ResponseMetadata": {"HTTPStatusCode": 404, "HTTPHeaders": headers}}, "HeadObject")


@pytest.fixture
def indexer():
    m = load_handler("vectorIndexer")
    m.vector_store = FakeVectorStore()
    m.s3_client = MagicMock()
    m.s3_client.get_object.return_value = {"Body": io.BytesIO(json.dumps(DOCUMENT).encode("utf-8"))}
    m.s3_client.head_object.return_value = {"VersionId": "v2", "Metadata": {}}
    m.s3_client.list_objects_v2.return_value = {"Contents": []}
    m.asset_storage_table = MagicMock()
    m.asset_storage_table.get_item.side_effect = lambda **kw: (
        {"Item": {"databaseId": "db1", "assetId": "a1", "bucketId": "bucket-guid",
                  "assetLocation": {"Key": "prefix-a/a1/"}}}
        if kw["Key"]["databaseId"] == "db1" else {})
    m.s3_asset_buckets_table = MagicMock()
    m.s3_asset_buckets_table.query.return_value = {
        "Items": [{"bucketId": "bucket-guid", "bucketName": "assets", "baseAssetsPrefix": "prefix-a/"}]}
    return m


@pytest.mark.unit
class TestDetailKeySet:
    """Cross-WP fixture pin: `DETAIL` is exactly the Detail key set the system pipeline's publisher test
    asserts on its side (`generateEmbedding` + `WHOLE_FILE_SEGMENT_FIELDS`). A key added or renamed in
    either fixture fails one of the two suites rather than drifting silently in both."""

    def test_the_fixture_is_the_registry_detail_key_set(self):
        # The 25 keys generateEmbedding emits (its 26-key document minus `embedding` and `sourceText`, plus
        # `documentS3Location`), the six segment fields at their whole-file defaults.
        assert set(DETAIL) == {
            "schemaVersion", "databaseId", "assetId", "filePath", "versionId", "contentEtag", "bucketId",
            "fileClass", "fileExt", "fileSize", "contentType", "embeddingModelId", "embeddingDimensions",
            "analysisModelId", "sourceModalities", "pipelineExecutionId", "workflowExecutionId",
            "generatedAt", "segmentKey", "segmentKind", "segmentLabel", "segmentStartMs", "segmentEndMs",
            "segmentCount", "documentS3Location",
        }
        assert len(DETAIL) == 25


@pytest.mark.unit
class TestLatestLiveVersion:
    def test_writes_one_item_with_string_filter_attributes_and_bool_flags(self, indexer):
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert outcome.ok and outcome.action == "put"
        assert indexer.vector_store.names() == ["put_latest_item", "delete_other_run_segments"]
        item = indexer.vector_store.items[0]
        assert (item.databaseId, item.assetId, item.filePath, item.versionId) == ("db1", "a1", "/models/part.glb", "v2")
        assert item.isLatest is True and item.isArchived is False
        assert item.fileClass == "mesh" and item.fileExt == "glb"
        assert item.embeddingModelId == "amazon.titan-embed-text-v2:0" and item.embeddingDimensions == 4
        # The seven INLINE_FILTER attributes: five strings, and the two flags as bools the store
        # serialises — a "false" string would be stored truthy, and VectorItem rejects it.
        for attr in ("databaseId", "fileClass", "fileExt", "embeddingModelId", "segmentKind"):
            assert isinstance(getattr(item, attr), str) and getattr(item, attr)
        for attr in ("isLatest", "isArchived"):
            assert isinstance(getattr(item, attr), bool)
        assert item.embedding[0] == pytest.approx(0.123456789, abs=1e-12) and len(item.embedding) == 4
        assert item.sourceText == "a bracket" and item.sourceModalities == ["render", "text"]
        assert (item.contentEtag, item.bucketId, item.fileSize, item.contentType) == ("etag-2", "bucket-guid", 1234, "model/gltf-binary")
        assert item.pipelineExecutionId == "pe-1" and item.workflowExecutionId == "we-1"
        assert item.indexedAt.endswith("+00:00")

    def test_reads_the_document_from_the_auxiliary_bucket_and_deletes_it_after_the_write(self, indexer):
        indexer.handle_embedding_ready(dict(DETAIL))
        indexer.s3_client.get_object.assert_called_once_with(Bucket=AUX_BUCKET, Key=DOCUMENT_KEY)
        indexer.s3_client.delete_object.assert_called_once_with(Bucket=AUX_BUCKET, Key=DOCUMENT_KEY)

    def test_head_object_resolves_the_object_under_the_asset_location_key(self, indexer):
        indexer.handle_embedding_ready(dict(DETAIL))
        # The state read before the write and the re-check after it, both on the same object; nothing
        # else heads an object.
        assert 1 <= indexer.s3_client.head_object.call_count <= 2
        assert {tuple(sorted(c.kwargs.items())) for c in indexer.s3_client.head_object.call_args_list} == {
            (("Bucket", "assets"), ("Key", "prefix-a/a1/models/part.glb"))}

    def test_preview_file_key_is_looked_up_beside_the_object(self, indexer):
        indexer.s3_client.list_objects_v2.return_value = {
            "Contents": [{"Key": "prefix-a/a1/models/part.glb.previewFile.png"}]}
        indexer.handle_embedding_ready(dict(DETAIL))
        assert indexer.vector_store.items[0].previewFileKey == "prefix-a/a1/models/part.glb.previewFile.png"
        indexer.s3_client.list_objects_v2.assert_called_once_with(
            Bucket="assets", Prefix="prefix-a/a1/models/part.glb.previewFile.", MaxKeys=5)

    def test_missing_extension_writes_the_none_sentinel(self, indexer):
        detail = {**DETAIL, "fileExt": "", "filePath": "/LICENSE"}
        indexer.handle_embedding_ready(detail)
        assert indexer.vector_store.items[0].fileExt == "none"

    def test_empty_bucket_id_in_the_event_is_resolved_from_the_asset_row(self, indexer):
        # An empty event bucketId (a chained-step run) falls back to the asset row's registration id.
        outcome = indexer.handle_embedding_ready({**DETAIL, "bucketId": ""})
        assert outcome.ok and outcome.action == "put"
        assert indexer.vector_store.items[0].bucketId == "bucket-guid"
        condition = indexer.s3_asset_buckets_table.query.call_args.kwargs["KeyConditionExpression"]
        assert condition._values[1] == "bucket-guid"

    def test_empty_version_id_is_stored_as_the_null_sentinel(self, indexer):
        # An unversioned bucket: the event carries `versionId: ""` and head_object returns no VersionId.
        indexer.s3_client.head_object.return_value = {"Metadata": {}}
        outcome = indexer.handle_embedding_ready({**DETAIL, "versionId": ""})
        assert outcome.ok and outcome.action == "put"
        item = indexer.vector_store.items[0]
        assert (item.versionId, item.isLatest) == ("null", True)
        assert "#null" in outcome.detail
        # The sweep receives the same stored form, never the event's "".
        assert indexer.vector_store.calls[1] == (
            "delete_other_run_segments", "db1:a1", "/models/part.glb", "null", "pe-1")


@pytest.mark.unit
class TestNotLatestAndArchived:
    def test_older_version_is_written_as_not_latest(self, indexer):
        indexer.s3_client.head_object.return_value = {"VersionId": "v3", "Metadata": {}}
        indexer.handle_embedding_ready(dict(DETAIL))
        assert indexer.vector_store.items[0].isLatest is False
        # A non-latest version is a plain put: no sibling is flipped. The whole-file sweep still runs.
        assert indexer.vector_store.names() == ["put_item", "delete_other_run_segments"]

    def test_current_delete_marker_marks_the_item_archived_and_still_latest(self, indexer):
        indexer.s3_client.head_object.side_effect = _not_found(delete_marker=True)
        indexer.s3_client.list_object_versions.return_value = {
            "Versions": [{"Key": "prefix-a/a1/models/part.glb", "VersionId": "v2", "IsLatest": False,
                          "LastModified": datetime(2026, 9, 2, tzinfo=timezone.utc)},
                         {"Key": "prefix-a/a1/models/part.glb", "VersionId": "v1", "IsLatest": False,
                          "LastModified": datetime(2026, 9, 1, tzinfo=timezone.utc)}],
            "DeleteMarkers": [{"Key": "prefix-a/a1/models/part.glb", "VersionId": "m1", "IsLatest": True}]}
        indexer.handle_embedding_ready(dict(DETAIL))
        item = indexer.vector_store.items[0]
        assert (item.isLatest, item.isArchived) == (True, True)
        assert indexer.vector_store.names() == ["put_latest_item", "delete_other_run_segments"]

    def test_asset_row_only_in_the_deleted_partition_marks_the_item_archived(self, indexer):
        indexer.asset_storage_table.get_item.side_effect = lambda **kw: (
            {"Item": {"databaseId": "db1#deleted", "assetId": "a1", "assetLocation": {"Key": "prefix-a/a1/"}}}
            if kw["Key"]["databaseId"] == "db1#deleted" else {})
        indexer.handle_embedding_ready(dict(DETAIL))
        assert indexer.vector_store.items[0].isArchived is True
        assert indexer.asset_storage_table.get_item.call_args_list[0].kwargs["Key"]["databaseId"] == "db1"


@pytest.mark.unit
class TestSegmentDocuments:
    def test_a_whole_file_document_writes_the_defaults_and_sweeps_other_runs_segments(self, indexer):
        # Seeded: two v2 chunk items an earlier run (pe0) left, the current run's (pe-1) chunk already in
        # place, and a v1 chunk of the earlier run. The sweep removes exactly the two stale v2 items; the
        # count in the outcome is derived from what the fake removed, not canned.
        store = FakeVectorStore(seed=[
            seeded_item("db1:a1", "/models/part.glb#v2#c000002", "v2", segment_kind="textChunk",
                        pipeline_execution_id="pe0"),
            seeded_item("db1:a1", "/models/part.glb#v2#c000003", "v2", segment_kind="textChunk",
                        pipeline_execution_id="pe0"),
            seeded_item("db1:a1", "/models/part.glb#v2#c000001", "v2", segment_kind="textChunk",
                        pipeline_execution_id="pe-1"),
            seeded_item("db1:a1", "/models/part.glb#v1#c000001", "v1", segment_kind="textChunk",
                        pipeline_execution_id="pe0"),
        ])
        indexer.vector_store = store
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert outcome.ok and outcome.action == "put"
        item = store.items[0]
        assert (item.segmentKey, item.segmentKind, item.segmentLabel) == ("", "none", "")
        assert (item.segmentStartMs, item.segmentEndMs, item.segmentCount) == (None, None, 0)
        assert store.names() == ["put_latest_item", "delete_other_run_segments"]
        assert store.calls[1] == ("delete_other_run_segments", "db1:a1", "/models/part.glb", "v2", "pe-1")
        # The two stale v2 chunks are gone; the current run's chunk, the v1 chunk and the whole-file item
        # just written remain.
        assert sorted(seeded["fileVersionKey"] for seeded in store.seeded) == [
            "/models/part.glb#v1#c000001", "/models/part.glb#v2", "/models/part.glb#v2#c000001"]
        assert "staleSegments=2" in outcome.detail

    def test_a_detail_without_segment_fields_gets_the_whole_file_defaults(self, indexer):
        # The plugin contract: a third-party publisher may omit the six fields.
        detail = {k: v for k, v in DETAIL.items() if k not in WHOLE_FILE_SEGMENT}
        _serve_document(indexer, {k: v for k, v in DOCUMENT.items() if k not in WHOLE_FILE_SEGMENT})
        outcome = indexer.handle_embedding_ready(detail)
        assert outcome.ok and outcome.action == "put"
        item = indexer.vector_store.items[0]
        assert (item.segmentKey, item.segmentKind, item.segmentLabel) == ("", "none", "")
        assert (item.segmentStartMs, item.segmentEndMs, item.segmentCount) == (None, None, 0)
        assert indexer.vector_store.names() == ["put_latest_item", "delete_other_run_segments"]

    def test_a_video_window_document_writes_its_fields_and_sweeps_nothing(self, indexer):
        outcome = indexer.handle_embedding_ready({**DETAIL, **VIDEO_SEGMENT})
        assert outcome.ok and outcome.action == "put"
        item = indexer.vector_store.items[0]
        assert (item.segmentKey, item.segmentKind) == ("t0000083456", "videoTime")
        assert item.segmentLabel == "00:01:23.456–00:01:33.456"
        assert (item.segmentStartMs, item.segmentEndMs, item.segmentCount) == (83456, 93456, 12)
        assert (item.filePath, item.versionId, item.isLatest) == ("/models/part.glb", "v2", True)
        # A segment document is a plain put whatever its version: the run's whole-file document, published
        # first, and the S3 ObjectCreated rule own the demotion. No sibling query, no sweep.
        assert indexer.vector_store.names() == ["put_item"]
        assert "#v2#t0000083456" in outcome.detail

    def test_an_older_versions_segment_document_is_a_plain_put_marked_not_latest(self, indexer):
        # isLatest is still computed per document; only the write path is the same for every segment.
        indexer.s3_client.head_object.return_value = {"VersionId": "v3", "Metadata": {}}
        outcome = indexer.handle_embedding_ready({**DETAIL, **VIDEO_SEGMENT})
        assert outcome.ok and outcome.action == "put"
        item = indexer.vector_store.items[0]
        assert (item.segmentKind, item.isLatest) == ("videoTime", False)
        assert indexer.vector_store.names() == ["put_item"]

    def test_a_text_chunk_document_stores_null_time_bounds(self, indexer):
        indexer.handle_embedding_ready({**DETAIL, **TEXT_CHUNK})
        item = indexer.vector_store.items[0]
        assert (item.segmentKey, item.segmentKind) == ("c000012", "textChunk")
        assert item.segmentLabel == "chunk 12/200 · page 7"
        assert (item.segmentStartMs, item.segmentEndMs, item.segmentCount) == (None, None, 200)
        assert indexer.vector_store.names() == ["put_item"]

    def test_segment_fields_absent_from_the_detail_are_read_from_the_document(self, indexer):
        detail = {k: v for k, v in DETAIL.items() if k not in WHOLE_FILE_SEGMENT}
        _serve_document(indexer, {**DOCUMENT, **TEXT_CHUNK})
        indexer.handle_embedding_ready(detail)
        item = indexer.vector_store.items[0]
        assert (item.segmentKey, item.segmentKind, item.segmentCount) == ("c000012", "textChunk", 200)
        assert indexer.vector_store.names() == ["put_item"]

    def test_a_redelivered_segment_document_issues_the_same_keyed_write_twice(self, indexer):
        _serve_document(indexer, {**DOCUMENT, **VIDEO_SEGMENT})
        first = indexer.handle_embedding_ready({**DETAIL, **VIDEO_SEGMENT})
        second = indexer.handle_embedding_ready({**DETAIL, **VIDEO_SEGMENT})
        assert first.action == second.action == "put"
        assert indexer.vector_store.names() == ["put_item", "put_item"]
        keys = [(item.filePath, item.versionId, item.segmentKey) for item in indexer.vector_store.items]
        assert keys == [("/models/part.glb", "v2", "t0000083456")] * 2
        assert indexer.s3_client.delete_object.call_count == 2


@pytest.mark.unit
class TestDocumentBoundary:
    """The document is read from, and deleted in, the auxiliary bucket only, and only up to a size cap."""

    def test_the_auxiliary_bucket_is_resolved_through_resource_names(self, indexer):
        assert indexer.aux_bucket_name == AUX_BUCKET
        assert indexer.aux_bucket_name == indexer.get_bucket_name(indexer.ResourceKeys.ASSET_AUXILIARY_BUCKET)

    def test_a_document_located_in_another_bucket_is_dropped_untouched(self, indexer):
        # The event's location is publisher input; a bucket other than the auxiliary one is never read or
        # deleted from, even one this role could reach.
        outcome = indexer.handle_embedding_ready(
            {**DETAIL, "documentS3Location": f"s3://some-asset-bucket/{DOCUMENT_KEY}"})
        assert outcome.ok and outcome.action == "drop" and "auxiliary bucket" in outcome.detail
        indexer.s3_client.get_object.assert_not_called()
        indexer.s3_client.delete_object.assert_not_called()
        indexer.s3_client.head_object.assert_not_called()
        assert indexer.vector_store.calls == []

    def test_a_document_over_the_size_cap_by_content_length_is_dropped_without_being_read(self, indexer):
        body = MagicMock()
        indexer.s3_client.get_object.side_effect = lambda **kw: {
            "ContentLength": indexer.EMBEDDING_DOCUMENT_MAX_BYTES + 1, "Body": body}
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert outcome.ok and outcome.action == "drop" and str(indexer.EMBEDDING_DOCUMENT_MAX_BYTES) in outcome.detail
        body.read.assert_not_called()
        assert indexer.vector_store.calls == []
        indexer.s3_client.delete_object.assert_not_called()

    def test_a_body_longer_than_the_cap_is_dropped_after_a_bounded_read(self, indexer):
        # No ContentLength on the response: the body itself is read one byte past the cap and refused.
        oversized = b"[" + b"0," * (indexer.EMBEDDING_DOCUMENT_MAX_BYTES // 2 + 1) + b"0]"
        assert len(oversized) > indexer.EMBEDDING_DOCUMENT_MAX_BYTES
        stream = io.BytesIO(oversized)
        indexer.s3_client.get_object.side_effect = lambda **kw: {"Body": stream}
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert outcome.ok and outcome.action == "drop"
        assert stream.tell() <= indexer.EMBEDDING_DOCUMENT_MAX_BYTES + 1
        assert indexer.vector_store.calls == []
        indexer.s3_client.delete_object.assert_not_called()

    def test_a_document_at_the_cap_is_read(self, indexer):
        # The control for the two drops above: a body of exactly the cap, with a matching ContentLength,
        # is read and written like any other.
        padded = dict(DOCUMENT)
        raw = json.dumps(padded).encode("utf-8")
        padded["sourceText"] = "x" * (indexer.EMBEDDING_DOCUMENT_MAX_BYTES - len(raw) + len(padded["sourceText"]))
        raw = json.dumps(padded).encode("utf-8")
        assert len(raw) == indexer.EMBEDDING_DOCUMENT_MAX_BYTES
        indexer.s3_client.get_object.side_effect = lambda **kw: {"ContentLength": len(raw), "Body": io.BytesIO(raw)}
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert outcome.ok and outcome.action == "put"
        assert indexer.vector_store.names() == ["put_latest_item", "delete_other_run_segments"]

    def test_a_document_that_is_not_a_json_object_is_dropped(self, indexer):
        indexer.s3_client.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(b"[1, 2, 3]")}
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert outcome.ok and outcome.action == "drop"
        assert indexer.vector_store.calls == []


@pytest.mark.unit
class TestNewerVersionRace:
    """A whole-file document of one version indexed while a newer version of the file lands: the latest
    write is re-checked against S3 and the file's marks realigned when the current version moved on."""

    def _versions_in_order(self, indexer, *version_ids):
        indexer.s3_client.head_object.side_effect = [{"VersionId": v, "Metadata": {}} for v in version_ids]

    def test_v1_indexed_after_v2_uploaded_leaves_v2_the_latest_version(self, indexer):
        # v2's whole-file item is already stored as latest (its own event was processed first). The v1
        # document reads S3 while v1 is still current, writes v1 latest (demoting v2), then re-reads S3,
        # finds v2 current, and realigns: v2 back to latest, its own v1 item not latest.
        store = FakeVectorStore(seed=[seeded_item("db1:a1", "/models/part.glb#v2", "v2", is_latest=True)])
        indexer.vector_store = store
        self._versions_in_order(indexer, "v1", "v2")
        outcome = indexer.handle_embedding_ready({**DETAIL, "versionId": "v1"})
        assert outcome.ok and outcome.action == "put"
        assert store.names() == ["put_latest_item", "set_latest_for_file_version", "delete_other_run_segments"]
        assert store.calls[1] == ("set_latest_for_file_version", "db1:a1", "/models/part.glb", "v2")
        latest = {row["fileVersionKey"]: row["isLatest"] for row in store.seeded}
        assert latest == {"/models/part.glb#v2": True, "/models/part.glb#v1": False}
        assert "realigned=2" in outcome.detail
        # The sweep is still the v1 document's own: it clears other runs' v1 segments, not v2's.
        assert store.calls[2] == ("delete_other_run_segments", "db1:a1", "/models/part.glb", "v1", "pe-1")

    def test_v1_indexed_after_v2_uploaded_but_before_v2_is_stored_demotes_itself(self, indexer):
        # v2's item has not landed yet; the realignment demotes v1 and v2's own event later writes v2 latest
        # with nothing left to demote.
        store = FakeVectorStore()
        indexer.vector_store = store
        self._versions_in_order(indexer, "v1", "v2")
        outcome = indexer.handle_embedding_ready({**DETAIL, "versionId": "v1"})
        assert outcome.ok
        assert ("set_latest_for_file_version", "db1:a1", "/models/part.glb", "v2") in store.calls
        assert {row["fileVersionKey"]: row["isLatest"] for row in store.seeded} == {"/models/part.glb#v1": False}

    def test_a_write_the_recheck_confirms_is_not_realigned(self, indexer):
        store = FakeVectorStore(seed=[seeded_item("db1:a1", "/models/part.glb#v1", "v1", is_latest=True)])
        indexer.vector_store = store
        self._versions_in_order(indexer, "v2", "v2")
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert outcome.ok and outcome.action == "put"
        assert "set_latest_for_file_version" not in store.names()
        assert {row["fileVersionKey"]: row["isLatest"] for row in store.seeded} == {
            "/models/part.glb#v1": False, "/models/part.glb#v2": True}
        assert "flipped=1" in outcome.detail and "realigned=0" in outcome.detail

    def test_a_non_latest_or_segment_write_is_not_rechecked(self, indexer):
        _serve_document(indexer, DOCUMENT)
        indexer.s3_client.head_object.return_value = {"VersionId": "v3", "Metadata": {}}
        first = indexer.handle_embedding_ready(dict(DETAIL))
        second = indexer.handle_embedding_ready({**DETAIL, **VIDEO_SEGMENT})
        assert first.action == second.action == "put"
        # One state read per document: neither a not-latest whole-file put nor a segment put re-reads S3.
        assert indexer.s3_client.head_object.call_count == 2
        assert "set_latest_for_file_version" not in indexer.vector_store.names()

    def test_a_file_deleted_between_the_write_and_the_recheck_is_left_to_the_delete_rule(self, indexer):
        store = FakeVectorStore()
        indexer.vector_store = store
        indexer.s3_client.head_object.side_effect = [{"VersionId": "v2", "Metadata": {}}, _not_found()]
        indexer.s3_client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert outcome.ok and outcome.action == "put"
        assert "set_latest_for_file_version" not in store.names()


@pytest.mark.unit
class TestRejections:
    @pytest.mark.parametrize("override", [{"embeddingModelId": "cohere.embed-english-v3"},
                                          {"embeddingDimensions": 1024}])
    def test_other_model_or_dimensions_is_dropped_without_a_write(self, indexer, override):
        outcome = indexer.handle_embedding_ready({**DETAIL, **override})
        assert outcome.ok and outcome.action == "drop"
        assert indexer.vector_store.calls == []
        indexer.s3_client.get_object.assert_not_called()

    def test_document_disagreeing_with_the_event_is_dropped(self, indexer):
        indexer.s3_client.get_object.return_value = {"Body": io.BytesIO(
            json.dumps({**DOCUMENT, "embeddingDimensions": 1024}).encode("utf-8"))}
        assert indexer.handle_embedding_ready(dict(DETAIL)).action == "drop"
        assert indexer.vector_store.calls == []

    def test_wrong_embedding_length_is_dropped(self, indexer):
        indexer.s3_client.get_object.return_value = {"Body": io.BytesIO(
            json.dumps({**DOCUMENT, "embedding": [0.1, 0.2]}).encode("utf-8"))}
        assert indexer.handle_embedding_ready(dict(DETAIL)).action == "drop"

    def test_missing_required_key_is_dropped(self, indexer):
        detail = dict(DETAIL)
        del detail["documentS3Location"]
        outcome = indexer.handle_embedding_ready(detail)
        assert outcome.ok and outcome.action == "drop" and "documentS3Location" in outcome.detail

    def test_absent_version_id_key_is_dropped_but_an_empty_one_is_not(self, indexer):
        detail = dict(DETAIL)
        del detail["versionId"]
        outcome = indexer.handle_embedding_ready(detail)
        assert outcome.ok and outcome.action == "drop" and "versionId" in outcome.detail
        assert indexer.vector_store.calls == []

    def test_bucket_id_absent_from_the_event_and_the_asset_row_is_a_failure(self, indexer):
        indexer.asset_storage_table.get_item.side_effect = lambda **kw: (
            {"Item": {"databaseId": "db1", "assetId": "a1", "assetLocation": {"Key": "prefix-a/a1/"}}}
            if kw["Key"]["databaseId"] == "db1" else {})
        outcome = indexer.handle_embedding_ready({**DETAIL, "bucketId": ""})
        assert not outcome.ok and outcome.action == "error"
        indexer.s3_asset_buckets_table.query.assert_not_called()
        assert indexer.vector_store.calls == []

    def test_asset_absent_from_both_partitions_is_dropped(self, indexer):
        indexer.asset_storage_table.get_item.side_effect = lambda **kw: {}
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert outcome.ok and outcome.action == "drop"
        assert indexer.vector_store.calls == []
        indexer.s3_client.delete_object.assert_not_called()

    def test_file_with_no_versions_left_is_dropped(self, indexer):
        indexer.s3_client.head_object.side_effect = _not_found()
        indexer.s3_client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert outcome.ok and outcome.action == "drop"
        assert indexer.vector_store.calls == []

    def test_missing_bucket_registration_is_a_failure(self, indexer):
        indexer.s3_asset_buckets_table.query.return_value = {"Items": []}
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert not outcome.ok and outcome.action == "error"

    def test_non_s3_uri_document_location_is_a_failure(self, indexer):
        outcome = indexer.handle_embedding_ready({**DETAIL, "documentS3Location": "https://x/y.json"})
        assert not outcome.ok and outcome.action == "error"

    def test_an_unknown_segment_kind_is_dropped_without_a_write(self, indexer):
        outcome = indexer.handle_embedding_ready({**DETAIL, **VIDEO_SEGMENT, "segmentKind": "pageRange"})
        assert outcome.ok and outcome.action == "drop" and "segmentKind" in outcome.detail
        assert indexer.vector_store.calls == []
        indexer.s3_client.delete_object.assert_not_called()

    def test_a_segment_key_over_32_bytes_is_dropped(self, indexer):
        outcome = indexer.handle_embedding_ready({**DETAIL, **VIDEO_SEGMENT, "segmentKey": "t" + "0" * 32})
        assert outcome.ok and outcome.action == "drop" and "segmentKey" in outcome.detail
        assert indexer.vector_store.calls == []

    def test_a_segment_key_carrying_the_sort_key_separator_is_dropped(self, indexer):
        outcome = indexer.handle_embedding_ready({**DETAIL, **VIDEO_SEGMENT, "segmentKey": "t#1"})
        assert outcome.ok and outcome.action == "drop" and "segmentKey" in outcome.detail
        assert indexer.vector_store.calls == []

    @pytest.mark.parametrize("override", [{"segmentKind": "videoTime", "segmentKey": ""},
                                          {"segmentKind": "none", "segmentKey": "t0000083456"}])
    def test_a_kind_that_disagrees_with_its_key_is_dropped(self, indexer, override):
        # An empty key on a segment kind would carry the whole-file sort key and overwrite that item.
        outcome = indexer.handle_embedding_ready({**DETAIL, **override})
        assert outcome.ok and outcome.action == "drop" and "segmentKind" in outcome.detail
        assert indexer.vector_store.calls == []

    def test_a_sort_key_over_1024_bytes_is_dropped_without_a_write(self, indexer):
        # A 1,000-byte path digests to FILE_PATH_KEY_BUDGET (926) bytes, which leaves room for a 64-byte
        # version id and a 32-byte segment key; a 100-byte version id pushes the assembled key past
        # SORT_KEY_MAX_BYTES, the builder raises, and the document is dropped before any lookup.
        long_path = "/" + "p" * 999
        segment = {**VIDEO_SEGMENT, "segmentKey": "t" + "0" * 31}
        outcome = indexer.handle_embedding_ready(
            {**DETAIL, **segment, "filePath": long_path, "versionId": "v" * 100})
        assert outcome.ok and outcome.action == "drop" and "sort key" in outcome.detail
        assert indexer.vector_store.calls == []
        indexer.asset_storage_table.get_item.assert_not_called()
        indexer.s3_client.delete_object.assert_not_called()

    def test_a_sort_key_of_exactly_1024_bytes_is_written(self, indexer):
        # The control for the drop above: the same path with a 64-byte version id fits the cap exactly,
        # which also shows the path was digested (an undigested 1,000-byte path could never fit).
        long_path = "/" + "p" * 999
        segment = {**VIDEO_SEGMENT, "segmentKey": "t" + "0" * 31}
        outcome = indexer.handle_embedding_ready(
            {**DETAIL, **segment, "filePath": long_path, "versionId": "v" * 64})
        assert outcome.ok and outcome.action == "put"
        assert indexer.vector_store.names() == ["put_item"]
        assert len(indexer.vector_store.items[0].fileVersionKey.encode("utf-8")) == 1024

    def test_a_value_the_dataclass_itself_refuses_is_dropped_without_a_write(self, indexer, monkeypatch):
        # VectorItem.__post_init__ re-checks the segment fields and the assembled sort key's length; the
        # indexer's own checks run first, so the dataclass's ValueError is reached only by standing in
        # for it. It is a drop, never a redelivery, and nothing is written or deleted. Its TypeError (a
        # non-bool flag, an unknown attribute) is an indexer fault the handler does not catch.
        def refusing(**attrs):
            raise ValueError("fileVersionKey exceeds 1024 bytes")
        monkeypatch.setattr(indexer, "VectorItem", refusing)
        outcome = indexer.handle_embedding_ready(dict(DETAIL))
        assert outcome.ok and outcome.action == "drop" and "VectorItem" in outcome.detail
        assert indexer.vector_store.calls == []
        indexer.s3_client.delete_object.assert_not_called()
