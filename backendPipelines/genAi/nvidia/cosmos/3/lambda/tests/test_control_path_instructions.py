#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every cosmos3 template must describe COSMOS3_CONTROL_PATH as the complete S3 URI it is.

The template's `inputInstructions` is the text an operator reads on the execute form, so it is the
authoritative description of the setting for whoever supplies it. The value reaches `aws s3 cp` as
given: a full `s3://bucket/key` URI, restricted to the deployment's own asset buckets. An
asset-relative path is not resolved against anything -- it is copied as a local path, so the run fails
naming the setting after the GPU job has started, or is rejected at launch by openPipeline's shape
gate. Either way the operator followed the instruction and the run did not work.

The instructions are checked across ALL templates rather than only the transfer ones: the setting is
read from asset metadata regardless of which template a run uses, and the docs state that transfer is
honored on the nano and super pipelines, so a text2video template's instruction text is reached.
"""

import glob
import json
import os

import pytest

_SCHEMA_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "vamsSchema"))

_SETTING = "COSMOS3_CONTROL_PATH"


def _template_paths():
    """Every registered template in the four cosmos3 bundles. Only the top level of each
    `templates/` directory is registered -- the registration construct's schemaHash does not read a
    subdirectory -- so only the top level is read here."""
    return sorted(glob.glob(os.path.join(_SCHEMA_ROOT, "*", "templates", "*.json")))


def _instruction_line(path):
    with open(path, encoding="utf-8") as handle:
        instructions = json.load(handle).get("inputInstructions", "")
    for line in instructions.splitlines():
        if _SETTING in line:
            return line
    return None


@pytest.mark.unit
class TestTheControlPathIsDescribedAsAnS3Uri:
    def test_the_templates_this_module_reads_are_the_ones_that_exist(self):
        # The corpus control. A glob that matched nothing would satisfy every assertion below, and
        # this bundle family is spread over four directories, so the set is asserted in-band.
        assert sorted(os.path.basename(p) for p in _template_paths()) == [
            "cosmos3-nano-image2video.json",
            "cosmos3-nano-text2video.json",
            "cosmos3-nano-transfer.json",
            "cosmos3-nano-video2video.json",
            "cosmos3-super-image2video.json",
            "cosmos3-super-text2image.json",
            "cosmos3-super-text2video.json",
            "cosmos3-super-transfer.json",
            "cosmos3-super-video2video.json",
        ], sorted(os.path.basename(p) for p in _template_paths())

    def test_every_template_that_names_the_setting_calls_it_an_s3_uri(self):
        described = {}
        for path in _template_paths():
            line = _instruction_line(path)
            if line is None:
                continue
            described[os.path.relpath(path, _SCHEMA_ROOT)] = line
        # The positive control for the assertion below: the setting really is documented, so a
        # rewording that dropped the line entirely would show up here rather than pass silently.
        assert len(described) == 6, sorted(described)
        for name, line in described.items():
            assert "S3 URI" in line, (name, line)

    def test_no_template_describes_it_as_asset_relative(self):
        offenders = {}
        for path in _template_paths():
            line = _instruction_line(path)
            if line and "asset-relative" in line.lower():
                offenders[os.path.relpath(path, _SCHEMA_ROOT)] = line
        assert offenders == {}, offenders
