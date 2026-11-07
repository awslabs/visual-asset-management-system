# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A release or exception grant made after an evaluation began is not undone by that evaluation,
and no write of the compliance record is computed from a stale read (D159).

Every write of the asset-state record used to be computed from an earlier read and conditioned at
most on evaluation ownership; the release, grant and revoke routes wrote with no condition at all.
So a release landing between an evaluation's read and its write was overwritten (the held pending
write put `quarantined` back with the reason the release cleared), the audit entries named a
transition that did not happen, and a release whose read saw `quarantined` could write over a
passing verdict that landed meanwhile and audit a second release. Now:

- a release or grant records the decision on the record (`manualDecision`, `manualDecisionAt`); an
  evaluation that began before it records its outcome (its row with `notApplied`, the record's
  pointers and status, a `compliance_check` entry saying not applied) and changes nothing else; no
  release entry, no notification; an evaluation that began after it decides normally;
- every write computed from a read is conditional on the record's revision as read; a lost
  condition re-reads and recomputes (evaluations) or re-checks the request's precondition (routes),
  a bounded number of times, and nothing is written blind;
- the audit entries follow the write that landed (`previousState` is the state it replaced).

The competing write is injected between the read a write is computed from and the write itself
(`_before_write`). Run against moto tables built from the CDK definitions, with compliance's real
launch, execute handler and workflow callback (`test_compliancePipelineRuleWaits.World`).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance import test_compliancePipelineRuleWaits as waits
from backend.tests.handlers.compliance.test_complianceBindingKeepsQuarantine import (
    DB as BIND_DB, NEW, World as BindingWorld,
)
from backend.tests.handlers.compliance.test_complianceQuarantineHeldWhilePending import (  # noqa: F401
    MARKER, REASON, _evaluate_now, _synchronous_body, held,
)
from backend.tests.handlers.compliance._harness import (
    ASSET, DB, SCHEMA, USER, body_of, claims_for, enforcer, rest_event,
)
from common.compliance import evaluationEngine as engine
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc

# The database bind test uses the D118 world, whose moto tables hold the apply record of a
# database binding change (conftest `no_binding_apply`).
USES_BINDING_APPLY_RECORD = True

PENDING = engine.EVALUATION_STATUS_PENDING_PIPELINE
# The record and evaluation-row fields and the answers this change adds, spelled out so the cases
# run (and fail on their assertions) against code without them; `test_the_names_are_the_stores`
# ties them to the store's and the engine's own.
MANUAL_DECISION = "manualDecision"
MANUAL_DECISION_AT = "manualDecisionAt"
REVISION = "recordRevision"
NOT_APPLIED_MESSAGES = {
    "released": "Not applied: the asset was released after this evaluation began",
    "exceptionGranted": "Not applied: an exception was granted after this evaluation began",
}
RECORD_CHANGED_MESSAGE = ("The asset's compliance record changed while the request was made; "
                          "try again")


def _route(method, action, body=None):
    """Call a quarantine route on the asset as an allowed caller; returns `(status, body)`."""
    with patch.object(quarantine_svc, "request_to_claims", claims_for(USER)), \
            patch.object(quarantine_svc, "CasbinEnforcer", return_value=enforcer()), \
            patch.object(quarantine_svc, "_enforce_asset", return_value=True):
        response = quarantine_svc.lambda_handler(rest_event(
            method, f"/compliance/quarantine/{DB}/{ASSET}/{action}",
            {"databaseId": DB, "assetId": ASSET}, body=json.dumps(body or {})), MagicMock())
    return response["statusCode"], body_of(response)


def _release():
    status, body = _route("POST", "release", {"reason": "checked by hand"})
    assert status == 200, body


def _grant():
    status, body = _route("POST", "exception", {"reason": "accepted risk"})
    assert status == 200, body


def _revoke():
    status, body = _route("DELETE", "exception")
    assert status == 200, body
    return body


DECISIONS = {"released": _release, "exceptionGranted": _grant}


def _is_claim(updates):
    return updates.get("lastEvaluationStatus") == PENDING and "complianceState" not in updates


def _is_pending_write(updates):
    return updates.get("lastEvaluationStatus") == PENDING and "complianceState" in updates


