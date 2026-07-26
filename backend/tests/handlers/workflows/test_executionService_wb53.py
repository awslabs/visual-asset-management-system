# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the WB5.3 global execution operations added to executionService: global
(asset-less) permission-filtered list, re-run reconstruction, permanent delete guard, abort-by-group.
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("WORKFLOW_EXECUTION_OUTPUTS_INDEX_TABLE_NAME", "t-out-index")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.handlers.workflows import executionService as le

MOD = "backend.backend.handlers.workflows.executionService"


@pytest.fixture(autouse=True)
def _clear_asset_cache():
    # The per-request asset memo is a module global; clear it between tests so a cached row from one
    # test cannot leak into another (mirrors the per-invocation clear in the real handlers).
    le._asset_details_cache.clear()
    yield
    le._asset_details_cache.clear()


def _allow_all():
    e = MagicMock()
    e.enforce.return_value = True
    e.enforceAPI.return_value = True
    return e


@pytest.mark.unit
class TestGlobalListRecencyWindow:
    """The global executions list queries the by-date GSI newest-first, defaulting to a recent window
    (90 days) and accepting an explicit filterStartDate/filterEndDate range as the SK key condition."""

    def _run(self, query_params):
        le.claims_and_roles = {"tokens": ["u1"]}
        table = MagicMock()
        table.query.return_value = {"Items": []}
        with patch(f"{MOD}.dynamodb") as mock_dynamodb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()):
            mock_dynamodb.Table.return_value = table
            resp = le.get_global_executions({}, query_params)
        return resp, table.query.call_args.kwargs

    def test_default_recency_window_queried_newest_first(self):
        resp, kwargs = self._run({"pageSize": "50"})
        # Queries the by-date GSI, newest-first, with a KeyConditionExpression (the date window).
        assert kwargs["IndexName"] == "WorkflowExecutionsByDateGSI"
        assert kwargs["ScanIndexForward"] is False
        assert "KeyConditionExpression" in kwargs
        body = json.loads(resp["body"])["message"]
        assert body["filterStartDate"].endswith("Z") and len(body["filterStartDate"]) >= 20
        assert "filterEndDate" not in body  # none supplied

    def test_explicit_range_echoed(self):
        resp, kwargs = self._run({
            "pageSize": "50",
            "filterStartDate": "2026-01-01T00:00:00Z",
            "filterEndDate": "2026-02-01T00:00:00Z",
        })
        assert kwargs["IndexName"] == "WorkflowExecutionsByDateGSI"
        body = json.loads(resp["body"])["message"]
        assert body["filterStartDate"] == "2026-01-01T00:00:00Z"
        assert body["filterEndDate"] == "2026-02-01T00:00:00Z"


@pytest.mark.unit
class TestGlobalListFilters:
    def test_filters_match_and_mismatch(self):
        row = {"workflowId": "wf", "workflowDatabaseId": "db", "executionStatus": "SUCCEEDED",
               "triggerType": "Manual", "executionGroupId": "g1", "triggeredByUserId": "u1"}
        assert le._global_list_matches_filters(row, {"workflowId": "wf"}) is True
        assert le._global_list_matches_filters(row, {"status": "FAILED"}) is False
        assert le._global_list_matches_filters(row, {"groupId": "g1", "triggerType": "Manual"}) is True
        assert le._global_list_matches_filters(row, {"groupId": "other"}) is False

    def test_visibility_requires_workflow_get(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        enforcer = MagicMock()
        enforcer.enforce.return_value = False  # workflow GET denied
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer):
            assert le._execution_visible_to_caller("E1", {"workflowId": "wf",
                                                          "workflowDatabaseId": "db"}) is False

    def test_visibility_via_input_asset(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        enforcer = _allow_all()
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[("db", "a1")]), \
             patch(f"{MOD}.get_asset_details", return_value={"assetId": "a1", "databaseId": "db"}):
            assert le._execution_visible_to_caller("E1", {"workflowId": "wf",
                                                          "workflowDatabaseId": "db"}) is True

    def test_visibility_empty_tokens_denied(self):
        le.claims_and_roles = {"tokens": []}
        assert le._execution_visible_to_caller("E1", {"workflowId": "wf",
                                                      "workflowDatabaseId": "db"}) is False

    def test_results_only_with_no_inputs_visible_on_workflow_get(self):
        # A results-only run with NO inputs is visible on workflow GET alone (no asset to gate on).
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value={"outputLocationType": "none"}):
            assert le._execution_visible_to_caller(
                "E1", {"workflowId": "wf", "workflowDatabaseId": "db"}) is True

    def test_results_only_with_unauthorized_inputs_not_visible(self):
        # L3: a results-only run that HAS input files the caller cannot GET must NOT be listed —
        # otherwise details/logs (which require GET on every input asset) would 403 on click-through.
        le.claims_and_roles = {"tokens": ["u1"]}
        enforcer = MagicMock()
        # workflow GET passes; asset GET denied.
        enforcer.enforce.side_effect = lambda obj, action, *a, **k: obj.get("object__type") != "asset"
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[("db", "a1")]), \
             patch(f"{MOD}.get_asset_details", return_value={"assetId": "a1", "databaseId": "db"}), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value={"outputLocationType": "none"}):
            assert le._execution_visible_to_caller(
                "E1", {"workflowId": "wf", "workflowDatabaseId": "db"}) is False


