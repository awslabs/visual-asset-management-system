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
        pexec = (detail or {}).get("pipelineExecutionId", "P1") or "P1"
        return {"detail": detail, "detail-type": "pipeline.execution.register",
                "source": f"vams.prod.execution.e1000000000000000000000000000001.pipeline.{pexec}"}

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
        # Every stored log entry carries the same seven keys; the descriptive ones are "" when the
        # producer did not report them.
        assert logs == [{"logGroupArn": self._LG_ARN, "logGroupName": "lg",
                         "logStreamName": "s1", "logStreamPrefix": "",
                         "stageName": "", "label": "", "sourceType": ""}]

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

    def test_a_source_naming_another_pipeline_execution_is_ignored(self):
        # The standing rule matches on the deployment's source PREFIX only, so this is the check that
        # keeps one pipeline from attaching a log location to another pipeline's execution.
        row = {"pipelineExecutionId": "P1", "workflowExecutionId": "e1000000000000000000000000000001",
               "registeredSubExecutions": [], "registeredLogs": []}
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        event = self._event({"pipelineExecutionId": "P1",
                             "subExecution": {"stateMachineArn": self._SM_ARN, "executionArn": self._EX_ARN}})
        event["source"] = "vams.prod.execution.e1000000000000000000000000000001.pipeline.P2"
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(event, MagicMock())
        table.query.assert_not_called()
        table.update_item.assert_not_called()

    def test_a_missing_source_is_ignored(self):
        table = MagicMock(query=MagicMock(return_value={"Items": []}), update_item=MagicMock())
        event = self._event({"pipelineExecutionId": "P1",
                             "subExecution": {"stateMachineArn": self._SM_ARN, "executionArn": self._EX_ARN}})
        del event["source"]
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(event, MagicMock())
        table.query.assert_not_called()
        table.update_item.assert_not_called()

    def test_a_source_ending_in_the_pipeline_execution_id_is_accepted(self):
        row = {"pipelineExecutionId": "P1", "workflowExecutionId": "e1000000000000000000000000000001",
               "registeredSubExecutions": [], "registeredLogs": []}
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({
                "pipelineExecutionId": "P1",
                "subExecution": {"stateMachineArn": self._SM_ARN, "executionArn": self._EX_ARN}}), MagicMock())
        table.update_item.assert_called_once()


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
        pexec = (detail or {}).get("pipelineExecutionId", "P1") or "P1"
        return {"detail": detail, "detail-type": "pipeline.execution.register",
                "source": f"vams.prod.execution.e1000000000000000000000000000001.pipeline.{pexec}"}

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

    def test_descriptive_log_fields_are_stored_when_valid(self):
        row = {"pipelineExecutionId": "Pvalid123", "workflowExecutionId": "e1000000000000000000000000000001",
               "registeredSubExecutions": [], "registeredLogs": []}
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        with patch.object(reg, "validate", _REAL_VALIDATE), \
             patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({
                "pipelineExecutionId": "Pvalid123",
                "subExecution": {
                    "stateMachineArn": "arn:aws:states:us-east-1:123456789012:stateMachine:sm",
                    "executionArn": "arn:aws:states:us-east-1:123456789012:execution:sm:e",
                    "stageName": "Preview3dThumbnailBatchJob", "label": "3D thumbnail processing"},
                "logs": [{
                    "logGroupArn": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/batch/job",
                    "logGroupName": "/aws/batch/job",
                    "logStreamPrefix": "vams-thumb-jobdef/default/",
                    "stageName": "Preview3dThumbnailBatchJob",
                    "label": "Preview3dThumbnailBatchJob container",
                    "sourceType": "batch",
                }],
            }), MagicMock())
        kw = table.update_item.call_args.kwargs
        sub = kw["ExpressionAttributeValues"][":s"][0]
        assert sub["stageName"] == "Preview3dThumbnailBatchJob"
        assert sub["label"] == "3D thumbnail processing"
        log = kw["ExpressionAttributeValues"][":l"][0]
        assert log["stageName"] == "Preview3dThumbnailBatchJob"
        assert log["label"] == "Preview3dThumbnailBatchJob container"
        assert log["sourceType"] == "batch"
        assert log["logStreamPrefix"] == "vams-thumb-jobdef/default/"

    def test_invalid_descriptive_fields_are_dropped_not_stored(self):
        row = {"pipelineExecutionId": "Pvalid123", "workflowExecutionId": "e1000000000000000000000000000001",
               "registeredSubExecutions": [], "registeredLogs": []}
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        with patch.object(reg, "validate", _REAL_VALIDATE), \
             patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({
                "pipelineExecutionId": "Pvalid123",
                "subExecution": {
                    "executionArn": "arn:aws:states:us-east-1:123456789012:execution:sm:e",
                    "stageName": "x" * 81, "label": "bad\tlabel"},
                "logs": [{
                    "logGroupArn": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/g:*",
                    "stageName": "line\nbreak", "label": "y" * 129,
                }],
            }), MagicMock())
        kw = table.update_item.call_args.kwargs
        sub = kw["ExpressionAttributeValues"][":s"][0]
        assert "stageName" not in sub and "label" not in sub
        log = kw["ExpressionAttributeValues"][":l"][0]
        assert log["stageName"] == "" and log["label"] == ""

    def test_an_unknown_source_type_is_stored_as_custom(self):
        row = {"pipelineExecutionId": "Pvalid123", "workflowExecutionId": "e1000000000000000000000000000001",
               "registeredSubExecutions": [], "registeredLogs": []}
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        with patch.object(reg, "validate", _REAL_VALIDATE), \
             patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({
                "pipelineExecutionId": "Pvalid123",
                "logs": [{"logGroupArn": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/g:*",
                          "sourceType": "kubernetes"}],
            }), MagicMock())
        log = table.update_item.call_args.kwargs["ExpressionAttributeValues"][":l"][0]
        assert log["sourceType"] == "custom"

    def test_a_sub_execution_carrying_only_a_label_is_still_dropped(self):
        # stageName/label describe a locator; they are not one.
        table = MagicMock(query=MagicMock(return_value={"Items": [
            {"pipelineExecutionId": "Pvalid123", "workflowExecutionId": "e1000000000000000000000000000001"}]}),
            update_item=MagicMock())
        with patch.object(reg, "validate", _REAL_VALIDATE), \
             patch.object(reg.dynamodb, "Table", return_value=table):
            reg.lambda_handler(self._event({
                "pipelineExecutionId": "Pvalid123",
                "subExecution": {"resourceType": "stepFunctionsExecution", "label": "orphan"},
            }), MagicMock())
        table.update_item.assert_not_called()