def _is_outcome_write(updates):
    return "lastEvaluationId" in updates and updates.get("lastEvaluationStatus") != PENDING


def _is_route_write(updates):
    return MANUAL_DECISION in updates


def _before_write(match, competing):
    """`(patch, done)`: while the patch is active, `competing()` runs once, right before the first
    asset-state write whose updates `match`: a change landing between the read that write was
    computed from and the write itself."""
    real = store.update_asset_state
    done = []

    def update(database_id, asset_id, updates, *args, **kwargs):
        if not done and match(updates):
            done.append(True)
            competing()
        return real(database_id, asset_id, updates, *args, **kwargs)
    return patch.object(store, "update_asset_state", side_effect=update), done


def _checks(w, evaluation_id):
    return [row for row in w.audits("compliance_check") if row.get("evaluationId") == evaluation_id]


def _assert_decision_stands(w, evaluation_id, decision):
    """The decision as the route left it, the evaluation recorded but not applied."""
    state = w.state()
    expected = (engine.STATE_COMPLIANT if decision == "released" else engine.STATE_EXCEPTION)
    assert state["complianceState"] == expected
    assert (state[MANUAL_DECISION], bool(state[MANUAL_DECISION_AT])) == (
        decision, True)
    assert (state.get("quarantineReason"), state.get("quarantinedAt")) == (None, None)
    assert all(state.get(key) is None for key in MARKER)
    if decision == "exceptionGranted":
        assert (state["exceptionGranted"], state["exceptionSchemaName"]) == (True, SCHEMA)
    # The evaluation is recorded, and the record no longer reads it as pending.
    assert (state["lastEvaluationId"], state["lastEvaluationStatus"] != PENDING) == (
        evaluation_id, True)
    row = w.evaluation(evaluation_id)
    assert (row["notApplied"], row["notAppliedMessage"]) == (
        decision, NOT_APPLIED_MESSAGES[decision])
    assert not row.get("exceptionApplied")
    # Its last entry says it was not applied and names no transition.
    final = sorted(_checks(w, evaluation_id), key=lambda entry: entry["timestamp"])[-1]
    details = json.loads(final["details"])
    assert details["notApplied"] == decision
    assert final["previousState"] == final["newState"] == expected
    # The route's own entry only: no automatic release, no notification.
    released = w.audits("quarantine_released")
    assert len(released) == (1 if decision == "released" else 0)
    if released:
        assert released[0]["previousState"] == engine.STATE_QUARANTINED
    w.notify.assert_not_called()


@pytest.mark.unit
class TestADecisionMadeWhileAnEvaluationRunsStands:
    """The decision lands between a read of the record and the write computed from it, at each
    write an evaluation makes; the evaluation began before it, so its outcome is not applied."""

    @pytest.mark.parametrize("decision", sorted(DECISIONS))
    @pytest.mark.parametrize("output", ["failing", "passing"])
    def test_between_the_claims_read_and_its_write(self, held, decision, output):
        w = held("none")
        between, done = _before_write(_is_claim, DECISIONS[decision])
        with between:
            evaluation_id = _evaluate_now(w)["evaluationId"]
        assert done
        (run,) = w.launched()
        w.end(run, output=waits.FAILING if output == "failing" else waits.PASSING)
        _assert_decision_stands(w, evaluation_id, decision)

    @pytest.mark.parametrize("decision", sorted(DECISIONS))
    def test_between_the_held_pending_writes_read_and_its_write(self, held, decision):
        w = held("none")
        between, done = _before_write(_is_pending_write, DECISIONS[decision])
        with between:
            evaluation_id = _evaluate_now(w)["evaluationId"]
        assert done
        # The pending write did not put the quarantine back while the rule runs.
        state = w.state()
        assert state["complianceState"] != engine.STATE_QUARANTINED
        assert state.get("quarantineReason") is None
        assert state["lastEvaluationStatus"] == PENDING
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        _assert_decision_stands(w, evaluation_id, decision)

    @pytest.mark.parametrize("decision", sorted(DECISIONS))
    def test_between_a_synchronous_verdicts_read_and_its_write(self, held, decision):
        w = held("none", body=_synchronous_body("quarantine"))
        between, done = _before_write(_is_outcome_write, DECISIONS[decision])
        with between:
            body = _evaluate_now(w)
        assert done
        expected = (engine.STATE_COMPLIANT if decision == "released" else engine.STATE_EXCEPTION)
        assert (body["complianceState"], body["notApplied"], body["notAppliedMessage"]) == (
            expected, decision, NOT_APPLIED_MESSAGES[decision])
        _assert_decision_stands(w, body["evaluationId"], decision)

    @pytest.mark.parametrize("decision", sorted(DECISIONS))
    @pytest.mark.parametrize("output", ["failing", "passing"])
    def test_between_the_pipeline_finalizes_read_and_its_write(self, held, decision, output):
        w = held("none")
        evaluation_id = _evaluate_now(w)["evaluationId"]
        (run,) = w.launched()
        between, done = _before_write(_is_outcome_write, DECISIONS[decision])
        with between:
            w.end(run, output=waits.FAILING if output == "failing" else waits.PASSING)
        assert done
        _assert_decision_stands(w, evaluation_id, decision)


