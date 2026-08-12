# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File ATTRIBUTES on the input-metadata read path.

Attributes reach a pipeline through the shared metadata envelope, but until they are persisted on the
input-metadata rows and projected by the details/paged responses, a caller cannot confirm what a run
captured — and the per-step fileAttributes gate #89 introduced has no observable effect through the
API. This is the read-path counterpart to that gate: a mechanism that cannot be observed cannot be
verified.

Shape decisions this pins:
  - attributes ride on the SAME row as that file's metadata, in their own `attributes` key. They
    describe the file the row already identifies, so they are a second property of one fact rather
    than a second fact — which also leaves the details view's dedupe identity
    (pipelineId, scope, databaseId, assetId, filePath) untouched.
  - they are NOT merged into `metadata`: fileMetadata and fileAttributes are independently gated, so a
    merged map would lose which gate delivered a value.
  - a record is skipped only when it has NEITHER metadata NOR attributes. The four gates are
    independent, so `fileMetadata: false` + `fileAttributes: true` is a valid configuration whose files
    carry attributes and no metadata; skipping on empty metadata alone would make them unreadable.
"""

import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# Env the execution-service lambda reads at import.
for _k, _v in {
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "PIPELINE_STORAGE_TABLE_V2_NAME": "t-pipe-v2",
    "WORKFLOW_STORAGE_TABLE_NAME": "t-workflows",
    "PIPELINE_STORAGE_TABLE_NAME": "t-pipelines",
    "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME": "t-pin-files",
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "WORKFLOW_EXECUTION_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:vams-wf:*",
}.items():
    os.environ.setdefault(_k, _v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf

from backend.backend.common.workflows import executionRecords as er
from backend.backend.handlers.workflows import executionService as le

MOD = "backend.backend.handlers.workflows.executionService"

FILE_KEY = "/clips/in.mp4"


def _envelope(metadata=None, attributes=None, file_key=FILE_KEY):
    """A v2 envelope with one asset carrying an asset-level record plus one per-file record."""
    files = [er.build_metadata_file_record("/", metadata={"ASSET_K": "asset-v"})]
    if metadata is not None or attributes is not None:
        files.append(er.build_metadata_file_record(
            file_key, metadata=metadata, attributes=attributes))
    return er.build_grouped_metadata_envelope(
        [er.build_metadata_asset_group("db1", "a1", files=files)])


def _file_row(rows, file_key=FILE_KEY):
    return next((r for r in rows if r["filePath"] == file_key), None)


@pytest.mark.unit
class TestEnvelopeRowsCarryAttributes:
    def test_attributes_land_on_the_files_own_row(self):
        rows = list(er.metadata_envelope_rows(
            _envelope(metadata={"FILE_K": "file-v"}, attributes={"fps": "30"})))
        row = _file_row(rows)
        assert row["metadata"] == {"FILE_K": "file-v"}
        assert row["attributes"] == {"fps": "30"}

    def test_attributes_are_not_merged_into_metadata(self):
        rows = list(er.metadata_envelope_rows(
            _envelope(metadata={"FILE_K": "file-v"}, attributes={"fps": "30"})))
        assert "fps" not in _file_row(rows)["metadata"]

    def test_no_extra_row_is_emitted_for_attributes(self):
        # One row per (asset, filePath) — attributes must not double the row count, which would
        # both spend the response budget faster and change the dedupe identity.
        rows = list(er.metadata_envelope_rows(
            _envelope(metadata={"FILE_K": "file-v"}, attributes={"fps": "30"})))
        assert len(rows) == 2  # the asset-level '/' row + the one file row
        assert sorted(r["filePath"] for r in rows) == ["/", FILE_KEY]

    def test_attributes_only_file_still_yields_a_row(self):
        # THE REACHABLE CASE: fileMetadata off, fileAttributes on. Skipping on empty metadata alone
        # would drop this row and make the attributes unreadable through the API.
        rows = list(er.metadata_envelope_rows(_envelope(metadata=None, attributes={"fps": "30"})))
        row = _file_row(rows)
        assert row is not None, "an attributes-only file must still produce a row"
        assert row["metadata"] == {}
        assert row["attributes"] == {"fps": "30"}

    def test_metadata_only_file_reports_empty_attributes(self):
        rows = list(er.metadata_envelope_rows(_envelope(metadata={"FILE_K": "file-v"})))
        assert _file_row(rows)["attributes"] == {}

    def test_record_with_neither_is_still_skipped(self):
        # The envelope emits a '/' record per asset even when empty; persisting those would pad the
        # details response. Relaxing the skip must not relax it into "never skip".
        env = er.build_grouped_metadata_envelope([
            er.build_metadata_asset_group("db1", "a1", files=[
                er.build_metadata_file_record("/", metadata=None),
                er.build_metadata_file_record(FILE_KEY, metadata=None, attributes=None)])])
        assert list(er.metadata_envelope_rows(env)) == []

    def test_database_and_legacy_rows_carry_an_empty_attributes_map(self):
        # Uniform shape: every row has the key, so a reader never has to branch on its presence.
        env = er.build_grouped_metadata_envelope(
            [], databases=[er.build_metadata_database_group("db1", {"dm": "1"})])
        assert list(er.metadata_envelope_rows(env))[0]["attributes"] == {}
        legacy = list(er.metadata_envelope_rows({"VAMS": {"assetMetadata": {"k": "v"}}}))
        assert legacy[0]["attributes"] == {}


@pytest.mark.unit
class TestRecordBuilderCarriesAttributes:
    def test_attributes_are_persisted_in_their_own_key(self):
        rec = er.build_input_metadata_record(
            "pe1", "db1", "a1", FILE_KEY, {"FILE_K": "file-v"}, "s3key",
            attributes={"fps": "30"})
        assert rec["metadata"] == {"FILE_K": "file-v"}
        assert rec["attributes"] == {"fps": "30"}

    def test_absent_attributes_default_to_an_empty_map(self):
        rec = er.build_input_metadata_record("pe1", "db1", "a1", "/", {"k": "v"}, "s3key")
        assert rec["attributes"] == {}

    def test_row_identity_is_unchanged_by_attributes(self):
        # The SK (and the details dedupe key) must not depend on attributes, or two reads of one
        # entity would become two distinct facts.
        with_attrs = er.build_input_metadata_record(
            "pe1", "db1", "a1", FILE_KEY, {"k": "v"}, "s3key", attributes={"fps": "30"})
        without = er.build_input_metadata_record(
            "pe1", "db1", "a1", FILE_KEY, {"k": "v"}, "s3key")
        assert with_attrs["databaseId:assetId:filePath"] == without["databaseId:assetId:filePath"]


@pytest.mark.unit
class TestScrubberProjectsAttributes:
    def test_attributes_reach_the_public_projection(self):
        out = le._scrub_input_metadata({
            "databaseId": "db1", "assetId": "a1", "filePath": FILE_KEY, "scope": "asset",
            "metadata": {"FILE_K": "file-v"}, "attributes": {"fps": "30"},
            "sourceInputMetadataFileS3Key": "internal/key.json"})
        assert out["attributes"] == {"fps": "30"}
        # The internal source key stays internal.
        assert "sourceInputMetadataFileS3Key" not in out

    def test_row_stored_before_the_key_existed_reads_as_empty(self):
        out = le._scrub_input_metadata({
            "databaseId": "db1", "assetId": "a1", "filePath": "/", "metadata": {"k": "v"}})
        assert out["attributes"] == {}


@pytest.mark.unit
class TestPerStepGateIsObservableThroughTheApi:
    """THE ACCEPTANCE CRITERION — the #89 read-path counterpart.

    Step 1's effective gate allows fileAttributes; step 2's excludes them. #89 narrows DELIVERY per
    step, so the two steps read different envelopes; this asserts the difference is visible in the
    details response, which is the only way to verify the gate from outside.
    """

    @staticmethod
    def _details_for(rows_by_pexec):
        """assemble_execution_details over two pipeline executions with their own metadata rows."""
        prows = [
            {"pipelineExecutionId": "pe1", "pipelineId": "p1", "pipelineDatabaseId": "db"},
            {"pipelineExecutionId": "pe2", "pipelineId": "p2", "pipelineDatabaseId": "db"},
        ]
        calls = {"n": 0}

        def _capped(table_name, key_condition, max_items):
            if table_name == le.pipeline_execution_input_metadata_table:
                # One call per pipeline execution, in prows order.
                pexec = "pe1" if calls["n"] == 0 else "pe2"
                calls["n"] += 1
                return rows_by_pexec.get(pexec, []), False
            return [], False

        with patch(f"{MOD}.get_workflow_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=prows), \
             patch(f"{MOD}._query_all", return_value=[]), \
             patch(f"{MOD}._query_capped", side_effect=_capped), \
             patch(f"{MOD}.get_produced_file_versions", return_value={}):
            return le.assemble_execution_details(
                "E1", {"workflowId": "wf", "workflowDatabaseId": "db"}, config_row={})

    def _rows(self):
        """Step 1 received attributes (gate on); step 2 did not (gate off). Both saw the file."""
        allowed = er.metadata_envelope_rows(
            _envelope(metadata={"FILE_K": "file-v"}, attributes={"fps": "30"}))
        excluded = er.metadata_envelope_rows(
            _envelope(metadata={"FILE_K": "file-v"}, attributes=None))
        return {"pe1": list(allowed), "pe2": list(excluded)}

    def test_allowed_step_shows_attributes_and_excluded_step_does_not(self):
        details = self._details_for(self._rows())
        by_pipeline = {}
        for md in details["inputMetadata"]:
            if md["filePath"] == FILE_KEY:
                by_pipeline[md["pipelineId"]] = md
        assert set(by_pipeline) == {"p1", "p2"}, f"expected both steps' rows, got {by_pipeline}"
        # The gate's effect, read through the API.
        assert by_pipeline["p1"]["attributes"] == {"fps": "30"}
        assert by_pipeline["p2"]["attributes"] == {}
        # Metadata is unaffected — only the attributes gate differed between the steps.
        assert by_pipeline["p1"]["metadata"] == by_pipeline["p2"]["metadata"] == {"FILE_K": "file-v"}

    def test_both_steps_rows_survive_dedupe(self):
        # The rows differ only by pipelineId; a dedupe key missing pipelineId would collapse them and
        # report one step as the only reader.
        details = self._details_for(self._rows())
        file_rows = [md for md in details["inputMetadata"] if md["filePath"] == FILE_KEY]
        assert len(file_rows) == 2
