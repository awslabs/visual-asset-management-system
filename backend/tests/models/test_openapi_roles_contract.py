# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests binding documentation/VAMS_API.yaml to the PUT /roles code it documents.

`PUT /roles` is a partial update: `update_role` writes only the fields the request body
carried, so an omitted field keeps its stored value. Three properties of the spec follow from
that and none of them is visible to a handler test:

1. `updateRoleRequest.mfaRequired` carries no `default`. A generated client honours a schema
   default by sending the value explicitly on every request, which is exactly the write the
   partial update exists to avoid -- documenting `false` there re-creates the defect one layer
   out. `createRoleRequest` keeps its default, where `create_role` genuinely applies one.
2. `source` and `sourceIdentifier` accept `null`, because clearing one is documented as sending
   it explicitly. In OpenAPI 3.0 a keyword alongside `$ref` is ignored, so the property has to
   be inline for `nullable` to mean anything.
3. The spec's `required` list matches the fields UpdateRoleRequestModel requires.
"""

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


@pytest.fixture(scope="module")
def update_role_request(spec):
    return spec["components"]["schemas"]["updateRoleRequest"]


@pytest.mark.unit
def test_update_role_request_documents_no_mfa_default(update_role_request):
    """An omitted mfaRequired keeps the stored value, so the schema must not supply one."""
    mfa = update_role_request["properties"]["mfaRequired"]
    assert "default" not in mfa, (
        "updateRoleRequest.mfaRequired carries a schema default. A generated client sends a "
        "documented default explicitly, which overwrites the stored MFA requirement on every "
        f"update: {mfa}"
    )


@pytest.mark.unit
def test_create_role_request_keeps_its_mfa_default(spec):
    """Positive control: create_role does default mfaRequired, so that schema keeps its default."""
    mfa = spec["components"]["schemas"]["createRoleRequest"]["properties"]["mfaRequired"]
    assert mfa.get("default") is False, (
        "createRoleRequest.mfaRequired lost its default. The absence of a default is specific "
        f"to the update schema; create applies one: {mfa}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("field", ["source", "sourceIdentifier"])
def test_update_role_request_permits_the_null_clear(spec, update_role_request, field):
    """Clearing a source linkage is documented as sending null, so the schema must accept null."""
    assert str(spec["openapi"]).startswith("3.0"), (
        f"Spec is OpenAPI {spec['openapi']}; the $ref-sibling reasoning below is 3.0-specific"
    )
    prop = update_role_request["properties"][field]
    assert "$ref" not in prop, (
        f"updateRoleRequest.{field} is a $ref. OpenAPI 3.0 ignores keywords beside $ref, so a "
        f"sibling nullable would be inert: {prop}"
    )
    assert prop.get("type") == "string"
    assert prop.get("nullable") is True, (
        f"updateRoleRequest.{field} does not accept null, but the endpoint description and "
        f"api/auth.md both document null as the way to remove the stored value: {prop}"
    )


@pytest.mark.unit
def test_put_roles_description_states_the_partial_semantics(spec):
    """The caller-facing description has to say an omitted field is preserved."""
    description = spec["paths"]["/roles"]["put"]["description"]
    assert "keeps its stored value" in description, (
        "The PUT /roles description does not state that an omitted field is preserved, so a "
        f"caller cannot tell a partial update from a full replace: {description}"
    )


@pytest.mark.unit
def test_update_role_request_required_matches_the_model(update_role_request):
    """The spec's required list and the model's required fields are the same set."""
    from models.roleConstraints import UpdateRoleRequestModel

    fields = UpdateRoleRequestModel.__fields__
    model_required = {name for name, field in fields.items() if field.required}

    # `roleName` alone identifies the role to update; every other field is optional, because PUT
    # /roles is a partial update and an omitted field keeps its stored value.
    assert set(update_role_request["required"]) == model_required == {"roleName"}
    # `description` is optional, and the model accepts an explicit null the same way it accepts an
    # omitted field — the two are distinguishable only through `__fields_set__`. The refusal of an
    # explicit null therefore lives in the handler (`createRole.handle_put_request`, which answers 400
    # "description cannot be null"), not here: RoleResponseModel.description is a required `str`, so a
    # stored null would fail response validation for every caller of GET /roles, not just the sender.
    assert fields["description"].required is False
    assert fields["description"].allow_none is True
    # Both source fields are optional and accept None, which is what makes the documented
    # explicit-null clear reach update_role rather than failing validation.
    for field in ("source", "sourceIdentifier"):
        assert fields[field].required is False
        assert fields[field].allow_none is True
