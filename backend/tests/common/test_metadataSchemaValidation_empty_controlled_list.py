# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An optional controlled-list field may be left empty.

The read path returns every schema field, an unset one with the empty string as its value, and the
web editor's Bulk Edit sends every row back in a replace-all write; its dropdown also offers a
"(Clear)" option that sends the empty string. The controlled-list check compared that empty string
against the list and refused the write ("Field 'colour' value '' is not in controlled list"), so
Bulk Edit Save All failed on any record with an unset optional dropdown field. Whether a field may
be empty is the required-field check's decision; the list check now judges only a value.
"""

import pytest

from backend.backend.common.metadataSchemaValidation import validate_metadata_against_schema

LIST_KEY = "colour"


def _schema(required=False):
    return {
        LIST_KEY: {
            "metadataFieldValueType": "inline_controlled_list",
            "required": required,
            "controlledListKeys": ["red", "blue"],
        }
    }


def _entry(value):
    return {LIST_KEY: {"metadataValue": value, "metadataValueType": "inline_controlled_list"}}


@pytest.mark.unit
@pytest.mark.parametrize("value_check_keys", [None, {LIST_KEY}], ids=["all-keys", "write-keys"])
@pytest.mark.parametrize("operation", ["POST", "PUT"])
def test_an_optional_controlled_list_field_may_be_empty(operation, value_check_keys):
    is_valid, errors, _ = validate_metadata_against_schema(
        _entry(""), _schema(), operation, value_check_keys=value_check_keys)

    assert is_valid, errors


@pytest.mark.unit
def test_a_required_controlled_list_field_left_empty_is_still_refused():
    is_valid, errors, _ = validate_metadata_against_schema(_entry(""), _schema(required=True), "PUT")

    assert not is_valid
    assert errors == [f"Required field '{LIST_KEY}' cannot be empty"]


@pytest.mark.unit
def test_a_value_outside_the_list_is_still_refused():
    is_valid, errors, _ = validate_metadata_against_schema(_entry("purple"), _schema(), "PUT")

    assert not is_valid
    assert errors == [f"Field '{LIST_KEY}' value 'purple' is not in controlled list: ['red', 'blue']"]
