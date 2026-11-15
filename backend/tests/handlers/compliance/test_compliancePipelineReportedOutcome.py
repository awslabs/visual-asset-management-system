# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A compliance pipeline rule over the outcome the workflow end-state step records.

The end-state step (`processWorkflowExecutionOutput`) runs for real here: it ingests what the pipeline
staged, reads the reserved results object `execution.status.json`, records the outputs and the terminal
status, and publishes `workflow.execution.completed`. The published entry is delivered, as EventBridge
delivers it, to the compliance workflow callback, which reads the result rows and the `executionError`
the step just wrote. Covered:

- a pipeline that writes a passing `compliance-output.json` and its metadata, then reports FAILED:
  the event carries FAILED, the rule fails with the step's `<error>: <cause>` as its failure reason,
  the passing output is not read, and the metadata write-back stays attributed to the execution (so
  the compliance trigger starts no evaluation on it). The same staging without the status object is
  the positive control: SUCCEEDED, and the output decides the rule;
- the failure reason reaches only a caller allowed to read the execution;
- a status object the step cannot use fails the rule with the step's generic message;
- refused write-backs whose messages grow `executionError` past the rule-result bound leave a
  bounded reason, headed by the reported failure;
- a pipeline rule whose workflow is archived or disabled: the execute handler's own gate refuses the
  launch, so the rule is recorded at once as an error, with no execution to wait for.
