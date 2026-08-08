# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the asset-less multi-file execute handler (executeWorkflowV2, WB5.2).

Covers the pure helpers (key resolution, grouped-metadata gating, template-resolution wiring, the
concurrency-guard candidate generator) and the handler's gate/authorization/launch orchestration via
mocked tables + SFN + S3. The handler loads env vars at import; conftest seeds table-name overrides."""

import json
import os
import sys
import types
from types import SimpleNamespace

import botocore
import pytest
from unittest.mock import MagicMock, patch

# executeWorkflowV2 loads these at import.
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

# handlers.workflows package __init__ imports get_task_builder at import time; the shared mock package
# does not provide it, so register a lightweight stub before importing the handler.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ewv2

MOD = "backend.backend.handlers.workflows.executeWorkflow"


@pytest.mark.unit
class TestKeyResolution:
    def test_resolve_full_key_no_duplication(self):
        # relative already prefixed with the asset root -> used as-is (no duplication).
        assert ewv2._resolve_full_key("assetX/", "assetX/a/b.glb") == "assetX/a/b.glb"

    def test_resolve_full_key_joins(self):
        assert ewv2._resolve_full_key("assetX/", "/a/b.glb") == "assetX/a/b.glb"
        assert ewv2._resolve_full_key("assetX", "a/b.glb") == "assetX/a/b.glb"

    def test_asset_root_key_from_location(self):
        assert ewv2._asset_root_key({"assetLocation": {"Key": "root/"}}) == "root/"
        assert ewv2._asset_root_key({}) == ""


@pytest.mark.unit
class TestGroupedMetadataGate:
    def test_metadata_gate_off_skips_fetches(self):
        selected = [{"databaseId": "db", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": ""}]
        assets = {("db", "a1"): {"assetName": "A1", "description": "", "tags": []}}
        gate = {"assetMetadata": False, "fileMetadata": False, "fileAttributes": False,
                "databaseMetadata": False}
        with patch(f"{MOD}._fetch_metadata") as mfetch, patch(f"{MOD}._fetch_file_metadata") as mfile:
            env = ewv2._build_grouped_metadata(selected, assets, gate, {"requestContext": {}})
        mfetch.assert_not_called()
        mfile.assert_not_called()
        assert env["schemaVersion"] == 2
        assert env["assets"][0]["assetId"] == "a1"
        # asset-level '/' record + one file record.
        keys = [f["fileKey"] for f in env["assets"][0]["files"]]
        assert "/" in keys and "/f.glb" in keys

    def test_metadata_gate_on_fetches_and_simplifies(self):
        selected = [{"databaseId": "db", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": ""}]
        assets = {("db", "a1"): {"assetName": "A1"}}
        gate = {"assetMetadata": True, "fileMetadata": True, "fileAttributes": True}
        with patch(f"{MOD}._fetch_metadata", return_value=[{"metadataKey": "k", "metadataValue": "v"}]), \
             patch(f"{MOD}._fetch_file_metadata", return_value=[{"metadataKey": "fk", "metadataValue": "fv"}]):
            env = ewv2._build_grouped_metadata(selected, assets, gate, {"requestContext": {}})
        asset_record = next(f for f in env["assets"][0]["files"] if f["fileKey"] == "/")
        assert asset_record["metadata"] == {"k": "v"}
        file_record = next(f for f in env["assets"][0]["files"] if f["fileKey"] == "/f.glb")
        assert file_record["metadata"] == {"fk": "fv"}
        assert file_record["attributes"] == {"fk": "fv"}


@pytest.mark.unit
class TestMetadataSources:
    """Metadata sources are entities (a database and/or assets, never a file) the run reads stored
    metadata from. They are exempt from arity, so an arity-'none' run can name them, and they stay out
    of selected_inputs — which drives output-target resolution, the S3 existence check, the concurrency
    guard, and the input-FILE rows."""

    _EVENT = {"requestContext": {}}
    _GATE = {"assetMetadata": True, "fileMetadata": True, "fileAttributes": True,
             "databaseMetadata": True}

    def test_no_sources_builds_the_same_envelope_as_before(self):
        selected = [{"databaseId": "db", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": ""}]
        assets = {("db", "a1"): {"assetName": "A1"}}
        with patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_file_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_database_metadata") as m_db:
            with_args = ewv2._build_grouped_metadata(
                selected, assets, self._GATE, self._EVENT,
                metadata_source_assets=[], metadata_source_databases=[])
            without_args = ewv2._build_grouped_metadata(selected, assets, self._GATE, self._EVENT)
        m_db.assert_not_called()
        assert with_args == without_args
        # HARD CONSTRAINT: byte-identical to the asset-only shape. No empty 'databases' key.
        assert set(with_args) == {"schemaVersion", "assets"}
        assert json.dumps(with_args) == json.dumps(
            {"schemaVersion": 2, "assets": with_args["assets"]})

    def test_arity_none_asset_source_yields_one_group_with_only_the_root_record(self):
        # A metadata source contributes the asset-level ('/') record only: it is an entity, not a file,
        # so no per-file record is emitted for it.
        assets = {("db", "src"): {"assetName": "Source", "description": "d", "tags": ["t"]}}
        with patch(f"{MOD}._fetch_metadata",
                   return_value=[{"metadataKey": "k", "metadataValue": "v"}]), \
             patch(f"{MOD}._fetch_file_metadata") as m_file:
            env = ewv2._build_grouped_metadata(
                [], assets, self._GATE, self._EVENT,
                metadata_source_assets=[{"databaseId": "db", "assetId": "src"}])
        m_file.assert_not_called()
        assert len(env["assets"]) == 1
        group = env["assets"][0]
        assert group["assetId"] == "src"
        assert group["assetData"]["assetName"] == "Source"
        assert [f["fileKey"] for f in group["files"]] == ["/"]
        assert group["files"][0]["metadata"] == {"k": "v"}

    def test_database_source_becomes_its_own_top_level_section(self):
        with patch(f"{MOD}._fetch_database_metadata",
                   return_value=[{"metadataKey": "owner", "metadataValue": "eng"}]) as m_db:
            env = ewv2._build_grouped_metadata(
                [], {}, self._GATE, self._EVENT, metadata_source_databases=["src-db"])
        m_db.assert_called_once()
        assert env["schemaVersion"] == 2
        assert env["assets"] == []
        # A sibling of assets[], never inside it.
        assert env["databases"] == [{"databaseId": "src-db", "metadata": {"owner": "eng"}}]

    def test_every_captured_database_gets_its_own_entry(self):
        # One read and one entry per database, in capture order — a run over input files spanning three
        # databases carries all three, not the first or a merge.
        with patch(f"{MOD}._fetch_database_metadata",
                   side_effect=lambda d, e: [{"metadataKey": "site", "metadataValue": d}]) as m_db:
            env = ewv2._build_grouped_metadata(
                [], {}, self._GATE, self._EVENT,
                metadata_source_databases=["db1", "db2", "db3"])
        assert m_db.call_count == 3
        assert env["databases"] == [
            {"databaseId": "db1", "metadata": {"site": "db1"}},
            {"databaseId": "db2", "metadata": {"site": "db2"}},
            {"databaseId": "db3", "metadata": {"site": "db3"}}]

    def test_database_gate_off_reads_nothing_and_emits_no_section(self):
        gate = dict(self._GATE, databaseMetadata=False)
        with patch(f"{MOD}._fetch_database_metadata") as m_db:
            env = ewv2._build_grouped_metadata(
                [], {}, gate, self._EVENT, metadata_source_databases=["src-db"])
        m_db.assert_not_called()
        assert "databases" not in env

    def test_a_captured_database_with_no_metadata_still_emits_its_entry(self):
        # Distinguishes "the run captured this database and it carries nothing" from "no source".
        with patch(f"{MOD}._fetch_database_metadata", return_value=[]):
            env = ewv2._build_grouped_metadata(
                [], {}, self._GATE, self._EVENT, metadata_source_databases=["src-db"])
        assert env["databases"] == [{"databaseId": "src-db", "metadata": {}}]

    def test_a_source_asset_that_is_also_an_input_asset_appears_once(self):
        selected = [{"databaseId": "db", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": ""}]
        assets = {("db", "a1"): {"assetName": "A1"}}
        with patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_file_metadata", return_value=[]):
            env = ewv2._build_grouped_metadata(
                selected, assets, self._GATE, self._EVENT,
                metadata_source_assets=[{"databaseId": "db", "assetId": "a1"}])
        assert [g["assetId"] for g in env["assets"]] == ["a1"]
        # The input's file record survives — the source did not replace the group with a '/'-only one.
        assert [f["fileKey"] for f in env["assets"][0]["files"]] == ["/", "/f.glb"]

    def test_source_metadata_is_persisted_as_scope_tagged_rows(self):
        # The captured VALUES are persisted so the details response can surface them, with scope saying
        # what each row describes.
        envelope = ewv2.er.build_grouped_metadata_envelope(
            [ewv2.er.build_metadata_asset_group(
                "db", "src", files=[ewv2.er.build_metadata_file_record("/", metadata={"k": "v"})])],
            databases=[ewv2.er.build_metadata_database_group("src-db", {"owner": "eng"})])
        rows = list(ewv2.er.metadata_envelope_rows(envelope))
        by_scope = {r["scope"]: r for r in rows}
        assert by_scope["database"]["databaseId"] == "src-db"
        assert by_scope["database"]["assetId"] == "" and by_scope["database"]["filePath"] == "/"
        assert by_scope["asset"]["assetId"] == "src"

    def test_the_projection_subject_falls_back_to_the_first_source_asset(self):
        # At arity none there is no input file to project metadata tags against, so the first
        # metadata-source asset's asset-level record becomes the subject.
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        wf = dict(wf)
        wf["systemConfig"] = dict(wf["systemConfig"])
        wf["systemConfig"]["metadataInputs"] = {
            "assetMetadata": True, "fileMetadata": False,
            "fileAttributes": False, "databaseMetadata": True}
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [],
                "metadataSourceAssets": [{"databaseId": "db1", "assetId": "a1"}],
                "metadataSourceDatabaseId": "db1"}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_metadata",
                   return_value=[{"metadataKey": "PROMPT", "metadataValue": "on the asset"}]), \
             patch(f"{MOD}._fetch_database_metadata",
                   return_value=[{"metadataKey": "owner", "metadataValue": "eng"}]), \
             patch(f"{MOD}.s3c") as m_s3, patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        metadata_puts = [c for c in m_s3.put_object.call_args_list
                         if c.kwargs.get("Key", "").endswith("metadata.json")]
        assert metadata_puts, "the execution metadata file was not written"
        envelope = json.loads(metadata_puts[0].kwargs["Body"].decode("utf-8"))
        assert envelope["assets"][0]["assetId"] == "a1"
        assert envelope["databases"] == [{"databaseId": "db1", "metadata": {"owner": "eng"}}]
        # The renderer's legacy view resolves against the source asset, not an empty subject.
        view = ewv2.er.to_legacy_vams_view(envelope, "db1", "a1", "/")
        assert view["VAMS"]["assetMetadata"] == {"PROMPT": "on the asset"}
        assert view["VAMS"]["databaseMetadata"] == {"owner": "eng"}

    def test_source_assets_do_not_become_input_files(self):
        # The whole point of separate request fields: an arity-'none' workflow rejects input files, so a
        # source that leaked into inputFiles would fail its own validation (and would resolve an output
        # target, be S3-checked, and be written as an input row).
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [], "metadataSourceAssets": [{"databaseId": "db1", "assetId": "a1"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._input_exists_in_s3") as m_exists, \
             patch(f"{MOD}.s3c") as m_s3, patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        m_exists.assert_not_called()
        sent = json.loads(m_sfn.start_execution.call_args.kwargs["input"])
        assert sent["outputLocationType"] == "none"
        manifest_puts = [c for c in m_s3.put_object.call_args_list
                         if c.kwargs.get("Key", "").endswith("pipeline1/manifest.json")]
        manifest = json.loads(manifest_puts[0].kwargs["Body"].decode("utf-8"))
        assert manifest["inputFiles"] == []

    def test_multiple_source_assets_need_cross_asset_allowed(self):
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [],
                "metadataSourceAssets": [{"databaseId": "db1", "assetId": "a1"},
                                         {"databaseId": "db1", "assetId": "a2"}]}
        with p["get_workflow"], p["get_pipeline"], p["enforcer"], p["claims"], \
             patch(f"{MOD}.sfn_client") as m_sfn:
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        assert "metadata-source" in json.loads(resp["body"])["message"].lower()
        m_sfn.start_execution.assert_not_called()

    def test_multiple_source_assets_allowed_when_the_workflow_spans_assets(self):
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        wf = dict(wf)
        wf["systemConfig"] = dict(wf["systemConfig"])
        wf["systemConfig"]["assetScope"] = {"crossAssetAllowed": True, "singleAssetOnly": False,
                                            "wholeAssetAllowed": True, "folderAllowed": True}
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [],
                "metadataSourceAssets": [{"databaseId": "db1", "assetId": "a1"},
                                         {"databaseId": "db1", "assetId": "a2"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}.s3c") as m_s3, patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        metadata_puts = [c for c in m_s3.put_object.call_args_list
                         if c.kwargs.get("Key", "").endswith("metadata.json")]
        envelope = json.loads(metadata_puts[0].kwargs["Body"].decode("utf-8"))
        assert [g["assetId"] for g in envelope["assets"]] == ["a1", "a2"]

    def test_a_missing_source_asset_is_404_and_an_unauthorized_one_is_403(self):
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [], "metadataSourceAssets": [{"databaseId": "db1", "assetId": "gone"}]}
        with p["get_workflow"], p["get_pipeline"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._get_asset", return_value=None):
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 404

        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        enforcer.enforce.side_effect = lambda obj, action, *a, **k: obj.get("object__type") != "asset"
        with p["get_workflow"], p["get_pipeline"], p["claims"], \
             patch(f"{MOD}._get_asset", return_value=dict(_ASSET)), \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer):
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 403

    def test_the_source_database_needs_a_database_get(self):
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [], "metadataSourceDatabaseId": "src-db"}
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        enforcer.enforce.side_effect = lambda obj, action, *a, **k: obj.get("object__type") != "database"
        with p["get_workflow"], p["get_pipeline"], p["claims"], \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.sfn_client") as m_sfn:
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 403
        m_sfn.start_execution.assert_not_called()
        database_objects = [c.args[0] for c in enforcer.enforce.call_args_list
                            if c.args[0].get("object__type") == "database"]
        # Authorized on its ids alone — no database record is read to build the object.
        assert database_objects == [{"databaseId": "src-db", "object__type": "database"}]

    def test_the_selection_is_recorded_on_the_configuration_row(self):
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [],
                "metadataSourceAssets": [{"databaseId": "db1", "assetId": "a1"}],
                "metadataSourceDatabaseId": "src-db"}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_database_metadata",
                   return_value=[{"metadataKey": "owner", "metadataValue": "eng"}]), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            tables = {}
            m_dynamo.Table.side_effect = lambda name: tables.setdefault(name, MagicMock())
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        cfg_rows = [c.kwargs["Item"] for c in tables["t-wf-cfg"].put_item.call_args_list]
        assert len(cfg_rows) == 1
        assert cfg_rows[0]["metadataSourceAssets"] == [{"databaseId": "db1", "assetId": "a1"}]
        assert cfg_rows[0]["inputMetadataDatabaseId"] == "src-db"
        # The captured set is recorded too — it is what the read paths gate on. The named database
        # leads, then the source asset's own database.
        assert cfg_rows[0]["metadataSourceDatabases"] == ["src-db", "db1"]
        # No input-FILE row: a re-run would re-emit it as inputFiles and fail its own arity check.
        assert tables["t-wf-inputs"].put_item.call_count == 0
        # The captured values ARE persisted, scope-tagged. They go out through the table's batch writer,
        # so the written items are on the context manager it yields.
        md_rows = [c.kwargs["Item"] for c in
                   tables["t-pin-md"].batch_writer.return_value.__enter__.return_value
                   .put_item.call_args_list]
        assert [r["scope"] for r in md_rows] == ["database", "database"]
        assert [r["databaseId:assetId:filePath"] for r in md_rows] == ["src-db::/", "db1::/"]

    def test_a_source_asset_alone_captures_its_database_on_a_file_less_run(self):
        # A file-less run naming only a metadata-source asset: the asset's database is captured, reaches
        # the envelope, and is recorded — no named metadataSourceDatabaseId is needed for the run to see
        # database metadata, and the "captured no metadata-source database" warning does not fire.
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        wf = dict(wf)
        wf["systemConfig"] = dict(wf["systemConfig"])
        wf["systemConfig"]["metadataInputs"] = {
            "assetMetadata": True, "fileMetadata": False,
            "fileAttributes": False, "databaseMetadata": True}
        pipe = dict(pipe)
        pipe["systemConfig"] = dict(pipe["systemConfig"])
        pipe["systemConfig"]["metadataInputs"] = {"assetMetadata": True, "databaseMetadata": True}
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [], "metadataSourceAssets": [{"databaseId": "db1", "assetId": "a1"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_database_metadata",
                   return_value=[{"metadataKey": "owner", "metadataValue": "eng"}]) as m_db, \
             patch(f"{MOD}.s3c") as m_s3, patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            tables = {}
            m_dynamo.Table.side_effect = lambda name: tables.setdefault(name, MagicMock())
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        assert [c.args[0] for c in m_db.call_args_list] == ["db1"]
        envelope = json.loads(next(
            c for c in m_s3.put_object.call_args_list
            if c.kwargs.get("Key", "").endswith("metadata.json")).kwargs["Body"].decode("utf-8"))
        assert envelope["databases"] == [{"databaseId": "db1", "metadata": {"owner": "eng"}}]
        cfg_row = tables["t-wf-cfg"].put_item.call_args_list[0].kwargs["Item"]
        assert cfg_row["metadataSourceDatabases"] == ["db1"]
        assert cfg_row["inputMetadataDatabaseId"] == ""
        warnings = json.loads(resp["body"])["message"]["warnings"] or []
        assert not any("database metadata" in w for w in warnings), warnings

    def test_a_denied_source_assets_database_is_skipped_not_fatal_on_a_file_less_run(self):
        # The derived-skip rule holds on the arity-'none' path: the caller may read the source asset but
        # not its database, and the run still launches capturing no database metadata.
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        wf = dict(wf)
        wf["systemConfig"] = dict(wf["systemConfig"])
        wf["systemConfig"]["metadataInputs"] = {
            "assetMetadata": False, "fileMetadata": False,
            "fileAttributes": False, "databaseMetadata": True}
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [], "metadataSourceAssets": [{"databaseId": "db1", "assetId": "a1"}]}
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        enforcer.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "database")
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["claims"], \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_database_metadata", return_value=[]) as m_db, \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            tables = {}
            m_dynamo.Table.side_effect = lambda name: tables.setdefault(name, MagicMock())
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        m_db.assert_not_called()
        cfg_row = tables["t-wf-cfg"].put_item.call_args_list[0].kwargs["Item"]
        assert cfg_row["metadataSourceDatabases"] == []

    def test_a_launch_warns_when_a_declared_source_was_not_named(self):
        # Never blocks: a pipeline that truly requires the metadata performs its own check. The warning
        # makes the otherwise-silent misconfiguration visible at launch.
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        wf = dict(wf)
        wf["systemConfig"] = dict(wf["systemConfig"])
        wf["systemConfig"]["metadataInputs"] = {
            "assetMetadata": True, "fileMetadata": False,
            "fileAttributes": False, "databaseMetadata": True}
        pipe = dict(pipe)
        pipe["systemConfig"] = dict(pipe["systemConfig"])
        pipe["systemConfig"]["metadataInputs"] = {"assetMetadata": True, "databaseMetadata": True}
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        with p["get_workflow"], p["get_pipeline"], p["default_bucket"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body={"inputFiles": []}), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        warnings = json.loads(resp["body"])["message"]["warnings"]
        assert len(warnings) == 2
        assert any("asset metadata" in w for w in warnings)
        assert any("database metadata" in w for w in warnings)

    def test_no_warning_once_the_sources_are_named(self):
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        wf = dict(wf)
        wf["systemConfig"] = dict(wf["systemConfig"])
        wf["systemConfig"]["metadataInputs"] = {
            "assetMetadata": True, "fileMetadata": False,
            "fileAttributes": False, "databaseMetadata": True}
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [],
                "metadataSourceAssets": [{"databaseId": "db1", "assetId": "a1"}],
                "metadataSourceDatabaseId": "src-db"}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_database_metadata", return_value=[]), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        assert json.loads(resp["body"])["message"]["warnings"] is None

    def test_a_gated_off_metadata_type_is_not_reported_as_missing_a_source(self):
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        wf = dict(wf)
        wf["systemConfig"] = dict(wf["systemConfig"])
        wf["systemConfig"]["metadataInputs"] = {
            "assetMetadata": False, "fileMetadata": False,
            "fileAttributes": False, "databaseMetadata": False}
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        with p["get_workflow"], p["get_pipeline"], p["default_bucket"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body={"inputFiles": []}), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        assert json.loads(resp["body"])["message"]["warnings"] is None


@pytest.mark.unit
class TestSourceDatabaseDerivation:
    """The databases a run captures metadata from. Every entity the run names contributes its own
    databaseId — each input file's asset and each metadata-source asset — because those are the databases
    the run's data lives in. The caller's single metadataSourceDatabaseId joins them only on the
    arity-'none' path, where there are no input files and it is the one database nameable directly."""

    _GATE = {"databaseMetadata": True}

    def _inputs(self, *pairs):
        return [{"databaseId": d, "assetId": a, "relativeFileKey": "/f.glb", "versionId": ""}
                for d, a in pairs]

    def test_input_files_derive_every_distinct_database(self):
        derived = ewv2._derive_metadata_source_databases(
            self._inputs(("db1", "a1"), ("db2", "a2"), ("db3", "a3")), [], "", self._GATE)
        assert derived == ["db1", "db2", "db3"]

    def test_repeated_databases_are_deduped_in_first_seen_order(self):
        derived = ewv2._derive_metadata_source_databases(
            self._inputs(("db2", "a1"), ("db1", "a2"), ("db2", "a3"), ("db1", "a4")),
            [], "", self._GATE)
        assert derived == ["db2", "db1"]

    def test_source_assets_contribute_their_databases_after_the_input_files(self):
        derived = ewv2._derive_metadata_source_databases(
            self._inputs(("db1", "a1")),
            [{"databaseId": "db-src", "assetId": "s1"}, {"databaseId": "db1", "assetId": "s2"}],
            "", self._GATE)
        # db1 already came from the input file, so the source asset does not repeat it.
        assert derived == ["db1", "db-src"]

    def test_the_named_database_is_ignored_when_input_files_exist(self):
        # It is the arity-'none' field: with input files the run's own databases are authoritative, so
        # honoring it too would capture a database nothing in the run points at.
        derived = ewv2._derive_metadata_source_databases(
            self._inputs(("db1", "a1")), [], "unrelated-db", self._GATE)
        assert derived == ["db1"]

    def test_the_named_database_is_the_whole_set_without_input_files(self):
        assert ewv2._derive_metadata_source_databases([], [], "src-db", self._GATE) == ["src-db"]

    def test_a_source_asset_contributes_its_database_without_input_files(self):
        # A metadata-source asset names a database the caller clearly intends, so it contributes it on
        # the arity-'none' path too — the run reads the asset's own database, not nothing.
        assert ewv2._derive_metadata_source_databases(
            [], [{"databaseId": "db1", "assetId": "s1"}], "", self._GATE) == ["db1"]

    def test_the_named_database_leads_the_source_assets_databases(self):
        assert ewv2._derive_metadata_source_databases(
            [], [{"databaseId": "db1", "assetId": "s1"}, {"databaseId": "src-db", "assetId": "s2"}],
            "src-db", self._GATE) == ["src-db", "db1"]

    def test_no_input_files_and_no_entities_captures_nothing(self):
        assert ewv2._derive_metadata_source_databases([], [], "", self._GATE) == []

    def test_the_gate_off_captures_nothing_from_either_shape(self):
        off = {"databaseMetadata": False}
        assert ewv2._derive_metadata_source_databases(
            self._inputs(("db1", "a1")), [], "", off) == []
        assert ewv2._derive_metadata_source_databases([], [], "src-db", off) == []

    def test_the_gate_defaults_on(self):
        assert ewv2._derive_metadata_source_databases([], [], "src-db", {}) == ["src-db"]
        assert ewv2._derive_metadata_source_databases([], [], "src-db", None) == ["src-db"]


@pytest.mark.unit
class TestSourceDatabaseAuthorization:
    """Every captured database needs its own database GET. A DERIVED database is skipped when denied
    (an asset GET does not imply a database GET, and metadata is optional/best-effort), while the
    caller's NAMED database fails the launch — they asked for something they may not read."""

    def _enforcer(self, denied_databases=()):
        enf = MagicMock()
        enf.enforceAPI.return_value = True
        enf.enforce.side_effect = lambda obj, action, *a, **k: not (
            obj.get("object__type") == "database" and obj.get("databaseId") in denied_databases)
        return enf

    def test_one_get_per_derived_database(self):
        enforcer = self._enforcer()
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.claims_and_roles", {"tokens": ["u1"]}):
            err, assets, databases = ewv2._resolve_and_authorize_metadata_sources(
                [], ["db1", "db2", "db3"], {})
        assert err is None and databases == ["db1", "db2", "db3"]
        checked = [c.args[0]["databaseId"] for c in enforcer.enforce.call_args_list
                   if c.args[0].get("object__type") == "database"]
        assert checked == ["db1", "db2", "db3"]

    def test_a_denied_derived_database_is_skipped_not_fatal(self):
        # The caller can read the input files but not their database. The execution still launches; it
        # simply captures no metadata for that database, and the resolved set says so, so no read path
        # later gates on metadata this run never captured.
        with patch(f"{MOD}.CasbinEnforcer", return_value=self._enforcer({"db2"})), \
             patch(f"{MOD}.claims_and_roles", {"tokens": ["u1"]}):
            err, assets, databases = ewv2._resolve_and_authorize_metadata_sources(
                [], ["db1", "db2", "db3"], {})
        assert err is None
        assert databases == ["db1", "db3"]

    def test_a_denied_named_database_fails_the_launch(self):
        with patch(f"{MOD}.CasbinEnforcer", return_value=self._enforcer({"src-db"})), \
             patch(f"{MOD}.claims_and_roles", {"tokens": ["u1"]}):
            err, assets, databases = ewv2._resolve_and_authorize_metadata_sources(
                [], ["src-db"], {}, named_database_ids=["src-db"])
        assert err is not None and err["statusCode"] == 403
        assert databases is None

    def test_named_and_derived_databases_in_one_set_keep_their_own_denial_rule(self):
        # A file-less run naming a database AND a metadata-source asset carries both categories at once:
        # the named database still fails the launch when denied, while the asset's derived database is
        # skipped, so lacking access to one source asset's database cannot fail an otherwise-valid run.
        with patch(f"{MOD}.CasbinEnforcer", return_value=self._enforcer({"derived-db"})), \
             patch(f"{MOD}.claims_and_roles", {"tokens": ["u1"]}):
            err, assets, databases = ewv2._resolve_and_authorize_metadata_sources(
                [], ["src-db", "derived-db"], {}, named_database_ids=["src-db"])
        assert err is None
        assert databases == ["src-db"]

        with patch(f"{MOD}.CasbinEnforcer", return_value=self._enforcer({"src-db"})), \
             patch(f"{MOD}.claims_and_roles", {"tokens": ["u1"]}):
            err, assets, databases = ewv2._resolve_and_authorize_metadata_sources(
                [], ["src-db", "derived-db"], {}, named_database_ids=["src-db"])
        assert err is not None and err["statusCode"] == 403

    def test_a_multi_database_launch_records_and_captures_the_derived_set(self):
        # End to end: two input assets in two databases -> two database reads, two envelope entries, two
        # scope-'database' metadata rows, and the derived set on the configuration row.
        wf = dict(_WORKFLOW)
        wf["systemConfig"] = dict(_WORKFLOW["systemConfig"])
        wf["systemConfig"]["inputFileArity"] = "multi"
        wf["systemConfig"]["assetScope"] = {"crossAssetAllowed": True, "singleAssetOnly": False,
                                            "wholeAssetAllowed": True, "folderAllowed": True}
        wf["systemConfig"]["metadataInputs"] = {"assetMetadata": False, "fileMetadata": False,
                                                "fileAttributes": False, "databaseMetadata": True}
        pipe = dict(_PIPELINE)
        pipe["systemConfig"] = dict(_PIPELINE["systemConfig"])
        pipe["systemConfig"]["inputFileArity"] = "multi"
        pipe["systemConfig"]["assetScope"] = wf["systemConfig"]["assetScope"]
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
                               {"databaseId": "db2", "assetId": "a2", "relativeFileKey": "/g.glb"}],
                "outputAssetId": "a1", "outputDatabaseId": "db1"}
        with p["get_workflow"], p["get_pipeline"], p["default_bucket"], p["asset_bucket"], \
             p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._get_asset",
                   side_effect=lambda d, a: dict(_ASSET, databaseId=d, assetId=a,
                                                 assetLocation={"Key": f"{a}/"})), \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_database_metadata",
                   side_effect=lambda d, e: [{"metadataKey": "site", "metadataValue": d}]) as m_db, \
             patch(f"{MOD}.s3c") as m_s3, patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            tables = {}
            m_dynamo.Table.side_effect = lambda name: tables.setdefault(name, MagicMock())
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        assert sorted(c.args[0] for c in m_db.call_args_list) == ["db1", "db2"]
        envelope = json.loads(next(
            c for c in m_s3.put_object.call_args_list
            if c.kwargs.get("Key", "").endswith("metadata.json")).kwargs["Body"].decode("utf-8"))
        assert envelope["databases"] == [{"databaseId": "db1", "metadata": {"site": "db1"}},
                                         {"databaseId": "db2", "metadata": {"site": "db2"}}]
        cfg_row = tables["t-wf-cfg"].put_item.call_args_list[0].kwargs["Item"]
        assert cfg_row["metadataSourceDatabases"] == ["db1", "db2"]
        # The caller named none, and a run with input files derives rather than naming.
        assert cfg_row["inputMetadataDatabaseId"] == ""
        db_rows = [c.kwargs["Item"] for c in
                   tables["t-pin-md"].batch_writer.return_value.__enter__.return_value
                   .put_item.call_args_list
                   if c.kwargs["Item"]["scope"] == "database"]
        assert [r["databaseId:assetId:filePath"] for r in db_rows] == ["db1::/", "db2::/"]

    def test_an_input_file_run_does_not_record_the_named_database(self):
        # The field is arity-'none' only; recording it for a run that derived its databases would make
        # the read path gate on a database the run never read.
        p = TestExecuteOrchestration()._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}],
                "metadataSourceDatabaseId": "unrelated-db"}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_database_metadata", return_value=[]) as m_db, \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            tables = {}
            m_dynamo.Table.side_effect = lambda name: tables.setdefault(name, MagicMock())
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        assert [c.args[0] for c in m_db.call_args_list] == ["db1"]
        cfg_row = tables["t-wf-cfg"].put_item.call_args_list[0].kwargs["Item"]
        assert cfg_row["metadataSourceDatabases"] == ["db1"]
        assert cfg_row["inputMetadataDatabaseId"] == ""


