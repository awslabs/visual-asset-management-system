# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from backend.backend.models.executions import (
    WorkflowExecutionRecord,
    PipelineExecutionRecord,
    PipelineExecutionInputFileRecord,
    WorkflowExecutionInputRecord,
    WorkflowExecutionConfigurationRecord,
    ExecuteWorkflowRequestV2Model,
)


@pytest.mark.unit
class TestExecutionModels:
    def test_workflow_execution_record_minimal(self):
        m = WorkflowExecutionRecord(
            workflowExecutionId="E1",
            workflowId="wf",
            workflowDatabaseId="db",
            triggerType="Manual",
        )
        assert m.workflowExecutionId == "E1"
        assert m.triggeredByUserId == "SYSTEM_USER"  # default (reserved system identity)
        assert m.executionStopDate == ""  # default

    def test_workflow_execution_record_rejects_bad_trigger_type(self):
        with pytest.raises(Exception):
            WorkflowExecutionRecord(
                workflowExecutionId="E1", workflowId="wf", workflowDatabaseId="db",
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
        # Output base path extension defaults to root.
        assert wc.outputFileBaseExecutionPathExtension == "/"


@pytest.mark.unit
class TestExecuteRequestOutputPathExtension:
    """Validation for the optional output base path extension on the execute request."""

    def test_accepts_plain_path(self):
        m = ExecuteWorkflowRequestV2Model(outputFileBaseExecutionPathExtension="runs/2026")
        assert m.outputFileBaseExecutionPathExtension == "runs/2026"

    def test_accepts_dynamic_tag_placeholders(self):
        # Placeholders are preserved verbatim (resolved later by the template renderer).
        m = ExecuteWorkflowRequestV2Model(
            outputFileBaseExecutionPathExtension="out/{{firstAssetFileFileNameNoExt}}/")
        assert "{{firstAssetFileFileNameNoExt}}" in m.outputFileBaseExecutionPathExtension

    def test_none_is_allowed(self):
        m = ExecuteWorkflowRequestV2Model()
        assert m.outputFileBaseExecutionPathExtension is None

    def test_rejects_traversal(self):
        with pytest.raises(Exception):
            ExecuteWorkflowRequestV2Model(outputFileBaseExecutionPathExtension="../escape")

    def test_rejects_backslash(self):
        with pytest.raises(Exception):
            ExecuteWorkflowRequestV2Model(outputFileBaseExecutionPathExtension="out\\win")
