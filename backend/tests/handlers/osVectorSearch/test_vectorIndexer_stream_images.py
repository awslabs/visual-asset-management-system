# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The vector indexer after the metadata and link tables moved to NEW_AND_OLD_IMAGES streams.

The stream relay (`snsQueuing.relayed_record`) keeps a REMOVE record's `OldImage` and drops it from
INSERT and MODIFY records. The vector indexer acts on asset-table records only and reads their
`NewImage` or `Keys`, so a relayed REMOVE of a metadata, attribute or link row (now with its old
image) changes nothing, and an asset-table REMOVE resolves the asset from its keys whether or not
an old image rides along. The bucket-sync envelope it reads from the file indexer topic yields the
same S3 records as `common.indexerEvents.s3_records_from_indexer_message`, the walk the file
indexer and the compliance trigger share.
"""

import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from common.indexerEvents import s3_records_from_indexer_message
from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, load_handler


@pytest.fixture
def indexer():
    m = load_handler("vectorIndexer")
    m.vector_store = FakeVectorStore()
    m.asset_storage_table = MagicMock()
    m.asset_storage_table.get_item.return_value = {}
    m.sqs_client = MagicMock()
    return m


_SNS_QUEUING = Path(__file__).resolve().parents[3] / "backend" / "handlers" / "indexing" / "snsQueuing.py"


def _relayed(record):
    """The record as the stream relay publishes it (`snsQueuing.relayed_record`)."""
    os.environ.setdefault("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:test-indexer-topic")
    with patch("boto3.client", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location("snsQueuing_vector_images", str(_SNS_QUEUING))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module.relayed_record(record)


def _metadata_remove():
    keys = {"metadataKey": {"S": "genai_title"},
            "databaseId:assetId:filePath": {"S": "db1:a1:/pump.glb"}}
    return {"eventSource": "aws:dynamodb", "eventName": "REMOVE",
            "eventSourceARN": "arn:aws:dynamodb:us-east-1:1:table/AssetFileMetadata/stream/x",
            "dynamodb": {"Keys": keys, "OldImage": {**keys, "metadataValue": {"S": "Pump"}}}}


def _asset_remove(module):
    keys = {"databaseId": {"S": "db1"}, "assetId": {"S": "a1"}}
    return {"eventSource": "aws:dynamodb", "eventName": "REMOVE",
            "eventSourceARN": "arn:aws:dynamodb:us-east-1:1:table/" + module.asset_storage_table_name
                              + "/stream/x",
            "dynamodb": {"Keys": keys, "OldImage": {**keys, "assetName": {"S": "Pump"}}}}


@pytest.mark.unit
class TestRemoveRecordsWithAnOldImage:

    def test_a_metadata_row_remove_keeps_its_old_image_and_changes_no_vector_state(self, indexer):
        record = _relayed(_metadata_remove())
        assert "OldImage" in record["dynamodb"]
        outcome = indexer.handle_stream_record(record)
        assert outcome.ok and outcome.action == "ignore"
        assert indexer.vector_store.calls == []

    def test_an_asset_remove_resolves_the_asset_from_its_keys(self, indexer):
        outcome = indexer.handle_stream_record(_relayed(_asset_remove(indexer)))
        assert outcome.action == "asset-deleted"
        assert indexer.vector_store.calls[0][:2] == ("delete_asset", "db1:a1")


def _bucket_sync_message(key, bucket="assets", prefix="pfx/"):
    """What sqsBucketSync publishes on the file indexer topic for one S3 notification."""
    s3_event = {"Records": [{"eventSource": "aws:s3", "eventName": "ObjectCreated:Put",
                             "s3": {"bucket": {"name": bucket},
                                    "object": {"key": key, "versionId": "v2"}}}]}
    inner = {"eventSource": "aws:sqs",
             "body": json.dumps({"Type": "Notification", "Message": json.dumps(s3_event)})}
    return {"Records": [inner], "ASSET_BUCKET_NAME": bucket, "ASSET_BUCKET_PREFIX": prefix}


@pytest.mark.unit
def test_the_bucket_sync_envelope_yields_the_records_the_shared_walk_yields(indexer):
    message = _bucket_sync_message("pfx/a1/pump.glb")
    seen = []
    indexer.handle_s3_record = lambda record, bucket, prefix, time_left: (
        seen.append((record["s3"]["object"]["key"], bucket, prefix)) or MagicMock(ok=True))
    indexer._dispatch_sns_message(message, None, None, lambda: 60_000)
    shared = [(r["s3"]["object"]["key"], message["ASSET_BUCKET_NAME"], message["ASSET_BUCKET_PREFIX"])
              for r in s3_records_from_indexer_message(message)]
    assert seen == shared == [("pfx/a1/pump.glb", "assets", "pfx/")]
