# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 3 tests: the pipeline sub-process registration lambda, the manifest envelope schema
version, the orchestration event-prefix helper, and the abort/logs best-effort use of
registered sub-process ARNs (warnings, never errors)."""

import os
import sys
import types
import json
import pytest
from unittest.mock import MagicMock, patch

for k, v in {
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
    "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME": "t-pin-files",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_STORAGE_TABLE_NAME": "t-workflows",
    "PIPELINE_STORAGE_TABLE_NAME": "t-pipelines",
    "WORKFLOW_EXECUTION_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:vams-wf:*",
}.items():
    os.environ.setdefault(k, v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf

from backend.backend.common.workflows import executionRecords as er
from backend.backend.handlers.workflows.sfn import registerPipelineExecution as reg
from backend.backend.handlers.workflows import executionService as le


# ============================ schema versioning + helpers ============================

@pytest.mark.unit
class TestManifestEnvelopeAndHelpers:
    def test_manifest_envelope_is_versioned_and_grouped(self):
        env = er.build_manifest_envelope(
            input_files=[er.build_manifest_entry("/a.glb", "bkt", "x/a.glb", "v1",
                                                 database_id="db", asset_id="x",
                                                 asset_root_s3_key="x/",
                                                 aux_preview_prefix="db/x/a.glb/preview")],
            input_metadata_s3_location="s3://bkt/.../metadata.json",
            outputs=er.build_manifest_outputs(bucket="bkt", files="o/files/"),
            aux_bucket="aux",
            aux_temp_prefix="pipelines/p/E/",
            aux_preview_pipeline_suffix="",
            system_config=er.build_manifest_system_config(
                orchestration_bus_arn="arn:bus", orchestration_event_prefix="vams.p.execution.E.pipeline.P"))
        assert env["schemaVersion"] == er.MANIFEST_SCHEMA_VERSION
        # Each input file is self-locating with relative keys + its own aux preview prefix.
        f = env["inputFiles"][0]
        assert f["assetId"] == "x" and f["assetRootS3Key"] == "x/" and f["versionId"] == "v1"
        assert f["auxPreviewPrefix"] == "db/x/a.glb/preview"
        # Grouped sections present; outputs carry a bucket + relative prefixes, auxBucket is a name.
        assert env["outputs"]["bucket"] == "bkt" and env["outputs"]["files"] == "o/files/"
        assert env["auxBucket"] == "aux"
        assert env["systemConfig"]["orchestrationBusArn"] == "arn:bus"
        assert env["systemConfig"]["orchestrationEventPrefix"] == "vams.p.execution.E.pipeline.P"

    def test_orchestration_event_prefix_format(self):
        assert er.orchestration_event_prefix("vams.prod", "e1000000000000000000000000000001", "P1") == \
            "vams.prod.execution.e1000000000000000000000000000001.pipeline.P1"

    def test_metadata_schema_version_constant(self):
        assert er.METADATA_SCHEMA_VERSION >= 1

    def test_pipeline_record_has_registered_arn_fields(self):
        rec = er.build_pipeline_execution_record(
            pipeline_execution_id="P1", workflow_execution_id="e1000000000000000000000000000001",
            pipeline_database_id="db", pipeline_id="p", end_state_pipeline=False,
            s3_asset_bucket="bkt", s3_aux_bucket="aux", output_prefixes={},
            input_metadata_file_prefix="", input_config_file_prefix="",
            aux_temp_prefix="", aux_preview_prefix="", pipeline_execution_type="Lambda",
            wait_for_callback="Disabled", pipeline_resource_arn="",
            orchestration_bus_event_prefix="vams.prod.execution.e1000000000000000000000000000001.pipeline.P1")
        assert rec["orchestrationBusEventPrefix"] == "vams.prod.execution.e1000000000000000000000000000001.pipeline.P1"
        assert rec["registeredSubExecutions"] == []
        assert rec["registeredLogs"] == []
        # The removed legacy single-ARN fields are gone (brand-new table, no real legacy).
        assert "pipeline_execution_sub_execution_arn" not in rec
        assert "pipeline_execution_sub_arn" not in rec


# ============================ registration lambda ============================

@pytest.mark.unit
class TestRegisterPipelineExecution:
    def _event(self, detail):
        return {"detail": detail, "detail-type": "pipeline.execution.register",
                "source": "vams.prod.execution.e1000000000000000000000000000001.pipeline.P1"}

    # Valid, partition-correct ARNs so these assertions hold whether validation is stubbed
    # (conftest) or real (full-suite ordering) — the lambda validates ARN formats before storing.
    _SM_ARN = "arn:aws:states:us-east-1:123456789012:stateMachine:sm"
    _EX_ARN = "arn:aws:states:us-east-1:123456789012:execution:sm:ex"
    _LG_ARN = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lg:*"

    def test_appends_sub_execution_and_logs(self):
        row = {"pipelineExecutionId": "P1", "workflowExecutionId": "e1000000000000000000000000000001",
               "registeredSubExecutions": [], "registeredLogs": []}
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({
                "pipelineExecutionId": "P1",
                "subExecution": {"stateMachineArn": self._SM_ARN, "executionArn": self._EX_ARN},
                "logs": [{"logGroupArn": self._LG_ARN, "logGroupName": "lg", "logStreamName": "s1"}],
            }), MagicMock())
        kw = table.update_item.call_args.kwargs
        # Atomic append: the expression carries only the NEW entries; DynamoDB list_appends them.
        assert "list_append" in kw["UpdateExpression"]
        subs = kw["ExpressionAttributeValues"][":s"]
        logs = kw["ExpressionAttributeValues"][":l"]
        # A bare {stateMachineArn, executionArn} report normalizes to a typed entry defaulting
        # to a Step Functions execution.
        assert subs == [{"resourceType": "stepFunctionsExecution",
                         "stateMachineArn": self._SM_ARN, "executionArn": self._EX_ARN}]
        assert logs == [{"logGroupArn": self._LG_ARN, "logGroupName": "lg",
                         "logStreamName": "s1", "logStreamPrefix": ""}]

    def test_append_is_atomic_carrying_only_new_entries(self):
        # An existing list is NOT read into the expression: the update is an atomic
        # list_append so concurrent reports cannot clobber each other.
        row = {"pipelineExecutionId": "P1", "workflowExecutionId": "e1000000000000000000000000000001",
               "registeredSubExecutions": [{"resourceType": "stepFunctionsExecution",
                                            "stateMachineArn": self._SM_ARN, "executionArn": self._EX_ARN}],
               "registeredLogs": []}
        new_sm = "arn:aws:states:us-east-1:123456789012:stateMachine:newsm"
        new_ex = "arn:aws:states:us-east-1:123456789012:execution:newsm:newex"
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({
                "pipelineExecutionId": "P1",
                "subExecution": {"stateMachineArn": new_sm, "executionArn": new_ex},
            }), MagicMock())
        kw = table.update_item.call_args.kwargs
        assert "list_append(if_not_exists(registeredSubExecutions" in kw["UpdateExpression"]
        subs = kw["ExpressionAttributeValues"][":s"]
        assert subs == [{"resourceType": "stepFunctionsExecution",
                         "stateMachineArn": new_sm, "executionArn": new_ex}]
        assert kw["ExpressionAttributeValues"][":empty"] == []

    def test_missing_pipeline_execution_id_no_write(self):
        table = MagicMock()
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({"subExecution": {"executionArn": "arn:ex"}}), MagicMock())
        table.update_item.assert_not_called()

    def test_unknown_pipeline_no_write(self):
        table = MagicMock(query=MagicMock(return_value={"Items": []}))
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({"pipelineExecutionId": "nope",
                                            "subExecution": {"executionArn": "arn:ex"}}), MagicMock())
        table.update_item.assert_not_called()

    def test_no_arns_no_write(self):
        table = MagicMock()
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({"pipelineExecutionId": "P1"}), MagicMock())
        table.update_item.assert_not_called()

    def test_never_raises(self):
        with patch.object(reg, "register", side_effect=Exception("boom")):
            resp = reg.lambda_handler(self._event({"pipelineExecutionId": "P1"}), MagicMock())
        assert resp == {"handled": True}


# ============================ registration input validation ============================

# The root conftest stubs common.validators.validate to always-pass; bind the REAL dispatcher
# (loaded by path in conftest) so these tests exercise the actual ARN/field-format checks.
import importlib.util as _ilu
import os as _os
_real_validators_path = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__)))),
    "backend", "common", "validators.py")
_rv_spec = _ilu.spec_from_file_location("_real_validators_for_reg_tests", _real_validators_path)
_real_validators = _ilu.module_from_spec(_rv_spec)
_rv_spec.loader.exec_module(_real_validators)
_REAL_VALIDATE = _real_validators.validate


@pytest.mark.unit
class TestRegistrationInputValidation:
    """The registration lambda validates ARN/field formats and drops malformed values
    (best-effort: never raises). These run against the REAL validate() dispatcher."""

    def _event(self, detail):
        return {"detail": detail, "detail-type": "pipeline.execution.register",
                "source": "vams.prod.execution.e1000000000000000000000000000001.pipeline.P1"}

    def test_invalid_arns_are_dropped_valid_kept(self):
        row = {"pipelineExecutionId": "Pvalid123", "workflowExecutionId": "e1000000000000000000000000000001",
               "registeredSubExecutions": [], "registeredLogs": []}
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        with patch.object(reg, "validate", _REAL_VALIDATE), \
             patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({
                "pipelineExecutionId": "Pvalid123",
                "subExecution": {
                    "resourceType": "stepFunctionsExecution",
                    "stateMachineArn": "arn:aws:states:us-east-1:123456789012:stateMachine:sm",
                    "executionArn": "not-an-arn",  # invalid -> dropped
                },
                "logs": [{
                    "logGroupArn": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/g:*",
                    "logStreamName": "bad:stream",  # invalid (':' not allowed) -> dropped
                    "logStreamPrefix": "ok/prefix",
                }],
            }), MagicMock())
        kw = table.update_item.call_args.kwargs
        sub = kw["ExpressionAttributeValues"][":s"][0]
        # Valid ARN kept; invalid executionArn omitted entirely (not stored as junk).
        assert sub["stateMachineArn"].endswith(":stateMachine:sm")
        assert "executionArn" not in sub
        log = kw["ExpressionAttributeValues"][":l"][0]
        assert log["logGroupArn"].endswith(":/aws/g:*")
        assert log["logStreamName"] == ""        # dropped
        assert log["logStreamPrefix"] == "ok/prefix"

    def test_sub_execution_with_only_invalid_locators_is_dropped(self):
        # resourceType valid but every locator malformed -> the whole sub entry is dropped, and
        # with no logs either there is nothing to write.
        table = MagicMock(query=MagicMock(return_value={"Items": [
            {"pipelineExecutionId": "Pvalid123", "workflowExecutionId": "e1000000000000000000000000000001"}]}),
            update_item=MagicMock())
        with patch.object(reg, "validate", _REAL_VALIDATE), \
             patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({
                "pipelineExecutionId": "Pvalid123",
                "subExecution": {"resourceType": "stepFunctionsExecution",
                                 "executionArn": "junk", "stateMachineArn": "also-junk"},
            }), MagicMock())
        table.update_item.assert_not_called()

    def test_invalid_pipeline_execution_id_ignored(self):
        # A malformed pipelineExecutionId (DynamoDB key) is rejected before any query/write.
        table = MagicMock()
        with patch.object(reg, "validate", _REAL_VALIDATE), \
             patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({
                "pipelineExecutionId": "bad id with spaces/and*chars",
                "subExecution": {"executionArn": "arn:aws:states:us-east-1:123456789012:execution:sm:e"},
            }), MagicMock())
        table.query.assert_not_called()
        table.update_item.assert_not_called()


# ============================ abort uses registered sub-execs ============================

@pytest.mark.unit
class TestAbortRegisteredSubExecutions:
    def _claims(self):
        return {"tokens": ["user@x"], "roles": [], "mfaEnabled": False}

    def _event(self):
        ev = {"requestContext": {"http": {"method": "DELETE", "path": "/x"}, "authorizer": {}},
              "pathParameters": {"executionId": "abc00000000000000000000000000001"}, "queryStringParameters": {}}
        return ev

    def _main_row(self):
        return {"executionId": "abc00000000000000000000000000001", "workflowId": "wfx", "workflowDatabaseId": "dbx",
                "workflow_execution_arn": "arn:ex:main", "executionStatus": "RUNNING",
                "executionStopDate": ""}

    def test_abort_stops_registered_sub_executions_and_warns_on_failure(self):
        prow = {"pipelineExecutionId": "P1", "workflowExecutionId": "abc00000000000000000000000000001",
                "executionStatus": "RUNNING", "executionStopDate": "",
                "registeredSubExecutions": [
                    {"resourceType": "stepFunctionsExecution",
                     "stateMachineArn": "arn:sm:ok", "executionArn": "arn:ex:ok"},
                    {"resourceType": "stepFunctionsExecution",
                     "stateMachineArn": "arn:sm:denied", "executionArn": "arn:ex:denied"}]}
        pexec_table = MagicMock()
        main_table = MagicMock()

        def _table(name):
            return pexec_table if name == le.pipeline_executions_table else main_table

        def _stop(executionArn):
            if executionArn == "arn:ex:denied":
                raise le.botocore.exceptions.ClientError(
                    {"Error": {"Code": "AccessDeniedException"}}, "StopExecution")
            return {}

        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[("dbx", "a1")]), \
             patch.object(le, "get_asset_details", return_value={"assetId": "a1", "databaseId": "dbx"}), \
             patch.object(le, "get_pipeline_execution_rows", return_value=[prow]), \
             patch.object(le.dynamodb, "Table", side_effect=_table), \
             patch.object(le.sfn, "stop_execution", side_effect=_stop) as mock_stop:
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = le.lambda_handler(self._event(), MagicMock())

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["message"] == "Execution aborted"
        # Both registered sub-executions + the main execution were attempted.
        stopped = {c.kwargs.get("executionArn") for c in mock_stop.call_args_list}
        assert "arn:ex:ok" in stopped and "arn:ex:denied" in stopped and "arn:ex:main" in stopped
        # The denied sub-execution surfaces a non-fatal warning; the abort still succeeds.
        assert "warnings" in body
        assert any("arn:ex:denied" in w for w in body["warnings"])

    def test_abort_terminates_a_registered_batch_job(self):
        # A pipeline that submits its OWN Batch job registers it as resourceType batchJob.
        # Nothing else stops that job (Step Functions owns only jobs submitted through the
        # `.sync` integration), so abort must call TerminateJob or the job keeps running.
        prow = {"pipelineExecutionId": "P1", "workflowExecutionId": "abc00000000000000000000000000001",
                "executionStatus": "RUNNING", "executionStopDate": "",
                "registeredSubExecutions": [
                    {"resourceType": "batchJob", "jobId": "batch-job-777"}]}
        pexec_table = MagicMock()
        main_table = MagicMock()

        def _table(name):
            return pexec_table if name == le.pipeline_executions_table else main_table

        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[("dbx", "a1")]), \
             patch.object(le, "get_asset_details", return_value={"assetId": "a1", "databaseId": "dbx"}), \
             patch.object(le, "get_pipeline_execution_rows", return_value=[prow]), \
             patch.object(le.dynamodb, "Table", side_effect=_table), \
             patch.object(le.batch_client, "terminate_job", return_value={}) as mock_terminate, \
             patch.object(le.sfn, "stop_execution", side_effect=lambda executionArn: {}) as mock_stop:
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = le.lambda_handler(self._event(), MagicMock())

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["message"] == "Execution aborted"
        # Terminated by id, and NOT sent to StopExecution (a Batch job is not an SFN
        # execution); only the main SFN execution was stopped.
        assert mock_terminate.call_args.kwargs["jobId"] == "batch-job-777"
        stopped = {c.kwargs.get("executionArn") for c in mock_stop.call_args_list}
        assert "batch-job-777" not in stopped
        assert "arn:ex:main" in stopped
        # Terminating cleanly is not a warning-worthy outcome.
        assert not any("batch" in w.lower() for w in body.get("warnings", []))