@pytest.mark.unit
class TestPerEntityMetadataCap:
    """Metadata is bounded at MAX_METADATA_ENTRIES_PER_ENTITY entries PER ENTITY ROW — per database, per
    asset, per file's metadata, per file's attributes — so a run over many entities keeps each entity's
    full budget while no single row can grow without bound. Truncation is deterministic and logged."""

    _EVENT = {"requestContext": {}}
    _GATE = {"assetMetadata": True, "fileMetadata": True, "fileAttributes": True,
             "databaseMetadata": True}

    def _array(self, count):
        # Zero-padded so sorted() order is the numeric order, making the retained subset legible.
        return [{"metadataKey": f"k{i:05d}", "metadataValue": str(i)} for i in range(count)]

    def test_under_the_cap_is_untouched(self):
        out = ewv2._simplify_metadata_array(self._array(ewv2.MAX_METADATA_ENTRIES_PER_ENTITY))
        assert len(out) == ewv2.MAX_METADATA_ENTRIES_PER_ENTITY

    def test_over_the_cap_retains_exactly_the_cap(self):
        out = ewv2._simplify_metadata_array(self._array(ewv2.MAX_METADATA_ENTRIES_PER_ENTITY + 250))
        assert len(out) == ewv2.MAX_METADATA_ENTRIES_PER_ENTITY

    def test_truncation_is_deterministic_by_key_order(self):
        # NOT dict-insertion order: the metadata service's answer order is not guaranteed, so the same
        # entity must yield the same subset on every run (and on a re-run).
        forward = self._array(ewv2.MAX_METADATA_ENTRIES_PER_ENTITY + 100)
        assert (ewv2._simplify_metadata_array(forward)
                == ewv2._simplify_metadata_array(list(reversed(forward))))
        out = ewv2._simplify_metadata_array(forward)
        assert sorted(out) == [f"k{i:05d}" for i in range(ewv2.MAX_METADATA_ENTRIES_PER_ENTITY)]

    def test_the_cap_is_logged_with_the_entity_named(self):
        # No silent caps: a truncated row must be attributable from the logs.
        with patch(f"{MOD}.logger") as m_logger:
            ewv2._simplify_metadata_array(
                self._array(ewv2.MAX_METADATA_ENTRIES_PER_ENTITY + 1),
                entity_label="database db-huge")
        m_logger.warning.assert_called_once()
        message = m_logger.warning.call_args.args[0]
        assert "database db-huge" in message and str(
            ewv2.MAX_METADATA_ENTRIES_PER_ENTITY) in message

    def test_no_warning_below_the_cap(self):
        with patch(f"{MOD}.logger") as m_logger:
            ewv2._simplify_metadata_array(self._array(3), entity_label="asset db:a1")
        m_logger.warning.assert_not_called()

    @pytest.mark.parametrize("read_kind", ["database", "asset", "fileMetadata", "fileAttributes"])
    def test_the_cap_applies_to_each_of_the_four_read_kinds(self, read_kind):
        # Every read the envelope makes passes through the one choke point, so each entity row is
        # bounded independently rather than the run as a whole.
        oversized = self._array(ewv2.MAX_METADATA_ENTRIES_PER_ENTITY + 10)
        small = self._array(2)
        selected = [{"databaseId": "db", "assetId": "a1", "relativeFileKey": "/f.glb",
                     "versionId": ""}]
        with patch(f"{MOD}._fetch_database_metadata",
                   return_value=oversized if read_kind == "database" else small), \
             patch(f"{MOD}._fetch_metadata",
                   return_value=oversized if read_kind == "asset" else small), \
             patch(f"{MOD}._fetch_file_metadata",
                   side_effect=lambda d, a, r, t, e: oversized if (
                       (t == "metadata" and read_kind == "fileMetadata")
                       or (t == "attribute" and read_kind == "fileAttributes")) else small):
            env = ewv2._build_grouped_metadata(
                selected, {("db", "a1"): {"assetName": "A1"}}, self._GATE, self._EVENT,
                metadata_source_databases=["db"])
        files = {f["fileKey"]: f for f in env["assets"][0]["files"]}
        sizes = {
            "database": len(env["databases"][0]["metadata"]),
            "asset": len(files["/"]["metadata"]),
            "fileMetadata": len(files["/f.glb"]["metadata"]),
            "fileAttributes": len(files["/f.glb"]["attributes"]),
        }
        assert sizes[read_kind] == ewv2.MAX_METADATA_ENTRIES_PER_ENTITY
        # Only the oversized row is capped; its siblings keep their own (small) sizes.
        assert [v for k, v in sizes.items() if k != read_kind] == [2, 2, 2]

    def test_each_entity_row_gets_its_own_budget(self):
        # Three databases at the cap yield 3 x cap entries in total — the bound is per row, not global.
        with patch(f"{MOD}._fetch_database_metadata",
                   return_value=self._array(ewv2.MAX_METADATA_ENTRIES_PER_ENTITY + 5)):
            env = ewv2._build_grouped_metadata(
                [], {}, self._GATE, self._EVENT,
                metadata_source_databases=["db1", "db2", "db3"])
        assert [len(g["metadata"]) for g in env["databases"]] == (
            [ewv2.MAX_METADATA_ENTRIES_PER_ENTITY] * 3)


