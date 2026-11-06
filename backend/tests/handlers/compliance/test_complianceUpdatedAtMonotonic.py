# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A compliance record's `updatedAt` never moves backwards (D155).

The evaluation's state writes stamped `updatedAt` with the evaluation's start (`evaluatedAt`) or a
time taken before the work that precedes the write, so on a test deployment the record read
18:42:34.838Z at the claim and .814Z after the pending write, once per evaluation. Each write now
stamps the time of the write itself: the claim, the pending write, a synchronous verdict, an
evaluation that cannot run and the pipeline finalize; a binding change stamps each record it
reaches when it writes it; and the exception, revoke and asset-binding routes take their time after
the reads they depend on. `lastEvaluatedAt` stays the evaluation's `evaluatedAt`.

The evaluations run through the pipeline-rule World (moto tables built from the CDK definitions,
the real execute handler and workflow callback) on a clock that advances one millisecond per read
of it; the routes run against the moto World of `test_complianceQuarantinedAt` with a write that
lands while the route is between its reads and its own write.
"""

import itertools
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.tests.handlers.compliance import test_compliancePipelineRuleWaits as waits
from backend.tests.handlers.compliance.test_complianceQuarantineHeldWhilePending import (  # noqa: F401
    _evaluate_now, _start, _synchronous_body, held,
)
from backend.tests.handlers.compliance.test_complianceQuarantinedAt import (
    ASSET as T_ASSET, DB as T_DB, NEW, OLD, QUARANTINED, World,
)
from backend.tests.handlers.compliance._harness import ASSET, DB
from common.compliance import evaluationEngine as engine
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc
from handlers.compliance import complianceSchemaBindingService as binding_svc


def _ticking_clock():
    """A `now_iso` that moves forward one millisecond each time it is read."""
    start = datetime.now(timezone.utc)
    ticks = itertools.count()
    return lambda: (start + timedelta(milliseconds=next(ticks))).isoformat()


def _recording(w):
    """`(patch, seen)`: while active, the record's `updatedAt` after each state write."""
    real = store.update_asset_state
    seen = []

    def update(*args, **kwargs):
        result = real(*args, **kwargs)
        seen.append(w.state()["updatedAt"])
        return result
    return patch.object(store, "update_asset_state", side_effect=update), seen


def _assert_never_backwards(stamps):
    assert len(stamps) >= 2
    assert stamps == sorted(stamps), stamps


@pytest.mark.unit
class TestEvaluationWrites:

    @pytest.mark.parametrize("entry", ["evaluate-now", "trigger", "cascade"])
    @pytest.mark.parametrize("state", [engine.STATE_QUARANTINED, engine.STATE_COMPLIANT])
    def test_claim_pending_write_and_finalize(self, held, entry, state):
        w = held("none", state=state)
        recording, seen = _recording(w)
        with patch.object(store, "now_iso", side_effect=_ticking_clock()), recording:
            if entry == "evaluate-now":
                evaluation_id = _evaluate_now(w)["evaluationId"]
            else:
                evaluation_id = _start(w, entry)
            # The claim and the pending write.
            assert len(seen) >= 2
            (run,) = w.launched()
            w.end(run, output=waits.FAILING)
        _assert_never_backwards(seen)
        assert len(seen) >= 3
        # `lastEvaluatedAt` is still the evaluation's start.
        record = w.state()
        assert record["lastEvaluatedAt"] == w.evaluation(evaluation_id)["evaluatedAt"]
        assert record["lastEvaluatedAt"] < seen[0]

    def test_a_waiting_rule(self, held):
        w = held("perAsset")
        w.hold(waits.USER_RUN)
        recording, seen = _recording(w)
        with patch.object(store, "now_iso", side_effect=_ticking_clock()), recording:
            _start(w, "evaluate-now")
            w.end(waits.USER_RUN, output=None)
            (run,) = w.launched()
            w.end(run, output=waits.PASSING)
        _assert_never_backwards(seen)
        assert w.state()["complianceState"] == engine.STATE_COMPLIANT

    @pytest.mark.parametrize("enforcement", ["quarantine", "warn"])
    def test_a_synchronous_verdict(self, held, enforcement):
        w = held("none", body=_synchronous_body(enforcement))
        recording, seen = _recording(w)
        with patch.object(store, "now_iso", side_effect=_ticking_clock()), recording:
            _evaluate_now(w)
        _assert_never_backwards(seen)
        assert len(seen) == 2

    def test_a_new_quarantine_starts_at_its_write(self, held):
        w = held("none", state=engine.STATE_COMPLIANT, body=_synchronous_body("quarantine"))
        recording, seen = _recording(w)
        with patch.object(store, "now_iso", side_effect=_ticking_clock()), recording:
            body = _evaluate_now(w)
        record = w.state()
        assert record["complianceState"] == engine.STATE_QUARANTINED
        assert record["quarantinedAt"] == record["updatedAt"] == seen[-1]
        assert record["quarantinedAt"] > w.evaluation(body["evaluationId"])["evaluatedAt"]

    def test_an_evaluation_that_cannot_run(self, held):
        w = held("none")
        recording, seen = _recording(w)
        with patch.object(store, "now_iso", side_effect=_ticking_clock()), recording:
            result = store.run_evaluation(DB, ASSET, "no-such-schema")
        assert result["error"] == "Schema not found"
        _assert_never_backwards(seen)
        assert w.state()["lastEvaluationStatus"] == engine.EVALUATION_STATUS_ERROR


