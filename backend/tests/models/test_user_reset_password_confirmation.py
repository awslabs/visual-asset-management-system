# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`ResetPasswordRequestModel` -- the confirmation the password-reset route requires.

Password reset deletes nothing, but it does force the account out of its current credential, so
the model is the interlock: only an explicitly true `confirmReset` parses. Each rejection case is
paired with the payload a shipping client actually sends, so the interlock cannot be tightened
past the requests the web app and the CLI make.

The absent-field case is what the interlock turns on, and it is also asserted for every
confirmation field in the models tree by `test_confirmation_interlocks_live.py`; it is repeated
here so this model's contract is readable on its own.

The route's two documentation sources are checked against the same contract, because a reader
following either one has to arrive at a request the model accepts.
"""

import pathlib

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError

from models.user import ResetPasswordRequestModel

_REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SPEC_PATH = _REPOSITORY_ROOT / "documentation" / "VAMS_API.yaml"
_REFERENCE_PATH = (
    _REPOSITORY_ROOT / "documentation" / "docusaurus-site" / "docs" / "api" / "auth.md"
)
_RESET_PASSWORD_ROUTE = "/user/cognito/{userId}/resetPassword"
_REFERENCE_HEADING = "### Reset a user's password"


@pytest.mark.unit
class TestResetPasswordConfirmationIsRequired:
    def test_absent_confirmation_is_rejected(self):
        """The default is not confirmation, so a body that omits the field must not parse."""
        with pytest.raises(ValidationError):
            parse({}, model=ResetPasswordRequestModel)

    def test_null_confirmation_is_rejected(self):
        """A JSON `null` is not the absent case.

        A JavaScript client sends `null` for an unset value. The field is a plain `bool`, so the
        null is rejected on its own; declaring it `Optional[bool]` would admit the null and hand
        the validator a value that is neither true nor a caller's false.
        """
        with pytest.raises(ValidationError):
            parse({'confirmReset': None}, model=ResetPasswordRequestModel)

    @pytest.mark.parametrize("value", [False, 'false', 0])
    def test_falsy_confirmation_is_rejected(self, value):
        """Every spelling of "no" is a rejection, not just the boolean one."""
        with pytest.raises(ValidationError):
            parse({'confirmReset': value}, model=ResetPasswordRequestModel)


@pytest.mark.unit
class TestConfirmedRequestsStillParse:
    """Positive controls: the interlock must not reject a properly confirmed request."""

    def test_the_web_payload_is_accepted(self):
        """`services/APIService.ts` posts the user id alongside the confirmation.

        The model declares `extra='ignore'`, so the surplus field is dropped rather than
        rejected -- asserting the parsed shape keeps that behaviour pinned.
        """
        parsed = parse(
            {'userId': 'reset-target@example.com', 'confirmReset': True},
            model=ResetPasswordRequestModel,
        )
        assert parsed.confirmReset is True
        assert parsed.dict() == {'confirmReset': True}

    def test_the_cli_payload_is_accepted(self):
        """`APIClient.reset_cognito_user_password` posts the confirmation on its own."""
        parsed = parse({'confirmReset': True}, model=ResetPasswordRequestModel)
        assert parsed.confirmReset is True

    @pytest.mark.parametrize("value", [True, 'true', 1])
    def test_truthy_confirmation_is_accepted(self, value):
        """A client that renders the boolean as a string or a number still confirms."""
        parsed = parse({'confirmReset': value}, model=ResetPasswordRequestModel)
        assert parsed.confirmReset is True


def _reference_section():
    """The API reference text for this route, from its heading to the next rule.

    Sliced rather than searched whole so a statement about a neighbouring route cannot answer for
    this one. The caller asserts the slice was found and carries the field, so a heading rename
    fails here instead of leaving the checks below matching an empty string.
    """
    page = _REFERENCE_PATH.read_text(encoding="utf-8")
    assert _REFERENCE_HEADING in page, (
        f"{_REFERENCE_PATH.name} no longer carries the heading {_REFERENCE_HEADING!r}, so the "
        f"assertions below would read an empty section")
    section = page.split(_REFERENCE_HEADING, 1)[1].split("\n---", 1)[0]
    assert "confirmReset" in section, (
        "the reset-password section of the API reference does not mention confirmReset, so the "
        "confirmation the route enforces is undocumented")
    return section


@pytest.mark.unit
class TestTheDocumentedContractMatchesTheModel:
    """Both API doc sources describe the confirmation the model enforces.

    A body-less POST is rejected, so documentation that presents the body or the field as
    optional sends a reader to a request that cannot succeed. The two sources are independent
    (Pattern 1), which is why each is asserted separately.
    """

    def test_the_openapi_spec_requires_the_body_and_the_field(self):
        # Skipped rather than module-level, so a missing PyYAML cannot also skip the model cases.
        yaml = pytest.importorskip("yaml")
        spec = yaml.safe_load(_SPEC_PATH.read_text(encoding="utf-8"))
        operation = spec["paths"][_RESET_PASSWORD_ROUTE]["post"]
        assert operation["requestBody"]["required"] is True, (
            "the spec declares the request body optional while the model refuses a body that "
            "omits confirmReset")
        schema = spec["components"]["schemas"]["resetPasswordRequest"]
        assert "confirmReset" in (schema.get("required") or []), (
            "resetPasswordRequest does not list confirmReset as required")

    def test_the_api_reference_documents_the_field_as_required(self):
        section = _reference_section()
        rows = [line for line in section.splitlines() if line.startswith("| `confirmReset`")]
        assert len(rows) == 1, f"expected one confirmReset field row, found {rows}"
        assert "| Yes " in rows[0], (
            f"the API reference marks confirmReset optional: {rows[0]}")

    def test_the_api_reference_does_not_call_the_body_optional(self):
        assert "The request body is optional" not in _reference_section(), (
            "the API reference states the request body is optional; a POST with no body is "
            "rejected with 400")
