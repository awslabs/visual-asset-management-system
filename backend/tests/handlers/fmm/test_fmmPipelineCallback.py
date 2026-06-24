# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.models.fmm import (
    Tolerance,
    ToleranceOperator,
)
from backend.backend.handlers.fmm.fmmPipelineCallback import (
    _compare_tolerance,
)


class TestToleranceComparison:
    """Tests for pipeline output tolerance comparison logic."""

    def test_lte_passes_when_under(self):
        t = Tolerance(operator=ToleranceOperator.lte, value=1.0)
        passed, msg = _compare_tolerance("check", 0.5, t)
        assert passed is True
        assert msg == ""

    def test_lte_passes_when_equal(self):
        t = Tolerance(operator=ToleranceOperator.lte, value=1.0)
        passed, msg = _compare_tolerance("check", 1.0, t)
        assert passed is True

    def test_lte_fails_when_over(self):
        t = Tolerance(operator=ToleranceOperator.lte, value=1.0)
        passed, msg = _compare_tolerance("check", 1.5, t)
        assert passed is False
        assert "1.5" in msg and "1.0" in msg

    def test_gte_passes_when_over(self):
        t = Tolerance(operator=ToleranceOperator.gte, value=5.0)
        passed, msg = _compare_tolerance("check", 7.0, t)
        assert passed is True

    def test_gte_fails_when_under(self):
        t = Tolerance(operator=ToleranceOperator.gte, value=5.0)
        passed, msg = _compare_tolerance("check", 3.0, t)
        assert passed is False

    def test_eq_passes_within_epsilon(self):
        t = Tolerance(operator=ToleranceOperator.eq, value=1.0, epsilon=0.01)
        passed, msg = _compare_tolerance("check", 1.005, t)
        assert passed is True

    def test_eq_fails_outside_epsilon(self):
        t = Tolerance(operator=ToleranceOperator.eq, value=1.0, epsilon=0.001)
        passed, msg = _compare_tolerance("check", 1.01, t)
        assert passed is False

    def test_eq_default_epsilon(self):
        t = Tolerance(operator=ToleranceOperator.eq, value=1.0)
        passed, _ = _compare_tolerance("check", 1.0005, t)
        assert passed is True

    def test_between_passes_in_range(self):
        t = Tolerance(operator=ToleranceOperator.between, min=0.0, max=10.0)
        passed, msg = _compare_tolerance("check", 5.0, t)
        assert passed is True

    def test_between_passes_at_boundary(self):
        t = Tolerance(operator=ToleranceOperator.between, min=0.0, max=10.0)
        passed, _ = _compare_tolerance("check", 0.0, t)
        assert passed is True
        passed, _ = _compare_tolerance("check", 10.0, t)
        assert passed is True

    def test_between_fails_outside_range(self):
        t = Tolerance(operator=ToleranceOperator.between, min=0.0, max=10.0)
        passed, msg = _compare_tolerance("check", 11.0, t)
        assert passed is False
        assert "11.0" in msg

    def test_non_numeric_value_fails(self):
        t = Tolerance(operator=ToleranceOperator.lte, value=1.0)
        passed, msg = _compare_tolerance("check", "not-a-number", t)
        assert passed is False
        assert "not numeric" in msg