def _write_meanwhile(world):
    """Another writer (an evaluation's claim) stamping the record now; returns its time."""
    stamp = store.now_iso()
    world.t["complianceAssetStateStorage"].update_item(
        Key={"databaseId": T_DB, "assetId": T_ASSET},
        UpdateExpression="SET updatedAt = :t", ExpressionAttributeValues={":t": stamp})
    return stamp


def _after_reading(target, name, world, stamps):
    """Patch `target.name` so a write lands right after the route reads through it."""
    real = getattr(target, name)

    def read(*args, **kwargs):
        result = real(*args, **kwargs)
        stamps.append(_write_meanwhile(world))
        return result
    return patch.object(target, name, side_effect=read)


@pytest.mark.unit
class TestRouteWrites:
    """A write that lands while a route is between its reads and its own write is not stepped
    back over: the route's `updatedAt` is taken after those reads."""

    def _quarantined(self, world):
        """The record as a quarantining evaluation (`ev-q`) left it, written straight to the
        tables."""
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

    def test_binding_a_database_reaches_each_record_at_its_own_time(self):
        with World() as world:
            self._quarantined(world)
            stamps = []
            with _after_reading(binding_svc, "_database_asset_state_rows", world, stamps):
                status, _ = world.call(binding_svc, "PUT", f"/compliance/bind/{T_DB}",
                                       {"databaseId": T_DB}, {"schemaName": NEW})
            assert status == 200 and stamps
            row = world.state()
            assert row["awaitingReevaluationReason"] == "schemaBound"
            assert row["updatedAt"] >= stamps[-1]

    def test_removing_a_database_binding(self):
        with World() as world:
            self._quarantined(world)
            stamps = []
            with _after_reading(binding_svc, "_database_asset_state_rows", world, stamps):
                status, _ = world.call(binding_svc, "DELETE", f"/compliance/bind/{T_DB}",
                                       {"databaseId": T_DB})
            assert status == 200 and stamps
            row = world.state()
            assert row["awaitingReevaluationReason"] == "schemaUnbound"
            assert row["updatedAt"] >= stamps[-1]

    def test_setting_and_clearing_an_asset_override(self):
        with World() as world:
            self._quarantined(world)
            path, params = world.asset_path("/compliance/bind")
            stamps = []
            with _after_reading(store, "get_compliance_record", world, stamps):
                status, _ = world.call(binding_svc, "PUT", path, params, {"schemaName": NEW})
            assert status == 200 and stamps
            assert world.state()["updatedAt"] >= stamps[-1]
            stamps = []
            with _after_reading(world.t["databaseStorage"], "get_item", world, stamps):
                status, _ = world.call(binding_svc, "DELETE", path, params)
            assert status == 200 and stamps
            assert world.state()["updatedAt"] >= stamps[-1]

    def test_granting_and_revoking_an_exception(self):
        with World() as world:
            self._quarantined(world)
            path, params = world.asset_path("/compliance/quarantine")
            stamps = []
            with _after_reading(quarantine_svc, "_current_schema_version", world, stamps):
                status, _ = world.call(quarantine_svc, "POST", f"{path}/exception", params,
                                       {"reason": "accepted risk"})
            assert status == 200 and stamps
            row = world.state()
            assert row["updatedAt"] >= stamps[-1]
            assert row["exceptionGrantedAt"] == row["updatedAt"]
            stamps = []
            with _after_reading(store, "get_evaluation", world, stamps):
                status, body = world.call(quarantine_svc, "DELETE", f"{path}/exception", params)
            assert (status, body["complianceState"]) == (200, "quarantined") and stamps
            row = world.state()
            assert row["updatedAt"] >= stamps[-1]
            assert row["quarantinedAt"] == row["updatedAt"]