@pytest.mark.unit
class TestRerunReconstruction:
    def test_reconstructs_inputs_and_pipeline_params(self):
        # The stored inputAssetFileKey is the FULL key (asset root + relative); re-run must strip the
        # asset root back to the asset-relative key. _query_all: first input rows, then pe1 config.
        with patch(f"{MOD}._query_all", side_effect=[
                [{"databaseId": "db", "assetId": "a1", "inputAssetFileKey": "/a1/x.glb",
                  "assetRootS3Key": "a1/"}],
                [{"templateId": "tpl", "templateTags": [{"key": "k", "value": "v"}]}]]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[
                {"pipelineExecutionId": "pe1", "pipelineId": "p1"}]):
            body = le._reconstruct_execute_request(
                "E1", {"workflowId": "wf", "workflowDatabaseId": "db"},
                {"outputAssetId": "a1", "outputDatabaseId": "db"})
        # relativeFileKey is asset-relative (asset root stripped), NOT the stored full key.
        assert body["inputFiles"] == [{"databaseId": "db", "assetId": "a1", "relativeFileKey": "/x.glb"}]
        assert body["outputAssetId"] == "a1"
        assert body["pipelineExecutionParameters"]["p1"]["templateId"] == "tpl"

    def test_key_strip_helper(self):
        assert le._to_asset_relative_key("/a1/x.glb", "a1/") == "/x.glb"
        assert le._to_asset_relative_key("/a1/", "a1/") == "/"           # whole-asset collapses to '/'
        assert le._to_asset_relative_key("/custom/root/f.txt", "custom/root/") == "/f.txt"
        assert le._to_asset_relative_key("/x.glb", "") == "/x.glb"        # no root -> unchanged

    def test_reconstructs_template_less_override(self):
        # A template-less override run: no templateId; re-run must carry the raw (untruncated) override.
        with patch(f"{MOD}._query_all", side_effect=[
                [{"databaseId": "db", "assetId": "a1", "inputAssetFileKey": "/x.glb",
                  "assetRootS3Key": ""}],
                [{"customTemplateOverrideUsed": True, "customTemplateOverride": "raw: body",
                  "customTemplateOverrideTruncated": False}]]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[
                {"pipelineExecutionId": "pe1", "pipelineId": "p1"}]):
            body = le._reconstruct_execute_request(
                "E1", {"workflowId": "wf", "workflowDatabaseId": "db"},
                {"outputAssetId": "a1", "outputDatabaseId": "db"})
        assert body["pipelineExecutionParameters"]["p1"]["customTemplateOverride"] == "raw: body"

    def test_truncated_template_less_override_fails_rerun(self):
        # A template-less override that was truncated at capture cannot be reproduced; re-run must
        # fail explicitly rather than silently launch a divergent (empty-config) run.
        with patch(f"{MOD}._query_all", side_effect=[
                [{"databaseId": "db", "assetId": "a1", "inputAssetFileKey": "/x.glb", "assetRootS3Key": ""}],
                [{"customTemplateOverrideUsed": True, "customTemplateOverride": "partial",
                  "customTemplateOverrideTruncated": True}]]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[
                {"pipelineExecutionId": "pe1", "pipelineId": "p1"}]):
            with pytest.raises(le.VAMSGeneralErrorResponse):
                le._reconstruct_execute_request(
                    "E1", {"workflowId": "wf", "workflowDatabaseId": "db"},
                    {"outputAssetId": "a1", "outputDatabaseId": "db"})

    def test_truncated_override_with_template_skips_only_override(self):
        # A truncated override alongside a real templateId is reproducible from the template: the
        # override is dropped but the templateId is preserved, so the re-run is not blocked.
        with patch(f"{MOD}._query_all", side_effect=[
                [{"databaseId": "db", "assetId": "a1", "inputAssetFileKey": "/x.glb", "assetRootS3Key": ""}],
                [{"templateId": "t1", "customTemplateOverrideUsed": True,
                  "customTemplateOverride": "partial", "customTemplateOverrideTruncated": True}]]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[
                {"pipelineExecutionId": "pe1", "pipelineId": "p1"}]):
            body = le._reconstruct_execute_request(
                "E1", {"workflowId": "wf", "workflowDatabaseId": "db"},
                {"outputAssetId": "a1", "outputDatabaseId": "db"})
        params = body["pipelineExecutionParameters"]["p1"]
        assert params["templateId"] == "t1"
        assert "customTemplateOverride" not in params


