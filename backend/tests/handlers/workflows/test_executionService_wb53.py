# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the WB5.3 global execution operations added to executionService: global
(asset-less) permission-filtered list, re-run reconstruction, permanent delete guard, abort-by-group.
"""

import base64
import json
import os
import botocore
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
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.handlers.workflows import executionService as le

MOD = "backend.backend.handlers.workflows.executionService"


@pytest.fixture(autouse=True)
def _clear_asset_cache():
    # The per-request asset memo and the decision memo are module-level; clear both between tests so a
    # cached row or authorization decision from one test cannot leak into another (mirrors the
    # per-invocation clear in the real handlers). Clearing the decision memo here is what makes a future
    # memo-scoping regression fail loudly in these tests rather than leak silently between them.
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    yield
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()


@pytest.fixture(autouse=True)
def _stub_configuration_row():
    """Stand in for the workflow-execution configuration table.

    A failed read of that row RAISES rather than degrading to {} — it carries the metadata sources and
    output target the read gate checks, so answering a failed read with {} would remove every
    data-level check and let a throttle turn a denial into an approval. Tests here that do not stub
    DynamoDB would otherwise attempt the real GetItem; the many tests that care about the row's CONTENT
    patch over this with their own return value."""
    with patch.object(le, "get_workflow_execution_configuration_row", return_value={}):
        yield


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

    def test_global_list_row_projects_output_target_from_config_row(self):
        """The output target lives on the CONFIGURATION row, not the main row, so the projection must
        take it from the passed-in item. It is threaded in (rather than read here) so the list stays at
        one configuration read per execution — the visibility check already fetches it."""
        main = {"workflowExecutionId": "e1000000000000000000000000000001", "workflowId": "wf", "workflowDatabaseId": "db",
                "executionStatus": "SUCCEEDED", "executionStartDate": "2026-01-01T00:00:00Z",
                "executionStopDate": "", "triggerType": "Manual", "triggeredByUserId": "u1",
                "executionGroupId": "g1"}
        cfg = {"outputLocationType": "asset", "outputAssetId": "a1", "outputDatabaseId": "d1"}

        row = le._global_list_row(main, cfg)
        assert row["outputLocationType"] == "asset"
        assert row["outputAssetId"] == "a1"
        assert row["outputDatabaseId"] == "d1"
        # The main-row fields are unchanged.
        assert row["workflowDatabaseId"] == "db"
        assert row["executionStatus"] == "SUCCEEDED"
        # No S3/ARN internals leak into the public row.
        assert not any("arn" in k.lower() or "s3" in k.lower() for k in row)

    def test_global_list_row_defaults_output_target_when_config_row_missing(self):
        """A missing configuration row (or an execution with no output target) yields empty strings
        rather than KeyErrors, so the column renders blank instead of failing the page."""
        main = {"workflowExecutionId": "e1000000000000000000000000000001", "workflowId": "wf", "workflowDatabaseId": "db"}
        for cfg in (None, {}):
            row = le._global_list_row(main, cfg)
            assert row["outputLocationType"] == ""
            assert row["outputAssetId"] == ""
            assert row["outputDatabaseId"] == ""

    def test_visibility_accepts_prefetched_config_row_without_reading_again(self):
        """When the caller passes config_row, the visibility check must NOT re-read it — that is what
        keeps the global list from doubling its configuration reads per page."""
        with patch(f"{MOD}.claims_and_roles", {"tokens": ["t"]}),              patch(f"{MOD}.get_execution_input_assets", return_value=[]),              patch(f"{MOD}._get_asset_details_cached", return_value={"assetId": "a1"}),              patch(f"{MOD}.get_workflow_execution_configuration_row") as read_cfg:
            enf = MagicMock()
            enf.enforce.return_value = True
            visible = le._execution_visible_to_caller(
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"}, enf,
                config_row={"outputDatabaseId": "d1", "outputAssetId": "a1"})
            assert visible is True
            read_cfg.assert_not_called()

    def test_global_list_reads_the_config_row_once_per_visible_row_and_not_for_hidden_rows(self):
        """The whole-loop property: with 3 candidates of which 1 is visible only via its OUTPUT asset,
        the page performs exactly ONE configuration read. Reading eagerly per candidate would make a
        narrowly-scoped role pay a lookup for the entire page (pageSize up to 100)."""
        le.claims_and_roles = {"tokens": ["u1"]}
        rows = [{"workflowExecutionId": f"e{i}" + "0" * 29 + f"{i}", "workflowId": f"wf{i}",
                 "workflowDatabaseId": "db"} for i in (1, 2, 3)]
        table = MagicMock()
        table.query.return_value = {"Items": rows}

        # Only wf3 is visible, and only through its output asset — wf1/wf2 fail workflow GET.
        def enforce(obj, action):
            if obj.get("object__type") == "workflow":
                return obj.get("workflowId") == "wf3"
            return True

        enf = MagicMock()
        enf.enforce.side_effect = enforce
        enf.enforceAPI.return_value = True

        with patch(f"{MOD}.dynamodb") as mock_dynamodb,              patch(f"{MOD}.CasbinEnforcer", return_value=enf),              patch(f"{MOD}.get_execution_input_assets", return_value=[]),              patch(f"{MOD}._get_asset_details_cached", return_value={"assetId": "a1"}),              patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value={"outputDatabaseId": "d1", "outputAssetId": "a1",
                                 "outputLocationType": "asset"}) as read_cfg:
            mock_dynamodb.Table.return_value = table
            resp = le.get_global_executions({}, {"pageSize": "50"})

        items = json.loads(resp["body"])["message"]["Items"]
        assert [i["workflowExecutionId"] for i in items] == ["e3000000000000000000000000000003"]
        # One read for the single visible row; none for the two discarded ones.
        assert read_cfg.call_count == 1
        assert read_cfg.call_args.args[0] == "e3000000000000000000000000000003"
        # And the projection still reports the output target from that same read.
        assert items[0]["outputAssetId"] == "a1"
        assert items[0]["outputDatabaseId"] == "d1"
        assert items[0]["outputLocationType"] == "asset"

    def test_visibility_prefers_the_loader_and_calls_it_only_once(self):
        """A caller that also needs the row for its own projection passes a loader, so the read is
        shared rather than issued twice."""
        calls = []

        def loader():
            calls.append(1)
            return {"outputDatabaseId": "d1", "outputAssetId": "a1"}

        with patch(f"{MOD}.claims_and_roles", {"tokens": ["t"]}),              patch(f"{MOD}.get_execution_input_assets", return_value=[]),              patch(f"{MOD}._get_asset_details_cached", return_value={"assetId": "a1"}),              patch(f"{MOD}.get_workflow_execution_configuration_row") as read_cfg:
            enf = MagicMock()
            enf.enforce.return_value = True
            visible = le._execution_visible_to_caller(
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"}, enf,
                config_row_loader=loader)
            assert visible is True
            assert len(calls) == 1
            read_cfg.assert_not_called()

    def test_visibility_reads_the_config_row_at_most_once_for_an_input_asset_authorized_row(self):
        """A row the caller can see through an input asset costs exactly ONE configuration read, not
        one per branch. Every check after workflow GET needs the row (the metadata sources, the output
        asset, and the results-only fallback all live on it), so it is read once and reused."""
        calls = []
        with patch(f"{MOD}.claims_and_roles", {"tokens": ["t"]}),              patch(f"{MOD}.get_execution_input_assets", return_value=[("db1", "a1")]),              patch(f"{MOD}._get_asset_details_cached", return_value={"assetId": "a1"}),              patch(f"{MOD}.get_workflow_execution_configuration_row") as read_cfg:
            enf = MagicMock()
            enf.enforce.return_value = True
            visible = le._execution_visible_to_caller(
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"}, enf,
                config_row_loader=lambda: calls.append(1) or {})
            assert visible is True
            assert calls == [1], "the loader is the single source of the row, called exactly once"
            read_cfg.assert_not_called()

    def test_visibility_does_not_read_the_config_row_when_workflow_get_is_denied(self):
        """A row the caller cannot see is discarded before the read. Eagerly reading in the list loop
        would charge one lookup for every candidate the visibility filter then throws away."""
        calls = []
        with patch(f"{MOD}.claims_and_roles", {"tokens": ["t"]}),              patch(f"{MOD}.get_execution_input_assets", return_value=[]),              patch(f"{MOD}.get_workflow_execution_configuration_row") as read_cfg:
            enf = MagicMock()
            enf.enforce.return_value = False
            visible = le._execution_visible_to_caller(
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"}, enf,
                config_row_loader=lambda: calls.append(1) or {})
            assert visible is False
            assert calls == []
            read_cfg.assert_not_called()

    def test_visibility_requires_workflow_get(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        enforcer = MagicMock()
        enforcer.enforce.return_value = False  # workflow GET denied
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer):
            assert le._execution_visible_to_caller("e1000000000000000000000000000001", {"workflowId": "wf",
                                                          "workflowDatabaseId": "db"}) is False

    def test_visibility_via_input_asset(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        enforcer = _allow_all()
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[("db", "a1")]), \
             patch(f"{MOD}.get_asset_details", return_value={"assetId": "a1", "databaseId": "db"}):
            assert le._execution_visible_to_caller("e1000000000000000000000000000001", {"workflowId": "wf",
                                                          "workflowDatabaseId": "db"}) is True

    def test_visibility_empty_tokens_denied(self):
        le.claims_and_roles = {"tokens": []}
        assert le._execution_visible_to_caller("e1000000000000000000000000000001", {"workflowId": "wf",
                                                      "workflowDatabaseId": "db"}) is False

    def test_results_only_with_no_inputs_visible_on_workflow_get(self):
        # A results-only run with NO inputs is visible on workflow GET alone (no asset to gate on).
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value={"outputLocationType": "none"}):
            assert le._execution_visible_to_caller(
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"}) is True

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
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"}) is False


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
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"},
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
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"},
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
                    "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"},
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
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"},
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
                return json.dumps({"statusCode": 200, "body": json.dumps({"executionId": "efffffffffffffffffffffffffffffff"})}).encode()

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
            resp = le.rerun_execution({"requestContext": {"authorizer": {}}}, "e1000000000000000000000000000001", model)
        assert resp["statusCode"] == 200
        assert captured["payload"]["lambdaCrossCall"]["mfaEnabled"] is False


@pytest.mark.unit
class TestPermanentDeleteGuard:
    def test_in_progress_blocks_delete(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowExecutionId": "e1000000000000000000000000000001", "executionStatus": "RUNNING", "executionStopDate": "",
                "workflow_execution_arn": "arn:ex", "workflowDatabaseId:workflowId": "db:wf"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
             patch(f"{MOD}.sfn") as m_sfn:
            m_sfn.describe_execution.return_value = {}  # no stopDate -> still running
            resp = le.permanent_delete_execution({}, "e1000000000000000000000000000001")
        assert resp["statusCode"] == 400
        assert "in progress" in json.loads(resp["body"])["message"].lower()

    def test_terminal_execution_deletes_rows(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowExecutionId": "e1000000000000000000000000000001", "executionStatus": "SUCCEEDED",
                "executionStopDate": "2026-01-01T00:00:00Z", "workflowDatabaseId:workflowId": "db:wf"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[]), \
             patch(f"{MOD}._delete_all_rows"), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value={"outputDatabaseId": "db", "outputAssetId": "a1"}), \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_dynamo.Table.return_value = MagicMock()
            resp = le.permanent_delete_execution({}, "e1000000000000000000000000000001")
        assert resp["statusCode"] == 200


@pytest.mark.unit
class TestAbortGroup:
    def test_authorized_reported_inaccessible_counted_not_leaked(self):
        # E1 terminal+authorized -> reported skipped-terminal; E2 running+authorized -> aborted;
        # E3 unauthorized -> counted opaquely, its id must NOT appear in results (no existence leak).
        le.claims_and_roles = {"tokens": ["u1"]}
        executions = [
            {"workflowExecutionId": "e1000000000000000000000000000001", "executionStatus": "SUCCEEDED", "executionStopDate": "x"},
            {"workflowExecutionId": "e2000000000000000000000000000002", "executionStatus": "RUNNING", "executionStopDate": ""},
            {"workflowExecutionId": "e3000000000000000000000000000003", "executionStatus": "RUNNING", "executionStopDate": ""},
        ]
        def _authz(execution_id, main_item):
            return (execution_id != "e3000000000000000000000000000003", "")
        with patch(f"{MOD}._executions_in_group", return_value=executions), \
             patch(f"{MOD}.authorize_abort", side_effect=_authz), \
             patch(f"{MOD}.abort_execution", return_value={"statusCode": 200}):
            resp = le.abort_group({}, "g1")
        assert resp["statusCode"] == 200
        message = json.loads(resp["body"])["message"]
        results = {r["executionId"]: r["status"] for r in message["results"]}
        assert results["e1000000000000000000000000000001"] == "skipped-terminal"
        assert results["e2000000000000000000000000000002"] == "aborted"
        assert "e3000000000000000000000000000003" not in results  # inaccessible member's id is not leaked
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
        main = {"workflowExecutionId": "e1000000000000000000000000000001", "executionStatus": "SUCCEEDED",
                "executionStopDate": "2026-01-01T00:00:00Z"}
        with patch.object(le, "sfn") as m_sfn:
            le._reconcile_main_status("e1000000000000000000000000000001", main)
        m_sfn.describe_execution.assert_not_called()

    def test_recent_sync_skips_poll(self):
        # A row polled within the min interval is not re-polled (RUNNING, fresh lastSfnSyncCheckDate).
        main = {"workflowExecutionId": "e1000000000000000000000000000001", "executionStatus": "RUNNING", "executionStopDate": "",
                "workflow_execution_arn": "arn:x", "lastSfnSyncCheckDate": le.er.iso_now()}
        with patch.object(le, "sfn") as m_sfn:
            le._reconcile_main_status("e1000000000000000000000000000001", main)
        m_sfn.describe_execution.assert_not_called()

    def test_stale_running_reconciles_to_terminal(self):
        main = {"workflowExecutionId": "e1000000000000000000000000000001", "workflowDatabaseId:workflowId": "db:wf",
                "executionStatus": "RUNNING", "executionStopDate": "",
                "workflow_execution_arn": "arn:x", "lastSfnSyncCheckDate": ""}
        import datetime
        with patch.object(le, "sfn") as m_sfn, \
             patch.object(le.dynamodb, "Table", return_value=MagicMock()):
            m_sfn.describe_execution.return_value = {
                "status": "ABORTED",
                "stopDate": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)}
            le._reconcile_main_status("e1000000000000000000000000000001", main)
        assert main["executionStatus"] == "ABORTED"
        assert main["executionStopDate"].startswith("2026-01-01")

    def test_stale_running_still_running_keeps_running(self):
        main = {"workflowExecutionId": "e1000000000000000000000000000001", "workflowDatabaseId:workflowId": "db:wf",
                "executionStatus": "RUNNING", "executionStopDate": "",
                "workflow_execution_arn": "arn:x", "lastSfnSyncCheckDate": ""}
        with patch.object(le, "sfn") as m_sfn, \
             patch.object(le.dynamodb, "Table", return_value=MagicMock()):
            m_sfn.describe_execution.return_value = {"status": "RUNNING"}  # no stopDate
            le._reconcile_main_status("e1000000000000000000000000000001", main)
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
            resp = le.get_execution_logs({}, "e1000000000000000000000000000001", self._q(mode="truncated"))
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
            resp = le.get_execution_logs({}, "e1000000000000000000000000000001", self._q(mode="truncated"))
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
            resp = le.get_execution_logs({}, "e1000000000000000000000000000001", self._q(mode="truncated", pipelineExecutionId="pe-1"))
        body = json.loads(resp["body"])["message"]
        assert body["logsSource"] == "live"
        assert "step line" in body["resultLog"]
        # Live search is scoped to both the execution and the pipeline execution.
        args = fls.call_args[0]
        assert args[1] == ["e1000000000000000000000000000001", "pe-1"]

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
            resp = le.get_execution_logs({}, "e1000000000000000000000000000001", self._q(mode="truncated"))
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
            resp = le.get_execution_logs({}, "e1000000000000000000000000000001", self._q(mode="full"))
        body = json.loads(resp["body"])["message"]
        assert body["sfnHistoryEvents"][0]["message"] == "ExecutionStarted"

    def test_full_mode_reads_the_step_invocation_log_for_a_lambda_step(self):
        # The SECONDARY per-step log: the log of the Lambda the top-level state machine INVOKED. It
        # requires no registration by the pipeline (that is the point — a vamsExecute lambda never
        # registers its own log), so it must be read from what the execute path already recorded.
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:aws:logs:us-west-2:1:log-group:/g:*"}
        prow = {"pipelineExecutionId": "pe-1", "registeredLogs": [], "registeredSubExecutions": [],
                "pipelineExecutionType": "Lambda", "pipelineResourceArn": "vams-vamsExecuteConv"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main),              patch(f"{MOD}.authorize_execution_access", return_value=(True, "")),              patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]),              patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}),              patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [], "nextToken": None}),              patch(f"{MOD}._fetch_registered_log_events",
                   return_value=(True, [{"timestamp": 5, "message": "START RequestId: abc"}])) as fetch:
            resp = le.get_execution_logs(
                {}, "e1000000000000000000000000000001", self._q(mode="full", pipelineExecutionId="pe-1"))
        assert resp["statusCode"] == 200
        # Read from the DERIVED lambda log group, not from anything registered.
        read_arns = [c.args[0] for c in fetch.call_args_list]
        assert "arn:aws:logs:us-west-2:1:log-group:/aws/lambda/vams-vamsExecuteConv:*" in read_arns
        body = json.loads(resp["body"])["message"]
        assert any("START RequestId" in e.get("message", "")
                   for e in body.get("subProcessEvents", []))

    def test_full_mode_scopes_the_invocation_log_to_this_execution(self):
        # A lambda's log group is shared across every execution of that pipeline, so the read MUST be
        # filtered to this execution (and step) or one run's view leaks another's events.
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:aws:logs:us-west-2:1:log-group:/g:*"}
        prow = {"pipelineExecutionId": "pe-1", "registeredLogs": [], "registeredSubExecutions": [],
                "pipelineExecutionType": "Lambda", "pipelineResourceArn": "vams-fn"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main),              patch(f"{MOD}.authorize_execution_access", return_value=(True, "")),              patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]),              patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}),              patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [], "nextToken": None}),              patch(f"{MOD}._fetch_registered_log_events", return_value=(True, [])) as fetch:
            le.get_execution_logs({}, "e1000000000000000000000000000001", self._q(mode="full", pipelineExecutionId="pe-1"))
        call = next(c for c in fetch.call_args_list
                    if "/aws/lambda/vams-fn" in c.args[0])
        scope = call.kwargs.get("scope_terms") or []
        assert "e1000000000000000000000000000001" in scope and "pe-1" in scope

    @pytest.mark.parametrize("execution_type,resource", [
        ("SQS", "https://sqs.us-west-2.amazonaws.com/1/q"),
        ("EventBridge", "arn:aws:events:us-west-2:1:event-bus/b"),
        ("DeadlineCloud", ""),
    ])
    def test_full_mode_reads_no_invocation_log_for_types_without_one(
            self, execution_type, resource):
        # These have no reachable invocation log, so NO read is attempted — an empty section labelled
        # "no log" is worse than no section, and a doomed CloudWatch call would only add a warning.
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:aws:logs:us-west-2:1:log-group:/g:*"}
        prow = {"pipelineExecutionId": "pe-1", "registeredLogs": [], "registeredSubExecutions": [],
                "pipelineExecutionType": execution_type, "pipelineResourceArn": resource}
        with patch(f"{MOD}.get_execution_main_row", return_value=main),              patch(f"{MOD}.authorize_execution_access", return_value=(True, "")),              patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]),              patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}),              patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [], "nextToken": None}),              patch(f"{MOD}._fetch_registered_log_events", return_value=(True, [])) as fetch:
            resp = le.get_execution_logs(
                {}, "e1000000000000000000000000000001", self._q(mode="full", pipelineExecutionId="pe-1"))
        assert resp["statusCode"] == 200
        assert not [c for c in fetch.call_args_list if "/aws/lambda/" in c.args[0]]

    def test_a_denied_invocation_log_warns_instead_of_failing_the_request(self):
        # A missing IAM grant on the invoked lambda's group must degrade to a NAMED warning: the rest
        # of the logs are still useful, and a silent omission would look like "the lambda logged
        # nothing".
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:aws:logs:us-west-2:1:log-group:/g:*"}
        prow = {"pipelineExecutionId": "pe-1", "registeredLogs": [], "registeredSubExecutions": [],
                "pipelineExecutionType": "Lambda", "pipelineResourceArn": "vams-fn"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main),              patch(f"{MOD}.authorize_execution_access", return_value=(True, "")),              patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]),              patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}),              patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [], "nextToken": None}),              patch(f"{MOD}._fetch_registered_log_events",
                   return_value=(False, "AccessDeniedException")):
            resp = le.get_execution_logs(
                {}, "e1000000000000000000000000000001", self._q(mode="full", pipelineExecutionId="pe-1"))
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])["message"]
        warnings = " ".join(body.get("warnings") or [])
        assert "/aws/lambda/vams-fn" in warnings and "AccessDenied" in warnings

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
                {}, "e1000000000000000000000000000001", self._q(mode="full", pipelineExecutionId="pe-1"))
        body = json.loads(resp["body"])["message"]
        msgs = [e["message"] for e in body["subProcessEvents"]]
        assert "TaskSucceeded" in msgs  # sub-SFN history
        assert "sub log line" in msgs   # resolved sub-SFN log group
        resolve.assert_called_once_with("arn:aws:states:us-west-2:1:stateMachine:sub")
        # L1: the shared sub-SFN log group is read SCOPED to this execution + pipeline, never whole.
        _, kw = fetch.call_args
        assert kw.get("scope_terms") == ["e1000000000000000000000000000001", "pe-1"]

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
            le.get_execution_logs({}, "e1000000000000000000000000000001", self._q(mode="full", pipelineExecutionId="pe-1"))
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


@pytest.mark.unit
class TestAssetCachePerInvocation:
    """The module-level asset memo backs every ABAC decision in the authorization paths, so a warm
    container must re-read asset rows on each invocation rather than deciding on a carried-over row."""

    def _details_event(self, execution_id="e0000000000000000000000000000001"):
        return {
            "requestContext": {"http": {"method": "GET",
                                        "path": f"/workflows/executions/{execution_id}/details"},
                               "authorizer": {}},
            "pathParameters": {"executionId": execution_id},
            "queryStringParameters": {},
            "headers": {"authorization": "Bearer t"},
        }

    def test_lambda_handler_clears_the_asset_cache(self):
        le._asset_details_cache[("db1", "a1")] = {"assetId": "a1", "tags": ["public"]}
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": []}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_main_row", return_value=None):
            le.lambda_handler(self._details_event(), MagicMock())
        assert le._asset_details_cache == {}

    def test_second_invocation_authorizes_against_the_current_asset_row(self):
        # A tag-based ABAC rule allows GET only while the asset carries the 'public' tag. The row is
        # retagged between two invocations of the same warm container; the second must be denied.
        main_item = {"workflowExecutionId": "e0000000000000000000000000000001", "workflowId": "wf1", "workflowDatabaseId": "db1"}
        asset_rows = {("db1", "a1"): {"databaseId": "db1", "assetId": "a1", "tags": ["public"]}}

        def _asset_query(database_id, asset_id):
            return dict(asset_rows[(database_id, asset_id)])

        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        enforcer.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "asset" or "public" in (obj.get("tags") or []))

        statuses = []
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_main_row", return_value=main_item), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[("db1", "a1")]), \
             patch(f"{MOD}.get_asset_details", side_effect=_asset_query), \
             patch(f"{MOD}._reconcile_main_status"), \
             patch(f"{MOD}.assemble_execution_details", return_value={}):
            statuses.append(le.lambda_handler(self._details_event(), MagicMock())["statusCode"])
            asset_rows[("db1", "a1")]["tags"] = ["restricted"]
            statuses.append(le.lambda_handler(self._details_event(), MagicMock())["statusCode"])
        assert statuses == [200, 403]


@pytest.mark.unit
class TestNumericLogParams:
    """limit/startTime/endTime reach int() inside the CloudWatch/SFN readers, so a non-numeric value
    must be a 400 from the logs handler rather than an unhandled ValueError -> 500."""

    def _logs_event(self, query):
        return {
            "requestContext": {"http": {"method": "GET", "path": "/workflows/executions/E1/logs"},
                               "authorizer": {}},
            "pathParameters": {"executionId": "e1000000000000000000000000000001"},
            "queryStringParameters": query,
        }

    @pytest.mark.parametrize("query", [
        {"limit": "abc"}, {"startTime": "now"}, {"endTime": "yesterday"}])
    def test_non_numeric_returns_400(self, query):
        le.claims_and_roles = {"tokens": ["u1"]}
        resp = le.handle_logs_request(self._logs_event(query))
        assert resp["statusCode"] == 400

    def test_numeric_and_absent_pass_through(self):
        assert le._numeric_log_param_error({"limit": "50", "startTime": "1700000000"}) == ""
        assert le._numeric_log_param_error({}) == ""
        assert le._numeric_log_param_error({"limit": ""}) == ""


@pytest.mark.unit
class TestStartingTokenValidation:
    def test_undecodable_token_is_a_400(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        resp = le.get_global_executions({}, {"startingToken": "not-base64"})
        assert resp["statusCode"] == 400

    def test_decode_rejects_a_non_mapping(self):
        token = base64.b64encode(json.dumps([1, 2]).encode("utf-8")).decode("utf-8")
        assert le._decode_starting_token(token) is None

    def test_decode_returns_the_key(self):
        token = base64.b64encode(
            json.dumps({"workflowExecutionId": "e1000000000000000000000000000001"}).encode("utf-8")).decode("utf-8")
        assert le._decode_starting_token(token) == {"workflowExecutionId": "e1000000000000000000000000000001"}


@pytest.mark.unit
class TestReconcilePersistIsTargeted:
    """A read-path reconcile writes only the attributes it reconciled: the end-state lambda can be
    writing the same row, so a whole-item put would restore the pre-completion snapshot."""

    def test_update_item_names_only_reconciled_attributes(self):
        table = MagicMock()
        main = {"workflowExecutionId": "e1000000000000000000000000000001", "workflowDatabaseId:workflowId": "db:wf",
                "executionStatus": "ABORTED", "lastSfnSyncCheckDate": "2026-01-01T00:00:00Z",
                "triggeredByUserId": "u1", "executionLogGroupArn": "arn:lg"}
        le._persist_reconciled_main_row(table, main, le.DETAIL_RECONCILED_MAIN_ROW_ATTRIBUTES)
        table.put_item.assert_not_called()
        kwargs = table.update_item.call_args.kwargs
        assert set(kwargs["ExpressionAttributeNames"].values()) == {
            "executionStatus", "lastSfnSyncCheckDate"}
        assert kwargs["Key"] == {"workflowExecutionId": "e1000000000000000000000000000001",
                                 "workflowDatabaseId:workflowId": "db:wf"}

    def test_no_reconciled_attributes_writes_nothing(self):
        table = MagicMock()
        le._persist_reconciled_main_row(
            table, {"workflowExecutionId": "e1000000000000000000000000000001"}, le.DETAIL_RECONCILED_MAIN_ROW_ATTRIBUTES)
        table.update_item.assert_not_called()


@pytest.mark.unit
class TestPermanentDeleteExpiredHistory:
    def _main_row(self):
        return {"workflowExecutionId": "e1000000000000000000000000000001", "executionStatus": "RUNNING", "executionStopDate": "",
                "workflow_execution_arn": "arn:ex", "workflowDatabaseId:workflowId": "db:wf"}

    def _client_error(self, code):
        return botocore.exceptions.ClientError(
            {"Error": {"Code": code, "Message": "x"}}, "DescribeExecution")

    def test_missing_sfn_execution_allows_delete(self):
        # SFN history expiry makes describe_execution raise ExecutionDoesNotExist; a stale
        # non-terminal row must still be deletable.
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.get_execution_main_row", return_value=self._main_row()), \
             patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[]), \
             patch(f"{MOD}._delete_all_rows"), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
             patch(f"{MOD}.dynamodb") as m_dynamo, \
             patch(f"{MOD}.sfn") as m_sfn:
            m_dynamo.Table.return_value = MagicMock()
            m_sfn.describe_execution.side_effect = self._client_error("ExecutionDoesNotExist")
            resp = le.permanent_delete_execution({}, "e1000000000000000000000000000001")
        assert resp["statusCode"] == 200

    def test_other_client_error_still_guards(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.get_execution_main_row", return_value=self._main_row()), \
             patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
             patch(f"{MOD}.sfn") as m_sfn:
            m_sfn.describe_execution.side_effect = self._client_error("ThrottlingException")
            resp = le.permanent_delete_execution({}, "e1000000000000000000000000000001")
        assert resp["statusCode"] == 400
        assert "in progress" in json.loads(resp["body"])["message"].lower()


@pytest.mark.unit
class TestRerunEnforcesExecuteRoute:
    """A re-run launches a new execution through a lambdaCrossCall, which enforceAPI auto-approves,
    so the execute route's Tier-1 check has to run against the caller here."""

    def _main_row(self):
        return {"workflowExecutionId": "e1000000000000000000000000000001", "workflowId": "wf1", "workflowDatabaseId": "db1"}

    def test_execute_route_denied_blocks_rerun(self):
        le.claims_and_roles = {"tokens": ["u1"], "mfaEnabled": True}
        enforcer = MagicMock()
        enforcer.enforceAPI.side_effect = lambda ev, *a, **k: (
            "/execute" not in ev["requestContext"]["http"]["path"])
        with patch(f"{MOD}.get_execution_main_row", return_value=self._main_row()), \
             patch(f"{MOD}._execution_visible_to_caller", return_value=True), \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch.object(le, "execute_workflow_v2_function", "t-execv2"), \
             patch(f"{MOD}.lambda_client") as m_lambda:
            model = type("M", (), {"executionGroupId": None})()
            resp = le.rerun_execution({"requestContext": {"authorizer": {}}}, "e1000000000000000000000000000001", model)
        assert resp["statusCode"] == 403
        m_lambda.invoke.assert_not_called()

    def test_execute_route_allowed_proceeds(self):
        le.claims_and_roles = {"tokens": ["u1"], "mfaEnabled": True}

        class _Payload:
            def read(self):
                return json.dumps({
                    "statusCode": 200,
                    "body": json.dumps({"message": {"executionId": "e2000000000000000000000000000002"}})}).encode("utf-8")

        with patch(f"{MOD}.get_execution_main_row", return_value=self._main_row()), \
             patch(f"{MOD}._execution_visible_to_caller", return_value=True), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
             patch(f"{MOD}._reconstruct_execute_request", return_value={"inputFiles": []}), \
             patch.object(le, "execute_workflow_v2_function", "t-execv2"), \
             patch(f"{MOD}.lambda_client") as m_lambda:
            m_lambda.invoke.return_value = {"Payload": _Payload()}
            model = type("M", (), {"executionGroupId": None})()
            resp = le.rerun_execution({"requestContext": {"authorizer": {}}}, "e1000000000000000000000000000001", model)
        assert resp["statusCode"] == 200


