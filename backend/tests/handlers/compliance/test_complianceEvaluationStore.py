# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceEvaluationStore: one evaluation end to end against mock tables -- synchronous rules,
the pipeline-rule launch through the execute-workflow Lambda (request body per
ExecuteWorkflowRequestV2Model, `manual` trigger, SYSTEM_USER cross-call), the records it writes,
and the completion path the workflow callback drives."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, DB, REAL_TO_UPDATE_EXPR, RULES_SCHEMA_BODY, SCHEMA, SYNC_RULES_SCHEMA_BODY, USER,
    put_items, schema_row, update_values,
)
from handlers.compliance import complianceEvaluationStore as store
from models.compliance import PipelineRule
from models.executions import ExecuteWorkflowRequestV2Model

STORE = "handlers.compliance.complianceEvaluationStore"
NOTIFICATIONS = "handlers.compliance.complianceNotifications"

PIPELINE_RULE = PipelineRule(**RULES_SCHEMA_BODY["rules"]["residual-bound"])


def _lambda_client(status_code=200, execution_id="exec-1", body=None):
    client = MagicMock(name="lambda_client")
    payload = MagicMock()
    response_body = body if body is not None else {"message": {"executionId": execution_id}}
    payload.read.return_value = json.dumps(
        {"statusCode": status_code, "body": json.dumps(response_body)}).encode()
    client.invoke.return_value = {"Payload": payload}
    return client


class Tables:
    """Every table the store touches, each a MagicMock scripted with the row shapes the store reads.
    Defaults describe an asset with the `owner` metadata key, one parentChild parent, an existing
    workflow and pipeline, and no prior compliance state."""

    def __init__(self, schema_body=RULES_SCHEMA_BODY, previous_state=None, metadata_keys=("owner",),
                 parents=1, workflow_exists=True, pipeline_exists=True):
        self.schema = MagicMock(name="schema_table")
        self.schema.query.return_value = {"Items": [schema_row(body=schema_body)]}
        self.state = MagicMock(name="asset_state_table")
        self.state.get_item.return_value = (
            {"Item": {"complianceState": previous_state}} if previous_state else {})
        self.evaluation = MagicMock(name="evaluation_table")
        self.evaluation.query.return_value = {"Items": []}
        self.audit = MagicMock(name="audit_table")
        self.metadata = MagicMock(name="asset_file_metadata_table")
        self.metadata.query.return_value = {"Items": [
            {"metadataKey": key, "metadataValue": "v", "metadataValueType": "STRING"}
            for key in metadata_keys]}
        self.metadata_schema = MagicMock(name="metadata_schema_table")
        self.metadata_schema.query.return_value = {"Items": []}
        self.links = MagicMock(name="asset_links_table")
        self.links.query.side_effect = lambda **kw: (
            {"Items": [{"relationshipType": "parentChild"}] * parents}
            if kw.get("IndexName") == "toAssetGSI" else {"Items": []})
        self.workflow = MagicMock(name="workflow_table")
        self.workflow.get_item.return_value = {"Item": {"workflowId": "wf-1"}} if workflow_exists else {}
        self.pipeline = MagicMock(name="pipeline_table")
        self.pipeline.get_item.return_value = {"Item": {"pipelineId": "pipe-1"}} if pipeline_exists else {}
        self.lambda_client = _lambda_client()

    def patches(self, function_name="execute-workflow-fn"):
        return [
            patch(f"{STORE}.to_update_expr", REAL_TO_UPDATE_EXPR),
            patch(f"{STORE}.schema_table", self.schema),
            patch(f"{STORE}.asset_state_table", self.state),
            patch(f"{STORE}.evaluation_table", self.evaluation),
            patch(f"{STORE}.audit_table", self.audit),
            patch(f"{STORE}.asset_file_metadata_table", self.metadata),
            patch(f"{STORE}.metadata_schema_table", self.metadata_schema),
            patch(f"{STORE}.asset_links_table", self.links),
            patch(f"{STORE}.workflow_table", self.workflow),
            patch(f"{STORE}.pipeline_table", self.pipeline),
            patch(f"{STORE}.lambda_client", self.lambda_client),
            patch(f"{STORE}.execute_workflow_function_name", function_name),
        ]

    def run(self, callable_, *args, function_name="execute-workflow-fn", **kwargs):
        patches = self.patches(function_name)
        for p in patches:
            p.start()
        try:
            return callable_(*args, **kwargs)
        finally:
            for p in reversed(patches):
                p.stop()

    def evaluation_records(self):
        rows = put_items(self.evaluation)
        parents = [r for r in rows if r.get("recordType") is None]
        tracking = [r for r in rows if r.get("recordType") == store.PIPELINE_EXECUTION_RECORD_TYPE]
        return parents, tracking

    def audit_events(self):
        return [r["eventType"] for r in put_items(self.audit)]


