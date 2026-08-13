# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-pipeline attribution of the persisted input-metadata rows.

The grouped metadata envelope is assembled once from ALL of a run's selected inputs and metadata
sources, and is written to S3 once per execution — one shared file every pipeline task reads. The
PipelineExecutionInputMetadataStorageTable rows are different: they are keyed by pipelineExecutionId
and back the execution DETAILS view, whose whole question is which metadata went into which pipeline.
Pipelines do not all receive the same inputs (each gets the subset passing its own effective
inputFileFilters; an arity-'none' pipeline gets none), so a pipeline's rows must describe the entities
IT reads.

Covers the pure narrowing helper (executionRecords.pipeline_metadata_envelope_rows) and the write path
in executeWorkflow._persist_execution_records end to end."""

import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# executeWorkflow loads these at import (mirrors test_executeWorkflow.py).
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates")
os.environ.setdefault("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.common.workflows import executionRecords as er
from backend.backend.handlers.workflows import executeWorkflow as ewv2

MOD = "backend.backend.handlers.workflows.executeWorkflow"


def _envelope(assets, databases=None):
    return er.build_grouped_metadata_envelope(assets, databases=databases)


def _asset_group(database_id, asset_id, files):
    """files: [(fileKey, metadata_or_None), ...]."""
    return er.build_metadata_asset_group(
        database_id, asset_id,
        files=[er.build_metadata_file_record(key, metadata=md) for key, md in files])


def _input(database_id, asset_id, relative_key):
    return {"databaseId": database_id, "assetId": asset_id, "relativeFileKey": relative_key,
            "versionId": ""}


def _identities(rows):
    return [(r["scope"], r["databaseId"], r["assetId"], r["filePath"]) for r in rows]


@pytest.mark.unit
class TestPipelineMetadataEnvelopeRows:
    """The pure narrowing helper. The envelope carries the whole run; each call asks what ONE pipeline
    reads."""

    _ENVELOPE = _envelope(
        [_asset_group("db1", "a1", [("/", {"asset": "a1"}),
                                    ("/m.glb", {"file": "glb"}),
                                    ("/s.e57", {"file": "e57"})])],
        databases=[er.build_metadata_database_group("db1", {"owner": "eng"})])

    def test_a_pipeline_gets_only_the_files_it_received(self):
        rows = list(er.pipeline_metadata_envelope_rows(
            self._ENVELOPE, [_input("db1", "a1", "/m.glb")]))
        assert ("asset", "db1", "a1", "/m.glb") in _identities(rows)
        assert ("asset", "db1", "a1", "/s.e57") not in _identities(rows)

    def test_disjoint_pipelines_get_disjoint_file_rows(self):
        glb = list(er.pipeline_metadata_envelope_rows(
            self._ENVELOPE, [_input("db1", "a1", "/m.glb")]))
        e57 = list(er.pipeline_metadata_envelope_rows(
            self._ENVELOPE, [_input("db1", "a1", "/s.e57")]))
        glb_files = [r["filePath"] for r in glb if r["scope"] == "asset" and r["filePath"] != "/"]
        e57_files = [r["filePath"] for r in e57 if r["scope"] == "asset" and r["filePath"] != "/"]
        assert glb_files == ["/m.glb"]
        assert e57_files == ["/s.e57"]

    def test_the_asset_row_follows_the_files_the_pipeline_received(self):
        # A pipeline holding a file from the asset reads that asset's own metadata.
        received = list(er.pipeline_metadata_envelope_rows(
            self._ENVELOPE, [_input("db1", "a1", "/m.glb")]))
        assert ("asset", "db1", "a1", "/") in _identities(received)
        # A pipeline holding NO file still reads asset metadata: with no input file to project
        # through, the pipeline-side helper takes the envelope's first asset group as its subject, so
        # that asset's row is what the step actually reads.
        none_received = list(er.pipeline_metadata_envelope_rows(self._ENVELOPE, []))
        assert ("asset", "db1", "a1", "/") in _identities(none_received)
        # It reads the asset LEVEL only — never a per-file record it was not given.
        assert not [i for i in _identities(none_received)
                    if i[0] == "asset" and i[3] not in ("", "/")]

    def test_an_asset_row_of_an_asset_no_file_came_from_is_excluded(self):
        envelope = _envelope([
            _asset_group("db1", "a1", [("/", {"asset": "a1"}), ("/m.glb", {"file": "glb"})]),
            _asset_group("db1", "a2", [("/", {"asset": "a2"}), ("/o.glb", {"file": "other"})])])
        rows = list(er.pipeline_metadata_envelope_rows(envelope, [_input("db1", "a1", "/m.glb")]))
        assert _identities(rows) == [("asset", "db1", "a1", "/"), ("asset", "db1", "a1", "/m.glb")]

    def test_a_named_source_asset_row_reaches_a_pipeline_holding_no_file(self):
        # The run reads a named source asset as an entity, not through a file, so a pipeline that
        # received nothing still reads it.
        envelope = _envelope([_asset_group("db1", "src", [("/", {"prompt": "p"})])])
        rows = list(er.pipeline_metadata_envelope_rows(
            envelope, [], metadata_source_assets=[{"databaseId": "db1", "assetId": "src"}]))
        assert _identities(rows) == [("asset", "db1", "src", "/")]

    def test_database_rows_reach_every_pipeline(self):
        # Database metadata describes an entity, not a file selection, and the shared envelope every
        # task reads carries the whole 'databases' list.
        for inputs in ([], [_input("db1", "a1", "/m.glb")], [_input("db1", "a1", "/s.e57")]):
            rows = list(er.pipeline_metadata_envelope_rows(self._ENVELOPE, inputs))
            assert ("database", "db1", "", "/") in _identities(rows)

    def test_a_database_row_is_not_narrowed_to_the_pipelines_own_files(self):
        envelope = _envelope(
            [_asset_group("db1", "a1", [("/", None), ("/m.glb", {"file": "glb"})])],
            databases=[er.build_metadata_database_group("db1", {"o": "1"}),
                       er.build_metadata_database_group("db2", {"o": "2"})])
        rows = list(er.pipeline_metadata_envelope_rows(envelope, [_input("db1", "a1", "/m.glb")]))
        assert [r["databaseId"] for r in rows if r["scope"] == "database"] == ["db1", "db2"]

    def test_a_pipeline_receiving_everything_gets_every_row(self):
        rows = list(er.pipeline_metadata_envelope_rows(
            self._ENVELOPE, [_input("db1", "a1", "/m.glb"), _input("db1", "a1", "/s.e57")]))
        assert _identities(rows) == _identities(list(er.metadata_envelope_rows(self._ENVELOPE)))

    def test_an_unkeyed_legacy_row_reaches_every_pipeline(self):
        # The legacy flat envelope's single row names no asset, so no per-pipeline input set can
        # include or exclude it.
        rows = list(er.pipeline_metadata_envelope_rows({"VAMS": {"assetMetadata": {"k": "v"}}}, []))
        assert _identities(rows) == [("asset", "", "", "/")]

    def test_a_relative_key_is_normalized_before_matching(self):
        rows = list(er.pipeline_metadata_envelope_rows(
            self._ENVELOPE, [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "m.glb"}]))
        assert ("asset", "db1", "a1", "/m.glb") in _identities(rows)

    def test_a_whole_asset_selection_reads_the_asset_row(self):
        # A whole-asset selection carries the '/' relative key and no per-file record of its own.
        rows = list(er.pipeline_metadata_envelope_rows(
            _envelope([_asset_group("db1", "a1", [("/", {"asset": "a1"})])]),
            [_input("db1", "a1", "/")]))
        assert _identities(rows) == [("asset", "db1", "a1", "/")]

    def test_rows_with_no_metadata_are_still_skipped(self):
        rows = list(er.pipeline_metadata_envelope_rows(
            _envelope([_asset_group("db1", "a1", [("/", None), ("/m.glb", {})])]),
            [_input("db1", "a1", "/m.glb")]))
        assert rows == []

    @pytest.mark.parametrize("payload", [None, {}, [], "x", 5])
    def test_junk_envelopes_yield_nothing(self, payload):
        assert list(er.pipeline_metadata_envelope_rows(payload, [_input("db1", "a1", "/m.glb")])) == []


#######################
# Write-path wiring
#######################

_WORKFLOW = {
    "databaseId": "db1", "workflowId": "wf1", "workflowName": "WF", "enabled": True, "archived": False,
    "workflow_arn": "arn:aws:states:us-east-1:1:stateMachine:vams-wf1",
    "jobNames": ["job-p1", "job-p2", "job-p3"],
    "specifiedPipelines": [{"pipelineDatabaseId": "db1", "pipelineId": "p1", "jobName": "p1"}],
    "systemConfig": {
        "inputFileArity": "multi",
        "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                       "wholeAssetAllowed": False, "folderAllowed": False},
        "metadataInputs": {"assetMetadata": True, "fileMetadata": True, "fileAttributes": False,
                           "databaseMetadata": True},
        "inputFileFilters": {"allow": [], "exclude": []},
        "concurrencyRestriction": "none",
        "outputTarget": {"locationType": "asset", "allowOverride": False},
    },
}
_ASSET = {"databaseId": "db1", "assetId": "a1", "assetName": "A1", "bucketId": "bkt-1",
          "assetLocation": {"Key": "a1/"}}


def _pipeline(pipeline_id, arity="multi", allow=None):
    return {
        "databaseId": "db1", "pipelineId": pipeline_id, "pipelineName": pipeline_id,
        "enabled": True, "archived": False,
        "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"},
                            "waitForCallback": "Disabled"},
        "systemConfig": {"inputFileArity": arity, "requireTemplate": False,
                         "allowCustomTemplateOverride": False,
                         "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                                        "wholeAssetAllowed": False, "folderAllowed": False},
                         "inputFileFilters": {"allow": allow or [], "exclude": []}},
    }


def _event(body):
    return {
        "requestContext": {"http": {"method": "POST", "path": "/workflows/db1/wf1/execute"},
                           "authorizer": {}},
        "pathParameters": {"workflowDatabaseId": "db1", "workflowId": "wf1"},
        "queryStringParameters": {},
        "headers": {"authorization": "Bearer t"},
        "body": json.dumps(body),
    }


def _allow_enforcer():
    e = MagicMock()
    e.enforce.return_value = True
    e.enforceAPI.return_value = True
    return e


def _md_batch(tables):
    """The input-metadata table's batch writer: the rows go out through table.batch_writer(), so the
    written items are on the context manager it yields rather than on the table's own put_item."""
    return tables["t-pin-md"].batch_writer.return_value.__enter__.return_value


