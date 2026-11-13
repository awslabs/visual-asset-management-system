# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compliance pipeline rules under the workflow concurrency locks.

A workflow whose `concurrencyRestriction` is `perAsset`, `perInputFile` or `perInputFileVersion`
locks the asset, the input files or the input file versions a run selects, and a launch whose scope
another run holds is answered 400 without starting. Compliance launches its pipeline rules through
the same execute handler, so its runs take and release the same locks. Here compliance's real launch
(`store.run_evaluation`) drives the real execute handler over a lock table that keeps its rows the
way DynamoDB does: a put lands only while the key is free, a delete only for its holder, and the
holder index lists an execution's rows. Covered:

- a launch refused because another run holds the lock, for each restriction kind: the rule is not
  an error and does not pass; it waits (a `waiting` tracking row keeping the launch request, the
  evaluation `pending_pipeline`, the asset `pending_evaluation`), and the holder keeps its lock.
  Whatever the holder is (another user's run, the asset's earlier evaluation's run, or a second rule
  of the schema naming the same workflow), the rule launches once the holder ends
  (`test_compliancePipelineRuleWaits.py`, D113); any other refusal is still an error at once;
- a compliance-launched run releases its locks on every terminal path, and its completion event
  still reaches the compliance callback, once: the end-state step (succeeded, and failed through the
  reserved status object), the error handler, the abort route, and a stop made outside VAMS that the
  details-view reconcile finds (it releases the locks whether or not its own status write landed,
  before it announces, and announces only the status it wrote). A launch that fails after taking its
  locks releases them, publishes nothing (nothing ran) and leaves the rule an error;
- a pipeline rule naming a system workflow (`isSystem`, registered by the deployment's `vamsSchema`
  import) launches it like any other workflow.