@pytest.mark.unit
class TestSynchronousEvaluation:

    def test_all_rules_pass_writes_a_completed_compliant_evaluation(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY)
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "compliant"
        assert result["complianceState"] == "compliant"
        assert result["pipelineRulesPending"] == 0
        assert sorted(r["ruleName"] for r in result["ruleResults"]) == ["has-parent", "owner-present"]

        parents, tracking = tables.evaluation_records()
        assert tracking == []
        record = parents[0]
        assert record["status"] == "completed"
        assert record["verdict"] == "compliant"
        assert record["databaseId:assetId"] == f"{DB}:{ASSET}"
        assert record["completedAt"] == record["evaluatedAt"]
        assert record["actor"] == USER
        assert "executionId" not in record

        state = update_values(tables.state)[0]
        assert state["complianceState"] == "compliant"
        assert state["schemaName"] == SCHEMA
        assert state["lastEvaluationId"] == record["evaluationId"]
        assert tables.audit_events() == ["compliance_check"]

    def test_a_failed_quarantine_rule_quarantines_and_notifies(self, notifications_aws):
        notifications_aws.dynamodb_client.query.return_value = {"Items": [
            {"assetName": {"S": "Turbine"}, "snsTopic": {"S": "arn:aws:sns:us-east-1:1:topic"}}]}
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, parents=0)
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "quarantined"
        assert result["complianceState"] == "quarantined"
        parents, _ = tables.evaluation_records()
        assert parents[0]["violations"] == ["parent: found 0 links, minimum is 1"]
        assert parents[0]["actor"] == store.SYSTEM_ACTOR
        assert update_values(tables.state)[0]["complianceState"] == "quarantined"
        published = notifications_aws.sns_client.publish.call_args.kwargs
        assert published["TopicArn"] == "arn:aws:sns:us-east-1:1:topic"
        assert "QUARANTINED" in published["Subject"]
        assert "has-parent" in published["Message"]

    def test_a_failed_warn_rule_is_non_compliant_without_notification(self, notifications_aws):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, metadata_keys=())
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "non_compliant"
        assert update_values(tables.state)[0]["complianceState"] == "non_compliant"
        notifications_aws.sns_client.publish.assert_not_called()

    def test_passing_after_quarantine_audits_the_release(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY, previous_state="quarantined")
        tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert tables.audit_events() == ["compliance_check", "quarantine_released"]
        audit = put_items(tables.audit)[0]
        assert audit["previousState"] == "quarantined"
        assert audit["newState"] == "compliant"

    def test_the_metadata_schema_is_read_from_the_database_then_global(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY)
        tables.metadata_schema.query.side_effect = [
            {"Items": []},
            {"Items": [{"schemaName": "Asset Schema", "fields": json.dumps(
                [{"metadataFieldKeyName": "units", "required": True}])}]},
        ]
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        partitions = [c.kwargs["KeyConditionExpression"]._values[1]
                      for c in tables.metadata_schema.query.call_args_list]
        assert partitions == [DB, "GLOBAL"]
        metadata_result = next(r for r in result["ruleResults"] if r["ruleName"] == "owner-present")
        assert metadata_result["passed"] is False
        assert "units" in metadata_result["message"]

    def test_a_missing_schema_records_an_error_evaluation(self):
        tables = Tables()
        tables.schema.query.return_value = {"Items": []}
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["verdict"] == "error"
        assert result["error"] == "Schema not found"
        assert result["complianceState"] == "unknown"
        parents, _ = tables.evaluation_records()
        assert parents[0]["status"] == "error"
        assert parents[0]["errorMessage"] == "Schema not found"
        tables.state.update_item.assert_not_called()
        tables.audit.put_item.assert_not_called()

    def test_a_legacy_json_schema_records_an_error_evaluation(self):
        tables = Tables(schema_body={"type": "object"})
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        assert result["error"] == "Schema is not vams-rules-v1 format"

    def test_asset_metadata_is_read_from_the_asset_root_composite_key_to_exhaustion(self):
        tables = Tables(schema_body=SYNC_RULES_SCHEMA_BODY)
        tables.metadata.query.side_effect = [
            {"Items": [{"metadataKey": "a", "metadataValue": "1", "metadataValueType": "NUMBER"}],
             "LastEvaluatedKey": {"k": 1}},
            {"Items": [{"metadataKey": "owner", "metadataValue": "x", "metadataValueType": "STRING"}]},
        ]
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        first = tables.metadata.query.call_args_list[0].kwargs
        assert first["IndexName"] == "DatabaseIdAssetIdFilePathIndex"
        assert first["KeyConditionExpression"]._values[1] == f"{DB}:{ASSET}:/"
        assert tables.metadata.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {"k": 1}
        metadata_result = next(r for r in result["ruleResults"] if r["ruleName"] == "owner-present")
        assert metadata_result["measured"]["metadataKeys"] == ["a", "owner"]


