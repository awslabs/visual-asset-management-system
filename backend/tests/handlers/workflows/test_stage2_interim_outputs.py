# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 2 unit tests: shared output-attribution module (executionOutputs), the interim
pipeline-tracking lambda (output version-diff + resolved input manifest), the error-handler
lambda (table reconciliation to FAILED), and the createWorkflow ASL flow (interim states +
error-catch routing + the Stage 2 input envelope)."""

import os
import sys
import types
import json
import pytest
from unittest.mock import MagicMock, patch

# Env vars the Stage 2 lambdas read at import time.
for k, v in {
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:vams-wf:*",
}.items():
    os.environ.setdefault(k, v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf

from backend.backend.common.workflows import executionOutputs as eo
from backend.backend.common.workflows import executionRecords as er
from backend.backend.handlers.workflows.sfn import interimPipelineTracking as ipt
from backend.backend.handlers.workflows.sfn import handleExecutionError as heh


# ============================ executionOutputs (pure) ============================

@pytest.mark.unit
class TestRunningStatusHelpers:
    """set_main_status_running / set_pipeline_status_running perform a conditional NEW->RUNNING
    update (ConditionExpression on executionStatus=NEW) so they never clobber a terminal status,
    and swallow the ConditionalCheckFailed a fast-finishing run raises."""

    def test_set_main_status_running_conditional_update(self):
        table = MagicMock()
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        eo.set_main_status_running(dynamo, "main-tbl", "E1", "db", "wf")
        kwargs = table.update_item.call_args.kwargs
        assert kwargs["ExpressionAttributeValues"][":st"] == "RUNNING"
        assert kwargs["ExpressionAttributeValues"][":new"] == "NEW"
        assert "executionStatus = :new" in kwargs["ConditionExpression"]

    def test_set_main_status_running_swallows_conditional_failure(self):
        table = MagicMock()
        table.update_item.side_effect = Exception("ConditionalCheckFailedException")
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        # Must not raise (a run that already finished/started is expected to fail the condition).
        eo.set_main_status_running(dynamo, "main-tbl", "E1", "db", "wf")

    def test_set_pipeline_status_running_conditional_update(self):
        table = MagicMock()
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        eo.set_pipeline_status_running(dynamo, "pexec-tbl", "P2", "E1")
        kwargs = table.update_item.call_args.kwargs
        assert kwargs["Key"] == {"pipelineExecutionId": "P2", "workflowExecutionId": "E1"}
        assert kwargs["ExpressionAttributeValues"][":st"] == "RUNNING"
        assert "executionStatus = :new" in kwargs["ConditionExpression"]


@pytest.mark.unit
class TestSetPipelineStatus:
    """set_pipeline_status guards against regressing a row that already holds a terminal status, so a
    still-in-flight interim lambda cannot flip an ABORTED/FAILED pipeline back to SUCCEEDED."""

    def test_conditions_on_non_terminal_status(self):
        table = MagicMock()
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        eo.set_pipeline_status(dynamo, "pexec-tbl", "P1", "E1", "SUCCEEDED", stop_date="2026-01-01T00:00:00Z")
        kwargs = table.update_item.call_args.kwargs
        condition = kwargs["ConditionExpression"]
        values = kwargs["ExpressionAttributeValues"]
        assert "attribute_not_exists(executionStatus)" in condition
        assert "NOT executionStatus IN (" in condition
        assert set(eo.TERMINAL_STATUSES).issubset(set(values.values()))
        assert values[":st"] == "SUCCEEDED"

    def test_swallows_conditional_failure_on_terminal_row(self):
        table = MagicMock()
        table.update_item.side_effect = Exception("ConditionalCheckFailedException")
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        # An already-ABORTED row fails the condition; that is expected and must not raise.
        eo.set_pipeline_status(dynamo, "pexec-tbl", "P1", "E1", "SUCCEEDED")

    def test_real_write_failure_propagates(self):
        table = MagicMock()
        table.update_item.side_effect = Exception("ProvisionedThroughputExceededException")
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        with pytest.raises(Exception, match="ProvisionedThroughput"):
            eo.set_pipeline_status(dynamo, "pexec-tbl", "P1", "E1", "SUCCEEDED")

    @pytest.mark.aws
    def test_terminal_row_is_not_regressed_against_dynamodb(self):
        # Against a real DynamoDB expression parser: an ABORTED row keeps its status while a RUNNING
        # row and a row with no status yet are advanced.
        import boto3
        from moto import mock_aws
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            ddb.create_table(
                TableName="pexec-cond",
                KeySchema=[{"AttributeName": "pipelineExecutionId", "KeyType": "HASH"},
                           {"AttributeName": "workflowExecutionId", "KeyType": "RANGE"}],
                AttributeDefinitions=[{"AttributeName": "pipelineExecutionId", "AttributeType": "S"},
                                      {"AttributeName": "workflowExecutionId", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST")
            table = ddb.Table("pexec-cond")
            table.put_item(Item={"pipelineExecutionId": "P1", "workflowExecutionId": "E1",
                                 "executionStatus": "ABORTED"})
            table.put_item(Item={"pipelineExecutionId": "P2", "workflowExecutionId": "E1",
                                 "executionStatus": "RUNNING"})
            table.put_item(Item={"pipelineExecutionId": "P3", "workflowExecutionId": "E1"})
            for pexec_id in ("P1", "P2", "P3"):
                eo.set_pipeline_status(ddb, "pexec-cond", pexec_id, "E1", "SUCCEEDED",
                                       stop_date="2026-01-01T00:00:00Z")
            statuses = {
                pexec_id: table.get_item(
                    Key={"pipelineExecutionId": pexec_id, "workflowExecutionId": "E1"}
                )["Item"].get("executionStatus")
                for pexec_id in ("P1", "P2", "P3")
            }
            assert statuses == {"P1": "ABORTED", "P2": "SUCCEEDED", "P3": "SUCCEEDED"}


@pytest.mark.unit
class TestOutputAttribution:
    def test_attribute_new_and_changed_versions(self):
        snapshot = {"k/a": "v1", "k/b": "v1"}
        current = [
            {"key": "k/a", "versionId": "v1", "relativePath": "/a"},   # unchanged -> not N's
            {"key": "k/b", "versionId": "v2", "relativePath": "/b"},   # changed -> N's
            {"key": "k/c", "versionId": "v1", "relativePath": "/c"},   # new -> N's
        ]
        produced = eo.attribute_pipeline_outputs(current, snapshot)
        rels = {f["relativePath"] for f in produced}
        assert rels == {"/b", "/c"}

    def test_attribute_without_version_signal(self):
        # A bucket without versioning reports every versionId as '' (both in the recorded baseline
        # and the current listing), so an overwrite carries no change signal. Those keys are
        # attributed to the pipeline rather than dropped.
        snapshot = {"k/a": ""}
        current = [
            {"key": "k/a", "versionId": "", "relativePath": "/a"},   # overwritten, no signal
            {"key": "k/b", "versionId": "", "relativePath": "/b"},   # new
        ]
        produced = eo.attribute_pipeline_outputs(current, snapshot)
        assert {f["relativePath"] for f in produced} == {"/a", "/b"}

    def test_build_resolved_manifest_shadows_only_overwritten_paths(self):
        # Original inputs: two asset files. Output folder has a new version of pump.e57 only.
        original = [
            {"relativePath": "/test/pump.e57", "bucket": "abkt", "key": "a1/test/pump.e57", "versionId": ""},
            {"relativePath": "/test/scan.las", "bucket": "abkt", "key": "a1/test/scan.las", "versionId": ""},
        ]
        s3 = MagicMock()
        # list_current_output_files is driven by the paginator; stub it directly instead.
        with patch.object(eo, "list_current_output_files", return_value=[
            {"key": "out/files/test/pump.e57", "relativePath": "/test/pump.e57",
             "versionId": "ov9", "fileSize": 1, "contentType": ""},
        ]):
            envelope = eo.build_resolved_manifest(s3, original, "abkt", "out/files/")
        # The manifest is now a grouped envelope; the resolved files are under inputFiles.
        assert envelope["schemaVersion"] == er.MANIFEST_SCHEMA_VERSION
        by_rel = {m["relativePath"]: m for m in envelope["inputFiles"]}
        # pump.e57 shadowed -> points at the OUTPUT file + its version.
        assert by_rel["/test/pump.e57"]["key"] == "out/files/test/pump.e57"
        assert by_rel["/test/pump.e57"]["versionId"] == "ov9"
        # scan.las untouched -> keeps the ORIGINAL asset file.
        assert by_rel["/test/scan.las"]["key"] == "a1/test/scan.las"

    def test_execution_input_prefix_keyed_only_on_execution_id(self):
        # REGRESSION (Fix 1): the input-definition folder must NOT depend on a job name --
        # executeWorkflow and the ASL each draw independent job-name uuids, so a job-name
        # segment would diverge. The prefix is a pure function of the execution id, and the
        # ASL template must produce the identical path when its {} is filled with the exec id.
        from backend.backend.common.workflows import executionRecords as er2
        prefix = er2.execution_input_prefix("EXECABC")
        assert prefix == "pipelines/workflowExecutionInputs/EXECABC/"
        # The createWorkflow ASL template (input_folder_template) filled with the exec name.
        asl_template = "pipelines/workflowExecutionInputs/{}/"
        assert asl_template.format("EXECABC") == prefix
        # Per-pipeline + metadata keys are likewise execution-id-only.
        assert er2.pipeline_input_config_key("EXECABC", 2) == prefix + "pipeline2/config.json"
        assert er2.pipeline_input_manifest_key("EXECABC", 1) == prefix + "pipeline1/manifest.json"
        assert er2.execution_input_metadata_key("EXECABC") == prefix + "metadata.json"

    def test_recorded_output_versions_reconstructs_snapshot(self):
        # Two prior pipelines' recorded output rows form the version baseline.
        def make_table(name):
            t = MagicMock()
            t.query.return_value = {"Items": [
                {"s3Key": "out/files/a", "s3VersionId": "v1"},
            ]}
            return t
        dynamo = MagicMock()
        dynamo.Table.side_effect = make_table
        snap = eo.recorded_output_versions(dynamo, "t-of", ["P1"])
        assert snap == {"out/files/a": "v1"}


# ============================ interim lambda ============================

@pytest.mark.unit
class TestInterimPipelineTracking:
    def _event(self, body):
        return {"body": body}

    def test_logs_prev_outputs_and_writes_next_manifest(self):
        body = {
            "workflowExecutionId": "EXEC1",
            "workflowDatabaseId": "wdb", "workflowId": "wf",
            "bucketAsset": "abkt",
            "outputFilesPrefix": "pipelines/p1/job/output/EXEC1/files/",
            "fromPipelineExecutionId": "P1",
            "priorPipelineExecutionIds": ["P1", "P2"],
            "nextPipelineManifestS3Key": "pipelines/p1/job/input/EXEC1/pipeline2/manifest.json",
            "nextPipelineConfigS3Key": "pipelines/p1/job/input/EXEC1/pipeline2/config.json",
        }
        # P1 produced one new output file; original input is one asset file.
        produced = [{"key": "pipelines/p1/job/output/EXEC1/files/test/x.glb",
                     "relativePath": "/test/x.glb", "versionId": "ov1",
                     "fileSize": 10, "contentType": ""}]
        put_object = MagicMock()
        record_outputs = MagicMock()
        set_status = MagicMock()
        with patch.object(ipt.s3c, "put_object", put_object), \
             patch.object(ipt.s3c, "get_object", MagicMock(side_effect=_absent_object_error())), \
             patch.object(ipt.eo, "recorded_output_versions", return_value={}), \
             patch.object(ipt.eo, "list_current_output_files", return_value=produced), \
             patch.object(ipt.eo, "record_pipeline_output_files", record_outputs), \
             patch.object(ipt.eo, "set_pipeline_status", set_status), \
             patch.object(ipt, "_get_original_input_entries", return_value=[
                 {"relativePath": "/test/x.glb", "bucket": "abkt", "key": "a1/test/x.glb", "versionId": ""}]):
            result = ipt.lambda_handler(self._event(body), MagicMock())
        # P1's outputs recorded + P1 marked SUCCEEDED.
        record_outputs.assert_called_once()
        assert record_outputs.call_args.args[2] == "P1"  # pipeline_execution_id
        set_status.assert_called_once()
        assert set_status.call_args.args[2] == "P1"
        # Next pipeline manifest written + locations returned.
        put_object.assert_called_once()
        assert put_object.call_args.kwargs["Key"] == body["nextPipelineManifestS3Key"]
        assert result["inputManifestS3Location"].endswith("pipeline2/manifest.json")
        assert result["inputConfigurationS3Location"].endswith("pipeline2/config.json")

    def test_next_pipeline_marked_running(self):
        # When the interim step carries a nextPipelineExecutionId, it flips that pipeline NEW->RUNNING
        # (via set_pipeline_status_running) as the previous one is marked SUCCEEDED, so the
        # per-pipeline progress indicator advances through the chain.
        body = {
            "workflowExecutionId": "EXEC1",
            "workflowDatabaseId": "wdb", "workflowId": "wf",
            "outputFilesPrefix": "pipelines/p1/job/output/EXEC1/files/",
            "fromPipelineExecutionId": "P1",
            "priorPipelineExecutionIds": ["P1"],
            "nextPipelineExecutionId": "P2",
            "nextPipelineManifestS3Key": "pipelines/p1/job/input/EXEC1/pipeline2/manifest.json",
            "nextPipelineConfigS3Key": "pipelines/p1/job/input/EXEC1/pipeline2/config.json",
        }
        run_status = MagicMock()
        with patch.object(ipt.s3c, "put_object", MagicMock()), \
             patch.object(ipt.s3c, "get_object", MagicMock(side_effect=_absent_object_error())), \
             patch.object(ipt.eo, "recorded_output_versions", return_value={}), \
             patch.object(ipt.eo, "list_current_output_files", return_value=[]), \
             patch.object(ipt.eo, "record_pipeline_output_files", MagicMock()), \
             patch.object(ipt.eo, "set_pipeline_status", MagicMock()), \
             patch.object(ipt.eo, "set_pipeline_status_running", run_status), \
             patch.object(ipt, "_get_original_input_entries", return_value=[]):
            ipt.lambda_handler(self._event(body), MagicMock())
        run_status.assert_called_once()
        # (dynamo, table, pipeline_execution_id, workflow_execution_id)
        assert run_status.call_args.args[2] == "P2"
        assert run_status.call_args.args[3] == "EXEC1"

    def test_original_input_relativepath_is_stripped_to_asset_relative(self):
        # The stored inputAssetFileKey is the FULL asset-ID-prefixed key; _get_original_input_entries
        # strips each file's OWN asset-root key (the per-row assetRootS3Key, bucket-relative) so
        # relativePath is asset-relative and matches the asset-relative output-files keys for shadow
        # detection. Each file's own aux preview prefix is rebuilt per file.
        inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
            {"inputAssetFileKey": "/a1xyz/test/pump.e57", "databaseId": "db", "assetId": "a1xyz",
             "s3Bucket": "abkt", "assetRootS3Key": "a1xyz/"},
        ]}))
        with patch.object(ipt.dynamodb, "Table", return_value=inputs_table):
            entries = ipt._get_original_input_entries("EXEC1")
        assert entries[0]["relativePath"] == "/test/pump.e57"   # base stripped, asset-relative
        assert entries[0]["key"] == "a1xyz/test/pump.e57"       # full key preserved for S3
        assert entries[0]["bucket"] == "abkt"                   # the file's own bucket
        assert entries[0]["assetRootS3Key"] == "a1xyz/"         # relative asset-root key
        # Per-file aux preview prefix keyed on the FULL asset file key: {databaseId}/{assetFileKey}/preview.
        assert entries[0]["auxPreviewPrefix"] == "db/a1xyz/test/pump.e57/preview"

    def test_shadowing_matches_after_stripping(self):
        # End-to-end shadow check with the full-prefixed stored key: pump.e57 was rewritten by
        # a prior pipeline (present in the output files folder), scan.las was not. Each input row
        # is self-locating (its own s3Bucket + assetRootS3Key).
        inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
            {"inputAssetFileKey": "/a1xyz/test/pump.e57", "databaseId": "db", "assetId": "a1",
             "s3Bucket": "abkt", "assetRootS3Key": "a1xyz/"},
            {"inputAssetFileKey": "/a1xyz/test/scan.las", "databaseId": "db", "assetId": "a1",
             "s3Bucket": "abkt", "assetRootS3Key": "a1xyz/"},
        ]}))
        body = {
            "workflowExecutionId": "EXEC1",
            "workflowExecutionS3InputOutputBucket": "abkt",
            "outputFilesPrefix": "pipelines/wei/EXEC1/files/",
            "nextPipelineManifestS3Key": "pipelines/workflowExecutionInputs/EXEC1/pipeline2/manifest.json",
            "nextPipelineConfigS3Key": "pipelines/workflowExecutionInputs/EXEC1/pipeline2/config.json",
        }
        captured = {}
        put_object = MagicMock(side_effect=lambda **kw: captured.update({kw["Key"]: kw["Body"]}))
        with patch.object(ipt.dynamodb, "Table", return_value=inputs_table), \
             patch.object(ipt.s3c, "put_object", put_object), \
             patch.object(ipt.s3c, "get_object", MagicMock(side_effect=_absent_object_error())), \
             patch.object(ipt.eo, "list_current_output_files", return_value=[
                 {"key": "pipelines/wei/EXEC1/files/test/pump.e57", "relativePath": "/test/pump.e57",
                  "versionId": "ov5", "fileSize": 1, "contentType": ""}]):
            result = ipt.prepare_next_pipeline(body)
        envelope = json.loads(captured[body["nextPipelineManifestS3Key"]].decode("utf-8"))
        input_files = envelope["inputFiles"]
        by_rel = {m["relativePath"]: m for m in input_files}
        # pump.e57 shadowed -> output version; scan.las untouched -> original asset file.
        assert by_rel["/test/pump.e57"]["key"] == "pipelines/wei/EXEC1/files/test/pump.e57"
        assert by_rel["/test/pump.e57"]["versionId"] == "ov5"
        assert by_rel["/test/scan.las"]["key"] == "a1xyz/test/scan.las"
        # No duplicate entry for the shadowed path (it shadows in place, not appended).
        assert len([m for m in input_files if m["relativePath"] == "/test/pump.e57"]) == 1
        # Shadowed entry keeps the original asset identity for traceability.
        assert by_rel["/test/pump.e57"]["assetId"] == "a1"
        assert result["inputManifestS3Location"].endswith("pipeline2/manifest.json")

    def test_next_pipeline_config_template_tags_rendered(self):
        # The interim lambda reads the next pipeline's raw config from S3, renders its template tags
        # against the next-pipeline manifest + execution context, and re-writes it in place.
        inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
            {"inputAssetFileKey": "/a1xyz/scan.e57", "databaseId": "db", "assetId": "a1xyz",
             "s3Bucket": "abkt", "assetRootS3Key": "a1xyz/"},
        ]}))
        raw_cfg = ('{"asset": "{{firstAssetFileAssetId}}", "uri": "{{firstAssetFileS3Uri}}", '
                   '"keys": {{assetFileKeyArray}}, "pipe": "{{pipelineId}}", "run": "{{executionId}}"}')
        cfg_key = "pipelines/workflowExecutionInputs/EXEC1/pipeline2/config.json"
        body = {
            "workflowExecutionId": "EXEC1",
            "workflowId": "wf1", "workflowDatabaseId": "wdb1", "executingUserName": "user@x",
            "workflowExecutionS3InputOutputBucket": "abkt",
            "outputFilesPrefix": "pipelines/wei/EXEC1/files/",
            "nextPipelineManifestS3Key": "pipelines/workflowExecutionInputs/EXEC1/pipeline2/manifest.json",
            "nextPipelineConfigS3Key": cfg_key,
            "nextPipelineExecutionId": "P2", "nextPipelineId": "convertPipe",
            "nextPipelineDatabaseId": "pdb", "nextPipelineJobName": "job-2",
        }
        captured = {}
        put_object = MagicMock(side_effect=lambda **kw: captured.update({kw["Key"]: kw["Body"]}))
        get_object = MagicMock(return_value={
            "Body": MagicMock(read=lambda: raw_cfg.encode("utf-8"))})
        with patch.object(ipt.dynamodb, "Table", return_value=inputs_table), \
             patch.object(ipt.s3c, "put_object", put_object), \
             patch.object(ipt.s3c, "get_object", get_object), \
             patch.object(ipt.eo, "list_current_output_files", return_value=[]):
            ipt.prepare_next_pipeline(body)
        rendered = json.loads(captured[cfg_key].decode("utf-8"))
        assert rendered["asset"] == "a1xyz"
        assert rendered["uri"] == "s3://abkt/a1xyz/scan.e57"
        assert rendered["keys"] == ["a1xyz/scan.e57"]
        assert rendered["pipe"] == "convertPipe"   # next pipeline identity from the interim payload
        assert rendered["run"] == "EXEC1"


# ============================ error handler ============================

@pytest.mark.unit
class TestHandleExecutionError:
    def test_reconciles_failed_execution(self):
        # The error-handler payload carries NO pipeline id (Step Functions can't inject the
        # failing state name into a static payload) -- the handler attributes the failure to
        # the in-flight (non-terminal) pipeline rows it fetches.
        body = {"workflowExecutionId": "EXEC1", "workflowDatabaseId": "wdb", "workflowId": "wf"}
        error_info = {"Error": "States.TaskFailed",
                      "Cause": json.dumps({"errorMessage": "boom"})}
        # One in-flight pipeline (gets a log row) + one already-SUCCEEDED (skipped).
        pipeline_rows = [
            {"pipelineExecutionId": "P1", "workflowExecutionId": "EXEC1", "executionStatus": "RUNNING"},
            {"pipelineExecutionId": "P0", "workflowExecutionId": "EXEC1", "executionStatus": "SUCCEEDED"},
        ]
        mark_terminal = MagicMock()
        finalize = MagicMock()
        logs_table = MagicMock()
        main_table = MagicMock(query=MagicMock(return_value={"Items": [{"executionStatus": "RUNNING"}]}))

        def _table(name):
            return logs_table if name == heh.pipeline_execution_logs_table else main_table

        with patch.object(heh, "_get_pipeline_rows", return_value=pipeline_rows), \
             patch.object(heh.eo, "mark_inflight_pipelines_terminal", mark_terminal), \
             patch.object(heh.eo, "finalize_main_row", finalize), \
             patch.object(heh, "_fetch_execution_log", return_value="full log"), \
             patch.object(heh.dynamodb, "Table", side_effect=_table):
            resp = heh.lambda_handler({"body": body, "errorInfo": error_info}, MagicMock())

        assert resp == {"handled": True}
        # In-flight pipelines marked FAILED.
        mark_terminal.assert_called_once()
        assert mark_terminal.call_args.args[3] == "FAILED"
        # Main row finalized FAILED with the extracted error message + log.
        finalize.assert_called_once()
        fkw = finalize.call_args
        assert "boom" in fkw.kwargs["execution_error"]
        assert fkw.kwargs["execution_log"] == "full log"
        # REGRESSION (Fix 4): a log row is written for the in-flight pipeline (P1) even with NO
        # threaded pipeline id, and NOT for the already-terminal one (P0).
        assert logs_table.put_item.call_count == 1
        assert logs_table.put_item.call_args.kwargs["Item"]["pipelineExecutionId"] == "P1"

    def test_error_handler_never_raises(self):
        # Even if reconciliation blows up, the handler returns (so the SFN reaches Fail).
        with patch.object(heh, "reconcile_failed_execution", side_effect=Exception("kaboom")):
            resp = heh.lambda_handler({"body": {"workflowExecutionId": "E"}, "errorInfo": {}}, MagicMock())
        assert resp == {"handled": True}

    def test_extract_error_message_from_cause_json(self):
        msg = heh._extract_error_message({"Error": "X", "Cause": json.dumps({"errorMessage": "detail"})})
        assert "detail" in msg

    def test_registered_sub_processes_are_stopped_before_rows_go_terminal(self):
        """A row stamped terminal is no longer abortable, so the sub-processes it registered must be
        stopped here. The already-terminal row's registration is left alone (that run finished)."""
        body = {"workflowExecutionId": "EXEC1", "workflowDatabaseId": "wdb", "workflowId": "wf"}
        pipeline_rows = [
            {"pipelineExecutionId": "P1", "workflowExecutionId": "EXEC1", "executionStatus": "RUNNING",
             "registeredSubExecutions": [
                 {"resourceType": "stepFunctionsExecution",
                  "executionArn": "arn:aws:states:us-east-1:1:execution:sub:x"},
                 {"resourceType": "batchJob", "jobId": "job-abc"}]},
            {"pipelineExecutionId": "P0", "workflowExecutionId": "EXEC1", "executionStatus": "SUCCEEDED",
             "registeredSubExecutions": [
                 {"resourceType": "stepFunctionsExecution",
                  "executionArn": "arn:aws:states:us-east-1:1:execution:sub:done"}]},
        ]
        stop_execution = MagicMock()
        terminate_job = MagicMock()
        logs_table = MagicMock()
        main_table = MagicMock(query=MagicMock(return_value={"Items": [{"executionStatus": "RUNNING"}]}))
        pexec_table = MagicMock()

        def _table(name):
            if name == heh.pipeline_execution_logs_table:
                return logs_table
            if name == heh.pipeline_executions_table:
                return pexec_table
            return main_table

        with patch.object(heh, "_get_pipeline_rows", return_value=pipeline_rows), \
             patch.object(heh.eo, "finalize_main_row", MagicMock()), \
             patch.object(heh, "_fetch_execution_log", return_value=""), \
             patch.object(heh.sfn_client, "stop_execution", stop_execution), \
             patch.object(heh.batch_client, "terminate_job", terminate_job), \
             patch.object(heh.dynamodb, "Table", side_effect=_table):
            heh.lambda_handler({"body": body, "errorInfo": {"Error": "States.Timeout"}}, MagicMock())

        stop_execution.assert_called_once_with(
            executionArn="arn:aws:states:us-east-1:1:execution:sub:x")
        assert terminate_job.call_args.kwargs["jobId"] == "job-abc"
        # The in-flight row was still stamped FAILED after the stops.
        assert pexec_table.update_item.call_args.kwargs[
            "ExpressionAttributeValues"][":st"] == "FAILED"

    def test_unstoppable_sub_process_is_recorded_on_the_pipeline_log_row(self):
        """Once the rows are terminal the log row is the only in-product record of what is still
        running, so a stop failure lands there rather than only in CloudWatch."""
        body = {"workflowExecutionId": "EXEC1", "workflowDatabaseId": "wdb", "workflowId": "wf"}
        pipeline_rows = [
            {"pipelineExecutionId": "P1", "workflowExecutionId": "EXEC1", "executionStatus": "RUNNING",
             "registeredSubExecutions": [{"resourceType": "ecsTask", "taskArn": "arn:task:9"}]},
        ]
        logs_table = MagicMock()
        main_table = MagicMock(query=MagicMock(return_value={"Items": []}))

        def _table(name):
            return logs_table if name == heh.pipeline_execution_logs_table else main_table

        with patch.object(heh, "_get_pipeline_rows", return_value=pipeline_rows), \
             patch.object(heh.eo, "set_pipeline_status", MagicMock()), \
             patch.object(heh.eo, "finalize_main_row", MagicMock()), \
             patch.object(heh, "_fetch_execution_log", return_value=""), \
             patch.object(heh.dynamodb, "Table", side_effect=_table):
            heh.lambda_handler({"body": body, "errorInfo": {"Error": "States.Timeout"}}, MagicMock())

        error_log = logs_table.put_item.call_args.kwargs["Item"]["errorLog"]
        assert "arn:task:9" in error_log


