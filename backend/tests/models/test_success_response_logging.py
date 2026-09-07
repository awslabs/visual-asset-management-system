# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-007 -- ``success()`` must not interpolate the response body into a log line.

``models/common.py`` once logged ``logger.info(f"Success response: {body}")``. ``success()`` is the
single response helper for ~152 ``return success(`` sites, which makes its log statement the most
broadly reached in the backend -- and the only place the newly minted plaintext API key exists in a
loggable form (``apiKeyService`` puts ``raw_key`` into the response item and hands it straight to
``success()``).

Redaction in ``safeLogger`` is KEY-driven: ``mask_sensitive_data`` walks a dict looking for sensitive
key names. An f-string has already collapsed the dict into text by the time the formatter sees it, so
the interpolation bypasses redaction entirely. That is why extending ``SENSITIVE_KEYS`` alone is a
no-op for this leak, and why both halves are pinned below: the body must stop being interpolated, AND
the key names must be redacted for defence in depth once a body can be walked.

TEST-VACUITY TRAP this file is written around: the backend suite replaces
``customLogging.logger.safeLogger`` with a no-op whose ``info()`` does ``pass`` (tests/conftest.py), so
any ``caplog``-based "the key is not in the log" assertion passes today, before any fix. These tests
therefore spy on the logger OBJECT the module actually holds and assert on the recorded call arguments,
and each spy assertion is preceded by a detector control proving the same scan finds a secret when one
really is logged.

The same defect class lived in the four sibling helpers -- ``validation_error``, ``general_error``,
``authorization_error`` and ``internal_error`` all logged ``logger.error(f"...: {body}")`` -- and is
pinned below too. Their bodies resolve differently from ``success()``: an error body is always
``{'message': <authored string>}``, which is the diagnostic an operator needs, so it is kept in the log
but passed as structured data where ``mask_sensitive_data`` can walk it, rather than dropped.

``success()`` keeps the drop, and logs the serialized SIZE alongside the status code so the INFO record
still says something. A masked full body was rejected: redaction is key-driven, and a presigned URL or
an asset name sits under a key no list can enumerate, so walking the body would reopen most of the leak
across ~152 call sites.
"""

import inspect
import json
import re
from decimal import Decimal

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.models import common as response_models
from backend.backend.customLogging.logger import (
    mask_sensitive_data,
    REDACTED,
    SENSITIVE_KEYS,
)

_SECRET = "vams_SECRETVALUE_do_not_log"

_API_KEY_RESPONSE_BODY = {
    "apiKeyId": "11111111-1111-4111-8111-111111111111",
    "apiKeyName": "my-key",
    "description": "d",
    "userId": "u1",
    "createdBy": "u1",
    "expiresAt": "2027-01-01T00:00:00Z",
    "isActive": "true",
    "apiKey": _SECRET,
}

_RESPONSE_HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-cache, no-store",
    "Access-Control-Allow-Origin": "*",
}

# (helper name, status code, default message) for the four error helpers.
_ERROR_HELPERS = (
    ("validation_error", 400, "Validation Error"),
    ("general_error", 400, "VAMS General Error"),
    ("authorization_error", 403, "Not Authorized"),
    ("internal_error", 500, "Internal Server Error"),
)

# Powertools builds the log record from these names, so an `extra` entry sharing one clobbers the
# record's own field.
_POWERTOOLS_RESERVED = {"message", "level", "location", "timestamp", "service", "exception"}


def _recorded_text(spy):
    """Every argument of every call recorded on a MagicMock logger, as one searchable string."""
    return "\n".join(repr(call) for call in spy.mock_calls)


@pytest.mark.unit
class TestSuccessDoesNotLogTheBody:
    """The response body must not reach the log through string interpolation."""

    def test_a_plaintext_api_key_in_the_body_is_never_recorded_on_the_logger(self):
        spy = MagicMock()
        with patch.object(response_models, "logger", spy):
            # DETECTOR CONTROL: the same scan, over the same spy object that success() resolves by
            # name, finds a secret when one is genuinely logged. This is what makes the assertion
            # below non-vacuous under the suite's no-op safeLogger.
            spy.info(f"control line carrying the body: {_API_KEY_RESPONSE_BODY}")
            assert _SECRET in _recorded_text(spy), "the spy/scan pair cannot see a logged secret"
            spy.reset_mock()

            response = response_models.success(body=dict(_API_KEY_RESPONSE_BODY))

        # POSITIVE CONTROL: the key is still returned to the caller. It is stored only as a
        # SHA-256, so a fix that drops it from the response makes the key unrecoverable.
        assert json.loads(response["body"])["apiKey"] == _SECRET
        assert _SECRET not in _recorded_text(spy)

    def test_the_whole_response_body_is_not_rendered_into_a_log_message(self):
        spy = MagicMock()
        body = {"Items": [{"assetId": "a1", "assetName": "Confidential Turbine Housing"}]}
        with patch.object(response_models, "logger", spy):
            spy.info(f"control line carrying the body: {body}")
            assert "Confidential Turbine Housing" in _recorded_text(spy)
            spy.reset_mock()
            response_models.success(body=body)
        assert "Confidential Turbine Housing" not in _recorded_text(spy)

    def test_success_source_contains_no_f_string_interpolation_of_the_body(self):
        """Pinned at zero occurrences so the line cannot be reintroduced by a later merge."""
        source = inspect.getsource(response_models.success)
        assert self._interpolating_log_lines(source) == []

    def test_the_interpolation_guard_flags_the_old_line(self):
        """CONTROL for the guard above: the same regex, applied to the line being removed, flags it.
        Without this the guard could be passing because the pattern matches nothing at all."""
        old = 'def success(status_code=200, body=None):\n    logger.info(f"Success response: {body}")\n'
        assert self._interpolating_log_lines(old) == ['logger.info(f"Success response: {body}")']
        benign = '    logger.debug("Success response", extra={"statusCode": status_code})\n'
        assert self._interpolating_log_lines(benign) == []

    @staticmethod
    def _interpolating_log_lines(source):
        pattern = re.compile(r"""logger\.\w+\(\s*f["'][^"']*\{\s*body\b[^"']*["']\s*\)""")
        return [match.group(0) for match in pattern.finditer(source)]


@pytest.mark.unit
class TestSuccessResponseUnchanged:
    """OVER-TIGHTENING CATCHER. Only the log line may move -- the bytes on the wire must not."""

    def test_response_shape_and_body_bytes_are_byte_identical(self):
        body = {
            "assetSize": Decimal("1024"),
            "ratio": Decimal("1.5"),
            "nested": [{"count": Decimal("2")}, "text"],
        }
        response = response_models.success(body=body)
        assert response["isBase64Encoded"] is False
        assert response["statusCode"] == 200
        assert response["headers"] == {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache, no-store",
            "Access-Control-Allow-Origin": "*",
        }
        assert response["body"] == (
            '{"assetSize": 1024, "ratio": 1.5, "nested": [{"count": 2}, "text"]}')

    def test_default_body_and_explicit_status_code_preserved(self):
        response = response_models.success(status_code=201)
        assert response["statusCode"] == 201
        assert json.loads(response["body"]) == {"message": "Success"}

    def test_no_log_call_puts_a_powertools_reserved_key_in_extra(self):
        """FORWARD GUARD -- passes today and must keep passing.

        VAMS responses are routinely ``{"message": {...}}`` (the pipeline/workflow/execution
        envelope). If the body is handed to the logger as a bare ``extra=body`` so masking can walk it,
        ``message`` clobbers the log record's own message and the log TEXT disappears; the body has to
        be nested under a key of its own instead.
        """
        reserved = _POWERTOOLS_RESERVED
        spy = MagicMock()
        envelope = {"message": {"Items": [{"pipelineId": "p1"}], "NextToken": "t"}}
        with patch.object(response_models, "logger", spy):
            response = response_models.success(body=envelope)
        for call in spy.mock_calls:
            collision = set(call.kwargs.get("extra") or {}) & reserved
            assert not collision, f"log call passes reserved powertools key(s) {collision} in extra"
        assert json.loads(response["body"]) == envelope


@pytest.mark.unit
class TestSensitiveKeyRedactionDefenceInDepth:
    """Once a body can be walked, the plaintext-key field names must be redacted too."""

    @pytest.mark.parametrize("key", ["apiKey", "apiKeySecret", "rawKey"])
    def test_plaintext_key_fields_are_redacted_at_every_depth(self, key):
        assert mask_sensitive_data({key: _SECRET})[key] == REDACTED
        nested = mask_sensitive_data({"a": {"b": {key: _SECRET}}})
        assert nested["a"]["b"][key] == REDACTED
        in_list = mask_sensitive_data({"Items": [{"apiKeyId": "k1", key: _SECRET}]})
        assert in_list["Items"][0][key] == REDACTED
        assert in_list["Items"][0]["apiKeyId"] == "k1"
        in_body = mask_sensitive_data({"body": json.dumps({"apiKeyName": "n", key: _SECRET})})
        assert _SECRET not in in_body["body"]
        assert json.loads(in_body["body"]) == {"apiKeyName": "n", key: REDACTED}

    def test_api_key_identifier_fields_are_not_redacted(self):
        """CONTROL: the match is whole-key and case-insensitive, so the adjacent identifier fields
        survive. They are what makes the API-key audit trail readable, and a blanket substring
        redaction would silently blind it."""
        record = {"apiKeyId": "k1", "apiKeyName": "n", "apiKeyHash": "h", "userId": "u1"}
        assert mask_sensitive_data(dict(record)) == record

    def test_credential_keys_still_listed(self):
        """CONTROL: the existing entries are not dropped while adding the new ones."""
        for key in ("authorization", "idJwtToken", "Credentials", "AccessKeyId",
                    "SecretAccessKey", "SessionToken"):
            assert key in SENSITIVE_KEYS


@pytest.mark.unit
class TestSuccessStillLogsANonValueSummary:
    """FIX-007 -- dropping the body must not cost every INFO-level diagnostic.

    The removed line was the only INFO record of what a response carried. Logging a MASKED body back
    is not the answer: redaction is key-driven, so a presigned URL (signature in the query string) and
    an asset name sit under keys no list enumerates, and walking the body would reopen most of the leak
    across ~152 call sites. The serialized SIZE is logged instead -- an integer, so no value can travel
    in it, and the number an operator compares against the 6 MB Lambda response limit (backend Rule 15).
    """

    def test_the_serialized_size_is_logged_and_matches_the_returned_body(self):
        spy = MagicMock()
        body = {"Items": [{"assetId": "a1", "assetName": "Confidential Turbine Housing"}]}
        with patch.object(response_models, "logger", spy):
            response = response_models.success(body=body)
        extra = spy.info.call_args.kwargs["extra"]
        assert extra["statusCode"] == 200
        assert extra["bodyBytes"] == len(response["body"])
        # Every logged value is a number, so nothing from the body can ride along inside one.
        assert all(isinstance(value, int) for value in extra.values())

    def test_the_summary_carries_no_value_from_the_body(self):
        spy = MagicMock()
        body = dict(_API_KEY_RESPONSE_BODY, assetName="Confidential Turbine Housing")
        with patch.object(response_models, "logger", spy):
            spy.info(f"control line carrying the body: {body}")
            assert _SECRET in _recorded_text(spy), "the spy/scan pair cannot see a logged secret"
            spy.reset_mock()
            response_models.success(body=body)
        recorded = _recorded_text(spy)
        assert _SECRET not in recorded
        assert "Confidential Turbine Housing" not in recorded

    def test_the_size_is_a_byte_count_for_non_ascii_content(self):
        """``json.dumps`` escapes non-ASCII by default, so the serialized body is ASCII-only and its
        character count IS its byte count. A future ``ensure_ascii=False`` would make the logged
        number smaller than the payload it is meant to bound."""
        spy = MagicMock()
        with patch.object(response_models, "logger", spy):
            response = response_models.success(body={"assetName": "Türbine 中"})
        logged = spy.info.call_args.kwargs["extra"]["bodyBytes"]
        assert logged == len(response["body"].encode("utf-8"))


@pytest.mark.unit
class TestErrorHelpersDoNotInterpolateTheBody:
    """FIX-007 sibling helpers -- the same defect class as ``success()``, in four more places.

    ``validation_error``, ``general_error``, ``authorization_error`` and ``internal_error`` each logged
    ``logger.error(f"...: {body}")``. Every current call site passes ``{'message': <authored string>}``,
    so no credential is known to leak through them today -- but the f-string collapses the dict before
    ``mask_sensitive_data`` can walk it, which makes redaction structurally impossible for all ~1060
    call sites, and ``general_error`` / ``internal_error`` accept an arbitrary body. The message is the
    diagnostic Rule 11 wants in the log, so it stays -- as structured data that masking can reach.
    """

    @pytest.mark.parametrize("name", [helper[0] for helper in _ERROR_HELPERS])
    def test_source_contains_no_f_string_interpolation_of_the_body(self, name):
        """Pinned at zero occurrences per helper. The control proving this same regex flags the removed
        line is ``TestSuccessDoesNotLogTheBody.test_the_interpolation_guard_flags_the_old_line``."""
        source = inspect.getsource(getattr(response_models, name))
        offenders = TestSuccessDoesNotLogTheBody._interpolating_log_lines(source)
        assert offenders == [], f"{name} still interpolates the body: {offenders}"

    @pytest.mark.parametrize("name, status, _default", _ERROR_HELPERS)
    def test_the_body_is_logged_where_masking_can_reach_it(self, name, status, _default):
        helper = getattr(response_models, name)
        body = {"message": "Invalid request.", "apiKey": _SECRET}
        spy = MagicMock()
        with patch.object(response_models, "logger", spy):
            # DETECTOR CONTROL: the shape being removed. Once interpolated into the log MESSAGE the
            # body is text, and mask_sensitive_data has no key left to match -- so the same secret
            # survives the very function that is supposed to redact it.
            spy.error(f"{name}: {body}")
            assert _SECRET in mask_sensitive_data(spy.error.call_args.args[0])
            spy.reset_mock()

            response = helper(body=dict(body))

        assert response["statusCode"] == status
        assert spy.error.call_count == 1
        message = spy.error.call_args.args[0]
        # A rendered body brings the dict's own braces with it, so their absence pins the plain message.
        assert _SECRET not in message and "{" not in message

        extra = spy.error.call_args.kwargs["extra"]
        assert extra["statusCode"] == status
        masked = mask_sensitive_data(extra)
        assert masked["errorBody"]["apiKey"] == REDACTED
        # POSITIVE CONTROL: masking took the credential and left the message an operator needs.
        assert masked["errorBody"]["message"] == "Invalid request."

    @pytest.mark.parametrize("name, _status, _default", _ERROR_HELPERS)
    def test_no_log_call_puts_a_powertools_reserved_key_in_extra(self, name, _status, _default):
        """FORWARD GUARD. Every error body is ``{'message': ...}``, so handing one to the logger as a
        bare ``extra=body`` would clobber the record's own message and erase the log TEXT."""
        spy = MagicMock()
        with patch.object(response_models, "logger", spy):
            getattr(response_models, name)(body={"message": "Invalid request."})
        for call in spy.mock_calls:
            collision = set(call.kwargs.get("extra") or {}) & _POWERTOOLS_RESERVED
            assert not collision, f"{name} passes reserved powertools key(s) {collision} in extra"


@pytest.mark.unit
class TestErrorResponsesUnchanged:
    """OVER-TIGHTENING CATCHER. Only the log line may move -- the bytes on the wire must not, and the
    audit entry must keep the message it reports."""

    @pytest.mark.parametrize("name, status, _default", _ERROR_HELPERS)
    def test_response_shape_and_body_bytes_are_byte_identical(self, name, status, _default):
        body = {"message": "Invalid request.", "count": Decimal("2")}
        response = getattr(response_models, name)(body=body)
        assert response["isBase64Encoded"] is False
        assert response["statusCode"] == status
        assert response["headers"] == _RESPONSE_HEADERS
        assert response["body"] == '{"message": "Invalid request.", "count": 2}'

    @pytest.mark.parametrize("name, status, default", _ERROR_HELPERS)
    def test_default_body_and_status_code_preserved(self, name, status, default):
        response = getattr(response_models, name)()
        assert response["statusCode"] == status
        assert json.loads(response["body"]) == {"message": default}

    @pytest.mark.parametrize("name", ["validation_error", "general_error", "internal_error"])
    def test_the_audit_hook_still_receives_the_message(self, name):
        """The audit log groups are where an error message is meant to be readable per caller, so the
        message must not have been squeezed out of the audit payload along with the log line.
        ``authorization_error`` is excluded: its audit call is commented out (Casbin logs the check)."""
        audit = MagicMock()
        with patch.object(response_models, "log_errors", audit):
            getattr(response_models, name)(body={"message": "Invalid request."},
                                           event={"headers": {}})
        assert audit.call_count == 1
        assert audit.call_args.args[2]["errorMessage"] == "Invalid request."
