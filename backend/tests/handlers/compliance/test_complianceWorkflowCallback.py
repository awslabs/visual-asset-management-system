# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""complianceWorkflowCallback (EventBridge-invoked): consumes the `workflow.execution.completed`
event, correlates the execution to a pending evaluation through the ExecutionIdIndex, reads the
pipeline's `compliance-output.json` from the execution's recorded result rows on success, and drives
the evaluation's completion. Requires the completion detail type and a detail that parses as the
completion contract; ignores other detail types, malformed details and executions it does not own."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, DB, REAL_TO_UPDATE_EXPR, RULES_SCHEMA_BODY, SCHEMA, update_values,
)
from backend.tests.handlers.compliance.test_complianceEvaluationStore import FakeEvaluationTable
from common.workflows.executionRecords import (
    WORKFLOW_EXECUTION_COMPLETED_DETAIL_TYPE, workflow_execution_completed_event,
)
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceWorkflowCallback as callback
from models.compliance import PipelineRule

MOD = "handlers.compliance.complianceWorkflowCallback"
STORE = "handlers.compliance.complianceEvaluationStore"

# Execution ids in the shape the emitters publish (executionRecords.new_guid: 32 lowercase hex).
EXECUTION_ID = "e1000000000000000000000000000001"
UNKNOWN_EXECUTION_ID = "e9990000000000000000000000000999"
STARTED = "2026-01-01T00:00:00+00:00"
COMPLETED = "2026-01-01T00:01:00+00:00"
PIPELINE_RULE = PipelineRule(**RULES_SCHEMA_BODY["rules"]["residual-bound"])
OUTPUT = {"complianceOutput": True, "status": "success", "measurements": {"residual": 0.2}}


def completion_event(status="SUCCEEDED", execution_id=EXECUTION_ID, detail_type=None):
    """The event as EventBridge delivers the emitters' `put_events` entry: the entry's `Detail`
    JSON becomes `detail`, its `DetailType` becomes `detail-type`."""
    entry = workflow_execution_completed_event(
        "arn:aws:events:us-east-1:123456789012:event-bus/orchestration", "vams.orch",
        execution_id, "GLOBAL", "wf-1", status, STARTED, COMPLETED)
    return {
        "source": entry["Source"],
        "detail-type": detail_type or entry["DetailType"],
        "detail": json.loads(entry["Detail"]),
    }


def _pending_evaluation(status="pending_pipeline"):
    return {
        "evaluationId": "eval-1", "databaseId": DB, "assetId": ASSET, "schemaName": SCHEMA,
        "status": status,
        "ruleResults": json.dumps([]),
        "pipelineRulesPending": json.dumps([{"ruleName": "residual-bound",
                                             "rule": PIPELINE_RULE.dict()}]),
        "pipelineExecutions": [{"ruleName": "residual-bound", "executionId": EXECUTION_ID,
                                "status": "pending"}],
        "executionId": EXECUTION_ID, "pipelineRuleName": "residual-bound",
    }


def _tracking_row():
    return {"evaluationId": "eval-1#residual-bound", "recordType": "pipelineExecution",
            "parentEvaluationId": "eval-1", "pipelineRuleName": "residual-bound",
            "executionId": EXECUTION_ID, "status": "pending"}


def _result_row(content, path="/compliance-output.json", truncated=False):
    row = {"pipelineExecutionId": "pe-1", "relativeFilePath": path,
           "resultsContent": content if isinstance(content, str) else json.dumps(content)}
    if truncated:
        row["resultsContentTruncated"] = True
    return row


