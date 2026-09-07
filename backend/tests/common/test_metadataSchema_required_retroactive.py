# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-061: retroactive enforcement of a newly-required schema field is intended.

Editing a metadata schema to mark a field required immediately blocks every
subsequent metadata write for entities that do not carry it. The repository owner
confirmed that behaviour is deliberate: existing records are NOT grandfathered, a
``defaultMetadataFieldValue`` is NOT required alongside ``required: true``, and the
API is unchanged -- the remediation is a non-blocking warning in the web schema
editor plus documentation.

These tests therefore pin the backend half of that decision rather than change it.
They pass against current code and exist so that the two rejected remedies -- the
grandfathering in option (b) and the forced default in option (c) -- cannot be
introduced later without a failure. The user-facing warning and the documentation
update are covered in the web and documentation suites.
"""

import pytest

from backend.backend.common.metadataSchemaValidation import (  # noqa: E402
    validate_metadata_against_schema,
)


def _schema(required=True, default=None):
    field = {
        "metadataFieldName": "programCode",
        "metadataFieldValueType": "string",
        "required": required,
    }
    if default is not None:
        field["defaultMetadataFieldValue"] = default
    return {"programCode": field}


def _value(value, value_type="string"):
    return {"metadataValue": value, "metadataValueType": value_type}


@pytest.mark.unit
class TestNewlyRequiredFieldBlocksWrites:
    """FIX-061 constraint: retroactive enforcement stays."""

    @pytest.mark.parametrize("operation_type", ["POST", "PUT"])
    def test_write_omitting_the_required_field_is_rejected(self, operation_type):
        """FIX-061: once a field is required, a write without it must be refused."""
        valid, errors, _ = validate_metadata_against_schema(
            {"otherField": _value("x")}, _schema(), operation_type)

        assert valid is False
        assert any("programCode" in error for error in errors), errors

    @pytest.mark.parametrize("operation_type", ["POST", "PUT"])
    def test_write_supplying_the_required_field_is_accepted(self, operation_type):
        """Control: the permitted half -- supplying the field must still succeed.

        Without this, a validator that rejected everything would satisfy the
        rejection tests.
        """
        valid, errors, _ = validate_metadata_against_schema(
            {"programCode": _value("PRJ-1")}, _schema(), operation_type)

        assert valid is True, errors

    def test_empty_value_does_not_satisfy_the_requirement(self):
        """Control: the requirement is about a value, not just a key."""
        valid, errors, _ = validate_metadata_against_schema(
            {"programCode": _value("")}, _schema(), "PUT")

        assert valid is False
        assert any("programCode" in error for error in errors), errors


@pytest.mark.unit
class TestRejectedRemediesStayRejected:
    """FIX-061 constraint: neither grandfathering nor a forced default was adopted."""

    def test_existing_metadata_without_the_field_is_not_grandfathered(self):
        """FIX-061: option (b) was rejected -- an existing record is still blocked.

        Passing the entity's stored metadata (which predates the requirement) must
        not exempt the write.
        """
        valid, errors, _ = validate_metadata_against_schema(
            {"otherField": _value("x")},
            _schema(),
            "PUT",
            existing_metadata={"otherField": _value("x")},
        )

        assert valid is False
        assert any("programCode" in error for error in errors), errors

    def test_required_without_a_default_is_not_auto_filled(self):
        """FIX-061: option (c) was rejected -- required does not imply a default."""
        _, _, with_defaults = validate_metadata_against_schema(
            {"otherField": _value("x")}, _schema(default=None), "PUT")

        assert "programCode" not in with_defaults

    def test_declared_default_is_still_applied(self):
        """Control: the defaults machinery works, so the test above is not vacuous.

        A schema that DOES declare defaultMetadataFieldValue auto-fills and passes;
        that path is unchanged.
        """
        valid, errors, with_defaults = validate_metadata_against_schema(
            {"otherField": _value("x")}, _schema(default="UNSET"), "PUT")

        assert valid is True, errors
        assert with_defaults["programCode"]["metadataValue"] == "UNSET"
