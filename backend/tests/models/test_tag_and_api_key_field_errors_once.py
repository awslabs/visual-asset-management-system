# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A tag, tag type or API key field that fails its own validation is reported once.

Pydantic v1 drops a field that fails its declared constraint (the name regex, a length bound) from
the values a root validator receives. These request models re-validate their fields in a root
validator, so without `skip_on_failure` that validator found the field absent and added
"<field> is a required field." (or, on the update models, "At least one of ... must be provided")
to the real error. A valid request still runs every root check.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models.apiKeys import (
    CreateApiKeyRequestModel,
    CreateUserApiKeyRequestModel,
    UpdateApiKeyRequestModel,
    UpdateUserApiKeyRequestModel,
)
from backend.backend.models.tag import (
    CreateTagRequestModel,
    CreateTagTypeRequestModel,
    UpdateTagRequestModel,
    UpdateTagTypeRequestModel,
)

LONG_DESCRIPTION = "d" * 257


def _errors(build):
    with pytest.raises(ValidationError) as exc:
        build()
    return exc.value.errors()


def _assert_one_error_on(errors, field):
    assert [error["loc"] for error in errors] == [(field,)]
    assert "required" not in errors[0]["msg"]
    assert "At least one of" not in errors[0]["msg"]


TAG = {"tagName": "tag1", "description": "a tag", "tagTypeName": "type1"}
TAG_TYPE = {"tagTypeName": "type1", "description": "a tag type"}
API_KEY = {"apiKeyName": "key1", "userId": "user1", "description": "a key"}
USER_API_KEY = {"apiKeyName": "key1", "description": "a key", "expiresAt": "2099-01-01T00:00:00Z"}


@pytest.mark.unit
class TestFieldErrorsAreReportedOnce:
    @pytest.mark.parametrize("model", [CreateTagRequestModel, UpdateTagRequestModel])
    @pytest.mark.parametrize("field,value", [
        ("description", LONG_DESCRIPTION), ("tagName", "bad name!"), ("tagTypeName", "bad name!")])
    def test_a_tag_field(self, model, field, value):
        _assert_one_error_on(_errors(lambda: model(**dict(TAG, **{field: value}))), field)

    @pytest.mark.parametrize("model", [CreateTagTypeRequestModel, UpdateTagTypeRequestModel])
    @pytest.mark.parametrize("field,value", [
        ("description", LONG_DESCRIPTION), ("tagTypeName", "bad name!")])
    def test_a_tag_type_field(self, model, field, value):
        _assert_one_error_on(_errors(lambda: model(**dict(TAG_TYPE, **{field: value}))), field)

    @pytest.mark.parametrize("field,value", [
        ("description", LONG_DESCRIPTION), ("apiKeyName", "bad name!")])
    def test_an_api_key_field(self, field, value):
        _assert_one_error_on(
            _errors(lambda: CreateApiKeyRequestModel(**dict(API_KEY, **{field: value}))), field)

    @pytest.mark.parametrize("field,value", [
        ("description", LONG_DESCRIPTION), ("apiKeyName", "bad name!")])
    def test_a_user_api_key_field(self, field, value):
        _assert_one_error_on(
            _errors(lambda: CreateUserApiKeyRequestModel(**dict(USER_API_KEY, **{field: value}))),
            field)

    @pytest.mark.parametrize("model", [UpdateApiKeyRequestModel, UpdateUserApiKeyRequestModel])
    def test_an_api_key_update_with_only_a_too_long_description(self, model):
        _assert_one_error_on(_errors(lambda: model(description=LONG_DESCRIPTION)), "description")


@pytest.mark.unit
class TestRootChecksStillRun:
    def test_valid_requests_pass(self):
        assert CreateTagRequestModel(**TAG).tagName == "tag1"
        assert UpdateTagTypeRequestModel(**TAG_TYPE).tagTypeName == "type1"
        assert CreateApiKeyRequestModel(**API_KEY).apiKeyName == "key1"
        assert UpdateApiKeyRequestModel(isActive="true").isActive == "true"

    @pytest.mark.parametrize("model", [UpdateApiKeyRequestModel, UpdateUserApiKeyRequestModel])
    def test_an_empty_api_key_update_is_still_refused_at_the_root(self, model):
        errors = _errors(lambda: model())

        assert [error["loc"] for error in errors] == [("__root__",)]
        assert "At least one of" in errors[0]["msg"]

    def test_a_bad_api_key_expiry_is_still_refused_at_the_root(self):
        errors = _errors(lambda: CreateApiKeyRequestModel(**dict(API_KEY, expiresAt="not-a-date")))

        assert [error["loc"] for error in errors] == [("__root__",)]