class Callback:
    """The tables the callback and the store read: the evaluation table (an in-memory fake honoring
    the store's status conditions) serves the ExecutionIdIndex query, the parent lookup and the
    tracking-row re-read; the pipeline-execution rows and their result rows serve the output read."""

    def __init__(self, evaluation=None, found=True, tracking=True, pipeline_rows=None,
                 result_rows=None):
        evaluation = evaluation if evaluation is not None else _pending_evaluation()
        self.evaluation_rows = FakeEvaluationTable()
        self.evaluation = self.evaluation_rows.mock
        rows = ([_tracking_row()] if tracking else [evaluation]) if found else []
        self.evaluation.query.return_value = {"Items": rows}
        if found:
            self.evaluation_rows.seed(evaluation, *([_tracking_row()] if tracking else []))
        self.state = MagicMock(name="asset_state_table")
        self.state.get_item.return_value = {"Item": {"complianceState": "pending_evaluation"}}
        self.audit = MagicMock(name="audit_table")
        self.pipeline_executions = MagicMock(name="pipeline_executions_table")
        self.pipeline_executions.query.return_value = {"Items": list(
            pipeline_rows if pipeline_rows is not None else
            [{"pipelineExecutionId": "pe-1", "pipelineId": "pipe-1", "endStatePipeline": "true"}])}
        self.results = MagicMock(name="output_results_table")
        self.results.query.return_value = {"Items": list(
            result_rows if result_rows is not None else [_result_row(OUTPUT)])}

    def run(self, event):
        with patch(f"{STORE}.to_update_expr", REAL_TO_UPDATE_EXPR), \
                patch(f"{STORE}.evaluation_table", self.evaluation), \
                patch(f"{STORE}.asset_state_table", self.state), \
                patch(f"{STORE}.audit_table", self.audit), \
                patch(f"{MOD}.pipeline_executions_table", self.pipeline_executions), \
                patch(f"{MOD}.output_results_table", self.results):
            return callback.lambda_handler(event, MagicMock())

    def finalized(self):
        return [u for u in update_values(self.evaluation) if "verdict" in u]


