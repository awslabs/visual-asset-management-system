# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An evaluation decides the asset only under the claim and the binding it started with.

- A failed evaluation is evaluated again. It records its start on the compliance record (its
  claim) before it reads anything, and the trigger takes a change as covered by an evaluation
  that started after it. An AWS error after the claim used to leave the claim in place, so the
  queue's retry evaluated nothing and the asset stayed `pending_pipeline` (a quarantine kept its
  awaiting-re-evaluation marker) for an evaluation no row records. Now the failed evaluation puts
  the record back as it was before its claim, and a claim old enough that its run cannot be alive
  and that no evaluation row records no longer covers a change.
- An evaluation that began before the binding changed, or was removed, does not write its verdict
  after it, and does not create a removed record again.
- An evaluation against a named schema other than the bound one is a dry run: results only.
- A schema with a rule that cannot be read, or a parent that is not found, gives no verdict.

Run against moto tables built from the CDK definitions, with compliance's real trigger, launch,
execute handler and workflow callback (`test_complianceQuarantineHeldWhilePending.held`). The
failures are real errors raised by a table read; nothing replaces the evaluation.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.compliance import test_compliancePipelineRuleWaits as waits
from backend.tests.handlers.compliance.test_complianceQuarantineHeldWhilePending import (  # noqa: F401
    held,
)
from backend.tests.handlers.compliance.test_complianceReleaseRace import _before_write, _is_outcome_write
from backend.tests.handlers.compliance.test_complianceTrigger import _sns_event, _stream_message
from backend.tests.handlers.compliance._harness import (
    ASSET, DB, SCHEMA, USER, body_of, claims_for, enforcer, rest_event, schema_row,
)
from common.compliance import evaluationEngine as engine
from handlers.compliance import complianceBindingApply as binding_apply
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceTrigger as trigger

THROTTLED = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                                   "Message": "slow down"}}, "Query")
PASSING_NAME = {"ruleType": "assetAttributes", "enforcement": "quarantine",
                "checks": [{"name": "name", "field": "assetName", "matches": "^[A-Z]"}]}
FAILING_NAME = {"ruleType": "assetAttributes", "enforcement": "quarantine",
                "checks": [{"name": "name", "field": "assetName", "matches": "^z"}]}
OTHER_SCHEMA = "other-schema"


def _synchronous(rule=PASSING_NAME, **extra):
    return {"schemaFormat": "vams-rules-v1", "rules": {"named": rule}, **extra}


def _ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def _bound_by_database(w, auto_eval=True):
    """The record inherits the database binding (not an asset override), with automatic
    evaluation on, as the trigger evaluates it."""
    w.doubles["databaseStorage"].put_item(Item={"databaseId": DB, "complianceSchemaName": SCHEMA,
                                                "complianceAutoEval": auto_eval})
    record = dict(w.state(), schemaSource="database")
    w.doubles["complianceAssetStateStorage"].put_item(Item=record)


def _deliver(event):
    response = trigger.lambda_handler(event, MagicMock())
    return [entry["itemIdentifier"] for entry in response["batchItemFailures"]]


def _evaluation_rows(w):
    return [row for row in w.doubles["complianceEvaluationStorage"].scan()["Items"]
            if row.get("recordType") is None]