@pytest.mark.unit
class TestListReconcileSharedLogBudget:
    """executionLog + executionError land on the same main row, so their combined size stays within
    the single-item log budget."""

    def test_log_and_error_share_the_byte_budget(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        oversized = "X" * le.er.MAX_LOG_FIELD_BYTES
        table = MagicMock()
        table.query.return_value = {"Items": []}
        with patch(f"{MOD}.get_asset_details", return_value={"databaseId": "db", "assetId": "a1"}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.dynamodb") as m_dynamo, \
             patch(f"{MOD}._fetch_execution_logs", return_value=oversized), \
             patch(f"{MOD}.build_execution_items", return_value=[]) as m_build:
            m_dynamo.Table.return_value = table
            le.get_executions({}, "db", "a1", "", "", {})
            fetcher = m_build.call_args.kwargs["fetch_execution_log_and_error"]
            error_text, log_text = fetcher(
                "e1000000000000000000000000000001", {"executionLogGroupArn": "arn:lg"}, {"error": oversized, "cause": ""})
        assert log_text and error_text
        assert (len(log_text.encode("utf-8"))
                + len(error_text.encode("utf-8"))) <= le.er.MAX_LOG_FIELD_BYTES


@pytest.mark.unit
class TestPerAssetListingIsChronological:
    """An asset's history merges two independently-queried directions, so it needs an explicit sort.

    Executions that READ the asset come from WorkflowExecInputsByAssetGSI; executions that WROTE to it
    come from WorkflowExecConfigByOutputAssetGSI. Each is newest-first on its own, but they are two
    queries whose results are concatenated — so without a sort every output-only execution trails all
    the input-side ones regardless of date. Verified live before this test existed: an asset whose only
    input-side run was from July listed it ABOVE an output-only run from today.
    """

    def _rows(self):
        # Input-side row is OLD; the output-target row is NEW. Insertion order mimics the two queries.
        return [
            {"workflowExecutionId": "e0d00000000000000000000000000001", "executionStartDate": "2026-07-17T23:41:53Z",
             "workflowId": "wf1", "workflowDatabaseId": "db"},
            {"workflowExecutionId": "e0e00000000000000000000000000002", "executionStartDate": "2026-08-02T17:32:14Z",
             "workflowId": "wf1", "workflowDatabaseId": "db"},
        ]

    def _listed_ids(self, rows):
        le.claims_and_roles = {"tokens": ["u1"]}
        table = MagicMock()
        # One page of input rows; the output-asset query returns nothing extra, so the ORDER of the
        # already-merged dict is what this exercises.
        table.query.return_value = {"Items": rows}
        with patch(f"{MOD}.get_asset_details", return_value={"databaseId": "db", "assetId": "a1"}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.dynamodb") as m_dynamo, \
             patch(f"{MOD}.build_execution_items", return_value=[]) as m_build:
            m_dynamo.Table.return_value = table
            le.get_executions({}, "db", "a1", "", "", {})
            passed = m_build.call_args.kwargs["input_items"]
        return [i["workflowExecutionId"] for i in passed]

    def test_newest_first_regardless_of_which_direction_found_it(self):
        assert self._listed_ids(self._rows()) == ["e0e00000000000000000000000000002", "e0d00000000000000000000000000001"]

    def test_order_is_independent_of_the_order_the_queries_returned(self):
        # Same set, reversed input order: the result must not change.
        assert self._listed_ids(list(reversed(self._rows()))) == ["e0e00000000000000000000000000002", "e0d00000000000000000000000000001"]

    def test_a_missing_date_sorts_last_rather_than_raising(self):
        # A row written before executionStartDate was recorded must not break the listing.
        rows = self._rows() + [{"workflowExecutionId": "e0000000000000000000000000000003", "workflowId": "wf1",
                                "workflowDatabaseId": "db"}]
        assert self._listed_ids(rows) == ["e0e00000000000000000000000000002", "e0d00000000000000000000000000001", "e0000000000000000000000000000003"]


@pytest.mark.unit
class TestDetailsInputMetadataScopes:
    """Input-metadata rows carry a scope discriminator: asset/file metadata stays under inputMetadata
    while a metadata-source database's own metadata becomes its own inputDatabaseMetadata collection
    (it belongs to no asset, so it cannot be rendered in an asset/file table)."""

    def _assemble(self, md_rows, config_row=None):
        prow = {"pipelineExecutionId": "pe1", "pipelineId": "p1", "pipelineDatabaseId": "db"}

        def _capped(table_name, key_condition, max_items):
            if table_name == le.pipeline_execution_input_metadata_table:
                return md_rows, False
            return [], False

        with patch(f"{MOD}.get_workflow_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._query_all", return_value=[]), \
             patch(f"{MOD}._query_capped", side_effect=_capped), \
             patch(f"{MOD}.get_produced_file_versions", return_value={}):
            return le.assemble_execution_details(
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"},
                config_row=config_row if config_row is not None else {})

    def test_database_scope_row_lands_in_its_own_collection(self):
        details = self._assemble([
            {"databaseId": "src-db", "assetId": "", "filePath": "/", "scope": "database",
             "metadata": {"program": "apollo"}},
            {"databaseId": "db", "assetId": "a1", "filePath": "/", "scope": "asset",
             "metadata": {"am": "1"}},
        ])
        assert details["inputDatabaseMetadata"] == [
            {"databaseId": "src-db", "assetId": "", "filePath": "/", "scope": "database",
             "metadata": {"program": "apollo"}, "attributes": {}, "pipelineId": "p1"}]
        # The asset row stays where a client already reads it, and the database row is NOT duplicated.
        assert [md["assetId"] for md in details["inputMetadata"]] == ["a1"]

    def test_rows_without_a_scope_read_as_asset_metadata(self):
        # Rows written before the discriminator existed have no scope attribute; they must group with
        # asset metadata rather than falling out of both collections.
        details = self._assemble([
            {"databaseId": "db", "assetId": "a1", "filePath": "/x.glb", "metadata": {"fm": "1"}}])
        assert details["inputDatabaseMetadata"] == []
        assert details["inputMetadata"][0]["scope"] == "asset"

    def test_dedupe_keeps_a_database_row_alongside_an_empty_id_asset_row(self):
        # The legacy-flat asset row also has an empty assetId and a '/' filePath, so a dedupe key
        # narrower than (scope, databaseId, assetId, filePath) would collapse one into the other.
        details = self._assemble([
            {"databaseId": "", "assetId": "", "filePath": "/", "scope": "asset",
             "metadata": {"legacy": "1"}},
            {"databaseId": "src-db", "assetId": "", "filePath": "/", "scope": "database",
             "metadata": {"dm": "1"}},
        ])
        assert len(details["inputMetadata"]) == 1
        assert len(details["inputDatabaseMetadata"]) == 1

    def test_metadata_sources_are_reported_from_the_configuration_row(self):
        details = self._assemble([], config_row={
            "inputMetadataDatabaseId": "src-db",
            "metadataSourceAssets": [{"databaseId": "db", "assetId": "a1"}]})
        assert details["metadataSourceDatabaseId"] == "src-db"
        assert details["metadataSourceAssets"] == [{"databaseId": "db", "assetId": "a1"}]

    def test_metadata_sources_are_empty_for_a_run_that_named_none(self):
        # Sent even when empty, so a client distinguishes "no source named" from "absent field".
        details = self._assemble([], config_row={})
        assert details["metadataSourceDatabaseId"] == ""
        assert details["metadataSourceAssets"] == []


@pytest.mark.unit
class TestDetailsInputCollectionTruncation:
    """The input collections (inputFiles, inputMetadata, inputDatabaseMetadata) are trimmed to
    MAX_DETAIL_INPUT_ROWS_RETURNED and each trimmed collection names ITSELF in truncatedCollections —
    a section is never returned partial without a flag."""

    def _assemble(self, md_rows=None, input_file_rows=None, md_read_truncated=False):
        prow = {"pipelineExecutionId": "pe1", "pipelineId": "p1", "pipelineDatabaseId": "db"}

        def _capped(table_name, key_condition, max_items):
            if table_name == le.pipeline_execution_input_metadata_table:
                return (md_rows or [])[:max_items], md_read_truncated
            if table_name == le.workflow_execution_inputs_table:
                return (input_file_rows or [])[:max_items], False
            return [], False

        with patch(f"{MOD}.get_workflow_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._query_all", return_value=[]), \
             patch(f"{MOD}._query_capped", side_effect=_capped), \
             patch(f"{MOD}.get_produced_file_versions", return_value={}):
            return le.assemble_execution_details(
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"}, config_row={})

    def _md(self, count, scope="asset", database_id="db"):
        return [{"databaseId": database_id, "assetId": f"a{i}", "filePath": f"/f{i}.glb",
                 "scope": scope, "metadata": {"k": str(i)}} for i in range(count)]

    def test_input_files_over_the_return_cap_are_trimmed_and_flagged(self):
        rows = [{"databaseId": "db", "assetId": f"a{i}", "inputAssetFileKey": f"/f{i}.glb"}
                for i in range(le.MAX_DETAIL_INPUT_ROWS_RETURNED + 5)]
        details = self._assemble(input_file_rows=rows)
        assert len(details["inputFiles"]) == le.MAX_DETAIL_INPUT_ROWS_RETURNED
        assert "inputFiles" in details["truncatedCollections"]

    def test_input_files_within_the_cap_are_complete_and_unflagged(self):
        rows = [{"databaseId": "db", "assetId": f"a{i}", "inputAssetFileKey": f"/f{i}.glb"}
                for i in range(3)]
        details = self._assemble(input_file_rows=rows)
        assert len(details["inputFiles"]) == 3
        assert details["truncatedCollections"] == []

    def test_asset_metadata_over_the_return_cap_flags_only_itself(self):
        # The trim runs after the scope split, so a trimmed asset collection does NOT drag the
        # database collection into the flag list with it.
        details = self._assemble(md_rows=self._md(le.MAX_DETAIL_INPUT_ROWS_RETURNED + 5))
        assert len(details["inputMetadata"]) == le.MAX_DETAIL_INPUT_ROWS_RETURNED
        assert details["truncatedCollections"] == ["inputMetadata"]

    def test_database_metadata_over_the_return_cap_flags_only_itself(self):
        rows = [{"databaseId": f"src-db-{i}", "assetId": "", "filePath": "/",
                 "scope": "database", "metadata": {"k": str(i)}}
                for i in range(le.MAX_DETAIL_INPUT_ROWS_RETURNED + 5)]
        details = self._assemble(md_rows=rows)
        assert len(details["inputDatabaseMetadata"]) == le.MAX_DETAIL_INPUT_ROWS_RETURNED
        assert details["truncatedCollections"] == ["inputDatabaseMetadata"]

    def test_metadata_within_the_cap_is_complete_and_unflagged(self):
        details = self._assemble(md_rows=self._md(4))
        assert len(details["inputMetadata"]) == 4
        assert details["truncatedCollections"] == []

    def test_a_read_cap_hit_flags_both_metadata_collections(self):
        # The two collections share one capped read, so a row dropped before the split has an unknown
        # scope — both are reported partial rather than implying precision that is not available.
        details = self._assemble(md_rows=self._md(2), md_read_truncated=True)
        assert details["truncatedCollections"] == ["inputDatabaseMetadata", "inputMetadata"]


@pytest.mark.unit
class TestRenderedConfigLocation:
    """The config body's Amazon S3 location is surfaced on the per-pipeline detail ONLY when the inline
    copy is truncated — the body always goes to S3 for the pipeline to read, so a truncated inline copy
    would otherwise be unreadable in full."""

    PROW = {"pipelineId": "p1", "S3AssetPipelineBucket": "run-bucket"}
    SNAPSHOT = {"inputConfigurationFileS3Key": "executions/E1/input/1/config.json"}

    def test_location_present_when_truncated(self):
        out = le._scrub_pipeline_detail(self.PROW, {"pipelineName": "P"}, "body", True, self.SNAPSHOT)
        assert out["renderedConfigTruncated"] is True
        assert out["renderedConfigLocation"] == {
            "bucket": "run-bucket", "key": "executions/E1/input/1/config.json"}
        json.dumps(out)

    def test_location_is_present_even_when_not_truncated(self):
        out = le._scrub_pipeline_detail(self.PROW, {"pipelineName": "P"}, "body", False, self.SNAPSHOT)
        # The two fields describe different STAGES of the same body: the inline renderedConfig is
        # post-user-tag / pre-system-tag, while the S3 object is the fully rendered body the step
        # ran with. A caller wanting what ran needs the pointer even when the inline copy is
        # complete, which is the common case (#101). renderedConfigTruncated carries the signal.
        assert out["renderedConfigLocation"] == {
            "bucket": "run-bucket", "key": "executions/E1/input/1/config.json"}
        assert out["renderedConfigTruncated"] is False

    def test_location_absent_when_the_row_recorded_no_key(self):
        # A run recorded before the key was stored: the flag still reports the truncation, but no
        # location is invented.
        out = le._scrub_pipeline_detail(self.PROW, {"pipelineName": "P"}, "body", True, {})
        assert out["renderedConfigTruncated"] is True
        assert "renderedConfigLocation" not in out

    def test_location_appears_on_the_assembled_details(self):
        prow = dict(self.PROW, pipelineExecutionId="pe1", pipelineDatabaseId="db")
        cfg_row = dict(self.SNAPSHOT, inputConfiguration="trimmed",
                       inputConfigurationTruncated=True)
        with patch(f"{MOD}.get_workflow_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._query_all", return_value=[cfg_row]), \
             patch(f"{MOD}._query_capped", return_value=([], False)), \
             patch(f"{MOD}.get_produced_file_versions", return_value={}):
            details = le.assemble_execution_details(
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"}, config_row={})
        assert details["pipelines"][0]["renderedConfigLocation"] == {
            "bucket": "run-bucket", "key": "executions/E1/input/1/config.json"}
        json.dumps(details)


@pytest.mark.unit
class TestMetadataSourceAuthorizationMatrix:
    """The read-path authorization rule over an execution's metadata sources. authorize_execution_access
    (details/logs/abort) and _execution_visible_to_caller (the global list) must AGREE: a row the list
    shows must not 403 when its details are opened."""

    MAIN = {"workflowId": "wf", "workflowDatabaseId": "wf-db"}

    def _denying(self, denied_type, denied_id=None):
        """An enforcer allowing everything except one object (by type, and optionally by id)."""
        def _enforce(obj, action, *a, **k):
            if obj.get("object__type") != denied_type:
                return True
            if denied_id is None:
                return False
            return denied_id not in (obj.get("databaseId", ""), obj.get("assetId", ""))
        enf = MagicMock()
        enf.enforce.side_effect = _enforce
        return enf

    def _both(self, config_row, input_assets, enforcer):
        """Run both authorization paths over one execution shape; returns (authorized, visible)."""
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=input_assets), \
             patch(f"{MOD}._get_asset_details_cached",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=config_row):
            allowed, _reason = le.authorize_execution_access("e1000000000000000000000000000001", self.MAIN, "GET")
            visible = le._execution_visible_to_caller("e1000000000000000000000000000001", self.MAIN)
        return allowed, visible

    def test_captured_database_metadata_requires_database_get(self):
        # Rule row 1: database metadata was captured -> a database GET is required, and denying it also
        # hides the execution from the list (or the list would offer a row that then 403s).
        config = {"inputMetadataDatabaseId": "src-db", "outputLocationType": "none"}
        assert self._both(config, [], self._denying("database")) == (False, False)
        assert self._both(config, [], _allow_all()) == (True, True)

    def test_database_get_is_required_even_when_an_input_asset_authorizes(self):
        # The database check is a REQUIREMENT, not an alternative: its metadata is part of what the
        # execution exposes, so asset access cannot substitute for it.
        config = {"inputMetadataDatabaseId": "src-db", "outputLocationType": "asset",
                  "outputDatabaseId": "db", "outputAssetId": "a1"}
        assert self._both(config, [("db", "a1")], self._denying("database")) == (False, False)

    def test_metadata_source_asset_requires_asset_get(self):
        # Rule row 2: no database metadata -> the assets the run read gate it, and a metadata-source
        # asset is one of them (its metadata is returned by the read paths).
        config = {"metadataSourceAssets": [{"databaseId": "db", "assetId": "src-asset"}],
                  "outputLocationType": "none"}
        assert self._both(config, [], self._denying("asset", "src-asset")) == (False, False)
        assert self._both(config, [], _allow_all()) == (True, True)

    def test_no_inputs_at_all_gates_on_the_output_asset(self):
        # Rule row 3: neither input files nor metadata sources -> the asset the run WROTE to is its only
        # data-level association, so that asset's GET decides.
        config = {"outputLocationType": "asset", "outputDatabaseId": "db", "outputAssetId": "out-a"}
        assert self._both(config, [], self._denying("asset", "out-a")) == (False, False)
        assert self._both(config, [], _allow_all()) == (True, True)

    def test_no_inputs_and_no_output_asset_rests_on_workflow_get(self):
        # Rule row 4: a results-only run with no inputs of either kind has no data entity to gate on, so
        # workflow GET alone decides — the pre-existing results-only rule, unchanged.
        config = {"outputLocationType": "none"}
        assert self._both(config, [], _allow_all()) == (True, True)
        assert self._both(config, [], self._denying("workflow")) == (False, False)

    def test_an_asset_that_is_both_an_input_and_a_source_is_checked_once(self):
        # De-duplicated: an asset named as both an input and a metadata source is enforced once.
        enf = _allow_all()
        config = {"metadataSourceAssets": [{"databaseId": "db", "assetId": "a1"}],
                  "outputLocationType": "none"}
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.CasbinEnforcer", return_value=enf), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[("db", "a1")]), \
             patch(f"{MOD}._get_asset_details_cached",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=config):
            assert le.authorize_execution_access("e1000000000000000000000000000001", self.MAIN, "GET")[0] is True
        asset_calls = [c for c in enf.enforce.call_args_list
                       if c.args[0].get("object__type") == "asset"]
        assert len(asset_calls) == 1

    def test_authorization_reuses_a_supplied_configuration_row(self):
        # The details path reads the row once and threads it through; a second read would double the
        # configuration reads of every details GET.
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_workflow_execution_configuration_row") as read_cfg:
            allowed, _reason = le.authorize_execution_access(
                "e1000000000000000000000000000001", self.MAIN, "GET", config_row={"outputLocationType": "none"})
        assert allowed is True
        read_cfg.assert_not_called()

    def test_empty_tokens_deny_both_paths(self):
        le.claims_and_roles = {"tokens": []}
        assert le.authorize_execution_access("e1000000000000000000000000000001", self.MAIN, "GET") == (False, "no tokens")
        assert le._execution_visible_to_caller("e1000000000000000000000000000001", self.MAIN) is False

    def test_the_global_list_still_reads_the_config_row_once_per_visible_row(self):
        """The whole-loop property with a metadata source present: the visibility check now needs the
        configuration row for its own checks AND the projection needs it for the output target, so it
        must still be ONE read per listed row (and none for a row discarded at workflow GET)."""
        le.claims_and_roles = {"tokens": ["u1"]}
        rows = [{"workflowExecutionId": f"e{i}" + "0" * 29 + f"{i}", "workflowId": f"wf{i}",
                 "workflowDatabaseId": "db"} for i in (1, 2)]
        table = MagicMock()
        table.query.return_value = {"Items": rows}
        enf = MagicMock()
        enf.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("workflowId") == "wf2" if obj.get("object__type") == "workflow" else True)
        enf.enforceAPI.return_value = True

        with patch(f"{MOD}.dynamodb") as mock_dynamodb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=enf), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}._get_asset_details_cached", return_value={"assetId": "a1"}), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value={"inputMetadataDatabaseId": "src-db",
                                 "outputDatabaseId": "d1", "outputAssetId": "a1",
                                 "outputLocationType": "asset"}) as read_cfg:
            mock_dynamodb.Table.return_value = table
            resp = le.get_global_executions({}, {"pageSize": "50"})

        items = json.loads(resp["body"])["message"]["Items"]
        assert [i["workflowExecutionId"] for i in items] == ["e2000000000000000000000000000002"]
        assert read_cfg.call_count == 1


