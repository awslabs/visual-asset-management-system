# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Subscribers are notified when an asset enters quarantine, and only then, on every path (D156).

A synchronous re-evaluation of an asset that was already quarantined notified the asset's
subscribers each time its verdict quarantined it again (on a test deployment, five messages in
about four minutes of metadata edits), while a pipeline evaluation's verdict did not. Now a state
write notifies when it puts the asset into `quarantined` from any other state, judged on the state
the write itself replaced (DynamoDB returns it with the write), so:

- an evaluation that quarantines a compliant, non-compliant, pending or unknown asset notifies
  once, through every entry point (Evaluate Now, the trigger, Evaluate All, a sweep, a cascade
  node), synchronous or at the pipeline finalize, and a finalized state written late
  (`apply_finalized_state_if_missing`);
- a verdict that keeps a quarantined asset quarantined notifies no one, even with other failing
  rules or another reason (the Audit Log records them), synchronous or pipeline;
- an exception revoke that re-quarantines the asset notifies (an entry);
- a release followed by a new quarantine notifies again;
- a write that finds the asset already quarantined by another writer since its read does not
  notify a second time.

The evaluations run through the pipeline-rule World (moto tables built from the CDK definitions,
the real execute handler and workflow callback); the exception and release routes against the moto
World of `test_complianceQuarantinedAt`.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance import test_compliancePipelineRuleWaits as waits
from backend.tests.handlers.compliance.test_complianceQuarantineHeldWhilePending import (  # noqa: F401
    ENTRIES, NOTIFY, _evaluate_now, _start, _synchronous_body, held,
)
from backend.tests.handlers.compliance.test_complianceQuarantinedAt import (
    ASSET as T_ASSET, DB as T_DB, OLD, QUARANTINED, World,
)
from backend.tests.handlers.compliance._harness import ASSET, DB, SCHEMA, schema_row
from common.compliance import evaluationEngine as engine
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc

NOT_QUARANTINED = [engine.STATE_COMPLIANT, engine.STATE_NON_COMPLIANT,
                   engine.STATE_PENDING_EVALUATION, engine.STATE_UNKNOWN]


def _two_failing_rules_body():
    """The synchronous quarantining rule plus a second one, so a re-evaluation can fail with
    other rules and another reason."""
    body = _synchronous_body("quarantine")
    body["rules"]["described"] = {
        "ruleType": "assetAttributes", "enforcement": "quarantine",
        "checks": [{"name": "description", "field": "assetName", "matches": "^zzz"}]}
    return body


@pytest.mark.unit
class TestAnEntryNotifiesOnce:

    @pytest.mark.parametrize("entry", ENTRIES)
    def test_a_synchronous_verdict_through_each_entry_point(self, held, entry):
        w = held("none", state=engine.STATE_COMPLIANT, body=_synchronous_body("quarantine"))
        _start(w, entry)
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED
        w.notify.assert_called_once()

    @pytest.mark.parametrize("state", NOT_QUARANTINED)
    def test_from_every_other_state(self, held, state):
        w = held("none", state=state, body=_synchronous_body("quarantine"))
        _evaluate_now(w)
        w.notify.assert_called_once()

    @pytest.mark.parametrize("entry", ["evaluate-now", "trigger", "cascade"])
    def test_a_pipeline_verdict_at_the_finalize(self, held, entry):
        w = held("none", state=engine.STATE_COMPLIANT)
        if entry == "evaluate-now":
            _evaluate_now(w)
        else:
            _start(w, entry)
        assert w.state()["complianceState"] == engine.STATE_PENDING_EVALUATION
        w.notify.assert_not_called()
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED
        w.notify.assert_called_once()

    def test_a_finalized_state_written_late(self, held):
        w = held("none", state=engine.STATE_COMPLIANT)
        evaluation_id = _evaluate_now(w)["evaluationId"]
        self._finalize_row_only(w, evaluation_id)
        assert store.apply_finalized_state_if_missing(store.get_evaluation(evaluation_id)) is True
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED
        w.notify.assert_called_once()

    @staticmethod
    def _finalize_row_only(w, evaluation_id):
        """The evaluation row finalized as quarantined while the state write never landed."""
        w.doubles["complianceEvaluationStorage"].update_item(
            Key={"evaluationId": evaluation_id},
            UpdateExpression="SET #s = :s, verdict = :v",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "completed", ":v": "quarantined"})