def _launch(pipelines, body, workflow=None, tables_out=None):
    """Run the handler over a workflow referencing `pipelines`, returning
    {pipelineExecutionId: [row, ...]} for the input-metadata table plus the pipeline-execution rows,
    so a row set can be attributed back to the pipeline that owns it.

    tables_out, when given, receives the table-name -> mock map so a caller can also assert on HOW the
    rows were written."""
    wf = dict(workflow or _WORKFLOW)
    wf["specifiedPipelines"] = [{"pipelineDatabaseId": p["databaseId"], "pipelineId": p["pipelineId"],
                                 "jobName": p["pipelineId"]} for p in pipelines]
    wf["jobNames"] = [f"uuid-{p['pipelineId']}" for p in pipelines]
    by_id = {p["pipelineId"]: p for p in pipelines}

    tables = {}
    with patch(f"{MOD}._get_workflow", return_value=wf), \
         patch(f"{MOD}._get_pipeline", side_effect=lambda db, pid: dict(by_id[pid])), \
         patch(f"{MOD}._get_asset", return_value=dict(_ASSET)), \
         patch(f"{MOD}._default_run_bucket",
               return_value={"bucketName": "run-bucket", "baseAssetsPrefix": ""}), \
         patch(f"{MOD}._asset_bucket_details",
               return_value={"bucketName": "asset-bucket", "baseAssetsPrefix": ""}), \
         patch(f"{MOD}._input_exists_in_s3", return_value=(True, "v-resolved")), \
         patch(f"{MOD}.CasbinEnforcer", return_value=_allow_enforcer()), \
         patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}), \
         patch(f"{MOD}._running_execution_exists", return_value=False), \
         patch(f"{MOD}._fetch_metadata",
               return_value=[{"metadataKey": "assetKey", "metadataValue": "assetValue"}]), \
         patch(f"{MOD}._fetch_file_metadata",
               side_effect=lambda d, a, r, kind, e: [
                   {"metadataKey": "fileKey", "metadataValue": r}]), \
         patch(f"{MOD}._fetch_database_metadata",
               return_value=[{"metadataKey": "owner", "metadataValue": "eng"}]), \
         patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
         patch(f"{MOD}.dynamodb") as m_dynamo:
        m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
        m_dynamo.Table.side_effect = lambda name: tables.setdefault(name, MagicMock())
        resp = ewv2.lambda_handler(_event(body), MagicMock())
    assert resp["statusCode"] == 200, resp["body"]
    if tables_out is not None:
        tables_out.update(tables)

    pexec_rows = [c.kwargs["Item"] for c in tables["t-pexec"].put_item.call_args_list]
    pipeline_of = {r["pipelineExecutionId"]: r["pipelineId"] for r in pexec_rows}
    rows_by_pipeline = {pid: [] for pid in by_id}
    for call in _md_batch(tables).put_item.call_args_list:
        item = call.kwargs["Item"]
        rows_by_pipeline[pipeline_of[item["pipelineExecutionId"]]].append(item)
    return rows_by_pipeline