@pytest.mark.unit
class TestReadAssetSpanParity:
    """Every asset a run read gates BOTH read paths. The list and the details/logs paths evaluate one
    rule, so a multi-asset run the caller can only partly read is hidden rather than listed-then-403."""

    MAIN = {"workflowId": "wf", "workflowDatabaseId": "wf-db"}

    def _both(self, config_row, input_assets, enforcer):
        """Run both authorization paths over one execution shape; returns (authorized, visible)."""
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=input_assets), \
             patch(f"{MOD}._get_asset_details_cached",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.prewarm_asset_details"), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=config_row):
            allowed, _reason = le.authorize_execution_access("e1000000000000000000000000000001", self.MAIN, "GET")
            visible = le._execution_visible_to_caller("e1000000000000000000000000000001", self.MAIN)
        return allowed, visible

    def _denying_assets(self, *denied):
        enf = MagicMock()
        enf.enforce.side_effect = lambda obj, action, *a, **k: not (
            obj.get("object__type") == "asset" and obj.get("assetId") in denied)
        return enf

    def test_all_readable_multi_asset_run_is_both_visible_and_readable(self):
        config = {"outputLocationType": "none"}
        assert self._both(config, [("db", "a1"), ("db", "a2"), ("db", "a3")],
                          _allow_all()) == (True, True)

    def test_one_denied_asset_hides_the_row_and_denies_its_details(self):
        # The two paths agree: partial asset access reaches neither the list nor the details.
        config = {"outputLocationType": "none"}
        assert self._both(config, [("db", "a1"), ("db", "a2")],
                          self._denying_assets("a2")) == (False, False)

    def test_a_readable_output_asset_does_not_substitute_for_a_denied_input_asset(self):
        # An output asset is the gate only for a run with NO inputs; it cannot rescue a run whose input
        # assets the caller cannot fully read.
        config = {"outputLocationType": "asset", "outputDatabaseId": "db", "outputAssetId": "out"}
        assert self._both(config, [("db", "a1"), ("db", "a2")],
                          self._denying_assets("a2")) == (False, False)

    def test_a_denied_metadata_source_asset_hides_a_run_whose_input_assets_are_readable(self):
        config = {"metadataSourceAssets": [{"databaseId": "db", "assetId": "src"}],
                  "outputLocationType": "none"}
        assert self._both(config, [("db", "a1")], self._denying_assets("src")) == (False, False)

    def test_a_denied_database_closes_both_paths(self):
        enf = MagicMock()
        enf.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "database")
        config = {"inputMetadataDatabaseId": "src-db", "outputLocationType": "none"}
        assert self._both(config, [("db", "a1")], enf) == (False, False)

    def test_no_inputs_with_an_output_asset_gates_on_that_asset(self):
        config = {"outputLocationType": "asset", "outputDatabaseId": "db", "outputAssetId": "out"}
        assert self._both(config, [], _allow_all()) == (True, True)
        assert self._both(config, [], self._denying_assets("out")) == (False, False)

    def test_no_inputs_and_no_output_rests_on_workflow_get(self):
        # A results-only run has no data entity to gate on, so workflow GET alone makes it readable.
        config = {"outputLocationType": "none"}
        assert self._both(config, [], _allow_all()) == (True, True)

    def test_every_asset_is_enforced_on_the_list_path(self):
        # The span check cannot short-circuit on the first pass: all three assets are enforced.
        enf = _allow_all()
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.CasbinEnforcer", return_value=enf), \
             patch(f"{MOD}.get_execution_input_assets",
                   return_value=[("db", "a1"), ("db", "a2"), ("db", "a3")]), \
             patch(f"{MOD}._get_asset_details_cached",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.prewarm_asset_details") as warm, \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value={"outputLocationType": "none"}):
            assert le._execution_visible_to_caller("e1000000000000000000000000000001", self.MAIN) is True
        checked = [c.args[0]["assetId"] for c in enf.enforce.call_args_list
                   if c.args[0].get("object__type") == "asset"]
        assert checked == ["a1", "a2", "a3"]
        # Resolved in ONE batched pre-warm, so the added enforcement costs no extra DynamoDB reads.
        assert warm.call_count == 1
        assert warm.call_args.args[0] == [("db", "a1"), ("db", "a2"), ("db", "a3")]

    def test_the_output_asset_joins_the_same_batched_prewarm(self):
        # A run's assets and its output asset resolve in one batch, so the output asset costs no read of
        # its own on a list page.
        le.claims_and_roles = {"tokens": ["u1"]}
        config = {"outputLocationType": "asset", "outputDatabaseId": "db", "outputAssetId": "out"}
        with patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[("db", "a1")]), \
             patch(f"{MOD}._get_asset_details_cached",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.prewarm_asset_details") as warm, \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=config):
            assert le._execution_visible_to_caller("e1000000000000000000000000000001", self.MAIN) is True
        assert warm.call_args.args[0] == [("db", "a1"), ("db", "out")]

    def test_a_no_input_run_prewarms_its_output_asset(self):
        # The no-inputs shape gates on the output asset, so that asset is what the pre-warm resolves.
        le.claims_and_roles = {"tokens": ["u1"]}
        config = {"outputLocationType": "asset", "outputDatabaseId": "db", "outputAssetId": "out"}
        with patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}._get_asset_details_cached",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.prewarm_asset_details") as warm, \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=config):
            assert le._execution_visible_to_caller("e1000000000000000000000000000001", self.MAIN) is True
        assert warm.call_args.args[0] == [("db", "out")]


