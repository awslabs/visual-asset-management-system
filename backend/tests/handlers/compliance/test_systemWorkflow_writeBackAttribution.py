# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every row a SYSTEM GenAI metadata run writes is the execution's write, so the compliance trigger
records the run as `workflowExecution` and never as a user's `metadataEdit`.

The system pipeline writes no table itself: it stages `<file>.metadata.json` (the location and the
descriptive genai_* rows), `<file>.attribute.json` (the sys_* groups with `sys_dependencies`, the
promoted ext_* facts and the genai_* provenance rows) and `asset.metadata.json` (an asset-target
run's rows with their provenance, and the asset summary run's genai_asset_* rows). The end-state step
(`processWorkflowExecutionOutput`, run for real here) writes each file back through the metadata
service with the execution named on the cross-call; the metadata service tags every row it writes
or deletes for that call (`attribute_rows_to_writing_workflow`), and the only DynamoDB writes it
makes go through that helper; the trigger reads the tag from the stream image and folds every row of
a queue batch into one `workflowExecution` stamp per asset, which starts no evaluation.
"""

import ast
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance.test_compliancePipelineReportedOutcome import (
    METADATA_PREFIX, EndState,
)
from backend.tests.handlers.compliance.test_complianceTrigger import (
    AUTO_EVAL_DB, _run, _sns_event, _stream_message,
)
from backend.tests.handlers.compliance.test_complianceTrigger_indexedChanges import (
    CHANGE_EPOCH, _stamps,
)
from backend.tests.handlers.compliance.test_complianceWorkflowCallback import EXECUTION_ID
from backend.backend.handlers.metadata import metadataService
from common.assetProvenance import (
    METADATA_ROW_DELETED_BY_WORKFLOW_ATTRIBUTE,
    METADATA_ROW_WORKFLOW_EXECUTION_ATTRIBUTE,
)
from common.s3MetadataKeys import VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION

REPO_ROOT = Path(__file__).resolve().parents[4]
SYSTEM_PIPELINE_DIR = REPO_ROOT / "backendPipelines" / "system"
GENERATE_METADATA = SYSTEM_PIPELINE_DIR / "genAiMetadata" / "lambda" / "generateMetadata.py"
METADATA_SERVICE = REPO_ROOT / "backend" / "backend" / "handlers" / "metadata" / "metadataService.py"

# The asset the end-state harness writes back into (EndState's outputDatabaseId / outputAssetId).
DB, ASSET = "db1", "asset1"
FILE = "model.glb"


def _rows(*pairs):
    return [{"metadataKey": key, "metadataValue": value, "metadataValueType": "string"}
            for key, value in pairs]


# A per-file run of the system pipeline on a single-file asset, in the shape generateMetadata writes.
FILE_METADATA_ROWS = _rows(("location", '{"lat": 47.6, "lng": -122.3}'), ("genai_title", "Pump"),
                           ("genai_description", "A centrifugal pump"), ("genai_keywords", "pump,impeller"),
                           ("genai_category", "machinery"))
FILE_ATTRIBUTE_ROWS = _rows(("sys_geometry", '{"vertexCount": 1200}'),
                            ("sys_dependencies", '{"missing": [], "references": 2}'),
                            ("ext_vertexCount", "1200"), ("genai_model", "anthropic.claude"),
                            ("genai_generated_at", "2026-03-01T12:00:00Z"))
ASSET_METADATA_ROWS = _rows(("genai_title", "Pump"), ("genai_asset_source", "file:/model.glb"),
                            ("genai_asset_source_version", "v1"),
                            ("genai_asset_generated_at", "2026-03-01T12:00:00Z"))
# The asset summary run's only output.
SUMMARY_ROWS = _rows(("genai_asset_title", "Pump assembly"), ("genai_asset_source", "aggregate:/"),
                     ("genai_asset_source_version", ""), ("genai_asset_file_count", "3"),
                     ("genai_asset_generated_at", "2026-03-01T12:00:00Z"))


def _file(rows, kind):
    return json.dumps({"type": kind, "updateType": "update", "metadata": rows}).encode("utf-8")


PER_FILE_RUN = {
    METADATA_PREFIX + FILE + ".metadata.json": _file(FILE_METADATA_ROWS, "metadata"),
    METADATA_PREFIX + FILE + ".attribute.json": _file(FILE_ATTRIBUTE_ROWS, "attribute"),
    METADATA_PREFIX + "asset.metadata.json": _file(ASSET_METADATA_ROWS, "metadata"),
}
SUMMARY_RUN = {METADATA_PREFIX + "asset.metadata.json": _file(SUMMARY_ROWS, "metadata")}


def _call_rows(call):
    return [row["metadataKey"] for row in json.loads(call["body"])["metadata"]]


def _call_table(call):
    """The table the metadata service writes a captured call's rows to."""
    body = json.loads(call["body"])
    if body.get("type") == "attribute":
        return metadataService.file_attribute_table_name
    return metadataService.asset_file_metadata_table_name