# ============================ location-keyed log dedup ============================

_ARN = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/batch/job"


def _log(arn=_ARN, name="/aws/batch/job", stream="", prefix="", stage="", label="", source=""):
    return {"logGroupArn": arn, "logGroupName": name, "logStreamName": stream, "logStreamPrefix": prefix,
            "stageName": stage, "label": label, "sourceType": source}


@pytest.mark.unit
class TestLogLocationPlanner:
    """A log entry is identified by WHERE it is (group, stream, prefix), never by its descriptive
    fields, so a legacy four-key row entry and a seven-key redelivery of the same place are one."""

    def test_location_key_strips_the_wildcard_suffix_and_falls_back_to_the_name(self):
        assert reg._location_key(_log(arn=_ARN + ":*", stream="s")) == (_ARN, "s", "")
        assert reg._location_key(_log(arn=_ARN, prefix="jd/default/")) == (_ARN, "", "jd/default/")
        assert reg._location_key({"logGroupArn": "", "logGroupName": "/g", "logStreamName": "",
                                  "logStreamPrefix": ""}) == ("/g", "", "")

    def test_a_new_location_is_appended(self):
        append, merges = reg._plan_log_writes([_log(prefix="jd/default/", stage="S")], [])
        assert append == [_log(prefix="jd/default/", stage="S")]
        assert merges == []

    def test_a_known_location_with_nothing_new_is_neither_appended_nor_merged(self):
        stored = _log(prefix="jd/default/", stage="S", label="L", source="batch")
        append, merges = reg._plan_log_writes([dict(stored)], [stored])
        assert append == [] and merges == []

    def test_a_known_location_fills_only_the_fields_the_stored_entry_lacks(self):
        legacy = {"logGroupArn": _ARN + ":*", "logGroupName": "/aws/batch/job",
                  "logStreamName": "", "logStreamPrefix": "jd/default/"}
        incoming = _log(prefix="jd/default/", stage="S", label="L", source="batch")
        append, merges = reg._plan_log_writes([incoming], [{"logGroupArn": "x"}, legacy])
        assert append == []
        assert merges == [(1, {"stageName": "S", "label": "L", "sourceType": "batch"})]

    def test_a_stored_field_is_never_overwritten(self):
        stored = _log(prefix="jd/default/", stage="Stored", label="", source="")
        incoming = _log(prefix="jd/default/", stage="Other", label="L", source="batch")
        _append, merges = reg._plan_log_writes([incoming], [stored])
        assert merges == [(0, {"label": "L", "sourceType": "batch"})]

    def test_duplicates_within_one_event_collapse_to_one_append(self):
        first = _log(stream="s1", stage="S")
        second = _log(stream="s1", label="L")
        append, merges = reg._plan_log_writes([first, second], [])
        assert len(append) == 1
        assert append[0]["stageName"] == "S" and append[0]["label"] == "L"
        assert merges == []

    def test_non_dict_stored_entries_are_ignored(self):
        append, merges = reg._plan_log_writes([_log(stream="s1")], ["junk", None])
        assert len(append) == 1 and merges == []


