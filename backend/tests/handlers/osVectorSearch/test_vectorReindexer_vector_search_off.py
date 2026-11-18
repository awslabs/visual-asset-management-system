# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""vectorReindexer with vector search off (`VECTOR_SEARCH_ENABLED=false`).

The function is deployed whenever the system GenAI metadata pipeline is on, so with vector search off it
must load without the vector index environment, build no vector store, launch the system workflow over
the existing files with `enqueue`, and refuse `clear` and `both`, which only make sense for vectors.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.handlers.osVectorSearch.vectorsearch_support import load_handler

VECTOR_ENV = ("VECTOR_INDEX_NAME", "EMBEDDING_MODEL_ID", "EMBEDDING_DIMENSIONS")


def _context(remaining_ms=600000):
    context = MagicMock()
    context.get_remaining_time_in_millis.return_value = remaining_ms
    return context


def _ref(asset_id, relative_file_key):
    return SimpleNamespace(database_id="db1", asset_id=asset_id, relative_file_key=relative_file_key,
                           bucket_name="assets", base_assets_prefix="prefix-a/",
                           s3_key=f"prefix-a/{asset_id}{relative_file_key}", bucket_id="bucket-guid")


@pytest.fixture
def vector_off(monkeypatch):
    monkeypatch.setenv("VECTOR_SEARCH_ENABLED", "false")
    for name in VECTOR_ENV:
        monkeypatch.delenv(name, raising=False)
    m = load_handler("vectorReindexer")
    m.lambda_client = MagicMock()
    m.sqs_client = MagicMock()
    m.sqs_client.send_message_batch.return_value = {"Successful": [], "Failed": []}
    m.workflow_storage_table_v2 = MagicMock()
    m.workflow_storage_table_v2.get_item.return_value = {"Item": {
        "databaseId": "GLOBAL", "workflowId": "system-genai-metadata",
        "specifiedPipelines": [{"pipelineDatabaseId": "GLOBAL", "pipelineId": "system-genai-metadata"}],
        "systemConfig": {"inputFileFilters": {"allow": ["*.glb"], "exclude": []}}}}
    m.pipeline_storage_table_v2 = MagicMock()
    m.pipeline_storage_table_v2.get_item.return_value = {"Item": {
        "databaseId": "GLOBAL", "pipelineId": "system-genai-metadata", "systemConfig": {}}}
    m.enumerate_latest_live_files = MagicMock(return_value=([_ref("a1", "/part.glb"),
                                                             _ref("a2", "/other.glb")], None))
    return m


@pytest.mark.unit
class TestVectorSearchOff:
    def test_the_module_loads_without_the_vector_index_environment_and_builds_no_vector_store(
            self, vector_off):
        assert vector_off.vector_search_enabled is False
        assert vector_off.vector_store is None

    def test_enqueue_launches_the_system_workflow_for_every_admitted_file(self, vector_off):
        result = vector_off.lambda_handler({"operation": "enqueue"}, _context())
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert (body["operation"], body["phase"], body["enqueued"], body["deleted"]) == ("enqueue", "done", 2, 0)
        sent = [json.loads(entry["MessageBody"])
                for call in vector_off.sqs_client.send_message_batch.call_args_list
                for entry in call.kwargs["Entries"]]
        assert [(m["assetId"], m["relativeFileKey"]) for m in sent] == [("a1", "/part.glb"), ("a2", "/other.glb")]
        assert "tableEmpty" not in body

    def test_an_enqueue_dry_run_sends_nothing(self, vector_off):
        result = vector_off.lambda_handler({"operation": "enqueue", "dryRun": True}, _context())
        assert json.loads(result["body"])["enqueued"] == 2
        vector_off.sqs_client.send_message_batch.assert_not_called()

    @pytest.mark.parametrize("operation", ["clear", "both"])
    def test_clear_and_both_are_refused_with_a_message_naming_enqueue(self, vector_off, operation):
        result = vector_off.lambda_handler({"operation": operation}, _context())
        assert result["statusCode"] == 400
        error = json.loads(result["body"])["error"]
        assert "vector search is not enabled on this deployment" in error
        assert "'enqueue'" in error
        vector_off.sqs_client.send_message_batch.assert_not_called()
        vector_off.enumerate_latest_live_files.assert_not_called()

    def test_a_custom_resource_both_request_is_rejected(self, vector_off):
        event = {"RequestType": "Create", "ResponseURL": "https://example.invalid",
                 "ResourceProperties": {"Operation": "both"}}
        with pytest.raises(ValueError, match="vector search is not enabled"):
            vector_off.lambda_handler(event, _context())


@pytest.mark.unit
class TestVectorSearchOn:
    def test_an_unset_flag_keeps_the_vector_behaviour(self, monkeypatch):
        monkeypatch.delenv("VECTOR_SEARCH_ENABLED", raising=False)
        m = load_handler("vectorReindexer")
        assert m.vector_search_enabled is True
        assert m.vector_store is not None
        params, error = m.validate_payload({"operation": "both"})
        assert error is None and params["operation"] == "both"

    def test_vector_search_on_still_requires_the_vector_index_environment(self, monkeypatch):
        monkeypatch.setenv("VECTOR_SEARCH_ENABLED", "true")
        monkeypatch.delenv("VECTOR_INDEX_NAME", raising=False)
        with pytest.raises(KeyError, match="VECTOR_INDEX_NAME"):
            load_handler("vectorReindexer")