@pytest.mark.unit
class TestEventContract:

    def test_the_emitted_entry_is_what_the_callback_consumes(self):
        """The callback reads exactly the fields the pure event builder writes."""
        detail = completion_event()["detail"]
        assert set(detail) == {"executionId", "workflowDatabaseId", "workflowId", "status",
                               "startedAt", "completedAt"}
        assert detail["executionId"] == EXECUTION_ID
        assert callback.WORKFLOW_EXECUTION_COMPLETED_DETAIL_TYPE == WORKFLOW_EXECUTION_COMPLETED_DETAIL_TYPE
        parsed = callback.parse_completion_detail(detail)
        assert (parsed.executionId, parsed.status) == (EXECUTION_ID, "SUCCEEDED")
        assert (parsed.startedAt, parsed.completedAt) == (STARTED, COMPLETED)

    def test_another_detail_type_is_ignored(self):
        harness = Callback()
        response = harness.run(completion_event(detail_type="pipeline.execution.registered"))
        assert response == {"statusCode": 200, "body": "Ignored"}
        harness.evaluation.query.assert_not_called()

    def test_an_event_without_a_detail_type_is_skipped(self):
        harness = Callback()
        event = completion_event()
        event.pop("detail-type")
        with patch(f"{MOD}.logger") as logger:
            response = harness.run(event)
        assert response == {"statusCode": 200, "body": "Ignored"}
        harness.evaluation.query.assert_not_called()
        logger.info.assert_called_once()

    def test_a_missing_execution_id_is_a_no_op(self):
        harness = Callback()
        event = completion_event()
        event["detail"].pop("executionId")
        response = harness.run(event)
        assert response["statusCode"] == 200
        harness.evaluation.query.assert_not_called()

    @pytest.mark.parametrize("detail", [
        pytest.param("not json", id="non-json-string"),
        pytest.param(["not", "an", "object"], id="list"),
        pytest.param("[1, 2]", id="json-list-string"),
        pytest.param(42, id="number"),
        pytest.param(None, id="absent"),
    ], )
    def test_a_detail_that_is_not_an_object_is_skipped_not_raised(self, detail):
        harness = Callback()
        event = completion_event()
        event["detail"] = detail
        with patch(f"{MOD}.logger") as logger:
            response = harness.run(event)
        assert response == {"statusCode": 200, "body": "Ignored"}
        harness.evaluation.query.assert_not_called()
        logger.info.assert_called_once()
        logger.exception.assert_not_called()

    @pytest.mark.parametrize("field,value", [
        ("executionId", "someone-elses-run"),
        ("executionId", "../" + "a" * 30),
        ("status", "RUNNING"),
        ("status", "SUCCEEDED; DROP"),
        ("startedAt", "yesterday"),
        ("completedAt", "2026-01-01 00:01:00"),
    ])
    def test_a_detail_outside_the_completion_contract_is_skipped(self, field, value):
        harness = Callback()
        event = completion_event()
        event["detail"][field] = value
        with patch(f"{MOD}.logger") as logger:
            response = harness.run(event)
        assert response == {"statusCode": 200, "body": "Ignored"}
        harness.evaluation.query.assert_not_called()
        # The offending value is not echoed into the log line.
        assert value not in logger.info.call_args.args[0]

    def test_empty_optional_fields_are_accepted(self):
        """A main row with no recorded start date yields an empty startedAt."""
        harness = Callback()
        event = completion_event()
        event["detail"]["startedAt"] = ""
        event["detail"]["workflowDatabaseId"] = ""
        response = harness.run(event)
        assert json.loads(response["body"])["finalized"] is True

    def test_extra_detail_fields_are_ignored(self):
        harness = Callback()
        event = completion_event()
        event["detail"]["templateBody"] = "{...}"
        response = harness.run(event)
        assert json.loads(response["body"])["finalized"] is True

    def test_a_string_encoded_detail_is_parsed(self):
        harness = Callback()
        event = completion_event()
        event["detail"] = json.dumps(event["detail"])
        response = harness.run(event)
        assert json.loads(response["body"])["finalized"] is True

    def test_an_unknown_execution_id_is_a_no_op(self):
        harness = Callback(found=False)
        response = harness.run(completion_event(execution_id=UNKNOWN_EXECUTION_ID))
        assert response == {"statusCode": 200, "body": "Not a compliance execution"}
        query = harness.evaluation.query.call_args.kwargs
        assert query["IndexName"] == "ExecutionIdIndex"
        assert query["KeyConditionExpression"]._values[1] == UNKNOWN_EXECUTION_ID
        harness.evaluation.update_item.assert_not_called()
        harness.state.update_item.assert_not_called()

    def test_an_evaluation_that_already_completed_is_not_touched(self):
        harness = Callback(evaluation=_pending_evaluation(status="completed"))
        response = harness.run(completion_event())
        assert response == {"statusCode": 200, "body": "Already processed"}
        harness.evaluation.update_item.assert_not_called()

    def test_a_redelivered_event_records_nothing_twice(self):
        harness = Callback()
        first = json.loads(harness.run(completion_event())["body"])
        assert first["finalized"] is True
        harness.evaluation.query.return_value = {"Items": [
            dict(_tracking_row(), status="completed")]}
        second = harness.run(completion_event())
        assert second == {"statusCode": 200, "body": "Already processed"}
        assert len(harness.finalized()) == 1
        assert harness.state.update_item.call_count == 1

    def test_a_failure_inside_the_callback_answers_500(self):
        harness = Callback()
        harness.evaluation.query.side_effect = RuntimeError("table unavailable")
        response = harness.run(completion_event())
        assert response["statusCode"] == 500


