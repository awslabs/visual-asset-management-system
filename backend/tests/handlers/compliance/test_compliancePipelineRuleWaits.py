# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A compliance pipeline rule whose launch the workflow's concurrency lock refuses waits behind the
running execution and launches when that execution ends (D113).

Under `perAsset`, `perInputFile` and `perInputFileVersion` a launch whose asset, file or file version
another execution of the workflow holds is refused. Compliance used to record such a rule as a
tooling error at once, so with other rules passing a failing asset read `compliant` and the running
execution's result was dropped. Now the rule waits (`waiting`, the evaluation `pending_pipeline`, the
asset `pending_evaluation`), and the workflow callback launches it when an execution of the workflow
over the asset ends. Covered, each under the three locking restrictions with `none` as the control:

- an edit during the run: the newer evaluation waits, launches when the earlier run ends, and its
  own run decides the verdict (a failing measurement quarantines);
- Evaluate Now pressed twice and three times during one run: exactly one more run, for the newest
  evaluation; an older evaluation's waiting rule does not launch, both when the newer evaluation
  closes it as it starts and when only the launch-time ownership check catches it;
- two pipeline rules of one schema naming the same workflow: the second waits for the first rule's
  run and the verdict comes from both;
- the holder is a user's run of the workflow on the asset; a run on another asset releases nothing;
- a relaunch the lock refuses again stays waiting, and launches on the next completion;
- redelivered and out-of-order completion events, and a claim another callback holds: one launch;
- the wait bound: past it the rule is an error with a fixed message, the evaluation finalizes and
  an audit entry says why, checked on a completion of the workflow and on a new evaluation;
- the holder ending by the end-state step (succeeded, and a pipeline-reported failure), the error
  handler, the abort route and a stop outside VAMS found by the details reconcile: the waiting rule
  launches in each case.

Run against DynamoDB doubles built from the CDK table definitions (`_cdkTables.table_doubles`, so
the evaluation table carries its PipelineRuleWaitIndex), with compliance's real launch driving the
real execute handler over a lock table that behaves as DynamoDB does (`test_complianceWorkflowLocks`),
and the real workflow callback.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from boto3.dynamodb.conditions import Key
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance import test_complianceWorkflowLocks as wl
from backend.tests.handlers.compliance._harness import (
    ASSET, DB, REAL_TO_UPDATE_EXPR, SCHEMA, schema_row,
)
from backend.tests.handlers.compliance.test_complianceEvaluationStore import (
    ASSET_PREFIX, BUCKET_ID, BUCKET_NAME, DEFAULT_ASSET_FILES, _s3_client,
)
from backend.tests.handlers.compliance.test_complianceWorkflowCallback import EXECUTION_ID
from common.compliance import evaluationEngine as engine
from common.workflows.executionRecords import workflow_execution_completed_event
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceWorkflowCallback as callback

el = wl.el
RESTRICTIONS = wl.RESTRICTIONS
WORKFLOW = ("GLOBAL", "wf-1")
RULE = "residual-bound"
TIGHT_RULE = "residual-tight"
USER_RUN = "e5550000000000000000000000000555"
OTHER_RUN = wl.OTHER_RUN
OTHER_ASSET = "other.glb"
STARTED = "2026-01-01T00:00:00+00:00"
COMPLETED = "2026-01-01T00:01:00+00:00"
PASSING = {"complianceOutput": True, "status": "success", "measurements": {"residual": 0.2}}
FAILING = {"complianceOutput": True, "status": "success", "measurements": {"residual": 3}}
# Inside the bound rule's tolerance (<= 1), outside the tight rule's (<= 0.5).
BETWEEN = {"complianceOutput": True, "status": "success", "measurements": {"residual": 0.7}}
NAME_RULE = {"ruleType": "assetAttributes", "enforcement": "warn",
             "checks": [{"name": "name", "field": "assetName", "matches": "^[A-Z]"}]}


def _pipeline_rule(workflow, bound=1):
    return {"ruleType": "pipeline", "enforcement": "quarantine",
            "pipelineRef": {"databaseId": workflow[0], "workflowId": workflow[1],
                            "pipelineDatabaseId": "GLOBAL", "pipelineId": "pipe-1"},
            "checks": [{"name": "residual", "outputField": "residual",
                        "tolerance": {"operator": "lte", "value": bound}}]}