@pytest.mark.unit
class TestPipelineRuleLaunch:

    def test_the_execute_request_follows_the_v2_model(self):
        rule = PipelineRule(**dict(RULES_SCHEMA_BODY["rules"]["residual-bound"],
                                   inputParameters={"expected_crs": "EPSG:27700"},
                                   pipelineRef=dict(PIPELINE_RULE.pipelineRef.dict(),
                                                    templateId="tmpl-1")))
        body = store.build_execute_workflow_request(rule, DB, ASSET, "grp-1")
        parsed = ExecuteWorkflowRequestV2Model(**body)
        assert parsed.triggerType == "manual"
        assert body["inputFiles"] == [{"databaseId": DB, "assetId": ASSET, "relativeFileKey": "/"}]
        assert body["pipelineExecutionParameters"] == {"pipe-1": {
            "templateId": "tmpl-1",
            "templateTags": [{"key": "expected_crs", "value": "EPSG:27700"}]}}
        assert body["executionGroupId"] == "grp-1"

    def test_a_rule_without_template_or_parameters_sends_an_empty_parameter_map(self):
        body = store.build_execute_workflow_request(PIPELINE_RULE, DB, ASSET)
        assert body["pipelineExecutionParameters"] == {"pipe-1": {}}
        assert "executionGroupId" not in body
        ExecuteWorkflowRequestV2Model(**body)

    def test_the_cross_call_event_targets_the_execute_route_as_system_user(self):
        event = store.build_execute_workflow_event("GLOBAL", "wf-1", {"x": 1})
        assert event["lambdaCrossCall"] == {"userName": "SYSTEM_USER"}
        assert event["requestContext"]["http"] == {"method": "POST",
                                                   "path": "/workflows/GLOBAL/wf-1/execute"}
        assert event["pathParameters"] == {"workflowDatabaseId": "GLOBAL", "workflowId": "wf-1"}
        assert json.loads(event["body"]) == {"x": 1}

    def test_a_pipeline_rule_launches_and_leaves_the_evaluation_pending(self):
        tables = Tables()
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, USER)
        assert result["verdict"] == "pending_pipeline"
        assert result["complianceState"] == "pending_evaluation"
        assert result["pipelineRulesPending"] == 1

        invoke = tables.lambda_client.invoke.call_args.kwargs
        assert invoke["FunctionName"] == "execute-workflow-fn"
        assert invoke["InvocationType"] == "RequestResponse"
        payload = json.loads(invoke["Payload"])
        assert payload["requestContext"]["http"]["path"] == "/workflows/GLOBAL/wf-1/execute"
        assert json.loads(payload["body"])["triggerType"] == "manual"

        parents, tracking = tables.evaluation_records()
        parent, track = parents[0], tracking[0]
        assert parent["status"] == "pending_pipeline"
        assert parent["executionId"] == "exec-1"
        assert parent["pipelineRuleName"] == "residual-bound"
        assert parent["pipelineExecutions"] == [
            {"ruleName": "residual-bound", "executionId": "exec-1", "status": "pending"}]
        assert "completedAt" not in parent
        assert [r["ruleName"] for r in json.loads(parent["pipelineRulesPending"])] == ["residual-bound"]
        assert track["evaluationId"] == parent["evaluationId"] + "#residual-bound"
        assert track["parentEvaluationId"] == parent["evaluationId"]
        assert track["executionId"] == "exec-1"
        assert track["status"] == "pending"
        assert "databaseId:assetId" not in track
        assert update_values(tables.state)[0]["complianceState"] == "pending_evaluation"

    @pytest.mark.parametrize("scenario", ["workflow-missing", "pipeline-missing", "refused",
                                          "no-execution-id", "unconfigured", "invoke-raises"])
    def test_a_launch_that_fails_marks_the_rule_failed_and_completes_synchronously(self, scenario):
        tables = Tables(workflow_exists=scenario != "workflow-missing",
                        pipeline_exists=scenario != "pipeline-missing")
        if scenario == "refused":
            tables.lambda_client = _lambda_client(status_code=403)
        if scenario == "no-execution-id":
            tables.lambda_client = _lambda_client(body={"message": "ok"})
        if scenario == "invoke-raises":
            tables.lambda_client.invoke.side_effect = RuntimeError("boom")
        function_name = "" if scenario == "unconfigured" else "execute-workflow-fn"

        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA, function_name=function_name)
        assert result["verdict"] == "quarantined"
        assert result["pipelineRulesPending"] == 0
        pipeline_result = next(r for r in result["ruleResults"] if r["ruleName"] == "residual-bound")
        assert pipeline_result["passed"] is False
        assert pipeline_result["message"] == "Pipeline execution could not be started"
        parents, tracking = tables.evaluation_records()
        assert tracking == []
        assert parents[0]["status"] == "completed"
        assert "pipelineRulesPending" not in parents[0]


