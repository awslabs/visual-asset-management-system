#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Cosmos Transfer reads its control input relative to the asset root from the manifest.

``controlPath`` names a file of the run's asset relative to its root. The root is the manifest entry's
``assetRootS3Key`` in ``assetRootBucket``, which the definition carries as ``inputAssetRootS3Key`` and
``inputAssetRootBucket``: for an asset in a bucket with a base prefix the root is not ``{assetId}/``, for
an asset created on an existing bucket folder it does not contain the asset ID, and for a chained run the
input is read from the run bucket while the asset root is in the asset bucket. A definition without the
root takes the input key up to its asset ID segment. A value that is not a file path within the asset
root fails the run before any model or file is fetched."""

import os
from unittest.mock import patch

import pytest

INPUT = "s3://asset-bkt/vams/xidA/clips/source.mp4"


def _definition(**fields):
    definition = {"inputS3AssetFilePath": INPUT, "outputS3AssetFilesPath": "s3://run-bkt/pipelines/p1/J/output/E1/files/",
                  "assetId": "xidA", "controlType": "edge"}
    definition.update(fields)
    return definition


MANIFEST_ROOT = {"inputAssetRootS3Key": "vams/xidA/", "inputAssetRootBucket": "asset-bkt"}


class TestTheControlInputIsUnderTheAssetRoot:
    def test_an_asset_in_a_bucket_with_a_base_prefix(self, transfer_entrypoint):
        uri = transfer_entrypoint.control_input_s3_uri(
            "controls/edge.mp4", _definition(**MANIFEST_ROOT), INPUT, "xidA")
        # The asset-ID join read s3://asset-bkt/xidA/controls/edge.mp4, outside the prefix.
        assert uri == "s3://asset-bkt/vams/xidA/controls/edge.mp4"

    def test_an_asset_on_an_existing_bucket_folder(self, transfer_entrypoint):
        source = "s3://asset-bkt/projects/site-a/clips/source.mp4"
        definition = _definition(inputS3AssetFilePath=source, inputAssetRootS3Key="projects/site-a/",
                                 inputAssetRootBucket="asset-bkt")
        assert transfer_entrypoint.control_input_s3_uri("controls/edge.mp4", definition, source, "xidA") == (
            "s3://asset-bkt/projects/site-a/controls/edge.mp4")

    def test_a_chained_input_reads_the_control_from_the_asset_bucket(self, transfer_entrypoint):
        source = "s3://run-bkt/pipelines/p0/J/output/E1/files/clips/source.mp4"
        definition = _definition(inputS3AssetFilePath=source, **MANIFEST_ROOT)
        assert transfer_entrypoint.control_input_s3_uri("controls/edge.mp4", definition, source, "xidA") == (
            "s3://asset-bkt/vams/xidA/controls/edge.mp4")

    def test_a_leading_slash_names_the_asset_root(self, transfer_entrypoint):
        assert transfer_entrypoint.control_input_s3_uri(
            "/controls/edge.mp4", _definition(**MANIFEST_ROOT), INPUT, "xidA") == (
            "s3://asset-bkt/vams/xidA/controls/edge.mp4")

    def test_without_the_manifest_root_the_key_up_to_the_asset_id(self, transfer_entrypoint):
        assert transfer_entrypoint.control_input_s3_uri("controls/edge.mp4", _definition(), INPUT, "xidA") == (
            "s3://asset-bkt/vams/xidA/controls/edge.mp4")

    def test_without_a_root_or_an_asset_id_segment_the_run_fails(self, transfer_entrypoint):
        source = "s3://asset-bkt/projects/site-a/source.mp4"
        with pytest.raises(ValueError, match="cannot be located"):
            transfer_entrypoint.control_input_s3_uri("controls/edge.mp4", _definition(), source, "xidA")


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
        transfer_entrypoint.control_input_s3_uri(value, _definition(**MANIFEST_ROOT), INPUT, "xidA")


@pytest.mark.parametrize("value", ["", "   ", None])
def test_no_control_path_names_none(transfer_entrypoint, value):
    assert transfer_entrypoint.normalize_control_path(value) == ""


class TestTheRunFailsBeforeAnyFetch:
    def _run(self, entrypoint, definition):
        with patch.object(entrypoint, "load_pipeline_definition", return_value=definition), \
                patch.object(entrypoint, "ensure_models_cached") as cached, \
                patch.object(entrypoint, "download_from_s3") as download, \
                patch.dict(os.environ, {"S3_MODEL_BUCKET": "model-bkt"}):
            with pytest.raises(ValueError, match="must name a file in the asset"):
                entrypoint.main()
        return cached, download

    def test_a_refused_path_fetches_nothing(self, transfer_entrypoint):
        cached, download = self._run(transfer_entrypoint,
                                     _definition(controlPath="../xidB/edge.mp4", **MANIFEST_ROOT))
        cached.assert_not_called()
        download.assert_not_called()