def schema_body(other_rules=True, two_rules=False, workflow=WORKFLOW):
    rules = {"named": NAME_RULE} if other_rules else {}
    rules[RULE] = _pipeline_rule(workflow)
    if two_rules:
        rules[TIGHT_RULE] = _pipeline_rule(workflow, bound=0.5)
    return {"schemaFormat": "vams-rules-v1", "rules": rules}


def _event(execution_id, workflow, status="SUCCEEDED", group=None):
    entry = workflow_execution_completed_event(
        "arn:aws:events:us-east-1:123456789012:event-bus/orchestration", "vams.orch",
        execution_id, workflow[0], workflow[1], status, STARTED, COMPLETED,
        execution_group_id=group)
    return {"source": entry["Source"], "detail-type": entry["DetailType"],
            "detail": json.loads(entry["Detail"])}


class World:
    """One asset under one schema, its workflow and pipeline rows, the compliance and execution
    tables as moto doubles, and the execute handler behind compliance's Lambda client over `locks`.
    `version` is the file version the execute handler resolves the input at (a file edit moves
    it). Every launch the handler answers 200 writes the execution's input row, as the handler
    does, so the callback can tell which assets an ended execution released."""

    def __init__(self, doubles, restriction, body=None, workflow=WORKFLOW, locks=None):
        self.doubles = doubles
        self.restriction = restriction
        self.workflow = workflow
        self.locks = locks if locks is not None else wl.Locks()
        self.version = "v1"
        self.responses = []
        self.groups = {}
        self.outputs = {}
        doubles["complianceSchemaStorage"].put_item(Item=schema_row(
            SCHEMA, body=body if body is not None else schema_body(workflow=workflow)))
        doubles["assetStorage"].put_item(Item={
            "databaseId": DB, "assetId": ASSET, "assetName": "Bracket", "bucketId": BUCKET_ID,
            "assetLocation": {"Key": ASSET_PREFIX}})
        doubles["s3AssetBucketsStorage"].put_item(Item={
            "bucketId": BUCKET_ID, "bucketName:baseAssetsPrefix": f"{BUCKET_NAME}:/",
            "bucketName": BUCKET_NAME, "baseAssetsPrefix": "/"})
        doubles["workflowStorageV2"].put_item(Item={
            "databaseId": workflow[0], "workflowId": workflow[1],
            "specifiedPipelines": [{"pipelineDatabaseId": "GLOBAL", "pipelineId": "pipe-1"}]})
        doubles["pipelineStorageV2"].put_item(Item={"databaseId": "GLOBAL", "pipelineId": "pipe-1"})
        doubles["complianceAssetStateStorage"].put_item(Item={
            "databaseId": DB, "assetId": ASSET, "schemaName": SCHEMA, "schemaSource": "asset",
            "complianceState": "compliant", "assetName": "Bracket", "assetNameSort": "bracket",
            "assetSearchText": "bracket asset.glb"})
        lambda_client = MagicMock(name="lambda_client")
        lambda_client.invoke.side_effect = self._invoke
        self._patches = [
            patch.object(store, "s3_client", _s3_client(list(DEFAULT_ASSET_FILES))),
            patch.object(store, "lambda_client", lambda_client),
            patch.object(store, "execute_workflow_function_name", "execute-workflow-fn"),
            patch.object(callback, "read_compliance_output",
                         side_effect=lambda execution_id, _pipeline_id: self.outputs.get(execution_id)),
            patch.object(callback, "read_execution_failure_reason", return_value=None),
        ]
        for active in self._patches:
            active.start()

    def stop(self):
        for active in reversed(self._patches):
            active.stop()

    # --- the execute handler ---

    def _invoke(self, FunctionName, InvocationType, Payload):
        event = json.loads(Payload)
        response, _sfn, _log, _stop = wl.launch._run(
            self.locks, restriction=self.restriction, event=event, tokens=(wl.SYSTEM,),
            resolved_version=self.version)
        self.responses.append(response)
        if response["statusCode"] == 200:
            body = json.loads(event["body"])
            execution_id = json.loads(response["body"])["message"]["executionId"]
            self.groups[execution_id] = body.get("executionGroupId")
            for entry in body["inputFiles"]:
                self.input_row(execution_id, entry["databaseId"], entry["assetId"],
                               entry["relativeFileKey"])
        payload = MagicMock()
        payload.read.return_value = json.dumps(response).encode("utf-8")
        return {"Payload": payload}

    def input_row(self, execution_id, database_id=DB, asset_id=ASSET, file_key="/model.stl"):
        self.doubles["workflowExecutionInputsStorage"].put_item(Item={
            "workflowExecutionId": execution_id,
            "databaseId:assetId:inputAssetFileKey": f"{database_id}:{asset_id}:{file_key}",
            "databaseId": database_id, "assetId": asset_id, "inputAssetFileKey": file_key})

    def launched(self):
        return [json.loads(r["body"])["message"]["executionId"]
                for r in self.responses if r["statusCode"] == 200]

    def refused(self):
        return [r for r in self.responses if r["statusCode"] == 400]

    def hold(self, holder, asset_id=ASSET):
        """Another execution (`holder`, not compliance's) holds the lock of the asset the rule
        reads; its input row names `asset_id`."""
        for key in wl._launch_keys(self.restriction):
            self.locks.rows[key] = {"lockKey": key, "workflowExecutionId": holder,
                                    "lockScope": el._scope_of_key(key),
                                    "acquiredAt": el._iso(int(datetime.now(timezone.utc).timestamp())),
                                    "expiresAt": int(datetime.now(timezone.utc).timestamp()) + 86400}
        self.input_row(holder, asset_id=asset_id)

    # --- compliance ---

    def evaluate(self):
        return store.run_evaluation(DB, ASSET, SCHEMA)

    def release(self, execution_id):
        """What every terminal path does before it announces the end: the run's lock rows go."""
        for key in self.locks.keys_of(execution_id):
            del self.locks.rows[key]

    def deliver(self, event):
        return callback.lambda_handler(event, MagicMock())

    def end(self, execution_id, output=FAILING, status="SUCCEEDED", release=True):
        """The run ends: its locks are released, then its completion event reaches the callback."""
        if release:
            self.release(execution_id)
        self.outputs[execution_id] = output
        return self.deliver(_event(execution_id, self.workflow, status,
                                   group=self.groups.get(execution_id)))

    def state(self):
        return self.doubles["complianceAssetStateStorage"].get_item(
            Key={"databaseId": DB, "assetId": ASSET}, ConsistentRead=True)["Item"]

    def evaluation(self, evaluation_id):
        return self.doubles["complianceEvaluationStorage"].get_item(
            Key={"evaluationId": evaluation_id}, ConsistentRead=True)["Item"]

    def tracking(self, evaluation_id, rule=RULE):
        return self.evaluation(store.pipeline_execution_record_id(evaluation_id, rule))

    def waiting_index(self):
        return self.doubles["complianceEvaluationStorage"].query(
            IndexName=store.PIPELINE_WAIT_INDEX,
            KeyConditionExpression=Key("waitingWorkflowKey").eq(f"{self.workflow[0]}:{self.workflow[1]}"),
        )["Items"]

    def audits(self, event_type):
        return [row for row in self.doubles["complianceAuditStorage"].scan()["Items"]
                if row.get("eventType") == event_type]

    def results(self, evaluation_id):
        return {r["ruleName"]: r for r in json.loads(self.evaluation(evaluation_id)["ruleResults"])}