@pytest.mark.unit
class TestPerAssetListSpanParity:
    """The per-asset listing (the asset detail page's Executions tab) evaluates the same rule the
    details path does, so a row it shows cannot 403 when it is opened. GET on the REQUESTED asset is
    not sufficient: an execution that also read another asset exposes that asset's data too."""

    def _list(self, enforcer, input_assets, config_row=None, input_rows=None):
        """Run the per-asset listing for asset (db, A); returns the listed execution ids."""
        le.claims_and_roles = {"tokens": ["u1"]}
        le._asset_details_cache.clear()
        rows = input_rows if input_rows is not None else [{
            "workflowExecutionId": "e1000000000000000000000000000001", "databaseId": "db", "assetId": "A",
            "workflowId": "wf", "workflowDatabaseId": "wf-db",
            "executionStartDate": "2026-01-01T00:00:00Z", "inputAssetFileKey": "/f.glb",
        }]
        main_row = {"workflowExecutionId": "e1000000000000000000000000000001", "workflowId": "wf", "workflowDatabaseId": "wf-db",
                    "executionStatus": "Succeeded", "executionStartDate": "2026-01-01T00:00:00Z",
                    "executionStopDate": "2026-01-01T00:01:00Z"}
        inputs_table, cfg_table, main_table = MagicMock(), MagicMock(), MagicMock()
        inputs_table.query.return_value = {"Items": rows}
        cfg_table.query.return_value = {"Items": []}
        main_table.query.return_value = {"Items": [main_row]}

        def _table(name):
            return {le.workflow_execution_inputs_table: inputs_table,
                    le.workflow_execution_database_v2: main_table,
                    le.workflow_execution_configuration_table: cfg_table}.get(name, MagicMock())

        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_asset_details",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.prewarm_asset_details"), \
             patch(f"{MOD}.get_execution_input_assets", return_value=input_assets), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value=config_row or {}), \
             patch(f"{MOD}.sfn"):
            ddb.Table.side_effect = _table
            resp = le.get_executions({}, "db", "A", "", "", {})
        return [i["workflowExecutionId"]
                for i in json.loads(resp["body"])["message"]["Items"]]

    def _denying_assets(self, *denied):
        enf = MagicMock()
        enf.enforce.side_effect = lambda obj, action, *a, **k: not (
            obj.get("object__type") == "asset" and obj.get("assetId") in denied)
        return enf

    def test_a_fully_readable_multi_asset_run_is_listed(self):
        assert self._list(_allow_all(), [("db", "A"), ("db", "B")]) == ["e1000000000000000000000000000001"]

    def test_a_run_reading_an_unreadable_second_asset_is_not_listed(self):
        # The requested asset A is readable (or the listing would 403 outright), but the run also read
        # B, which the details path requires and denies — so the row must not appear here either.
        assert self._list(self._denying_assets("B"), [("db", "A"), ("db", "B")]) == []

    def test_a_denied_metadata_source_asset_is_not_listed_either(self):
        config = {"metadataSourceAssets": [{"databaseId": "db", "assetId": "src"}]}
        assert self._list(self._denying_assets("src"), [("db", "A")], config_row=config) == []

    def test_a_captured_database_the_caller_cannot_read_is_not_listed(self):
        enf = MagicMock()
        enf.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "database")
        assert self._list(enf, [("db", "A")],
                          config_row={"inputMetadataDatabaseId": "src-db"}) == []

    def test_an_output_only_row_authorizes_on_the_main_rows_workflow(self):
        # The output-direction placeholder row carries no workflow ids, so the rule reads them off the
        # main row instead. A workflow the caller cannot GET keeps the row out of the listing.
        placeholder = [{"workflowExecutionId": "e1000000000000000000000000000001", "databaseId": "db", "assetId": "A",
                        "executionStartDate": "2026-01-01T00:00:00Z"}]
        enf = MagicMock()
        enf.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "workflow")
        assert self._list(enf, [], input_rows=placeholder) == []
        assert self._list(_allow_all(), [], input_rows=placeholder,
                          config_row={"outputLocationType": "none"}) == ["e1000000000000000000000000000001"]

    def test_the_main_row_is_read_once_for_authorization_and_the_response(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        rows = [{"workflowExecutionId": "e1000000000000000000000000000001", "databaseId": "db", "assetId": "A",
                 "executionStartDate": "2026-01-01T00:00:00Z"}]
        main_row = {"workflowExecutionId": "e1000000000000000000000000000001", "workflowId": "wf", "workflowDatabaseId": "wf-db",
                    "executionStatus": "Succeeded", "executionStartDate": "2026-01-01T00:00:00Z",
                    "executionStopDate": "2026-01-01T00:01:00Z"}
        inputs_table, cfg_table, main_table = MagicMock(), MagicMock(), MagicMock()
        inputs_table.query.return_value = {"Items": rows}
        cfg_table.query.return_value = {"Items": []}
        main_table.query.return_value = {"Items": [main_row]}

        def _table(name):
            return {le.workflow_execution_inputs_table: inputs_table,
                    le.workflow_execution_database_v2: main_table,
                    le.workflow_execution_configuration_table: cfg_table}.get(name, MagicMock())

        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_asset_details",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.prewarm_asset_details"), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value={"outputLocationType": "none"}), \
             patch(f"{MOD}.sfn"):
            ddb.Table.side_effect = _table
            le.get_executions({}, "db", "A", "", "", {})
        # The authorization pass and the response builder share one memoized main-row read.
        assert main_table.query.call_count == 1


