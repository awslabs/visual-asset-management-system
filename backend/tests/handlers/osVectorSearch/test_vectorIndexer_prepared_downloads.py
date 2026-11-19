# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""No Download as Zip archive is indexed, embedded or analysed as a file.

Download as Zip writes each archive to the auxiliary bucket under AUXILIARY_PREPARED_DOWNLOADS_PREFIX
(``vams.preparedDownloads/{databaseId}/{assetId}/{userHash}/{requestId}/``), never into an asset, so the
asset-bucket event topics the vector indexer consumes never carry it. Two paths could still name one:

- a ``vector.embedding.ready`` event, whose document location is publisher input in the auxiliary bucket:
  a location under the prefix is dropped before the object is read (an archive is no embedding document,
  and reading it failed the record into the dead-letter queue);
- an S3 lifecycle record of the auxiliary bucket, if a deployment also registered that bucket as an asset
  bucket: an archive under the prefix is ignored before any lookup. The same key in a real asset bucket
  is an ordinary asset path (an asset id may contain a dot) and is resolved as before.

The reindexer's bucket walk needs no skip: a prepared-download key resolves to the asset whose id is its
first segment (``vams.preparedDownloads``) only, so it lists nothing unless such an asset exists, and then
the key is that asset's file.
"""

import importlib.util
import io
import json
import os
from unittest.mock import MagicMock

import pytest

from tests.handlers.osVectorSearch.test_vectorIndexer_embedding_ready import DETAIL, DOCUMENT
from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, load_handler

AUX_BUCKET = os.environ["S3_ASSET_AUXILIARY_BUCKET"]
ARCHIVE_KEY = "vams.preparedDownloads/db1/a1/3f2a9c/req-1/a1.zip"
_FILE_ENUMERATION = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "indexing", "fileEnumeration.py")


@pytest.fixture
def indexer():
    m = load_handler("vectorIndexer")
    m.vector_store = FakeVectorStore()
    m.s3_client = MagicMock()
    m.s3_client.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(b"PK\x03\x04" + b"\xff" * 64)}
    m.s3_client.head_object.return_value = {"VersionId": "v2", "Metadata": {"databaseid": "db1", "assetid": "a1"}}
    m.asset_storage_table = MagicMock()
    m.asset_storage_table.get_item.return_value = {}
    m.s3_asset_buckets_table = MagicMock()
    m.s3_asset_buckets_table.query.return_value = {"Items": []}
    return m


def _s3_record(bucket, key, event_name="ObjectCreated:CompleteMultipartUpload"):
    return {"eventSource": "aws:s3", "eventName": event_name,
            "s3": {"bucket": {"name": bucket}, "object": {"key": key, "versionId": "v1"}}}


@pytest.mark.unit
class TestEmbeddingReady:
    def test_the_prefix_is_the_shared_constant(self, indexer):
        assert indexer.AUXILIARY_PREPARED_DOWNLOADS_PREFIX == "vams.preparedDownloads/"

    def test_a_location_under_the_prepared_downloads_prefix_is_dropped_unread(self, indexer):
        outcome = indexer.handle_embedding_ready(
            {**DETAIL, "documentS3Location": f"s3://{AUX_BUCKET}/{ARCHIVE_KEY}"})
        assert (outcome.ok, outcome.action) == (True, "drop")
        assert "prepared downloads" in outcome.detail
        indexer.s3_client.get_object.assert_not_called()
        indexer.s3_client.delete_object.assert_not_called()
        indexer.s3_client.head_object.assert_not_called()
        assert indexer.vector_store.calls == []

    def test_a_document_elsewhere_in_the_auxiliary_bucket_is_still_read(self, indexer):
        # Control: the rule is the prefix, not the bucket.
        indexer.s3_client.get_object.side_effect = lambda **kw: {
            "Body": io.BytesIO(json.dumps(DOCUMENT).encode("utf-8"))}
        indexer.handle_embedding_ready(dict(DETAIL))
        indexer.s3_client.get_object.assert_called_once()


@pytest.mark.unit
class TestS3Lifecycle:
    @pytest.mark.parametrize("event_name", ["ObjectCreated:CompleteMultipartUpload", "ObjectRemoved:Delete",
                                            "ObjectRemoved:DeleteMarkerCreated"])
    def test_an_archive_in_the_auxiliary_bucket_is_ignored_before_any_lookup(self, indexer, event_name):
        outcome = indexer.handle_s3_record(_s3_record(AUX_BUCKET, ARCHIVE_KEY, event_name), AUX_BUCKET, "/")
        assert (outcome.ok, outcome.action, outcome.detail) == (True, "ignore", "prepared download")
        indexer.s3_client.head_object.assert_not_called()
        indexer.asset_storage_table.get_item.assert_not_called()
        indexer.s3_asset_buckets_table.query.assert_not_called()
        assert indexer.vector_store.calls == []

    def test_the_same_key_in_an_asset_bucket_is_resolved_as_an_asset_path(self, indexer):
        outcome = indexer.handle_s3_record(_s3_record("assets", ARCHIVE_KEY), "assets", "/")
        assert outcome.detail != "prepared download"
        assert indexer.s3_client.head_object.called


@pytest.mark.unit
class TestReindexWalk:
    def _fe(self):
        spec = importlib.util.spec_from_file_location("fileEnumeration_prepared_downloads",
                                                      os.path.abspath(_FILE_ENUMERATION))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _walk(self, asset_rows):
        fe = self._fe()
        s3 = MagicMock()
        s3.get_paginator.return_value.paginate.return_value = [{"Contents": [{"Key": ARCHIVE_KEY}]}]
        buckets = MagicMock()
        buckets.scan.return_value = {"Items": [{"bucketId": "aux-reg", "bucketName": AUX_BUCKET,
                                                "baseAssetsPrefix": "/"}]}
        assets = MagicMock()
        assets.query.return_value = {"Items": asset_rows}
        refs, token = fe.enumerate_latest_live_files(s3_client=s3, buckets_table=buckets, asset_table=assets)
        return refs, token, assets

    def test_an_auxiliary_bucket_registration_lists_no_archive(self):
        refs, token, assets = self._walk([])
        assert (refs, token) == ([], None)
        # The key resolves through the asset table under its first segment, which is no asset.
        assert assets.query.call_count == 1

    def test_only_an_asset_named_like_the_prefix_owns_such_a_key(self):
        refs, _token, _assets = self._walk([{"databaseId": "db9", "assetId": "vams.preparedDownloads"}])
        assert [(r.database_id, r.asset_id) for r in refs] == [("db9", "vams.preparedDownloads")]
