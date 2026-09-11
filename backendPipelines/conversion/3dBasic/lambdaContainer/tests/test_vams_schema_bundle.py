#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on the conversion/3dBasic vamsSchema bundle.

The container keeps two sets: ``SUPPORTED_INPUT_FORMATS``, the formats trimesh loads, and
``SUPPORTED_OUTPUT_FORMATS``, the formats it writes. The bundle's ``inputFileFilters.allow`` has to
agree with the first and every template's target format with the second. A declared extension trimesh
cannot load is selectable in the execute form and fails on load; a template target trimesh cannot
export fails at the export.
"""

import os
import ast
import json

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))
_TEMPLATES_ROOT = os.path.join(_SCHEMA_ROOT, "templates")

# Formats trimesh registers a loader for only when an optional dependency is present: `.dae` needs
# pycollada, and `.xaml` / `.3dxml` need lxml and networkx. poetry.lock resolves this container to
# aws-lambda-powertools, jmespath, numpy, trimesh and typing-extensions only, so trimesh answers a
# load of one of these with an ExceptionWrapper. It also carries no `.xaml` / `.3dxml` exporter at
# any dependency set, so neither can be a conversion target either.
_FORMATS_WITHOUT_A_LOADER = (".dae", ".xaml", ".3dxml")

# `.xyz` loads as a PointCloud but cannot be written: trimesh's xyz exporter takes a PointCloud only
# and raises even for one, so it is the format the two sets differ by.
_LOADS_BUT_DOES_NOT_EXPORT = (".xyz",)


def _declared_formats(name):
    """A module-level format set read out of lambda.py's syntax tree, so the sets are checked without
    importing the module (and therefore without trimesh, boto3 or powertools)."""
    tree = ast.parse(open(os.path.join(_LAMBDA_DIR, "lambda.py"), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise AssertionError(f"lambda.py declares no module-level {name}")


def _input_formats():
    return _declared_formats("SUPPORTED_INPUT_FORMATS")


def _output_formats():
    return _declared_formats("SUPPORTED_OUTPUT_FORMATS")


def _load(*relative_parts):
    with open(os.path.join(_SCHEMA_ROOT, *relative_parts), encoding="utf-8") as handle:
        return json.load(handle)


def _template_files():
    """The bundle's template files. Only the top level is registered — the registration construct's
    schemaHash does not read a subdirectory — so only the top level is checked."""
    return sorted(name for name in os.listdir(_TEMPLATES_ROOT) if name.endswith(".json"))


@pytest.mark.unit
class Test3dBasicBundle:
    def test_pipeline_filter_matches_the_container_accept_list(self):
        pipeline_allow = _load("pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        assert sorted(pipeline_allow) == sorted(f"*{ext}" for ext in _input_formats())

    def test_a_format_it_cannot_write_is_still_an_accepted_input(self):
        # The accept list is the INPUT set, so narrowing the output set must not narrow it: `.xyz` is
        # the one format the two sets differ by and it stays selectable in the execute form.
        pipeline_allow = _load("pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        inputs, outputs = _input_formats(), _output_formats()
        assert len(_LOADS_BUT_DOES_NOT_EXPORT) == 1, _LOADS_BUT_DOES_NOT_EXPORT
        assert outputs < inputs, (sorted(outputs), sorted(inputs))
        assert inputs - outputs == set(_LOADS_BUT_DOES_NOT_EXPORT), sorted(inputs - outputs)
        for extension in _LOADS_BUT_DOES_NOT_EXPORT:
            assert f"*{extension}" in pipeline_allow, extension

    def test_formats_without_a_loader_are_not_declared(self):
        pipeline_allow = _load("pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        inputs, outputs = _input_formats(), _output_formats()
        assert len(_FORMATS_WITHOUT_A_LOADER) == 3, _FORMATS_WITHOUT_A_LOADER
        for extension in _FORMATS_WITHOUT_A_LOADER:
            assert f"*{extension}" not in pipeline_allow, extension
            assert extension not in inputs, extension
            assert extension not in outputs, extension

    def test_the_description_names_no_format_the_bundle_does_not_allow(self):
        # An operator picks the pipeline from its description, so a format named there but absent
        # from the filter reads as supported and is simply unselectable.
        pipeline = _load("pipeline.json")
        allowed = {
            extension.lstrip("*.").upper()
            for extension in pipeline["systemConfig"]["inputFileFilters"]["allow"]
        }
        named = {
            token.strip(" .,")
            for token in pipeline["description"].split("Supported inputs:")[-1].split(",")
        }
        assert named, pipeline["description"]
        assert named <= allowed, sorted(named - allowed)

    def test_every_template_targets_a_format_the_container_can_export(self):
        # The count is asserted in-band: a listing that matched nothing would otherwise leave this
        # test passing while checking no template at all.
        template_files = _template_files()
        assert len(template_files) == 4, template_files
        exportable = _output_formats()
        checked = []
        for name in template_files:
            template = _load("templates", name)
            assert template["configFormat"] == "json", name
            output_type = json.loads(template["configBody"])["outputType"]
            assert output_type in exportable, (name, output_type)
            checked.append(output_type)
        assert sorted(checked) == [".glb", ".gltf", ".obj", ".stl"], checked

    def test_the_workflow_declares_no_trigger_filter_to_keep_in_step(self):
        # The meshCad bundle's fileUpload trigger carries its own copy of the filter and can drift
        # from the pipeline's; this workflow declares no triggers, so there is no second copy. If one
        # is added it has to be checked against the pipeline filter the way meshCad's is.
        assert "triggers" not in _load("workflow.json")