@pytest.mark.unit
class TestRerunMfaPropagation:
    def test_rerun_propagates_real_mfa_state(self):
        # A non-MFA caller's rerun must invoke executeWorkflowV2 with mfaEnabled=False, so MFA-gated
        # roles are not silently activated (the delegated handler would otherwise see mfaEnabled=True).
        le.claims_and_roles = {"tokens": ["u1"], "mfaEnabled": False}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "workflowDatabaseId:workflowId": "db:wf"}
        captured = {}

        class _Payload:
            def read(self):
                return json.dumps({"statusCode": 200, "body": json.dumps({"executionId": "NEW"})}).encode()

        def _invoke(**kwargs):
            captured["payload"] = json.loads(kwargs["Payload"].decode("utf-8"))
            return {"Payload": _Payload()}

        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}._execution_visible_to_caller", return_value=True), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value={"outputAssetId": "a1", "outputDatabaseId": "db"}), \
             patch(f"{MOD}._reconstruct_execute_request", return_value={"inputFiles": []}), \
             patch.object(le, "execute_workflow_v2_function", "t-execv2"), \
             patch(f"{MOD}.lambda_client") as m_lambda:
            m_lambda.invoke.side_effect = _invoke
            model = type("M", (), {"executionGroupId": None})()
            resp = le.rerun_execution({"requestContext": {"authorizer": {}}}, "E1", model)
        assert resp["statusCode"] == 200
        assert captured["payload"]["lambdaCrossCall"]["mfaEnabled"] is False


@pytest.mark.unit
class TestPermanentDeleteGuard:
    def test_in_progress_blocks_delete(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowExecutionId": "E1", "executionStatus": "RUNNING", "executionStopDate": "",
                "workflow_execution_arn": "arn:ex", "workflowDatabaseId:workflowId": "db:wf"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
             patch(f"{MOD}.sfn") as m_sfn:
            m_sfn.describe_execution.return_value = {}  # no stopDate -> still running
            resp = le.permanent_delete_execution({}, "E1")
        assert resp["statusCode"] == 400
        assert "in progress" in json.loads(resp["body"])["message"].lower()

    def test_terminal_execution_deletes_rows(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowExecutionId": "E1", "executionStatus": "SUCCEEDED",
                "executionStopDate": "2026-01-01T00:00:00Z", "workflowDatabaseId:workflowId": "db:wf"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[]), \
             patch(f"{MOD}._delete_all_rows"), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value={"outputDatabaseId": "db", "outputAssetId": "a1"}), \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_dynamo.Table.return_value = MagicMock()
            resp = le.permanent_delete_execution({}, "E1")
        assert resp["statusCode"] == 200


