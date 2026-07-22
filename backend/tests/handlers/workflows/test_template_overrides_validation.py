# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Save-time validation for pipeline-template `overrides` and freeform bodies (configBody/webFormJson).

overrides may set only the four overridable systemConfig keys, each with a validated value shape;
unknown keys are rejected. configBody is JSON-parse-checked only when configFormat is 'json';
webFormJson (when present) must be valid JSON. Dependency-free (pure pydantic models)."""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models.pipelines import (
    CreateTemplateRequestModel,
    UpdateTemplateRequestModel,
    TEMPLATE_OVERRIDE_KEYS,
)


def _create(**kw):
    base = {"templateName": "t", "configFormat": "yaml", "configBody": "x: 1"}
    base.update(kw)
    return CreateTemplateRequestModel(**base)


@pytest.mark.unit
class TestTemplateOverridesValidation:
    def test_overridable_keys_constant(self):
        assert TEMPLATE_OVERRIDE_KEYS == (
            "inputFileArity", "metadataInputs", "assetScope", "inputFileFilters")

    def test_valid_overrides_accepted(self):
        m = _create(overrides={
            "inputFileArity": "multi",
            "assetScope": {"crossAssetAllowed": True, "singleAssetOnly": False},
            "metadataInputs": {"assetMetadata": True},
            "inputFileFilters": {"allow": ["*.glb"], "exclude": []},
        })
        assert m.overrides["inputFileArity"] == "multi"

    def test_empty_overrides_ok(self):
        assert _create(overrides={}).overrides == {}

    def test_unknown_override_key_rejected(self):
        with pytest.raises(ValidationError):
            _create(overrides={"requireTemplate": True})  # not an overridable key

    def test_bad_arity_rejected(self):
        with pytest.raises(ValidationError):
            _create(overrides={"inputFileArity": "seventeen"})

    def test_unknown_asset_scope_key_rejected(self):
        with pytest.raises(ValidationError):
            _create(overrides={"assetScope": {"bogus": True}})

    def test_non_boolean_scope_value_rejected(self):
        with pytest.raises(ValidationError):
            _create(overrides={"assetScope": {"crossAssetAllowed": "yes"}})

    def test_unknown_metadata_key_rejected(self):
        with pytest.raises(ValidationError):
            _create(overrides={"metadataInputs": {"customMeta": True}})

    def test_filters_must_be_string_lists(self):
        with pytest.raises(ValidationError):
            _create(overrides={"inputFileFilters": {"allow": [1, 2]}})

    def test_update_model_validates_overrides(self):
        with pytest.raises(ValidationError):
            UpdateTemplateRequestModel(overrides={"nope": 1})


@pytest.mark.unit
class TestTemplateBodyValidation:
    def test_json_config_body_must_parse(self):
        with pytest.raises(ValidationError):
            CreateTemplateRequestModel(
                templateName="t", configFormat="json", configBody="not json")

    def test_json_config_body_valid(self):
        m = CreateTemplateRequestModel(
            templateName="t", configFormat="json", configBody='{"k": 1}')
        assert m.configBody == '{"k": 1}'

    def test_non_json_body_not_parsed(self):
        # yaml/raw bodies are passed through — not JSON-parsed.
        m = CreateTemplateRequestModel(
            templateName="t", configFormat="yaml", configBody="x: {{tag}}")
        assert m.configBody == "x: {{tag}}"

    def test_web_form_json_must_be_valid_json(self):
        with pytest.raises(ValidationError):
            CreateTemplateRequestModel(
                templateName="t", configFormat="yaml", configBody="x: 1",
                webFormJson="{not valid")

    def test_web_form_json_valid(self):
        m = CreateTemplateRequestModel(
            templateName="t", configFormat="yaml", configBody="x: 1",
            webFormJson='[{"tagKey": "a"}]')
        assert m.webFormJson == '[{"tagKey": "a"}]'
