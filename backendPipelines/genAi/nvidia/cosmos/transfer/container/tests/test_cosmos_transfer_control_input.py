#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Cosmos Transfer reads its control input relative to the asset root from the manifest.

``controlPath`` names a file of the run's asset relative to its root. The root is the manifest entry's
``assetRootS3Key`` in the input file's bucket, which the definition carries as ``inputAssetRootS3Key``:
for an asset in a bucket with a base prefix the root is not ``{assetId}/``, and for an asset created on
an existing bucket folder it does not contain the asset ID. A definition without the root takes the
input key up to its asset ID segment. A value that is not a file path within the asset root fails the
run before any model or file is fetched."""

import os
from unittest.mock import patch

import pytest

INPUT = "s3://asset-bkt/vams/xidA/clips/source.mp4"
EXISTING_FOLDER_INPUT = "s3://asset-bkt/projects/site-a/clips/source.mp4"
BASE_PREFIX_ROOT = {"inputAssetRootS3Key": "vams/xidA/"}
EXISTING_FOLDER_ROOT = {"inputAssetRootS3Key": "projects/site-a/"}


def _definition(**fields):
    definition = {"inputS3AssetFilePath": INPUT,
                  "outputS3AssetFilesPath": "s3://asset-bkt/pipelines/p1/J/output/E1/files/",
                  "assetId": "xidA", "controlType": "edge"}
    definition.update(fields)
    return definition


class TestTheControlInputIsUnderTheAssetRoot:
    def test_an_asset_in_a_bucket_with_a_base_prefix(self, transfer_entrypoint):
        uri = transfer_entrypoint.control_input_s3_uri(
            "controls/edge.mp4", _definition(**BASE_PREFIX_ROOT), INPUT, "xidA")
        # Joined to {assetId}/ the control input was read from s3://asset-bkt/xidA/..., outside the prefix.
        assert uri == "s3://asset-bkt/vams/xidA/controls/edge.mp4"

    def test_an_asset_on_an_existing_bucket_folder(self, transfer_entrypoint):
        definition = _definition(inputS3AssetFilePath=EXISTING_FOLDER_INPUT, **EXISTING_FOLDER_ROOT)
        assert transfer_entrypoint.control_input_s3_uri(
            "controls/edge.mp4", definition, EXISTING_FOLDER_INPUT, "xidA") == (
            "s3://asset-bkt/projects/site-a/controls/edge.mp4")

    def test_a_root_without_a_trailing_slash(self, transfer_entrypoint):
        definition = _definition(inputS3AssetFilePath=EXISTING_FOLDER_INPUT,
                                 inputAssetRootS3Key="projects/site-a")
        assert transfer_entrypoint.control_input_s3_uri(
            "controls/edge.mp4", definition, EXISTING_FOLDER_INPUT, "xidA") == (
            "s3://asset-bkt/projects/site-a/controls/edge.mp4")

    def test_a_leading_slash_names_the_asset_root(self, transfer_entrypoint):
        assert transfer_entrypoint.control_input_s3_uri(
            "/controls/edge.mp4", _definition(**BASE_PREFIX_ROOT), INPUT, "xidA") == (
            "s3://asset-bkt/vams/xidA/controls/edge.mp4")

    def test_a_leading_slash_naming_another_asset_stays_in_this_asset(self, transfer_entrypoint):
        definition = _definition(inputS3AssetFilePath=EXISTING_FOLDER_INPUT, **EXISTING_FOLDER_ROOT)
        assert transfer_entrypoint.control_input_s3_uri(
            "/xidB/edge.mp4", definition, EXISTING_FOLDER_INPUT, "xidA") == (
            "s3://asset-bkt/projects/site-a/xidB/edge.mp4")

    def test_without_the_manifest_root_the_key_up_to_the_asset_id(self, transfer_entrypoint):
        assert transfer_entrypoint.control_input_s3_uri("controls/edge.mp4", _definition(), INPUT, "xidA") == (
            "s3://asset-bkt/vams/xidA/controls/edge.mp4")

    def test_without_the_manifest_root_an_asset_at_the_bucket_root(self, transfer_entrypoint):
        source = "s3://asset-bkt/xidA/clips/source.mp4"
        assert transfer_entrypoint.control_input_s3_uri(
            "controls/edge.mp4", _definition(inputS3AssetFilePath=source), source, "xidA") == (
            "s3://asset-bkt/xidA/controls/edge.mp4")

    def test_without_a_root_or_an_asset_id_segment_the_run_fails(self, transfer_entrypoint):
        with pytest.raises(ValueError, match="cannot be located"):
            transfer_entrypoint.control_input_s3_uri(
                "controls/edge.mp4", _definition(inputS3AssetFilePath=EXISTING_FOLDER_INPUT),
                EXISTING_FOLDER_INPUT, "xidA")


@pytest.mark.parametrize("root", [BASE_PREFIX_ROOT, EXISTING_FOLDER_ROOT], ids=["base-prefix", "existing-folder"])
@pytest.mark.parametrize("value", [
    "../xidB/controls/edge.mp4",
    "../../xidB/edge.mp4",
    "controls/../../xidB/edge.mp4",
    "s3://asset-bkt/xidB/edge.mp4",
    "./controls/edge.mp4",
    "controls//edge.mp4",
    "controls/edge.mp4/",
    "/",
    "file:///etc/hosts",
    "controls\\edge.mp4",
    "controls/edge\x00.mp4",
    "controls/\nedge.mp4",
    "c/" + "a" * 1100 + ".mp4",
    7,
])
def test_a_path_outside_the_asset_is_refused(transfer_entrypoint, root, value):
    with pytest.raises(ValueError, match="must name a file in the asset"):
        transfer_entrypoint.control_input_s3_uri(value, _definition(**root), INPUT, "xidA")


@pytest.mark.parametrize("value", ["", "   ", None])
def test_no_control_path_names_none(transfer_entrypoint, value):
    assert transfer_entrypoint.normalize_control_path(value) == ""


class TestTheRunReadsTheControlInputFromTheAssetRoot:
    def _run(self, entrypoint, definition, tmp_path, expected, match):
        # Inference is stubbed to stop the run: a run that got that far has already fetched its inputs.
        with patch.object(entrypoint, "load_pipeline_definition", return_value=definition), \
                patch.object(entrypoint, "ensure_models_cached") as cached, \
                patch.object(entrypoint, "download_from_s3") as download, \
                patch.object(entrypoint, "run_inference", side_effect=RuntimeError("inference started")), \
                patch.object(entrypoint, "OUTPUT_DIR", tmp_path / "output"), \
                patch.dict(os.environ, {"S3_MODEL_BUCKET": "model-bkt"}):
            with pytest.raises(expected, match=match):
                entrypoint.main()
        return cached, download

    def test_a_refused_path_fetches_nothing(self, transfer_entrypoint, tmp_path):
        cached, download = self._run(
            transfer_entrypoint, _definition(controlPath="../xidB/edge.mp4", **BASE_PREFIX_ROOT),
            tmp_path, ValueError, "must name a file in the asset")
        cached.assert_not_called()
        download.assert_not_called()

    def test_an_asset_on_an_existing_bucket_folder(self, transfer_entrypoint, tmp_path):
        definition = _definition(inputS3AssetFilePath=EXISTING_FOLDER_INPUT, controlPath="/controls/edge.mp4",
                                 **EXISTING_FOLDER_ROOT)
        _, download = self._run(transfer_entrypoint, definition, tmp_path, RuntimeError, "inference started")
        assert [c.args[0] for c in download.call_args_list] == [
            EXISTING_FOLDER_INPUT, "s3://asset-bkt/projects/site-a/controls/edge.mp4"]

    def test_an_asset_in_a_bucket_with_a_base_prefix(self, transfer_entrypoint, tmp_path):
        definition = _definition(controlPath="controls/edge.mp4", **BASE_PREFIX_ROOT)
        _, download = self._run(transfer_entrypoint, definition, tmp_path, RuntimeError, "inference started")
        assert [c.args[0] for c in download.call_args_list] == [
            INPUT, "s3://asset-bkt/vams/xidA/controls/edge.mp4"]