@pytest.mark.unit
class TestAbortGroup:
    def test_authorized_reported_inaccessible_counted_not_leaked(self):
        # E1 terminal+authorized -> reported skipped-terminal; E2 running+authorized -> aborted;
        # E3 unauthorized -> counted opaquely, its id must NOT appear in results (no existence leak).
        le.claims_and_roles = {"tokens": ["u1"]}
        executions = [
            {"workflowExecutionId": "E1", "executionStatus": "SUCCEEDED", "executionStopDate": "x"},
            {"workflowExecutionId": "E2", "executionStatus": "RUNNING", "executionStopDate": ""},
            {"workflowExecutionId": "E3", "executionStatus": "RUNNING", "executionStopDate": ""},
        ]
        def _authz(execution_id, main_item):
            return (execution_id != "E3", "")
        with patch(f"{MOD}._executions_in_group", return_value=executions), \
             patch(f"{MOD}.authorize_abort", side_effect=_authz), \
             patch(f"{MOD}.abort_execution", return_value={"statusCode": 200}):
            resp = le.abort_group({}, "g1")
        assert resp["statusCode"] == 200
        message = json.loads(resp["body"])["message"]
        results = {r["executionId"]: r["status"] for r in message["results"]}
        assert results["E1"] == "skipped-terminal"
        assert results["E2"] == "aborted"
        assert "E3" not in results  # inaccessible member's id is not leaked
        assert message["skippedInaccessibleCount"] == 1

    def test_empty_group_404(self):
        with patch(f"{MOD}._executions_in_group", return_value=[]):
            resp = le.abort_group({}, "nope")
        assert resp["statusCode"] == 404


@pytest.mark.unit
class TestReconcileMainStatus:
    """_reconcile_main_status lazily reconciles a non-terminal main row against SFN. RUNNING is
    written at launch so the common path skips the poll; this only fires when the row is non-terminal
    AND stale (past the min sync interval), and catches an out-of-band terminal transition."""

    def test_terminal_row_skips_poll(self):
        main = {"workflowExecutionId": "E1", "executionStatus": "SUCCEEDED",
                "executionStopDate": "2026-01-01T00:00:00Z"}
        with patch.object(le, "sfn") as m_sfn:
            le._reconcile_main_status("E1", main)
        m_sfn.describe_execution.assert_not_called()

    def test_recent_sync_skips_poll(self):
        # A row polled within the min interval is not re-polled (RUNNING, fresh lastSfnSyncCheckDate).
        main = {"workflowExecutionId": "E1", "executionStatus": "RUNNING", "executionStopDate": "",
                "workflow_execution_arn": "arn:x", "lastSfnSyncCheckDate": le.er.iso_now()}
        with patch.object(le, "sfn") as m_sfn:
            le._reconcile_main_status("E1", main)
        m_sfn.describe_execution.assert_not_called()

    def test_stale_running_reconciles_to_terminal(self):
        main = {"workflowExecutionId": "E1", "workflowDatabaseId:workflowId": "db:wf",
                "executionStatus": "RUNNING", "executionStopDate": "",
                "workflow_execution_arn": "arn:x", "lastSfnSyncCheckDate": ""}
        import datetime
        with patch.object(le, "sfn") as m_sfn, \
             patch.object(le.dynamodb, "Table", return_value=MagicMock()):
            m_sfn.describe_execution.return_value = {
                "status": "ABORTED",
                "stopDate": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)}
            le._reconcile_main_status("E1", main)
        assert main["executionStatus"] == "ABORTED"
        assert main["executionStopDate"].startswith("2026-01-01")

    def test_stale_running_still_running_keeps_running(self):
        main = {"workflowExecutionId": "E1", "workflowDatabaseId:workflowId": "db:wf",
                "executionStatus": "RUNNING", "executionStopDate": "",
                "workflow_execution_arn": "arn:x", "lastSfnSyncCheckDate": ""}
        with patch.object(le, "sfn") as m_sfn, \
             patch.object(le.dynamodb, "Table", return_value=MagicMock()):
            m_sfn.describe_execution.return_value = {"status": "RUNNING"}  # no stopDate
            le._reconcile_main_status("E1", main)
        assert main["executionStatus"] == "RUNNING"
        assert main["executionStopDate"] == ""


