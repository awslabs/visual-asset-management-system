# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests binding documentation/VAMS_API.yaml to the pipeline-template code it documents.

Two save-time rules are enforced by the request models and are only discoverable from the spec:

1. A `tagSchema` entry's keys are an ALLOW LIST. An unrecognized key is rejected rather than
   ignored, because a stored definition is read a named key at a time -- a misspelled `requried`
   would leave the tag optional and a differently cased `Type` would leave it a string. That makes
   the spec's property list the caller's only statement of which spellings exist, so it has to be
   the same set the model accepts AND the same set the rejection names. A field added to the model
   without a matching property leaves callers unable to discover it; a property with no model field
   documents a key every request would be rejected for sending.
2. A template's `overrides` block is bounded by its serialized size. The figure lives in the schema
   description rather than in a keyword, since OpenAPI has no size bound for an object, so nothing
   but a test ties the documented number to the constant that enforces it.

The list order is asserted as well as the set: the rejection message renders the model's field order
verbatim, so a reader comparing the message with the spec sees one ordering or two.
"""

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

SPEC_PATH = Path(__file__).resolve().parents[3] / "documentation" / "VAMS_API.yaml"


@pytest.fixture(scope="module")
def spec():
    if not SPEC_PATH.is_file():
        pytest.skip(f"OpenAPI spec not found at {SPEC_PATH}")
    with open(SPEC_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.mark.unit
def test_tag_field_properties_match_the_model_fields(spec):
    """The documented keys and the keys the model declares are the same list, in the same order."""
    from models.pipelines import TemplateTagFieldModel

    documented = list(spec["components"]["schemas"]["pipelineTemplateTagField"]["properties"])
    declared = list(TemplateTagFieldModel.__fields__)

    assert documented == declared, (
        "pipelineTemplateTagField's properties and TemplateTagFieldModel's fields have drifted. "
        "An unrecognized tag-definition key is REJECTED, so a property with no field documents a "
        "key every request is refused for sending, and a field with no property is undiscoverable: "
        f"documented={documented} declared={declared}"
    )


@pytest.mark.unit
def test_the_rejection_names_exactly_the_documented_keys(spec):
    """The allow list the error reports is the documented one -- measured, not restated.

    The message is what a caller reads when the save fails, so it is the third copy of this set
    (spec property list, model fields, error text). Asserting it here is what makes the other two
    assertions about behaviour rather than about two declarations that happen to agree.
    """
    from aws_lambda_powertools.utilities.parser import ValidationError
    from models.pipelines import CreateTemplateRequestModel

    with pytest.raises(ValidationError) as excinfo:
        CreateTemplateRequestModel(
            templateName="contract-probe",
            tagSchema=[{"tagKey": "PROMPT", "type": "string", "requried": True}],
        )

    message = str(excinfo.value)
    assert "unknown key 'requried'" in message, (
        f"The typo was not reported as an unknown key, so this test no longer covers it: {message}"
    )
    reported = re.search(r"allowed: \(([^)]*)\)", message)
    assert reported, f"The rejection did not report the allowed key list: {message}"
    named = [key.strip().strip("'\"") for key in reported.group(1).split(",") if key.strip()]

    documented = list(spec["components"]["schemas"]["pipelineTemplateTagField"]["properties"])
    assert named == documented, (
        "The keys the rejection names differ from the keys the OpenAPI specification documents, so "
        f"a caller reading the spec cannot resolve the error: named={named} documented={documented}"
    )


@pytest.mark.unit
def test_tag_field_schema_documents_that_an_unknown_key_is_rejected(spec):
    """A property list alone reads as advisory; the description has to say a stray key fails."""
    description = spec["components"]["schemas"]["pipelineTemplateTagField"]["description"]
    assert "rejected" in description, (
        "pipelineTemplateTagField's description does not say an unrecognized key is rejected. "
        "Every other VAMS object schema tolerates extra keys, so silence here reads as the same "
        f"tolerance: {description}"
    )


@pytest.mark.unit
def test_overrides_schema_documents_the_enforced_size_bound(spec):
    """The documented byte figure is the constant the validator applies."""
    from models.pipelines import MAX_CONFIG_BLOCK_BYTES

    description = spec["components"]["schemas"]["pipelineTemplateOverrides"]["description"]
    assert f"{MAX_CONFIG_BLOCK_BYTES:,}" in description, (
        f"pipelineTemplateOverrides does not document its {MAX_CONFIG_BLOCK_BYTES}-byte bound, or "
        f"the documented figure no longer matches the constant: {description}"
    )


@pytest.mark.unit
def test_overrides_properties_match_the_overridable_keys(spec):
    """Positive control: the pre-existing key allow list still agrees.

    This held before the size bound was added and must keep holding, which is what shows the
    fixture is reading the real schema rather than passing on an empty lookup.
    """
    from models.pipelines import TEMPLATE_OVERRIDE_KEYS

    documented = set(spec["components"]["schemas"]["pipelineTemplateOverrides"]["properties"])
    assert documented == set(TEMPLATE_OVERRIDE_KEYS), (
        f"documented={sorted(documented)} enforced={sorted(TEMPLATE_OVERRIDE_KEYS)}"
    )