@pytest.mark.unit
class TestStayingQuarantinedNotifiesNoOne:

    @pytest.mark.parametrize("entry", ENTRIES)
    def test_a_synchronous_re_evaluation_through_each_entry_point(self, held, entry):
        w = held("none", body=_synchronous_body("quarantine"))
        _start(w, entry)
        state = w.state()
        assert state["complianceState"] == engine.STATE_QUARANTINED
        # The Audit Log records the new reason; nobody is told again.
        assert state["quarantineReason"] != "residual 3 exceeds 1"
        w.notify.assert_not_called()

    def test_repeated_edits_with_other_failing_rules(self, held):
        w = held("none", body=_synchronous_body("quarantine"))
        _evaluate_now(w)
        first_reason = w.state()["quarantineReason"]
        w.doubles["complianceSchemaStorage"].put_item(
            Item=schema_row(SCHEMA, body=_two_failing_rules_body()))
        for _ in range(4):
            _evaluate_now(w)
        assert w.state()["quarantineReason"] != first_reason
        checks = [row for row in w.audits("compliance_check")
                  if (row["previousState"], row["newState"]) == ("quarantined", "quarantined")]
        assert len(checks) == 5
        w.notify.assert_not_called()

    def test_a_pipeline_re_evaluation(self, held):
        w = held("none")
        _start(w, "evaluate-now")
        (run,) = w.launched()
        w.end(run, output=waits.FAILING)
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED
        w.notify.assert_not_called()

    def test_a_finalized_state_written_late(self, held):
        w = held("none")
        evaluation_id = _start(w, "evaluate-now")
        TestAnEntryNotifiesOnce._finalize_row_only(w, evaluation_id)
        assert store.apply_finalized_state_if_missing(store.get_evaluation(evaluation_id)) is True
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED
        w.notify.assert_not_called()

    def test_a_quarantine_another_writer_made_since_the_read_is_not_announced_twice(self, held):
        """The finalize judged the asset against a read that found it pending; another writer
        quarantined it before the finalize's write. The write replaces `quarantined`, so it is no
        entry."""
        w = held("none", state=engine.STATE_COMPLIANT)
        _evaluate_now(w)
        real = store._state_for_verdict

        def judged(*args, **kwargs):
            outcome = real(*args, **kwargs)
            w.doubles["complianceAssetStateStorage"].update_item(
                Key={"databaseId": DB, "assetId": ASSET},
                UpdateExpression="SET complianceState = :q",
                ExpressionAttributeValues={":q": engine.STATE_QUARANTINED})
            return outcome
        (run,) = w.launched()
        with patch.object(store, "_state_for_verdict", side_effect=judged):
            w.end(run, output=waits.FAILING)
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED
        w.notify.assert_not_called()