@pytest.mark.unit
class TestScrubOutputFileDecimal:
    """_scrub_output_file must return a JSON-serializable dict; fileSize arrives from DynamoDB as a
    Decimal, and success()/json.dumps cannot encode Decimal (would 500 execution details)."""

    def test_decimal_filesize_coerced_to_int(self):
        from decimal import Decimal
        out = le._scrub_output_file({
            "relativeFilePath": "model.glb.mockout.json", "fileType": "file",
            "fileSize": Decimal("154"), "contentType": "application/json",
            "s3VersionId": "v1",
        })
        assert out["fileSize"] == 154 and isinstance(out["fileSize"], int)
        # The scrubbed record must be JSON-serializable (the actual 500 root cause).
        json.dumps(out)

    def test_missing_filesize_omitted(self):
        out = le._scrub_output_file({"relativeFilePath": "x", "fileType": "file"})
        assert "fileSize" not in out
        json.dumps(out)


@pytest.mark.unit
class TestPipelineDetailScrub:
    """_scrub_pipeline_detail: human-readable name (fixes the web 'Unknown pipeline') + the
    rendered config body attached per pipeline."""

    def test_name_from_pipeline_def(self):
        out = le._scrub_pipeline_detail(
            {"pipelineId": "p1", "pipelineExecutionType": "Lambda"},
            {"pipelineName": "My Pipeline", "description": "d", "category": "conversion"},
            "{\"k\": 1}", False)
        assert out["name"] == "My Pipeline"
        assert out["pipelineType"] == "conversion"
        assert out["renderedConfig"] == "{\"k\": 1}"
        assert out["renderedConfigTruncated"] is False
        json.dumps(out)

    def test_name_falls_back_to_id_when_def_missing(self):
        out = le._scrub_pipeline_detail({"pipelineId": "p1"}, {}, "", False)
        # Falls back to the pipelineId (never blank -> the UI won't show "Unknown Pipeline").
        assert out["name"] == "p1"
        assert out["renderedConfig"] == ""

    def test_rendered_config_truncated_flag(self):
        out = le._scrub_pipeline_detail({"pipelineId": "p1"}, {"pipelineName": "P"}, "body", True)
        assert out["renderedConfigTruncated"] is True

    def test_exposes_pipeline_execution_id(self):
        # The web log viewer scopes per-step logs by pipelineExecutionId, so the detail view
        # must surface it.
        out = le._scrub_pipeline_detail(
            {"pipelineId": "p1", "pipelineExecutionId": "pe-123"}, {"pipelineName": "P"}, "", False)
        assert out["pipelineExecutionId"] == "pe-123"


