#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the cosmos reason vamsSchema bundles.

openPipeline hard-rejects an input whose extension is outside ALLOWED_INPUT_FILEEXTENSIONS (failing
the execution via a task-failure callback), so the registered inputFileFilters must not advertise
extensions the lambda refuses. A template's configBody is the run's user-editable knob set, so every
key must be one the container or the vamsExecute lambda reads — the model type and size are fixed per
registered pipeline (openPipeline supplies modelType; the Batch job definition supplies MODEL_SIZE)."""

import os
import sys
import json
import types
import importlib
from unittest.mock import MagicMock

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

_SCHEMA_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))

# Keys read from the fetched input configuration: INVALIDATE_COSMOS_MODELS by the container,
# PROMPT / prompt by the vamsExecute lambda.
_CONSUMED_CONFIG_KEYS = {"INVALIDATE_COSMOS_MODELS", "PROMPT", "prompt"}

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for _k, _v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:CosmosReason",
}.items():
    os.environ.setdefault(_k, _v)


def _load(*parts):
    with open(os.path.join(_SCHEMA_ROOT, *parts), encoding="utf-8") as handle:
        return json.load(handle)


def _lambda_allowed_extensions():
    """openPipeline's accepted extension set (its default mirrors the CDK-supplied env value)."""
    if "openPipeline" in sys.modules:
        module = importlib.reload(sys.modules["openPipeline"])
    else:
        module = importlib.import_module("openPipeline")
    return {ext.strip() for ext in module.ALLOWED_INPUT_FILEEXTENSIONS.split(",") if ext.strip()}


@pytest.mark.unit
class TestReason2bBundle:
    def test_pipeline_filter_matches_the_lambda_allow_list(self):
        allow = _load("reason-2b", "pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        assert {ext.lstrip("*") for ext in allow} == _lambda_allowed_extensions()

    def test_trigger_filter_matches_the_pipeline_filter(self):
        pipeline_allow = _load("reason-2b", "pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        trigger_allow = _load("reason-2b", "workflow.json")["triggers"][0]["inputFileFilters"]["allow"]
        assert sorted(trigger_allow) == sorted(pipeline_allow)


@pytest.mark.unit
class TestReason8bTemplate:
    def test_configBody_keys_are_all_consumed(self):
        config = json.loads(_load("reason-8b", "templates", "cosmos-reason-8b.json")["configBody"])
        assert set(config) <= _CONSUMED_CONFIG_KEYS
