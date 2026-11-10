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

from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, load_handler

K1 = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v1"}}


def _context(remaining_ms=600000):
    context = MagicMock()
    context.get_remaining_time_in_millis.return_value = remaining_ms
    return context


def _ref(asset_id, relative_file_key, database_id="db1"):
    return SimpleNamespace(database_id=database_id, asset_id=asset_id, relative_file_key=relative_file_key,
                           bucket_name="assets", base_assets_prefix="prefix-a/",
                           s3_key=f"prefix-a/{asset_id}{relative_file_key}", bucket_id="bucket-guid")


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


def _continuation(**overrides):
    state = {"runId": "abc123def456", "phase": "enqueue", "clearStartKey": None, "startAfter": None,
             "chunk": 0, "enqueued": 0, "deleted": 0, "invocations": 1}
    state.update(overrides)
    return {"operation": "enqueue", "continuation": state}


def _failure(excinfo):
    return json.loads(str(excinfo.value))


@pytest.mark.unit
class TestFailedContinuation:
    """A continuation runs asynchronously, so a returned 500 would read as success to AWS Lambda and the run
    would stop with nothing in the dead-letter queue. It raises instead (the async config retries nothing,
    so it reaches the queue once), and the error names the payload that resumes after the last file sent."""

    def test_a_failed_continuation_raises_with_a_payload_that_resumes_it(self, reindexer):
        reindexer.workflow_storage_table_v2.get_item.return_value = {}
        token = '{"bucket": "assets", "startAfter": "prefix-a/a0/z.glb"}'
        payload = _continuation(startAfter=token, enqueued=5)
        with pytest.raises(reindexer.ReindexContinuationFailed) as excinfo:
            reindexer.lambda_handler(payload, _context())
        failure = _failure(excinfo)
        assert failure["reindexRunId"] == "abc123def456" and failure["phase"] == "enqueue"
        assert failure["error"].startswith("SystemWorkflowNotFound: ")
        assert failure["resumePayload"] == {"operation": "enqueue", "dryRun": False, "continuation": {
            **payload["continuation"], "invocations": 2}}
        reindexer.sqs_client.send_message_batch.assert_not_called()
        reindexer.lambda_client.invoke.assert_not_called()

    def test_the_first_invocation_still_answers_its_caller_with_a_500(self, reindexer):
        reindexer.workflow_storage_table_v2.get_item.return_value = {}
        response = reindexer.lambda_handler({"operation": "enqueue"}, _context())
        assert response["statusCode"] == 500 and json.loads(response["body"])["phase"] == "enqueue"

    def test_a_resume_after_a_failed_batch_sends_no_file_twice(self, reindexer):
        """SQS refuses the third batch outright: the resume payload counts the 20 files sent and its cursor
        is the 20th file's key, so resuming enumerates from there and sends only the ten that were not sent."""
        refs = [_ref(f"a{i:02d}", f"/f{i:02d}.glb") for i in range(30)]
        reindexer.enumerate_latest_live_files.return_value = (refs, None)
        ok = {"Successful": [], "Failed": []}
        reindexer.sqs_client.send_message_batch.side_effect = [ok, ok, RuntimeError("SQS unavailable")]
        with pytest.raises(reindexer.ReindexContinuationFailed) as excinfo:
            reindexer.lambda_handler(_continuation(), _context())
        resume = _failure(excinfo)["resumePayload"]
        assert resume["continuation"]["enqueued"] == 20
        assert resume["continuation"]["startAfter"] == reindexer.encode_continuation("assets", refs[19].s3_key, "bucket-guid")
        first = _bodies(reindexer.sqs_client)

        reindexer.sqs_client.send_message_batch.reset_mock(side_effect=True)
        reindexer.sqs_client.send_message_batch.return_value = ok
        reindexer.enumerate_latest_live_files.return_value = (refs[20:], None)
        body = json.loads(reindexer.lambda_handler(resume, _context())["body"])
        assert reindexer.enumerate_latest_live_files.call_args.kwargs["start_after"] == \
            resume["continuation"]["startAfter"]
        second = _bodies(reindexer.sqs_client)
        sent = [m["assetId"] for m in first[:20] + second]
        assert sent == [f"a{i:02d}" for i in range(30)], "every file once"
        assert body["enqueued"] == 30 and body["phase"] == "done" and body["reindexRunId"] == "abc123def456"

    def test_a_partly_refused_batch_resumes_at_its_first_unsent_entry(self, reindexer):
        """Entry 4 of the third batch fails twice: the files before it count as sent and the cursor stops at
        the last of them. The entries after it that SQS accepted are sent again on resume (at most nine)."""
        refs = [_ref(f"a{i:02d}", f"/f{i:02d}.glb") for i in range(30)]
        reindexer.enumerate_latest_live_files.return_value = (refs, None)
        ok = {"Successful": [], "Failed": []}
        refused = {"Successful": [], "Failed": [{"Id": "4", "Code": "X", "Message": "y", "SenderFault": False}]}
        reindexer.sqs_client.send_message_batch.side_effect = [ok, ok, refused, refused]
        with pytest.raises(reindexer.ReindexContinuationFailed) as excinfo:
            reindexer.lambda_handler(_continuation(), _context())
        resume = _failure(excinfo)["resumePayload"]
        assert resume["continuation"]["enqueued"] == 24
        assert resume["continuation"]["startAfter"] == reindexer.encode_continuation("assets", refs[23].s3_key, "bucket-guid")
        assert resume["continuation"]["chunk"] == 0


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