# ============================ concurrency-safe writes ============================

def _conditional_failure():
    import botocore
    return botocore.exceptions.ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "cond"}}, "UpdateItem")


@pytest.mark.unit
class TestConcurrencySafeRegistration:
    """Two in-flight events for one location must not both append; the guard is the list length the
    writer read, and the merge is guarded by the identity of the element it targets. A writer that
    loses the race twice appends unconditionally, so contention never drops a registration."""

    _SM_ARN = "arn:aws:states:us-east-1:123456789012:stateMachine:sm"
    _EX_ARN = "arn:aws:states:us-east-1:123456789012:execution:sm:ex"
    _SRC = "vams.prod.execution.e1000000000000000000000000000001.pipeline.P1"

    def _row(self, subs=None, logs=None):
        return {"pipelineExecutionId": "P1", "workflowExecutionId": "e1000000000000000000000000000001",
                "executionStatus": "RUNNING",
                "registeredSubExecutions": list(subs or []), "registeredLogs": list(logs or [])}

    def test_the_append_is_conditioned_on_the_list_lengths_that_were_read(self):
        row = self._row(logs=[_log(stream="already")])
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.register({"pipelineExecutionId": "P1",
                          "subExecution": {"stateMachineArn": self._SM_ARN, "executionArn": self._EX_ARN},
                          "logs": [_log(stream="new")]}, source=self._SRC)
        kw = table.update_item.call_args.kwargs
        condition = kw["ConditionExpression"]
        assert "attribute_not_exists(registeredSubExecutions) OR size(registeredSubExecutions) = :ns" in condition
        assert "attribute_not_exists(registeredLogs) OR size(registeredLogs) = :nl" in condition
        assert " AND " in condition
        assert kw["ExpressionAttributeValues"][":ns"] == 0
        assert kw["ExpressionAttributeValues"][":nl"] == 1
        assert kw["ExpressionAttributeValues"][":l"] == [_log(stream="new")]
        assert "list_append(if_not_exists(registeredLogs, :empty), :l)" in kw["UpdateExpression"]

    def test_a_failed_condition_re_reads_once_and_drops_what_the_re_read_now_holds(self):
        first_row = self._row()
        # Between the read and the write another event stored the same location.
        second_row = self._row(logs=[_log(stream="s1")])
        table = MagicMock(update_item=MagicMock(side_effect=[_conditional_failure(), None]))
        table.query.side_effect = [{"Items": [first_row]}, {"Items": [second_row]}]
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.register({"pipelineExecutionId": "P1",
                          "subExecution": {"stateMachineArn": self._SM_ARN, "executionArn": self._EX_ARN},
                          "logs": [_log(stream="s1")]}, source=self._SRC)
        assert table.query.call_count == 2
        assert table.update_item.call_count == 2
        retry = table.update_item.call_args_list[1].kwargs
        # The log is now known, so only the sub-execution is appended, against the fresh lengths.
        assert retry["ExpressionAttributeValues"][":l"] == []
        assert retry["ExpressionAttributeValues"][":nl"] == 1
        assert len(retry["ExpressionAttributeValues"][":s"]) == 1

    def test_the_re_read_after_a_lost_race_is_strongly_consistent(self):
        table = MagicMock(query=MagicMock(return_value={"Items": [self._row()]}),
                          update_item=MagicMock(side_effect=[_conditional_failure(), None]))
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.register({"pipelineExecutionId": "P1", "logs": [_log(stream="s1")]}, source=self._SRC)
        first, second = table.query.call_args_list
        assert "ConsistentRead" not in first.kwargs
        assert second.kwargs["ConsistentRead"] is True

    def test_a_second_failed_condition_appends_unconditionally_rather_than_dropping(self):
        # Both conditioned attempts lose. The re-read row already holds one of the two reported
        # locations, so only the other one and the sub-execution go on — without a condition, so the
        # abort path still learns of the job.
        rows = [self._row(), self._row(logs=[_log(stream="held")])]
        table = MagicMock(query=MagicMock(side_effect=[{"Items": [r]} for r in rows]),
                          update_item=MagicMock(side_effect=[_conditional_failure(), _conditional_failure(), None]))
        logger = MagicMock()
        with patch.object(reg.dynamodb, "Table", return_value=table), patch.object(reg, "logger", logger):
            reg.register({"pipelineExecutionId": "P1",
                          "subExecution": {"resourceType": "batchJob", "jobId": "job-1"},
                          "logs": [_log(stream="held"), _log(stream="s1")]}, source=self._SRC)
        assert table.update_item.call_count == 3
        for conditioned in table.update_item.call_args_list[:2]:
            assert "ConditionExpression" in conditioned.kwargs
        fallback = table.update_item.call_args_list[2].kwargs
        assert "ConditionExpression" not in fallback
        assert "list_append(if_not_exists(registeredSubExecutions, :empty), :s)" in fallback["UpdateExpression"]
        assert "list_append(if_not_exists(registeredLogs, :empty), :l)" in fallback["UpdateExpression"]
        assert fallback["ExpressionAttributeValues"] == {
            ":s": [{"resourceType": "batchJob", "jobId": "job-1"}], ":l": [_log(stream="s1")], ":empty": []}
        warnings = " ".join(str(c.args[0]) for c in logger.warning.call_args_list)
        assert "P1" in warnings and "unconditionally" in warnings

    def test_a_non_conditional_client_error_still_propagates_to_the_handler(self):
        import botocore
        other = botocore.exceptions.ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem")
        table = MagicMock(query=MagicMock(return_value={"Items": [self._row()]}),
                          update_item=MagicMock(side_effect=other))
        with patch.object(reg.dynamodb, "Table", return_value=table):
            with pytest.raises(botocore.exceptions.ClientError):
                reg.register({"pipelineExecutionId": "P1", "logs": [_log(stream="s1")]}, source=self._SRC)

    def test_a_known_location_is_merged_in_place_under_an_identity_guard(self):
        legacy = {"logGroupArn": _ARN + ":*", "logGroupName": "/aws/batch/job",
                  "logStreamName": "", "logStreamPrefix": "jd/default/"}
        row = self._row(logs=[_log(stream="other"), legacy])
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.register({"pipelineExecutionId": "P1",
                          "logs": [_log(prefix="jd/default/", stage="Batch", label="Batch container",
                                        source="batch")]}, source=self._SRC)
        assert table.update_item.call_count == 1
        kw = table.update_item.call_args.kwargs
        update = kw["UpdateExpression"]
        assert update.startswith("SET ")
        for field in ("label", "sourceType", "stageName"):
            assert f"registeredLogs[1].{field} = :m_{field}" in update
        assert "registeredLogs[0]" not in update
        guard = kw["ConditionExpression"]
        for field, placeholder in (("logGroupArn", ":loc_arn"), ("logStreamName", ":loc_stream"),
                                   ("logStreamPrefix", ":loc_prefix")):
            assert f"registeredLogs[1].{field} = {placeholder}" in guard
        assert kw["ExpressionAttributeValues"] == {
            ":loc_arn": _ARN + ":*", ":loc_stream": "", ":loc_prefix": "jd/default/",
            ":m_label": "Batch container", ":m_sourceType": "batch", ":m_stageName": "Batch"}
        assert "list_append" not in kw["UpdateExpression"]

    def test_a_merge_whose_guard_fails_is_skipped_with_a_warning(self):
        row = self._row(logs=[_log(prefix="jd/default/")])
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}),
                          update_item=MagicMock(side_effect=_conditional_failure()))
        logger = MagicMock()
        with patch.object(reg.dynamodb, "Table", return_value=table), patch.object(reg, "logger", logger):
            reg.register({"pipelineExecutionId": "P1",
                          "logs": [_log(prefix="jd/default/", stage="Batch")]}, source=self._SRC)
        assert table.update_item.call_count == 1
        assert logger.warning.called

    def test_appends_beyond_the_store_cap_are_skipped_and_warned(self):
        stored = [_log(stream=f"s{i}") for i in range(reg.MAX_REGISTERED_LOGS_STORED - 1)]
        row = self._row(logs=stored)
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        logger = MagicMock()
        with patch.object(reg.dynamodb, "Table", return_value=table), patch.object(reg, "logger", logger):
            reg.register({"pipelineExecutionId": "P1",
                          "logs": [_log(stream="new-a"), _log(stream="new-b")]}, source=self._SRC)
        kw = table.update_item.call_args.kwargs
        assert [e["logStreamName"] for e in kw["ExpressionAttributeValues"][":l"]] == ["new-a"]
        assert logger.warning.called

    def test_sub_execution_appends_are_capped_too(self):
        stored = [{"resourceType": "batchJob", "jobId": f"job-{i}"}
                  for i in range(reg.MAX_REGISTERED_SUB_EXECUTIONS_STORED)]
        row = self._row(subs=stored)
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        logger = MagicMock()
        with patch.object(reg.dynamodb, "Table", return_value=table), patch.object(reg, "logger", logger):
            reg.register({"pipelineExecutionId": "P1",
                          "subExecution": {"resourceType": "batchJob", "jobId": "job-new"}}, source=self._SRC)
        table.update_item.assert_not_called()
        assert logger.warning.called

    def test_an_ordinary_registration_emits_no_warning(self):
        # Control for the warning assertions above.
        table = MagicMock(query=MagicMock(return_value={"Items": [self._row()]}), update_item=MagicMock())
        logger = MagicMock()
        with patch.object(reg.dynamodb, "Table", return_value=table), patch.object(reg, "logger", logger):
            reg.register({"pipelineExecutionId": "P1", "logs": [_log(stream="s1")]}, source=self._SRC)
        assert not logger.warning.called
        assert table.update_item.call_count == 1


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
