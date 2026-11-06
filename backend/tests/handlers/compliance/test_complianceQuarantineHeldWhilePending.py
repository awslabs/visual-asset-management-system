# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A quarantined asset stays quarantined, download block included, while an evaluation waits for
its pipeline rules (D147).

An evaluation whose schema has a pipeline rule used to write `pending_evaluation` over a
`quarantined` record while the rule ran or waited, clearing the quarantine reason and the execution
failures, so with `quarantineBlocksDownload` on an edit (automatic evaluation) or Evaluate Now made
a quarantined asset downloadable until the run ended. Now the record stays `quarantined` with its
reason, execution failures and `quarantinedAt`, marked awaiting re-evaluation (`evaluationPending`),
with no release entry and no notification, until the verdict decides it. Covered:

- every entry point that starts an evaluation: Evaluate Now, the trigger, Evaluate All, a sweep and
  a cascade node, with the rule running and with it waiting behind another execution;
- each ending: a pass releases (one `quarantine_released`), a quarantine-level failure keeps the
  original `quarantinedAt` and sends no second notification, a warn-level failure moves the asset
  to `non_compliant`, an expired wait (no verdict) leaves it quarantined, and an evaluation a newer
  one superseded leaves the decision to the newer one;
- a binding-change marker (D118) the record already carries is kept while the evaluation is pending;
- an asset that was not quarantined still reads `pending_evaluation` meanwhile.

Run against moto tables built from the CDK definitions, with compliance's real launch driving the
real execute handler (`test_compliancePipelineRuleWaits.World`) and the real workflow callback.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance import test_compliancePipelineRuleWaits as waits
from backend.tests.handlers.compliance._harness import (
    ASSET, DB, REAL_TO_UPDATE_EXPR, SCHEMA, USER, body_of, claims_for, enforcer, rest_event,
)
from common.compliance import evaluationEngine as engine
from common.compliance import quarantineGuard
from handlers.compliance import complianceCascadeExecutor as executor
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceTrigger as trigger
from handlers.compliance import complianceWorkflowCallback as callback
from models.common import VAMSGeneralErrorResponse

QUARANTINED_AT = "2026-11-01T08:00:00+00:00"
REASON = "residual 3 exceeds 1"
FAILURES = [{"ruleName": waits.RULE, "executionId": "e-old", "executionFailureReason": "mesh broken"}]
MARKER = ("awaitingReevaluationSince", "awaitingReevaluationSchema", "awaitingReevaluationReason")
NOTIFY = "handlers.compliance.complianceNotifications.notify_quarantine"


def _warn_body():
    body = waits.schema_body(other_rules=False)
    body["rules"][waits.RULE]["enforcement"] = "warn"
    return body