@pytest.mark.unit
class TestPerEntityMetadataByteCap:
    """The entry cap bounds how MANY keys a row carries; it cannot bound how LARGE it is, because a
    metadata value has no length limit. A row is therefore also bounded by MAX_METADATA_BYTES_PER_ENTITY
    so it stays inside the 400 KB DynamoDB item limit — without it, a few hundred long values (or one very
    long one) fail the input-metadata write AFTER the state machine has started, killing a valid run."""

    _EVENT = {"requestContext": {}}
    _GATE = {"assetMetadata": True, "fileMetadata": True, "fileAttributes": True,
             "databaseMetadata": True}

    def _sized(self, count, key_length, value_length):
        return [{"metadataKey": f"k{i:0{max(1, key_length - 1)}d}"[:key_length],
                 "metadataValue": "v" * value_length} for i in range(count)]

    def _row_bytes(self, metadata):
        return len(json.dumps(ewv2.er.build_input_metadata_record(
            "pe1", "db", "", "/", metadata, "", scope="database")).encode("utf-8"))

    @pytest.mark.parametrize("count,key_length,value_length", [
        (1000, 256, 400),   # 648 KB before the byte cap
        (1000, 128, 512),   # 633 KB
        (500, 128, 700),    # 408 KB — under the ENTRY cap, still over the item limit
        (500, 64, 800),     # 426 KB
        (5000, 256, 1000),
    ])
    def test_a_row_stays_within_the_dynamodb_item_limit(self, count, key_length, value_length):
        out = ewv2._simplify_metadata_array(self._sized(count, key_length, value_length))
        assert self._row_bytes(out) < 400 * 1024
        assert len(out) <= ewv2.MAX_METADATA_ENTRIES_PER_ENTITY

    def test_one_entry_larger_than_the_whole_budget_costs_only_itself(self):
        # The oversized entry is skipped rather than ending the walk, so its siblings still travel.
        array = ([{"metadataKey": "a_huge",
                   "metadataValue": "x" * (ewv2.MAX_METADATA_BYTES_PER_ENTITY + 1)}]
                 + [{"metadataKey": f"b_small{i}", "metadataValue": "v"} for i in range(5)])
        out = ewv2._simplify_metadata_array(array)
        assert sorted(out) == [f"b_small{i}" for i in range(5)]

    def test_a_single_oversized_value_never_reaches_a_row(self):
        # The entry cap alone never fires here (one entry), so only the byte bound can catch it.
        out = ewv2._simplify_metadata_array(
            [{"metadataKey": "k", "metadataValue": "v" * (500 * 1024)}])
        assert out == {}

    def test_byte_truncation_is_deterministic_regardless_of_answer_order(self):
        array = self._sized(2000, 64, 400)
        first = ewv2._simplify_metadata_array(list(array))
        assert first == ewv2._simplify_metadata_array(list(reversed(array)))
        # Retained by sorted key order, so the subset is reproducible on a re-run.
        assert list(first) == sorted(first)
        assert 0 < len(first) < 2000

    def test_a_small_row_is_returned_untouched(self):
        array = self._sized(100, 64, 64)
        out = ewv2._simplify_metadata_array(array)
        assert len(out) == 100

    def test_the_byte_cap_is_logged_with_the_entity_named(self):
        with patch(f"{MOD}.logger") as m_logger:
            ewv2._simplify_metadata_array(self._sized(500, 64, 800),
                                          entity_label="database db-fat")
        m_logger.warning.assert_called_once()
        assert "database db-fat" in m_logger.warning.call_args.args[0]

    def test_a_truncated_row_is_reported_to_the_caller(self):
        # No silent caps: the truncation reaches the execute response, not only the logs.
        notices = []
        with patch(f"{MOD}._fetch_database_metadata", return_value=self._sized(500, 64, 800)):
            ewv2._build_grouped_metadata(
                [], {}, self._GATE, self._EVENT, metadata_source_databases=["db1"],
                notices=notices)
        assert len(notices) == 1
        assert "db1" in notices[0] and "per-entity metadata limits" in notices[0]

    def test_the_notice_lists_a_bounded_number_of_entities(self):
        # A run over hundreds of entities returns one bounded warning, not one per entity.
        entities = [f"e{i}" for i in range(ewv2.MAX_METADATA_NOTICE_ENTITIES_LISTED + 4)]
        (warning,) = ewv2._metadata_capture_warnings(entities, [])
        assert f"e{ewv2.MAX_METADATA_NOTICE_ENTITIES_LISTED}" not in warning
        assert "and 4 more" in warning
        assert str(len(entities)) in warning

    def test_no_notice_when_nothing_was_truncated(self):
        notices = []
        with patch(f"{MOD}._fetch_database_metadata", return_value=self._sized(3, 8, 8)):
            ewv2._build_grouped_metadata(
                [], {}, self._GATE, self._EVENT, metadata_source_databases=["db1"],
                notices=notices)
        assert notices == []