"""

import contextlib
import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.workflows import test_execute_per_input_file_version_lock as launch
from backend.tests.handlers.compliance import test_compliancePipelineReportedOutcome as reported
from backend.tests.handlers.compliance._harness import (  # noqa: E402
    ASSET, DB, RULES_SCHEMA_BODY, SCHEMA, update_values,
)
from backend.tests.handlers.compliance.test_complianceEvaluationStore import Tables
from backend.tests.handlers.compliance.test_complianceWorkflowCallback import (
    EXECUTION_ID, Callback,
)
from backend.backend.handlers.workflows import executionService as le
from backend.backend.handlers.workflows.sfn import handleExecutionError as heh
from common.compliance import evaluationEngine as engine
from handlers.compliance import complianceEvaluationStore as store

el = launch.ewv2.el
SYSTEM = "SYSTEM_USER"
OTHER_RUN = "e7770000000000000000000000000777"
BUS = "arn:aws:events:us-east-1:123456789012:event-bus/vams-orchestration"
PREFIX = "vams.orchestration"
RESTRICTIONS = ["perAsset", "perInputFile", "perInputFileVersion"]
PIPELINE_ONLY_SCHEMA_BODY = reported.PIPELINE_ONLY_SCHEMA_BODY


def _condition_failure(operation, item=None):
    response = {"Error": {"Code": "ConditionalCheckFailedException", "Message": "condition"}}
    if item is not None:
        response["Item"] = item
    return ClientError(response, operation)


class Locks:
    """The workflow execution locks table as DynamoDB keeps it. A put lands while the key has no
    live row (or as the takeover of the same holder and acquiredAt), answering a refusal with the
    live row (ALL_OLD, wire format); a delete lands only for the row's holder; the holder index
    lists an execution's rows. `journal` records each lock, release and (when a test appends to it)
    publish in order."""

    def __init__(self, held=None):
        now = int(time.time())
        self.rows = {key: {"lockKey": key, "workflowExecutionId": holder,
                           "lockScope": el._scope_of_key(key), "acquiredAt": el._iso(now),
                           "expiresAt": now + 86400}
                     for key, holder in (held or {}).items()}
        self.journal = []
        self.puts, self.deletes = [], []

    def holder(self, key):
        return (self.rows.get(key) or {}).get("workflowExecutionId")

    def keys_of(self, execution_id):
        return sorted(k for k, row in self.rows.items() if row["workflowExecutionId"] == execution_id)

    def put_item(self, Item, ExpressionAttributeValues=None, **_):
        key = Item["lockKey"]
        self.puts.append(dict(Item))
        self.journal.append(("lock", key))
        values = ExpressionAttributeValues or {}
        current = self.rows.get(key)
        live = current is not None and current["expiresAt"] >= values.get(":now", 0)
        takeover = live and (values.get(":holder"), values.get(":acquiredAt")) == (
            current["workflowExecutionId"], current["acquiredAt"])
        if live and not takeover:
            raise _condition_failure("PutItem", {
                "workflowExecutionId": {"S": current["workflowExecutionId"]},
                "acquiredAt": {"S": current["acquiredAt"]}})
        self.rows[key] = dict(Item)
        return {}

    def delete_item(self, Key, ExpressionAttributeValues=None, **_):
        key = Key["lockKey"]
        self.deletes.append(key)
        self.journal.append(("release", key))
        current = self.rows.get(key)
        if current is None or current["workflowExecutionId"] != (ExpressionAttributeValues or {}).get(":e"):
            raise _condition_failure("DeleteItem")
        del self.rows[key]
        return {}

    def query(self, IndexName=None, KeyConditionExpression=None, **_):
        assert IndexName == el.HOLDER_INDEX_NAME
        holder = KeyConditionExpression.get_expression()["values"][1]
        return {"Items": [{"lockKey": key, "workflowExecutionId": holder}
                          for key in self.keys_of(holder)]}


# --- compliance's launch through the real execute handler ---


def _execute_client(locks, restriction, version="v1", start_error=None, system_workflow=False):
    """A Lambda client whose execute-workflow invoke runs the execute handler's lambda_handler on
    the event compliance sends, over `locks` (the launch stubs of the execute handler's own lock
    tests: the workflow under `restriction`, its pipeline, the asset, and every input present at
    `version`). `responses` keeps each answer."""
    responses = []
    workflow = launch._workflow

    def _workflow(r):
        row = workflow(r)
        if system_workflow:
            row["isSystem"] = True
        return row

    def invoke(FunctionName, InvocationType, Payload):
        with patch.object(launch, "_workflow", _workflow):
            response, _sfn, _log, _stop = launch._run(
                locks, restriction=restriction, event=json.loads(Payload), tokens=(SYSTEM,),
                resolved_version=version, start_error=start_error)
        responses.append(response)
        payload = MagicMock()
        payload.read.return_value = json.dumps(response).encode("utf-8")
        return {"Payload": payload}

    client = MagicMock(name="lambda_client")
    client.invoke.side_effect = invoke
    client.responses = responses
    return client


def _evaluate(locks, restriction, schema_body=RULES_SCHEMA_BODY, **client_options):
    tables = Tables(schema_body=schema_body)
    tables.lambda_client = _execute_client(locks, restriction, **client_options)
    return tables, tables.run(store.run_evaluation, DB, ASSET, SCHEMA)


def _launch_keys(restriction):
    """The lock keys a compliance launch of the pipeline rule takes under `restriction`."""
    locks = Locks()
    tables, result = _evaluate(locks, restriction)
    assert result["pipelineRulesPending"] == 1
    (execution_id,) = {row["workflowExecutionId"] for row in locks.rows.values()}
    return locks.keys_of(execution_id)


def _rule(result):
    return next(r for r in result["ruleResults"] if r["ruleName"] == "residual-bound")


@pytest.mark.unit
class TestALaunchTakesTheWorkflowsLock:

    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_the_rule_starts_and_its_run_holds_the_lock(self, restriction):
        locks = Locks()
        tables, result = _evaluate(locks, restriction)
        assert tables.lambda_client.responses[0]["statusCode"] == 200
        execution_id = json.loads(tables.lambda_client.responses[0]["body"])["message"]["executionId"]
        assert result["pipelineRulesPending"] == 1
        _parents, tracking = tables.evaluation_records()
        assert [row["executionId"] for row in tracking] == [execution_id]
        assert len(locks.keys_of(execution_id)) == 1
        scope = el.lock_scope_for_restriction(restriction)
        assert locks.rows[locks.keys_of(execution_id)[0]]["lockScope"] == scope

    def test_the_none_restriction_takes_no_lock(self):
        locks = Locks()
        _tables, result = _evaluate(locks, "none")
        assert result["pipelineRulesPending"] == 1
        assert locks.rows == {} and locks.puts == []


@pytest.mark.unit
class TestALaunchAnotherRunHoldsWaits:
    """A launch the lock refuses is not a tooling error (D113): the rule waits for the holder to
    end, the evaluation stays `pending_pipeline` and the asset `pending_evaluation`, whatever the
    schema's other rules say. The launch itself is covered in `test_compliancePipelineRuleWaits.py`."""

    @pytest.mark.parametrize("schema_body", [RULES_SCHEMA_BODY, PIPELINE_ONLY_SCHEMA_BODY],
                             ids=["with-other-rules", "pipeline-rule-only"])
    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_the_rule_waits_the_evaluation_stays_pending_and_the_holder_keeps_the_lock(
            self, restriction, schema_body):
        keys = _launch_keys(restriction)
        locks = Locks(held={key: OTHER_RUN for key in keys})
        tables, result = _evaluate(locks, restriction, schema_body=schema_body)

        response = tables.lambda_client.responses[0]
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == el.LOCK_CONFLICT_MESSAGE_BY_SCOPE[
            el.lock_scope_for_restriction(restriction)]
        assert all(r["ruleName"] != "residual-bound" for r in result["ruleResults"])
        assert result["errorRules"] == []
        assert result["verdict"] == "pending_pipeline"
        assert (result["pipelineRulesPending"], result["pipelineRulesWaiting"]) == (1, 1)
        assert result["complianceState"] == engine.STATE_PENDING_EVALUATION
        parents, tracking = tables.evaluation_records()
        assert parents[0]["status"] == engine.EVALUATION_STATUS_PENDING_PIPELINE
        assert parents[0]["pipelineExecutions"] == [
            {"ruleName": "residual-bound", "status": store.PIPELINE_EXECUTION_WAITING}]
        assert "executionId" not in parents[0]
        (row,) = tracking
        assert row["status"] == store.PIPELINE_EXECUTION_WAITING
        assert row[store.WAITING_WORKFLOW_KEY_ATTRIBUTE] == "GLOBAL:wf-1"
        assert row["waitingAssetKey"] == f"{DB}:{ASSET}"
        assert "executionId" not in row
        assert json.loads(row["launchRequest"])["executionGroupId"] == parents[0]["evaluationId"]
        assert {locks.holder(key) for key in keys} == {OTHER_RUN}

    def test_the_wait_lasts_at_least_as_long_as_the_holders_lock_can(self):
        """The wait bound is the lock lifetime of the workflow's pipelines (here one pipeline whose
        taskTimeout is above the one-day floor, as the execute handler's own lock tests use) plus
        the margin, so a lock taken before the wait expires before the wait does."""
        keys = _launch_keys("perAsset")
        locks = Locks(held={key: OTHER_RUN for key in keys})
        tables = Tables(schema_body=RULES_SCHEMA_BODY)
        tables.lambda_client = _execute_client(locks, "perAsset")
        tables.workflow.get_item.return_value = {"Item": {
            "databaseId": "GLOBAL", "workflowId": "wf-1",
            "specifiedPipelines": [{"pipelineDatabaseId": "GLOBAL", "pipelineId": "pipe-1"}]}}
        tables.pipeline.get_item.return_value = {"Item": {
            "pipelineId": "pipe-1", "executionConfig": {"taskTimeout": "100000"}}}
        before = datetime.now(timezone.utc)
        tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        _parents, (row,) = tables.evaluation_records()
        waited = (datetime.fromisoformat(row["waitExpiresAt"]) - before).total_seconds()
        holder_lock = el.lock_ttl_seconds([{"executionConfig": {"taskTimeout": "100000"}}])
        assert holder_lock == 100000 + el.LOCK_TTL_MARGIN_SECONDS
        assert waited >= holder_lock + store.PIPELINE_WAIT_MARGIN_SECONDS - 5

    def test_another_refusal_is_still_an_error_at_once(self):
        """A 400 that is not the lock's answer (here: the rule's file is not found) records the
        launch error at once, as before."""
        tables = Tables()
        tables.lambda_client = MagicMock(name="lambda_client")
        payload = MagicMock()
        payload.read.return_value = json.dumps({"statusCode": 400, "body": json.dumps(
            {"message": "A conflicting input file could not be found."})}).encode()
        tables.lambda_client.invoke.return_value = {"Payload": payload}
        result = tables.run(store.run_evaluation, DB, ASSET, SCHEMA)
        rule = _rule(result)
        assert (rule["passed"], rule["status"], rule["message"]) == (
            False, "error", store.LAUNCH_FAILED_MESSAGE)
        assert result["pipelineRulesWaiting"] == 0
        _parents, tracking = tables.evaluation_records()
        assert tracking == []

    def test_a_new_file_version_is_not_held_under_per_input_file_version(self):
        keys = _launch_keys("perInputFileVersion")
        locks = Locks(held={key: OTHER_RUN for key in keys})
        tables, result = _evaluate(locks, "perInputFileVersion", version="v2")
        assert tables.lambda_client.responses[0]["statusCode"] == 200
        assert result["pipelineRulesPending"] == 1