@pytest.fixture
def held():
    """`held(restriction, state="quarantined", body=None)`: the pipeline-rule World with the
    asset's record in `state` (quarantined with a reason, execution failures and start time), the
    download block on, and the quarantine notifier recorded."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(
            resource, (store, callback, evaluate_svc, trigger, executor))
        patches.append(patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR))
        if "workflowExecutionInputsStorage" not in doubles:
            inputs = _cdkTables.storage_tables()["dynamoTables/workflowExecutionInputsStorage"]
            resource.create_table(**inputs.create_table_kwargs("t-workflow-execution-inputs"))
            doubles["workflowExecutionInputsStorage"] = resource.Table("t-workflow-execution-inputs")
        notify = MagicMock(name="notify_quarantine")
        patches += [
            patch.object(quarantineGuard, "quarantine_blocks_download", True),
            patch.object(quarantineGuard, "compliance_asset_state_table",
                         doubles["complianceAssetStateStorage"]),
            patch(NOTIFY, notify),
        ]
        for active in patches:
            active.start()
        built = []

        def build(restriction="none", state="quarantined", body=None, marker=None):
            w = waits.World(doubles, restriction, body=body)
            w.notify = notify
            doubles["databaseStorage"].put_item(Item={"databaseId": DB})
            record = dict(doubles["complianceAssetStateStorage"].get_item(
                Key={"databaseId": DB, "assetId": ASSET})["Item"])
            record.update(complianceState=state, lastEvaluationId="ev-before",
                          lastEvaluatedAt="2026-11-01T07:59:00+00:00",
                          lastEvaluationStatus="completed")
            if state == engine.STATE_QUARANTINED:
                record.update(quarantineReason=REASON, quarantineExecutionFailures=FAILURES,
                              quarantinedAt=QUARANTINED_AT)
            record.update(marker or {})
            doubles["complianceAssetStateStorage"].put_item(Item=record)
            built.append(w)
            return w
        try:
            yield build
        finally:
            for one in built:
                one.stop()
            for active in reversed(patches):
                active.stop()


def _blocked():
    try:
        quarantineGuard.check_quarantine_block(DB, ASSET)
    except VAMSGeneralErrorResponse:
        return True
    return False


def _released(w):
    return w.audits("quarantine_released")


def _assert_held(w, evaluation_id, reason="evaluationPending", schema=SCHEMA):
    """The record while the evaluation is pending: quarantined as before, marked, blocked."""
    state = w.state()
    assert state["complianceState"] == engine.STATE_QUARANTINED
    assert (state["quarantineReason"], state["quarantineExecutionFailures"],
            state["quarantinedAt"]) == (REASON, FAILURES, QUARANTINED_AT)
    assert (state["lastEvaluationId"], state["lastEvaluationStatus"]) == (
        evaluation_id, engine.EVALUATION_STATUS_PENDING_PIPELINE)
    assert state["awaitingReevaluationReason"] == reason
    assert state.get("awaitingReevaluationSchema") == schema
    assert state["awaitingReevaluationSince"]
    assert _blocked() is True
    assert _released(w) == []
    w.notify.assert_not_called()
    (check,) = [row for row in w.audits("compliance_check") if row["evaluationId"] == evaluation_id]
    assert (check["previousState"], check["newState"]) == ("quarantined", "quarantined")


def _evaluate_now(w):
    with patch.object(evaluate_svc, "request_to_claims", claims_for(USER)), \
            patch.object(evaluate_svc, "CasbinEnforcer", return_value=enforcer()), \
            patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()):
        response = evaluate_svc.lambda_handler(rest_event(
            "POST", f"/compliance/evaluate/{DB}/{ASSET}", {"databaseId": DB, "assetId": ASSET},
            body=json.dumps({})), MagicMock())
    assert response["statusCode"] == 200, response
    return body_of(response)


def _service(path, params):
    with patch.object(evaluate_svc, "request_to_claims", claims_for(USER)), \
            patch.object(evaluate_svc, "CasbinEnforcer", return_value=enforcer()), \
            patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()):
        response = evaluate_svc.lambda_handler(rest_event("POST", path, params,
                                                          body=json.dumps({})), MagicMock())
    assert response["statusCode"] == 200, response
    return body_of(response)


def _trigger(w):
    with patch.object(trigger, "check_and_trigger_cascade", MagicMock()):
        trigger.trigger_evaluation(DB, ASSET, SCHEMA)
    return w.state()["lastEvaluationId"]


def _cascade_node(w):
    node = {"databaseId": DB, "assetId": ASSET}
    w.doubles["complianceCascadeStorage"].put_item(Item={
        "cascadeId": "c-1", "state": executor.CASCADE_STATE_EXECUTING,
        "triggeredByDatabaseId": DB, "triggeredByAssetId": "parent"})
    with patch.object(executor, "discover_all_descendants", return_value=[node]), \
            patch.object(executor, "topological_sort", return_value=[node]), \
            patch.object(executor, "_authorized_database_ids", return_value=None), \
            patch.object(executor, "_authorized_asset_keys", return_value=None), \
            patch.object(executor, "automatic_evaluation_target", return_value="evaluate"), \
            patch.object(executor, "_complete_cascade", MagicMock()):
        executor.execute_cascade("c-1")
    return w.state()["lastEvaluationId"]


def _start(w, entry):
    """Start an evaluation of the asset through `entry`; returns its evaluation id."""
    if entry == "evaluate-now":
        body = _evaluate_now(w)
        assert body["complianceState"] == engine.STATE_QUARANTINED
        return body["evaluationId"]
    if entry == "trigger":
        return _trigger(w)
    if entry == "evaluate-all":
        _service(f"/compliance/evaluate/{DB}", {"databaseId": DB})
        return w.state()["lastEvaluationId"]
    if entry == "sweep":
        _service(f"/compliance/sweep/{SCHEMA}", {"schemaName": SCHEMA})
        return w.state()["lastEvaluationId"]
    if entry == "cascade":
        return _cascade_node(w)
    result = w.evaluate()
    assert result["complianceState"] == engine.STATE_QUARANTINED
    return result["evaluationId"]


ENTRIES = ["evaluate-now", "trigger", "evaluate-all", "sweep", "cascade"]


@pytest.mark.unit
class TestTheQuarantineHoldsWhileTheEvaluationIsPending:

    @pytest.mark.parametrize("entry", ENTRIES)
    def test_while_the_rule_runs(self, held, entry):
        w = held("none")
        evaluation_id = _start(w, entry)
        (run,) = w.launched()
        assert w.evaluation(evaluation_id)["status"] == engine.EVALUATION_STATUS_PENDING_PIPELINE
        _assert_held(w, evaluation_id)
        # The verdict decides it: a pass releases, once, and the marker goes.
        w.end(run, output=waits.PASSING)
        state = w.state()
        assert state["complianceState"] == engine.STATE_COMPLIANT
        assert all(state.get(key) is None for key in MARKER)
        assert (state.get("quarantineReason"), state.get("quarantinedAt")) == (None, None)
        assert _blocked() is False
        assert len(_released(w)) == 1
        w.notify.assert_not_called()

    @pytest.mark.parametrize("entry", ["evaluate-now", "trigger", "cascade"])
    def test_while_the_rule_waits_behind_another_execution(self, held, entry):
        w = held("perAsset")
        w.hold(waits.USER_RUN)
        evaluation_id = _start(w, entry)
        assert w.launched() == []
        assert w.tracking(evaluation_id)["status"] == store.PIPELINE_EXECUTION_WAITING
        _assert_held(w, evaluation_id)
        # The holder ends: the rule launches, and the asset is still held while it runs.
        w.end(waits.USER_RUN, output=None)
        (run,) = w.launched()
        _assert_held(w, evaluation_id)
        w.end(run, output=waits.FAILING)
        self._assert_still_quarantined(w, evaluation_id)

    @staticmethod
    def _assert_still_quarantined(w, evaluation_id):
        state = w.state()
        assert (state["complianceState"], state["lastEvaluationId"]) == (
            engine.STATE_QUARANTINED, evaluation_id)
        # The quarantine never lifted: its start is kept and no second notification is sent.
        assert state["quarantinedAt"] == QUARANTINED_AT
        assert state["quarantineReason"] != REASON
        assert all(state.get(key) is None for key in MARKER)
        assert _blocked() is True
        assert _released(w) == []
        w.notify.assert_not_called()


@pytest.mark.unit
class TestEachEnding:

    def test_a_quarantine_level_failure_keeps_the_quarantine_and_its_start(self, held):
        w = held("none")
        evaluation_id = _start(w, "evaluate-now")
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        TestTheQuarantineHoldsWhileTheEvaluationIsPending._assert_still_quarantined(w, evaluation_id)
        # The reason is the new verdict's.
        assert "residual" in w.state()["quarantineReason"]

    def test_a_warn_level_failure_moves_it_to_non_compliant(self, held):
        w = held("none", body=_warn_body())
        evaluation_id = _start(w, "evaluate-now")
        _assert_held(w, evaluation_id)
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        state = w.state()
        assert state["complianceState"] == engine.STATE_NON_COMPLIANT
        assert (state.get("quarantinedAt"), state.get("quarantineReason")) == (None, None)
        assert all(state.get(key) is None for key in MARKER)
        assert _blocked() is False
        # Not a release: quarantined to non_compliant writes no quarantine_released.
        assert _released(w) == []

    def test_an_expired_wait_with_no_verdict_leaves_it_quarantined(self, held):
        # The pipeline rule only: once it errors no rule is left to decide, so there is no verdict.
        w = held("perAsset", body=waits.schema_body(other_rules=False))
        w.hold(waits.USER_RUN)
        evaluation_id = _start(w, "evaluate-now")
        waits._expire(w, evaluation_id)
        w.end(waits.USER_RUN, output=None)
        waits._assert_expired(w, evaluation_id)
        assert w.evaluation(evaluation_id)["verdict"] == "error"
        state = w.state()
        assert (state["complianceState"], state["lastEvaluationStatus"]) == (
            engine.STATE_QUARANTINED, engine.EVALUATION_STATUS_ERROR)
        assert (state["quarantineReason"], state["quarantinedAt"]) == (REASON, QUARANTINED_AT)
        # Nothing is pending any more: the marker goes and the earlier verdict stands.
        assert all(state.get(key) is None for key in MARKER)
        assert _blocked() is True
        assert _released(w) == []
        w.notify.assert_not_called()

    def test_a_superseded_evaluation_leaves_the_decision_to_the_newer_one(self, held):
        w = held("perAsset")
        w.hold(waits.USER_RUN)
        first = _start(w, "evaluate-now")
        second = _start(w, "evaluate-now")
        assert w.evaluation(first)["closedReason"] == engine.CLOSED_SUPERSEDED
        _assert_held(w, second)
        w.end(waits.USER_RUN, output=None)
        (run,) = w.launched()
        assert w.groups[run] == second
        _assert_held(w, second)
        w.end(run, output=waits.PASSING)
        assert w.state()["complianceState"] == engine.STATE_COMPLIANT
        assert len(_released(w)) == 1

    def test_an_archive_while_pending_closes_it_and_leaves_it_quarantined(self, held):
        w = held("perAsset")
        w.hold(waits.USER_RUN)
        evaluation_id = _start(w, "evaluate-now")
        assert store.close_open_evaluations_of_asset(DB, ASSET, engine.CLOSED_ASSET_ARCHIVED) == 1
        state = w.state()
        assert (state["complianceState"], state["lastEvaluationStatus"]) == (
            engine.STATE_QUARANTINED, engine.EVALUATION_STATUS_ERROR)
        assert all(state.get(key) is None for key in MARKER)
        assert w.evaluation(evaluation_id)["closedReason"] == engine.CLOSED_ASSET_ARCHIVED


@pytest.mark.unit
class TestOtherRecords:

    def test_a_binding_change_marker_is_kept_while_the_evaluation_is_pending(self, held):
        marker = {"awaitingReevaluationSince": "2026-11-01T07:59:30+00:00",
                  "awaitingReevaluationReason": "schemaBound",
                  "awaitingReevaluationSchema": SCHEMA}
        w = held("none", marker=marker)
        evaluation_id = _start(w, "evaluate-now")
        _assert_held(w, evaluation_id, reason="schemaBound")
        assert w.state()["awaitingReevaluationSince"] == marker["awaitingReevaluationSince"]
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        assert all(w.state().get(key) is None for key in MARKER)

    @staticmethod
    def _bind_during_the_run(w):
        """A binding change while the evaluation's rule runs (automatic evaluation off): its marker
        is set after the evaluation began. Like every write of the record, it increments the
        record's revision (D159)."""
        w.doubles["complianceAssetStateStorage"].update_item(
            Key={"databaseId": DB, "assetId": ASSET},
            UpdateExpression="SET awaitingReevaluationSince = :t, awaitingReevaluationReason = :r, "
                             "awaitingReevaluationSchema = :s ADD recordRevision :one",
            ExpressionAttributeValues={":t": store.now_iso(), ":r": "schemaBound",
                                       ":s": "newer-schema", ":one": 1})

    def test_a_binding_change_during_the_run_keeps_its_marker_past_a_quarantining_verdict(
            self, held):
        """D152: the verdict ran under the binding before the change, so the change's marker stays
        while the asset stays quarantined (the D118 rule, now also on the pipeline finalize)."""
        w = held("none")
        _start(w, "evaluate-now")
        self._bind_during_the_run(w)
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        state = w.state()
        assert state["complianceState"] == engine.STATE_QUARANTINED
        assert (state["awaitingReevaluationReason"], state["awaitingReevaluationSchema"]) == (
            "schemaBound", "newer-schema")
        assert _blocked() is True

    def test_a_verdict_that_ends_the_quarantine_removes_even_a_later_marker(self, held):
        w = held("none")
        _start(w, "evaluate-now")
        self._bind_during_the_run(w)
        (run,) = w.launched()
        w.end(run, output=waits.PASSING)
        state = w.state()
        assert state["complianceState"] == engine.STATE_COMPLIANT
        assert all(state.get(key) is None for key in MARKER)

    @pytest.mark.parametrize("state", [engine.STATE_COMPLIANT, engine.STATE_NON_COMPLIANT,
                                       engine.STATE_UNKNOWN])
    def test_any_other_state_reads_pending_evaluation_meanwhile(self, held, state):
        w = held("none", state=state)
        body = _evaluate_now(w)
        assert body["complianceState"] == engine.STATE_PENDING_EVALUATION
        row = w.state()
        assert row["complianceState"] == engine.STATE_PENDING_EVALUATION
        assert all(row.get(key) is None for key in MARKER)
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        row = w.state()
        assert (row["complianceState"], row["quarantinedAt"]) == (
            engine.STATE_QUARANTINED, row["updatedAt"])
        # A new quarantine is announced.
        w.notify.assert_called_once()