@pytest.mark.unit
class TestSucceededExecution:

    def test_the_output_is_applied_and_the_evaluation_finalized(self):
        harness = Callback()
        response = harness.run(completion_event("SUCCEEDED"))
        outcome = json.loads(response["body"])
        assert outcome["finalized"] is True
        assert outcome["verdict"] == "compliant"
        assert outcome["ruleName"] == "residual-bound"

        tracking = update_values(harness.evaluation)[0]
        assert tracking["status"] == "completed"
        assert tracking["executionStatus"] == "SUCCEEDED"
        result = json.loads(tracking["ruleResults"])[0]
        assert result["passed"] is True
        assert result["measured"] == {"residual": 0.2}
        parent = harness.finalized()[0]
        assert parent["status"] == "completed"
        assert update_values(harness.state)[0]["complianceState"] == "compliant"

        pipeline_query = harness.pipeline_executions.query.call_args.kwargs
        assert pipeline_query["IndexName"] == "PipelineExecByWorkflowExecGSI"
        assert pipeline_query["KeyConditionExpression"]._values[1] == EXECUTION_ID
        results_query = harness.results.query.call_args.kwargs
        assert results_query["KeyConditionExpression"]._values[1] == "pe-1"
        # The end-state lambda wrote these rows moments before it published the event.
        assert results_query["ConsistentRead"] is True

    def test_a_measurement_out_of_tolerance_quarantines(self):
        harness = Callback(result_rows=[_result_row(dict(OUTPUT, measurements={"residual": 9}))])
        outcome = json.loads(harness.run(completion_event("SUCCEEDED"))["body"])
        assert outcome["verdict"] == "quarantined"
        assert update_values(harness.state)[0]["complianceState"] == "quarantined"

    def test_the_rules_pipeline_is_read_before_the_end_state_pipeline(self):
        other = {"pipelineExecutionId": "pe-end", "pipelineId": "end", "endStatePipeline": "true"}
        mine = {"pipelineExecutionId": "pe-1", "pipelineId": "pipe-1"}
        harness = Callback(pipeline_rows=[other, mine])
        harness.run(completion_event("SUCCEEDED"))
        read_order = [c.kwargs["KeyConditionExpression"]._values[1]
                      for c in harness.results.query.call_args_list]
        assert read_order[0] == "pe-1"

    def test_other_result_files_and_truncated_outputs_are_skipped(self):
        harness = Callback(result_rows=[
            _result_row({"complianceOutput": True, "measurements": {"residual": 99}},
                        path="/summary.json"),
            _result_row(OUTPUT, truncated=True),
            _result_row("not json"),
            _result_row(dict(OUTPUT, measurements={"residual": 0.1})),
        ])
        outcome = json.loads(harness.run(completion_event("SUCCEEDED"))["body"])
        assert outcome["verdict"] == "compliant"
        tracking = update_values(harness.evaluation)[0]
        assert json.loads(tracking["ruleResults"])[0]["measured"] == {"residual": 0.1}

    def test_no_output_document_falls_back_to_the_default_measurements(self):
        harness = Callback(result_rows=[])
        outcome = json.loads(harness.run(completion_event("SUCCEEDED"))["body"])
        # residual is not a default measurement, so the rule fails on the missing field.
        assert outcome["verdict"] == "quarantined"
        tracking = update_values(harness.evaluation)[0]
        result = json.loads(tracking["ruleResults"])[0]
        assert "output field 'residual' missing" in result["message"]
        assert result["measured"] == {"residual": None}

    def test_result_rows_are_paged_to_exhaustion(self):
        harness = Callback()
        harness.results.query.side_effect = [
            {"Items": [_result_row({"other": 1}, path="/a.json")], "LastEvaluatedKey": {"k": 1}},
            {"Items": [_result_row(OUTPUT)]},
        ]
        outcome = json.loads(harness.run(completion_event("SUCCEEDED"))["body"])
        assert outcome["verdict"] == "compliant"
        assert harness.results.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {"k": 1}


@pytest.mark.unit
class TestTerminalFailures:

    @pytest.mark.parametrize("status", ["FAILED", "ABORTED", "TIMED_OUT"])
    def test_a_non_succeeded_execution_fails_the_rule_without_reading_outputs(self, status):
        harness = Callback()
        outcome = json.loads(harness.run(completion_event(status))["body"])
        assert outcome["finalized"] is True
        assert outcome["verdict"] == "quarantined"
        harness.pipeline_executions.query.assert_not_called()
        harness.results.query.assert_not_called()
        tracking = update_values(harness.evaluation)[0]
        assert tracking["executionStatus"] == status
        assert json.loads(tracking["ruleResults"])[0]["message"] == f"Pipeline execution {status}"
        parent = harness.finalized()[0]
        assert parent["violations"] == [f"Pipeline execution {status}"]
        assert update_values(harness.state)[0]["complianceState"] == "quarantined"

    def test_a_failed_warn_rule_is_non_compliant(self):
        evaluation = _pending_evaluation()
        warn_rule = PipelineRule(**dict(RULES_SCHEMA_BODY["rules"]["residual-bound"],
                                        enforcement="warn"))
        evaluation["pipelineRulesPending"] = json.dumps(
            [{"ruleName": "residual-bound", "rule": warn_rule.dict()}])
        harness = Callback(evaluation=evaluation)
        outcome = json.loads(harness.run(completion_event("FAILED"))["body"])
        assert outcome["verdict"] == "non_compliant"


