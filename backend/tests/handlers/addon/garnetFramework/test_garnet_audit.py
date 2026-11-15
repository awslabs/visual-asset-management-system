# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Garnet indexers write one audit record per entity they sync, not one per SQS batch.

The record is the maintainers' rule for bulk work (as the Physna syncs record): each entity queued to
Garnet, or that failed to queue, is its own record with its ids, so a per-database or per-asset query
finds it.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.addon.garnetFramework import (
    garnetDataIndexAsset,
    garnetDataIndexDatabase,
    garnetDataIndexFile,
)


STREAM_ARN = "arn:aws:dynamodb:us-east-1:123456789012:table/{}/stream/x"


def _sqs_record(message_id, source_arn, event_name, keys):
    message = {"eventSourceARN": source_arn, "eventName": event_name, "dynamodb": {"Keys": keys}}
    body = {"Type": "Notification", "Message": json.dumps(message)}
    return {"eventSource": "aws:sqs", "messageId": message_id, "body": json.dumps(body)}


def _wire(monkeypatch, module, event, send_results):
    recorder = MagicMock()
    monkeypatch.setattr(module, "log_action", recorder)
    monkeypatch.setattr(module, "current_audit_context", lambda: SimpleNamespace(event=event))
    monkeypatch.setattr(module, "write_outbound_sync_record", MagicMock())
    monkeypatch.setattr(module, "send_to_garnet_ingestion_queue", MagicMock(side_effect=send_results))
    return recorder


@pytest.mark.unit
class TestOneRecordPerEntity:
    def test_an_asset_batch_records_each_asset(self, monkeypatch):
        m = garnetDataIndexAsset
        arn = STREAM_ARN.format(m.asset_storage_table_name)
        event = {"Records": [
            _sqs_record("m1", arn, "REMOVE", {"databaseId": {"S": "db1"}, "assetId": {"S": "a1"}}),
            _sqs_record("m2", arn, "REMOVE", {"databaseId": {"S": "db1"}, "assetId": {"S": "a2"}}),
        ]}
        recorder = _wire(monkeypatch, m, event, [True, False])
        m.lambda_handler(event, None)
        assert recorder.call_count == 2
        first, second = recorder.call_args_list
        assert first.args == (event, "garnetAssetSync")
        assert first.kwargs["outcome"] == "success"
        assert first.kwargs["database_id"] == "db1"
        assert first.kwargs["asset_id"] == "a1"
        assert first.kwargs["details"]["operation"] == m.SYNC_ACTION_DELETE
        assert first.kwargs["details"]["entityId"] == "urn:vams:asset:db1:a1"
        assert second.args == (event, "garnetAssetSync")
        assert second.kwargs["outcome"] == "failed"
        assert second.kwargs["reason"] == "internal"
        assert second.kwargs["asset_id"] == "a2"
        assert "item_count" not in first.kwargs

    def test_a_database_batch_records_each_database(self, monkeypatch):
        m = garnetDataIndexDatabase
        arn = STREAM_ARN.format(m.database_storage_table_name)
        event = {"Records": [
            _sqs_record("m1", arn, "REMOVE", {"databaseId": {"S": "db1"}}),
            _sqs_record("m2", arn, "REMOVE", {"databaseId": {"S": "db2"}}),
        ]}
        recorder = _wire(monkeypatch, m, event, [True, True])
        m.lambda_handler(event, None)
        assert recorder.call_count == 2
        first, second = recorder.call_args_list
        assert first.args == (event, "garnetDatabaseSync")
        assert first.kwargs["database_id"] == "db1"
        assert second.kwargs["database_id"] == "db2"

    def test_a_batch_that_syncs_nothing_records_nothing(self, monkeypatch):
        m = garnetDataIndexDatabase
        event = {"Records": [{"eventSource": "aws:unknown", "messageId": "m1"}]}
        recorder = _wire(monkeypatch, m, event, [])
        m.lambda_handler(event, None)
        recorder.assert_not_called()

    def test_a_file_sync_records_the_file(self, monkeypatch):
        m = garnetDataIndexFile
        event = {"Records": []}
        recorder = _wire(monkeypatch, m, event, [])
        m._record_sync(m.SYNC_OBJECT_TYPE_ASSET_FILE, "create", True, "db1", asset_id="a1",
                       file_path="/models/part.stl", s3_version_id="v1", entity_id="urn:vams:file:x")
        recorder.assert_called_once()
        assert recorder.call_args.args == (event, "garnetFileSync")
        assert recorder.call_args.kwargs["file_path"] == "/models/part.stl"
        assert recorder.call_args.kwargs["asset_id"] == "a1"

    def test_no_record_outside_a_request(self, monkeypatch):
        m = garnetDataIndexFile
        recorder = _wire(monkeypatch, m, None, [])
        monkeypatch.setattr(m, "current_audit_context", lambda: None)
        m._record_sync(m.SYNC_OBJECT_TYPE_ASSET_FILE, "create", True, "db1", asset_id="a1")
        recorder.assert_not_called()
