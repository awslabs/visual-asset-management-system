# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The metadata service tags every row a SYSTEM GenAI metadata run writes back.

The end-state step sends each of the run's output files as one cross-call naming the execution
(`lambdaCrossCall: {userName: SYSTEM_USER, workflowExecutionId}`;
tests/handlers/compliance/test_systemWorkflow_writeBackAttribution.py pins that shape for the per-file
run and the asset summary run). For such a call every row the service writes to the file-metadata or
file-attribute table carries the execution's id and every row it deletes is marked first, for the
genai_*, ext_*, sys_dependencies and provenance rows alike, so the compliance trigger records the run
as `workflowExecution`.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.metadata import metadataService

EXECUTION_ID = "e1000000000000000000000000000001"
CROSS_CALL_EVENT = {"lambdaCrossCall": {"userName": "SYSTEM_USER", "workflowExecutionId": EXECUTION_ID}}

# (table, composite key, row keys) for each file a run writes back.
PER_FILE_RUN = [
    (metadataService.asset_file_metadata_table_name, "db1:asset1:/model.glb",
     ["location", "genai_title", "genai_description", "genai_keywords", "genai_category"]),
    (metadataService.file_attribute_table_name, "db1:asset1:/model.glb",
     ["sys_geometry", "sys_dependencies", "ext_vertexCount", "genai_model", "genai_generated_at"]),
    (metadataService.asset_file_metadata_table_name, "db1:asset1:/",
     ["genai_title", "genai_asset_source", "genai_asset_source_version", "genai_asset_generated_at"]),
]
SUMMARY_RUN = [
    (metadataService.asset_file_metadata_table_name, "db1:asset1:/",
     ["genai_asset_title", "genai_asset_source", "genai_asset_source_version",
      "genai_asset_file_count", "genai_asset_generated_at"]),
]


@pytest.mark.unit
@pytest.mark.parametrize("files", [PER_FILE_RUN, SUMMARY_RUN], ids=["per-file-run", "asset-summary-run"])
def test_every_row_of_the_run_is_tagged_and_every_deleted_row_marked(files):
    assert metadataService.workflow_execution_writer(CROSS_CALL_EVENT) == EXECUTION_ID
    for table, composite, keys in files:
        batch = [{"PutRequest": {"Item": {"metadataKey": {"S": key},
                                          "databaseId:assetId:filePath": {"S": composite},
                                          "metadataValue": {"S": "v"}}}} for key in keys]
        # An "update" run also removes a stale key the previous run wrote.
        batch.append({"DeleteRequest": {"Key": {"metadataKey": {"S": "genai_stale"},
                                                "databaseId:assetId:filePath": {"S": composite}}}})
        client = MagicMock()
        client.batch_write_item.return_value = {"UnprocessedItems": {}}
        with patch.object(metadataService, "dynamodb_client", client), \
                patch.object(metadataService, "writing_workflow_execution_id",
                             metadataService.workflow_execution_writer(CROSS_CALL_EVENT)):
            metadataService.batch_write_with_retry(table, batch)
        written = [request for c in client.batch_write_item.call_args_list
                   for request in c.kwargs["RequestItems"][table]]
        puts = [r["PutRequest"]["Item"] for r in written if "PutRequest" in r]
        assert [item["metadataKey"]["S"] for item in puts] == keys
        assert all(item["lastChangeWorkflowExecutionId"] == {"S": EXECUTION_ID} for item in puts)
        mark = client.update_item.call_args.kwargs
        assert mark["ExpressionAttributeNames"]["#deletedBy"] == "deletedByWorkflowExecutionId"
        assert mark["ExpressionAttributeValues"] == {":executionId": {"S": EXECUTION_ID}}