@pytest.mark.unit
class TestUnreadableSourceDatabase:
    """A metadata read that FAILED and a database that genuinely carries no metadata both leave an entry
    with empty metadata and persist no row, so with several databases a partial failure would otherwise be
    invisible. The failed read is reported instead."""

    _EVENT = {"requestContext": {}}
    _GATE = {"databaseMetadata": True}

    def test_a_failed_read_is_reported(self):
        notices = []
        with patch(f"{MOD}._fetch_database_metadata", return_value=None):
            env = ewv2._build_grouped_metadata(
                [], {}, self._GATE, self._EVENT, metadata_source_databases=["dbX"],
                notices=notices)
        # The entry stays, so the run still records which databases it covered.
        assert env["databases"] == [{"databaseId": "dbX", "metadata": {}}]
        assert len(notices) == 1
        assert "dbX" in notices[0] and "could not read" in notices[0]

    def test_an_empty_database_is_not_reported(self):
        notices = []
        with patch(f"{MOD}._fetch_database_metadata", return_value=[]):
            env = ewv2._build_grouped_metadata(
                [], {}, self._GATE, self._EVENT, metadata_source_databases=["dbEmpty"],
                notices=notices)
        assert env["databases"] == [{"databaseId": "dbEmpty", "metadata": {}}]
        assert notices == []

    def test_a_partial_failure_across_many_databases_is_reported(self):
        # The case the multi-database capture makes invisible: most databases read fine, a few do not.
        failing = {"db3", "db7"}
        with patch(f"{MOD}._fetch_database_metadata",
                   side_effect=lambda d, e: None if d in failing else [
                       {"metadataKey": "site", "metadataValue": d}]):
            notices = []
            env = ewv2._build_grouped_metadata(
                [], {}, self._GATE, self._EVENT,
                metadata_source_databases=[f"db{i}" for i in range(10)], notices=notices)
        assert len(env["databases"]) == 10
        assert [g["databaseId"] for g in env["databases"] if not g["metadata"]] == ["db3", "db7"]
        assert len(notices) == 1
        assert "db3" in notices[0] and "db7" in notices[0]

    def test_an_unreadable_database_surfaces_in_the_execute_response(self):
        wf, pipe = TestExecuteOrchestration()._results_only_workflow()
        wf = dict(wf)
        wf["systemConfig"] = dict(wf["systemConfig"])
        wf["systemConfig"]["metadataInputs"] = {
            "assetMetadata": False, "fileMetadata": False,
            "fileAttributes": False, "databaseMetadata": True}
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [], "metadataSourceDatabaseId": "src-db"}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_database_metadata", return_value=None), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        warnings = json.loads(resp["body"])["message"]["warnings"] or []
        assert any("src-db" in w and "could not read" in w for w in warnings), warnings