@pytest.mark.unit
class TestTheLongWindow:
    """A decision made while a pipeline rule runs or waits, long before the verdict."""

    @pytest.mark.parametrize("decision", sorted(DECISIONS))
    def test_while_the_rule_runs_then_evaluate_now_decides(self, held, decision):
        w = held("none")
        evaluation_id = _evaluate_now(w)["evaluationId"]
        DECISIONS[decision]()
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        _assert_decision_stands(w, evaluation_id, decision)
        if decision != "released":
            return
        # An evaluation that begins after the release decides normally: a new quarantine, a new
        # start, one notification, and the decision goes.
        later = _evaluate_now(w)["evaluationId"]
        (_first, run) = w.launched()
        w.end(run, output=waits.FAILING)
        state = w.state()
        assert (state["complianceState"], state["lastEvaluationId"]) == (
            engine.STATE_QUARANTINED, later)
        assert state["quarantinedAt"] and "residual" in state["quarantineReason"]
        assert state.get(MANUAL_DECISION) is None
        assert not w.evaluation(later).get("notApplied")
        w.notify.assert_called_once()

    def test_while_the_rule_waits_behind_another_execution(self, held):
        w = held("perAsset")
        w.hold(waits.USER_RUN)
        evaluation_id = _evaluate_now(w)["evaluationId"]
        assert w.tracking(evaluation_id)["status"] == store.PIPELINE_EXECUTION_WAITING
        _release()
        w.end(waits.USER_RUN, output=None)
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        _assert_decision_stands(w, evaluation_id, "released")

    def test_a_decision_made_before_the_evaluation_began_does_not_outrank_it(self, held):
        w = held("none")
        _release()
        evaluation_id = _evaluate_now(w)["evaluationId"]
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        state = w.state()
        assert (state["complianceState"], state.get(MANUAL_DECISION)) == (
            engine.STATE_QUARANTINED, None)
        assert not w.evaluation(evaluation_id).get("notApplied")
        w.notify.assert_called_once()