# --- D153: the marker is in place from the evaluation's claim on ---


def _claim_snapshots(w):
    """`(patch, seen)`: while the patch is active, the record is read right after each claim that
    lands, before the evaluation reads its schema or launches anything: what a reader polling the
    state sees from the moment the evaluation starts."""
    real = store._claim_state_row
    seen = []

    def claim(*args, **kwargs):
        landed = real(*args, **kwargs)
        if landed:
            seen.append(dict(w.state()))
        return landed
    return patch.object(store, "_claim_state_row", side_effect=claim), seen


def _synchronous_body(enforcement):
    """A schema with one asset-attribute rule the asset's name fails, and no pipeline rule."""
    return {"schemaFormat": "vams-rules-v1", "rules": {"named": {
        **waits.NAME_RULE, "enforcement": enforcement,
        "checks": [{"name": "name", "field": "assetName", "matches": "^[a-z]"}]}}}


def _write_between_read_and_claim(w, write):
    """Patch the state writes so `write(w)` lands once, right before the first marking claim: a
    change made between the claim's read of the record and its write."""
    real = store.update_asset_state
    done = []

    def update(database_id, asset_id, updates, *args, **kwargs):
        if not done and updates.get("awaitingReevaluationReason") == "evaluationPending" \
                and "complianceState" not in updates:
            done.append(True)
            write(w)
        return real(database_id, asset_id, updates, *args, **kwargs)
    return patch.object(store, "update_asset_state", side_effect=update), done