def _fail_once(error=THROTTLED):
    """A patch under which the first asset metadata read (made after the claim) raises."""
    real = store.get_asset_metadata
    calls = []

    def read(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise error
        return real(*args, **kwargs)
    return patch.object(store, "get_asset_metadata", side_effect=read)


@pytest.mark.unit
class TestAFailedEvaluationIsEvaluatedAgain:

    def test_the_queue_retry_of_a_change_evaluates_it(self, held):
        w = held("none", body=_synchronous())
        _bound_by_database(w)
        before = w.state()
        event = _sns_event(_stream_message(change_source="assetEdit", change_at=_ago(minutes=1)))
        with _fail_once():
            assert _deliver(event) == ["q-1"]
        # The failed evaluation's claim is gone: the record is as it was, marker and pointers.
        after = w.state()
        for field in ("lastEvaluationId", "lastEvaluatedAt", "lastEvaluationStatus",
                      "complianceState", "awaitingReevaluationReason"):
            assert after.get(field) == before.get(field), field
        assert _evaluation_rows(w) == []

        # The redelivered record evaluates the change.
        assert _deliver(event) == []
        (row,) = _evaluation_rows(w)
        state = w.state()
        assert (state["lastEvaluationId"], state["lastEvaluationStatus"]) == (
            row["evaluationId"], engine.EVALUATION_STATUS_COMPLETED)
        assert state["complianceState"] == engine.STATE_COMPLIANT

    def test_a_claim_whose_run_stopped_does_not_cover_a_change(self, held):
        """A run that timed out wrote its claim and nothing else: once it is older than any run
        can be, the change it was for is evaluated."""
        w = held("none", state="compliant", body=_synchronous())
        _bound_by_database(w)
        stale = _ago(seconds=store.EVALUATION_CLAIM_STALE_SECONDS + 60)
        w.doubles["complianceAssetStateStorage"].update_item(
            Key={"databaseId": DB, "assetId": ASSET},
            UpdateExpression="SET lastEvaluationId = :id, lastEvaluatedAt = :at, "
                             "lastEvaluationStatus = :pending",
            ExpressionAttributeValues={":id": "ev-timed-out", ":at": stale,
                                       ":pending": engine.EVALUATION_STATUS_PENDING_PIPELINE})
        changed = (datetime.fromisoformat(stale) - timedelta(minutes=1)).isoformat()
        assert _deliver(_sns_event(_stream_message(change_source="assetEdit", change_at=changed))) == []
        (row,) = _evaluation_rows(w)
        assert w.state()["lastEvaluationId"] == row["evaluationId"]

    def test_a_recent_claim_still_covers_the_change_it_was_for(self, held):
        w = held("none", state="compliant", body=_synchronous())
        _bound_by_database(w)
        recent = _ago(minutes=1)
        w.doubles["complianceAssetStateStorage"].update_item(
            Key={"databaseId": DB, "assetId": ASSET},
            UpdateExpression="SET lastEvaluationId = :id, lastEvaluatedAt = :at, "
                             "lastEvaluationStatus = :pending",
            ExpressionAttributeValues={":id": "ev-running", ":at": recent,
                                       ":pending": engine.EVALUATION_STATUS_PENDING_PIPELINE})
        changed = _ago(minutes=2)
        assert _deliver(_sns_event(_stream_message(change_source="assetEdit", change_at=changed))) == []
        assert _evaluation_rows(w) == []
        assert w.state()["lastEvaluationId"] == "ev-running"

    def test_an_undo_after_a_retried_claim_keeps_the_concurrent_write(self, held):
        """A writer releases the record between the evaluation's read and its claim; the claim
        lands on the re-read record and the evaluation then fails. The undo puts back the copy the
        claim was computed from, not the first read: the release stands."""
        w = held("none", state="quarantined", body=_synchronous())
        real_update = store.update_asset_state
        fired = []

        def update(database_id, asset_id, updates, *args, **kwargs):
            if not fired and "lastEvaluationId" in updates:
                fired.append(True)
                current = store.get_compliance_record(DB, ASSET, consistent_read=True)
                real_update(DB, ASSET, {"complianceState": engine.STATE_COMPLIANT,
                                        "updatedAt": store.now_iso()},
                            remove_fields=("quarantineReason",), computed_from=current)
            return real_update(database_id, asset_id, updates, *args, **kwargs)

        with patch.object(store, "update_asset_state", side_effect=update), _fail_once():
            with pytest.raises(ClientError):
                store.run_evaluation(DB, ASSET, SCHEMA)
        assert fired
        state = w.state()
        assert state["complianceState"] == engine.STATE_COMPLIANT, state
        assert state.get("lastEvaluationStatus") == engine.EVALUATION_STATUS_COMPLETED

    def test_a_failure_after_the_launch_leaves_the_claim_and_launches_once(self, held):
        """The pending state write fails after the pipeline rule launched. The evaluation row
        exists, so the claim stays: the queue's retry finds the change covered and launches
        nothing, and the run's callback finalizes the evaluation (as on the base)."""
        w = held("none", state="compliant", body=waits.schema_body(other_rules=False))
        _bound_by_database(w)
        real = store._write_evaluation_state
        calls = []

        def write(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise THROTTLED
            return real(*args, **kwargs)

        event = _sns_event(_stream_message(change_source="assetEdit", change_at=_ago(minutes=1)))
        with patch.object(store, "_write_evaluation_state", side_effect=write):
            assert _deliver(event) == ["q-1"]
            (row,) = _evaluation_rows(w)
            assert w.state()["lastEvaluationId"] == row["evaluationId"]
            assert _deliver(event) == []
        assert len(w.launched()) == 1
        assert len(_evaluation_rows(w)) == 1


@pytest.mark.unit
class TestAnEvaluationBegunBeforeABindingChangeDoesNotDecide:

    def test_a_pipeline_verdict_after_a_binding_change_is_not_applied(self, held):
        w = held("none", state="compliant", body=waits.schema_body(other_rules=False))
        _bound_by_database(w, auto_eval=False)
        w.doubles["complianceSchemaStorage"].put_item(Item=schema_row(OTHER_SCHEMA))
        started = w.evaluate()
        (run,) = w.launched()
        outcome, change = binding_apply.bind_record(DB, ASSET, w.state(), OTHER_SCHEMA, "Bracket")
        assert outcome == binding_apply.WRITTEN
        w.end(run, output=waits.FAILING)
        state = w.state()
        # The binding's state stands: pending evaluation under the new schema, no quarantine.
        assert (state["schemaName"], state["complianceState"]) == (
            OTHER_SCHEMA, engine.STATE_PENDING_EVALUATION)
        assert "quarantineReason" not in state or state["quarantineReason"] is None
        # The verdict is recorded on the evaluation's own row only.
        assert w.evaluation(started["evaluationId"])["status"] == engine.EVALUATION_STATUS_COMPLETED
        assert [row for row in w.audits("compliance_check")
                if row["evaluationId"] == started["evaluationId"]
                and row.get("newState") == engine.STATE_QUARANTINED] == []

    def test_a_removed_record_is_not_created_again(self, held):
        w = held("none", state="compliant", body=waits.schema_body(other_rules=False))
        _bound_by_database(w, auto_eval=False)
        w.evaluate()
        (run,) = w.launched()
        outcome, _ = binding_apply.unbind_record(DB, w.state())
        assert outcome == binding_apply.WRITTEN
        w.end(run, output=waits.FAILING)
        assert "Item" not in w.doubles["complianceAssetStateStorage"].get_item(
            Key={"databaseId": DB, "assetId": ASSET}, ConsistentRead=True)

    def test_a_binding_change_between_the_read_and_the_write_holds_the_verdict_off(self, held):
        w = held("none", state="compliant", body=_synchronous(FAILING_NAME))
        _bound_by_database(w, auto_eval=False)
        w.doubles["complianceSchemaStorage"].put_item(Item=schema_row(OTHER_SCHEMA))

        def rebind():
            binding_apply.bind_record(DB, ASSET, w.state(), OTHER_SCHEMA, "Bracket")
        between, done = _before_write(_is_outcome_write, rebind)
        with between:
            result = store.run_evaluation(DB, ASSET, SCHEMA)
        assert done
        assert result["verdict"] == engine.STATE_QUARANTINED
        state = w.state()
        assert (state["schemaName"], state["complianceState"]) == (
            OTHER_SCHEMA, engine.STATE_PENDING_EVALUATION)

    def test_an_evaluation_asked_for_the_schema_the_record_was_bound_to_before_does_not_run(self, held):
        w = held("none", state="compliant", body=_synchronous(FAILING_NAME))
        _bound_by_database(w, auto_eval=False)
        binding_apply.bind_record(DB, ASSET, w.state(), OTHER_SCHEMA, "Bracket")
        result = store.run_evaluation(DB, ASSET, SCHEMA)
        assert result["error"] == store.BINDING_CHANGED_MESSAGE
        assert _evaluation_rows(w) == []

    def test_an_evaluation_begun_after_the_change_decides(self, held):
        w = held("none", state="compliant", body=_synchronous(FAILING_NAME))
        _bound_by_database(w, auto_eval=False)
        w.doubles["complianceSchemaStorage"].put_item(Item=schema_row(
            OTHER_SCHEMA, body=_synchronous(FAILING_NAME)))
        binding_apply.bind_record(DB, ASSET, w.state(), OTHER_SCHEMA, "Bracket")
        result = store.run_evaluation(DB, ASSET, OTHER_SCHEMA)
        assert result["verdict"] == engine.STATE_QUARANTINED
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED


def _snapshot(w):
    return {name: sorted(json.dumps(item, sort_keys=True, default=str)
                         for item in w.doubles[name].scan()["Items"])
            for name in ("complianceAssetStateStorage", "complianceEvaluationStorage",
                         "complianceAuditStorage")}


def _evaluate_route(body):
    with patch.object(evaluate_svc, "request_to_claims", claims_for(USER)), \
            patch.object(evaluate_svc, "CasbinEnforcer", return_value=enforcer()), \
            patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()) as cascade:
        response = evaluate_svc.lambda_handler(rest_event(
            "POST", f"/compliance/evaluate/{DB}/{ASSET}", {"databaseId": DB, "assetId": ASSET},
            body=json.dumps(body)), MagicMock())
    return response["statusCode"], body_of(response), cascade


@pytest.mark.unit
class TestANamedSchemaOtherThanTheBoundOneIsADryRun:

    def test_its_results_are_returned_and_nothing_is_recorded_or_launched(self, held):
        w = held("none", state="quarantined", body=_synchronous())
        w.doubles["complianceSchemaStorage"].put_item(Item=schema_row(OTHER_SCHEMA, body={
            "schemaFormat": "vams-rules-v1",
            "rules": {"named": PASSING_NAME, waits.RULE: waits._pipeline_rule(waits.WORKFLOW)}}))
        before = _snapshot(w)
        status, body, cascade = _evaluate_route({"schemaName": OTHER_SCHEMA})
        assert status == 200, body
        assert body["dryRun"] is True and "evaluationId" not in body
        assert body["complianceState"] == engine.STATE_QUARANTINED
        assert body["pipelineRulesNotRun"] == 1
        results = {r["ruleName"]: r for r in body["ruleResults"]}
        assert results["named"]["passed"] is True
        assert (results[waits.RULE]["status"], results[waits.RULE]["message"]) == (
            "error", engine.DRY_RUN_PIPELINE_RULE_MESSAGE)
        assert _snapshot(w) == before
        assert w.launched() == []
        cascade.assert_not_called()

    def test_naming_the_bound_schema_evaluates_it(self, held):
        w = held("none", state="compliant", body=_synchronous())
        status, body, _ = _evaluate_route({"schemaName": SCHEMA})
        assert status == 200, body
        assert body["dryRun"] is False
        assert w.state()["lastEvaluationId"] == body["evaluationId"]


@pytest.mark.unit
class TestASchemaReadInPartGivesNoVerdict:

    @pytest.mark.parametrize("enforce_tooling", [False, True], ids=["default", "tooling-enforced"])
    def test_an_unreadable_rule_leaves_a_quarantine_as_it_is(self, held, enforce_tooling):
        w = held("none", body={"schemaFormat": "vams-rules-v1", "rules": {
            "named": PASSING_NAME, "broken": {"ruleType": "notARuleType", "enforcement": "warn"}}})
        with patch.object(engine, "TOOLING_FAILURES_APPLY_ENFORCEMENT", enforce_tooling):
            result = w.evaluate()
        assert result["verdict"] == "error"
        state = w.state()
        assert state["complianceState"] == engine.STATE_QUARANTINED
        assert state["lastEvaluationStatus"] == engine.EVALUATION_STATUS_ERROR
        assert w.audits("quarantine_released") == []
        assert w.evaluation(result["evaluationId"])["errorMessage"] == engine.UNREADABLE_SCHEMA_MESSAGE

    def test_a_missing_parent_gives_no_verdict(self, held):
        w = held("none", body=_synchronous(extends="no-such-parent"))
        result = w.evaluate()
        assert result["verdict"] == "error"
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED

    def test_no_pipeline_rule_of_such_a_schema_is_launched(self, held):
        w = held("none", body={"schemaFormat": "vams-rules-v1", "rules": {
            waits.RULE: waits._pipeline_rule(waits.WORKFLOW),
            "broken": {"ruleType": "notARuleType", "enforcement": "warn"}}})
        result = w.evaluate()
        assert result["verdict"] == "error" and result["pipelineRulesPending"] == 0
        assert w.launched() == []
        results = {r["ruleName"]: r for r in result["ruleResults"]}
        assert results[waits.RULE]["message"] == engine.PIPELINE_RULE_NOT_RUN_MESSAGE