@pytest.mark.unit
class TestStopRegisteredSubProcesses:
    """The shared stop helper the error handler and the abort path both need."""

    def test_stops_sfn_and_batch_for_non_terminal_rows_only(self):
        sfn = MagicMock()
        batch = MagicMock()
        rows = [
            {"executionStatus": "RUNNING", "registeredSubExecutions": [
                {"resourceType": "stepFunctionsExecution", "executionArn": "arn:sub:1"},
                {"resourceType": "batchJob", "jobId": "j1"}]},
            {"executionStatus": "ABORTED", "registeredSubExecutions": [
                {"resourceType": "stepFunctionsExecution", "executionArn": "arn:sub:2"}]},
        ]
        assert eo.stop_registered_sub_processes(rows, sfn_client=sfn, batch_client=batch) == []
        sfn.stop_execution.assert_called_once_with(executionArn="arn:sub:1")
        batch.terminate_job.assert_called_once()

    def test_already_stopped_execution_is_not_a_warning(self):
        sfn = MagicMock()
        sfn.stop_execution.side_effect = _client_error("ExecutionDoesNotExist")
        assert eo.stop_registered_sub_process(
            {"resourceType": "stepFunctionsExecution", "executionArn": "arn:sub:1"},
            sfn_client=sfn) == ""

    def test_real_stop_failure_is_reported(self):
        sfn = MagicMock()
        sfn.stop_execution.side_effect = _client_error("AccessDeniedException")
        message = eo.stop_registered_sub_process(
            {"resourceType": "stepFunctionsExecution", "executionArn": "arn:sub:1"},
            sfn_client=sfn)
        assert "arn:sub:1" in message and "AccessDeniedException" in message

    def test_unsupported_resource_type_names_its_locator(self):
        message = eo.stop_registered_sub_process({"resourceType": "ecsTask", "taskArn": "arn:task:1"})
        assert "arn:task:1" in message

    def test_no_client_is_a_silent_no_op(self):
        # A caller without stop permissions still reconciles the rows rather than reporting noise.
        assert eo.stop_registered_sub_process(
            {"resourceType": "stepFunctionsExecution", "executionArn": "arn:sub:1"}) == ""


