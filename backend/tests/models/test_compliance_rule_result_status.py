# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""models.compliance.RuleResult: the `status` a rule result carries -- `evaluated` (the checks ran)
or `error` (the tooling could not evaluate the rule) -- its default, its vocabulary, and its
round trip through the stored JSON shape."""

import pytest

from backend.backend.models.compliance import (
    RULE_STATUS_ERROR, RULE_STATUS_EVALUATED, RULE_STATUSES, RuleResult,
)


def _result(**overrides):
    fields = {"ruleName": "r", "ruleType": "pipeline", "enforcement": "quarantine", "passed": False}
    fields.update(overrides)
    return RuleResult(**fields)


@pytest.mark.unit
class TestRuleResultStatus:

    def test_the_vocabulary(self):
        assert RULE_STATUSES == ("evaluated", "error")
        assert RULE_STATUS_EVALUATED == "evaluated"
        assert RULE_STATUS_ERROR == "error"

    def test_a_result_without_a_status_is_evaluated(self):
        assert _result().status == RULE_STATUS_EVALUATED
        assert _result().dict()["status"] == "evaluated"

    def test_an_error_status_is_kept(self):
        result = _result(status="error", message="Pipeline execution could not be started")
        assert result.status == RULE_STATUS_ERROR
        assert result.passed is False

    @pytest.mark.parametrize("status", ["failed", "ERROR", "", "evaluated "])
    def test_anything_outside_the_vocabulary_is_refused(self, status):
        with pytest.raises(ValueError):
            _result(status=status)

    def test_the_status_constraint_is_live(self):
        field = RuleResult.__fields__["status"].field_info
        assert field.regex is not None
        assert not field.extra

    def test_a_stored_result_round_trips_its_status(self):
        stored = _result(status="error").dict()
        assert RuleResult(**stored).status == "error"
        legacy = {"ruleName": "r", "ruleType": "metadata", "enforcement": "warn", "passed": True}
        assert RuleResult(**legacy).status == "evaluated"
