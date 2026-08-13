#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the splatToolbox container's S3_OUTPUT / UUID split.

Upstream `src/main.py` writes every output to ``{S3_OUTPUT}/{UUID}/...`` and rejects an empty UUID.
The workflow hands the container a per-execution output-files prefix
(``pipelines/{p}/{j}/output/{executionId}/files/``) and the write-back step maps whatever relative
path the container writes below it onto the output asset, so outputs must hang directly off that
prefix — an extra container-chosen level would show up as a stray folder inside every asset. The pair
is therefore split so it recomposes to exactly that prefix, and a workflow-level output path prefix
is the only thing that nests outputs.
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

_PREFIX = "pipelines/splatToolbox/JOB/output/3f2c9a10/files/"


@pytest.fixture(scope="module")
def main_module():
    """The container entry module, loaded by file (its name is ``__main__.py``).

    Its ``vams_utils`` / ``boto3`` imports are stubbed: this test covers pure path arithmetic and
    must not need the container's AWS dependencies installed.
    """
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stubbed = {}
    for name in ("boto3", "vams_utils", "vams_utils.manifest_io"):
        if name not in sys.modules:
            stubbed[name] = MagicMock()
    sys.modules.update(stubbed)
    try:
        spec = importlib.util.spec_from_file_location(
            "splat_container_main", os.path.join(container_dir, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name in stubbed:
            sys.modules.pop(name, None)
    return module


@pytest.mark.unit
class TestResolveOutputEnv:
    def test_the_pair_recomposes_to_the_output_files_prefix(self, main_module):
        """The property that matters: joining the two back the way main.py does must land exactly on
        the prefix the workflow gave, with no extra level."""
        s3_output, job_uuid = main_module.resolve_output_env("run-bucket", _PREFIX, "JOB")
        assert f"{s3_output}/{job_uuid}/" == f"s3://run-bucket/{_PREFIX}"

    def test_uuid_takes_the_last_prefix_segment(self, main_module):
        s3_output, job_uuid = main_module.resolve_output_env("run-bucket", _PREFIX, "JOB")
        assert job_uuid == "files"
        assert s3_output == "s3://run-bucket/pipelines/splatToolbox/JOB/output/3f2c9a10"

    def test_a_missing_trailing_slash_makes_no_difference(self, main_module):
        assert (main_module.resolve_output_env("run-bucket", _PREFIX.rstrip("/"), "JOB")
                == main_module.resolve_output_env("run-bucket", _PREFIX, "JOB"))

    def test_a_single_segment_prefix_leaves_the_bucket_root(self, main_module):
        s3_output, job_uuid = main_module.resolve_output_env("run-bucket", "files/", "JOB")
        assert (s3_output, job_uuid) == ("s3://run-bucket/", "files")
        assert f"{s3_output}{job_uuid}/" == "s3://run-bucket/files/"

    def test_an_empty_prefix_falls_back_to_the_job_name_so_uuid_is_never_empty(self, main_module):
        """main.py raises error 700 on an empty UUID, so the fallback is load-bearing for the
        direct/local invocation path where no output prefix is supplied."""
        for object_dir in ("", "/", None):
            _s3_output, job_uuid = main_module.resolve_output_env("run-bucket", object_dir, "JOB")
            assert job_uuid == "JOB"

    def test_no_execution_id_folder_is_appended_below_the_prefix(self, main_module):
        """The write-back maps paths relative to the prefix, so anything the container adds becomes a
        folder inside the user's asset."""
        s3_output, job_uuid = main_module.resolve_output_env("run-bucket", _PREFIX, "JOB")
        written = f"{s3_output}/{job_uuid}/model.ply"
        assert written == f"s3://run-bucket/{_PREFIX}model.ply"
        assert "3f2c9a10/3f2c9a10" not in written
