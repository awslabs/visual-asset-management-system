# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The execution record models must describe the rows the builders actually write.

models/executions.py documents the canonical record shapes; common/workflows/executionRecords.py
writes them. Nothing in backend/backend imports the record models, so a drift between the two has no
runtime effect and no failing test -- it surfaces later, when a contributor uses a record model as the
schema of record for a new write path and omits whatever it left out. The attributes most likely to
be omitted are the index keys, and a row missing one is silently invisible: without allListPartition
it is absent from the global newest-first execution list, without outputDatabaseId:outputAssetId from
an asset's execution history.

Parity is asserted in both directions and by the STORED attribute name (a field's alias, which
pydantic reports as the field name when none is declared), because two of those keys carry a ':' and
so can only be declared under an alias.
"""

import pytest

from backend.backend.common.workflows import executionRecords as er
from backend.backend.models import executions as em


def stored_attribute_names(model):
    """The attribute names a model claims a row carries, as stored."""
    return {field.alias for field in model.__fields__.values()}


def workflow_execution_record_keys():
    """Every attribute build_workflow_execution_record can write, across its conditional branches
    (executionGroupId is written only for a grouped execution)."""
    ungrouped = er.build_workflow_execution_record(
        "E1", "db1", "wf1", "wfArn", "execArn", "2026-01-01T00:00:00Z", "NEW",
        "someuser", "Manual", "logGroupArn")
    grouped = er.build_workflow_execution_record(
        "E1", "db1", "wf1", "wfArn", "execArn", "2026-01-01T00:00:00Z", "NEW",
        "someuser", "Manual", "logGroupArn",
        last_sfn_sync_check_date="2026-01-01T00:05:00Z", execution_group_id="grp1")
    return set(ungrouped) | set(grouped), grouped


def workflow_configuration_record_keys():
    """Every attribute build_workflow_configuration_record can write. The by-output-asset GSI
    partition is written only for an asset-targeted run with a resolved destination."""
    results_only = er.build_workflow_configuration_record(
        "E1", "{}", [{"pipelineId": "pipe1"}], output_location_type="results")
    asset_targeted = er.build_workflow_configuration_record(
        "E1", "{}", [{"pipelineId": "pipe1"}],
        output_location_type="asset", output_asset_id="asset1", output_database_id="db1",
        output_file_base_execution_path_extension="/runs/",
        input_metadata_database_id="db1", input_metadata_file_s3_key="k",
        execution_start_date="2026-01-01T00:00:00Z",
        metadata_source_assets=[{"databaseId": "db1", "assetId": "asset1"}],
        metadata_source_databases=["db1"])
    return set(results_only) | set(asset_targeted), asset_targeted


@pytest.mark.unit
class TestWorkflowExecutionRecordParity:
    def test_every_attribute_the_builder_writes_is_declared(self):
        builder_keys, _ = workflow_execution_record_keys()
        # Non-vacuity: the row is wide, so an empty or near-empty key set means the builder was not
        # exercised rather than that parity holds.
        assert len(builder_keys) >= 15
        missing = sorted(builder_keys - stored_attribute_names(em.WorkflowExecutionRecord))
        assert missing == [], (
            "WorkflowExecutionRecord does not declare attributes build_workflow_execution_record "
            "writes: " + ", ".join(missing))

    def test_it_declares_nothing_the_builder_never_writes(self):
        builder_keys, _ = workflow_execution_record_keys()
        extra = sorted(stored_attribute_names(em.WorkflowExecutionRecord) - builder_keys)
        assert extra == [], (
            "WorkflowExecutionRecord declares attributes no write path produces: "
            + ", ".join(extra))

    def test_the_index_key_attributes_are_declared(self):
        # Named explicitly: these are the attributes whose absence hides a row from a listing, so
        # they must not be lost to a future reshuffle of the parity sets above.
        names = stored_attribute_names(em.WorkflowExecutionRecord)
        assert "allListPartition" in names
        assert "executionGroupId" in names
        assert "workflowDatabaseId:workflowId" in names

    def test_a_builder_row_parses_and_keeps_its_index_keys(self):
        _, grouped = workflow_execution_record_keys()
        parsed = em.WorkflowExecutionRecord(**grouped)
        assert parsed.allListPartition == er.ALL_EXECUTIONS_LIST_PARTITION
        assert parsed.executionGroupId == "grp1"
        assert parsed.workflowDatabaseIdWorkflowId == grouped["workflowDatabaseId:workflowId"]
        # Serializing by alias reproduces the stored attribute names.
        assert set(grouped) <= set(parsed.dict(by_alias=True))


@pytest.mark.unit
class TestWorkflowExecutionConfigurationRecordParity:
    def test_every_attribute_the_builder_writes_is_declared(self):
        builder_keys, _ = workflow_configuration_record_keys()
        assert len(builder_keys) >= 15
        missing = sorted(
            builder_keys - stored_attribute_names(em.WorkflowExecutionConfigurationRecord))
        assert missing == [], (
            "WorkflowExecutionConfigurationRecord does not declare attributes "
            "build_workflow_configuration_record writes: " + ", ".join(missing))

    def test_it_declares_nothing_the_builder_never_writes(self):
        builder_keys, _ = workflow_configuration_record_keys()
        extra = sorted(
            stored_attribute_names(em.WorkflowExecutionConfigurationRecord) - builder_keys)
        assert extra == [], (
            "WorkflowExecutionConfigurationRecord declares attributes no write path produces: "
            + ", ".join(extra))

    def test_the_gsi_keys_and_truncation_flags_are_declared(self):
        names = stored_attribute_names(em.WorkflowExecutionConfigurationRecord)
        assert "outputDatabaseId:outputAssetId" in names
        assert "executionStartDate" in names
        # One flag per variable-size collection the row's shared byte budget can trim.
        for flag in ("inputMetadataTruncated", "specifiedPipelinesSnapshotTruncated",
                     "metadataSourceAssetsTruncated", "metadataSourceDatabasesTruncated"):
            assert flag in names, flag

    def test_a_builder_row_parses_and_keeps_its_gsi_keys(self):
        _, asset_targeted = workflow_configuration_record_keys()
        parsed = em.WorkflowExecutionConfigurationRecord(**asset_targeted)
        assert parsed.outputDatabaseIdOutputAssetId == \
            asset_targeted["outputDatabaseId:outputAssetId"]
        assert parsed.executionStartDate == "2026-01-01T00:00:00Z"
        assert parsed.specifiedPipelinesSnapshotTruncated is False
        assert set(asset_targeted) <= set(parsed.dict(by_alias=True))


@pytest.mark.unit
class TestRecordModelsStillConstructMinimally:
    """Positive controls: the added attributes are all optional, so the existing construction
    shapes -- a hand-built minimal record, and a row that predates an attribute -- keep working."""

    def test_minimal_workflow_execution_record(self):
        record = em.WorkflowExecutionRecord(
            workflowExecutionId="E1", workflowId="wf", workflowDatabaseId="db",
            triggerType="Manual")
        assert record.allListPartition == ""
        assert record.executionGroupId == ""
        assert record.workflowDatabaseIdWorkflowId == ""
        assert record.triggeredByUserId == "SYSTEM_USER"

    def test_minimal_workflow_configuration_record(self):
        record = em.WorkflowExecutionConfigurationRecord(workflowExecutionId="E1")
        assert record.outputFileBaseExecutionPathExtension == "/"
        assert record.outputDatabaseIdOutputAssetId == ""
        assert record.executionStartDate == ""
        assert record.metadataSourceAssetsTruncated is False

    def test_the_trigger_type_rule_still_applies(self):
        with pytest.raises(Exception):
            em.WorkflowExecutionRecord(
                workflowExecutionId="E1", workflowId="wf", workflowDatabaseId="db",
                triggerType="Nope")
