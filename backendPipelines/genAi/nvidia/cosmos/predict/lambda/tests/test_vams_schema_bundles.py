#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the cosmos predict vamsSchema template bundles.

A template's configBody is the run's user-editable knob set, so every key must be one the container
or the vamsExecute lambda actually reads. The model type and size are fixed per registered pipeline
(constructPipeline supplies modelType; the Batch job definition supplies MODEL_SIZE), so they are not
config keys."""

import os
import json

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))

# Keys read from the fetched input configuration: the flag set in containerv2.5/__main__.py plus
# PROMPT / prompt read by the vamsExecute lambdas.
_CONSUMED_CONFIG_KEYS = {
    "INVALIDATE_COSMOS_MODELS", "DISABLE_GUARDRAILS", "GENERATE_PREVIEW_GIF",
    "OFFLOAD_TEXT_ENCODER", "OFFLOAD_TOKENIZER", "OFFLOAD_DIFFUSION_MODEL",
    "PROMPT", "prompt",
}


def _template(bundle):
    template_dir = os.path.join(_SCHEMA_ROOT, bundle, "templates")
    name = next(n for n in sorted(os.listdir(template_dir)) if n.endswith(".json"))
    with open(os.path.join(template_dir, name), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.unit
class TestText2World14bTemplate:
    def test_configBody_keys_are_all_consumed(self):
        config = json.loads(_template("text2world-14b")["configBody"])
        assert set(config) <= _CONSUMED_CONFIG_KEYS

    def test_template_is_the_pipeline_default(self):
        assert _template("text2world-14b")["isDefault"] is True