@pytest.mark.unit
class TestARouteDecidesOnTheRecordAsItIsNow:

    def test_a_passing_verdict_between_a_releases_read_and_its_write_is_not_released_twice(
            self, held):
        w = held("none")

        def verdict():
            # A passing verdict of a newer evaluation lands: one automatic release entry.
            later = store.now_iso()
            assert store._write_evaluation_state(
                DB, ASSET, "system", "ev-pass", later, SCHEMA, 1, w.state(),
                engine.STATE_COMPLIANT, engine.EVALUATION_STATUS_COMPLETED, [], {})
        between, done = _before_write(_is_route_write, verdict)
        with between:
            status, body = _route("POST", "release", {"reason": "checked by hand"})
        assert done
        assert (status, body["message"]) == (400, "Asset is not quarantined")
        state = w.state()
        assert (state["complianceState"], state.get(MANUAL_DECISION)) == (
            engine.STATE_COMPLIANT, None)
        (released,) = w.audits("quarantine_released")
        assert released["evaluationId"] == "ev-pass"

    def test_a_grant_whose_asset_was_released_meanwhile_is_refused(self, held):
        w = held("none")
        between, done = _before_write(_is_route_write, _release)
        with between:
            status, body = _route("POST", "exception", {"reason": "accepted risk"})
        assert done
        assert (status, body["message"]) == (400, "Cannot grant exception: asset is not quarantined")
        assert w.state()[MANUAL_DECISION] == "released"
        assert w.audits("exception_granted") == []

    def test_a_record_that_changes_under_every_try_answers_and_writes_nothing(self, held):
        w = held("none")
        table = w.doubles["complianceAssetStateStorage"]

        def bump(*_args, **_kwargs):
            table.update_item(Key={"databaseId": DB, "assetId": ASSET},
                              UpdateExpression="ADD recordRevision :one",
                              ExpressionAttributeValues={":one": 1})
        real = store.update_asset_state

        def update(database_id, asset_id, updates, *args, **kwargs):
            bump()
            return real(database_id, asset_id, updates, *args, **kwargs)
        with patch.object(store, "update_asset_state", side_effect=update):
            status, body = _route("POST", "release", {"reason": "checked by hand"})
        assert (status, body["message"]) == (400, RECORD_CHANGED_MESSAGE)
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED
        assert w.audits("quarantine_released") == []

    def test_a_revoke_between_the_finalizes_read_and_its_write_lets_the_verdict_decide(self, held):
        """An exception granted before the evaluation began applies to it; a revoke landing
        between the finalize's read and its write returns the asset to its verdicts, and the
        finalize, reading again, applies its quarantining verdict instead of `exception`."""
        w = held("none")
        _grant()
        evaluation_id = _evaluate_now(w)["evaluationId"]
        (run,) = w.launched()
        between, done = _before_write(_is_outcome_write, _revoke)
        with between:
            w.end(run, output=waits.FAILING)
        assert done
        state = w.state()
        assert (state["complianceState"], state["exceptionGranted"]) == (
            engine.STATE_QUARANTINED, False)
        assert state.get(MANUAL_DECISION) is None
        row = w.evaluation(evaluation_id)
        assert not row.get("notApplied") and not row.get("exceptionApplied")
        final = sorted(_checks(w, evaluation_id), key=lambda entry: entry["timestamp"])[-1]
        assert (final["previousState"], final["newState"]) == (
            engine.STATE_QUARANTINED, engine.STATE_QUARANTINED)
        # The revoke's entry into quarantine notified; the verdict kept it quarantined.
        w.notify.assert_called_once()


