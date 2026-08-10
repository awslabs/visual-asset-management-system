#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the coordinateTransform vamsSchema bundle.

The Batch container aborts unless the resolved transform parameters carry sourceCrs and targetCrs,
and the only guaranteed source of those is the template config body — so the pipeline must require a
template rather than let a template-less execution reach the container."""

import os
import json

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))

# Parameters container/coord_transform_pipeline/core.py hard-requires.
_REQUIRED_TRANSFORM_PARAMS = ("sourceCrs", "targetCrs")


def _load(*parts):
    with open(os.path.join(_SCHEMA_ROOT, *parts), encoding="utf-8") as handle:
        return json.load(handle)


def _templates():
    template_dir = os.path.join(_SCHEMA_ROOT, "templates")
    return [_load("templates", name) for name in sorted(os.listdir(template_dir))
            if name.endswith(".json")]


@pytest.mark.unit
class TestCoordinateTransformBundle:
    def test_pipeline_requires_a_template(self):
        assert _load("pipeline.json")["systemConfig"]["requireTemplate"] is True

    def test_every_template_supplies_the_required_transform_parameters(self):
        templates = _templates()
        assert templates
        for template in templates:
            config = json.loads(template["configBody"])
            for key in _REQUIRED_TRANSFORM_PARAMS:
                assert config.get(key), f"{template['templateId']} is missing {key}"

    def test_trigger_default_template_exists(self):
        trigger = _load("workflow.json")["triggers"][0]
        template_ids = {template["templateId"] for template in _templates()}
        for template_id in trigger["defaultTemplateIds"].values():
            assert template_id in template_ids
