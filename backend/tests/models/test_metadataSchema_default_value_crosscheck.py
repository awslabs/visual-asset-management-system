# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Default field values that the deployment-time default file metadata schema check must agree on.

The synth-time check of infra/config/metadataSchema/defaultFileMetadataSchema.json mirrors
MetadataSchemaFieldModel. Both sides read the same fixture: this test pins the 'backend' outcome
of each case to the real model, and the infra test pins the 'file' outcome and asserts the file
never accepts a value the backend refuses.
"""

import json
from pathlib import Path

import pytest

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "infra" / "test" / "platform" / "fixtures" / "defaultMetadataValueCrossCheck.json"
)


def _cases():
    if not FIXTURE.is_file():
        return []
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _accepted(value_type, value):
    from models.metadataSchema import MetadataSchemaFieldModel
    try:
        MetadataSchemaFieldModel(
            metadataFieldKeyName="A",
            metadataFieldValueType=value_type,
            defaultMetadataFieldValue=value,
        )
        return True
    except ValueError:
        return False


@pytest.mark.unit
def test_fixture_is_present():
    assert FIXTURE.is_file(), f"cross-check fixture missing: {FIXTURE}"
    assert len(_cases()) > 0


@pytest.mark.unit
@pytest.mark.parametrize("case", _cases(), ids=lambda c: f"{c['type']}:{c['value']}")
def test_backend_outcome_matches_fixture(case):
    assert _accepted(case["type"], case["value"]) is case["backend"]