@pytest.mark.unit
class TestCorrelation:

    def test_a_parent_row_without_a_tracking_row_still_resolves(self):
        """The parent row alone resolves the rule (no parent lookup), and the completion creates the
        rule's tracking row rather than requiring one to exist."""
        harness = Callback(tracking=False)
        outcome = json.loads(harness.run(completion_event("SUCCEEDED"))["body"])
        assert outcome["finalized"] is True
        parent_lookups = [c for c in harness.evaluation.get_item.call_args_list
                          if c.kwargs["Key"] == {"evaluationId": "eval-1"}]
        assert parent_lookups == []
        assert harness.evaluation_rows.rows["eval-1#residual-bound"]["status"] == "completed"

    def test_finalization_is_recorded_by_the_system_actor(self):
        harness = Callback()
        harness.run(completion_event("SUCCEEDED"))
        audit = harness.audit.put_item.call_args.kwargs["Item"]
        assert audit["eventType"] == "compliance_check"
        assert audit["actor"] == store.SYSTEM_ACTOR
        assert audit["evaluationId"] == "eval-1"
        assert json.loads(audit["details"]) == {"verdict": "compliant",
                                                "pipelineExecutionStatus": "SUCCEEDED",
                                                "phase": "pipeline_callback"}


@pytest.mark.unit
class TestFinalizeOrdering:
    """The callback finalizes its evaluation row whatever the asset-state row says, but writes the
    state row only while its evaluation still owns it."""

    def test_a_callback_landing_after_a_newer_evaluation_leaves_the_state_row_alone(self):
        harness = Callback(evaluation=dict(_pending_evaluation(),
                                           evaluatedAt="2026-01-01T00:00:00+00:00"))
        harness.state.get_item.return_value = {"Item": {
            "complianceState": "quarantined", "lastEvaluationId": "eval-2",
            "lastEvaluatedAt": "2026-01-01T00:00:30+00:00", "lastEvaluationStatus": "completed"}}
        outcome = json.loads(harness.run(completion_event("SUCCEEDED"))["body"])
        assert outcome["finalized"] is True
        assert outcome["verdict"] == "compliant"
        assert harness.evaluation_rows.rows["eval-1"]["status"] == "completed"
        harness.state.update_item.assert_not_called()
        harness.audit.put_item.assert_not_called()

    def test_the_rows_own_evaluation_writes_its_start_as_the_last_evaluated_at(self):
        harness = Callback(evaluation=dict(_pending_evaluation(),
                                           evaluatedAt="2026-01-01T00:00:00+00:00"))
        harness.state.get_item.return_value = {"Item": {
            "complianceState": "pending_evaluation", "lastEvaluationId": "eval-1",
            "lastEvaluatedAt": "2026-01-01T00:00:00+00:00",
            "lastEvaluationStatus": "pending_pipeline"}}
        harness.run(completion_event("SUCCEEDED"))
        state = update_values(harness.state)[0]
        assert state["complianceState"] == "compliant"
        assert state["lastEvaluationId"] == "eval-1"
        assert state["lastEvaluatedAt"] == "2026-01-01T00:00:00+00:00"
        assert state["lastEvaluationStatus"] == "completed"
        assert harness.evaluation_rows.rows["eval-1"]["hasRuleErrors"] is False
