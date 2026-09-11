# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Builder/model parity for the pipeline-level and per-file execution record shapes.

Companion to test_execution_record_model_builder_parity.py, which covers the two workflow-level
pairs. The same drift exists in the pipeline-level classes, and it lands on the same class of
attribute: the composite sort keys and by-asset GSI partitions that a row must carry to be
reachable at all. A row written without ``databaseId:assetId`` is absent from an asset's
execution-input listing exactly as a row without ``allListPartition`` is absent from the global
list, so a contributor using these classes as the schema of record repeats the known trap.

Parity is asserted in both directions and by the STORED attribute name (a field's alias, which
pydantic reports as the field name when none is declared), because the composite keys carry a ':'
and so can only be declared under an alias.
"""

import pytest

from backend.backend.common.workflows import executionRecords as er
from backend.backend.models import executions as em


def stored_attribute_names(model):
    """The attribute names a model claims a row carries, as stored."""
    return {field.alias for field in model.__fields__.values()}


def pipeline_execution_record_keys():
    """Every attribute build_pipeline_execution_record can write. from_pipeline_execution_id is
    the sparse chain-GSI sort key, written only for a chained pipeline."""
    common = dict(
        pipeline_execution_id="P1", workflow_execution_id="E1", pipeline_database_id="db1",
        pipeline_id="pipe1", end_state_pipeline=False, s3_asset_bucket="bucket",
        s3_aux_bucket="auxBucket",
        output_prefixes={"files": "f/", "metadata": "m/", "previews": "p/", "results": "r/"},
        input_metadata_file_prefix="im/", input_config_file_prefix="ic/",
        aux_temp_prefix="t/", aux_preview_prefix="pv/", pipeline_execution_type="Lambda",
        wait_for_callback="Disabled", pipeline_resource_arn="arn:aws:lambda:::function:f")
    unchained = er.build_pipeline_execution_record(**common)
    chained = er.build_pipeline_execution_record(
        **common, from_pipeline_execution_id="P0",
        orchestration_bus_event_prefix="vams.pipeline.P1")
    return set(unchained) | set(chained), chained


def pipeline_input_file_record_keys():
    row = er.build_pipeline_input_file_record("P1", "E1", "db1", "asset1", "/folder/scan.laz")
    return set(row), row


def workflow_execution_input_record_keys():
    row = er.build_workflow_execution_input_record(
        "E1", "db1", "asset1", "/folder/scan.laz", "2026-01-01T00:00:00Z", "wf1", "wfdb1",
        s3_bucket="bucket", asset_root_s3_key="asset1/", version_id="v1")
    return set(row), row


def input_metadata_record_keys():
    """Both scopes: an asset/file row and a metadata-source database row."""
    asset_row = er.build_input_metadata_record(
        "P1", "db1", "asset1", "/folder/scan.laz", {"k": "v"}, "s3/key.json",
        attributes={"a": "b"})
    database_row = er.build_input_metadata_record(
        "P1", "db1", "", "/", {"k": "v"}, "s3/key.json", scope="database")
    return set(asset_row) | set(database_row), asset_row


def input_configuration_record_keys():
    row = er.build_input_configuration_record(
        "P1", "rendered config", "s3/config.json", template_id="tmpl1",
        template_schema_version="1", tag_schema_version="1",
        template_tags=[{"key": "k", "value": "v"}], custom_template_override_used=True,
        custom_template_override="raw body", config_format="json",
        effective_system_config={"inputFileArity": "one"},
        template_overrides={"inputFileArity": "one"})
    return set(row), row


def output_file_record_keys():
    row = er.build_output_file_record(
        "P1", "file", "/out/scan.glb", "bucket", "k/out/scan.glb", 1024, "model/gltf-binary", "v1")
    return set(row), row


def output_metadata_record_keys():
    row = er.build_output_metadata_record("P1", "/out/scan.glb", "triangles", "1000", "meta.json")
    return set(row), row


def output_result_record_keys():
    row = er.build_output_result_record("P1", "/out/results.json", "{}", "k/out/results.json")
    return set(row), row


def log_record_keys():
    row = er.build_log_record("P1", "summary", "stdout", "stderr", "logGroupArn", "logStream")
    return set(row), row


# Each entry pairs a record model with the builder key set it must describe, and names the stored
# attributes whose absence makes a row unreachable through an index. The index-key column is
# listed explicitly so a later reshuffle of the parity sets cannot quietly drop it.
PAIRS = [
    ("PipelineExecutionRecord", em.PipelineExecutionRecord, pipeline_execution_record_keys,
     ["pipelineDatabaseId:pipelineId"]),
    ("PipelineExecutionInputFileRecord", em.PipelineExecutionInputFileRecord,
     pipeline_input_file_record_keys,
     ["databaseId:assetId", "databaseId:assetId:inputAssetFileKey"]),
    ("WorkflowExecutionInputRecord", em.WorkflowExecutionInputRecord,
     workflow_execution_input_record_keys,
     ["databaseId:assetId", "databaseId:assetId:inputAssetFileKey"]),
    ("PipelineExecutionInputMetadataRecord", em.PipelineExecutionInputMetadataRecord,
     input_metadata_record_keys, ["databaseId:assetId:filePath"]),
    ("PipelineExecutionInputConfigurationRecord", em.PipelineExecutionInputConfigurationRecord,
     input_configuration_record_keys, ["recordType"]),
    ("PipelineExecutionOutputFileRecord", em.PipelineExecutionOutputFileRecord,
     output_file_record_keys, ["fileType:relativeFilePath"]),
    ("PipelineExecutionOutputMetadataRecord", em.PipelineExecutionOutputMetadataRecord,
     output_metadata_record_keys, ["targetFilePath:metadataKey"]),
    ("PipelineExecutionOutputResultRecord", em.PipelineExecutionOutputResultRecord,
     output_result_record_keys, ["relativeFilePath"]),
    ("PipelineExecutionLogRecord", em.PipelineExecutionLogRecord, log_record_keys, ["logType"]),
]

PAIR_IDS = [name for name, _, _, _ in PAIRS]


@pytest.mark.unit
@pytest.mark.parametrize("name,model,keys_fn,index_keys", PAIRS, ids=PAIR_IDS)
class TestRecordModelDescribesItsBuilder:
    def test_every_attribute_the_builder_writes_is_declared(self, name, model, keys_fn,
                                                            index_keys):
        builder_keys, _ = keys_fn()
        # Non-vacuity: every one of these rows carries at least a partition key, a sort key and
        # payload, so a near-empty key set means the builder was not exercised.
        assert len(builder_keys) >= 5, (name, builder_keys)
        missing = sorted(builder_keys - stored_attribute_names(model))
        assert missing == [], (
            f"{name} does not declare attributes its builder writes: " + ", ".join(missing))

    def test_it_declares_nothing_the_builder_never_writes(self, name, model, keys_fn, index_keys):
        builder_keys, _ = keys_fn()
        extra = sorted(stored_attribute_names(model) - builder_keys)
        assert extra == [], (
            f"{name} declares attributes no write path produces: " + ", ".join(extra))

    def test_the_key_attributes_are_declared(self, name, model, keys_fn, index_keys):
        names = stored_attribute_names(model)
        for key in index_keys:
            assert key in names, f"{name} is missing the key attribute {key}"

    def test_a_builder_row_parses_and_round_trips_its_stored_names(self, name, model, keys_fn,
                                                                   index_keys):
        _, row = keys_fn()
        parsed = model(**row)
        # Serializing by alias must reproduce every stored attribute name, which is what a model
        # declared with a ':'-bearing alias buys and a field-name-only declaration does not.
        assert set(row) <= set(parsed.dict(by_alias=True)), name