# --- release and the completion event on each terminal path ---


def _held_by_the_run(restriction="perAsset"):
    """A lock table holding the compliance-launched run's lock (the callback harness's execution)."""
    return Locks(held={key: EXECUTION_ID for key in _launch_keys(restriction)})


def _workflow_table(restriction="perAsset"):
    table = MagicMock(name="workflow_table")
    table.get_item.return_value = {"Item": {
        "databaseId": "db1", "workflowId": "wf1",
        "systemConfig": {"concurrencyRestriction": restriction}}}
    return table


def _no_input_rows():
    table = MagicMock(name="workflow_execution_inputs_table")
    table.query.return_value = {"Items": []}
    return table


def _events(locks):
    events = MagicMock(name="events_client")
    events.put_events.side_effect = lambda **_kw: locks.journal.append(("publish",)) or {
        "FailedEntryCount": 0, "Entries": [{"EventId": "ev-1"}]}
    return events


def _delivered(events):
    """The one completion entry `events` was asked to put, as EventBridge delivers it."""
    events.put_events.assert_called_once()
    (entry,) = events.put_events.call_args.kwargs["Entries"]
    return {"source": entry["Source"], "detail-type": entry["DetailType"],
            "detail": json.loads(entry["Detail"])}


def _callback_outcome(event, execution_error=""):
    harness = Callback(execution_error=execution_error)
    return harness, json.loads(harness.run(event)["body"])