def _call_composite(call):
    body = json.loads(call["body"])
    return f"{DB}:{ASSET}:{body.get('filePath') or '/'}"


@pytest.mark.unit
class TestTheSystemPipelineWritesNoRowItself:

    def test_no_system_pipeline_module_holds_a_dynamodb_client_or_calls_the_metadata_service(self):
        sources = [p for p in SYSTEM_PIPELINE_DIR.rglob("*.py") if "tests" not in p.parts]
        assert sources
        offenders = [str(p.relative_to(REPO_ROOT)) for p in sources
                     if re.search(r"dynamodb|lambdaCrossCall|METADATA_SERVICE", p.read_text(), re.I)]
        assert offenders == []

    def test_its_output_file_names_are_the_ones_the_end_state_step_writes_back(self):
        text = GENERATE_METADATA.read_text()
        assert re.search(r'^ATTRIBUTE_FILE_SUFFIX = "\.attribute\.json"$', text, re.M)
        assert re.search(r'^METADATA_FILE_SUFFIX = "\.metadata\.json"$', text, re.M)
        assert re.search(r'^ASSET_METADATA_FILENAME = "asset\.metadata\.json"$', text, re.M)


@pytest.mark.unit
@pytest.mark.parametrize("objects,expected_calls", [
    (PER_FILE_RUN, 3), (SUMMARY_RUN, 1)], ids=["per-file-run", "asset-summary-run"])
class TestEveryOutputFileIsWrittenBackAsTheExecutions:

    def test_each_file_is_one_cross_call_naming_the_execution(self, objects, expected_calls):
        end_state = EndState(objects)
        assert end_state.status == "SUCCEEDED"
        assert len(end_state.metadata_calls) == expected_calls
        for call in end_state.metadata_calls:
            assert call["lambdaCrossCall"] == {"userName": "SYSTEM_USER",
                                               "workflowExecutionId": EXECUTION_ID}
            assert metadataService.workflow_execution_writer(call) == EXECUTION_ID

    def test_every_staged_row_travels_on_one_of_those_calls(self, objects, expected_calls):
        end_state = EndState(objects)
        sent = sorted(key for call in end_state.metadata_calls for key in _call_rows(call))
        staged = sorted(row["metadataKey"] for body in objects.values()
                        for row in json.loads(body)["metadata"])
        assert sent == staged

    def test_the_metadata_service_tags_every_row_those_calls_write(self, objects, expected_calls):
        for call in EndState(objects).metadata_calls:
            table = _call_table(call)
            batch = [{"PutRequest": {"Item": {
                "metadataKey": {"S": key},
                "databaseId:assetId:filePath": {"S": _call_composite(call)},
                "metadataValue": {"S": "v"}}}} for key in _call_rows(call)]
            # An "update" run also replaces a stale key the previous run wrote.
            batch.append({"DeleteRequest": {"Key": {
                "metadataKey": {"S": "genai_stale"},
                "databaseId:assetId:filePath": {"S": _call_composite(call)}}}})
            client = MagicMock()
            client.batch_write_item.return_value = {"UnprocessedItems": {}}
            with patch.object(metadataService, "dynamodb_client", client), \
                    patch.object(metadataService, "writing_workflow_execution_id",
                                 metadataService.workflow_execution_writer(call)):
                metadataService.batch_write_with_retry(table, batch)
            written = [request for c in client.batch_write_item.call_args_list
                       for request in c.kwargs["RequestItems"][table]]
            puts = [r["PutRequest"]["Item"] for r in written if "PutRequest" in r]
            assert len(puts) == len(_call_rows(call))
            assert all(item[METADATA_ROW_WORKFLOW_EXECUTION_ATTRIBUTE] == {"S": EXECUTION_ID}
                       for item in puts)
            mark = client.update_item.call_args.kwargs
            assert mark["ExpressionAttributeNames"]["#deletedBy"] == \
                METADATA_ROW_DELETED_BY_WORKFLOW_ATTRIBUTE
            assert mark["ExpressionAttributeValues"] == {":executionId": {"S": EXECUTION_ID}}


