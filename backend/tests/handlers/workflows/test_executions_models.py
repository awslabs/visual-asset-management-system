# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from backend.backend.models.executions import (
    WorkflowExecutionRecord,
    PipelineExecutionRecord,
    PipelineExecutionInputFileRecord,
    WorkflowExecutionInputRecord,
    WorkflowExecutionConfigurationRecord,
)


@pytest.mark.unit
class TestExecutionModels:
    def test_workflow_execution_record_minimal(self):
        m = WorkflowExecutionRecord(
            executionId="E1",
            workflowId="wf",
            workflowDatabaseId="db",
            triggerType="Manual",
        )
        assert m.executionId == "E1"
        assert m.triggeredByUserId == "system"  # default
        assert m.executionStopDate == ""  # default

    def test_workflow_execution_record_rejects_bad_trigger_type(self):
        with pytest.raises(Exception):
            WorkflowExecutionRecord(
                executionId="E1", workflowId="wf", workflowDatabaseId="db",
                triggerType="Nope",
            )

    def test_pipeline_execution_record_defaults(self):
        m = PipelineExecutionRecord(
            pipelineExecutionId="P1", workflowExecutionId="E1",
            pipelineId="pid", pipelineDatabaseId="pdb",
            endStatePipeline="false", pipelineExecutionType="Lambda",
        )
        assert m.credentialVendingState == "notVended"
        assert m.s3ReadOnlyScopes == []

    def test_input_file_record(self):
        m = PipelineExecutionInputFileRecord(
            pipelineExecutionId="P1", workflowExecutionId="E1",
            assetId="a1", databaseId="db", inputAssetFileKey="/x.glb",
        )
        assert m.inputAssetFileKey == "/x.glb"

    def test_workflow_input_and_config_records(self):
        wi = WorkflowExecutionInputRecord(
            workflowExecutionId="E1", assetId="a1", databaseId="db",
            inputAssetFileKey="/x.glb", executionStartDate="2026-06-16T00:00:00Z",
        )
        assert wi.assetId == "a1"
        wc = WorkflowExecutionConfigurationRecord(workflowExecutionId="E1")
        assert wc.specifiedPipelinesSnapshot == []