@pytest.mark.unit
class TestGetExecutionLogsLiveFallback:
    """Truncated-mode logs fall back to a live CloudWatch search when the stored log is empty
    (the end-state lambda captures the stored log before CloudWatch finishes ingesting the run,
    so the stored copy is frequently empty even for a succeeded execution)."""

    def _q(self, **kw):
        return {k: v for k, v in kw.items()}

    def test_whole_execution_falls_back_to_live_when_stored_empty(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db", "executionLog": "",
                "executionError": "", "executionLogGroupArn": "arn:...:log-group:/g:*"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}._full_log_search",
                   return_value={"events": [{"timestamp": 1, "message": "live line"}],
                                 "nextToken": None}) as fls:
            resp = le.get_execution_logs({}, "E1", self._q(mode="truncated"))
        body = json.loads(resp["body"])["message"]
        assert body["logsSource"] == "live"
        assert "live line" in body["executionLog"]
        fls.assert_called_once()

    def test_whole_execution_uses_stored_when_present(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db", "executionLog": "stored text",
                "executionError": "", "executionLogGroupArn": "arn:...:log-group:/g:*"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}._full_log_search") as fls:
            resp = le.get_execution_logs({}, "E1", self._q(mode="truncated"))
        body = json.loads(resp["body"])["message"]
        assert body["logsSource"] == "stored"
        assert body["executionLog"] == "stored text"
        fls.assert_not_called()

    def test_pipeline_scope_falls_back_to_live_when_stored_empty(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:...:log-group:/g:*"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows",
                   return_value=[{"pipelineExecutionId": "pe-1"}]), \
             patch(f"{MOD}._query_all", return_value=[]), \
             patch(f"{MOD}._full_log_search",
                   return_value={"events": [{"timestamp": 1, "message": "step line"}],
                                 "nextToken": None}) as fls:
            resp = le.get_execution_logs({}, "E1", self._q(mode="truncated", pipelineExecutionId="pe-1"))
        body = json.loads(resp["body"])["message"]
        assert body["logsSource"] == "live"
        assert "step line" in body["resultLog"]
        # Live search is scoped to both the execution and the pipeline execution.
        args = fls.call_args[0]
        assert args[1] == ["E1", "pe-1"]

    def test_whole_execution_falls_back_to_sfn_history_when_cloudwatch_empty(self):
        # When both the stored log and the live CloudWatch search are empty, the whole-execution
        # view falls back to the Step Functions execution history (authoritative, no ingestion lag).
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db", "executionLog": "",
                "executionError": "", "executionLogGroupArn": "arn:...:log-group:/g:*",
                "workflow_execution_arn": "arn:aws:states:us-west-2:1:execution:sm:E1"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}._full_log_search",
                   return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [{"timestamp": 1, "message": "TaskStateEntered: Convert"}],
                                 "nextToken": None}) as hist:
            resp = le.get_execution_logs({}, "E1", self._q(mode="truncated"))
        body = json.loads(resp["body"])["message"]
        assert body["logsSource"] == "sfnHistory"
        assert "TaskStateEntered: Convert" in body["executionLog"]
        hist.assert_called_once()

    def test_full_mode_whole_execution_includes_sfn_history(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:...:log-group:/g:*",
                "workflow_execution_arn": "arn:aws:states:us-west-2:1:execution:sm:E1"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [{"timestamp": 1, "message": "ExecutionStarted"}],
                                 "nextToken": None}):
            resp = le.get_execution_logs({}, "E1", self._q(mode="full"))
        body = json.loads(resp["body"])["message"]
        assert body["sfnHistoryEvents"][0]["message"] == "ExecutionStarted"

    def test_full_mode_pipeline_scope_resolves_sub_sfn_logs(self):
        # A registered Step Functions sub-execution surfaces its own history plus the resolved log
        # group of its state machine (discovered from the state-machine ARN, not explicitly reported).
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:...:log-group:/g:*"}
        prow = {"pipelineExecutionId": "pe-1", "registeredLogs": [],
                "registeredSubExecutions": [{
                    "resourceType": "stepFunctionsExecution",
                    "stateMachineArn": "arn:aws:states:us-west-2:1:stateMachine:sub",
                    "executionArn": "arn:aws:states:us-west-2:1:execution:sub:x"}]}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [{"timestamp": 2, "message": "TaskSucceeded"}],
                                 "nextToken": None}), \
             patch(f"{MOD}._resolve_sfn_log_group_arn",
                   return_value="arn:aws:logs:us-west-2:1:log-group:/aws/vendedlogs/sub") as resolve, \
             patch(f"{MOD}._fetch_registered_log_events",
                   return_value=(True, [{"timestamp": 3, "message": "sub log line",
                                         "logGroupArn": "arn:...:/aws/vendedlogs/sub"}])) as fetch:
            resp = le.get_execution_logs(
                {}, "E1", self._q(mode="full", pipelineExecutionId="pe-1"))
        body = json.loads(resp["body"])["message"]
        msgs = [e["message"] for e in body["subProcessEvents"]]
        assert "TaskSucceeded" in msgs  # sub-SFN history
        assert "sub log line" in msgs   # resolved sub-SFN log group
        resolve.assert_called_once_with("arn:aws:states:us-west-2:1:stateMachine:sub")
        # L1: the shared sub-SFN log group is read SCOPED to this execution + pipeline, never whole.
        _, kw = fetch.call_args
        assert kw.get("scope_terms") == ["E1", "pe-1"]

    def test_full_mode_sub_sfn_log_group_not_double_read(self):
        # L2: when a sub-execution's resolved log group is the SAME group already read from
        # registeredLogs, it is not read a second time (no duplicate events).
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:...:log-group:/g:*"}
        shared = "arn:aws:logs:us-west-2:1:log-group:/aws/vendedlogs/sub"
        prow = {"pipelineExecutionId": "pe-1",
                "registeredLogs": [{"logGroupArn": shared}],
                "registeredSubExecutions": [{
                    "resourceType": "stepFunctionsExecution",
                    "stateMachineArn": "arn:aws:states:us-west-2:1:stateMachine:sub",
                    "executionArn": "arn:aws:states:us-west-2:1:execution:sub:x"}]}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._resolve_sfn_log_group_arn", return_value=shared), \
             patch(f"{MOD}._fetch_registered_log_events",
                   return_value=(True, [])) as fetch:
            le.get_execution_logs({}, "E1", self._q(mode="full", pipelineExecutionId="pe-1"))
        # The shared group is read exactly once (from registeredLogs), not again after resolution.
        assert fetch.call_count == 1