def _client_error(code):
    import botocore.exceptions
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": code}}, "StopExecution")


def _absent_object_error():
    """A missing-object GetObject fault, the case the interim step treats as 'no config/metadata to
    render'. Any other fault propagates so the state machine's Catch reconciles the run."""
    import botocore.exceptions
    return botocore.exceptions.ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "NoSuchKey"}}, "GetObject")


@pytest.mark.unit
class TestOutputListingVersionSource:
    """The output-files listing takes each key's versionId from the SAME list_object_versions
    pagination (the IsLatest entry), so a thousand-file folder costs list calls rather than a
    HeadObject per object."""

    def _versions_paginator(self, pages):
        paginator = MagicMock()
        paginator.paginate.return_value = pages
        return paginator

    def test_versions_come_from_the_listing_with_no_head_object(self):
        s3 = MagicMock()
        s3.get_paginator.return_value = self._versions_paginator([{"Versions": [
            {"Key": "out/files/a.glb", "VersionId": "v9", "IsLatest": True, "Size": 12},
            {"Key": "out/files/a.glb", "VersionId": "v8", "IsLatest": False, "Size": 11},
            {"Key": "out/files/sub/", "VersionId": "v1", "IsLatest": True, "Size": 0},
        ]}])
        files = eo.list_current_output_files(s3, "bkt", "out/files/")
        assert files == [{"key": "out/files/a.glb", "relativePath": "/a.glb", "versionId": "v9",
                          "fileSize": 12, "contentType": ""}]
        s3.head_object.assert_not_called()
        s3.get_paginator.assert_called_once_with("list_object_versions")

    def test_many_objects_cost_no_per_object_request(self):
        s3 = MagicMock()
        s3.get_paginator.return_value = self._versions_paginator([{"Versions": [
            {"Key": f"out/files/f{i}.glb", "VersionId": f"v{i}", "IsLatest": True, "Size": 1}
            for i in range(500)]}])
        assert len(eo.list_current_output_files(s3, "bkt", "out/files/")) == 500
        assert s3.head_object.call_count == 0

    def test_unversioned_bucket_falls_back_to_a_plain_listing(self):
        s3 = MagicMock()

        def _paginator(name):
            if name == "list_object_versions":
                raise Exception("versioning not enabled")
            return self._versions_paginator([
                {"Contents": [{"Key": "out/files/a.glb", "Size": 5}]}])

        s3.get_paginator.side_effect = _paginator
        files = eo.list_current_output_files(s3, "bkt", "out/files/")
        assert files[0]["versionId"] == ""
        s3.head_object.assert_not_called()

    def test_precomputed_listing_is_not_re_listed(self):
        # The interim lambda already lists this exact set for attribution; passing it through must not
        # cost a second pagination.
        s3 = MagicMock()
        listing = [{"key": "out/files/a.glb", "relativePath": "/a.glb", "versionId": "v3",
                    "fileSize": 1, "contentType": ""}]
        resolved = eo.resolve_manifest_input_files(
            s3, [{"relativePath": "/a.glb", "bucket": "abkt", "key": "a1/a.glb", "versionId": ""}],
            "bkt", "out/files/", current_output_files=listing)
        s3.get_paginator.assert_not_called()
        assert resolved[0]["key"] == "out/files/a.glb" and resolved[0]["versionId"] == "v3"

        envelope = eo.build_resolved_manifest(
            s3, [], "bkt", "out/files/", current_output_files=listing)
        s3.get_paginator.assert_not_called()
        assert envelope["inputFiles"][0]["versionId"] == "v3"
