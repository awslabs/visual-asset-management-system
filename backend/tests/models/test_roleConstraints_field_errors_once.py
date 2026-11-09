# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A field that fails its own validation is reported once, not again as missing.

Pydantic v1 drops a field that fails its declared constraint (the name regex, a length bound) from
the values a root validator receives. The constraint and role request models re-validate their
fields in a root validator, so without `skip_on_failure` that validator found the field absent and
added "<field> is a required field." to the real error. A valid request still runs every root check.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models.roleConstraints import (
    CreateConstraintRequestModel,
    CreateRoleRequestModel,
    CreateUserRolesRequestModel,
)


def _constraint(**overrides):
    body = {
        "identifier": "test-constraint",
        "name": "test-constraint",
        "description": "a valid description",
        "objectType": "asset",
        "criteriaAnd": [{"field": "assetName", "operator": "equals", "value": "x"}],
        "groupPermissions": [{"groupId": "admin", "permission": "GET", "permissionType": "allow"}],
    }
    body.update(overrides)
    return CreateConstraintRequestModel(**body)


def _errors(build):
    with pytest.raises(ValidationError) as exc:
        build()
    return exc.value.errors()


@pytest.mark.unit
class TestFieldErrorsAreReportedOnce:
    def test_an_invalid_constraint_name_is_one_error_on_the_name(self):
        errors = _errors(lambda: _constraint(name="bad name!"))

        assert [error["loc"] for error in errors] == [("name",)]
        assert "required" not in errors[0]["msg"]

    def test_an_invalid_constraint_identifier_is_one_error_on_the_identifier(self):
        errors = _errors(lambda: _constraint(identifier="bad id!"))

        assert [error["loc"] for error in errors] == [("identifier",)]

    def test_an_invalid_role_name_is_one_error_on_the_role_name(self):
        errors = _errors(lambda: CreateRoleRequestModel(roleName="bad name!", description="d"))

        assert [error["loc"] for error in errors] == [("roleName",)]

    def test_a_too_short_user_id_is_one_error_on_the_user_id(self):
        errors = _errors(lambda: CreateUserRolesRequestModel(userId="ab", roleName=["admin"]))

        assert [error["loc"] for error in errors] == [("userId",)]


@pytest.mark.unit
class TestRootChecksStillRun:
    def test_a_valid_constraint_passes(self):
        assert _constraint().name == "test-constraint"

    def test_a_root_level_rule_still_refuses_a_valid_shaped_request(self):
        errors = _errors(lambda: _constraint(
            criteriaAnd=[{"field": "workflowId", "operator": "equals", "value": "x"}]))

        assert errors and all(error["loc"] == ("__root__",) for error in errors)
