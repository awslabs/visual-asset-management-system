# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validation of the execution-listing date bounds, and the binding that makes it testable.

`filterStartDate` / `filterEndDate` become DynamoDB sort-key bounds on `executionStartDate`. A sort
key comparison is a plain lexicographic string compare, not a date compare, so an unvalidated value
does not error — it silently widens the window ('0' sorts below every stored date, matching all
history) or empties it ('9999' sorts above every stored date, matching nothing).

The last test guards the harness itself: handlers bind `validate` at import, so a permissive
conftest stub makes every handler-level input check unobservable, including the ones above.

executionService resolves its table names at import (mirrors test_details_metadata_paging.py)."""

import os

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.handlers.workflows import executionService as le  # noqa: E402

# Taken off the handler module rather than imported directly: the handler resolves
# `models.common` while this file's package path would resolve `backend.backend.models.common`, and
# the two produce DISTINCT class objects — so a directly-imported one never catches what the
# handler raises.
VAMSGeneralErrorResponse = le.VAMSGeneralErrorResponse

# A value that is not a timestamp at all, plus the two that are the reason validation exists:
# '0' sorts below every stored date and '9999' above every one, so as a `.gte` bound they
# respectively return all history and nothing — neither raises without the check.
MALFORMED_BOUNDS = [
    "not-a-date",
    "0",
    "9999",
    "2026-01-01",
    "2026-01-01T00:00:00",
    "2026-13-01T00:00:00Z",
    "2026-02-30T00:00:00Z",
    "9" * 500,
    "' OR '1'='1",
]


@pytest.mark.unit
class TestListingDateBoundsAreValidated:
    """A malformed bound must 400 rather than silently reshape the query window."""

    @pytest.mark.parametrize("bound", MALFORMED_BOUNDS)
    def test_malformed_filter_start_date_is_rejected(self, bound):
        with pytest.raises(VAMSGeneralErrorResponse):
            le._resolve_filter_start_date({"filterStartDate": bound})

    @pytest.mark.parametrize("bound", MALFORMED_BOUNDS)
    def test_malformed_filter_end_date_is_rejected(self, bound):
        with pytest.raises(VAMSGeneralErrorResponse):
            le._resolve_date_filter({"filterEndDate": bound}, "filterEndDate")

    def test_the_error_does_not_echo_the_rejected_value(self):
        """Backend Rule 11: a client error carries no request input."""
        secret = "zzz-caller-supplied-zzz"
        with pytest.raises(VAMSGeneralErrorResponse) as excinfo:
            le._resolve_filter_start_date({"filterStartDate": secret})
        assert secret not in str(excinfo.value)


@pytest.mark.unit
class TestValidBoundsAreCanonicalized:
    """Accepted values reach the sort key in the exact form the rows are stored in."""

    def test_the_canonical_form_passes_through(self):
        assert le._resolve_filter_start_date(
            {"filterStartDate": "2026-01-01T00:00:00Z"}) == "2026-01-01T00:00:00Z"

    @pytest.mark.parametrize("supplied", ["2026-01-01T00:00:00.500Z",
                                          "2026-01-01T00:00:00.123456Z",
                                          "2026-01-01T00:00:00+00:00"])
    def test_tolerated_forms_are_normalized_to_the_stored_form(self, supplied):
        """'.500Z' and '+00:00' are accepted but must NOT reach the key as-is.

        '.' (0x2E) and '+' (0x2B) both sort BEFORE 'Z' (0x5A), so an un-normalized bound of
        '...T00:00:00.500Z' sorts below the stored '...T00:00:00Z' and pulls in a row the caller
        asked to exclude. Normalizing to the stored form makes the boundary exact.
        """
        assert le._resolve_filter_start_date(
            {"filterStartDate": supplied}) == "2026-01-01T00:00:00Z"

    @pytest.mark.parametrize("absent", [{}, {"filterStartDate": ""},
                                        {"filterStartDate": "   "}, {"filterStartDate": None}])
    def test_an_absent_bound_falls_back_to_the_default_window(self, absent):
        """No bound is not an error — it means the default lookback."""
        resolved = le._resolve_filter_start_date(absent)
        assert resolved.endswith("Z") and len(resolved) == 20

    def test_an_absent_end_date_is_none_rather_than_an_error(self):
        assert le._resolve_date_filter({}, "filterEndDate") is None
        assert le._resolve_date_filter({"filterEndDate": ""}, "filterEndDate") is None


@pytest.mark.unit
class TestTheGlobalListActuallyValidatesItsBounds:
    """The bounds must be validated at the CALL SITE, not merely validatable in isolation.

    Asserting on `_resolve_date_filter` alone cannot see `get_global_executions` reverting to a raw
    `.strip()`, so these drive the listing entry point itself. It is reached before any DynamoDB
    call: an invalid bound is rejected while building the key condition.
    """

    @pytest.mark.parametrize("param", ["filterStartDate", "filterEndDate"])
    def test_a_malformed_bound_fails_the_listing(self, param):
        with pytest.raises(VAMSGeneralErrorResponse):
            le.get_global_executions({}, {param: "9999"})

    @pytest.mark.parametrize("param", ["filterStartDate", "filterEndDate"])
    def test_a_widening_bound_fails_the_listing(self, param):
        """'0' sorts below every stored date, so unchecked it returns all history."""
        with pytest.raises(VAMSGeneralErrorResponse):
            le.get_global_executions({}, {param: "0"})


@pytest.mark.unit
class TestHandlersBindRealValidation:
    """Guard the harness: a permissive `validate` stub silently disables every handler-level check.

    Handlers do `from common.validators import validate` at import, which binds the function object
    itself. Overriding the attribute afterwards leaves the handler holding the original reference, so
    this must be asserted through the reference the HANDLER holds — not a fresh import of the module.
    """

    def test_the_handlers_bound_validate_actually_rejects(self):
        valid, message = le.validate(
            {"filterStartDate": {"value": "not-a-date", "validator": "ISO8601_UTC"}})
        assert valid is False, (
            "executionService.validate accepted a malformed timestamp: the test harness has "
            "replaced the dispatcher with a permissive stub, which makes every handler input "
            "check in this suite unobservable.")
        assert "filterStartDate" in message

    def test_the_bound_validate_is_not_a_lambda_stub(self):
        assert getattr(le.validate, "__name__", "") == "validate", (
            f"expected the real dispatcher, found {le.validate!r}")
