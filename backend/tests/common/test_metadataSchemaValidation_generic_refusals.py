# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Schema validation refusals repeat neither the request nor the schema's list.

A key the schema does not define is refused with a fixed text that names no field, since the key is
the caller's own input. A type change of a stored field and a value outside a controlled list name
the schema-defined field, as the required, dependency and wrong-type refusals do, but carry neither
the submitted value nor the field's controlled list (backend Rule 11).
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
        assert errors == ["Field 'colour' has a value that is not in its controlled list."]
        assert CALLER_VALUE not in errors[0] and "listed-one" not in errors[0]

    def test_a_type_change_of_a_stored_field(self):
        stored = {"count": {"metadataValue": "5", "metadataValueType": "number"}}
        valid, errors, _ = msv.validate_metadata_against_schema(
            {"count": {"metadataValue": CALLER_VALUE, "metadataValueType": "string"}},
            _schema(), "PUT", existing_metadata=stored)
        assert not valid
        assert "Field 'count' has its type defined by its metadata schema; the type cannot be changed." in errors
        assert not any(CALLER_VALUE in error for error in errors)
        assert not any("Cannot change type" in error for error in errors)