"""

import io
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# The values every suite that imports these handlers seeds (see conftest.py).
for _key, _value in {
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    "FILE_UPLOAD_LAMBDA_FUNCTION_NAME": "t-upload",
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "ASSET_UPLOAD_TABLE_NAME": "t-asset-upload",
    "DATABASE_STORAGE_TABLE_NAME": "t-db",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME": "t-pin-files",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_STORAGE_TABLE_NAME": "t-workflows",
    "PIPELINE_STORAGE_TABLE_NAME": "t-pipelines",
    "EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME": "t-execv2",
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "PIPELINE_STORAGE_TABLE_V2_NAME": "t-pipe-v2",
    "PIPELINE_TEMPLATES_STORAGE_TABLE_NAME": "t-templates",
    "PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME": "t-tagschema",
    "S3_ASSETAUXILIARY_STORAGE_BUCKET": "t-aux",
}.items():
    os.environ.setdefault(_key, _value)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.common.s3PathPatterns import EXECUTION_STATUS_RESULTS_FILENAME  # noqa: E402
from backend.backend.handlers.workflows import executeWorkflow as ew  # noqa: E402
from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402
from backend.tests.handlers.compliance._harness import (  # noqa: E402
    ASSET, DB, RULES_SCHEMA_BODY, SCHEMA, update_values,
)
from backend.tests.handlers.compliance.test_complianceEvaluationStore import Tables  # noqa: E402
from backend.tests.handlers.compliance.test_complianceWorkflowCallback import (  # noqa: E402
    EXECUTION_ID, OUTPUT, Callback,
)
from common.compliance import evaluationEngine as engine  # noqa: E402
from common.compliance import executionFailureAccess as failure_access  # noqa: E402
from handlers.compliance import complianceEvaluationStore as store  # noqa: E402

BUS = "arn:aws:events:us-east-1:123456789012:event-bus/vams-orchestration"
PREFIX = "vams.orchestration"
STARTED = "2026-01-01T00:00:00+00:00"
EVALUATION_ID = "eval-1"
RUN_BUCKET = "run-io-bucket"
METADATA_PREFIX = "pipelines/p1/JOB/output/E1/metadata/"
RESULTS_PREFIX = "pipelines/p1/JOB/output/E1/results/"
STATUS_KEY = RESULTS_PREFIX + EXECUTION_STATUS_RESULTS_FILENAME
COMPLIANCE_OUTPUT_KEY = RESULTS_PREFIX + engine.COMPLIANCE_OUTPUT_FILE_NAME
ATTRIBUTE_KEY = METADATA_PREFIX + "model.stl.attribute.json"

REPORTED = {"status": "FAILED", "error": "MeshCheckFailed",
            "cause": "the scan has 3 open boundaries"}
REPORTED_ERROR = "MeshCheckFailed: the scan has 3 open boundaries"
GENERIC = "Pipeline execution FAILED"


def _staged(status=None, attribute_files=1):
    """The run bucket after the pipeline wrote `attribute_files` attribute files and a PASSING
    compliance output (OUTPUT's residual is inside the rule's tolerance), plus an optional status
    object (a dict to serialize, or raw bytes)."""
    objects = {COMPLIANCE_OUTPUT_KEY: json.dumps(OUTPUT).encode("utf-8")}
    for index in range(attribute_files):
        key = ATTRIBUTE_KEY if index == 0 else f"{METADATA_PREFIX}part{index}.stl.attribute.json"
        objects[key] = json.dumps({"type": "attribute", "metadata": [
            {"metadataKey": "inspected", "metadataValue": "true"}]}).encode("utf-8")
    if status is not None:
        objects[STATUS_KEY] = status if isinstance(status, bytes) else json.dumps(status).encode("utf-8")
    return objects


def _s3_stub(objects):
    """An S3 client whose get_object serves `objects` ({key: bytes}), honouring a Range read."""
    def _get_object(Bucket, Key, **kwargs):
        body = objects[Key]
        if kwargs.get("Range"):
            first, last = kwargs["Range"][len("bytes="):].split("-")
            body = body[int(first):int(last) + 1]
        return {"Body": io.BytesIO(body)}
    stub = MagicMock()
    stub.get_object.side_effect = _get_object
    return stub


def _listing(objects, prefix):
    contents = [{"Key": key, "Size": len(body)}
                for key, body in objects.items() if key.startswith(prefix)]
    return {"Contents": contents} if contents else {}


class _Table:
    """A DynamoDB table that keeps what the step writes: batched and single puts, and updates."""

    def __init__(self, attributes=None):
        self.items = []
        self.updates = []
        self._attributes = attributes

    def batch_writer(self, **_kwargs):
        table = self

        class _Batch:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def put_item(self, Item):
                table.items.append(Item)
        return _Batch()

    def put_item(self, Item, **_kwargs):
        self.items.append(Item)

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        return {"Attributes": dict(self._attributes or {})}

    def query(self, **_kwargs):
        return {"Items": []}


class _Dynamo:
    """The DynamoDB resource the step writes through. The main execution row answers its terminal
    write with the row as stored at launch: its start date and the evaluation as execution group."""

    def __init__(self):
        self.tables = {}

    def Table(self, name):
        if name not in self.tables:
            attributes = ({"executionStartDate": STARTED, "executionGroupId": EVALUATION_ID}
                          if name == po.workflow_execution_database_v2 else None)
            self.tables[name] = _Table(attributes)
        return self.tables[name]


class EndState:
    """One run of the end-state step over staged objects, and what it recorded and published."""

    def __init__(self, objects, refusals=None):
        self.metadata_calls = []
        refusals = list(refusals or [])
        dynamo = _Dynamo()
        events = MagicMock(name="events_client")

        def metadata_service(payload):
            self.metadata_calls.append(payload)
            if refusals:
                response = {"statusCode": 400, "body": json.dumps({"message": refusals.pop(0)})}
            else:
                response = {"statusCode": 200, "body": json.dumps({"message": "updated"})}
            return {"Payload": io.BytesIO(json.dumps(response).encode("utf-8"))}

        event = {"body": {
            "outputAssetId": "asset1", "outputDatabaseId": "db1",
            "workflowExecutionId": EXECUTION_ID, "endStatePipelineExecutionId": "pe-1",
            "workflowDatabaseId": "GLOBAL", "workflowId": "wf-1",
            "workflowExecutionS3InputOutputBucket": RUN_BUCKET,
            "executingUserName": "SYSTEM_USER", "executingRequestContext": {"http": {}},
            "metadataPathKey": METADATA_PREFIX, "resultsPathKey": RESULTS_PREFIX,
        }}
        with patch.object(po, "lookup_existing_asset",
                          return_value={"databaseId": "db1", "assetId": "asset1", "bucketId": "b1"}), \
                patch.object(po, "get_default_bucket_details",
                             return_value={"bucketId": "b1", "bucketName": "asset-bucket",
                                           "baseAssetsPrefix": "assets/"}), \
                patch.object(po, "_fetch_execution_logs", return_value=("", "")), \
                patch.object(po, "verify_get_path_objects",
                             side_effect=lambda _bucket, prefix: _listing(objects, prefix)), \
                patch.object(po, "s3c", _s3_stub(objects)), \
                patch.object(po, "_lambda_metadata_service", side_effect=metadata_service), \
                patch.object(po, "dynamodb", dynamo), \
                patch.object(po, "events_client", events), \
                patch.object(po, "orchestration_bus_arn", BUS), \
                patch.object(po, "orchestration_event_source_prefix", PREFIX), \
                patch.object(po.eo, "set_pipeline_status"):
            response = po.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 200

        main_writes = dynamo.Table(po.workflow_execution_database_v2).updates
        assert len(main_writes) == 1
        self.main_values = main_writes[0]["ExpressionAttributeValues"]
        self.result_rows = list(dynamo.Table(po.pipeline_execution_output_results_table).items)
        events.put_events.assert_called_once()
        entries = events.put_events.call_args.kwargs["Entries"]
        assert len(entries) == 1
        self.entry = entries[0]

    @property
    def status(self):
        return self.main_values[":st"]

    @property
    def execution_error(self):
        return self.main_values.get(":er", "")

    def delivered_event(self):
        """The entry as EventBridge delivers it to the callback's rule target."""
        return {"source": self.entry["Source"], "detail-type": self.entry["DetailType"],
                "detail": json.loads(self.entry["Detail"])}


