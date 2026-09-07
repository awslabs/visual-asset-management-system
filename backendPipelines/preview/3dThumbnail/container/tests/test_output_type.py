#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the outputType input parameter.

outputType selects the thumbnail format a run writes. `auto` keeps the frame-count behaviour — an
animated GIF when the renderer produced several frames, a still JPG otherwise — and each named format
applies whatever the frame count. The parameter shipped for several releases as a comma-separated list
of every format that no code read, so a value naming no format carries the auto default rather than
failing the run: a configuration cloned from one of those templates keeps working.

The shipped template declares the parameter as an enum tag, and a tag DECLARED in the tagSchema but
never referenced in the configBody is silently dropped, so both halves are asserted together along
with the container's own reading of the value.

Guards S4-PIPELINES-056."""

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

CONFIG_KEY = "outputType"
CONFIG_TAG_KEY = "OUTPUT_TYPE"

OUTPUT_DIR = "pipelines/preview3dThumbnail/JOB/output/E1/files/"
INPUT_KEY = "xid/test/pump.e57"


def _template():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _output_type_field(template):
    field = next((f for f in (template.get("tagSchema") or [])
                  if f.get("tagKey") == CONFIG_TAG_KEY), None)
    assert field is not None, f"{CONFIG_TAG_KEY} is not declared in the shipped template's tagSchema"
    return field


def _stage():
    output = StageOutput(bucketName="run-bucket", objectDir=OUTPUT_DIR)
    return PipelineStage(
        type=PipelineType.PREVIEW_3D_THUMBNAIL,
        inputFile={"bucketName": "asset-bucket", "objectKey": INPUT_KEY,
                   "fileExtension": ".e57"},
        outputFiles=output.__dict__,
        outputMetadata=output.__dict__,
        temporaryFiles=output.__dict__,
    )


def _run(tmp_path, input_parameters, frame_count=8):
    """Run the stage to completion against stubbed I/O and rendering.

    Returns (result, uploaded S3 key, the name of the image_utils writer that was used)."""
    directories = {}

    def _create_dir(parts):
        path = tmp_path / "-".join(parts)
        path.mkdir(parents=True, exist_ok=True)
        directories["-".join(parts)] = str(path)
        return str(path)

    def _download(bucket, key, path):
        with open(path, "wb") as handle:
            handle.write(b"model")
        return path

    written = []

    def _writer(name):
        def _write(*args, **kwargs):
            # args: (frames|frame, output_path)
            path = args[1]
            with open(path, "wb") as handle:
                handle.write(b"preview")
            written.append(name)
            return path
        return _write

    upload = MagicMock(side_effect=lambda bucket, key, path: key)
    frames = list(range(frame_count))

    with patch.object(core, "_create_dir", _create_dir), \
            patch.object(core, "_check_existing_preview", MagicMock(return_value=None)), \
            patch.object(core, "_load_file", MagicMock(return_value=MagicMock())), \
            patch.object(core, "_normalize_up_axis", lambda data, ext: data), \
            patch.object(core.renderer, "generate_rotating_frames",
                         MagicMock(return_value=frames)), \
            patch.object(core.image_utils, "ensure_under_size_limit",
                         MagicMock(side_effect=_writer("ensure_under_size_limit"))), \
            patch.object(core.image_utils, "save_jpeg",
                         MagicMock(side_effect=_writer("save_jpeg"))), \
            patch.object(core.image_utils, "save_png",
                         MagicMock(side_effect=_writer("save_png"))), \
            patch.object(core.s3, "get_object_size", MagicMock(return_value=1024)), \
            patch.object(core.s3, "download", _download), \
            patch.object(core.s3, "upload", upload):
        result = core._run_preview_pipeline(_stage(), {}, input_parameters, False, "xid")

    assert result.status == PipelineStatus.COMPLETE, result.errorMessage
    assert upload.call_count == 1
    return result, upload.call_args.args[1], written[-1]


@pytest.mark.unit
class TestResolveOutputType:
    @pytest.mark.parametrize("value,expected", [
        ("auto", "auto"),
        ("gif", "gif"),
        ("jpg", "jpg"),
        ("png", "png"),
        (".gif", "gif"),
        (".PNG", "png"),
        (" JPG ", "jpg"),
        ("jpeg", "jpg"),
        (".jpeg", "jpg"),
    ])
    def test_named_formats(self, value, expected):
        assert core._resolve_output_type({CONFIG_KEY: value}) == expected

    def test_absent_parameter_is_auto(self):
        assert core._resolve_output_type({}) == "auto"

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_blank_value_is_auto(self, value):
        assert core._resolve_output_type({CONFIG_KEY: value}) == "auto"

    def test_the_legacy_comma_separated_list_is_auto(self):
        """The value the template shipped with while no code read it. It names no single format, so
        it keeps the automatic behaviour a cloned template already had rather than failing."""
        assert core._resolve_output_type({CONFIG_KEY: ".gif,.jpg,.png"}) == "auto"

    @pytest.mark.parametrize("value", ["bmp", "webp", "true", 7])
    def test_an_unsupported_value_is_auto(self, value):
        assert core._resolve_output_type({CONFIG_KEY: value}) == "auto"

    def test_a_non_dictionary_configuration_is_auto(self):
        assert core._resolve_output_type(None) == "auto"


@pytest.mark.unit
class TestOutputTypeDrivesTheWrittenFile:
    def test_auto_animates_several_frames(self, tmp_path):
        _, key, writer = _run(tmp_path, {}, frame_count=8)
        assert key == f"{OUTPUT_DIR}test/pump.e57.previewFile.gif"
        assert writer == "ensure_under_size_limit"

    def test_auto_writes_a_jpg_for_a_single_frame(self, tmp_path):
        _, key, writer = _run(tmp_path, {}, frame_count=1)
        assert key == f"{OUTPUT_DIR}test/pump.e57.previewFile.jpg"
        assert writer == "save_jpeg"

    def test_gif_animates_even_a_single_frame(self, tmp_path):
        """A named format applies whatever the frame count: with the parameter unread, a static
        render wrote a .jpg however the operator set the field."""
        _, key, writer = _run(tmp_path, {CONFIG_KEY: "gif"}, frame_count=1)
        assert key == f"{OUTPUT_DIR}test/pump.e57.previewFile.gif"
        assert writer == "ensure_under_size_limit"

    def test_png_writes_a_still_png_from_several_frames(self, tmp_path):
        """With the parameter unread, several frames always wrote an animated .gif."""
        _, key, writer = _run(tmp_path, {CONFIG_KEY: "png"}, frame_count=8)
        assert key == f"{OUTPUT_DIR}test/pump.e57.previewFile.png"
        assert writer == "save_png"

    def test_jpg_writes_a_still_jpg_from_several_frames(self, tmp_path):
        _, key, writer = _run(tmp_path, {CONFIG_KEY: ".jpg"}, frame_count=8)
        assert key == f"{OUTPUT_DIR}test/pump.e57.previewFile.jpg"
        assert writer == "save_jpeg"

    def test_the_legacy_list_value_still_animates(self, tmp_path):
        _, key, writer = _run(tmp_path, {CONFIG_KEY: ".gif,.jpg,.png"}, frame_count=8)
        assert key == f"{OUTPUT_DIR}test/pump.e57.previewFile.gif"
        assert writer == "ensure_under_size_limit"


@pytest.mark.unit
class TestShippedTemplateEnum:
    def test_the_tag_is_declared_as_an_enum_and_referenced_by_the_body(self):
        template = _template()
        field = _output_type_field(template)
        assert field["type"] == "enum"
        assert field["enumValues"]
        assert field.get("label")
        assert CONFIG_TAG_KEY in set(TAG_PATTERN.findall(template.get("configBody", ""))), (
            f"configBody does not reference {{{{{CONFIG_TAG_KEY}}}}}, so the operator's value is "
            "silently dropped")

    def test_the_placeholder_sits_inside_the_string_it_fills(self):
        """An enum renders text, so its placeholder must be quoted in a json body — the save-time
        gate rejects the unquoted form, which would fail the CDK registration."""
        template = _template()
        body = template["configBody"]
        assert f'"{{{{{CONFIG_TAG_KEY}}}}}"' in body, body

    def test_the_declared_default_is_offered_by_the_enum(self):
        field = _output_type_field(_template())
        assert field["default"] in field["enumValues"]

    def test_every_value_the_form_offers_reaches_a_format(self):
        """The execute form's options and the container's accepted set are the same list — an option
        the container does not recognize would silently write the automatic format instead."""
        field = _output_type_field(_template())
        for value in field["enumValues"]:
            assert core._resolve_output_type({CONFIG_KEY: value}) == value

    def test_the_declared_default_animates_in_the_container(self, tmp_path):
        """The tag default and the container default agree: the body the shipped template renders
        with no operator input drives the same automatic format a bare run writes."""
        field = _output_type_field(_template())
        _, key, writer = _run(tmp_path, {CONFIG_KEY: field["default"]}, frame_count=8)
        assert key.endswith(".previewFile.gif")
        assert writer == "ensure_under_size_limit"
