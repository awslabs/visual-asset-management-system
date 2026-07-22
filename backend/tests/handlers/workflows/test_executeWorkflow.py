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
os.environ.setdefault("WORKFLOW_EXECUTION_OUTPUTS_INDEX_TABLE_NAME", "t-wf-out-index")
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
        gate = {"assetMetadata": False, "fileMetadata": False, "fileAttributes": False}
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

    def test_single_input_override_gated_by_allow_override(self):
        # One input asset, allowOverride False + explicit output -> override IGNORED, locked to input.
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
        assert resp["statusCode"] == 200
        sent = json.loads(m_sfn.start_execution.call_args.kwargs["input"])
        assert sent["outputAssetId"] == "a1"  # locked to input asset, override ignored

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
        body = {"inputFiles": [{"databaseId": "smoke-db", "assetId": "X", "relativeFileKey": "/f.glb"}]}
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
class TestNormalizeOutputPathExtension:
    """_normalize_output_path_extension: single leading+trailing slash; '/' default; tags preserved."""

    def test_empty_and_none_default_to_root(self):
        assert ewv2._normalize_output_path_extension(None) == "/"
        assert ewv2._normalize_output_path_extension("") == "/"
        assert ewv2._normalize_output_path_extension("   ") == "/"
        assert ewv2._normalize_output_path_extension("/") == "/"

    def test_wraps_with_single_slashes(self):
        assert ewv2._normalize_output_path_extension("runs/2026") == "/runs/2026/"
        assert ewv2._normalize_output_path_extension("/runs/2026/") == "/runs/2026/"
        assert ewv2._normalize_output_path_extension("///runs///") == "/runs/"

    def test_preserves_dynamic_tag_placeholders(self):
        out = ewv2._normalize_output_path_extension("out/{{firstAssetFileFileNameNoExt}}")
        assert out == "/out/{{firstAssetFileFileNameNoExt}}/"


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