def _pending_evaluation(rule_results=None, executions=None):
    """A stored `pending_pipeline` evaluation record for the one pipeline rule of RULES_SCHEMA_BODY,
    with the synchronous rule results already recorded."""
    return {
        "evaluationId": "eval-1",
        "databaseId": DB,
        "assetId": ASSET,
        "schemaName": SCHEMA,
        "status": "pending_pipeline",
        "ruleResults": json.dumps(rule_results if rule_results is not None else [
            {"ruleName": "has-parent", "ruleType": "relationship", "enforcement": "quarantine",
             "passed": True}]),
        "pipelineRulesPending": json.dumps([
            {"ruleName": "residual-bound", "rule": PIPELINE_RULE.dict()}]),
        "pipelineExecutions": executions or [
            {"ruleName": "residual-bound", "executionId": "exec-1", "status": "pending"}],
        "executionId": "exec-1",
        "pipelineRuleName": "residual-bound",
    }


@pytest.mark.unit
class TestResolvePipelineExecution:

    def test_a_tracking_row_resolves_to_its_parent_and_rule(self):
        tables = Tables()
        tracking = {"evaluationId": "eval-1#residual-bound", "recordType": "pipelineExecution",
                    "parentEvaluationId": "eval-1", "pipelineRuleName": "residual-bound",
                    "executionId": "exec-1"}
        tables.evaluation.query.return_value = {"Items": [tracking]}
        tables.evaluation.get_item.return_value = {"Item": _pending_evaluation()}
        parent, rule_name = tables.run(store.resolve_pipeline_execution, "exec-1")
        assert parent["evaluationId"] == "eval-1"
        assert rule_name == "residual-bound"
        query = tables.evaluation.query.call_args.kwargs
        assert query["IndexName"] == "ExecutionIdIndex"
        assert query["KeyConditionExpression"]._values[1] == "exec-1"

    def test_a_parent_row_alone_resolves_through_its_own_rule_name(self):
        tables = Tables()
        tables.evaluation.query.return_value = {"Items": [_pending_evaluation()]}
        parent, rule_name = tables.run(store.resolve_pipeline_execution, "exec-1")
        assert parent["evaluationId"] == "eval-1" and rule_name == "residual-bound"
        tables.evaluation.get_item.assert_not_called()

    def test_an_unknown_execution_is_none(self):
        tables = Tables()
        assert tables.run(store.resolve_pipeline_execution, "other") is None

    def test_a_tracking_row_whose_parent_is_gone_is_none(self):
        tables = Tables()
        tables.evaluation.query.return_value = {"Items": [
            {"recordType": "pipelineExecution", "parentEvaluationId": "eval-x",
             "pipelineRuleName": "r"}]}
        tables.evaluation.get_item.return_value = {}
        assert tables.run(store.resolve_pipeline_execution, "exec-1") is None


