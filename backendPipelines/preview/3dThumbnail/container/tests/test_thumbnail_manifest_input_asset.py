#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The preview's subfolder and the referenced-file root come from the input's manifest entry.

The launching Lambda names, in the Batch command's second argument, where the input sits in its asset:
``{"inputAsset": {"relativePath", "assetRootS3Key", "assetRootBucket"}}``. The folder of
``relativePath`` is the subfolder, whatever the key looks like: an asset created on an existing bucket
folder has keys without the asset ID, and an input an earlier step wrote is keyed under the run's output
folder. Without the document (an older Lambda, or a payload without a manifest) the image derives both
from the key, as before."""

import json

import pytest

from preview_pipeline import core
from preview_pipeline.utils.pipeline import objects
from preview_pipeline.utils.pipeline.objects import StageInput, StageOutput

ASSET_ID = "xd130a6d6"
OUTPUT_FILES_DIR = "pipelines/preview3dThumbnail/j1/output/exec99/files/"


def _input(key, bucket="asset-bucket"):
    return StageInput(bucketName=bucket, objectKey=key, fileExtension=".glb")


def _output(bucket="asset-bucket"):
    return StageOutput(bucketName=bucket, objectDir=OUTPUT_FILES_DIR)


def _asset(relative_path, root="", root_bucket=""):
    return {"relativePath": relative_path, "assetRootS3Key": root, "assetRootBucket": root_bucket}


@pytest.mark.unit
class TestSubfolderFromTheManifest:
    def test_a_normal_asset_file(self):
        assert core._relative_subdir(_input(f"{ASSET_ID}/test/pump.glb"), _output(), ASSET_ID,
                                     _asset("/test/pump.glb", f"{ASSET_ID}/")) == "test"

    def test_an_asset_on_an_existing_bucket_folder(self):
        # The key does not contain the asset ID: the asset-ID search put the preview at the top.
        stage_input = _input("projects/site-a/scans/pump.glb")
        assert core._relative_subdir(stage_input, _output(), ASSET_ID) == ""
        assert core._relative_subdir(stage_input, _output(), ASSET_ID,
                                     _asset("/scans/pump.glb", "projects/site-a/")) == "scans"

    def test_a_chained_input(self):
        # An earlier step's output, keyed under the run's output folder by its asset-relative path.
        stage_input = _input(f"{OUTPUT_FILES_DIR}a/b/model.glb", bucket="run-bucket")
        assert core._relative_subdir(stage_input, _output("run-bucket"), ASSET_ID,
                                     _asset("/a/b/model.glb", f"{ASSET_ID}/", "asset-bucket")) == "a/b"

    def test_two_same_named_files_in_two_subfolders_keep_apart(self):
        first = core._relative_subdir(_input("projects/site-a/left/pump.glb"), _output(), ASSET_ID,
                                      _asset("/left/pump.glb", "projects/site-a/"))
        second = core._relative_subdir(_input("projects/site-a/right/pump.glb"), _output(), ASSET_ID,
                                       _asset("/right/pump.glb", "projects/site-a/"))
        assert (first, second) == ("left", "right")

    def test_a_file_at_the_asset_root(self):
        assert core._relative_subdir(_input("projects/site-a/pump.glb"), _output(), ASSET_ID,
                                     _asset("/pump.glb", "projects/site-a/")) == ""

    def test_without_the_manifest_the_key_decides(self):
        assert core._relative_subdir(_input(f"{ASSET_ID}/test/pump.glb"), _output(), ASSET_ID, None) == "test"


@pytest.mark.unit
class TestReferenceRootFromTheManifest:
    def test_an_asset_on_an_existing_bucket_folder_is_confined_to_its_root(self):
        stage_input = _input("projects/site-a/scans/pump.gltf")
        # The old derivation confines references to the input's own folder, so ../textures is refused.
        assert core._reference_root_key(stage_input, _output(), ASSET_ID) == "projects/site-a/scans/"
        assert core._reference_root_key(stage_input, _output(), ASSET_ID,
                                        _asset("/scans/pump.gltf", "projects/site-a/")) == "projects/site-a/"

    def test_a_root_in_another_bucket_is_not_applied_to_the_input_key(self):
        stage_input = _input(f"{OUTPUT_FILES_DIR}scans/pump.gltf", bucket="run-bucket")
        assert core._reference_root_key(
            stage_input, _output("run-bucket"), ASSET_ID,
            _asset("/scans/pump.gltf", "projects/site-a/", "asset-bucket")) == OUTPUT_FILES_DIR

    def test_a_normal_asset_file(self):
        assert core._reference_root_key(_input(f"{ASSET_ID}/a/b.gltf"), _output(), ASSET_ID,
                                        _asset("/a/b.gltf", f"{ASSET_ID}/", "asset-bucket")) == f"{ASSET_ID}/"

    def test_without_the_manifest_the_key_decides(self):
        assert core._reference_root_key(_input(f"{ASSET_ID}/a/b.gltf"), _output(), ASSET_ID) == f"{ASSET_ID}/"


@pytest.mark.unit
class TestTheCommandDocument:
    def test_the_location_is_read_from_the_second_argument(self):
        document = json.dumps({"inputVersions": {}, "inputAsset": _asset("/a/b.glb", "r/", "bkt")})
        assert objects.input_asset_location([document]) == _asset("/a/b.glb", "r/", "bkt")

    @pytest.mark.parametrize("extra", [[], [json.dumps({"inputVersions": {}})], ["not json"],
                                       [json.dumps({"inputVersions": {}, "inputAsset": "x"})],
                                       [json.dumps({"inputVersions": {}, "inputAsset": _asset("")})]])
    def test_an_older_lambdas_command_names_none(self, extra):
        assert objects.input_asset_location(extra) is None

    def test_a_non_string_field_reads_as_empty(self):
        document = json.dumps({"inputVersions": {}, "inputAsset": {"relativePath": "/a.glb",
                                                                    "assetRootS3Key": 7}})
        assert objects.input_asset_location([document]) == _asset("/a.glb")

    def test_apply_input_versions_accepts_the_document(self):
        params = {"stages": [{"type": "PREVIEW_3D_THUMBNAIL", "inputFile": {"bucketName": "b", "objectKey": "k"}}]}
        document = json.dumps({"inputVersions": {"PREVIEW_3D_THUMBNAIL": "v1"},
                               "inputAsset": _asset("/k", "r/")})
        assert objects.apply_input_versions(params, [document])["stages"][0]["inputFile"]["versionId"] == "v1"