@pytest.mark.unit
class TestRoutes:

    @staticmethod
    def _quarantined(world):
        world.t["complianceEvaluationStorage"].put_item(Item={
            "evaluationId": "ev-q", "databaseId": T_DB, "assetId": T_ASSET,
            "databaseId:assetId": f"{T_DB}:{T_ASSET}", "schemaName": OLD, "schemaVersion": 1,
            "evaluatedAt": QUARANTINED, "status": "completed", "verdict": "quarantined",
            "ruleResults": "[]"})
        world.t["complianceAssetStateStorage"].update_item(
            Key={"databaseId": T_DB, "assetId": T_ASSET},
            UpdateExpression="SET complianceState = :q, quarantinedAt = :t, quarantineReason = :r, "
                             "lastEvaluationId = :e, lastEvaluatedAt = :t, "
                             "lastEvaluationStatus = :c, updatedAt = :t",
            ExpressionAttributeValues={":q": "quarantined", ":t": QUARANTINED, ":r": "failed",
                                       ":e": "ev-q", ":c": "completed"})

    def test_an_exception_revoke_that_quarantines_notifies(self):
        notify = MagicMock(name="notify_quarantine")
        with World() as world, patch(NOTIFY, notify):
            self._quarantined(world)
            path, params = world.asset_path("/compliance/quarantine")
            status, _ = world.call(quarantine_svc, "POST", f"{path}/exception", params,
                                   {"reason": "accepted risk"})
            assert status == 200
            notify.assert_not_called()
            status, body = world.call(quarantine_svc, "DELETE", f"{path}/exception", params)
            assert (status, body["complianceState"]) == (200, "quarantined")
            notify.assert_called_once()

    def test_a_release_then_a_new_quarantine_notifies_again(self, held):
        w = held("none", body=_synchronous_body("quarantine"))
        _evaluate_now(w)
        w.notify.assert_not_called()
        w.doubles["complianceAssetStateStorage"].update_item(
            Key={"databaseId": DB, "assetId": ASSET},
            UpdateExpression="SET complianceState = :c REMOVE quarantineReason, quarantinedAt",
            ExpressionAttributeValues={":c": engine.STATE_COMPLIANT})
        _evaluate_now(w)
        assert w.state()["complianceState"] == engine.STATE_QUARANTINED
        w.notify.assert_called_once()
        # Still quarantined at the next edit: no second message.
        _evaluate_now(w)
        w.notify.assert_called_once()

    def test_a_release_through_the_route_then_a_new_quarantine_notifies(self):
        notify = MagicMock(name="notify_quarantine")
        with World() as world, patch(NOTIFY, notify):
            self._quarantined(world)
            path, params = world.asset_path("/compliance/quarantine")
            status, _ = world.call(quarantine_svc, "POST", f"{path}/release", params,
                                   {"reason": "checked by hand"})
            assert status == 200
            # An evaluation that began after the release (one begun before it would not be
            # applied, D159).
            later = (engine.parse_timestamp(world.state()["manualDecisionAt"])
                     + timedelta(seconds=1)).isoformat()
            with patch.object(store, "now_iso", return_value=later):
                assert store._write_evaluation_state(
                    T_DB, T_ASSET, "system", "ev-later", later, OLD, 1,
                    world.state(), engine.STATE_QUARANTINED, "completed", [], {})
            notify.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("new_state, replaced, as_read, entered", [
    (engine.STATE_QUARANTINED, {"complianceState": engine.STATE_COMPLIANT}, None, True),
    (engine.STATE_QUARANTINED, {"complianceState": engine.STATE_EXCEPTION}, None, True),
    (engine.STATE_QUARANTINED, {}, None, True),
    (engine.STATE_QUARANTINED, {"complianceState": engine.STATE_QUARANTINED},
     engine.STATE_PENDING_EVALUATION, False),
    (engine.STATE_QUARANTINED, {"complianceState": engine.STATE_PENDING_EVALUATION},
     engine.STATE_QUARANTINED, True),
    (engine.STATE_QUARANTINED, None, engine.STATE_QUARANTINED, False),
    (engine.STATE_QUARANTINED, None, engine.STATE_COMPLIANT, True),
    (engine.STATE_COMPLIANT, {"complianceState": engine.STATE_QUARANTINED}, None, False),
])
def test_entered_quarantine(new_state, replaced, as_read, entered):
    """The write's own answer decides; the read stands in only when the write reported nothing."""
    assert store.entered_quarantine(new_state, replaced, as_read) is entered