@pytest.mark.unit
class TestGlobalIsNotAMetadataSourceDatabase:
    """GLOBAL is the unscoped/all-databases keyword, not a database record whose metadata can be read —
    metadataSourceDatabaseId rejects it at the model layer for that reason. An input file or a
    metadata-source asset MAY live in GLOBAL, so the DERIVED set has to drop it: otherwise the launch
    spends a metadata read resolving nothing, and records a database whose GET no permission template
    grants, making the execution unreadable for every non-admin."""

    _GATE = {"databaseMetadata": True}

    def test_an_input_file_in_global_derives_no_database(self):
        assert ewv2._derive_metadata_source_databases(
            [{"databaseId": ewv2.GLOBAL_DATABASE, "assetId": "a1", "relativeFileKey": "/f.glb"}],
            [], "", self._GATE) == []

    def test_a_global_source_asset_is_dropped_and_real_databases_survive(self):
        assert ewv2._derive_metadata_source_databases(
            [{"databaseId": "dbZ", "assetId": "a1", "relativeFileKey": "/f.glb"}],
            [{"databaseId": ewv2.GLOBAL_DATABASE, "assetId": "g1"}], "", self._GATE) == ["dbZ"]

    def test_global_is_dropped_without_disturbing_order_or_dedupe(self):
        assert ewv2._derive_metadata_source_databases(
            [{"databaseId": "dbZ"}, {"databaseId": ewv2.GLOBAL_DATABASE},
             {"databaseId": "dbA"}, {"databaseId": "dbZ"}], [], "", self._GATE) == ["dbZ", "dbA"]

    def test_a_global_only_run_reads_no_database_metadata(self):
        wf = dict(_WORKFLOW)
        wf["systemConfig"] = dict(_WORKFLOW["systemConfig"])
        wf["systemConfig"]["metadataInputs"] = {
            "assetMetadata": False, "fileMetadata": False,
            "fileAttributes": False, "databaseMetadata": True}
        p = TestExecuteOrchestration()._patches(workflow=wf)
        # No output target: the run has one input asset, so the output locks to it. (Naming a
        # different one would be refused — this case is about metadata sources, not the output.)
        body = {"inputFiles": [{"databaseId": ewv2.GLOBAL_DATABASE, "assetId": "a1",
                                "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["default_bucket"], p["asset_bucket"], \
             p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._get_asset",
                   side_effect=lambda d, a: dict(_ASSET, databaseId=d, assetId=a,
                                                 assetLocation={"Key": f"{a}/"})), \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_file_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_database_metadata") as m_db, \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            tables = {}
            m_dynamo.Table.side_effect = lambda name: tables.setdefault(name, MagicMock())
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        m_db.assert_not_called()
        cfg_row = tables["t-wf-cfg"].put_item.call_args_list[0].kwargs["Item"]
        assert cfg_row["metadataSourceDatabases"] == []


@pytest.mark.unit
class TestMetadataServicePayloadIdentity:
    """The metadata-service invoke must carry the identity THIS execute arrived with. A cross-call
    (trigger dispatch, re-run) has no authorizer, and forwarding `authorizer: None` makes the metadata
    service's claims extraction fail — so a trigger-fired run would silently get no metadata."""

    def test_an_api_request_forwards_its_authorizer(self):
        event = {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": "u1"}}}}}
        payload = ewv2._metadata_service_payload("/database/db1/metadata", {"databaseId": "db1"}, event)
        assert payload["requestContext"]["authorizer"] == {"jwt": {"claims": {"sub": "u1"}}}
        assert "lambdaCrossCall" not in payload
        assert payload["pathParameters"] == {"databaseId": "db1"}
        assert payload["requestContext"]["http"] == {"path": "/database/db1/metadata", "method": "GET"}

    def test_a_cross_call_forwards_its_identity_instead_of_a_null_authorizer(self):
        event = {"requestContext": {"http": {"method": "POST"}},
                 "lambdaCrossCall": {"userName": "SYSTEM_USER"}}
        payload = ewv2._metadata_service_payload("/database/db1/metadata", {"databaseId": "db1"}, event)
        assert payload["lambdaCrossCall"] == {"userName": "SYSTEM_USER"}
        assert "authorizer" not in payload["requestContext"]

    def test_a_non_200_answer_is_logged_rather_than_silently_empty(self):
        # None, not [] — a read that failed must not look like a database that carries no metadata.
        stream = MagicMock()
        stream.read.return_value = json.dumps({"statusCode": 403, "body": "{}"}).encode("utf-8")
        with patch(f"{MOD}._metadata_service_lambda", return_value={"Payload": stream}), \
             patch(f"{MOD}.logger") as m_logger:
            assert ewv2._fetch_database_metadata("db1", {"requestContext": {}}) is None
        assert m_logger.warning.called

    def test_an_absent_payload_reports_a_failed_read(self):
        with patch(f"{MOD}._metadata_service_lambda", return_value={"Payload": ""}), \
             patch(f"{MOD}.logger") as m_logger:
            assert ewv2._fetch_database_metadata("db1", {"requestContext": {}}) is None
        assert m_logger.warning.called

    def test_an_exception_reports_a_failed_read(self):
        with patch(f"{MOD}._metadata_service_lambda", side_effect=RuntimeError("boom")), \
             patch(f"{MOD}.logger") as m_logger:
            assert ewv2._fetch_database_metadata("db1", {"requestContext": {}}) is None
        assert m_logger.exception.called

    def test_a_successful_answer_returns_the_metadata_list(self):
        stream = MagicMock()
        stream.read.return_value = json.dumps({
            "statusCode": 200,
            "body": json.dumps({"metadata": [{"metadataKey": "k", "metadataValue": "v"}]}),
        }).encode("utf-8")
        with patch(f"{MOD}._metadata_service_lambda", return_value={"Payload": stream}):
            result = ewv2._fetch_database_metadata("db1", {"requestContext": {}})
        assert result == [{"metadataKey": "k", "metadataValue": "v"}]

    def test_a_successful_read_of_an_empty_database_returns_an_empty_list(self):
        # [] is the distinct "read succeeded, this database carries nothing" answer, so an empty
        # database is never reported as an unreadable one.
        stream = MagicMock()
        stream.read.return_value = json.dumps({
            "statusCode": 200, "body": json.dumps({"metadata": []})}).encode("utf-8")
        with patch(f"{MOD}._metadata_service_lambda", return_value={"Payload": stream}), \
             patch(f"{MOD}.logger") as m_logger:
            assert ewv2._fetch_database_metadata("db1", {"requestContext": {}}) == []
        assert not m_logger.warning.called


@pytest.mark.unit
class TestConcurrencyGuard:
    def test_restriction_none_never_conflicts(self):
        inputs = [{"databaseId": "db", "assetId": "a", "relativeFileKey": "/f"}]
        assert ewv2._running_execution_exists("db", "wf", inputs, {}, "none") is False


@pytest.mark.unit
class TestPipelineResourceMapping:
    def test_lambda_resource(self):
        rec = {"executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}}}
        assert ewv2._pipeline_resource_arn(rec) == "fn"

    def test_sqs_resource(self):
        rec = {"executionConfig": {"executionType": "SQS", "sqs": {"queueUrl": "https://q"}}}
        assert ewv2._pipeline_resource_arn(rec) == "https://q"

    def test_eventbridge_resource(self):
        rec = {"executionConfig": {"executionType": "EventBridge", "eventBridge": {"busArn": "arn:bus"}}}
        assert ewv2._pipeline_resource_arn(rec) == "arn:bus"


def _event(method="POST", body=None, path_params=None):
    return {
        "requestContext": {"http": {"method": method, "path": "/workflows/db1/wf1/execute"},
                           "authorizer": {}},
        "pathParameters": path_params or {"workflowDatabaseId": "db1", "workflowId": "wf1"},
        "queryStringParameters": {},
        "headers": {"authorization": "Bearer t"},
        "body": json.dumps(body or {}),
    }


_WORKFLOW = {
    "databaseId": "db1", "workflowId": "wf1", "workflowName": "WF", "enabled": True, "archived": False,
    "workflow_arn": "arn:aws:states:us-east-1:1:stateMachine:vams-wf1", "jobNames": ["job-p1"],
    "specifiedPipelines": [{"pipelineDatabaseId": "db1", "pipelineId": "p1", "jobName": "p1"}],
    "systemConfig": {
        "inputFileArity": "one",
        "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                       "wholeAssetAllowed": False, "folderAllowed": False},
        "metadataInputs": {"assetMetadata": False, "fileMetadata": False, "fileAttributes": False},
        "inputFileFilters": {"allow": [], "exclude": []},
        "concurrencyRestriction": "none",
        "outputTarget": {"locationType": "asset", "allowOverride": False},
    },
}
_PIPELINE = {
    "databaseId": "db1", "pipelineId": "p1", "pipelineName": "P1", "enabled": True, "archived": False,
    "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"},
                        "waitForCallback": "Disabled"},
    "systemConfig": {"inputFileArity": "one", "requireTemplate": False,
                     "allowCustomTemplateOverride": False,
                     "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                                    "wholeAssetAllowed": False, "folderAllowed": False},
                     "inputFileFilters": {"allow": [], "exclude": []}},
}
_ASSET = {"databaseId": "db1", "assetId": "a1", "assetName": "A1", "bucketId": "bkt-1",
          "assetLocation": {"Key": "a1/"}}


def _allow_enforcer():
    e = MagicMock()
    e.enforce.return_value = True
    e.enforceAPI.return_value = True
    return e


@pytest.mark.unit
class TestExecuteOrchestration:
    def _patches(self, workflow=_WORKFLOW, pipeline=_PIPELINE):
        """Patch the record fetches, bucket resolution, S3, SFN, and table writers for a happy path."""
        return {
            "get_workflow": patch(f"{MOD}._get_workflow", return_value=dict(workflow)),
            "get_pipeline": patch(f"{MOD}._get_pipeline", return_value=dict(pipeline)),
            "get_asset": patch(f"{MOD}._get_asset", return_value=dict(_ASSET)),
            "default_bucket": patch(f"{MOD}._default_run_bucket",
                                    return_value={"bucketName": "run-bucket", "baseAssetsPrefix": ""}),
            "asset_bucket": patch(f"{MOD}._asset_bucket_details",
                                  return_value={"bucketName": "asset-bucket", "baseAssetsPrefix": ""}),
            "exists": patch(f"{MOD}._input_exists_in_s3", return_value=(True, "v-resolved")),
            "enforcer": patch(f"{MOD}.CasbinEnforcer", return_value=_allow_enforcer()),
            "claims": patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}),
        }

    def test_happy_path_launches_and_persists(self):
        p = self._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c") as m_s3, patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200
        out = json.loads(resp["body"])["message"]
        assert out["executionId"]
        m_sfn.start_execution.assert_called_once()
        # Run I/O written to the default run bucket.
        assert any(c.kwargs.get("Bucket") == "run-bucket" for c in m_s3.put_object.call_args_list)

    def test_workflow_and_pipeline_authorized_with_get_not_post(self):
        """Execution is authorized by Tier-1 on the execute route plus Tier-2 GET on the workflow and
        its pipelines. A POST on the workflow object means create/modify, so requiring it here would
        make 'may execute' imply 'may create workflows'."""
        p = self._patches()
        enforcer = _allow_enforcer()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["claims"], \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200
        actions_by_type = {}
        for call in enforcer.enforce.call_args_list:
            obj = call.args[0]
            actions_by_type.setdefault(obj.get("object__type"), set()).add(call.args[1])
        assert actions_by_type.get("workflow") == {"GET"}
        assert actions_by_type.get("pipeline") == {"GET"}
        # The output asset is genuinely written, so it keeps POST.
        assert "POST" in actions_by_type.get("asset", set())

    def test_disabled_workflow_blocks(self):
        wf = dict(_WORKFLOW); wf["enabled"] = False
        p = self._patches(workflow=wf)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["enforcer"], p["claims"]:
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        assert "disabled" in json.loads(resp["body"])["message"].lower()

    def test_deadline_cloud_pipeline_blocked_when_type_disabled(self):
        # A workflow whose pipeline is DeadlineCloud cannot execute when the deployment disabled the
        # type after the pipeline was created (createJob task + callback lambda are not deployed).
        dc_pipeline = dict(_PIPELINE)
        dc_pipeline["executionConfig"] = {"executionType": "DeadlineCloud",
                                          "deadlineCloud": {"farmId": "farm-1", "queueId": "queue-1"}}
        p = self._patches(pipeline=dc_pipeline)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["enforcer"], p["claims"], \
             patch.object(ewv2, "DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED", False):
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        assert "DeadlineCloud" in json.loads(resp["body"])["message"]

    def test_output_lock_multi_asset_blocks(self):
        # Inputs spanning two assets with NO explicit output target -> cannot resolve a single output
        # asset. (allowOverride no longer matters for multi-asset: an explicit output is honored when
        # supplied regardless, and its absence is the error.)
        p = self._patches()
        body = {"inputFiles": [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
            {"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/g.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["enforcer"], p["claims"]:
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        assert "does not resolve to a single input asset" in json.loads(resp["body"])["message"].lower()

    def test_multi_asset_explicit_output_honored_without_override(self):
        # Inputs spanning two assets + an explicit output target (both ids) -> honored regardless of
        # allowOverride (there is no single input asset to lock to). Launches.
        wf = dict(_WORKFLOW)
        wf["systemConfig"] = dict(_WORKFLOW["systemConfig"])
        wf["systemConfig"]["inputFileArity"] = "multi"
        wf["systemConfig"]["assetScope"] = {"crossAssetAllowed": True, "singleAssetOnly": False,
                                            "wholeAssetAllowed": True, "folderAllowed": True}
        pipe = dict(_PIPELINE)
        pipe["systemConfig"] = dict(_PIPELINE["systemConfig"])
        pipe["systemConfig"]["inputFileArity"] = "multi"
        pipe["systemConfig"]["assetScope"] = {"crossAssetAllowed": True, "singleAssetOnly": False,
                                              "wholeAssetAllowed": True, "folderAllowed": True}
        p = self._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [
                    {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
                    {"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/g.glb"}],
                "outputAssetId": "a1", "outputDatabaseId": "db1"}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200
        m_sfn.start_execution.assert_called_once()
        sent = json.loads(m_sfn.start_execution.call_args.kwargs["input"])
        assert sent["outputLocationType"] == "asset"
        assert sent["outputAssetId"] == "a1" and sent["outputDatabaseId"] == "db1"

    def test_multi_asset_output_asset_without_db_errors(self):
        # outputAssetId supplied without outputDatabaseId + multi-asset -> the both-ids error (not the
        # old generic "Could not resolve").
        wf = dict(_WORKFLOW)
        wf["systemConfig"] = dict(_WORKFLOW["systemConfig"])
        wf["systemConfig"]["inputFileArity"] = "multi"
        wf["systemConfig"]["assetScope"] = {"crossAssetAllowed": True, "singleAssetOnly": False,
                                            "wholeAssetAllowed": True, "folderAllowed": True}
        p = self._patches(workflow=wf)
        body = {"inputFiles": [
                    {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
                    {"databaseId": "db1", "assetId": "a2", "relativeFileKey": "/g.glb"}],
                "outputAssetId": "a1"}
        with p["get_workflow"], p["get_pipeline"], p["enforcer"], p["claims"]:
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        assert "does not resolve to a single input asset" in json.loads(resp["body"])["message"].lower()

    def test_single_input_override_refused_when_allow_override_false(self):
        # One input asset, allowOverride False + a DIFFERENT explicit output -> refused. Silently
        # relocking to the input asset would write the outputs somewhere the caller never named while
        # reporting a successful launch, which is the same contradiction the results-only branch rejects.
        p = self._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}],
                "outputAssetId": "otherAsset", "outputDatabaseId": "db1"}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        assert "allowoverride" in json.loads(resp["body"])["message"].lower()
        m_sfn.start_execution.assert_not_called()

    def test_single_input_override_naming_the_input_asset_is_a_noop(self):
        # The re-run replay path: _reconstruct_execute_request ALWAYS re-sends the recorded output
        # target, and for a run that did not override, that target IS the single input asset. Naming it
        # asks for nothing different, so it must launch even with allowOverride False — otherwise every
        # re-run of a non-overridden single-asset run would 400.
        p = self._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}],
                "outputAssetId": "a1", "outputDatabaseId": "db1"}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200
        sent = json.loads(m_sfn.start_execution.call_args.kwargs["input"])
        assert sent["outputAssetId"] == "a1" and sent["outputDatabaseId"] == "db1"

    def test_single_input_override_allowed_redirects(self):
        # allowOverride True + a different explicit output -> honored (the behavior the refusal above
        # must not have broken).
        wf = dict(_WORKFLOW)
        wf["systemConfig"] = dict(_WORKFLOW["systemConfig"])
        wf["systemConfig"]["outputTarget"] = {"locationType": "asset", "allowOverride": True}
        p = self._patches(workflow=wf)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}],
                "outputAssetId": "a2", "outputDatabaseId": "db1"}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200
        sent = json.loads(m_sfn.start_execution.call_args.kwargs["input"])
        assert sent["outputAssetId"] == "a2"

    def test_single_input_override_db_only_with_allow_override_errors(self):
        # allowOverride True but only outputDatabaseId supplied: there is no target ASSET, so the
        # override cannot be applied. Falling back to the input asset would silently ignore a named
        # database, so this is refused and says which field is missing.
        wf = dict(_WORKFLOW)
        wf["systemConfig"] = dict(_WORKFLOW["systemConfig"])
        wf["systemConfig"]["outputTarget"] = {"locationType": "asset", "allowOverride": True}
        p = self._patches(workflow=wf)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}],
                "outputDatabaseId": "otherDb"}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        message = json.loads(resp["body"])["message"].lower()
        # Must be the missing-field message, NOT the allowOverride-is-false one — this workflow DOES
        # allow overriding. Both messages name outputAssetId, so assert on the discriminating clause.
        assert "alone does not name a target asset" in message
        assert "allowoverride is false" not in message
        m_sfn.start_execution.assert_not_called()

    def test_single_input_no_output_target_still_locks_to_input(self):
        # The common case — no output ids supplied at all — is untouched: locked to the input asset.
        p = self._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200
        sent = json.loads(m_sfn.start_execution.call_args.kwargs["input"])
        assert sent["outputAssetId"] == "a1" and sent["outputDatabaseId"] == "db1"

    def _results_only_workflow(self):
        wf = dict(_WORKFLOW)
        wf["systemConfig"] = dict(_WORKFLOW["systemConfig"])
        wf["systemConfig"]["inputFileArity"] = "none"
        wf["systemConfig"]["outputTarget"] = {"locationType": "none", "allowOverride": False}
        pipe = dict(_PIPELINE)
        pipe["systemConfig"] = dict(_PIPELINE["systemConfig"])
        pipe["systemConfig"]["inputFileArity"] = "none"
        return wf, pipe

    def test_results_only_launches_no_output_asset(self):
        # Results-only workflow (locationType 'none', arity 'none'), no inputs, no output asset ->
        # launches; SFN input carries outputLocationType 'none' + empty ids; no output-asset authz;
        # no OutputsIndex row written.
        wf, pipe = self._results_only_workflow()
        p = self._patches(workflow=wf, pipeline=pipe)
        with p["get_workflow"], p["get_pipeline"], p["default_bucket"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._get_asset") as m_get_asset, \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            tables = {}
            m_dynamo.Table.side_effect = lambda name: tables.setdefault(name, MagicMock())
            resp = ewv2.lambda_handler(_event(body={"inputFiles": []}), MagicMock())
        assert resp["statusCode"] == 200
        m_sfn.start_execution.assert_called_once()
        sent = json.loads(m_sfn.start_execution.call_args.kwargs["input"])
        assert sent["outputLocationType"] == "none"
        assert sent["outputAssetId"] == "" and sent["outputDatabaseId"] == ""
        # No output asset was resolved/authorized.
        m_get_asset.assert_not_called()
        # OutputsIndex table was never written (sparse — no ':' ghost key).
        assert "t-wf-out-index" not in tables

    def test_results_only_contradiction_with_explicit_output(self):
        # locationType 'none' but an explicit output asset was supplied -> contradiction error.
        wf, pipe = self._results_only_workflow()
        p = self._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [], "outputAssetId": "a1", "outputDatabaseId": "db1"}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["enforcer"], p["claims"]:
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        assert "results-only" in json.loads(resp["body"])["message"].lower()

    def test_missing_input_file_404(self):
        p = self._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["asset_bucket"], \
             p["enforcer"], p["claims"], patch(f"{MOD}._input_exists_in_s3", return_value=(False, "")):
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 404

    def test_cross_validation_error_blocks(self):
        # Pipeline arity 'one' with zero input files -> validator hard error, no launch.
        p = self._patches()
        body = {"inputFiles": []}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}.sfn_client") as m_sfn:
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        body_msg = json.loads(resp["body"])["message"]
        # zero inputs -> output cannot resolve to a single asset (locked) OR validator arity error;
        # either way it must NOT launch.
        m_sfn.start_execution.assert_not_called()

    def test_missing_input_asset_returns_404(self):
        # A genuinely-missing input asset is a 404 (matches the rest of VAMS, which reports
        # not-found distinctly from unauthorized). Exists-but-no-access remains 403.
        p = self._patches()
        body = {"inputFiles": [{"databaseId": "smoke-db", "assetId": "missing-asset", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._get_asset", return_value=None):
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 404

    def test_unauthorized_input_asset_returns_403(self):
        # An input asset that exists but the caller cannot GET is a 403 (not 404).
        p = self._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        # CasbinEnforcer.enforce(obj, action): workflow + pipeline GET pass; asset GET is denied.
        enforcer.enforce.side_effect = lambda obj, action, *a, **k: obj.get("object__type") != "asset"
        with p["get_workflow"], p["get_pipeline"], p["claims"], \
             patch(f"{MOD}._get_asset", return_value={"assetId": "a1", "databaseId": "db1",
                                                      "assetName": "A1"}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer):
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 403

    def test_output_prefix_uses_jobname_not_pipelineid(self):
        # jobName parity: when the first pipeline ref's jobName differs from pipelineId, the manifest
        # output prefix must use the jobName-based folder (matching the ASL), not the pipelineId.
        wf = dict(_WORKFLOW)
        wf["specifiedPipelines"] = [{"pipelineDatabaseId": "db1", "pipelineId": "cadConverter",
                                     "jobName": "convertStep"}]
        wf["jobNames"] = ["uuid5-convertStep"]
        pipe = dict(_PIPELINE); pipe["pipelineId"] = "cadConverter"
        p = self._patches(workflow=wf, pipeline=pipe)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c") as m_s3, patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200
        # Find the manifest put (pipeline1/manifest.json) and assert its output prefix uses the jobName.
        manifest_puts = [c for c in m_s3.put_object.call_args_list
                         if c.kwargs.get("Key", "").endswith("pipeline1/manifest.json")]
        assert manifest_puts, "pipeline 1 manifest was not written"
        manifest = json.loads(manifest_puts[0].kwargs["Body"].decode("utf-8"))
        assert "pipelines/convertStep/" in manifest["outputs"]["files"]
        assert "pipelines/cadConverter/" not in manifest["outputs"]["files"]

    def test_same_id_pipelines_across_databases_no_collision(self):
        # Composite-key fix: two pipelines sharing pipelineId across GLOBAL + db resolve to distinct
        # config entries (keyed by pipelineDatabaseId:pipelineId), so neither overwrites the other.
        records = [
            {"databaseId": "GLOBAL", "pipelineId": "convert", "systemConfig": {}, "_jobName": "g"},
            {"databaseId": "db1", "pipelineId": "convert", "systemConfig": {}, "_jobName": "d"},
        ]
        params = {"convert": {}}  # both share the pipelineId-keyed params
        with patch(f"{MOD}._get_template_row", return_value=None), \
             patch(f"{MOD}._get_default_template_id", return_value=""):
            errors, resolved = ewv2._resolve_pipeline_configs(records, params, "run-bucket")
        assert errors == []
        assert set(resolved.keys()) == {"GLOBAL:convert", "db1:convert"}

    def test_require_template_auto_selects_pipeline_default(self):
        # A require-template pipeline with no run-supplied templateId falls back to the pipeline's
        # default template (isDefault), so the run resolves against it instead of erroring.
        records = [{"databaseId": "db1", "pipelineId": "p1",
                    "systemConfig": {"requireTemplate": True}, "_jobName": "j"}]
        params = {"p1": {}}  # no templateId supplied
        default_row = {"templateId": "def-tmpl", "configBody": "x: 1", "configFormat": "yaml",
                       "bodyStorage": "inline"}
        with patch(f"{MOD}._get_default_template_id", return_value="def-tmpl") as mock_def, \
             patch(f"{MOD}._get_template_row", return_value=default_row), \
             patch(f"{MOD}._rehydrate_template_row", return_value=default_row), \
             patch(f"{MOD}._load_tag_schema_fields", return_value=[]):
            errors, resolved = ewv2._resolve_pipeline_configs(records, params, "run-bucket")
        assert errors == []
        mock_def.assert_called_once_with("db1", "p1")
        assert resolved["db1:p1"]["templateId"] == "def-tmpl"

    def test_non_require_pipeline_does_not_auto_select_default(self):
        # A pipeline that does NOT require a template is left template-less when the run supplies
        # none, even if a default template exists — the default is a UI pre-selection only, never
        # auto-applied at execute time (would silently change a no-config run's config).
        records = [{"databaseId": "db1", "pipelineId": "p1",
                    "systemConfig": {"requireTemplate": False}, "_jobName": "j"}]
        params = {"p1": {}}  # no templateId supplied
        with patch(f"{MOD}._get_default_template_id", return_value="def-tmpl") as mock_def, \
             patch(f"{MOD}._get_template_row", return_value=None):
            errors, resolved = ewv2._resolve_pipeline_configs(records, params, "run-bucket")
        assert errors == []
        mock_def.assert_not_called()
        assert resolved["db1:p1"]["templateId"] == ""


@pytest.mark.unit
class TestPerPipelineFilteredManifest:
    """Pipeline 1's manifest carries only the inputs that pipeline accepts (its own inputFileFilters
    applied, none for arity 'none'), not the workflow's full selection."""

    def _multi_input_workflow(self, pipeline_filters=None, pipeline_arity="multi"):
        wf = dict(_WORKFLOW)
        wf["systemConfig"] = dict(_WORKFLOW["systemConfig"])
        wf["systemConfig"]["inputFileArity"] = "multi"
        pipe = dict(_PIPELINE)
        pipe["systemConfig"] = dict(_PIPELINE["systemConfig"])
        pipe["systemConfig"]["inputFileArity"] = pipeline_arity
        if pipeline_filters is not None:
            pipe["systemConfig"]["inputFileFilters"] = pipeline_filters
        return wf, pipe

    def _launch_and_read_manifest(self, wf, pipe, body):
        p = TestExecuteOrchestration()._patches(workflow=wf, pipeline=pipe)
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c") as m_s3, patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        manifest_puts = [c for c in m_s3.put_object.call_args_list
                         if c.kwargs.get("Key", "").endswith("pipeline1/manifest.json")]
        assert manifest_puts, "pipeline 1 manifest was not written"
        return json.loads(manifest_puts[0].kwargs["Body"].decode("utf-8"))

    def test_manifest_excludes_files_the_pipeline_filters_reject(self):
        # Workflow allows multiple files with no filters; the pipeline allows only *.glb. The manifest
        # must carry the .glb only — the pipeline never sees the .txt the workflow selected.
        wf, pipe = self._multi_input_workflow(pipeline_filters={"allow": ["*.glb"], "exclude": []})
        body = {"inputFiles": [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/notes.txt"}]}
        manifest = self._launch_and_read_manifest(wf, pipe, body)
        paths = [f["relativePath"] for f in manifest["inputFiles"]]
        assert paths == ["/f.glb"]

    def test_manifest_respects_pipeline_exclude_filters(self):
        wf, pipe = self._multi_input_workflow(
            pipeline_filters={"allow": [], "exclude": ["*.previewFile.*"]})
        body = {"inputFiles": [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.previewFile.png"}]}
        manifest = self._launch_and_read_manifest(wf, pipe, body)
        paths = [f["relativePath"] for f in manifest["inputFiles"]]
        assert paths == ["/f.glb"]

    def test_arity_none_pipeline_receives_no_input_files(self):
        # A pipeline that consumes no files gets an empty manifest input list even when the workflow
        # selected files (the validator sized it against zero inputs).
        wf, pipe = self._multi_input_workflow(pipeline_arity="none")
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        manifest = self._launch_and_read_manifest(wf, pipe, body)
        assert manifest["inputFiles"] == []

    def test_filtered_map_is_keyed_by_composite_pipeline_key(self):
        # Two same-id pipelines from different databases with different filters must not share a key.
        records = [
            {"databaseId": "GLOBAL", "pipelineId": "convert",
             "systemConfig": {"inputFileArity": "multi",
                              "inputFileFilters": {"allow": ["*.glb"], "exclude": []}}},
            {"databaseId": "db1", "pipelineId": "convert",
             "systemConfig": {"inputFileArity": "multi",
                              "inputFileFilters": {"allow": ["*.txt"], "exclude": []}}},
        ]
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
                    {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/n.txt"}]
        errors, filtered = ewv2._run_cross_validation(
            {"systemConfig": {"inputFileArity": "multi",
                              "assetScope": {"singleAssetOnly": True},
                              "inputFileFilters": {"allow": [], "exclude": []}}},
            records, {}, selected, {})
        assert errors == []
        assert set(filtered.keys()) == {"GLOBAL:convert", "db1:convert"}
        assert [i["relativeFileKey"] for i in filtered["GLOBAL:convert"]] == ["/f.glb"]
        assert [i["relativeFileKey"] for i in filtered["db1:convert"]] == ["/n.txt"]


@pytest.mark.unit
class TestStoredJobNamesGate:
    """A workflow whose record cannot supply the ASL's uuid-prefixed job names cannot execute: its
    outputs would land in a folder the end-state lambda never lists."""

    def test_missing_job_names_blocks_launch(self):
        wf = dict(_WORKFLOW)
        wf["jobNames"] = []
        p = TestExecuteOrchestration()._patches(workflow=wf)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["enforcer"], p["claims"], \
             patch(f"{MOD}.sfn_client") as m_sfn:
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        assert "state machine" in json.loads(resp["body"])["message"].lower()
        m_sfn.start_execution.assert_not_called()

    def test_short_job_names_blocks_launch(self):
        wf = dict(_WORKFLOW)
        wf["specifiedPipelines"] = [
            {"pipelineDatabaseId": "db1", "pipelineId": "p1", "jobName": "p1"},
            {"pipelineDatabaseId": "db1", "pipelineId": "p1", "jobName": "p1b"}]
        wf["jobNames"] = ["uuid5-p1"]
        p = TestExecuteOrchestration()._patches(workflow=wf)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["enforcer"], p["claims"], \
             patch(f"{MOD}.sfn_client") as m_sfn:
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        m_sfn.start_execution.assert_not_called()

    def test_stored_job_names_error_accepts_matching_record(self):
        assert ewv2._stored_job_names_error({"jobNames": ["uuid5-p1"]}, [{"pipelineId": "p1"}]) == ""


@pytest.mark.unit
class TestResolveRequestedOutputExtension:
    """The execution's output extension: the request's value, else the workflow's stored default.

    Normalization itself is covered by tests/common/test_outputPathExtension.py; this class covers the
    fallback decision, which is what makes a workflow-level default reachable.
    """

    def _workflow(self, default):
        return {"systemConfig": {"defaultOutputFileBaseExecutionPathExtension": default}}

    def test_a_request_value_wins_over_the_workflow_default(self):
        request = SimpleNamespace(outputFileBaseExecutionPathExtension="/from-request/")
        assert (ewv2._resolve_requested_output_extension(request, self._workflow("/wf-default/"))
                == "/from-request/")

    def test_an_omitted_request_value_falls_back_to_the_workflow_default(self):
        request = SimpleNamespace(outputFileBaseExecutionPathExtension=None)
        assert (ewv2._resolve_requested_output_extension(request, self._workflow("/wf-default/"))
                == "/wf-default/")

    def test_an_explicit_root_is_a_deliberate_choice_and_is_not_overridden(self):
        """"" and "/" say 'write at the asset root'. Treating them as 'unset' would make a workflow
        default impossible to opt out of at execute time."""
        for explicit in ("", "/"):
            request = SimpleNamespace(outputFileBaseExecutionPathExtension=explicit)
            assert (ewv2._resolve_requested_output_extension(
                request, self._workflow("/wf-default/")) == "/")

    def test_no_request_value_and_no_default_means_the_asset_root(self):
        request = SimpleNamespace(outputFileBaseExecutionPathExtension=None)
        assert ewv2._resolve_requested_output_extension(request, self._workflow("")) == "/"
        assert ewv2._resolve_requested_output_extension(request, {}) == "/"

    def test_the_workflow_default_is_returned_with_its_tags_unresolved(self):
        """The stored default is templated; substitution happens later, once the launch has a
        manifest — so this step must not consume or mangle the placeholders."""
        request = SimpleNamespace(outputFileBaseExecutionPathExtension=None)
        assert (ewv2._resolve_requested_output_extension(request, self._workflow("{{jobName}}"))
                == "/{{jobName}}")


@pytest.mark.unit
class TestRenderOutputPathExtension:
    """The output base path extension's {{dynamicTag}} placeholders are substituted at launch, so the
    value reaching the manifest, the SFN input, and the configuration row is a concrete path."""

    def _launch(self, body, workflow=None):
        p = (TestExecuteOrchestration()._patches(workflow=workflow) if workflow
             else TestExecuteOrchestration()._patches())
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c") as m_s3, patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        return resp, m_s3, m_sfn

    def test_placeholder_resolved_in_manifest_and_sfn_input(self):
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}],
                "outputFileBaseExecutionPathExtension": "/out/{{firstAssetFileFileNameNoExt}}/"}
        resp, m_s3, m_sfn = self._launch(body)
        assert resp["statusCode"] == 200, resp["body"]
        sent = json.loads(m_sfn.start_execution.call_args.kwargs["input"])
        assert sent["outputFileBaseExecutionPathExtension"] == "/out/f/"
        manifest_puts = [c for c in m_s3.put_object.call_args_list
                         if c.kwargs.get("Key", "").endswith("pipeline1/manifest.json")]
        manifest = json.loads(manifest_puts[0].kwargs["Body"].decode("utf-8"))
        assert manifest["outputTarget"]["fileBaseExecutionPathExtension"] == "/out/f/"

    def test_extension_without_placeholders_keeps_its_authored_trailing_slash(self):
        """The trailing slash decides folder-vs-glue placement, so it is carried through verbatim
        rather than forced on."""
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}],
                "outputFileBaseExecutionPathExtension": "runs/2026"}
        resp, _m_s3, m_sfn = self._launch(body)
        assert resp["statusCode"] == 200, resp["body"]
        sent = json.loads(m_sfn.start_execution.call_args.kwargs["input"])
        assert sent["outputFileBaseExecutionPathExtension"] == "/runs/2026"

        body["outputFileBaseExecutionPathExtension"] = "runs/2026/"
        resp, _m_s3, m_sfn = self._launch(body)
        assert resp["statusCode"] == 200, resp["body"]
        sent = json.loads(m_sfn.start_execution.call_args.kwargs["input"])
        assert sent["outputFileBaseExecutionPathExtension"] == "/runs/2026/"

    def test_the_workflow_default_reaches_the_sfn_input_resolved(self):
        """End to end for the new default: the workflow stores "/{{jobName}}/" unresolved, a request
        that names no prefix inherits it, and the launch resolves it to the run's job name."""
        workflow = dict(_WORKFLOW)
        workflow["systemConfig"] = dict(workflow.get("systemConfig") or {})
        workflow["systemConfig"]["defaultOutputFileBaseExecutionPathExtension"] = (
            "/{{firstAssetFileFileNameNoExt}}/")
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        resp, _m_s3, m_sfn = self._launch(body, workflow=workflow)
        assert resp["statusCode"] == 200, resp["body"]
        sent = json.loads(m_sfn.start_execution.call_args.kwargs["input"])
        assert sent["outputFileBaseExecutionPathExtension"] == "/f/"

    def test_undefined_placeholder_is_a_caller_error(self):
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}],
                "outputFileBaseExecutionPathExtension": "/{{notARealTag}}/"}
        resp, _m_s3, m_sfn = self._launch(body)
        assert resp["statusCode"] == 400
        assert "template tag" in json.loads(resp["body"])["message"].lower()

    def test_a_rendered_value_over_the_length_cap_is_rejected(self):
        """Rendering can grow the value without bound (a JSON-kind tag on a large selection renders to
        kilobytes). Unchecked, it passes here and then fails every object write on S3's 1024-byte key
        limit — a late, per-object failure after the pipelines have already run."""
        long_name = "n" * 1100
        manifest = {"inputFiles": [{"relativePath": f"/{long_name}.glb",
                                    "key": f"a1/{long_name}.glb", "bucket": "b"}],
                    "outputs": {}, "outputTarget": {}, "auxBucket": "", "auxTempPrefix": ""}
        with pytest.raises(ewv2.VAMSGeneralErrorResponse, match="too long"):
            ewv2._render_output_path_extension("/{{firstAssetFileFileName}}/", manifest, {})

    def test_a_rendered_json_or_uri_value_is_rejected_rather_than_silently_mangled(self):
        """The field advertises system tags, but a JSON-kind tag renders braces/brackets and a URI tag
        renders '//' that normalization collapses ('s3://b/k' -> 's3:/b/k'). Both would become literal
        garbage inside every output key, so they are refused."""
        manifest = {"inputFiles": [{"relativePath": "/a.glb", "key": "a1/a.glb", "bucket": "b"}],
                    "outputs": {}, "outputTarget": {}, "auxBucket": "", "auxTempPrefix": ""}
        for tag in ("{{assetFileRelativePathArray}}", "{{firstAssetFileS3Uri}}"):
            with pytest.raises(ewv2.VAMSGeneralErrorResponse):
                ewv2._render_output_path_extension(f"/{tag}/", manifest, {})

    def test_rendered_traversal_is_rejected(self):
        # A tag whose value renders to a traversal segment must not become part of an output key.
        manifest = {"inputFiles": [{"relativePath": "/..", "key": "a1/..", "bucket": "b"}],
                    "outputs": {}, "outputTarget": {}, "auxBucket": "", "auxTempPrefix": ""}
        with pytest.raises(ewv2.VAMSGeneralErrorResponse):
            ewv2._render_output_path_extension(
                "/{{firstAssetFileFileName}}/", manifest, {})


