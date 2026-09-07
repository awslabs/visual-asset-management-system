#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the meshCadMetadataExtraction vamsSchema bundle.

The fileUpload trigger filter decides which uploads dispatch an execution, so it must cover every
extension the pipeline accepts (and the extractors handle) — otherwise an accepted format never
auto-extracts. The reverse matters just as much: a declared extension the extractors cannot load is
selectable in the execute form and auto-triggered on upload, and it reaches a load failure rather
than an attribute set."""

import os
import json
import importlib.util

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))

# Formats trimesh registers a loader for only when an optional dependency is present: `.dae` needs
# pycollada, and `.xaml` / `.3dxml` need lxml and networkx. The container installs none of them
# (requirements.txt), so trimesh answers a load of one of these with an ExceptionWrapper.
_FORMATS_WITHOUT_A_LOADER = (".dae", ".xaml", ".3dxml")


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

    def test_the_bundle_files_this_module_checks_are_the_ones_that_exist(self):
        # Every assertion here reads a named file, so a bundle file added later would be checked by
        # nothing while the suite still passed. The count is asserted in-band rather than inferred
        # from a glob that could silently match nothing.
        present = sorted(name for name in os.listdir(_SCHEMA_ROOT) if name.endswith(".json"))
        assert present == ["pipeline.json", "workflow.json"], present
        assert not os.path.isdir(os.path.join(_SCHEMA_ROOT, "templates")), \
            "the bundle grew a templates/ directory that no test reads"

    def test_formats_without_a_loader_are_not_declared(self):
        pipeline_allow = _load("pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        trigger_allow = _load("workflow.json")["triggers"][0]["inputFileFilters"]["allow"]
        supported = _supported_formats()
        assert len(_FORMATS_WITHOUT_A_LOADER) == 3, _FORMATS_WITHOUT_A_LOADER
        for extension in _FORMATS_WITHOUT_A_LOADER:
            assert f"*{extension}" not in pipeline_allow, extension
            assert f"*{extension}" not in trigger_allow, extension
            assert extension not in supported, extension

    def test_the_descriptions_name_no_format_the_bundle_does_not_allow(self):
        # An operator picks the pipeline from its description, so a format named there but absent
        # from the filter reads as supported and is simply unselectable.
        allowed = {
            extension.lstrip("*.").upper()
            for extension in _load("pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        }
        for source in ("pipeline.json", "workflow.json"):
            description = _load(source)["description"]
            named = {
                token.strip(" .,")
                for token in description.split("Supported files are")[-1].split(",")
            }
            assert named, source
            assert named <= allowed, (source, sorted(named - allowed))
