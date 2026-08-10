#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the isaacLab container's output prefix resolution.

The workflow hands the container a per-execution output-files prefix
(``pipelines/{p}/{j}/output/{executionId}/files/``) and the write-back step maps whatever relative
path the container writes below it onto the output asset, so outputs must hang directly off that
prefix rather than under a further execution-id folder."""

import os
import importlib.util

import pytest

_OUTPUT_PREFIX = "s3://run-bucket/pipelines/isaacLab/JOB/output/3f2c9a10/files/"


@pytest.fixture(scope="module")
def main_module():
    """The container entry module, loaded by file (its name is ``__main__.py``)."""
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "isaaclab_container_main", os.path.join(container_dir, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestResolveOutputBasePath:
    def test_execution_prefix_is_used_verbatim(self, main_module):
        assert main_module.resolve_output_base_path(_OUTPUT_PREFIX) == _OUTPUT_PREFIX

    def test_missing_trailing_slash_is_added(self, main_module):
        assert main_module.resolve_output_base_path(_OUTPUT_PREFIX.rstrip("/")) == _OUTPUT_PREFIX

    def test_empty_path_stays_empty(self, main_module):
        assert main_module.resolve_output_base_path("") == ""

    def test_execution_id_is_not_appended(self, main_module):
        base = main_module.resolve_output_base_path(_OUTPUT_PREFIX)
        execution_id = main_module.get_job_uuid_from_output_path(_OUTPUT_PREFIX)
        assert execution_id == "3f2c9a10"
        assert not base.endswith(f"{execution_id}/{execution_id}/")
        assert f"{base}checkpoints/model_1500.pt".endswith(
            "output/3f2c9a10/files/checkpoints/model_1500.pt")