@pytest.mark.unit
class TestCompletePipelineRule:
    OUTPUT = {"complianceOutput": True, "status": "success", "measurements": {"residual": 0.2}}

    def test_the_last_rule_finalizes_the_evaluation_and_the_asset_state(self):
        tables = Tables(previous_state="pending_evaluation")
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                             "SUCCEEDED", self.OUTPUT, "2026-01-01T00:00:00+00:00",
                             "2026-01-01T00:01:00+00:00")
        assert outcome == {"evaluationId": "eval-1", "ruleName": "residual-bound",
                           "finalized": True, "verdict": "compliant", "complianceState": "compliant"}
        updates = update_values(tables.evaluation)
        tracking, parent = updates[0], updates[1]
        assert tracking["status"] == "completed"
        assert tracking["executionStatus"] == "SUCCEEDED"
        assert json.loads(tracking["ruleResults"])[0]["passed"] is True
        assert parent["status"] == "completed"
        assert parent["verdict"] == "compliant"
        assert parent["violations"] == []
        assert [r["ruleName"] for r in json.loads(parent["ruleResults"])] == [
            "has-parent", "residual-bound"]
        assert parent["pipelineExecutions"][0]["status"] == "completed"
        finalize = tables.evaluation.update_item.call_args_list[1].kwargs
        assert "ConditionExpression" in finalize
        assert update_values(tables.state)[0]["complianceState"] == "compliant"
        assert tables.audit_events() == ["compliance_check"]
        audit = put_items(tables.audit)[0]
        assert json.loads(audit["details"])["phase"] == "pipeline_callback"
        assert audit["actor"] == store.SYSTEM_ACTOR

    def test_a_measurement_out_of_tolerance_quarantines(self):
        tables = Tables()
        output = dict(self.OUTPUT, measurements={"residual": 5})
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                             "SUCCEEDED", output, None, None)
        assert outcome["verdict"] == "quarantined"
        assert update_values(tables.state)[0]["complianceState"] == "quarantined"

    @pytest.mark.parametrize("status", ["FAILED", "ABORTED", "TIMED_OUT"])
    def test_a_non_succeeded_execution_fails_the_rule(self, status):
        tables = Tables()
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                             status, None, None, None)
        assert outcome["finalized"] is True
        assert outcome["verdict"] == "quarantined"
        parent = update_values(tables.evaluation)[1]
        assert parent["violations"] == [f"Pipeline execution {status}"]

    def test_a_rule_that_was_not_pending_is_a_no_op(self):
        tables = Tables()
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(), "other-rule",
                             "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome == {"evaluationId": "eval-1", "ruleName": "other-rule", "finalized": False}
        tables.evaluation.update_item.assert_not_called()
        tables.state.update_item.assert_not_called()

    def test_an_outstanding_sibling_rule_defers_finalization(self):
        tables = Tables()
        evaluation = _pending_evaluation(executions=[
            {"ruleName": "residual-bound", "executionId": "exec-1", "status": "pending"},
            {"ruleName": "other", "executionId": "exec-2", "status": "pending"}])
        tables.evaluation.get_item.return_value = {"Item": {"status": "pending"}}
        outcome = tables.run(store.complete_pipeline_rule, evaluation, "residual-bound",
                             "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome["finalized"] is False
        parent = update_values(tables.evaluation)[1]
        assert "status" not in parent
        assert parent["pipelineExecutions"][0]["status"] == "completed"
        assert parent["pipelineExecutions"][1]["status"] == "pending"
        tables.state.update_item.assert_not_called()
        tables.audit.put_item.assert_not_called()

    def test_a_sibling_whose_tracking_row_already_completed_lets_this_one_finalize(self):
        tables = Tables()
        evaluation = _pending_evaluation(executions=[
            {"ruleName": "residual-bound", "executionId": "exec-1", "status": "pending"},
            {"ruleName": "other", "executionId": "exec-2", "status": "pending"}])
        tables.evaluation.get_item.return_value = {"Item": {"status": "completed"}}
        outcome = tables.run(store.complete_pipeline_rule, evaluation, "residual-bound",
                             "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome["finalized"] is True
        assert tables.evaluation.get_item.call_args.kwargs["Key"] == {"evaluationId": "eval-1#other"}

    def test_losing_the_finalize_race_leaves_the_asset_state_alone(self):
        tables = Tables()
        conditional_failure = store.dynamodb.meta.client.exceptions.ConditionalCheckFailedException(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem")
        tables.evaluation.update_item.side_effect = [{}, conditional_failure]
        outcome = tables.run(store.complete_pipeline_rule, _pending_evaluation(),
                             "residual-bound", "SUCCEEDED", self.OUTPUT, None, None)
        assert outcome["finalized"] is False
        tables.state.update_item.assert_not_called()
        tables.audit.put_item.assert_not_called()

    def test_finalizing_compliant_after_quarantine_audits_the_release(self):
        tables = Tables(previous_state="quarantined")
        tables.run(store.complete_pipeline_rule, _pending_evaluation(), "residual-bound",
                   "SUCCEEDED", self.OUTPUT, None, None)
        assert tables.audit_events() == ["compliance_check", "quarantine_released"]