@pytest.mark.unit
def test_the_metadata_service_writes_its_tables_only_through_the_tagging_helper():
    """No write path of the metadata service bypasses `attribute_rows_to_writing_workflow`: its one
    BatchWriteItem is the retrying batch write that tags the batch, and its one UpdateItem is the
    delete mark. A new direct put, update or delete would let a write-back row reach the stream
    untagged, which the trigger would read as a user's `metadataEdit`."""
    tree = ast.parse(METADATA_SERVICE.read_text())
    writes = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                    node.func.attr in ("batch_write_item", "put_item", "update_item", "delete_item",
                                       "transact_write_items", "batch_writer"):
                writes.append((function.name, node.func.attr))
    assert sorted(set(writes)) == [("_mark_row_deleted_by_workflow", "update_item"),
                                   ("batch_write_with_retry", "batch_write_item")]
    source = ast.get_source_segment(METADATA_SERVICE.read_text(), next(
        f for f in ast.walk(tree)
        if isinstance(f, ast.FunctionDef) and f.name == "batch_write_with_retry"))
    assert "attribute_rows_to_writing_workflow(table_name" in source


def _tagged_record(event_name, key, file_path, epoch, removed_by_workflow=False):
    """A stream record of one row the write-back wrote (or, for a REMOVE, deleted after marking)."""
    composite = f"{DB}:{ASSET}:{file_path}"
    keys = {"metadataKey": {"S": key}, "databaseId:assetId:filePath": {"S": composite}}
    image = {**keys, "metadataValue": {"S": "v"}}
    if removed_by_workflow:
        image[METADATA_ROW_DELETED_BY_WORKFLOW_ATTRIBUTE] = {"S": EXECUTION_ID}
    else:
        image[METADATA_ROW_WORKFLOW_EXECUTION_ATTRIBUTE] = {"S": EXECUTION_ID}
    data = {"Keys": keys, "ApproximateCreationDateTime": epoch, "SequenceNumber": "1",
            "OldImage" if event_name == "REMOVE" else "NewImage": image}
    return {"eventName": event_name, "eventSource": "aws:dynamodb", "dynamodb": data}


def _run_records():
    """One queue batch (10 records, the trigger's batch size) of the per-file run's rows."""
    records = [_tagged_record("INSERT", row["metadataKey"], "/" + FILE, CHANGE_EPOCH + index)
               for index, row in enumerate(FILE_METADATA_ROWS + FILE_ATTRIBUTE_ROWS[:3])]
    records.append(_tagged_record("REMOVE", "genai_stale", "/" + FILE, CHANGE_EPOCH + 9,
                                  removed_by_workflow=True))
    records.append(_tagged_record("MODIFY", "genai_asset_source", "/", CHANGE_EPOCH + 9))
    return records


@pytest.mark.unit
class TestTheTriggerRecordsTheRunAsTheExecutions:

    def test_a_batch_of_the_runs_rows_is_one_workflow_stamp_and_no_evaluation(self):
        records = _run_records()
        assert len(records) == 10
        mocks = _run(_sns_event(*records), database_item=AUTO_EVAL_DB)
        stamps = _stamps(mocks)
        assert len(stamps) == 1
        (database_id, asset_id, source, _at, workflow_execution_id), = stamps
        assert (database_id, asset_id) == (DB, ASSET)
        assert (source, workflow_execution_id) == (VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION, EXECUTION_ID)
        mocks["run_evaluation"].assert_not_called()
        assert mocks["response"] == {"batchItemFailures": []}

    def test_the_stamps_own_asset_modify_starts_no_evaluation(self):
        mocks = _run(_sns_event(_stream_message(
            "MODIFY", database_id=DB, asset_id=ASSET,
            change_source=VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
            workflow_execution_id=EXECUTION_ID)), database_item=AUTO_EVAL_DB)
        mocks["run_evaluation"].assert_not_called()
        assert _stamps(mocks) == []