@pytest.mark.unit
class TestPersistFailureStopsExecution:
    """A record-write failure after start_execution must not leave an orphan running state machine
    that no read/abort path can see."""

    def test_persist_failure_stops_the_started_execution(self):
        p = TestExecuteOrchestration()._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo, \
             patch(f"{MOD}._persist_execution_records", side_effect=RuntimeError("throttled")):
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 500
        m_sfn.stop_execution.assert_called_once()
        assert m_sfn.stop_execution.call_args.kwargs["executionArn"] == "arn:exec"

    def test_stop_failure_does_not_mask_the_persist_error(self):
        with patch(f"{MOD}.sfn_client") as m_sfn:
            m_sfn.stop_execution.side_effect = RuntimeError("access denied")
            ewv2._stop_started_execution("arn:exec")  # must not raise
        assert m_sfn.stop_execution.called


@pytest.mark.unit
class TestBoundedInputFanOut:
    """The per-input S3 + metadata-service reads run through a bounded worker pool, so a large
    selection does not serialize into a request-timeout."""

    def test_verify_inputs_runs_checks_in_parallel(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": f"/f{i}.glb"}
                    for i in range(25)]
        asset_records = {("db1", "a1"): {"bucketId": "b", "assetLocation": {"Key": "a1/"}}}
        with patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "asset-bucket"}), \
             patch(f"{MOD}._input_exists_in_s3", return_value=(True, "v1")), \
             patch(f"{MOD}.ThreadPoolExecutor", wraps=ewv2.ThreadPoolExecutor) as m_pool:
            missing = ewv2._verify_inputs_exist(selected, asset_records)
        assert missing == []
        assert all(i["resolvedVersionId"] == "v1" for i in selected)
        m_pool.assert_called_once_with(max_workers=ewv2.MAX_PARALLEL_INPUT_WORKERS)

    def test_grouped_metadata_runs_fetches_in_parallel(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": f"/f{i}.glb"}
                    for i in range(25)]
        assets = {("db1", "a1"): {"assetName": "A1"}}
        gate = {"assetMetadata": True, "fileMetadata": True, "fileAttributes": True}
        with patch(f"{MOD}._fetch_metadata", return_value=[{"metadataKey": "k", "metadataValue": "v"}]), \
             patch(f"{MOD}._fetch_file_metadata",
                   return_value=[{"metadataKey": "fk", "metadataValue": "fv"}]), \
             patch(f"{MOD}.ThreadPoolExecutor", wraps=ewv2.ThreadPoolExecutor) as m_pool:
            env = ewv2._build_grouped_metadata(selected, assets, gate, {"requestContext": {}})
        m_pool.assert_called_once_with(max_workers=ewv2.MAX_PARALLEL_INPUT_WORKERS)
        files = env["assets"][0]["files"]
        # '/' asset record + one record per selected file, each carrying its own fetched values.
        assert len(files) == len(selected) + 1
        for record in files:
            if record["fileKey"] == "/":
                assert record["metadata"] == {"k": "v"}
            else:
                assert record["metadata"] == {"fk": "fv"}
                assert record["attributes"] == {"fk": "fv"}

    def test_grouped_metadata_gate_off_issues_no_fetches(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]
        assets = {("db1", "a1"): {"assetName": "A1"}}
        gate = {"assetMetadata": False, "fileMetadata": False, "fileAttributes": False}
        with patch(f"{MOD}._fetch_metadata") as m_asset, \
             patch(f"{MOD}._fetch_file_metadata") as m_file, \
             patch(f"{MOD}.ThreadPoolExecutor") as m_pool:
            ewv2._build_grouped_metadata(selected, assets, gate, {"requestContext": {}})
        m_asset.assert_not_called()
        m_file.assert_not_called()
        m_pool.assert_not_called()