@pytest.fixture
def world():
    """`world(restriction, **options)` builds the World; the moto tables live for the test."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, (store, callback))
        patches.append(patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR))
        if "workflowExecutionInputsStorage" not in doubles:
            # The execute handler's input rows exist whether or not a compliance module reads them.
            inputs = _cdkTables.storage_tables()["dynamoTables/workflowExecutionInputsStorage"]
            resource.create_table(**inputs.create_table_kwargs("t-workflow-execution-inputs"))
            doubles["workflowExecutionInputsStorage"] = resource.Table("t-workflow-execution-inputs")
        for active in patches:
            active.start()
        built = []

        def build(restriction, **options):
            built.append(World(doubles, restriction, **options))
            return built[-1]
        try:
            yield build
        finally:
            for one in built:
                one.stop()
            for active in reversed(patches):
                active.stop()


def _assert_waiting(w, result):
    assert result["verdict"] == "pending_pipeline"
    assert result["pipelineRulesWaiting"] == 1
    assert result["errorRules"] == []
    row = w.tracking(result["evaluationId"])
    assert row["status"] == store.PIPELINE_EXECUTION_WAITING
    assert "executionId" not in row
    assert w.evaluation(result["evaluationId"])["status"] == engine.EVALUATION_STATUS_PENDING_PIPELINE
    state = w.state()
    assert (state["complianceState"], state["lastEvaluationId"], state["lastEvaluationStatus"]) == (
        engine.STATE_PENDING_EVALUATION, result["evaluationId"],
        engine.EVALUATION_STATUS_PENDING_PIPELINE)


@pytest.mark.unit
class TestAnEditDuringTheRun:

    @pytest.mark.parametrize("other_rules", [True, False], ids=["with-other-rules", "pipeline-rule-only"])
    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_the_newer_evaluation_waits_launches_when_the_run_ends_and_decides_the_verdict(
            self, world, restriction, other_rules):
        w = world(restriction, body=schema_body(other_rules=other_rules))
        e1 = w.evaluate()
        (r1,) = w.launched()
        # A metadata edit: the same file version, so every locking restriction holds it.
        e2 = w.evaluate()
        assert len(w.refused()) == 1
        _assert_waiting(w, e2)
        assert w.locks.keys_of(r1) != []

        w.end(r1, output=FAILING)
        # The earlier run's result lands on its own evaluation only; the newer one launches.
        assert w.evaluation(e1["evaluationId"])["verdict"] == "quarantined"
        r1_, r2 = w.launched()
        assert r1_ == r1 and w.groups[r2] == e2["evaluationId"]
        row = w.tracking(e2["evaluationId"])
        assert (row["status"], row["executionId"]) == (store.PIPELINE_EXECUTION_PENDING, r2)
        assert "waitingWorkflowKey" not in row and w.waiting_index() == []
        assert w.evaluation(e2["evaluationId"])["executionId"] == r2
        assert w.state()["complianceState"] == engine.STATE_PENDING_EVALUATION
        assert w.locks.keys_of(r2) != []

        w.end(r2, output=FAILING)
        evaluation = w.evaluation(e2["evaluationId"])
        assert (evaluation["status"], evaluation["verdict"]) == ("completed", "quarantined")
        assert w.results(e2["evaluationId"])[RULE]["executionId"] == r2
        state = w.state()
        assert (state["complianceState"], state["lastEvaluationId"]) == (
            engine.STATE_QUARANTINED, e2["evaluationId"])

    @pytest.mark.parametrize("other_rules", [True, False], ids=["with-other-rules", "pipeline-rule-only"])
    def test_none_the_control_launches_at_once_and_the_newer_run_decides(self, world, other_rules):
        w = world("none", body=schema_body(other_rules=other_rules))
        w.evaluate()
        e2 = w.evaluate()
        r1, r2 = w.launched()
        assert w.refused() == [] and e2["pipelineRulesWaiting"] == 0
        assert w.tracking(e2["evaluationId"])["status"] == store.PIPELINE_EXECUTION_PENDING
        w.end(r1, output=PASSING)
        assert w.state()["complianceState"] == engine.STATE_PENDING_EVALUATION
        w.end(r2, output=FAILING)
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED
        assert len(w.launched()) == 2

    def test_a_file_edit_under_per_input_file_version_launches_at_once(self, world):
        w = world("perInputFileVersion")
        w.evaluate()
        w.version = "v2"
        e2 = w.evaluate()
        assert len(w.launched()) == 2 and w.refused() == []
        assert e2["pipelineRulesWaiting"] == 0


@pytest.mark.unit
class TestEvaluateNowPressedDuringOneRun:

    @pytest.mark.parametrize("presses", [2, 3])
    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_exactly_one_more_run_for_the_newest_evaluation(self, world, restriction, presses):
        w = world(restriction)
        w.evaluate()
        (r1,) = w.launched()
        later = [w.evaluate() for _ in range(presses)]
        newest = later[-1]
        assert len(w.refused()) == presses
        _assert_waiting(w, newest)
        # Each press closed the waiting rule of the evaluation it replaced.
        for older in later[:-1]:
            row = w.tracking(older["evaluationId"])
            assert row["status"] == store.PIPELINE_EXECUTION_COMPLETED
            assert json.loads(row["ruleResults"])[0]["message"] == store.PIPELINE_WAIT_SUPERSEDED_MESSAGE
            assert w.evaluation(older["evaluationId"])["status"] != engine.EVALUATION_STATUS_PENDING_PIPELINE
        assert [row["evaluationId"] for row in w.waiting_index()] == [
            store.pipeline_execution_record_id(newest["evaluationId"], RULE)]

        w.end(r1, output=PASSING)
        launched = w.launched()
        assert len(launched) == 2
        assert w.groups[launched[1]] == newest["evaluationId"]
        w.end(launched[1], output=FAILING)
        assert len(w.launched()) == 2
        state = w.state()
        assert (state["complianceState"], state["lastEvaluationId"]) == (
            engine.STATE_QUARANTINED, newest["evaluationId"])

    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_a_waiting_rule_the_newer_evaluation_did_not_close_is_dropped_at_launch_time(
            self, world, restriction):
        """A newer evaluation claimed the asset state while the older one's waiting row was not
        yet visible to it: the launch-time ownership check keeps the older rule from launching."""
        w = world(restriction)
        w.evaluate()
        (r1,) = w.launched()
        e2 = w.evaluate()
        with patch.object(store, "settle_waiting_rules_of", return_value=0):
            e3 = w.evaluate()
        assert w.tracking(e2["evaluationId"])["status"] == store.PIPELINE_EXECUTION_WAITING

        w.end(r1, output=PASSING)
        (_r1, r_new) = w.launched()
        assert w.groups[r_new] == e3["evaluationId"]
        row = w.tracking(e2["evaluationId"])
        assert row["status"] == store.PIPELINE_EXECUTION_COMPLETED
        assert json.loads(row["ruleResults"])[0]["message"] == store.PIPELINE_WAIT_SUPERSEDED_MESSAGE
        # The older evaluation finalized on its own row and left the asset state to the newest.
        assert w.evaluation(e2["evaluationId"])["status"] != engine.EVALUATION_STATUS_PENDING_PIPELINE
        assert w.state()["lastEvaluationId"] == e3["evaluationId"]


@pytest.mark.unit
class TestTwoRulesNamingOneWorkflow:

    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_the_second_waits_for_the_first_and_the_verdict_comes_from_both(self, world, restriction):
        w = world(restriction, body=schema_body(two_rules=True))
        e1 = w.evaluate()
        (r1,) = w.launched()
        assert (e1["pipelineRulesPending"], e1["pipelineRulesWaiting"]) == (2, 1)
        assert w.tracking(e1["evaluationId"], TIGHT_RULE)["status"] == store.PIPELINE_EXECUTION_WAITING
        assert w.groups[r1] == e1["evaluationId"]

        w.end(r1, output=BETWEEN)
        (_r1, r2) = w.launched()
        assert w.groups[r2] == e1["evaluationId"]
        assert w.evaluation(e1["evaluationId"])["status"] == engine.EVALUATION_STATUS_PENDING_PIPELINE
        w.end(r2, output=BETWEEN)

        evaluation = w.evaluation(e1["evaluationId"])
        assert (evaluation["status"], evaluation["verdict"]) == ("completed", "quarantined")
        results = w.results(e1["evaluationId"])
        assert (results[RULE]["status"], results[RULE]["passed"]) == ("evaluated", True)
        assert (results[TIGHT_RULE]["status"], results[TIGHT_RULE]["passed"]) == ("evaluated", False)
        assert evaluation.get("errorRules") in (None, [])
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED

    def test_none_the_control_launches_both_at_once(self, world):
        w = world("none", body=schema_body(two_rules=True))
        e1 = w.evaluate()
        assert len(w.launched()) == 2 and e1["pipelineRulesWaiting"] == 0


@pytest.mark.unit
class TestTheHolderIsAUsersRun:

    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_the_rule_launches_when_the_users_run_on_the_asset_ends(self, world, restriction):
        w = world(restriction)
        w.hold(USER_RUN)
        e1 = w.evaluate()
        _assert_waiting(w, e1)
        response = w.end(USER_RUN, output=None)
        assert response["body"] == "Not a compliance execution"
        (r1,) = w.launched()
        assert w.groups[r1] == e1["evaluationId"]
        w.end(r1, output=FAILING)
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED

    def test_a_run_of_the_workflow_on_another_asset_releases_nothing(self, world):
        w = world("perAsset")
        w.hold(USER_RUN)
        e1 = w.evaluate()
        w.input_row(OTHER_RUN, asset_id=OTHER_ASSET)
        w.end(OTHER_RUN, output=None)
        assert w.launched() == [] and len(w.refused()) == 1
        assert w.tracking(e1["evaluationId"])["status"] == store.PIPELINE_EXECUTION_WAITING


@pytest.mark.unit
class TestARelaunchTheLockRefusesAgain:

    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_it_stays_waiting_then_launches_on_the_next_completion(self, world, restriction):
        w = world(restriction)
        w.hold(USER_RUN)
        e1 = w.evaluate()
        first_expiry = w.tracking(e1["evaluationId"])["waitExpiresAt"]
        # Another run takes the lock between the user's run's release and the callback.
        w.release(USER_RUN)
        w.hold(OTHER_RUN)
        w.end(USER_RUN, output=None, release=False)
        assert w.launched() == [] and len(w.refused()) == 2
        row = w.tracking(e1["evaluationId"])
        assert row["status"] == store.PIPELINE_EXECUTION_WAITING
        assert "launchClaimId" not in row
        assert row["waitExpiresAt"] >= first_expiry
        assert len(w.waiting_index()) == 1

        w.end(OTHER_RUN, output=None)
        (r1,) = w.launched()
        assert w.groups[r1] == e1["evaluationId"]


@pytest.mark.unit
class TestRedeliveredAndOutOfOrderCompletions:

    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_each_waiting_rule_launches_once(self, world, restriction):
        w = world(restriction)
        w.evaluate()
        (r1,) = w.launched()
        e2 = w.evaluate()
        holder_event = _event(r1, WORKFLOW, group=w.groups[r1])
        w.release(r1)
        w.outputs[r1] = FAILING
        w.deliver(holder_event)
        w.deliver(holder_event)
        (_r1, r2) = w.launched()
        # The waiting rule's own run ends, then the holder's completion arrives once more.
        w.end(r2, output=FAILING)
        w.deliver(holder_event)
        w.deliver(_event(r2, WORKFLOW, group=e2["evaluationId"]))
        assert len(w.launched()) == 2
        assert w.evaluation(e2["evaluationId"])["verdict"] == "quarantined"
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED

    def test_a_claim_another_callback_holds_is_left_to_it_and_a_stale_one_is_taken_over(self, world):
        w = world("perAsset")
        w.hold(USER_RUN)
        e1 = w.evaluate()
        tracking_id = store.pipeline_execution_record_id(e1["evaluationId"], RULE)
        table = w.doubles["complianceEvaluationStorage"]
        claimed_at = datetime.now(timezone.utc).isoformat()
        table.update_item(Key={"evaluationId": tracking_id},
                          UpdateExpression="SET #s = :l, launchClaimId = :c, launchClaimedAt = :t",
                          ExpressionAttributeNames={"#s": "status"},
                          ExpressionAttributeValues={":l": store.PIPELINE_EXECUTION_LAUNCHING,
                                                     ":c": "other-callback", ":t": claimed_at})
        w.end(USER_RUN, output=None)
        assert w.launched() == []
        stale = (datetime.now(timezone.utc) - timedelta(
            seconds=store.PIPELINE_LAUNCH_CLAIM_STALE_SECONDS + 60)).isoformat()
        table.update_item(Key={"evaluationId": tracking_id}, UpdateExpression="SET launchClaimedAt = :t",
                          ExpressionAttributeValues={":t": stale})
        w.deliver(_event(USER_RUN, WORKFLOW))
        (r1,) = w.launched()
        assert w.tracking(e1["evaluationId"])["executionId"] == r1

    def test_the_run_ending_before_its_launch_is_recorded_is_retried(self, world):
        """The waiting rule's own run ends while the callback that launched it has not yet written
        its execution id: the completion cannot be resolved yet, so it is retried, not dropped."""
        w = world("perAsset")
        w.hold(USER_RUN)
        e1 = w.evaluate()
        tracking_id = store.pipeline_execution_record_id(e1["evaluationId"], RULE)
        w.doubles["complianceEvaluationStorage"].update_item(
            Key={"evaluationId": tracking_id},
            UpdateExpression="SET #s = :l, launchClaimId = :c, launchClaimedAt = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":l": store.PIPELINE_EXECUTION_LAUNCHING, ":c": "c",
                                       ":t": datetime.now(timezone.utc).isoformat()})
        with pytest.raises(store.ComplianceExecutionUnresolved):
            w.deliver(_event(OTHER_RUN, WORKFLOW, group=e1["evaluationId"]))


def _expire(w, evaluation_id):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    w.doubles["complianceEvaluationStorage"].update_item(
        Key={"evaluationId": store.pipeline_execution_record_id(evaluation_id, RULE)},
        UpdateExpression="SET waitExpiresAt = :t", ExpressionAttributeValues={":t": past})


def _assert_expired(w, evaluation_id):
    row = w.tracking(evaluation_id)
    assert row["status"] == store.PIPELINE_EXECUTION_COMPLETED
    (result,) = json.loads(row["ruleResults"])
    assert (result["status"], result["message"]) == ("error", store.PIPELINE_WAIT_EXPIRED_MESSAGE)
    evaluation = w.evaluation(evaluation_id)
    assert evaluation["status"] != engine.EVALUATION_STATUS_PENDING_PIPELINE
    assert evaluation["errorRules"] == [RULE]
    (audit,) = [row for row in w.audits(store.AUDIT_EVALUATION_ERROR)
                if row.get("evaluationId") == evaluation_id]
    details = audit["details"] if isinstance(audit["details"], dict) else json.loads(audit["details"])
    assert details == {"ruleNames": [RULE], "reason": store.PIPELINE_WAIT_EXPIRED_MESSAGE}


@pytest.mark.unit
class TestNothingWaitsForever:

    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_past_its_wait_a_completion_of_the_workflow_records_it_and_finalizes(
            self, world, restriction):
        w = world(restriction)
        w.hold(USER_RUN)
        e1 = w.evaluate()
        _expire(w, e1["evaluationId"])
        # Any completion of the workflow checks the wait, here a run on another asset.
        w.input_row(OTHER_RUN, asset_id=OTHER_ASSET)
        w.end(OTHER_RUN, output=None)
        _assert_expired(w, e1["evaluationId"])
        assert w.launched() == [] and w.waiting_index() == []
        # The name rule passed; the rule that never ran does not count (a tooling error).
        assert w.evaluation(e1["evaluationId"])["verdict"] == "compliant"
        assert w.state()["lastEvaluationStatus"] == "completed"

    def test_past_its_wait_a_new_evaluation_of_the_asset_records_it(self, world):
        w = world("perAsset")
        w.hold(USER_RUN)
        e1 = w.evaluate()
        _expire(w, e1["evaluationId"])
        e2 = w.evaluate()
        _assert_expired(w, e1["evaluationId"])
        # The new evaluation is itself held by the user's run.
        _assert_waiting(w, e2)

    def test_a_waiting_rule_with_no_readable_expiry_has_waited_too_long(self):
        assert store.wait_expired({"waitExpiresAt": "not a time"})
        assert store.wait_expired({})
        assert not store.wait_expired(
            {"waitExpiresAt": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})


def _terminal_world(world, workflow):
    """A rule waiting behind the run the terminal-path harnesses end (EXECUTION_ID, perAsset), in a
    world whose rule names the workflow those harnesses announce."""
    locks = wl._held_by_the_run("perAsset")
    w = world("perAsset", body=schema_body(workflow=workflow), workflow=workflow, locks=locks)
    w.input_row(EXECUTION_ID)
    e1 = w.evaluate()
    _assert_waiting(w, e1)
    return w, e1


def _launched_for(w, e1):
    (r1,) = w.launched()
    assert w.groups[r1] == e1["evaluationId"]
    assert w.tracking(e1["evaluationId"])["executionId"] == r1
    assert w.locks.keys_of(r1) != [] and w.locks.keys_of(EXECUTION_ID) == []


@pytest.mark.unit
class TestTheWaitingRuleLaunchesHoweverTheHolderEnds:

    @pytest.mark.parametrize("status", [None, wl.reported.REPORTED], ids=["succeeded", "reported-failed"])
    def test_the_end_state_step(self, world, status):
        w, e1 = _terminal_world(world, ("GLOBAL", "wf-1"))
        end_state = wl.end_state_step(w.locks, status=status)
        w.deliver(end_state.delivered_event())
        _launched_for(w, e1)

    def test_the_error_handler(self, world):
        w, e1 = _terminal_world(world, ("db1", "wf1"))
        w.deliver(wl.error_handler_step(w.locks))
        _launched_for(w, e1)

    def test_the_abort_route(self, world):
        w, e1 = _terminal_world(world, ("db1", "wf1"))
        _response, event = wl.abort_route(w.locks)
        w.deliver(event)
        _launched_for(w, e1)

    def test_a_stop_outside_vams_found_by_the_details_reconcile(self, world):
        w, e1 = _terminal_world(world, ("db1", "wf1"))
        events = wl.details_reconcile(w.locks, landed=True)
        w.deliver(wl._delivered(events))
        _launched_for(w, e1)
