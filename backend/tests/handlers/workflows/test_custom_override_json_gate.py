# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A caller-supplied ``customTemplateOverride`` is held to the same shape rules as a stored body.

An override arrives at LAUNCH, so it never passed the save-time gate the stored body did. Two things
could therefore reach a pipeline through it that could not be stored:

* structurally broken JSON, and
* a quoted placeholder for a tag declared integer/number/boolean/string-list, which parses but delivers
  the string ``"150"`` where the schema promised ``150``.

Neither is reported downstream. Every pipeline-side config reader treats an unparseable configuration as
absent and falls back to its defaults, so the run SUCCEEDS with the caller's parameters silently dropped
(``manifest_io._get_json`` returns ``None`` on any exception; the callers then use their default dict).
That is why this is checked at the door rather than left to the pipeline.

The template-less override case (no ``templateId``) is deliberately NOT checked — it resolves as ``raw``
and makes no claim to be JSON. ``test_a_template_less_override_is_not_shape_checked`` pins that so the
exemption is a decision rather than an oversight.
"""

import pytest

from common.workflows import templateResolution as tr

_PSC_ALLOWS_OVERRIDE = {"requireTemplate": False, "allowCustomTemplateOverride": True}
_INT_SCHEMA = [{"tagKey": "STEPS", "type": "integer"}]
_STR_SCHEMA = [{"tagKey": "NAME", "type": "string"}]


def _json_template(config_body, allow_custom_edit=False):
    row = {"templateId": "t1", "configBody": config_body, "configFormat": "json"}
    if allow_custom_edit:
        row["allowCustomEdit"] = True
    return row


def _resolve(override, schema=_INT_SCHEMA, tags=None, stored='{"steps": {{STEPS}}}'):
    return tr.resolve_pipeline_config(
        _PSC_ALLOWS_OVERRIDE,
        _json_template(stored),
        schema,
        {"templateId": "t1",
         "templateTags": tags if tags is not None else [{"key": "STEPS", "value": 150}],
         "customTemplateOverride": override},
    )


@pytest.mark.unit
class TestOverrideStructuralCheck:
    """A json-format override must parse, with its placeholders standing in for what they render."""

    def test_a_correct_override_is_accepted_and_renders_the_declared_type(self):
        """POSITIVE CONTROL for every rejection below: the happy path must still work, and the value
        must arrive as a number rather than text."""
        errors, resolved = _resolve('{"steps": {{STEPS}}, "extra": "x"}')
        assert errors == []
        assert resolved["renderedConfig"] == '{"steps": 150, "extra": "x"}'
        assert resolved["customTemplateOverrideUsed"] is True

    def test_structurally_broken_json_is_rejected(self):
        errors, resolved = _resolve('{"steps": {{STEPS}},,}')
        assert resolved is None
        assert errors and "customTemplateOverride is not valid" in errors[0]

    def test_a_quoted_typed_placeholder_is_rejected(self):
        """The silent one. `{"steps": "{{STEPS}}"}` parses perfectly and delivers "150"."""
        errors, resolved = _resolve('{"steps": "{{STEPS}}"}')
        assert resolved is None
        assert errors and "customTemplateOverride is not valid" in errors[0]

    def test_a_quoted_string_placeholder_is_still_accepted(self):
        """REGRESSION GUARD: a string tag renders text, so quoting it is correct and must not be
        caught by the new check."""
        errors, resolved = _resolve('{"name": "{{NAME}}"}', schema=_STR_SCHEMA,
                                    tags=[{"key": "NAME", "value": "widget"}],
                                    stored='{"name": "{{NAME}}"}')
        assert errors == []
        assert resolved["renderedConfig"] == '{"name": "widget"}'

    def test_the_stored_body_is_unaffected_when_no_override_is_supplied(self):
        """The check must apply to the override only. A run with no override resolves the stored body,
        which already passed this gate at save time."""
        errors, resolved = tr.resolve_pipeline_config(
            _PSC_ALLOWS_OVERRIDE,
            _json_template('{"steps": {{STEPS}}}'),
            _INT_SCHEMA,
            {"templateId": "t1", "templateTags": [{"key": "STEPS", "value": 150}]},
        )
        assert errors == []
        assert resolved["renderedConfig"] == '{"steps": 150}'
        assert resolved["customTemplateOverrideUsed"] is False

    def test_a_non_json_format_override_is_passed_through(self):
        """yaml/openjd/xml/raw bodies are text at save time and stay text here."""
        errors, resolved = tr.resolve_pipeline_config(
            _PSC_ALLOWS_OVERRIDE,
            {"templateId": "t1", "configBody": "stored: 1", "configFormat": "yaml"},
            _INT_SCHEMA,
            {"templateId": "t1", "templateTags": [{"key": "STEPS", "value": 150}],
             "customTemplateOverride": "steps: {{STEPS}}"},
        )
        assert errors == []
        assert resolved["renderedConfig"] == "steps: 150"

    def test_an_override_is_checked_under_the_template_allow_custom_edit_grant_too(self):
        """Both grants reach the same body, so both must reach the same check: the pipeline's
        allowCustomTemplateOverride and the template's own allowCustomEdit."""
        errors, resolved = tr.resolve_pipeline_config(
            {"requireTemplate": True, "allowCustomTemplateOverride": False},
            _json_template('{"steps": {{STEPS}}}', allow_custom_edit=True),
            _INT_SCHEMA,
            {"templateId": "t1", "templateTags": [{"key": "STEPS", "value": 150}],
             "customTemplateOverride": '{"steps": "{{STEPS}}"}'},
        )
        assert resolved is None
        assert errors and "customTemplateOverride is not valid" in errors[0]

    def test_an_override_that_is_refused_outright_still_reports_the_grant_error(self):
        """Ordering: a pipeline that forbids overrides must say so, not complain about JSON shape."""
        errors, resolved = tr.resolve_pipeline_config(
            {"requireTemplate": False, "allowCustomTemplateOverride": False},
            _json_template('{"steps": {{STEPS}}}'),
            _INT_SCHEMA,
            {"templateId": "t1", "templateTags": [{"key": "STEPS", "value": 150}],
             "customTemplateOverride": '{"steps": {{STEPS}},,}'},
        )
        assert resolved is None
        assert errors == ["this pipeline does not allow a custom template override"]

    def test_a_template_less_override_is_not_shape_checked(self):
        """Pins the documented exemption. With no templateId there is no declared configFormat: the
        body resolves as `raw` and claims nothing, so there is nothing to hold it to. A pipeline that
        needs a checked json body declares a template.
        """
        errors, resolved = tr.resolve_pipeline_config(
            _PSC_ALLOWS_OVERRIDE, None, None,
            {"customTemplateOverride": '{"steps": {{n}},,}', "templateTags": [{"key": "n", "value": 5}]},
        )
        assert errors == []
        assert resolved["configFormat"] == tr.CONFIG_FORMAT_RAW

    def test_the_error_does_not_echo_the_callers_body_or_tag_key(self):
        """Rule 11: a validator message names the rule, never the caller's input. The tagKey and the
        body text are both caller-supplied."""
        errors, _ = _resolve('{"steps": "{{STEPS}}", "secret": "s3cr3t-value"}')
        assert errors
        message = errors[0]
        assert "s3cr3t-value" not in message
        assert "STEPS" not in message
