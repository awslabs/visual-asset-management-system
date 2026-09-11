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

# The formats the bundle offers as inputs. PLY is deliberately absent: it records no CRS, and the
# built-in template sets onMismatch to error with enforceSourceCrs at its default of true, so every
# PLY input is refused at validation. PLY stays an output format, and the Lambda extension gate keeps
# `.ply` so a custom template that overrides inputFileFilters can still process one on purpose.
_ACCEPTED_INPUT_PATTERNS = {"*.e57", "*.las", "*.laz"}


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

    def test_the_pipeline_accepts_only_the_formats_that_record_a_crs(self):
        allow = _load("pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
        assert set(allow) == _ACCEPTED_INPUT_PATTERNS, (
            f"the pipeline's accepted-input list is {allow}. A format that records no CRS of its own "
            f"is refused by the built-in template rather than transformed"
        )

    def test_the_upload_trigger_accepts_the_same_formats_as_the_pipeline(self):
        # Two separately stored records: the trigger's filters are PUT to the trigger route, so a
        # bundle that narrows one and not the other keeps advertising the wider set on upload.
        trigger = _load("workflow.json")["triggers"][0]
        assert set(trigger["inputFileFilters"]["allow"]) == _ACCEPTED_INPUT_PATTERNS

    def test_the_description_names_no_format_the_pipeline_does_not_accept(self):
        # The description is what the execute wizard and the pipeline listing show, so a format named
        # there that the filter refuses reads as an accepted input the operator cannot actually use.
        description = _load("pipeline.json")["description"]
        named = {f"*.{token.lower()}" for token in ("E57", "LAS", "LAZ", "PLY")
                 if token in description}
        assert named <= _ACCEPTED_INPUT_PATTERNS, (
            f"the description names {sorted(named - _ACCEPTED_INPUT_PATTERNS)}, which the "
            f"accepted-input list refuses: {description}"
        )
        assert named, "the description names no input format at all"

    def test_the_built_in_template_still_enforces_the_source_crs(self):
        # The counterpart of the narrowing: enforcement was deliberately NOT relaxed, so a file with
        # no CRS keeps failing. Flipping onMismatch to warn here would silently reintroduce the
        # wrong-coordinates outcome the narrowing avoids.
        for template in _templates():
            config = json.loads(template["configBody"])
            assert config.get("onMismatch", "warn") == "error", (
                f"{template['templateId']} no longer reports a failed CRS validation as an error"
            )

    def test_no_shipped_template_contradicts_its_own_output_compression(self):
        """compressLaz and outputFormats govern one property, so a shipped body must not disagree.

        LAZ is the compressed LAS format. A template body carrying `compressLaz: false` alongside a
        `laz` output format is refused at run time by constructPipeline, so shipping one would deploy
        a template that fails every execution — and the body reaches a deployment only through a
        redeploy / vamsSchema re-import, where nothing else would catch it.
        """
        templates = _templates()
        assert templates, "no templates were loaded, so this check proved nothing"
        checked = 0
        for template in templates:
            config = json.loads(template["configBody"])
            formats = config.get("outputFormats") or []
            if isinstance(formats, str):
                formats = [entry.strip() for entry in formats.split(",") if entry.strip()]
            formats = [str(entry).strip().lower() for entry in formats]
            compress = config.get("compressLaz", True)
            if isinstance(compress, str):
                compress = compress.strip().lower() not in {"false", "0", "no", "off"}
            checked += 1
            assert compress or "laz" not in formats, (
                f"{template['templateId']} sets compressLaz false with a laz output format; "
                f"every execution of it would be refused"
            )
        assert checked == len(templates), (
            f"only {checked} of {len(templates)} templates were examined")
