# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""vectorReindexer: `enqueue` and `both`.

Enumerated files are filtered by the system workflow's aggregate input filters (the same finite list its
upload trigger applies), sent in SendMessageBatch entries of ten, chunked per 1,000 files into
`executionGroupId`s, capped by `limit`, and never sent on a dry run. `both` deletes the whole table before
the first message is sent. A continuation token from the enumerator produces a self-invoke carrying it.
"""

import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.handlers.vectorsearch.vectorsearch_support import FakeVectorStore, load_handler

K1 = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v1"}}


def _context(remaining_ms=600000):
    context = MagicMock()
    context.get_remaining_time_in_millis.return_value = remaining_ms
    return context


def _ref(asset_id, relative_file_key, database_id="db1"):
    return SimpleNamespace(database_id=database_id, asset_id=asset_id, relative_file_key=relative_file_key,
                           bucket_name="assets", base_assets_prefix="prefix-a/",
                           s3_key=f"prefix-a/{asset_id}{relative_file_key}")


def _bodies(sqs_client):
    return [json.loads(entry["MessageBody"])
            for call in sqs_client.send_message_batch.call_args_list
            for entry in call.kwargs["Entries"]]


@pytest.fixture
def reindexer():
    m = load_handler("vectorReindexer")
    m.vector_store = FakeVectorStore(scan_pages=[(None, ([K1], None))])
    m.lambda_client = MagicMock()
    m.sqs_client = MagicMock()
    m.sqs_client.send_message_batch.return_value = {"Successful": [], "Failed": []}
    m.workflow_storage_table_v2 = MagicMock()
    m.workflow_storage_table_v2.get_item.return_value = {"Item": {
        "databaseId": "GLOBAL", "workflowId": "system-genai-metadata",
        "specifiedPipelines": [{"pipelineDatabaseId": "GLOBAL", "pipelineId": "system-genai-metadata"}],
        "systemConfig": {"inputFileFilters": {"allow": ["*.glb", "*.png"], "exclude": []}}}}
    m.pipeline_storage_table_v2 = MagicMock()
    m.pipeline_storage_table_v2.get_item.return_value = {"Item": {
        "databaseId": "GLOBAL", "pipelineId": "system-genai-metadata",
        "systemConfig": {"inputFileFilters": {"allow": ["*.glb", "*.png"], "exclude": ["*.tmp.glb"]}}}}
    m.enumerate_latest_live_files = MagicMock(return_value=([
        _ref("a1", "/part.glb"), _ref("a1", "/notes.txt"), _ref("a2", "/photo.png"),
        _ref("a2", "/scratch.tmp.glb")], None))
    return m


@pytest.mark.unit
class TestEnvironment:
    def test_the_workflow_database_id_has_no_default_and_fails_the_load(self, monkeypatch):
        # The system workflow's database id is deployment configuration, read the same fail-fast way as
        # GENAI_METADATA_WORKFLOW_ID; a silent literal would enqueue launches for an unregistered workflow.
        monkeypatch.delenv("GENAI_METADATA_WORKFLOW_DATABASE_ID")
        with pytest.raises(KeyError, match="GENAI_METADATA_WORKFLOW_DATABASE_ID"):
            load_handler("vectorReindexer")

    def test_the_workflow_database_id_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("GENAI_METADATA_WORKFLOW_DATABASE_ID", "SYSTEMDB")
        assert load_handler("vectorReindexer").system_workflow_database_id == "SYSTEMDB"


@pytest.mark.unit
class TestEnqueue:
    def test_enumerates_with_the_shared_helper_and_the_time_guard(self, reindexer):
        reindexer.lambda_handler({"operation": "enqueue", "databaseId": "db1", "startAfter": "prefix-a/a0/z"}, _context())
        kwargs = reindexer.enumerate_latest_live_files.call_args.kwargs
        assert kwargs["s3_client"] is reindexer.s3_client
        assert kwargs["buckets_table"] is reindexer.s3_asset_buckets_table
        assert kwargs["asset_table"] is reindexer.asset_storage_table
        assert kwargs["database_id"] == "db1" and kwargs["start_after"] == "prefix-a/a0/z"
        assert kwargs["min_remaining_ms"] == reindexer.MIN_REMAINING_MS and callable(kwargs["time_remaining_fn"])

    def test_only_files_the_system_workflow_admits_are_enqueued(self, reindexer):
        body = json.loads(reindexer.lambda_handler({"operation": "enqueue"}, _context())["body"])
        sent = _bodies(reindexer.sqs_client)
        assert [(m["assetId"], m["relativeFileKey"]) for m in sent] == [("a1", "/part.glb"), ("a2", "/photo.png")]
        assert body["enqueued"] == 2 and body["chunks"] == 1 and body["continued"] is False
        reindexer.workflow_storage_table_v2.get_item.assert_called_once_with(
            Key={"databaseId": "GLOBAL", "workflowId": "system-genai-metadata"})
        reindexer.pipeline_storage_table_v2.get_item.assert_called_once_with(
            Key={"databaseId": "GLOBAL", "pipelineId": "system-genai-metadata"})

    def test_message_shape_matches_the_launcher_contract(self, reindexer):
        body = json.loads(reindexer.lambda_handler({"operation": "enqueue"}, _context())["body"])
        message = _bodies(reindexer.sqs_client)[0]
        assert message == {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/part.glb",
                           "versionId": "", "reindexRunId": body["reindexRunId"], "chunk": 0}
        entries = reindexer.sqs_client.send_message_batch.call_args.kwargs
        assert entries["QueueUrl"] == "https://sqs.us-east-1.amazonaws.com/123456789012/launch-queue"
        assert [e["Id"] for e in entries["Entries"]] == ["0", "1"]

    def test_batches_of_ten_and_chunks_per_thousand(self, reindexer):
        reindexer.enumerate_latest_live_files.return_value = (
            [_ref(f"a{i}", f"/f{i}.glb") for i in range(1005)], None)
        body = json.loads(reindexer.lambda_handler({"operation": "enqueue"}, _context())["body"])
        calls = reindexer.sqs_client.send_message_batch.call_args_list
        assert len(calls) == 101 and all(len(c.kwargs["Entries"]) <= 10 for c in calls)
        chunks = [m["chunk"] for m in _bodies(reindexer.sqs_client)]
        assert chunks[999] == 0 and chunks[1000] == 1 and body["chunks"] == 2

    def test_limit_caps_the_enqueue(self, reindexer):
        body = json.loads(reindexer.lambda_handler({"operation": "enqueue", "limit": 1}, _context())["body"])
        assert body["enqueued"] == 1 and len(_bodies(reindexer.sqs_client)) == 1 and body["continued"] is False

    def test_dry_run_sends_nothing_but_counts(self, reindexer):
        body = json.loads(reindexer.lambda_handler({"operation": "enqueue", "dryRun": True}, _context())["body"])
        reindexer.sqs_client.send_message_batch.assert_not_called()
        assert body["enqueued"] == 2

    def test_enumerator_continuation_token_produces_a_self_invoke(self, reindexer):
        reindexer.enumerate_latest_live_files.return_value = ([_ref("a1", "/part.glb")], "prefix-a/a1/part.glb")
        body = json.loads(reindexer.lambda_handler({"operation": "enqueue"}, _context())["body"])
        assert body["continued"] is True and body["phase"] == "enqueue"
        payload = json.loads(reindexer.lambda_client.invoke.call_args.kwargs["Payload"])
        assert payload["continuation"]["startAfter"] == "prefix-a/a1/part.glb"
        assert payload["continuation"]["enqueued"] == 1 and "startAfter" not in payload

    def test_failed_entries_are_retried_once_then_fail_the_run(self, reindexer):
        reindexer.sqs_client.send_message_batch.side_effect = [
            {"Successful": [], "Failed": [{"Id": "1", "Code": "X", "Message": "y", "SenderFault": False}]},
            {"Successful": [], "Failed": [{"Id": "1", "Code": "X", "Message": "y", "SenderFault": False}]}]
        response = reindexer.lambda_handler({"operation": "enqueue"}, _context())
        assert response["statusCode"] == 500
        retry = reindexer.sqs_client.send_message_batch.call_args_list[1].kwargs["Entries"]
        assert [json.loads(e["MessageBody"])["assetId"] for e in retry] == ["a2"]

    def test_missing_system_workflow_row_is_a_500_without_sends(self, reindexer):
        reindexer.workflow_storage_table_v2.get_item.return_value = {}
        response = reindexer.lambda_handler({"operation": "enqueue"}, _context())
        assert response["statusCode"] == 500
        reindexer.sqs_client.send_message_batch.assert_not_called()

    def test_fileref_contract_fields(self, reindexer):
        names = {f.name for f in dataclasses.fields(reindexer.FileRef)}
        assert {"database_id", "asset_id", "relative_file_key", "bucket_name", "base_assets_prefix", "s3_key"} <= names


@pytest.mark.unit
class TestBoth:
    def test_clear_completes_before_the_first_message_is_sent(self, reindexer):
        order = []
        reindexer.vector_store.delete_keys = lambda keys: order.append("delete") or len(keys)
        reindexer.sqs_client.send_message_batch.side_effect = lambda **kw: order.append("send") or {"Successful": [], "Failed": []}
        body = json.loads(reindexer.lambda_handler({"operation": "both"}, _context())["body"])
        assert order == ["delete", "send"]
        assert (body["deleted"], body["enqueued"], body["phase"], body["tableEmpty"]) == (1, 2, "done", True)

    def test_a_clear_continuation_does_not_start_enqueue_early(self, reindexer):
        cursor = {"databaseId:assetId": {"S": "db1:a9"}, "fileVersionKey": {"S": "/z#v1"}}
        reindexer.vector_store = FakeVectorStore(scan_pages=[(None, ([K1], cursor)), (cursor, ([K1], None))])
        body = json.loads(reindexer.lambda_handler({"operation": "both"}, _context(remaining_ms=30000))["body"])
        assert body["phase"] == "clear" and body["continued"] is True
        reindexer.sqs_client.send_message_batch.assert_not_called()
        assert json.loads(reindexer.lambda_client.invoke.call_args.kwargs["Payload"])["operation"] == "both"
