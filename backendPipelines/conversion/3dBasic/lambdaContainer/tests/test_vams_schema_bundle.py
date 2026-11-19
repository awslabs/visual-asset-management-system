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

# `.xaml` and `.3dxml` have no exporter at any dependency set and this container installs no reader
# for them, so neither can be an input or a conversion target.
_FORMATS_WITHOUT_A_LOADER = (".xaml", ".3dxml")

# `.xyz` loads as a PointCloud but cannot be written: trimesh's xyz exporter takes a PointCloud only
# and raises even for one. `.usd` is the generic USD extension: the outputs name their encoding
# (.usdc, .usda, .usdz), so it is read but never written.
_LOADS_BUT_DOES_NOT_EXPORT = (".xyz", ".usd")

# The seven templates the bundle ships: the five original ids (extended in place) and two grouped ones.
_TEMPLATE_IDS = {"convert-to-glb", "convert-to-gltf", "convert-to-obj", "convert-to-ply", "convert-to-stl",
                 "convert-to-usd", "convert-to-interchange"}


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


def _tag(template, tag_key):
    return next(entry for entry in template["tagSchema"] if entry["tagKey"] == tag_key)


def _units_axes_values():
    """The option value tuples of units_axes.py, read from its syntax tree, keyed by template tag."""
    with open(os.path.join(_LAMBDA_DIR, "units_axes.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            constants[node.targets[0].id] = node.value
    units = list(ast.literal_eval(constants["UNITS"]))
    return {
        "SOURCE_UNITS": ("auto", *units), "TARGET_UNITS": ("keep", *units),
        "SOURCE_UP_AXIS": ast.literal_eval(constants["SOURCE_UP_AXIS_VALUES"]),
        "TARGET_UP_AXIS": ast.literal_eval(constants["TARGET_UP_AXIS_VALUES"]),
        "MISSING_REFERENCES": ast.literal_eval(constants["MISSING_REFERENCES_VALUES"]),
    }


def _reference_handling_values():
    with open(os.path.join(_LAMBDA_DIR, "units_axes.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "REFERENCE_HANDLING_VALUES":
            return ast.literal_eval(node.value)
    raise AssertionError("units_axes.py declares no REFERENCE_HANDLING_VALUES")


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
        assert len(_LOADS_BUT_DOES_NOT_EXPORT) == 2, _LOADS_BUT_DOES_NOT_EXPORT
        assert outputs < inputs, (sorted(outputs), sorted(inputs))
        assert inputs - outputs == set(_LOADS_BUT_DOES_NOT_EXPORT), sorted(inputs - outputs)
        for extension in _LOADS_BUT_DOES_NOT_EXPORT:
            assert f"*{extension}" in pipeline_allow, extension

    def test_formats_without_a_loader_are_not_declared(self):
        pipeline_allow = _load("pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        inputs, outputs = _input_formats(), _output_formats()
        assert len(_FORMATS_WITHOUT_A_LOADER) == 2, _FORMATS_WITHOUT_A_LOADER
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

    def test_the_templates_are_the_seven_planned_ones(self):
        assert {name[:-5] for name in _template_files()} == _TEMPLATE_IDS
        for name in _template_files():
            assert _load("templates", name)["templateId"] == name[:-5], name

    def test_every_template_targets_formats_the_container_can_export_and_no_two_share_one(self):
        # The resolved output set of a template is its literal outputType, or the enumValues of the tag
        # its outputType placeholder names. Their union is exactly what the container writes, and no
        # format is offered by two templates.
        exportable = _output_formats()
        seen = {}
        for name in _template_files():
            template = _load("templates", name)
            assert template["configFormat"] == "json", name
            output_type = json.loads(template["configBody"])["outputType"]
            outputs = ([output_type] if not output_type.startswith("{{") else
                       _tag(template, output_type.strip("{}"))["enumValues"])
            for output in outputs:
                assert output in exportable, (name, output)
                assert output not in seen, (output, name, seen.get(output))
                seen[output] = name
        assert set(seen) == exportable, sorted(set(seen) ^ exportable)

    def test_the_tags_and_the_body_placeholders_agree_and_sit_inside_quotes(self):
        import re
        for name in _template_files():
            template = _load("templates", name)
            body = template["configBody"]
            placeholders = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", body))
            tags = {entry["tagKey"] for entry in template.get("tagSchema", [])}
            assert placeholders == tags, (name, placeholders ^ tags)
            for tag in tags:
                assert f'"{{{{{tag}}}}}"' in body, (name, tag)

    def test_every_tag_is_optional_with_a_default_that_is_one_of_its_values(self):
        for name in _template_files():
            for entry in _load("templates", name).get("tagSchema", []):
                assert entry["type"] == "enum", (name, entry["tagKey"])
                assert entry["required"] is False, (name, entry["tagKey"])
                assert entry["default"] in entry["enumValues"], (name, entry["tagKey"])
                assert entry.get("label") and entry.get("description"), (name, entry["tagKey"])

    def test_the_rendered_default_body_is_valid_json_with_a_supported_output_type(self):
        for name in _template_files():
            template = _load("templates", name)
            body = template["configBody"]
            for entry in template.get("tagSchema", []):
                body = body.replace("{{" + entry["tagKey"] + "}}", entry["default"])
            rendered = json.loads(body)
            assert rendered["outputType"] in _output_formats(), (name, rendered)
            # The defaults leave every conversion as it was.
            for key in ("sourceUnits", "sourceUpAxis"):
                assert rendered.get(key, "auto") == "auto", (name, key)
            for key in ("targetUnits", "targetUpAxis"):
                assert rendered.get(key, "keep") == "keep", (name, key)
            assert rendered["referenceHandling"] in ("flatten", "auto"), name
            assert rendered["missingReferences"] == "skip", name

    def test_the_option_values_are_the_ones_the_container_reads(self):
        values = _units_axes_values()
        for name in _template_files():
            for entry in _load("templates", name).get("tagSchema", []):
                if entry["tagKey"] in values:
                    assert entry["enumValues"] == list(values[entry["tagKey"]]), (name, entry["tagKey"])
                    assert entry["default"] == values[entry["tagKey"]][0], (name, entry["tagKey"])

    def test_every_template_offers_the_reference_options(self):
        allowed = _reference_handling_values()
        for name in _template_files():
            tags = {entry["tagKey"]: entry for entry in _load("templates", name)["tagSchema"]}
            handling = tags["REFERENCE_HANDLING"]["enumValues"]
            assert set(handling) <= set(allowed), (name, handling)
            if name == "convert-to-usd.json":
                # Only USD output can keep layers: package and convert-linked are offered there alone.
                assert handling == list(allowed) and tags["REFERENCE_HANDLING"]["default"] == "auto"
            else:
                assert handling == ["flatten", "selected-only"], (name, handling)
            assert tags["MISSING_REFERENCES"]["enumValues"] == ["skip", "fail"], name

    def test_gltf_outputs_offer_no_target_options(self):
        # The glTF convention (metres, Y up) is fixed, so the form cannot ask for another.
        for name in ("convert-to-glb.json", "convert-to-gltf.json"):
            tags = {entry["tagKey"] for entry in _load("templates", name)["tagSchema"]}
            assert tags == {"SOURCE_UNITS", "SOURCE_UP_AXIS", "REFERENCE_HANDLING", "MISSING_REFERENCES"}, (name, tags)

    def test_no_template_carries_input_filter_overrides(self):
        # An override replaces the pipeline's whole filter list, so it would have to restate it.
        for name in _template_files():
            assert "overrides" not in _load("templates", name), name

    def test_the_workflow_declares_no_trigger_filter_to_keep_in_step(self):
        # The meshCad bundle's fileUpload trigger carries its own copy of the filter and can drift
        # from the pipeline's; this workflow declares no triggers, so there is no second copy. If one
        # is added it has to be checked against the pipeline filter the way meshCad's is.
        assert "triggers" not in _load("workflow.json")
