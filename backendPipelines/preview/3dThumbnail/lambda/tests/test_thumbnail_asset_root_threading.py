#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The input's asset root (the manifest's ``assetRootS3Key``) travels from vamsExecute through
openPipeline and constructPipeline into the stage's ``inputFile``, where the container resolves a .gltf
input's resources against it."""

import datetime
import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_manifest_refactor as refactor_tests  # noqa: E402

LOCATION = "p/model.gltf"


def _load(name):
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def _payload(manifest):
    base = refactor_tests.TestVamsExecuteUsesManifest()
    mod = base._load_module()
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
    invoke = MagicMock(return_value={"StatusCode": 200})
    with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
        resp = mod.lambda_handler({"body": json.dumps(base._manifest_body())}, MagicMock())
    assert resp["statusCode"] == 200
    return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))


def _manifest(root, key=LOCATION):
    manifest = refactor_tests.TestVamsExecuteUsesManifest()._manifest()
    manifest["inputFiles"][0].update({"key": key, "assetRootS3Key": root})
    return manifest


@pytest.mark.unit
class TestTheAssetRootReachesTheStage:
    @pytest.mark.parametrize("root", [LOCATION, "p/"])
    def test_vams_execute_forwards_the_manifest_root(self, root):
        assert _payload(_manifest(root))["assetRootS3Key"] == root

    def test_a_manifest_entry_that_is_not_the_input_forwards_no_root(self):
        # Without a manifest the legacy input path is used, and the entry's root does not describe it.
        mod = refactor_tests.TestVamsExecuteUsesManifest()._load_module()
        resolved = {"inputS3AssetFilePath": "s3://abkt/legacy/pump.glb",
                    "inputFiles": [{"bucket": "abkt", "key": LOCATION, "assetRootS3Key": LOCATION}]}
        assert mod.input_asset_root(resolved) == ""

    def test_open_pipeline_puts_it_on_the_state_machine_input(self):
        mod = _load("openPipeline")
        start = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:Preview3dThumbnail:PipelineJob_x",
            "startDate": datetime.datetime(2026, 1, 1)})
        event = {
            "inputS3AssetFilePath": f"s3://abkt/{LOCATION}",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/MJOB/output/E1/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/MJOB/output/E1/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/p1/E1/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
            "assetRootS3Key": LOCATION,
        }
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            mod.lambda_handler(event, MagicMock())
        assert json.loads(start.call_args.kwargs["input"])["assetRootS3Key"] == LOCATION

    @pytest.mark.parametrize("root", [LOCATION, ""])
    def test_construct_pipeline_puts_it_on_the_stage_input(self, root):
        mod = _load("constructPipeline")
        event = {"jobName": "PipelineJob_x", "inputS3AssetFilePath": f"s3://abkt/{LOCATION}",
                 "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/"}
        if root:
            event["assetRootS3Key"] = root
        definition = mod.construct_preview_3d_thumbnail_definition(event)
        assert definition["stages"][0]["inputFile"]["assetRootS3Key"] == root