@pytest.mark.unit
class TestTheClaimMarksAQuarantinedAsset:
    """D153: the claim that starts an evaluation of a quarantined asset marks it, so the record never
    reads quarantined and pending without the awaiting-re-evaluation marker (it used to appear only
    with the pending write, after the pipeline launch: 1 to 3 s on a test deployment)."""

    @staticmethod
    def _assert_marked_at_claim(w, at_claim, evaluation_id):
        assert (at_claim["complianceState"], at_claim["lastEvaluationId"],
                at_claim["lastEvaluationStatus"]) == (
            engine.STATE_QUARANTINED, evaluation_id, engine.EVALUATION_STATUS_PENDING_PIPELINE)
        assert (at_claim["quarantineReason"], at_claim["quarantinedAt"]) == (REASON, QUARANTINED_AT)
        assert (at_claim.get("awaitingReevaluationReason"),
                at_claim.get("awaitingReevaluationSchema")) == ("evaluationPending", SCHEMA)
        assert at_claim["awaitingReevaluationSince"] == w.evaluation(evaluation_id)["evaluatedAt"]

    @pytest.mark.parametrize("entry", ENTRIES)
    def test_every_entry_point_marks_it_at_the_claim(self, held, entry):
        w = held("none")
        claim, seen = _claim_snapshots(w)
        with claim:
            evaluation_id = _start(w, entry)
        (at_claim,) = seen
        self._assert_marked_at_claim(w, at_claim, evaluation_id)
        # The pending write keeps the claim's marker as it is.
        _assert_held(w, evaluation_id)
        assert w.state()["awaitingReevaluationSince"] == at_claim["awaitingReevaluationSince"]
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        assert all(w.state().get(key) is None for key in MARKER)

    @pytest.mark.parametrize("entry", ["evaluate-now", "trigger", "cascade"])
    def test_a_rule_that_waits_is_marked_at_the_claim_too(self, held, entry):
        w = held("perAsset")
        w.hold(waits.USER_RUN)
        claim, seen = _claim_snapshots(w)
        with claim:
            evaluation_id = _start(w, entry)
        (at_claim,) = seen
        self._assert_marked_at_claim(w, at_claim, evaluation_id)
        assert w.tracking(evaluation_id)["status"] == store.PIPELINE_EXECUTION_WAITING

    @pytest.mark.parametrize("enforcement, ends_in", [
        ("quarantine", engine.STATE_QUARANTINED), ("warn", engine.STATE_NON_COMPLIANT)])
    def test_a_synchronous_evaluation_is_marked_while_it_runs_and_its_verdict_removes_it(
            self, held, enforcement, ends_in):
        w = held("none", body=_synchronous_body(enforcement))
        claim, seen = _claim_snapshots(w)
        with claim:
            body = _evaluate_now(w)
        (at_claim,) = seen
        self._assert_marked_at_claim(w, at_claim, body["evaluationId"])
        state = w.state()
        assert (state["complianceState"], state["lastEvaluationStatus"]) == (ends_in, "completed")
        assert all(state.get(key) is None for key in MARKER)
        assert w.launched() == []

    def test_an_evaluation_that_cannot_run_removes_the_claims_marker(self, held):
        w = held("none")
        claim, seen = _claim_snapshots(w)
        with claim:
            result = store.run_evaluation(DB, ASSET, "no-such-schema")
        assert result["error"] == "Schema not found"
        (at_claim,) = seen
        assert (at_claim["awaitingReevaluationReason"], at_claim["awaitingReevaluationSchema"]) == (
            "evaluationPending", "no-such-schema")
        state = w.state()
        assert (state["complianceState"], state["lastEvaluationStatus"]) == (
            engine.STATE_QUARANTINED, engine.EVALUATION_STATUS_ERROR)
        assert (state["quarantineReason"], state["quarantinedAt"]) == (REASON, QUARANTINED_AT)
        assert all(state.get(key) is None for key in MARKER)
        assert _blocked() is True

    @pytest.mark.parametrize("state", [engine.STATE_COMPLIANT, engine.STATE_NON_COMPLIANT,
                                       engine.STATE_UNKNOWN, engine.STATE_PENDING_EVALUATION])
    def test_an_asset_that_is_not_quarantined_is_not_marked(self, held, state):
        w = held("none", state=state)
        claim, seen = _claim_snapshots(w)
        with claim:
            _evaluate_now(w)
        (at_claim,) = seen
        assert at_claim["lastEvaluationStatus"] == engine.EVALUATION_STATUS_PENDING_PIPELINE
        assert all(at_claim.get(key) is None for key in MARKER)
        assert all(w.state().get(key) is None for key in MARKER)

    def test_a_binding_change_marker_is_kept_as_is_by_the_claim(self, held):
        marker = {"awaitingReevaluationSince": "2026-11-01T07:59:30+00:00",
                  "awaitingReevaluationReason": "schemaBound",
                  "awaitingReevaluationSchema": SCHEMA}
        w = held("none", marker=marker)
        claim, seen = _claim_snapshots(w)
        with claim:
            evaluation_id = _start(w, "evaluate-now")
        (at_claim,) = seen
        assert {key: at_claim.get(key) for key in MARKER} == marker
        _assert_held(w, evaluation_id, reason="schemaBound")

    def test_a_binding_change_between_the_claims_read_and_its_write_keeps_its_marker(self, held):
        w = held("none")
        between, done = _write_between_read_and_claim(w, TestOtherRecords._bind_during_the_run)
        with between:
            evaluation_id = _start(w, "evaluate-now")
        assert done
        state = w.state()
        # The claim landed on its second try, from the record as the change left it.
        assert (state["lastEvaluationId"], state["complianceState"]) == (
            evaluation_id, engine.STATE_QUARANTINED)
        assert (state["awaitingReevaluationReason"], state["awaitingReevaluationSchema"]) == (
            "schemaBound", "newer-schema")

    def test_a_release_between_the_claims_read_and_its_write_leaves_no_marker(self, held):
        """The release lands after the evaluation began, so its pending write leaves the released
        record as the release left it (D159) rather than writing `pending_evaluation` over it."""
        def release(w):
            w.doubles["complianceAssetStateStorage"].update_item(
                Key={"databaseId": DB, "assetId": ASSET},
                UpdateExpression="SET complianceState = :c, manualDecision = :d, "
                                 "manualDecisionAt = :t REMOVE quarantineReason, quarantinedAt "
                                 "ADD recordRevision :one",
                ExpressionAttributeValues={":c": engine.STATE_COMPLIANT, ":d": "released",
                                           ":t": store.now_iso(), ":one": 1})
        w = held("none")
        between, done = _write_between_read_and_claim(w, release)
        with between:
            body = _evaluate_now(w)
        assert done
        state = w.state()
        assert (state["lastEvaluationId"], state["complianceState"]) == (
            body["evaluationId"], engine.STATE_COMPLIANT)
        assert all(state.get(key) is None for key in MARKER)
