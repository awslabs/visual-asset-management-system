# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input validation on the tag and tag-type request models.

The 'required' flag is stored as a string and consumers compare it against the
exact value "True" (tagService list annotation, createAsset required-tag
enforcement), so these tests pin the canonical spelling the models emit.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError, parse


def _tag_type_body(**overrides):
    body = {"tagTypeName": "Material", "description": "Material classification"}
    body.update(overrides)
    return body


@pytest.mark.unit
class TestTagTypeRequiredFlagNormalization:
    MODELS = ["CreateTagTypeRequestModel", "UpdateTagTypeRequestModel"]

    @pytest.mark.parametrize("model_name", MODELS)
    @pytest.mark.parametrize("submitted", ["true", "TRUE", "True", " true ", "tRuE"])
    def test_truthy_spellings_normalize_to_canonical_true(self, model_name, submitted):
        """A lowercase 'true' must not silently read as not-required downstream."""
        import models.tag as t
        model_cls = getattr(t, model_name)
        model = parse(_tag_type_body(required=submitted), model=model_cls)
        assert model.required == "True"

    @pytest.mark.parametrize("model_name", MODELS)
    @pytest.mark.parametrize("submitted", ["false", "FALSE", "False", " false "])
    def test_falsy_spellings_normalize_to_canonical_false(self, model_name, submitted):
        import models.tag as t
        model_cls = getattr(t, model_name)
        model = parse(_tag_type_body(required=submitted), model=model_cls)
        assert model.required == "False"

    @pytest.mark.parametrize("model_name", MODELS)
    def test_bool_normalizes_to_canonical_string(self, model_name):
        import models.tag as t
        model_cls = getattr(t, model_name)
        assert parse(_tag_type_body(required=True), model=model_cls).required == "True"
        assert parse(_tag_type_body(required=False), model=model_cls).required == "False"

    @pytest.mark.parametrize("model_name", MODELS)
    def test_default_is_canonical_false(self, model_name):
        import models.tag as t
        model_cls = getattr(t, model_name)
        assert parse(_tag_type_body(), model=model_cls).required == "False"

    @pytest.mark.parametrize("model_name", MODELS)
    @pytest.mark.parametrize("submitted", ["banana", "yes", "1", "<script>x</script>"])
    def test_rejects_non_boolean_values(self, model_name, submitted):
        import models.tag as t
        model_cls = getattr(t, model_name)
        with pytest.raises(ValidationError):
            parse(_tag_type_body(required=submitted), model=model_cls)


@pytest.mark.unit
class TestTagPaginationTokenBounds:
    MODELS = ["GetTagsRequestModel", "GetTagTypesRequestModel"]

    @pytest.mark.parametrize("model_name", MODELS)
    def test_rejects_oversized_starting_token(self, model_name):
        import models.tag as t
        model_cls = getattr(t, model_name)
        with pytest.raises(ValidationError):
            parse(
                {"startingToken": "A" * (t.MAX_TAG_TOKEN_LENGTH + 1)},
                model=model_cls,
            )

    @pytest.mark.parametrize("model_name", MODELS)
    def test_accepts_realistic_starting_token(self, model_name):
        import base64
        import json

        import models.tag as t
        model_cls = getattr(t, model_name)
        token = base64.b64encode(
            json.dumps({"tagName": "Concrete"}).encode("utf-8")
        ).decode("utf-8")
        assert parse({"startingToken": token}, model=model_cls).startingToken == token

    @pytest.mark.parametrize("model_name", MODELS)
    def test_accepts_absent_starting_token(self, model_name):
        import models.tag as t
        model_cls = getattr(t, model_name)
        assert parse({}, model=model_cls).startingToken is None