class _LockedDynamo(reported._Dynamo):
    """The end-state step's DynamoDB resource with the lock table, the workflow row and the
    execution's (absent) input rows the lock release reads."""
    locks = None

    def Table(self, name):
        from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po
        if name == po.workflow_execution_locks_table:
            return self.locks
        if name == po.workflow_table_name:
            return _workflow_table()
        if name == po.workflow_execution_inputs_table:
            return _no_input_rows()
        return super().Table(name)


def end_state_step(locks, status=None):
    """Run the end-state step for the run (EXECUTION_ID) over `locks`: a passing compliance output,
    and `status` as the reserved status object (None: none). Returns the step (`reported.EndState`;
    its `delivered_event()` is the completion event it published)."""
    _LockedDynamo.locks = locks
    with patch.object(reported, "_Dynamo", _LockedDynamo):
        return reported.EndState(reported._staged(status=status))


@pytest.mark.unit
class TestTheEndStateStepReleasesAndAnnounces:

    @pytest.mark.parametrize("status,passed", [(None, True), (reported.REPORTED, False)],
                             ids=["succeeded", "reported-failed"])
    def test_the_lock_is_released_and_the_callback_records_the_rule(self, status, passed):
        locks = _held_by_the_run()
        end_state = end_state_step(locks, status=status)
        assert locks.keys_of(EXECUTION_ID) == []
        assert [kind for kind, *_ in locks.journal if kind == "release"] == ["release"]
        harness, outcome = reported._deliver(end_state)
        assert reported._rule_result(harness)["passed"] is passed