def _deliver(end_state):
    """Deliver the step's completion event to the compliance callback, over the result rows and the
    main-row `executionError` the step recorded. Returns (callback harness, outcome)."""
    harness = Callback(result_rows=end_state.result_rows,
                       execution_error=end_state.execution_error)
    outcome = json.loads(harness.run(end_state.delivered_event())["body"])
    return harness, outcome


def _rule_result(harness):
    return json.loads(update_values(harness.evaluation)[0]["ruleResults"])[0]


@pytest.mark.unit
class TestOutputsThenAReportedFailure:

    def test_the_positive_control_without_a_status_object_lets_the_output_decide(self):
        end_state = EndState(_staged())
        assert end_state.status == "SUCCEEDED"
        assert end_state.delivered_event()["detail"]["status"] == "SUCCEEDED"
        harness, outcome = _deliver(end_state)
        assert outcome["verdict"] == "compliant"
        assert _rule_result(harness)["passed"] is True

    def test_the_rule_fails_with_the_reported_error_and_the_passing_output_is_not_read(self):
        end_state = EndState(_staged(status=REPORTED))
        assert end_state.status == "FAILED"
        assert end_state.execution_error == REPORTED_ERROR
        detail = end_state.delivered_event()["detail"]
        assert (detail["executionId"], detail["status"]) == (EXECUTION_ID, "FAILED")
        assert detail["executionGroupId"] == EVALUATION_ID
        # The passing output and the status object were both recorded as results rows.
        recorded = {row["relativeFilePath"] for row in end_state.result_rows}
        assert {"/" + engine.COMPLIANCE_OUTPUT_FILE_NAME,
                "/" + EXECUTION_STATUS_RESULTS_FILENAME} <= recorded

        harness, outcome = _deliver(end_state)
        assert outcome["verdict"] == "quarantined"
        result = _rule_result(harness)
        assert result["passed"] is False
        assert result["message"] == GENERIC
        assert result["executionId"] == EXECUTION_ID
        assert result["executionFailureReason"] == REPORTED_ERROR
        harness.results.query.assert_not_called()
        state = update_values(harness.state)[0]
        assert state["complianceState"] == "quarantined"
        assert state["quarantineExecutionFailures"] == [{
            "ruleName": "residual-bound", "executionId": EXECUTION_ID,
            "executionFailureReason": REPORTED_ERROR}]

    def test_the_metadata_written_before_the_failure_stays_attributed_to_the_execution(self):
        """The write-back lands before the status object is read, and its cross-call names the
        execution, so the metadata service records the row as the execution's and the compliance
        trigger starts no evaluation on it."""
        end_state = EndState(_staged(status=REPORTED))
        assert len(end_state.metadata_calls) == 1
        cross_call = end_state.metadata_calls[0]["lambdaCrossCall"]
        assert cross_call == {"userName": "SYSTEM_USER", "workflowExecutionId": EXECUTION_ID}


@pytest.mark.unit
class TestTheReasonReachesOnlyAnExecutionReader:

    def _stored(self):
        harness, _ = _deliver(EndState(_staged(status=REPORTED)))
        return (update_values(harness.evaluation)[0]["ruleResults"],
                update_values(harness.state)[0])

    @pytest.mark.parametrize("allowed", [True, False], ids=["execution-reader", "other-reader"])
    def test_the_rule_result_and_the_quarantine_record(self, allowed):
        rule_results, state = self._stored()
        scope = failure_access.new_scope(MagicMock(name="casbin_enforcer"))
        with patch.object(failure_access, "_decide", return_value=allowed):
            results = failure_access.rule_results_for_caller(scope, rule_results)
            record = failure_access.state_record_for_caller(scope, state)
        result = json.loads(results)[0] if isinstance(results, str) else results[0]
        entry = record["quarantineExecutionFailures"][0]
        assert result["message"] == GENERIC
        assert result["executionId"] == entry["executionId"] == EXECUTION_ID
        if allowed:
            assert result["executionFailureReason"] == REPORTED_ERROR
            assert entry["executionFailureReason"] == REPORTED_ERROR
        else:
            assert "executionFailureReason" not in result
            assert "executionFailureReason" not in entry


