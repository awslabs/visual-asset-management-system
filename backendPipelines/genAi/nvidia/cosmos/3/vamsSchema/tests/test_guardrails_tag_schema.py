#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests that DISABLE_GUARDRAILS is an operator-settable tag on every shipped Cosmos 3 template.

The container decides whether the NVIDIA content-safety guardrails run from the DISABLE_GUARDRAILS key
of the rendered input configuration (container/__main__.py). While every template hardcoded that key
there was no way to run a generation WITH guardrails short of hand-editing the body, so the flag is
declared as a tag instead — and it keeps the shipped default of true (guardrails off), because the
guardrail models load alongside the generation model and need a larger instance than the shipped
compute environments provide.

Two shapes of the tag would ship broken and neither fails at runtime:

  - A boolean tag declared optional with no default is REJECTED by validate_tag_schema
    (TYPES_WITHOUT_EMPTY_VALUE), so the template service refuses it and the CDK registration custom
    resource fails the deploy. The negative control below is what proves that validator is live.
  - A tag the configBody never references is silently dropped, so the operator fills in a field whose
    value reaches no pipeline.

Everything here runs the real backend validators and renderer against the shipped files.

Guards FIX-050 (S4-PIPELINES-048): content-safety guardrails defaulting off in the container and
hardcoded off in every shipped template.
"""

import glob
import json
import os

import pytest

from common.workflows.templateResolution import _substitute_user_tags
from common.workflows.templateTagSchema import (
    required_tags_without_default,
    validate_tag_schema,
    validate_tags,
)
from common.workflows import templateRender as tr

_SCHEMA_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_GUARDRAILS_TAG = "DISABLE_GUARDRAILS"


def _templates():
    """(path, template dict) for every shipped Cosmos 3 bundle template."""
    found = []
    for path in sorted(glob.glob(os.path.join(_SCHEMA_ROOT, "*", "templates", "*.json"))):
        with open(path, encoding="utf-8") as handle:
            found.append((path, json.load(handle)))
    return found


def _tag(template, tag_key):
    for field in template.get("tagSchema") or []:
        if isinstance(field, dict) and field.get("tagKey") == tag_key:
            return field
    return None


@pytest.mark.unit
class TestGuardrailsTagIsExposed:
    def test_bundle_discovery_is_not_vacuous(self):
        # Every assertion below iterates the discovered set, so an empty set would pass them all.
        assert len(_templates()) >= 9, f"template discovery broke: {_SCHEMA_ROOT}"

    def test_every_template_declares_the_guardrails_tag_with_the_shipped_default(self):
        offenders = []
        for path, template in _templates():
            name = os.path.basename(path)
            field = _tag(template, _GUARDRAILS_TAG)
            if field is None:
                offenders.append(f"{name}: {_GUARDRAILS_TAG} not declared in tagSchema")
                continue
            if field.get("type") != "boolean":
                offenders.append(f"{name}: type is {field.get('type')!r}, expected 'boolean'")
            # The default is what keeps the declaration legal at all (a boolean has no blank form) AND
            # what preserves the shipped behaviour of guardrails-off.
            if field.get("default") is not True:
                offenders.append(f"{name}: default is {field.get('default')!r}, expected True")
            if field.get("required"):
                offenders.append(f"{name}: the tag is required; it must stay optional")
        assert not offenders, offenders

    def test_the_guardrails_key_is_no_longer_hardcoded_in_any_config_body(self):
        offenders = []
        for path, template in _templates():
            body = template.get("configBody") or ""
            placeholder = "{{" + _GUARDRAILS_TAG + "}}"
            if placeholder not in body:
                offenders.append(f"{os.path.basename(path)}: configBody does not reference "
                                 f"{placeholder}, so the operator's value is dropped")
            parsed = json.loads(tr.json_body_placeholder_text(body, tag_schema=template.get("tagSchema")))
            if not isinstance(parsed.get(_GUARDRAILS_TAG), bool):
                offenders.append(f"{os.path.basename(path)}: {_GUARDRAILS_TAG} renders "
                                 f"{parsed.get(_GUARDRAILS_TAG)!r}, not a JSON boolean")
        assert not offenders, offenders

    def test_every_declared_tag_is_referenced_by_its_own_config_body(self):
        # Wider than the guardrails tag: a declared-but-unreferenced tag is a form field that reaches
        # no pipeline, with no error anywhere.
        offenders = []
        for path, template in _templates():
            body = template.get("configBody") or ""
            for field in template.get("tagSchema") or []:
                key = field.get("tagKey")
                if key and "{{" + key + "}}" not in body:
                    offenders.append(f"{os.path.basename(path)}: {key} declared but not referenced")
        assert not offenders, offenders

    def test_every_shipped_tag_schema_passes_the_real_declaration_validator(self):
        for path, template in _templates():
            errors = validate_tag_schema(template.get("tagSchema"))
            assert errors == [], f"{os.path.basename(path)}: {errors}"

    def test_removing_the_default_is_rejected_so_the_validator_is_live(self):
        # Negative control for the sweep above. Without this, a validator that returned [] for
        # everything would make the sweep pass while the deploy still fails.
        for path, template in _templates():
            stripped = []
            for field in template.get("tagSchema") or []:
                field = dict(field)
                if field.get("tagKey") == _GUARDRAILS_TAG:
                    field.pop("default", None)
                stripped.append(field)
            errors = validate_tag_schema(stripped)
            assert any("no blank form" in e for e in errors), (
                f"{os.path.basename(path)}: a defaultless boolean tag was accepted; the declaration "
                f"gate is not running")

    def test_no_template_has_a_tag_a_headless_trigger_could_not_supply(self):
        # required_tags_without_default backs the trigger-save guard the CDK registration hits: a
        # trigger-referenced template with such a tag fails the registration custom resource.
        for path, template in _templates():
            missing = required_tags_without_default(template.get("tagSchema"))
            assert missing == [], f"{os.path.basename(path)}: {missing}"


@pytest.mark.unit
class TestGuardrailsValueReachesTheContainer:
    """The rendered configuration is what the container reads, so the assertion is on the value the
    container's own expression resolves to, not on the tag map."""

    @staticmethod
    def _container_disable_guardrails(rendered_config):
        """container/__main__.py: str(params.get("DISABLE_GUARDRAILS", "true")).lower() != "false"."""
        return str(rendered_config.get(_GUARDRAILS_TAG, "true")).lower() != "false"

    def _render(self, template, provided):
        errors, filled = validate_tags(template.get("tagSchema"), provided)
        assert errors == [], errors
        rendered, render_errors = _substitute_user_tags(
            template.get("configBody"), filled, "json")
        assert render_errors == [], render_errors
        return json.loads(rendered)

    @pytest.mark.parametrize("provided,expected", [
        ({}, True),
        ({_GUARDRAILS_TAG: True}, True),
        ({_GUARDRAILS_TAG: "true"}, True),
        ({_GUARDRAILS_TAG: False}, False),
        ({_GUARDRAILS_TAG: "false"}, False),
    ])
    def test_every_template_resolves_the_supplied_value(self, provided, expected):
        templates = _templates()
        assert templates, "template discovery broke"
        for path, template in templates:
            config = self._render(template, provided)
            assert self._container_disable_guardrails(config) is expected, (
                f"{os.path.basename(path)} with {provided}: container would read "
                f"{config.get(_GUARDRAILS_TAG)!r}")
