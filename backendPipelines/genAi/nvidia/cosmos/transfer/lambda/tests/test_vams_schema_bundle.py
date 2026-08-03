#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the cosmos transfer vamsSchema bundle.

The pipeline writes an .mp4 back into the input asset and the fileUpload trigger allows .mp4, so the
trigger must exclude the pipeline's own output naming pattern or an enabled trigger re-fires on its
own result. Every configBody key must also be one the vamsExecute lambda or the container reads."""

import os
import json
import fnmatch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))

# Keys read from the fetched input configuration. Kept in step with the two consumers:
#   container/inference.py            — DISABLE_GUARDRAILS, CONTROL_WEIGHT
#   lambda/vamsExecuteCosmosTransferPipeline.py — PROMPT/prompt, CONTROL_TYPE/controlType,
#                                                 CONTROL_PATH/controlPath (each with a
#                                                 COSMOS_TRANSFER_* asset-metadata fallback)
# A key in the shipped configBody that no consumer reads is silently ignored at run time, which is
# what this set exists to catch — so it must list every key that IS read, or it reports false alarms.
_CONSUMED_CONFIG_KEYS = {
    "DISABLE_GUARDRAILS",
    "CONTROL_WEIGHT",
    "PROMPT",
    "prompt",
    "CONTROL_TYPE",
    "controlType",
    "CONTROL_PATH",
    "controlPath",
}

# The container's output filename shape: {stem}_CosmosTransfer_{controlType}_{timestamp}.mp4
_PRODUCED_OUTPUT_NAME = "clip_CosmosTransfer_edge_20260101-000000.mp4"


def _load(*parts):
    with open(os.path.join(_SCHEMA_ROOT, *parts), encoding="utf-8") as handle:
        return json.load(handle)


def _passes_filters(file_name, filters):
    """Mirror of executionValidation.apply_input_file_filters for a single file name (lowercased
    fnmatch against allow then exclude)."""
    name = file_name.lower()
    allow = [p.lower() for p in filters.get("allow") or []]
    exclude = [p.lower() for p in filters.get("exclude") or []]
    if allow and not any(fnmatch.fnmatchcase(name, p) for p in allow):
        return False
    if any(fnmatch.fnmatchcase(name, p) for p in exclude):
        return False
    return True


@pytest.mark.unit
class TestTransferBundle:
    def test_trigger_does_not_fire_on_the_pipelines_own_output(self):
        filters = _load("workflow.json")["triggers"][0]["inputFileFilters"]
        assert _passes_filters(_PRODUCED_OUTPUT_NAME, filters) is False

    def test_trigger_still_fires_on_a_source_upload(self):
        filters = _load("workflow.json")["triggers"][0]["inputFileFilters"]
        assert _passes_filters("clip.mp4", filters) is True
        assert _passes_filters("clip.mov", filters) is True

    def test_configBody_keys_are_all_consumed(self):
        config = json.loads(_load("templates", "cosmos-transfer-edge-2b.json")["configBody"])
        assert set(config) <= _CONSUMED_CONFIG_KEYS
