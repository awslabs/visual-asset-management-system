#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the relative-subdirectory derivation that keeps a preview beside its input file.

An original asset file is keyed under its asset location, so the segments between the asset id and
the filename are the subdirectory. A file an earlier workflow step produced or rewrote is keyed by
its asset-relative path under the run's output FILES folder — the folder this stage writes to — so
the path after that prefix is what states where the file sits on the asset."""

import pytest

from preview_pipeline import core
from preview_pipeline.utils.pipeline.objects import StageInput, StageOutput

ASSET_ID = "xd130a6d6"
OUTPUT_FILES_DIR = "pipelines/3dBasicConversion/a1b2c-job/output/exec99/files/"


def _input(key, bucket="asset-bucket"):
    return StageInput(bucketName=bucket, objectKey=key, fileExtension=".glb")


def _output(object_dir=OUTPUT_FILES_DIR, bucket="run-bucket"):
    return StageOutput(bucketName=bucket, objectDir=object_dir)


@pytest.mark.unit
class TestRelativeSubdirAssetInput:
    def test_subdirectory_input(self):
        assert core._relative_subdir(
            _input(f"{ASSET_ID}/test/pump.e57"), _output(), ASSET_ID) == "test"

    def test_nested_subdirectory_input(self):
        assert core._relative_subdir(
            _input(f"{ASSET_ID}/a/b/model.glb"), _output(), ASSET_ID) == "a/b"

    def test_asset_root_input(self):
        assert core._relative_subdir(
            _input(f"{ASSET_ID}/pump.e57"), _output(), ASSET_ID) == ""

    def test_custom_asset_base_prefix_before_the_asset_id(self):
        assert core._relative_subdir(
            _input(f"base/{ASSET_ID}/test/pump.e57"), _output(), ASSET_ID) == "test"

    def test_asset_id_absent_from_the_key(self):
        assert core._relative_subdir(
            _input("someOtherAsset/test/pump.e57"), _output(), ASSET_ID) == ""

    def test_no_asset_id(self):
        assert core._relative_subdir(_input(f"{ASSET_ID}/test/pump.e57"), _output(), "") == ""


@pytest.mark.unit
class TestRelativeSubdirShadowedInput:
    def test_prior_step_output_nests_beside_its_file(self):
        stage_input = _input(f"{OUTPUT_FILES_DIR}test/pump.glb", bucket="run-bucket")
        assert core._relative_subdir(stage_input, _output(), ASSET_ID) == "test"

    def test_prior_step_output_at_the_asset_root(self):
        stage_input = _input(f"{OUTPUT_FILES_DIR}pump.glb", bucket="run-bucket")
        assert core._relative_subdir(stage_input, _output(), ASSET_ID) == ""

    def test_prior_step_output_nested_several_levels(self):
        stage_input = _input(f"{OUTPUT_FILES_DIR}a/b/model.glb", bucket="run-bucket")
        assert core._relative_subdir(stage_input, _output(), ASSET_ID) == "a/b"

    def test_prior_step_output_with_no_asset_id_threaded(self):
        stage_input = _input(f"{OUTPUT_FILES_DIR}test/pump.glb", bucket="run-bucket")
        assert core._relative_subdir(stage_input, _output(), "") == "test"

    def test_matching_prefix_in_a_different_bucket_is_not_treated_as_output(self):
        stage_input = _input(f"{OUTPUT_FILES_DIR}test/pump.glb", bucket="asset-bucket")
        assert core._relative_subdir(stage_input, _output(), ASSET_ID) == ""

    def test_local_test_paths_carry_no_bucket_and_fall_back_to_the_asset_id(self):
        stage_input = _input(f"{ASSET_ID}/test/pump.glb", bucket="")
        assert core._relative_subdir(
            stage_input, _output(object_dir="/data/output/", bucket=""), ASSET_ID) == "test"