@pytest.mark.unit
class TestRerunMetadataSources:
    """Re-run reproduces the metadata-source selection from the configuration row, and must NOT re-emit
    it as inputFiles — that would fail an arity-'none' workflow's own no-input-files rule on the new
    run."""

    def _reconstruct(self, config_row):
        with patch(f"{MOD}._query_all", return_value=[]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[]):
            return le._reconstruct_execute_request(
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"}, config_row)

    def test_sources_round_trip_in_their_own_fields(self):
        body = self._reconstruct({
            "inputMetadataDatabaseId": "src-db",
            "metadataSourceAssets": [{"databaseId": "db", "assetId": "a1"},
                                     {"databaseId": "db", "assetId": "a2"}]})
        assert body["metadataSourceDatabaseId"] == "src-db"
        assert body["metadataSourceAssets"] == [{"databaseId": "db", "assetId": "a1"},
                                                {"databaseId": "db", "assetId": "a2"}]
        # The sources are entities, not files: an arity-none re-run still sends no input files.
        assert body["inputFiles"] == []

    def test_a_run_with_no_sources_sends_empty_values(self):
        body = self._reconstruct({"outputAssetId": "a1", "outputDatabaseId": "db"})
        assert body["metadataSourceDatabaseId"] == ""
        assert body["metadataSourceAssets"] == []

    def test_legacy_configuration_rows_reconstruct_without_the_attributes(self):
        # A row written before metadata sources existed carries neither attribute.
        body = self._reconstruct({})
        assert body["metadataSourceDatabaseId"] == ""
        assert body["metadataSourceAssets"] == []

    def test_the_reconstructed_body_parses_as_an_execute_request(self):
        # The execute handler parses this body, so the source fields must satisfy its request model.
        from backend.backend.models.executions import ExecuteWorkflowRequestV2Model
        body = self._reconstruct({
            "inputMetadataDatabaseId": "src-db",
            "metadataSourceAssets": [{"databaseId": "db1", "assetId": "a1"}]})
        model = ExecuteWorkflowRequestV2Model(**body)
        assert model.metadataSourceDatabaseId == "src-db"
        assert [(s.databaseId, s.assetId) for s in model.metadataSourceAssets] == [("db1", "a1")]

    def test_a_derived_multi_database_run_replays_no_named_database(self):
        # The derived set is NOT replayed as metadataSourceDatabaseId: the new run derives the same
        # databases from the same inputFiles, and naming them would be read as an arity-'none' selection
        # (which the re-run's own arity validation then rejects, since it does send input files).
        body = self._reconstruct({
            "inputMetadataDatabaseId": "",
            "metadataSourceDatabases": ["db1", "db2", "db3"]})
        assert body["metadataSourceDatabaseId"] == ""


@pytest.mark.unit
class TestMultiDatabaseSourceAuthorization:
    """Only a NAMED metadata-source database gates the read paths; databases derived from the input
    files' assets do not. authorize_execution_access (details/logs/abort) and
    _execution_visible_to_caller (the global list) must agree, or a row lists and then 403s."""

    MAIN = {"workflowId": "wf", "workflowDatabaseId": "wf-db"}

    def _denying_databases(self, *denied):
        enf = MagicMock()
        enf.enforce.side_effect = lambda obj, action, *a, **k: not (
            obj.get("object__type") == "database" and obj.get("databaseId") in denied)
        return enf

    def _both(self, config_row, input_assets, enforcer):
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_input_assets", return_value=input_assets), \
             patch(f"{MOD}._get_asset_details_cached",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=config_row):
            allowed, _reason = le.authorize_execution_access("e1000000000000000000000000000001", self.MAIN, "GET")
            visible = le._execution_visible_to_caller("e1000000000000000000000000000001", self.MAIN)
        return allowed, visible

    def _config(self, databases):
        return {"metadataSourceDatabases": databases, "outputLocationType": "none"}

    def test_the_named_database_gates_both_read_paths(self):
        config = {"inputMetadataDatabaseId": "src-db", "outputLocationType": "none"}
        enf = _allow_all()
        assert self._both(config, [], enf) == (True, True)
        checked = [c.args[0]["databaseId"] for c in enf.enforce.call_args_list
                   if c.args[0].get("object__type") == "database"]
        # One evaluation of the named database, reused by the second path — the same caller asking the
        # same question of the same database, so it is computed once per caller.
        assert checked == ["src-db"]
        # And denying it closes both.
        assert self._both(config, [], self._denying_databases("src-db")) == (False, False)

    def test_derived_databases_do_not_gate_the_read(self):
        # Reading a run whose database metadata was DERIVED from its input files already requires GET on
        # those input assets, which is the same data. Gating additionally on their databases would
        # narrow EVERY ordinary execution to callers holding database-level GET, because
        # databaseMetadata defaults on and so every run records its input databases.
        config = {"metadataSourceDatabases": ["db1", "db2"], "outputLocationType": "none"}
        assert self._both(config, [("db1", "a1")], self._denying_databases("db1", "db2")) == (True, True)

    def test_an_asset_scoped_caller_keeps_reading_a_cross_database_run(self):
        # The recorded set is computed from the LAUNCHING identity, so gating on it would let one
        # launcher's breadth decide what every later reader may see: an admin launching over db1+db2
        # would lock out a db1-scoped collaborator who can read every asset involved.
        config = {"metadataSourceDatabases": ["db1", "db2"], "outputLocationType": "asset",
                  "outputDatabaseId": "db1", "outputAssetId": "a1"}
        assert self._both(config, [("db1", "a1"), ("db2", "a2")],
                          self._denying_databases("db1", "db2")) == (True, True)

    def test_a_named_database_still_gates_a_run_that_also_derived_others(self):
        # An explicit selection is the deliberate act that makes a database's own metadata part of the
        # run, so it gates regardless of what else the run derived.
        config = {"inputMetadataDatabaseId": "named-db",
                  "metadataSourceDatabases": ["named-db", "db2"], "outputLocationType": "none"}
        assert self._both(config, [], self._denying_databases("named-db")) == (False, False)
        assert self._both(config, [], self._denying_databases("db2")) == (True, True)

    def test_the_entities_helper_reports_only_the_named_database(self):
        databases, assets = le._metadata_source_entities(
            {"inputMetadataDatabaseId": "named-db",
             "metadataSourceDatabases": ["db2", "db1", "named-db"]})
        assert databases == ["named-db"]
        assert assets == []
        # No named selection means no database gates the read.
        assert le._metadata_source_entities({"metadataSourceDatabases": ["db1", "db2"]})[0] == []

    def test_the_details_response_reports_the_captured_databases(self):
        prow = {"pipelineExecutionId": "pe1", "pipelineId": "p1", "pipelineDatabaseId": "db"}
        with patch(f"{MOD}.get_workflow_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_definition", return_value={}), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._query_all", return_value=[]), \
             patch(f"{MOD}._query_capped", return_value=([], False)), \
             patch(f"{MOD}.get_produced_file_versions", return_value={}):
            details = le.assemble_execution_details(
                "e1000000000000000000000000000001", {"workflowId": "wf", "workflowDatabaseId": "db"},
                config_row={"metadataSourceDatabases": ["db1", "db2"]})
        assert details["metadataSourceDatabases"] == ["db1", "db2"]
        # The singular field stays, empty, for the arity-'none' selection a client re-runs from.
        assert details["metadataSourceDatabaseId"] == ""
        json.dumps(details)
