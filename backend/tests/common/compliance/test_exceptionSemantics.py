# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The evaluation engine's quarantine-exception helpers: when a granted exception applies to an
evaluation (same schema name and `internalVersion`), when it is superseded, the state a verdict
produces while one is active, the update that clears one, and the version coercion the stored
DynamoDB number goes through."""

from decimal import Decimal

import pytest

from common.compliance import evaluationEngine as engine
from models.compliance import EvaluationVerdict

ACTIVE = {
    "complianceState": "exception",
    "exceptionGranted": True,
    "exceptionReason": "waiver",
    "exceptionGrantedBy": "user1",
    "exceptionGrantedAt": "2026-01-01T00:00:00+00:00",
    "exceptionSchemaName": "schema-1",
    "exceptionSchemaVersion": Decimal(3),
}


@pytest.mark.unit
class TestExceptionApplies:

    @pytest.mark.parametrize("version", [3, Decimal(3), "3", 3.0], ids=["int", "decimal", "str", "float"])
    def test_the_same_schema_name_and_version_applies_whatever_the_numeric_spelling(self, version):
        assert engine.exception_applies(ACTIVE, "schema-1", version) is True
        assert engine.exception_is_superseded(ACTIVE, "schema-1", version) is False

    @pytest.mark.parametrize("schema_name,version", [
        ("schema-1", 4), ("schema-1", 2), ("schema-2", 3),
    ], ids=["newer-version", "older-version", "other-schema"])
    def test_another_schema_or_version_does_not_apply_and_supersedes(self, schema_name, version):
        assert engine.exception_applies(ACTIVE, schema_name, version) is False
        assert engine.exception_is_superseded(ACTIVE, schema_name, version) is True

    @pytest.mark.parametrize("row", [
        None, {}, {"complianceState": "quarantined"}, {"exceptionGranted": False},
        {"exceptionGranted": True, "exceptionSchemaName": "schema-1"},
        {"exceptionGranted": True, "exceptionSchemaVersion": 3},
        {"exceptionGranted": True, "exceptionSchemaName": "schema-1", "exceptionSchemaVersion": "x"},
    ], ids=["no-row", "empty", "no-exception", "revoked", "no-version", "no-name", "bad-version"])
    def test_a_row_without_a_scoped_exception_neither_applies_nor_is_superseded_unless_granted(self, row):
        assert engine.exception_applies(row, "schema-1", 3) is False
        assert engine.exception_is_superseded(row, "schema-1", 3) is bool((row or {}).get("exceptionGranted"))

    def test_an_unknown_evaluated_version_never_applies(self):
        assert engine.exception_applies(ACTIVE, "schema-1", None) is False


@pytest.mark.unit
class TestExceptionState:

    @pytest.mark.parametrize("verdict,state", [
        (EvaluationVerdict.compliant, "compliant"),
        (EvaluationVerdict.non_compliant, "exception"),
        (EvaluationVerdict.quarantined, "exception"),
        (EvaluationVerdict.pending_pipeline, "pending_evaluation"),
        (EvaluationVerdict.error, "unknown"),
    ])
    def test_a_failing_verdict_is_the_exception_state_and_the_rest_map_as_usual(self, verdict, state):
        assert engine.exception_state(verdict) == state

    def test_the_exception_state_is_a_distinct_stored_state(self):
        assert engine.STATE_EXCEPTION == "exception"
        assert engine.STATE_EXCEPTION not in engine._VERDICT_TO_STATE.values()


@pytest.mark.unit
class TestClearedFields:

    def test_the_update_clears_every_exception_attribute(self):
        cleared = engine.cleared_exception_fields()
        assert set(cleared) == set(ACTIVE) - {"complianceState"}
        assert cleared["exceptionGranted"] is False
        assert all(cleared[field] is None for field in cleared if field != "exceptionGranted")
        assert engine.exception_applies({**ACTIVE, **cleared}, "schema-1", 3) is False


@pytest.mark.unit
class TestSchemaVersionNumber:

    @pytest.mark.parametrize("value,expected", [
        (1, 1), (Decimal(7), 7), ("12", 12), (2.0, 2), (None, None), ("", None), ("v1", None),
        (True, None),
    ])
    def test_stored_spellings_coerce_to_an_int_or_none(self, value, expected):
        assert engine.schema_version_number(value) == expected