@pytest.mark.unit
class TestOtherWritersUnderTheSameCondition:

    def test_a_binding_change_between_a_passing_verdicts_read_and_its_write_is_ended_by_it(
            self, held):
        """The verdict reads the record again and so sees the binding's marker; a verdict that ends
        the quarantine removes any marker (it used to leave one on a compliant record)."""
        from backend.tests.handlers.compliance.test_complianceQuarantineHeldWhilePending import (
            TestOtherRecords)
        w = held("none")
        evaluation_id = _evaluate_now(w)["evaluationId"]
        (run,) = w.launched()
        between, done = _before_write(
            _is_outcome_write, lambda: TestOtherRecords._bind_during_the_run(w))
        with between:
            w.end(run, output=waits.PASSING)
        assert done
        state = w.state()
        assert (state["complianceState"], state["lastEvaluationId"]) == (
            engine.STATE_COMPLIANT, evaluation_id)
        assert all(state.get(key) is None for key in MARKER)

    def test_a_binding_change_between_a_quarantining_verdicts_read_and_its_write_keeps_its_marker(
            self, held):
        from backend.tests.handlers.compliance.test_complianceQuarantineHeldWhilePending import (
            TestOtherRecords)
        w = held("none")
        _evaluate_now(w)
        (run,) = w.launched()
        between, done = _before_write(
            _is_outcome_write, lambda: TestOtherRecords._bind_during_the_run(w))
        with between:
            w.end(run, output=waits.FAILING)
        assert done
        state = w.state()
        assert state["complianceState"] == engine.STATE_QUARANTINED
        assert (state["awaitingReevaluationReason"], state["awaitingReevaluationSchema"]) == (
            "schemaBound", "newer-schema")

    def test_two_evaluations_racing_keep_the_newer_ones_outcome(self, held):
        w = held("none", body=_synchronous_body("quarantine"))

        def newer():
            # A newer evaluation claims the record between the older one's read and its write.
            assert store._claim_state_row(DB, ASSET, "ev-newer", "2099-01-01T00:00:00+00:00",
                                          w.state(), SCHEMA)
        between, done = _before_write(_is_outcome_write, newer)
        with between:
            body = _evaluate_now(w)
        assert done
        state = w.state()
        assert (state["lastEvaluationId"], state["lastEvaluationStatus"]) == ("ev-newer", PENDING)
        assert _checks(w, body["evaluationId"]) == []

    def test_a_claim_whose_record_changes_under_every_try_does_not_run(self, held):
        w = held("none")
        table = w.doubles["complianceAssetStateStorage"]
        real = store.update_asset_state

        def update(database_id, asset_id, updates, *args, **kwargs):
            table.update_item(Key={"databaseId": DB, "assetId": ASSET},
                              UpdateExpression="ADD recordRevision :one",
                              ExpressionAttributeValues={":one": 1})
            return real(database_id, asset_id, updates, *args, **kwargs)
        with patch.object(store, "update_asset_state", side_effect=update):
            result = store.run_evaluation(DB, ASSET, SCHEMA)
        assert (result["error"], result["superseded"]) == (RECORD_CHANGED_MESSAGE, False)
        assert w.launched() == []
        assert w.state()["lastEvaluationId"] == "ev-before"

    def test_a_database_bind_reaching_a_record_quarantined_since_its_read_keeps_the_quarantine(self):
        """The bind read every record first; a quarantining verdict lands on one before the bind
        reaches it. The bind used to replace it with a `pending_evaluation` record (the quarantine
        and its download block gone); it now reads it again and marks it."""
        with BindingWorld() as world:
            def quarantine():
                world.t["complianceAssetStateStorage"].update_item(
                    Key={"databaseId": BIND_DB, "assetId": "compliant"},
                    UpdateExpression="SET complianceState = :q, quarantineReason = :r "
                                     "ADD recordRevision :one",
                    ExpressionAttributeValues={":q": "quarantined", ":r": "units missing",
                                               ":one": 1})
            between, done = _before_write(
                lambda updates: updates.get("complianceState") == engine.STATE_PENDING_EVALUATION,
                quarantine)
            with between:
                status, body = world.bind_database(NEW)
            assert done and status == 202
            row = world.state("compliant")
            assert (row["complianceState"], row["quarantineReason"]) == ("quarantined",
                                                                          "units missing")
            assert (row["awaitingReevaluationReason"], row["awaitingReevaluationSchema"]) == (
                "schemaBound", NEW)
            assert body["apply"]["assetsKeptQuarantined"] == 2
            assert world.download_blocked("compliant") is True

    def test_every_write_increments_the_revision_and_a_route_write_is_conditional_on_it(
            self, held):
        w = held("none")
        before = w.state().get(REVISION)
        _release()
        after = w.state()[REVISION]
        assert after == (before or 0) + 1


def test_the_names_are_the_stores():
    assert (engine.MANUAL_DECISION_FIELD, engine.MANUAL_DECISION_AT_FIELD) == (
        MANUAL_DECISION, MANUAL_DECISION_AT)
    assert (engine.NOT_APPLIED_MESSAGES, store.RECORD_CHANGED_MESSAGE, store.RECORD_REVISION_FIELD) == (
        NOT_APPLIED_MESSAGES, RECORD_CHANGED_MESSAGE, REVISION)


def test_outranking_decision():
    """The decision outranks an evaluation only when it was made after the evaluation began."""
    row = {MANUAL_DECISION: "released", MANUAL_DECISION_AT: "2026-11-06T10:00:00+00:00"}
    assert engine.outranking_decision(row, "2026-11-06T09:59:59+00:00") == "released"
    assert engine.outranking_decision(row, "2026-11-06T10:00:00+00:00") is None
    assert engine.outranking_decision(row, "2026-11-06T10:00:01+00:00") is None
    assert engine.outranking_decision({}, "2026-11-06T09:00:00+00:00") is None
    assert engine.outranking_decision(dict(row, manualDecision="other"),
                                      "2026-11-06T09:00:00+00:00") is None
    assert engine.outranking_decision(dict(row, manualDecisionAt="not a time"),
                                      "2026-11-06T09:00:00+00:00") is None