def error_handler_step(locks):
    """Run the error handler for the run (EXECUTION_ID, a step timed out) over `locks`; returns the
    one completion event it published, as EventBridge delivers it."""
    events = _events(locks)
    main_table = MagicMock(name="main_table")
    main_table.query.return_value = {"Items": [{
        "executionStatus": "RUNNING", "executionStartDate": reported.STARTED,
        "executionGroupId": "eval-1"}]}

    def _table(name):
        return {heh.workflow_table: _workflow_table(),
                heh.workflow_execution_inputs_table: _no_input_rows(),
                heh.workflow_execution_locks_table: locks}.get(name, main_table)

    body = {"workflowExecutionId": EXECUTION_ID, "workflowDatabaseId": "db1", "workflowId": "wf1"}
    error_info = {"Error": "States.Timeout", "Cause": json.dumps({"errorMessage": "step timed out"})}
    with contextlib.ExitStack() as stack:
        for context in (
            patch.object(heh, "_get_pipeline_rows", return_value=[]),
            patch.object(heh.eo, "finalize_main_row", return_value=True),
            patch.object(heh, "_fetch_execution_log", return_value=""),
            patch.object(heh.dynamodb, "Table", side_effect=_table),
            patch.object(heh, "events_client", events),
            patch.object(heh, "orchestration_bus_arn", BUS),
            patch.object(heh, "orchestration_event_source_prefix", PREFIX),
        ):
            stack.enter_context(context)
        assert heh.lambda_handler({"body": body, "errorInfo": error_info}, MagicMock()) == {
            "handled": True}
    return _delivered(events)


@pytest.mark.unit
class TestTheErrorHandlerReleasesAndAnnounces:

    def test_the_lock_is_released_and_one_failed_event_reaches_the_callback(self):
        locks = _held_by_the_run()
        event = error_handler_step(locks)
        assert locks.keys_of(EXECUTION_ID) == []
        assert (event["detail"]["status"], event["detail"]["executionGroupId"]) == ("FAILED", "eval-1")
        harness, outcome = _callback_outcome(event, execution_error="States.Timeout: step timed out")
        assert outcome["finalized"] is True
        assert reported._rule_result(harness)["passed"] is False


def _main_row():
    return {"workflowExecutionId": EXECUTION_ID, "workflowDatabaseId:workflowId": "db1:wf1",
            "workflowDatabaseId": "db1", "workflowId": "wf1", "executionGroupId": "eval-1",
            "workflow_execution_arn": "arn:ex:main", "executionStatus": "RUNNING",
            "executionStartDate": "2026-01-01T00:00:00Z"}


def _service_tables(locks):
    main_table = MagicMock(name="main_table")

    def _table(name):
        return {le.workflow_database: _workflow_table(),
                le.workflow_execution_inputs_table: _no_input_rows(),
                le.workflow_execution_locks_table: locks}.get(name, main_table)
    return _table


def abort_route(locks):
    """Abort the run (EXECUTION_ID) through the abort route over `locks`; returns the route's
    answer and the one completion event it published."""
    events = _events(locks)
    with patch.object(le, "get_execution_main_row", return_value=_main_row()), \
            patch.object(le, "authorize_abort", return_value=(True, "")), \
            patch.object(le, "get_pipeline_execution_rows", return_value=[]), \
            patch.object(le.sfn, "stop_execution", return_value={}), \
            patch.object(le, "_persist_reconciled_main_row", return_value=True), \
            patch.object(le, "log_actions"), \
            patch.object(le, "events_client", events), \
            patch.object(le, "orchestration_bus_arn", BUS), \
            patch.object(le, "orchestration_event_source_prefix", PREFIX), \
            patch.object(le.dynamodb, "Table", side_effect=_service_tables(locks)):
        response = le.abort_execution({"requestContext": {"http": {}}}, EXECUTION_ID)
    return response, _delivered(events)


def details_reconcile(locks, landed=True, pipeline_rows=()):
    """The details-view reconcile finding the run (EXECUTION_ID) stopped outside VAMS, over `locks`;
    `landed` is whether its own status write landed, and `pipeline_rows` the run's pipeline rows
    (each one it marks stopped is journaled as `("mark", status)`). Returns the events client it
    published on."""
    events = _events(locks)
    described = {"status": "ABORTED",
                 "stopDate": datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)}

    def _mark(_dynamodb, _table, _pipeline_execution_id, _execution_id, status, **_kw):
        locks.journal.append(("mark", status))

    with patch.object(le.sfn, "describe_execution", return_value=described), \
            patch.object(le, "get_pipeline_execution_rows", return_value=list(pipeline_rows)), \
            patch.object(le.eo, "set_pipeline_status", side_effect=_mark), \
            patch.object(le, "_persist_reconciled_main_row", return_value=landed), \
            patch.object(le, "events_client", events), \
            patch.object(le, "orchestration_bus_arn", BUS), \
            patch.object(le, "orchestration_event_source_prefix", PREFIX), \
            patch.object(le.dynamodb, "Table", side_effect=_service_tables(locks)):
        le._reconcile_main_status(EXECUTION_ID, _main_row())
    return events


