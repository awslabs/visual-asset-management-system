# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An evaluation closed because a newer evaluation of the asset began while its pipeline rule waited
for its workflow reads as superseded, not as a verdict (D125).

Evaluate Now pressed during a run leaves the older evaluation's rule waiting; the newer evaluation
closes it ("Not started: a newer evaluation of the asset began while the rule waited for its
workflow"). The older evaluation used to finalize from its other rules, so with those passing its row
read `compliant` with a rule error, a verdict the asset never had. Now its row ends with no verdict
(`status` and `verdict` `error`), `closedReason: superseded`, the fixed message "Not completed: a
newer evaluation of the asset started", every pipeline entry `completed`, no violations and no rule
listed as not evaluated; the asset state stays the newer evaluation's. Covered when the newer
evaluation closes the rule as it starts and when the launch-time ownership check closes it, under
each locking restriction. The history route returns the row as stored, so the web, the CLI and
MCP read these fields.
"""

import json
from unittest.mock import patch

import pytest

from backend.tests.handlers.compliance.test_compliancePipelineRuleWaits import (  # noqa: F401
    PASSING, RESTRICTIONS, RULE, world,
)
from common.compliance import evaluationEngine as engine
from handlers.compliance import complianceEvaluationStore as store

SUPERSEDED_TEXT = "Not completed: a newer evaluation of the asset started"


def _assert_closed_as_superseded(w, evaluation_id, newest_id):
    evaluation = w.evaluation(evaluation_id)
    assert (evaluation["status"], evaluation["verdict"]) == ("error", "error")
    assert evaluation["closedReason"] == "superseded"
    assert evaluation["errorMessage"] == SUPERSEDED_TEXT
    assert evaluation["violations"] == []
    assert evaluation["hasRuleErrors"] is False
    assert "errorRules" not in evaluation
    assert {entry["status"] for entry in evaluation["pipelineExecutions"]} == {"completed"}
    # The rule keeps its own reason in the rule results, for the history dialog.
    assert w.results(evaluation_id)[RULE]["message"] == store.PIPELINE_WAIT_SUPERSEDED_MESSAGE
    # The asset state is the newer evaluation's.
    assert w.state()["lastEvaluationId"] == newest_id
    assert w.state()["complianceState"] == engine.STATE_PENDING_EVALUATION


@pytest.mark.unit
class TestASupersededEvaluationHasNoVerdict:

    @pytest.mark.parametrize("presses", [2, 3])
    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_closed_by_the_newer_evaluation_as_it_starts(self, world, restriction, presses):
        w = world(restriction)
        w.evaluate()
        later = [w.evaluate() for _ in range(presses)]
        for older in later[:-1]:
            _assert_closed_as_superseded(w, older["evaluationId"], later[-1]["evaluationId"])

    @pytest.mark.parametrize("restriction", RESTRICTIONS)
    def test_closed_by_the_launch_time_ownership_check(self, world, restriction):
        w = world(restriction)
        w.evaluate()
        (r1,) = w.launched()
        e2 = w.evaluate()
        with patch.object(store, "settle_waiting_rules_of", return_value=0):
            e3 = w.evaluate()
        w.end(r1, output=PASSING)
        _assert_closed_as_superseded(w, e2["evaluationId"], e3["evaluationId"])

    def test_no_audit_entry_names_a_verdict_for_it(self, world):
        w = world("perAsset")
        w.evaluate()
        e2 = w.evaluate()
        w.evaluate()
        checks = [row for row in w.audits("compliance_check")
                  if row.get("evaluationId") == e2["evaluationId"]]
        # Only the entry its start wrote (pending its pipeline rule); none records a verdict.
        assert len(checks) == 1
        details = checks[0]["details"]
        details = json.loads(details) if isinstance(details, str) else details
        assert details["verdict"] == "pending_pipeline"
