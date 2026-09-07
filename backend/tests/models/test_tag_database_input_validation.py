# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input-validation coverage for the tag and database request models."""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError


@pytest.mark.unit
class TestTagTypeRequiredFlagNormalization:
    """The 'required' flag is stored as a string and consumers compare it against the
    exact value "True" (tagService list annotation, createAsset required-tag
    enforcement), so every accepted spelling must normalize to that canonical form.
    """

    MODEL_NAMES = ["CreateTagTypeRequestModel", "UpdateTagTypeRequestModel"]

    def _model(self, model_name):
        import models.tag as t
        return getattr(t, model_name)

    def _body(self, required):
        return {
            "tagTypeName": "material-type",
            "description": "Material classification",
            "required": required,
        }

    @pytest.mark.parametrize("model_name", MODEL_NAMES)
    @pytest.mark.parametrize("supplied", ["True", "true", "TRUE", "  True  ", "tRuE"])
    def test_truthy_spellings_normalize_to_canonical_True(self, model_name, supplied):
        # A lowercase "true" previously stored verbatim and failed the consumers'
        # `== "True"` comparison, silently making a required tag type optional.
        model = self._model(model_name)(**self._body(supplied))
        assert model.required == "True"

    @pytest.mark.parametrize("model_name", MODEL_NAMES)
    @pytest.mark.parametrize("supplied", ["False", "false", "FALSE", "  false  "])
    def test_falsey_spellings_normalize_to_canonical_False(self, model_name, supplied):
        model = self._model(model_name)(**self._body(supplied))
        assert model.required == "False"

    @pytest.mark.parametrize("model_name", MODEL_NAMES)
    def test_defaults_to_canonical_False(self, model_name):
        body = self._body("False")
        del body["required"]
        assert self._model(model_name)(**body).required == "False"

    @pytest.mark.parametrize("model_name", MODEL_NAMES)
    @pytest.mark.parametrize("supplied", ["yes", "no", "1", "0", "banana", "<script>x</script>"])
    def test_rejects_non_boolean_spellings(self, model_name, supplied):
        with pytest.raises((ValidationError, ValueError)):
            self._model(model_name)(**self._body(supplied))

    @pytest.mark.parametrize("model_name", MODEL_NAMES)
    def test_normalized_value_satisfies_the_consumer_comparison(self, model_name):
        # Mirrors tagService.py `tag_type["required"] == "True"` and createAsset.py
        # `deserialized_document.get("required", "False") == "True"`.
        enforced = self._model(model_name)(**self._body("true")).required == "True"
        assert enforced is True


@pytest.mark.unit
class TestTagPaginationTokenBounds:
    LIST_MODELS = ["GetTagsRequestModel", "GetTagTypesRequestModel"]

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_rejects_oversized_starting_token(self, model_name):
        import models.tag as t
        model_cls = getattr(t, model_name)
        with pytest.raises((ValidationError, ValueError)):
            model_cls(startingToken="A" * (t.MAX_TAG_TOKEN_LENGTH + 1))

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_accepts_realistic_starting_token(self, model_name):
        import base64
        import json
        import models.tag as t
        model_cls = getattr(t, model_name)
        token = base64.b64encode(
            json.dumps({"tagName": {"S": "structural-steel"}}).encode()
        ).decode()
        assert model_cls(startingToken=token).startingToken == token

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_accepts_absent_starting_token(self, model_name):
        import models.tag as t
        assert getattr(t, model_name)().startingToken is None


@pytest.mark.unit
class TestDatabasePaginationTokenBounds:
    LIST_MODELS = ["GetDatabasesRequestModel", "GetBucketsRequestModel"]

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_rejects_oversized_starting_token(self, model_name):
        import models.databases as d
        model_cls = getattr(d, model_name)
        with pytest.raises((ValidationError, ValueError)):
            model_cls(startingToken="A" * (d.MAX_PAGINATION_TOKEN_LENGTH + 1))

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_accepts_realistic_starting_token(self, model_name):
        import base64
        import json
        import models.databases as d
        model_cls = getattr(d, model_name)
        token = base64.b64encode(
            json.dumps({"databaseId": {"S": "building-scans"}}).encode()
        ).decode()
        assert model_cls(startingToken=token).startingToken == token

    @pytest.mark.parametrize("model_name", LIST_MODELS)
    def test_accepts_absent_starting_token(self, model_name):
        import models.databases as d
        assert getattr(d, model_name)().startingToken is None
