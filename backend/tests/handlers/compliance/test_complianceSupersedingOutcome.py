# SPDX-License-Identifier: Apache-2.0

"""#1504: a SUCCEEDED of the same execution supersedes the FAILED a pipeline rule recorded.

A workflow execution can announce its end twice: overlapping attempts of its end-state output step
(#1271) record FAILED and then SUCCEEDED on the execution's main row, and each announces it. The
callback records the rule FAILED on the first and, on the second, replaces that outcome and finalizes
the evaluation again through the store's own finalize. Only the same execution, only FAILED to
SUCCEEDED, never on a closed evaluation, idempotent on redelivery, and a release granted after the
evaluation began still outranks the new outcome."""

import json

import pytest

from backend.tests.handlers.compliance._harness import update_values
from backend.tests.handlers.compliance.test_complianceWorkflowCallback import (
    EXECUTION_ID, UNKNOWN_EXECUTION_ID, Callback, _pending_evaluation, completion_event,
)

pytestmark = pytest.mark.unit

TRACKING_ID = "eval-1#residual-bound"


def _body(response):
    body = response["body"]
    return json.loads(body) if body.startswith("{") else body


def _rows(harness):
    return harness.evaluation_rows.rows


def _failed_then(harness):
    """The first announcement: the rule FAILED, the evaluation finalized quarantined."""
    first = _body(harness.run(completion_event("FAILED")))
    assert first["finalized"] is True and first["verdict"] == "quarantined"
    assert _rows(harness)[TRACKING_ID]["executionStatus"] == "FAILED"
    return first


def test_regression_the_superseding_succeeded_finalizes_the_evaluation_again():
    # On development the second announcement answers "Already processed" and the rule stays FAILED.
    harness = Callback()
    _failed_then(harness)
    outcome = _body(harness.run(completion_event("SUCCEEDED")))
    assert outcome["superseded"] is True
    assert outcome["finalized"] is True
    assert outcome["verdict"] == "compliant"
    tracking = _rows(harness)[TRACKING_ID]
    assert tracking["executionStatus"] == "SUCCEEDED"
    assert tracking["supersededExecutionStatus"] == "FAILED"
    assert json.loads(tracking["ruleResults"])[0]["passed"] is True
    parent = _rows(harness)["eval-1"]
    assert parent["status"] == "completed" and parent["verdict"] == "compliant"
    assert update_values(harness.state)[-1]["complianceState"] == "compliant"


def test_a_redelivered_superseding_succeeded_changes_nothing_more():
    harness = Callback()
    _failed_then(harness)
    harness.run(completion_event("SUCCEEDED"))
    writes = harness.state.update_item.call_count
    finalized = len(harness.finalized())
    again = _body(harness.run(completion_event("SUCCEEDED")))
    assert again == "Already processed"
    assert harness.state.update_item.call_count == writes
    assert len(harness.finalized()) == finalized


def test_a_failed_never_replaces_a_succeeded():
    harness = Callback()
    first = _body(harness.run(completion_event("SUCCEEDED")))
    assert first["verdict"] == "compliant"
    again = _body(harness.run(completion_event("FAILED")))
    assert again == "Already processed"
    assert _rows(harness)[TRACKING_ID]["executionStatus"] == "SUCCEEDED"
    assert _rows(harness)["eval-1"]["verdict"] == "compliant"


def test_a_redelivered_failed_after_the_supersede_keeps_the_succeeded():
    harness = Callback()
    _failed_then(harness)
    harness.run(completion_event("SUCCEEDED"))
    assert _body(harness.run(completion_event("FAILED"))) == "Already processed"
    assert _rows(harness)[TRACKING_ID]["executionStatus"] == "SUCCEEDED"
    assert _rows(harness)["eval-1"]["verdict"] == "compliant"


def test_another_executions_succeeded_never_touches_the_rule():
    # The harness's index lookup answers this evaluation for any id; the tracking row names its own.
    harness = Callback()
    _failed_then(harness)
    other = _body(harness.run(completion_event("SUCCEEDED", execution_id=UNKNOWN_EXECUTION_ID)))
    assert other == "Already processed"
    assert _rows(harness)[TRACKING_ID]["executionStatus"] == "FAILED"
    assert _rows(harness)["eval-1"]["verdict"] == "quarantined"


def test_a_closed_evaluation_is_not_reopened():
    harness = Callback()
    _failed_then(harness)
    _rows(harness)["eval-1"]["closedReason"] = "superseded"
    assert _body(harness.run(completion_event("SUCCEEDED"))) == "Already processed"
    assert _rows(harness)[TRACKING_ID]["executionStatus"] == "FAILED"


def test_a_release_granted_after_the_evaluation_began_still_outranks_it():
    harness = Callback(evaluation=dict(_pending_evaluation(), evaluatedAt="2026-01-01T00:00:00Z"))
    _failed_then(harness)
    released = {"complianceState": "compliant", "lastEvaluationId": "eval-1",
                "lastEvaluatedAt": "2026-01-01T00:00:00Z", "lastEvaluationStatus": "completed",
                "manualDecision": "released", "manualDecisionAt": "2026-01-01T00:05:00Z"}
    harness.state.get_item.return_value = {"Item": dict(released)}
    outcome = _body(harness.run(completion_event("SUCCEEDED")))
    assert outcome["superseded"] is True and outcome["finalized"] is True
    assert outcome.get("notApplied") == "released"
    # The state write records the evaluation pointers only: no state change of its own.
    assert "complianceState" not in update_values(harness.state)[-1]


def test_a_supersede_interrupted_after_the_reopen_completes_on_redelivery():
    # The reopen landed, the outcome replacement did not (the invocation failed and is retried).
    harness = Callback()
    _failed_then(harness)
    _rows(harness)["eval-1"]["status"] = "pending_pipeline"
    outcome = _body(harness.run(completion_event("SUCCEEDED")))
    assert outcome["superseded"] is True and outcome["verdict"] == "compliant"
    assert _rows(harness)[TRACKING_ID]["executionStatus"] == "SUCCEEDED"


def test_a_reopened_evaluation_carries_none_of_the_superseded_finalization_fields():
    harness = Callback()
    _failed_then(harness)
    _rows(harness)["eval-1"].update({"exceptionApplied": True, "errorRules": ["x"]})
    harness.run(completion_event("SUCCEEDED"))
    parent = _rows(harness)["eval-1"]
    assert "exceptionApplied" not in parent and "errorRules" not in parent
    assert parent["hasRuleErrors"] is False
    assert parent["violations"] == []
