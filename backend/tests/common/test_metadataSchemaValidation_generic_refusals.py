# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Schema validation refusals that would repeat the request or the schema's list are generic.

A key the schema does not define, a type change of a stored field and a value outside a
controlled list are refused with fixed texts, so the response carries neither the caller's key or
value nor the field's controlled list (backend Rule 11). Refusals that name a field the schema
itself defines (a required field, a dependency, a value of the wrong type) keep naming it.
"""

import pytest

from backend.backend.common import metadataSchemaValidation as msv

CALLER_KEY = "caller-chosen-key"
CALLER_VALUE = "caller-chosen-value"


def _schema():
    return {
        "colour": {"metadataFieldValueType": "inline_controlled_list",
                   "controlledListKeys": ["listed-one", "listed-two"]},
        "count": {"metadataFieldValueType": "number"},
    }


@pytest.mark.unit
class TestRefusalsAreGeneric:
    def test_a_key_outside_the_schema(self):
        valid, errors = msv.validate_metadata_keys_against_schema(
            {CALLER_KEY: {"metadataValue": "x", "metadataValueType": "string"}}, _schema(), True)
        assert not valid
        assert errors == [msv.MSG_FIELD_NOT_IN_SCHEMA]
        assert CALLER_KEY not in errors[0]

    def test_a_value_outside_the_controlled_list(self):
        valid, errors, _ = msv.validate_metadata_against_schema(
            {"colour": {"metadataValue": CALLER_VALUE, "metadataValueType": "inline_controlled_list"}},
            _schema(), "PUT")
        assert not valid
        assert errors == [msv.MSG_VALUE_NOT_IN_CONTROLLED_LIST]
        assert CALLER_VALUE not in errors[0] and "listed-one" not in errors[0]

    def test_a_type_change_of_a_stored_field(self):
        stored = {"count": {"metadataValue": "5", "metadataValueType": "number"}}
        valid, errors, _ = msv.validate_metadata_against_schema(
            {"count": {"metadataValue": CALLER_VALUE, "metadataValueType": "string"}},
            _schema(), "PUT", existing_metadata=stored)
        assert not valid
        assert msv.MSG_FIELD_TYPE_FIXED_BY_SCHEMA in errors
        assert not any("Cannot change type" in error for error in errors)