@pytest.mark.unit
class TestResolvedInputVersion:
    """_verify_inputs_exist stamps each single-file input with the concrete S3 version resolved at
    launch (from head_object), so the execution record shows the exact version used, not 'latest'."""

    def test_stamps_resolved_version_on_single_file(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]
        asset_records = {("db1", "a1"): {"bucketId": "b", "assetLocation": {"Key": "root/"}}}
        with patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "asset-bucket"}), \
             patch(f"{MOD}._input_exists_in_s3", return_value=(True, "s3-ver-abc")):
            missing = ewv2._verify_inputs_exist(selected, asset_records)
        assert missing == []
        assert selected[0]["resolvedVersionId"] == "s3-ver-abc"

    def test_missing_file_not_stamped(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]
        asset_records = {("db1", "a1"): {"bucketId": "b", "assetLocation": {"Key": "root/"}}}
        with patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "asset-bucket"}), \
             patch(f"{MOD}._input_exists_in_s3", return_value=(False, "")):
            missing = ewv2._verify_inputs_exist(selected, asset_records)
        assert len(missing) == 1
        assert "resolvedVersionId" not in selected[0]

    def test_manifest_entry_carries_the_resolved_version(self):
        # The persisted input row records resolvedVersionId, so the manifest must name the same
        # version rather than the (possibly empty) value the caller sent.
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb",
                     "versionId": "", "resolvedVersionId": "s3-ver-abc"}]
        asset_records = {("db1", "a1"): dict(_ASSET)}
        with patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "asset-bucket"}):
            entries = ewv2._build_input_manifest_entries(selected, asset_records)
        assert entries[0]["versionId"] == "s3-ver-abc"

    def test_manifest_entry_falls_back_to_the_requested_version(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb",
                     "versionId": "caller-ver"}]
        asset_records = {("db1", "a1"): dict(_ASSET)}
        with patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "asset-bucket"}):
            entries = ewv2._build_input_manifest_entries(selected, asset_records)
        assert entries[0]["versionId"] == "caller-ver"


