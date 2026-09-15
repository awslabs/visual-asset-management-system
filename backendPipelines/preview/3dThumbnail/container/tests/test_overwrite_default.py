#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the overwriteExistingPreviewFiles default.

Overwriting is the default: a run that supplies no input configuration — a triggered run, or an
execution that names no template — regenerates the thumbnail. Skipping is opt-in, which a manual run
selects by clearing the template's "Overwrite existing preview files" field.

The behavioural tests stub the S3 download to return nothing, which halts the run immediately after
the guard: the download being attempted at all is what distinguishes regenerating from the guard's
early COMPLETE.

Guards FIX-025 (S4-PIPELINES-036): an existing-preview skip that reports SUCCESS makes an
explicit regenerate silently do nothing.
"""

import json
import os
import re

from unittest.mock import MagicMock, patch

import pytest

from preview_pipeline import core
from preview_pipeline.utils.pipeline.objects import (
    PipelineStage,
    PipelineStatus,
    PipelineType,
    StageOutput,
)

# The shipped vamsSchema bundle for this pipeline, two levels up from the container tests.
TEMPLATE_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
    "vamsSchema", "templates", "preview-3d-thumbnail-default.json"))

# The {{tag}} name charset the backend renderer substitutes
# (common/workflows/templateRender._TAG_PATTERN).
TAG_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

CONFIG_KEY = "overwriteExistingPreviewFiles"
CONFIG_TAG_KEY = "OVERWRITE_EXISTING_PREVIEW_FILES"

# The tag types whose value renders as a bare JSON literal rather than inside the quotes the body
# already carries (common/workflows/templateRender.USER_TAG_TYPE_SHAPES).
_JSON_LITERAL_TYPES = ("integer", "number", "boolean", "string-list")


def _stage():
    output = StageOutput(
        bucketName="run-bucket",
        objectDir="pipelines/preview3dThumbnail/JOB/output/E1/files/")
    return PipelineStage(
        type=PipelineType.PREVIEW_3D_THUMBNAIL,
        inputFile={"bucketName": "asset-bucket", "objectKey": "xid/pump.e57",
                   "fileExtension": ".e57"},
        outputFiles=output.__dict__,
        outputMetadata=output.__dict__,
        temporaryFiles=output.__dict__,
    )


def _run_with(input_parameters):
    """Run the stage against a file that already has a preview. Returns (result, download mock)."""
    download = MagicMock(return_value=None)
    with patch.object(core, "_check_existing_preview",
                      MagicMock(return_value="xid/pump.e57.previewFile.gif")), \
            patch.object(core.s3, "get_object_size", MagicMock(return_value=1024)), \
            patch.object(core.s3, "download", download):
        result = core._run_preview_pipeline(_stage(), {}, input_parameters, False, "xid")
    return result, download


@pytest.mark.unit
class TestOverwriteDefault:
    def test_no_input_parameters_regenerates(self):
        result, download = _run_with({})
        download.assert_called_once()
        assert result.status == PipelineStatus.FAILED
        assert "already exists" not in (result.errorMessage or "")

    def test_parameter_absent_from_a_populated_configuration_regenerates(self):
        result, download = _run_with({"outputType": ".gif,.jpg,.png"})
        download.assert_called_once()
        assert result.status == PipelineStatus.FAILED

    def test_explicit_false_skips_and_reports_success(self):
        result, download = _run_with({CONFIG_KEY: False})
        download.assert_not_called()
        assert result.status == PipelineStatus.COMPLETE
        assert not result.errorMessage

    def test_explicit_true_regenerates(self):
        result, download = _run_with({CONFIG_KEY: True})
        download.assert_called_once()
        assert result.status == PipelineStatus.FAILED

    @pytest.mark.parametrize("value", ["false", "False", "0", "no", " FALSE "])
    def test_text_false_skips(self, value):
        result, download = _run_with({CONFIG_KEY: value})
        download.assert_not_called()
        assert result.status == PipelineStatus.COMPLETE

    @pytest.mark.parametrize("value", ["true", "True", "1", "yes"])
    def test_text_true_regenerates(self, value):
        _, download = _run_with({CONFIG_KEY: value})
        download.assert_called_once()

    def test_unrecognized_value_carries_the_default(self):
        _, download = _run_with({CONFIG_KEY: "maybe"})
        download.assert_called_once()


@pytest.mark.unit
class TestShippedTemplateTag:
    """The default is expressed as a template tag so an operator can see and change it.

    A tag DECLARED in the tagSchema but never referenced in the configBody is silently dropped — the
    field renders on the execute form and its value reaches no pipeline — so both halves are asserted
    together."""

    def _template(self):
        assert os.path.isfile(TEMPLATE_PATH), (
            f"shipped default template not found at {TEMPLATE_PATH}")
        with open(TEMPLATE_PATH, encoding="utf-8") as f:
            return json.load(f)

    def _tag_field(self, template):
        referenced = set(TAG_PATTERN.findall(template.get("configBody", "")))
        declared = {f.get("tagKey"): f for f in (template.get("tagSchema") or [])
                    if isinstance(f, dict)}
        assert CONFIG_TAG_KEY in referenced, (
            f"configBody does not reference {{{{{CONFIG_TAG_KEY}}}}}, so {CONFIG_KEY} is not "
            "operator-overridable")
        assert CONFIG_TAG_KEY in declared, (
            f"{CONFIG_TAG_KEY} is referenced by the configBody but declared by no tagSchema entry, "
            "so the execute form never asks for it")
        # Every declared tag must also be referenced: a declared-but-unreferenced tag renders a
        # field on the execute form whose value reaches no pipeline.
        assert not set(declared) - referenced, (
            f"declared but unreferenced: {sorted(set(declared) - referenced)}")
        return declared[CONFIG_TAG_KEY]

    def _render(self, template, values):
        """The configBody with each declared tag substituted the way the backend renderer would: a
        typed tag as a bare JSON literal, a string/enum tag as text inside the body's own quotes."""
        types_by_key = {f.get("tagKey"): f.get("type")
                        for f in (template.get("tagSchema") or []) if isinstance(f, dict)}

        def _substitute(match):
            key = match.group(1)
            value = values[key]
            if types_by_key.get(key) in _JSON_LITERAL_TYPES:
                return json.dumps(value)
            return str(value)

        return TAG_PATTERN.sub(_substitute, template["configBody"])

    def _defaults(self, template):
        return {f["tagKey"]: f.get("default")
                for f in (template.get("tagSchema") or []) if isinstance(f, dict)}

    def test_tag_is_declared_and_referenced(self):
        template = self._template()
        field = self._tag_field(template)
        assert field["type"] == "boolean"
        assert field["default"] is True
        assert not field.get("required")
        assert field.get("label")

    def test_placeholder_is_the_whole_value_so_it_renders_a_boolean(self):
        """A boolean tag renders a bare JSON literal, so quoting its placeholder would deliver the
        string "true" where the container expects a boolean (the json-body quoting rule the backend
        enforces on save)."""
        template = self._template()
        self._tag_field(template)
        for rendered_value in (True, False):
            values = dict(self._defaults(template), **{CONFIG_TAG_KEY: rendered_value})
            body = self._render(template, values)
            assert json.loads(body)[CONFIG_KEY] is rendered_value

    def test_declared_default_regenerates_in_the_container(self):
        """The tag default and the container default agree: the body the shipped template renders
        with no operator input drives the same regeneration a bare run performs."""
        template = self._template()
        self._tag_field(template)
        body = self._render(template, self._defaults(template))
        result, download = _run_with(json.loads(body))
        download.assert_called_once()
        assert result.status == PipelineStatus.FAILED
