#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on EVERY cosmos predict template bundle.

A template's configBody is the run's user-editable knob set, so every key must be one the container
or the vamsExecute lambda actually reads, and every tag the operator is asked for must reach the
body. The model type and size are fixed per registered pipeline (constructPipeline supplies
modelType; the Batch job definition supplies MODEL_SIZE), so they are not config keys.

Each check counts the files it validated and asserts that count, because a bundle-validation test
whose glob resolves to nothing passes while measuring nothing."""

import os
import re
import json

import pytest

from common.workflows.templateTags import SYSTEM_TAG_NAMES, METADATA_DYNAMIC_TAG_PREFIX

_SCHEMA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every bundle shipped under this vamsSchema root. Named rather than globbed so an added bundle
# fails here until it is brought into these checks.
_BUNDLES = ("text2world-2b", "text2world-14b", "video2world-2b", "video2world-14b")

# Keys read from the fetched input configuration: the flag set in containerv2.5/__main__.py plus
# PROMPT / prompt read by the vamsExecute lambdas.
_CONSUMED_CONFIG_KEYS = {
    "INVALIDATE_COSMOS_MODELS", "DISABLE_GUARDRAILS", "GENERATE_PREVIEW_GIF",
    "OFFLOAD_TEXT_ENCODER", "OFFLOAD_TOKENIZER", "OFFLOAD_DIFFUSION_MODEL",
    "PROMPT", "prompt",
}

_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _template_files():
    """Every top-level templates/*.json in every bundle -- the files the registration construct
    uploads and the importer reads."""
    found = []
    for bundle in _BUNDLES:
        template_dir = os.path.join(_SCHEMA_ROOT, bundle, "templates")
        for name in sorted(os.listdir(template_dir)):
            if name.endswith(".json"):
                found.append((bundle, os.path.join(template_dir, name)))
    return found


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.unit
class TestEveryPredictTemplate:
    def test_every_bundle_ships_exactly_one_template(self):
        found = _template_files()
        assert len(found) == len(_BUNDLES) == 4
        assert sorted(bundle for bundle, _ in found) == sorted(_BUNDLES)

    def test_configBody_keys_are_all_consumed(self):
        offenders, checked = {}, []
        for bundle, path in _template_files():
            config = json.loads(_load(path)["configBody"])
            unread = sorted(set(config) - _CONSUMED_CONFIG_KEYS)
            if unread:
                offenders[bundle] = unread
            checked.append(bundle)
        assert len(checked) == 4, f"validated {len(checked)} of 4 templates: {checked}"
        assert not offenders, f"configBody keys nothing reads: {offenders}"

    def test_every_declared_tag_is_referenced_by_the_body(self):
        # A declared tag the body never references renders a form field that reaches no pipeline.
        offenders, checked = {}, []
        for bundle, path in _template_files():
            template = _load(path)
            referenced = {m.group(1) for m in _PLACEHOLDER.finditer(template["configBody"])}
            unused = sorted({tag["tagKey"] for tag in template.get("tagSchema", [])} - referenced)
            if unused:
                offenders[bundle] = unused
            checked.append(bundle)
        assert len(checked) == 4, f"validated {len(checked)} of 4 templates: {checked}"
        assert not offenders, f"tagSchema declares tags the configBody never uses: {offenders}"

    def test_every_body_placeholder_is_declared_or_a_system_tag(self):
        # The reverse direction: an undeclared placeholder renders literally into the config the
        # container reads.
        offenders, checked = {}, []
        for bundle, path in _template_files():
            template = _load(path)
            declared = {tag["tagKey"] for tag in template.get("tagSchema", [])}
            undeclared = sorted(
                m.group(1) for m in _PLACEHOLDER.finditer(template["configBody"])
                if m.group(1) not in declared and m.group(1) not in SYSTEM_TAG_NAMES
                and not m.group(1).startswith(METADATA_DYNAMIC_TAG_PREFIX))
            if undeclared:
                offenders[bundle] = undeclared
            checked.append(bundle)
        assert len(checked) == 4, f"validated {len(checked)} of 4 templates: {checked}"
        assert not offenders, f"configBody references undeclared tags: {offenders}"

    def test_each_template_is_its_pipeline_default(self):
        # requireTemplate with no default forces every caller to name a templateId.
        offenders, checked = [], []
        for bundle, path in _template_files():
            if _load(path).get("isDefault") is not True:
                offenders.append(bundle)
            checked.append(bundle)
        assert len(checked) == 4, f"validated {len(checked)} of 4 templates: {checked}"
        assert not offenders, f"bundles whose only template is not the default: {offenders}"
