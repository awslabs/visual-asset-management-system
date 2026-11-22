#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Cosmos Transfer reads its control input only from within the run's asset.

``controlPath`` names a file of the run's asset relative to its root, ``{assetId}/`` in the input
file's bucket. A value that is not a file path within that root fails the run before any model or file
is fetched."""

import os
from unittest.mock import patch

import pytest

INPUT = "s3://asset-bkt/xidA/clips/source.mp4"


def _definition(**fields):
    definition = {"inputS3AssetFilePath": INPUT,
                  "outputS3AssetFilesPath": "s3://asset-bkt/pipelines/p1/J/output/E1/files/",
                  "assetId": "xidA", "controlType": "edge"}
    definition.update(fields)
    return definition


class TestTheControlInputIsUnderTheAssetRoot:
    def test_a_path_in_the_asset(self, transfer_entrypoint):
        assert transfer_entrypoint.control_input_s3_uri("controls/edge.mp4", INPUT, "xidA") == (
            "s3://asset-bkt/xidA/controls/edge.mp4")

    def test_a_leading_slash_names_the_asset_root(self, transfer_entrypoint):
        assert transfer_entrypoint.control_input_s3_uri("/controls/edge.mp4", INPUT, "xidA") == (
            "s3://asset-bkt/xidA/controls/edge.mp4")

    def test_without_an_asset_id_the_run_fails(self, transfer_entrypoint):
        with pytest.raises(ValueError, match="cannot be located"):
            transfer_entrypoint.control_input_s3_uri("controls/edge.mp4", INPUT, "")


@pytest.mark.parametrize("value", [
    "../xidB/controls/edge.mp4",
    "controls/../../xidB/edge.mp4",
    "./controls/edge.mp4",
    "controls//edge.mp4",
    "controls/edge.mp4/",
    "/",
    "s3://other-bkt/controls/edge.mp4",
    "file:///etc/hosts",
    "controls\\edge.mp4",
    "controls/edge\x00.mp4",
    "controls/\nedge.mp4",
    "c/" + "a" * 1100 + ".mp4",
    7,
])
def test_a_path_outside_the_asset_is_refused(transfer_entrypoint, value):
    with pytest.raises(ValueError, match="must name a file in the asset"):
        transfer_entrypoint.control_input_s3_uri(value, INPUT, "xidA")


@pytest.mark.parametrize("value", ["", "   ", None])
def test_no_control_path_names_none(transfer_entrypoint, value):
    assert transfer_entrypoint.normalize_control_path(value) == ""


class TestTheRunChecksThePathBeforeAnyFetch:
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
        cached, download = self._run(transfer_entrypoint, _definition(controlPath="../xidB/edge.mp4"),
                                     tmp_path, ValueError, "must name a file in the asset")
        cached.assert_not_called()
        download.assert_not_called()

    def test_a_path_in_the_asset_is_read_from_the_asset_root(self, transfer_entrypoint, tmp_path):
        _, download = self._run(transfer_entrypoint, _definition(controlPath="/controls/edge.mp4"),
                                tmp_path, RuntimeError, "inference started")
        assert [c.args[0] for c in download.call_args_list] == [
            INPUT, "s3://asset-bkt/xidA/controls/edge.mp4"]