@pytest.mark.unit
class TestTheAbortRouteReleasesAndAnnounces:

    def test_the_lock_is_released_and_one_aborted_event_reaches_the_callback(self):
        locks = _held_by_the_run()
        response, event = abort_route(locks)
        assert response["statusCode"] == 200
        assert locks.keys_of(EXECUTION_ID) == []
        assert (event["detail"]["status"], event["detail"]["executionGroupId"]) == ("ABORTED", "eval-1")
        harness, outcome = _callback_outcome(event)
        assert outcome["finalized"] is True
        assert reported._rule_result(harness)["passed"] is False


@pytest.mark.unit
class TestAStopOutsideVamsFoundByTheDetailsReconcile:
    """A stop made outside VAMS (a direct StopExecution, a deleted state machine) runs no terminal
    step; the details-view reconcile that reads the run's end from Step Functions records it."""

    def _reconcile(self, landed, pipeline_rows=()):
        locks = _held_by_the_run()
        return locks, details_reconcile(locks, landed=landed, pipeline_rows=pipeline_rows)

    def test_the_lock_is_released_before_the_one_aborted_event(self):
        locks, events = self._reconcile(landed=True)
        assert locks.keys_of(EXECUTION_ID) == []
        assert [entry[0] for entry in locks.journal if entry[0] != "lock"] == ["release", "publish"]
        event = _delivered(events)
        assert event["detail"]["status"] == "ABORTED"
        harness, outcome = _callback_outcome(event)
        assert outcome["finalized"] is True

    def test_a_pipeline_step_still_running_is_marked_before_the_lock_goes_and_the_one_event(self):
        running = {"pipelineExecutionId": "pex-1", "workflowExecutionId": EXECUTION_ID,
                   "executionStatus": "RUNNING"}
        locks, events = self._reconcile(landed=True, pipeline_rows=[running])
        assert locks.keys_of(EXECUTION_ID) == []
        assert [entry[0] for entry in locks.journal if entry[0] != "lock"] == [
            "mark", "release", "publish"]
        assert ("mark", "ABORTED") in locks.journal
        event = _delivered(events)
        assert event["detail"]["status"] == "ABORTED"
        harness, outcome = _callback_outcome(event)
        assert outcome["finalized"] is True
        assert reported._rule_result(harness)["passed"] is False

    def test_a_status_another_writer_recorded_still_releases_and_is_not_announced_again(self):
        locks, events = self._reconcile(landed=False)
        assert locks.keys_of(EXECUTION_ID) == []
        events.put_events.assert_not_called()


@pytest.mark.unit
class TestALaunchThatFailsAfterTakingItsLock:

    def test_the_lock_is_released_nothing_is_announced_and_the_rule_is_an_error(self):
        locks = Locks()
        tables, result = _evaluate(locks, "perAsset", start_error=RuntimeError("start refused"))
        assert tables.lambda_client.responses[0]["statusCode"] == 500
        assert [kind for kind, *_ in locks.journal] == ["lock", "start", "release"]
        assert locks.rows == {}
        rule = _rule(result)
        assert (rule["passed"], rule["status"], rule["message"]) == (
            False, "error", store.LAUNCH_FAILED_MESSAGE)
        _parents, tracking = tables.evaluation_records()
        assert tracking == []


@pytest.mark.unit
class TestASystemWorkflow:

    @pytest.mark.parametrize("restriction", ["none", "perAsset"])
    def test_a_pipeline_rule_launches_a_system_workflow_like_any_other(self, restriction):
        locks = Locks()
        tables, result = _evaluate(locks, restriction, system_workflow=True)
        assert tables.lambda_client.responses[0]["statusCode"] == 200
        assert result["pipelineRulesPending"] == 1
        assert result["errorRules"] == []
        _parents, tracking = tables.evaluation_records()
        assert len(tracking) == 1