@pytest.mark.unit
class TestDeleteMarkerInput:
    """HeadObject against a delete-marker version answers 405 MethodNotAllowed. That version is not
    readable, so it is a missing input (404 to the caller), not an unexpected failure (500)."""

    def _client_error(self, code):
        return botocore.exceptions.ClientError(
            {"Error": {"Code": code, "Message": "x"}}, "HeadObject")

    @pytest.mark.parametrize("code", ["405", "MethodNotAllowed", "404", "NoSuchKey", "NotFound"])
    def test_unreadable_version_is_missing(self, code):
        with patch(f"{MOD}.s3c") as m_s3:
            m_s3.head_object.side_effect = self._client_error(code)
            assert ewv2._input_exists_in_s3("b", "a1/f.glb", "delete-marker-ver") == (False, "")

    def test_access_denied_still_raises(self):
        with patch(f"{MOD}.s3c") as m_s3:
            m_s3.head_object.side_effect = self._client_error("AccessDenied")
            with pytest.raises(botocore.exceptions.ClientError):
                ewv2._input_exists_in_s3("b", "a1/f.glb")


@pytest.mark.unit
class TestMalformedInputVersionId:
    """A versionId that is not a well-formed S3 version is caller input, not a server fault.

    S3 rejects the argument before looking anything up, so HeadObject answers 400 Bad Request rather
    than 404. Left unhandled that propagated as a 500 with an unexplained stack trace. A VAMS ASSET
    version number ("0", "3") is the realistic way to hit this — it is what an asset-version list
    offers, and the execute API's versionId is an S3 object version of one key.
    """

    def _client_error(self, code):
        return botocore.exceptions.ClientError(
            {"Error": {"Code": code, "Message": "Bad Request"}}, "HeadObject")

    @pytest.mark.parametrize("code", ["400", "BadRequest", "InvalidArgument", "InvalidVersionId"])
    def test_a_malformed_requested_version_is_a_missing_input(self, code):
        with patch(f"{MOD}.s3c") as m_s3:
            m_s3.head_object.side_effect = self._client_error(code)
            assert ewv2._input_exists_in_s3("b", "a1/f.glb", "0") == (False, "")

    def test_a_400_without_a_requested_version_still_raises(self):
        # Nothing about the caller's input can produce this one, so it is a genuine fault and must not
        # be reported to the user as "your file is missing".
        with patch(f"{MOD}.s3c") as m_s3:
            m_s3.head_object.side_effect = self._client_error("400")
            with pytest.raises(botocore.exceptions.ClientError):
                ewv2._input_exists_in_s3("b", "a1/f.glb")

    def test_the_handler_answers_404_rather_than_500(self):
        # End-to-end through the handler: the status code is the whole point of the fix.
        p = TestExecuteOrchestration()._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1",
                                "relativeFileKey": "/f.glb", "versionId": "0"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["asset_bucket"], \
             p["enforcer"], p["claims"], patch(f"{MOD}.s3c") as m_s3, \
             patch(f"{MOD}.sfn_client") as m_sfn:
            m_s3.head_object.side_effect = self._client_error("400")
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 404
        # And it must not have launched anything.
        m_sfn.start_execution.assert_not_called()

    def test_a_valid_version_is_unaffected(self):
        with patch(f"{MOD}.s3c") as m_s3:
            m_s3.head_object.return_value = {"VersionId": "real-s3-ver"}
            assert ewv2._input_exists_in_s3("b", "a1/f.glb", "real-s3-ver") == (
                True, "real-s3-ver")


@pytest.mark.unit
class TestDuplicateInputFiles:
    """A repeated selection names one file, so it is collapsed before arity, the manifest, and the
    persisted input rows are derived from it (they would otherwise disagree)."""

    def test_duplicate_selection_runs_as_one_input_on_arity_one(self):
        p = TestExecuteOrchestration()._patches()
        entry = {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}
        body = {"inputFiles": [dict(entry), dict(entry)]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c") as m_s3, patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        manifest_puts = [c for c in m_s3.put_object.call_args_list
                         if c.kwargs.get("Key", "").endswith("pipeline1/manifest.json")]
        manifest = json.loads(manifest_puts[0].kwargs["Body"].decode("utf-8"))
        assert len(manifest["inputFiles"]) == 1

    def test_distinct_versions_of_one_key_are_kept(self):
        model = ewv2.ExecuteWorkflowRequestV2Model(inputFiles=[
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": "v1"},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": "v2"},
        ])
        assert len(model.inputFiles) == 2


@pytest.mark.unit
class TestUnescapeRenderedPath:
    """render_config JSON-escapes scalar tag values for substitution inside a template's own quotes.
    A bare path is not that context, so the escaping is reversed before the path is used."""

    def test_non_ascii_stem_renders_as_real_characters(self):
        manifest = {"inputFiles": [{"relativePath": "/café.glb", "key": "a1/café.glb",
                                    "bucket": "b"}],
                    "outputs": {}, "outputTarget": {}, "auxBucket": "", "auxTempPrefix": ""}
        rendered = ewv2._render_output_path_extension(
            "/{{firstAssetFileFileNameNoExt}}/", manifest, {})
        assert rendered == "/café/"

    def test_plain_text_is_unchanged(self):
        assert ewv2._unescape_rendered_path("/runs/2026/") == "/runs/2026/"


@pytest.mark.unit
class TestMissingTemplateTagIsCallerError:
    """render_config raises MissingTemplateTagError when a stored config body uses an undefined tag.
    That is a caller/authoring error, so it answers 400 rather than a generic 500."""

    def test_unknown_tag_in_pipeline_config_returns_400(self):
        p = TestExecuteOrchestration()._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo, \
             patch(f"{MOD}.tr.render_config",
                   side_effect=ewv2.tr.MissingTemplateTagError(["metadata_location"])):
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ewv2.lambda_handler(_event(body=body), MagicMock())
        assert resp["statusCode"] == 400
        m_sfn.start_execution.assert_not_called()