@pytest.mark.unit
class TestAStatusObjectTheStepCannotUse:

    @pytest.mark.parametrize("raw,message", [
        (json.dumps({"status": "DONE"}).encode("utf-8"), po.EXECUTION_STATUS_FILE_INVALID),
        (b"{not json", po.EXECUTION_STATUS_FILE_UNREADABLE),
    ], ids=["invalid", "unreadable"])
    def test_the_rule_fails_with_the_steps_message(self, raw, message):
        end_state = EndState(_staged(status=raw))
        assert end_state.status == "FAILED"
        assert end_state.execution_error == message
        harness, outcome = _deliver(end_state)
        assert outcome["verdict"] == "quarantined"
        result = _rule_result(harness)
        assert result["message"] == GENERIC
        assert result["executionFailureReason"] == message
        harness.results.query.assert_not_called()


@pytest.mark.unit
class TestRefusedWriteBacksStayWithinTheReasonBound:

    def test_a_long_execution_error_leaves_a_bounded_reason_headed_by_the_reported_failure(self):
        refusals = [f"value {index} of 'inspected' does not match its declared type " + "x" * 2000
                    for index in range(12)]
        end_state = EndState(_staged(status=REPORTED, attribute_files=12), refusals=refusals)
        assert end_state.status == "FAILED"
        error_bytes = len(end_state.execution_error.encode("utf-8"))
        assert engine.MAX_EXECUTION_FAILURE_REASON_LENGTH < error_bytes <= po.MAX_EXECUTION_ERROR_BYTES
        assert end_state.execution_error.startswith(REPORTED_ERROR)

        harness, outcome = _deliver(end_state)
        assert outcome["verdict"] == "quarantined"
        reason = _rule_result(harness)["executionFailureReason"]
        assert len(reason) == engine.MAX_EXECUTION_FAILURE_REASON_LENGTH
        assert reason.startswith(REPORTED_ERROR)
        assert reason.endswith("\u2026")
        entry = update_values(harness.state)[0]["quarantineExecutionFailures"][0]
        assert entry["executionFailureReason"] == reason


def _execute_handler_client(workflow_row):
    """A Lambda client whose execute-workflow invoke runs the execute handler's own workflow gate
    over `workflow_row` (the row the evaluation store read), for a caller the gate authorizes."""
    def invoke(FunctionName, InvocationType, Payload):
        event = json.loads(Payload)
        params = event["pathParameters"]
        with patch.object(ew, "_get_workflow", return_value=dict(workflow_row)), \
                patch.object(ew, "_enforce", return_value=True):
            response = ew.execute_workflow(
                event, params["workflowDatabaseId"], params["workflowId"], None)
        payload = MagicMock()
        payload.read.return_value = json.dumps(response).encode("utf-8")
        return {"Payload": payload}
    client = MagicMock(name="lambda_client")
    client.invoke.side_effect = invoke
    return client


PIPELINE_ONLY_SCHEMA_BODY = {
    "schemaFormat": RULES_SCHEMA_BODY["schemaFormat"],
    "rules": {"residual-bound": RULES_SCHEMA_BODY["rules"]["residual-bound"]},
}


@pytest.mark.unit
class TestAPipelineRuleWhoseWorkflowCannotRun:

    @pytest.mark.parametrize("workflow_row", [
        {"workflowId": "wf-1", "archived": True, "workflow_arn": "arn:aws:states:::wf-1"},
        {"workflowId": "wf-1", "enabled": False, "workflow_arn": "arn:aws:states:::wf-1"},
    ], ids=["archived", "disabled"])
    @pytest.mark.parametrize("schema_body,verdict", [
        (RULES_SCHEMA_BODY, "compliant"),
        (PIPELINE_ONLY_SCHEMA_BODY, "error"),
    ], ids=["with-other-rules", "pipeline-rule-only"])
    def test_the_rule_is_an_error_at_once_and_nothing_waits(self, workflow_row, schema_body,
                                                             verdict):
        tables = Tables(schema_body=schema_body)
        tables.workflow.get_item.return_value = {"Item": dict(workflow_row)}
        tables.lambda_client = _execute_handler_client(workflow_row)
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)

        tables.lambda_client.invoke.assert_called_once()
        rule = next(r for r in result["ruleResults"] if r["ruleName"] == "residual-bound")
        assert (rule["passed"], rule["status"]) == (False, "error")
        assert rule["message"] == store.LAUNCH_FAILED_MESSAGE
        assert result["pipelineRulesPending"] == 0
        assert result["errorRules"] == ["residual-bound"]
        assert result["verdict"] == verdict
        parents, tracking = tables.evaluation_records()
        assert tracking == []
        assert parents[0]["status"] != engine.EVALUATION_STATUS_PENDING_PIPELINE
