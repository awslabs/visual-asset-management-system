#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the meshCadMetadataExtraction vamsSchema bundle.

The fileUpload trigger filter decides which uploads dispatch an execution, so it must cover every
extension the pipeline accepts (and the extractors handle) — otherwise an accepted format never
auto-extracts."""

import os
import json
import importlib.util

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))


def _supported_formats():
    """The extractors' supported extension list, loaded from format_handlers directly so the CAD
    extractor's cadquery dependency is not needed."""
    spec = importlib.util.spec_from_file_location(
        "meshcad_format_handlers",
        os.path.join(_LAMBDA_DIR, "metadata_extractors", "format_handlers.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SUPPORTED_FORMATS


def _load(name):
    with open(os.path.join(_SCHEMA_ROOT, name), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.unit
class TestMeshCadBundle:
    def test_trigger_filter_matches_the_pipeline_filter(self):
        pipeline_allow = _load("pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        trigger_allow = _load("workflow.json")["triggers"][0]["inputFileFilters"]["allow"]
        assert sorted(trigger_allow) == sorted(pipeline_allow)

    def test_pipeline_filter_covers_every_extractor_format(self):
        pipeline_allow = _load("pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        assert sorted(pipeline_allow) == sorted(f"*{ext}" for ext in _supported_formats())
