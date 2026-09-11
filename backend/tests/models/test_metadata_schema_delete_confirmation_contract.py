# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-114: the documented metadata-schema delete confirmation and the enforced one agree.

`DELETE /database/{databaseId}/metadataSchema/{metadataSchemaId}` is documented as requiring
`confirmDelete: true`, and `handle_delete_request` never reads the model it parses -- so
`DeleteMetadataSchemaRequestModel` is the whole gate. That made the spec and the code able to
disagree silently: with a plain `@validator`, pydantic 1.10.13 skipped the check for an absent
value, the spec still promised a required field, and a body of `{}` deleted the schema.

`tests/models/test_confirmation_interlocks_live.py` asserts the interlock is live and
`tests/handlers/metadataschema/test_metadataSchema_tier2_fail_closed.py` asserts the handler
rejects an unconfirmed request. Neither reads the specification, so a schema whose `required`
list stops naming the field -- or a model that stops enforcing what the list names -- is the
drift left uncovered. This file binds the two together and is driven off the spec's own
`required` list rather than a hardcoded field name, so a renamed confirmation is covered too.
"""

import re
from pathlib import Path

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError

from models.metadataSchema import DeleteMetadataSchemaRequestModel

yaml = pytest.importorskip("yaml")

_REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = _REPO_ROOT / "documentation" / "VAMS_API.yaml"
REFERENCE_PATH = _REPO_ROOT / "documentation" / "docusaurus-site" / "docs" / "api" / "metadata.md"

DELETE_PATH = "/database/{databaseId}/metadataSchema/{metadataSchemaId}"
REQUEST_SCHEMA_NAME = "deleteMetadataSchemaRequest"


@pytest.fixture(scope="module")
def spec():
    if not SPEC_PATH.is_file():
        pytest.skip(f"OpenAPI spec not found at {SPEC_PATH}")
    with open(SPEC_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def request_schema(spec):
    return spec["components"]["schemas"][REQUEST_SCHEMA_NAME]


@pytest.fixture(scope="module")
def required_fields(request_schema):
    return list(request_schema.get("required") or [])


@pytest.mark.unit
def test_the_delete_operation_actually_uses_that_request_schema(spec):
    """Positive control: everything below is vacuous if the schema is unreferenced.

    A `components/schemas` entry no operation `$ref`s documents nothing, so the required list
    asserted below would bind the model to a promise no caller is ever shown.
    """
    operation = spec["paths"][DELETE_PATH]["delete"]
    referenced = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert referenced == f"#/components/schemas/{REQUEST_SCHEMA_NAME}", (
        f"DELETE {DELETE_PATH} does not use {REQUEST_SCHEMA_NAME}; it uses {referenced}"
    )


@pytest.mark.unit
def test_the_spec_requires_a_confirmation(required_fields):
    assert required_fields == ["confirmDelete"], (
        f"{REQUEST_SCHEMA_NAME}.required is {required_fields}. The delete is gated by this "
        "request model alone -- the handler discards the parsed model -- so the spec dropping "
        "the confirmation means the documented interlock has no enforcement behind it"
    )


@pytest.mark.unit
def test_omitting_a_field_the_spec_requires_is_rejected(required_fields):
    """The link: every field the spec calls required must be one the model refuses to default.

    A pydantic v1 field with a default is only checked when the caller supplies it unless its
    validator declares `always=True`, so a documented-required field can parse cleanly from an
    empty body -- which is exactly how `{}` deleted a schema.

    The rejection is attributed to the field under test rather than merely observed: a second
    required field added to the spec would otherwise inherit the first field's rejection and be
    asserted vacuously.
    """
    with pytest.raises(ValidationError) as excinfo:
        parse({}, model=DeleteMetadataSchemaRequestModel)
    rejected = {error["loc"][0] for error in excinfo.value.errors() if error["loc"]}

    for field_name in required_fields:
        assert field_name in DeleteMetadataSchemaRequestModel.__fields__, (
            f"the spec requires {field_name}, which the model does not declare"
        )
        assert field_name in rejected, (
            f"the spec requires {field_name}, but an empty body is not rejected on account of it "
            f"(rejected: {sorted(rejected)})"
        )


@pytest.mark.unit
def test_a_schema_default_of_false_does_not_become_a_way_out(request_schema):
    """The spec documents `default: false`, which a generated client may send verbatim."""
    assert request_schema["properties"]["confirmDelete"].get("default") is False
    with pytest.raises(ValidationError):
        parse({"confirmDelete": False}, model=DeleteMetadataSchemaRequestModel)


@pytest.mark.unit
def test_the_documented_example_is_accepted(request_schema):
    """Positive control: the body the spec shows callers must still parse.

    An interlock that also rejects the documented request is an outage, not a fix, and the web
    application sends exactly this body.
    """
    example = request_schema["example"]
    parsed = parse(example, model=DeleteMetadataSchemaRequestModel)
    assert parsed.confirmDelete is True


@pytest.mark.unit
def test_the_api_reference_documents_the_confirmation_as_required():
    """The reference page is the second published source of this contract (root Pattern 1)."""
    if not REFERENCE_PATH.is_file():
        pytest.skip(f"API reference not found at {REFERENCE_PATH}")
    reference = REFERENCE_PATH.read_text(encoding="utf-8")
    row = re.search(r"^\|\s*`confirmDelete`\s*\|(?P<cells>.*)$", reference, re.MULTILINE)
    assert row is not None, (
        f"{REFERENCE_PATH.name} no longer documents confirmDelete in a field table, so the "
        "enforced interlock is undocumented on the page operators read"
    )
    cells = [cell.strip() for cell in row.group("cells").split("|")]
    assert cells[:2] == ["boolean", "Yes"], (
        f"{REFERENCE_PATH.name} documents confirmDelete as {cells[:2]}; the model rejects a body "
        "that omits it, so the page must state the field is required"
    )