def _sort_keys(rows):
    return sorted(r["databaseId:assetId:filePath"] for r in rows)


@pytest.mark.unit
class TestPersistedRowsAreAttributedPerPipeline:
    _TWO_FILES = {"inputFiles": [
        {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/m.glb"},
        {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/s.e57"}]}

    def test_disjoint_filter_pipelines_get_rows_only_for_their_own_files(self):
        rows = _launch([_pipeline("pglb", allow=["*.glb"]), _pipeline("pe57", allow=["*.e57"])],
                       self._TWO_FILES)
        # Each pipeline: its own file, its asset's '/' row, and the run's database row.
        assert _sort_keys(rows["pglb"]) == ["db1::/", "db1:a1:/", "db1:a1:/m.glb"]
        assert _sort_keys(rows["pe57"]) == ["db1::/", "db1:a1:/", "db1:a1:/s.e57"]
        # The other pipeline's file is absent from each set — the misattribution this guards.
        assert not any(r["filePath"] == "/s.e57" for r in rows["pglb"])
        assert not any(r["filePath"] == "/m.glb" for r in rows["pe57"])

    def test_a_rows_metadata_is_the_value_captured_for_that_file(self):
        rows = _launch([_pipeline("pglb", allow=["*.glb"]), _pipeline("pe57", allow=["*.e57"])],
                       self._TWO_FILES)
        glb = next(r for r in rows["pglb"] if r["filePath"] == "/m.glb")
        e57 = next(r for r in rows["pe57"] if r["filePath"] == "/s.e57")
        assert glb["metadata"] == {"fileKey": "/m.glb"}
        assert e57["metadata"] == {"fileKey": "/s.e57"}

    def test_an_arity_none_pipeline_gets_no_per_file_rows(self):
        # It receives no input files, so no FILE's metadata is something it read. It does read the
        # asset level: with no input file to project through, the pipeline-side helper takes the
        # envelope's first asset group as its subject, so that asset's row belongs to the step.
        rows = _launch([_pipeline("pall"), _pipeline("pnone", arity="none")], self._TWO_FILES)
        assert _sort_keys(rows["pnone"]) == ["db1::/", "db1:a1:/"]
        assert _sort_keys(rows["pall"]) == ["db1::/", "db1:a1:/", "db1:a1:/m.glb", "db1:a1:/s.e57"]

    def test_an_arity_none_pipeline_gets_its_metadata_source_entities(self):
        # A file-less run's arity-'none' pipeline reads the entities the run NAMED: the source asset's
        # own metadata, the source asset's database, and the named source database. A named asset
        # contributes its database the same way an input file's asset does.
        wf = dict(_WORKFLOW)
        wf["systemConfig"] = dict(_WORKFLOW["systemConfig"])
        wf["systemConfig"]["inputFileArity"] = "none"
        wf["systemConfig"]["outputTarget"] = {"locationType": "none", "allowOverride": False}
        rows = _launch(
            [_pipeline("pnone", arity="none")],
            {"inputFiles": [], "metadataSourceAssets": [{"databaseId": "db1", "assetId": "a1"}],
             "metadataSourceDatabaseId": "src-db"},
            workflow=wf)
        assert _sort_keys(rows["pnone"]) == ["db1::/", "db1:a1:/", "src-db::/"]
        by_key = {r["databaseId:assetId:filePath"]: r for r in rows["pnone"]}
        assert by_key["db1:a1:/"]["scope"] == "asset"
        assert by_key["db1::/"]["scope"] == "database"
        assert by_key["src-db::/"]["scope"] == "database"

    def test_a_single_pipeline_execution_writes_the_whole_envelope(self):
        # Regression guard: with one pipeline receiving every selected file, the row set is exactly the
        # envelope's rows — unchanged from a run with no narrowing.
        rows = _launch([_pipeline("ponly")], self._TWO_FILES)
        assert _sort_keys(rows["ponly"]) == [
            "db1::/", "db1:a1:/", "db1:a1:/m.glb", "db1:a1:/s.e57"]

    def test_pipelines_admitting_every_file_all_get_the_whole_envelope(self):
        # No narrowing applies when the filters admit everything; every pipeline reads every file.
        rows = _launch([_pipeline("p1"), _pipeline("p2"), _pipeline("p3")], self._TWO_FILES)
        for pid in ("p1", "p2", "p3"):
            assert _sort_keys(rows[pid]) == [
                "db1::/", "db1:a1:/", "db1:a1:/m.glb", "db1:a1:/s.e57"]

    def test_every_written_row_is_deletable_by_the_permanent_delete_key_schema(self):
        # permanent_delete_execution deletes by PK pipelineExecutionId + SK databaseId:assetId:filePath;
        # a row missing either key would survive the delete.
        rows = _launch([_pipeline("pglb", allow=["*.glb"]), _pipeline("pe57", allow=["*.e57"])],
                       self._TWO_FILES)
        for pipeline_rows in rows.values():
            for row in pipeline_rows:
                assert row["pipelineExecutionId"]
                assert row["databaseId:assetId:filePath"]


@pytest.mark.unit
class TestRowsAreWrittenThroughOneBatchWriter:
    """The rows are transmitted through table.batch_writer() — boto3 buffers to 25 items per
    BatchWriteItem and retries unprocessed items itself. One writer spans the whole pipeline loop, so a
    multi-pipeline run fills whole requests rather than sending a part-filled one per pipeline."""

    _TWO_FILES = {"inputFiles": [
        {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/m.glb"},
        {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/s.e57"}]}

    def test_one_writer_spans_every_pipeline_and_carries_every_row(self):
        tables = {}
        rows = _launch([_pipeline("p1"), _pipeline("p2"), _pipeline("p3")], self._TWO_FILES,
                       tables_out=tables)
        # One writer for the whole loop, not one per pipeline.
        assert tables["t-pin-md"].batch_writer.call_count == 1
        # Every expected row reaches it, and nothing takes the per-row path.
        batched = [c.kwargs["Item"] for c in _md_batch(tables).put_item.call_args_list]
        assert len(batched) == 4 * 3
        assert tables["t-pin-md"].put_item.call_count == 0
        # The batched rows are the pipelines' rows — each pipeline's full row set, keyed to its own
        # pipelineExecutionId.
        assert sorted(r["databaseId:assetId:filePath"] for r in batched) == sorted(
            k for pipeline_rows in rows.values() for k in _sort_keys(pipeline_rows))
        assert len({r["pipelineExecutionId"] for r in batched}) == 3

    def test_the_other_record_kinds_still_write_per_row(self):
        # Only the input-metadata rows batch: the rest are one row per pipeline, so there is nothing to
        # batch, and the main/input/configuration rows are single writes.
        tables = {}
        _launch([_pipeline("p1"), _pipeline("p2")], self._TWO_FILES, tables_out=tables)
        assert tables["t-pexec"].put_item.call_count == 2
        assert tables["t-pin-cfg"].put_item.call_count == 2
        assert tables["t-wf-cfg"].put_item.call_count == 1
        assert tables["t-wf-inputs"].put_item.call_count == 2

    def test_a_repeated_row_key_collapses_instead_of_failing_the_request(self):
        # One BatchWriteItem request carrying the same key twice is rejected outright, where a per-row
        # put_item silently overwrote. Selecting one file at two versionIds reaches exactly that: the
        # versionId is part of an input's identity but not of its metadata row's, so the file yields two
        # envelope rows with one key. The writer is asked to de-duplicate on the table's own key, so the
        # row collapses to its last value and the run still records one row per (asset, filePath).
        tables = {}
        rows = _launch([_pipeline("ponly")], {"inputFiles": [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/m.glb", "versionId": "v1"},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/m.glb", "versionId": "v2"}]},
            tables_out=tables)
        assert tables["t-pin-md"].batch_writer.call_args.kwargs["overwrite_by_pkeys"] == [
            "pipelineExecutionId", "databaseId:assetId:filePath"]
        assert _sort_keys(rows["ponly"]) == ["db1::/", "db1:a1:/", "db1:a1:/m.glb", "db1:a1:/m.glb"]


@pytest.mark.unit
class TestRowCountReduction:
    """The row-count saving is entirely a function of how much the pipelines' input sets overlap."""

    def test_disjoint_filters_save_the_other_pipelines_files(self):
        body = {"inputFiles": [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/m.glb"},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/s.e57"}]}
        rows = _launch([_pipeline("pglb", allow=["*.glb"]), _pipeline("pe57", allow=["*.e57"])], body)
        written = sum(len(v) for v in rows.values())
        # Envelope rows x pipelines is what an un-narrowed write costs.
        assert written == 6 and 4 * 2 == 8

    def test_full_overlap_saves_nothing(self):
        body = {"inputFiles": [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/m.glb"},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/s.e57"}]}
        rows = _launch([_pipeline("p1"), _pipeline("p2"), _pipeline("p3")], body)
        written = sum(len(v) for v in rows.values())
        assert written == 4 * 3


@pytest.mark.unit
class TestPersistedRowsHonourTheStepsTypeGate:
    """The rows must be narrowed by the step's own metadataInputs, not just by its input files.

    These rows are what the execution DETAILS response reports as "what this step received". The
    delivery channels (the step's metadata.json, its manifest location, its template-tag payload) are
    narrowed per step by _resolve_step_delivery; if the ROWS are built from the shared envelope
    instead, a gated step's report over-states its input while its actual delivery was correctly
    narrowed — the report and the delivery disagree.

    Every test here drives the REAL handler and asserts on the rows PUT to the table. A test that
    hand-builds rows and feeds them to the details projection cannot detect this defect: it verifies
    that the projection faithfully reports whatever rows it is given, which is a different claim.
    """

    _TWO_FILES = {"inputFiles": [
        {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/m.glb"},
        {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/s.e57"}]}

    @staticmethod
    def _gated(pipeline_id, metadata_inputs, **kwargs):
        """A pipeline whose systemConfig carries an explicit metadataInputs gate.

        _pipeline() takes no metadata_inputs kwarg, so the gate is set on the returned dict.
        """
        p = _pipeline(pipeline_id, **kwargs)
        p["systemConfig"]["metadataInputs"] = metadata_inputs
        return p

    _ALL_ON = {"assetMetadata": True, "fileMetadata": True,
               "fileAttributes": True, "databaseMetadata": True}

    def test_control_both_steps_receive_rows_when_nothing_is_gated(self):
        # POSITIVE CONTROL. A fixture that fails validation, or a launch that writes no rows at all,
        # would make every "gated step has no values" assertion below pass for the wrong reason.
        rows = _launch([self._gated("pa", self._ALL_ON), self._gated("pb", self._ALL_ON)],
                       self._TWO_FILES)
        assert rows["pa"] and rows["pb"], "expected both steps to have rows written"
        assert any(r["metadata"] for r in rows["pa"])
        assert any(r["metadata"] for r in rows["pb"])

    def test_file_metadata_excluded_drops_that_steps_file_rows(self):
        # narrow_metadata_envelope clears an excluded per-file record's metadata, and
        # metadata_envelope_rows skips a record carrying neither metadata nor attributes — so the file
        # rows drop out entirely, which is the correct "this step read nothing for that file".
        allowed = self._gated("pallow", self._ALL_ON)
        excluded = self._gated("pexcl", {**self._ALL_ON, "fileMetadata": False})
        rows = _launch([allowed, excluded], self._TWO_FILES)

        assert [r["filePath"] for r in rows["pallow"] if r["filePath"].startswith("/m")] == ["/m.glb"]
        assert not any(r["filePath"] in ("/m.glb", "/s.e57") for r in rows["pexcl"]), \
            f"gated step still carries file rows: {_sort_keys(rows['pexcl'])}"
        # The asset-level and database rows are unaffected by a fileMetadata gate.
        assert "db1:a1:/" in _sort_keys(rows["pexcl"])
        assert "db1::/" in _sort_keys(rows["pexcl"])

    def test_asset_metadata_excluded_drops_that_steps_asset_row(self):
        allowed = self._gated("pallow", self._ALL_ON)
        excluded = self._gated("pexcl", {**self._ALL_ON, "assetMetadata": False})
        rows = _launch([allowed, excluded], self._TWO_FILES)

        assert "db1:a1:/" in _sort_keys(rows["pallow"])
        assert "db1:a1:/" not in _sort_keys(rows["pexcl"]), \
            f"gated step still carries the asset-level row: {_sort_keys(rows['pexcl'])}"
        # Its file rows survive — only the asset scope was gated off.
        assert any(r["filePath"] == "/m.glb" for r in rows["pexcl"])

    def test_database_metadata_excluded_drops_that_steps_database_row(self):
        allowed = self._gated("pallow", self._ALL_ON)
        excluded = self._gated("pexcl", {**self._ALL_ON, "databaseMetadata": False})
        rows = _launch([allowed, excluded], self._TWO_FILES)

        assert "db1::/" in _sort_keys(rows["pallow"])
        assert "db1::/" not in _sort_keys(rows["pexcl"]), \
            f"gated step still carries the database row: {_sort_keys(rows['pexcl'])}"
        assert not any(r["scope"] == "database" for r in rows["pexcl"])

    def test_a_steps_gate_does_not_affect_its_sibling(self):
        # The narrowing is per step, so one gated step must not narrow another's report.
        rows = _launch([self._gated("pa", {**self._ALL_ON, "databaseMetadata": False}),
                        self._gated("pb", self._ALL_ON)],
                       self._TWO_FILES)
        assert not any(r["scope"] == "database" for r in rows["pa"])
        assert any(r["scope"] == "database" for r in rows["pb"])