@pytest.mark.unit
class TestSfnHistoryFormatting:
    """_sfn_history_event_line renders a concise timeline line per Step Functions history event."""

    def test_state_entered_uses_state_name(self):
        ev = {"type": "TaskStateEntered", "stateEnteredEventDetails": {"name": "Convert"}}
        assert le._sfn_history_event_line(ev) == "TaskStateEntered: Convert"

    def test_task_failed_includes_error_and_cause(self):
        ev = {"type": "TaskFailed",
              "taskFailedEventDetails": {"resourceType": "lambda", "error": "E", "cause": "boom"}}
        line = le._sfn_history_event_line(ev)
        assert "TaskFailed" in line and "E: boom" in line

    def test_execution_failed_includes_error(self):
        ev = {"type": "ExecutionFailed",
              "executionFailedEventDetails": {"error": "States.Timeout", "cause": "no token"}}
        line = le._sfn_history_event_line(ev)
        assert "ExecutionFailed" in line and "States.Timeout" in line

    def test_bare_event_returns_type(self):
        assert le._sfn_history_event_line({"type": "ExecutionStarted"}) == "ExecutionStarted"

    def test_history_filters_to_summary_types_and_stamps_epoch_ms(self):
        import datetime
        ts = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
        fake = {"events": [
            {"type": "ExecutionStarted", "timestamp": ts},
            {"type": "LambdaFunctionScheduled", "timestamp": ts},  # not a summary type -> dropped
        ]}
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.get_execution_history.return_value = fake
            out = le._sfn_execution_history_events("arn:exec", {})
        assert len(out["events"]) == 1
        assert out["events"][0]["message"] == "ExecutionStarted"
        assert out["events"][0]["timestamp"] == int(ts.timestamp() * 1000)

    def test_history_empty_on_no_arn(self):
        assert le._sfn_execution_history_events("", {}) == {"events": [], "nextToken": None}

    def test_resolve_sfn_log_group_from_logging_config(self):
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_state_machine.return_value = {
                "loggingConfiguration": {"destinations": [
                    {"cloudWatchLogsLogGroup": {"logGroupArn": "arn:logs:...:log-group:/g:*"}}]}}
            assert le._resolve_sfn_log_group_arn("arn:sm") == "arn:logs:...:log-group:/g:*"

    def test_resolve_sfn_log_group_empty_when_no_destination(self):
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_state_machine.return_value = {"loggingConfiguration": {"destinations": []}}
            assert le._resolve_sfn_log_group_arn("arn:sm") == ""
