# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Save-time validation for pipeline-template `overrides` and freeform bodies (configBody/webFormJson).

overrides may set only the four overridable systemConfig keys, each with a validated value shape;
unknown keys are rejected. configBody is JSON-parse-checked only when configFormat is 'json' — and that
check is tag-aware, so a body carrying {{tagName}} placeholders is validated for the JSON around them;
webFormJson (when present) must be valid JSON. Dependency-free (pure pydantic models)."""

import glob
import json
import os

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.common.workflows import templateRender as tr
from backend.backend.common.workflows import templateTagSchema as ts
from backend.backend.models.pipelines import (
    CreateTemplateRequestModel,
    UpdateTemplateRequestModel,
    TEMPLATE_OVERRIDE_KEYS,
)

# The CDK pipeline-registration schemas, whose template bodies go through this same model on import.
_PIPELINES_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "backendPipelines"))


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


@pytest.mark.unit
class TestJsonConfigBodyWithTemplateTags:
    """A json-format config body carries {{tagName}} placeholders, so the save-time parse check stands
    each tag in for the JSON its renderer emits: an object/array/number literal for the tags substituted
    as JSON literals, bare text for a scalar tag (valid only inside the template's own quotes). That
    keeps the surrounding structure validated while the body remains authorable.

    The quoted form of an object-valued tag is rejected: it parses as JSON but renders an object literal
    inside the string's quotes, so the pipeline receives malformed JSON at run time."""

    def _json(self, body):
        return CreateTemplateRequestModel(
            templateName="t", configFormat="json", configBody=body)

    @pytest.mark.parametrize("tag", sorted(tr.STRUCTURED_TAG_NAMES))
    def test_every_structured_tag_is_accepted_unquoted_as_a_whole_value(self, tag):
        m = self._json('{"payload": {{%s}}}' % tag)
        assert m.configBody == '{"payload": {{%s}}}' % tag

    @pytest.mark.parametrize("tag", sorted(tr.STRUCTURED_TAG_NAMES))
    def test_every_structured_tag_is_rejected_when_quoted(self, tag):
        with pytest.raises(ValidationError) as excinfo:
            self._json('{"payload": "{{%s}}"}' % tag)
        message = str(excinfo.value)
        assert "renders a JSON object or array" in message
        # Rule 11: the message names the allowed shape, never the caller's body or the tag they wrote.
        assert tag not in message

    def test_a_scalar_tag_inside_quotes_is_accepted(self):
        m = self._json('{"key": "{{firstAssetFileKey}}", "n": {{assetFileCount}}}')
        assert "firstAssetFileKey" in m.configBody

    def test_a_scalar_tag_embedded_in_a_longer_string_is_accepted(self):
        assert self._json('{"uri": "s3://{{outputBucket}}/{{outputFilesPrefix}}out.glb"}')

    def test_a_user_tag_inside_quotes_is_accepted(self):
        # A template's own tag is not in the system catalog; it stands in as text, which is where the
        # resolver substitutes it (escaped, inside the template's quotes).
        assert self._json('{"PROMPT": "{{PROMPT}}", "NEG": "{{NEGATIVE_PROMPT}}"}')

    def test_a_user_tag_as_a_bare_value_is_rejected(self):
        with pytest.raises(ValidationError) as excinfo:
            self._json('{"PROMPT": {{PROMPT}}}')
        assert "belongs inside the JSON string it fills" in str(excinfo.value)

    def test_broken_json_around_valid_tags_is_still_rejected(self):
        with pytest.raises(ValidationError):
            self._json('{"payload": {{assetFileKeyArray}}, "key": "{{firstAssetFileKey}}"')

    def test_a_tag_free_body_still_needs_to_parse(self):
        with pytest.raises(ValidationError):
            self._json("not json")

    def test_the_update_model_applies_the_same_check(self):
        assert UpdateTemplateRequestModel(
            configFormat="json", configBody='{"m": {{databaseMetadataObject}}}')
        with pytest.raises(ValidationError):
            UpdateTemplateRequestModel(
                configFormat="json", configBody='{"m": "{{databaseMetadataObject}}"}')

    def test_a_non_json_format_body_is_not_shape_checked(self):
        # yaml/openjd/xml/raw bodies are passed through, so a quoted object tag there is the author's
        # business — the JSON rule does not apply to them.
        assert CreateTemplateRequestModel(
            templateName="t", configFormat="yaml",
            configBody='payload: "{{databaseMetadataObject}}"')


@pytest.mark.unit
class TestJsonBodyPlaceholderText:
    """The placeholder substitution the save-time check parses. Each tag's stand-in has to match the
    JSON its renderer emits, or the check would validate a shape the pipeline never receives."""

    def test_object_tags_stand_in_as_object_literals(self):
        assert tr.json_body_placeholder_text("{{databaseMetadataObject}}") == "{}"

    def test_array_tags_stand_in_as_array_literals(self):
        assert tr.json_body_placeholder_text("{{assetFileKeyArray}}") == "[]"

    def test_the_count_tag_stands_in_as_a_number(self):
        assert tr.json_body_placeholder_text("{{assetFileCount}}") == "0"

    @pytest.mark.parametrize("declared", ["integer", "INTEGER"])
    def test_a_typed_user_tag_is_classified_by_its_normalized_type(self, declared):
        # validate_tag_schema accepts a declared type in any casing, so the stand-in has to be chosen
        # the same way: classifying "INTEGER" as text would reject the correct unquoted body and accept
        # the quoted one, which renders the string "7" where the schema promised 7.
        schema = [{"tagKey": "scale", "type": declared}]
        assert tr.user_tag_shapes(schema) == {"scale": "0"}
        assert tr.json_body_placeholder_text('{"s": {{scale}}}', tag_schema=schema) == '{"s": 0}'
        assert CreateTemplateRequestModel(
            templateName="t", configFormat="json", configBody='{"s": {{scale}}}',
            tagSchema=schema)
        with pytest.raises(ValidationError) as excinfo:
            CreateTemplateRequestModel(
                templateName="t", configFormat="json", configBody='{"s": "{{scale}}"}',
                tagSchema=schema)
        assert "takes no quotes" in str(excinfo.value)

    def test_scalar_and_unknown_tags_stand_in_as_bare_text(self):
        assert tr.json_body_placeholder_text(
            "{{firstAssetFileKey}}|{{PROMPT}}") == f"{tr.SCALAR_TAG_PLACEHOLDER}|{tr.SCALAR_TAG_PLACEHOLDER}"

    def test_the_string_pass_quotes_only_the_structured_tags(self):
        text = tr.json_body_placeholder_text(
            "{{assetFileKeyArray}} {{assetFileCount}} {{firstAssetFileKey}}",
            structured_as_string=True)
        assert text == f'"{tr.SCALAR_TAG_PLACEHOLDER}" 0 {tr.SCALAR_TAG_PLACEHOLDER}'

    def test_text_without_tags_is_unchanged(self):
        assert tr.json_body_placeholder_text('{"a": 1}') == '{"a": 1}'
        assert tr.json_body_placeholder_text("") == ""
        assert tr.json_body_placeholder_text(None) == ""

    def test_every_built_in_template_body_saves(self):
        # The registration path builds a CreateTemplateRequestModel per shipped template, so a body the
        # save-time check rejects fails the CDK import rather than surfacing in any API test.
        files = sorted(set(
            glob.glob(os.path.join(_PIPELINES_ROOT, "**", "templates", "*.json"), recursive=True)))
        # A floor rather than an exact count: the glob silently matching nothing (or one subtree) would
        # pass the loop while validating nothing, which is the failure this test exists to prevent.
        assert len(files) >= 25, f"expected the shipped template schemas, found {len(files)}"
        rejected = []
        typed_schemas = 0
        for path in files:
            body = json.load(open(path, encoding="utf-8"))
            # tagSchema MUST be passed: vamsSchemaImport._template_create_body includes it, so this is
            # the request the CDK registration actually builds. Omitting it validated every typed user
            # tag as text, which both hid the defect being fixed here and would have accepted a body the
            # deployed gate rejects — a green test for a registration that fails the stack.
            tag_schema = body.get("tagSchema")
            if any(isinstance(f, dict) and f.get("type") in ("integer", "number", "boolean",
                                                             "string-list")
                   for f in (tag_schema or [])):
                typed_schemas += 1
            try:
                CreateTemplateRequestModel(
                    templateName=body.get("templateName") or "t",
                    configFormat=body.get("configFormat", "json"),
                    configBody=body.get("configBody", ""),
                    tagSchema=tag_schema)
            except Exception as e:
                rejected.append(f"{os.path.relpath(path, _PIPELINES_ROOT)}: {e}")
        assert not rejected, rejected
        # Positive control for the line above: at least one shipped template must declare a non-text
        # tag type, or the tagSchema argument is inert here and this test would pass even with the
        # type-aware gate disabled entirely.
        assert typed_schemas >= 1, (
            "no shipped template declares an integer/number/boolean/string-list tag, so this test no "
            "longer exercises the type-aware json gate")

    def test_a_typed_tag_cannot_be_declared_optional_without_a_default(self):
        # A blank integer/number/boolean has no value to materialize, so a body referencing one would
        # fail EVERY execution as an unmatched tag — including headless trigger runs, whose only trace
        # is a dispatcher log line. The declaration is where it is caught, so the tag is either
        # required or carries a default and validate_tags can always fill it.
        for tag_type in sorted(ts.TYPES_WITHOUT_EMPTY_VALUE):
            errs = ts.validate_tag_schema([{"tagKey": "v", "type": tag_type}])
            assert any("no blank form" in e for e in errs), tag_type
            assert ts.validate_tag_schema(
                [{"tagKey": "v", "type": tag_type, "required": True}]) == [], tag_type
            assert ts.validate_tag_schema(
                [{"tagKey": "v", "type": tag_type, "default": 1}]) == [], tag_type

    def test_the_types_with_a_blank_form_stay_optional(self):
        for tag_type, extra in ((ts.TAG_TYPE_STRING, {}), (ts.TAG_TYPE_ENUM, {"enumValues": ["a"]})):
            assert ts.validate_tag_schema([dict({"tagKey": "v", "type": tag_type}, **extra)]) == []

    def test_a_case_variant_type_is_held_to_the_same_rule(self):
        # The type is normalized before the rule is applied, matching how it is normalized everywhere
        # else — otherwise "INTEGER" would slip past the declaration gate.
        errs = ts.validate_tag_schema([{"tagKey": "v", "type": "INTEGER"}])
        assert any("no blank form" in e for e in errs)

    def test_the_headless_guard_flags_a_typed_tag_with_no_default(self):
        # required_tags_without_default backs the trigger-save / template-save guards, and a typed tag
        # with no default is as unsupplyable headlessly as a required one.
        assert ts.required_tags_without_default([{"tagKey": "n", "type": "integer"}]) == ["n"]
        assert ts.required_tags_without_default(
            [{"tagKey": "n", "type": "integer", "default": 0}]) == []
        assert ts.required_tags_without_default([{"tagKey": "s", "type": "string"}]) == []

    def test_every_json_kind_tag_has_a_shape_and_no_scalar_tag_does(self):
        # The shapes are derived from the renderer's own context, so a new tag is classified by the kind
        # it is registered with rather than by a hand-maintained list here.
        context = tr.build_template_context({}, {})
        context.update(tr._metadata_context({}))
        json_kind = {name for name, (kind, _v) in context.items() if kind == "json"}
        assert set(tr.JSON_LITERAL_TAG_SHAPES) == json_kind
        assert tr.STRUCTURED_TAG_NAMES <= json_kind
        assert "firstAssetFileKey" not in tr.JSON_LITERAL_TAG_SHAPES
