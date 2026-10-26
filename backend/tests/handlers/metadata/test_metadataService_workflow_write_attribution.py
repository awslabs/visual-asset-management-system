# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A workflow execution's metadata write-back marks the rows it writes or deletes, so the compliance
trigger can tell a pipeline rule's own metadata output apart from a user change in the indexer events
and never re-evaluates the asset on it. An API request's writes are untouched: same requests, no
extra call."""

from unittest.mock import MagicMock, call, patch

import pytest

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import (
    attribute_rows_to_writing_workflow,
    batch_write_with_retry,
    workflow_execution_writer,
)

FILE_TABLE = metadataService.asset_file_metadata_table_name
ATTRIBUTE_TABLE = metadataService.file_attribute_table_name
LINK_TABLE = metadataService.asset_links_metadata_table_name
DATABASE_TABLE = metadataService.database_metadata_table_name
COMPOSITE = "db1:x0asset:/model.stl"


def _put(key):
    return {"PutRequest": {"Item": {"metadataKey": {"S": key},
                                    "databaseId:assetId:filePath": {"S": COMPOSITE},
                                    "metadataValue": {"S": "v"}}}}


def _delete(key):
    return {"DeleteRequest": {"Key": {"metadataKey": {"S": key},
                                      "databaseId:assetId:filePath": {"S": COMPOSITE}}}}


def _write(table, batch, execution_id):
    client = MagicMock()
    client.batch_write_item.return_value = {"UnprocessedItems": {}}
    with patch.object(metadataService, "dynamodb_client", client), \
            patch.object(metadataService, "writing_workflow_execution_id", execution_id):
        batch_write_with_retry(table, batch)
    return client


@pytest.mark.unit
class TestTheWriterIsReadFromTheEvent:

    @pytest.mark.parametrize("event,expected", [
        ({}, None),
        ({"lambdaCrossCall": {"userName": "SYSTEM_USER"}}, None),
        ({"lambdaCrossCall": {"userName": "SYSTEM_USER", "workflowExecutionId": "exec-1"}}, "exec-1"),
        ({"lambdaCrossCall": {"workflowExecutionId": ""}}, None),
        ({"lambdaCrossCall": "not-a-dict"}, None),
    ])
    def test_only_a_cross_call_naming_an_execution_is_a_write_back(self, event, expected):
        assert workflow_execution_writer(event) == expected

    def test_every_invocation_sets_it_afresh(self):
        with patch.object(metadataService, "request_to_claims", return_value={"tokens": []}):
            metadataService.lambda_handler(
                {"lambdaCrossCall": {"workflowExecutionId": "exec-1"}}, MagicMock())
            assert metadataService.writing_workflow_execution_id == "exec-1"
            metadataService.lambda_handler({}, MagicMock())
            assert metadataService.writing_workflow_execution_id is None


@pytest.mark.unit
class TestAnApiRequestsWritesAreUntouched:

    @pytest.mark.parametrize("table", [FILE_TABLE, ATTRIBUTE_TABLE, LINK_TABLE])
    def test_the_same_requests_and_no_extra_call(self, table):
        batch = [_put("a"), _delete("b")]
        client = _write(table, batch, None)
        assert client.batch_write_item.call_args_list == [
            call(RequestItems={table: [_put("a"), _delete("b")]})]
        client.update_item.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("table", [FILE_TABLE, ATTRIBUTE_TABLE, LINK_TABLE])
class TestAWriteBackMarksItsRows:

    def test_a_written_row_carries_the_execution(self, table):
        client = _write(table, [_put("a")], "exec-7")
        (sent,), = [c.kwargs["RequestItems"][table] for c in client.batch_write_item.call_args_list]
        assert sent["PutRequest"]["Item"]["lastChangeWorkflowExecutionId"] == {"S": "exec-7"}
        assert sent["PutRequest"]["Item"]["metadataValue"] == {"S": "v"}
        client.update_item.assert_not_called()

    def test_a_deleted_row_is_marked_before_the_delete(self, table):
        order = []
        client = MagicMock()
        client.update_item.side_effect = lambda **kw: order.append("mark")
        client.batch_write_item.side_effect = lambda **kw: order.append("write") or {
            "UnprocessedItems": {}}
        with patch.object(metadataService, "dynamodb_client", client), \
                patch.object(metadataService, "writing_workflow_execution_id", "exec-7"):
            batch_write_with_retry(table, [_delete("b")])
        assert order == ["mark", "write"]
        kwargs = client.update_item.call_args.kwargs
        assert kwargs["TableName"] == table
        assert kwargs["Key"] == _delete("b")["DeleteRequest"]["Key"]
        assert kwargs["ExpressionAttributeValues"] == {":executionId": {"S": "exec-7"}}
        assert kwargs["ExpressionAttributeNames"]["#deletedBy"] == "deletedByWorkflowExecutionId"
        assert kwargs["ConditionExpression"] == "attribute_exists(#key)"

    def test_a_failed_mark_does_not_stop_the_delete(self, table):
        client = MagicMock()
        client.update_item.side_effect = RuntimeError("throttled")
        client.batch_write_item.return_value = {"UnprocessedItems": {}}
        with patch.object(metadataService, "dynamodb_client", client), \
                patch.object(metadataService, "writing_workflow_execution_id", "exec-7"):
            batch_write_with_retry(table, [_delete("b")])
        client.batch_write_item.assert_called_once()


@pytest.mark.unit
def test_a_table_outside_the_asset_rows_is_not_marked():
    batch = [_put("a")]
    assert attribute_rows_to_writing_workflow(DATABASE_TABLE, batch) is batch
    client = _write(DATABASE_TABLE, [_put("a")], "exec-7")
    (sent,), = [c.kwargs["RequestItems"][DATABASE_TABLE]
                for c in client.batch_write_item.call_args_list]
    assert "lastChangeWorkflowExecutionId" not in sent["PutRequest"]["Item"]
