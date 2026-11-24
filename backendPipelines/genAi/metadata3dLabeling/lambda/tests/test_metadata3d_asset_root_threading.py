# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The input's asset root (the manifest's ``assetRootS3Key``) travels from vamsExecute through
openPipeline and constructPipeline into the Blender stage's ``inputFile``, where the container decides
which prefix it lists for the hierarchy download."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_manifest_refactor as refactor_tests  # noqa: E402

LOCATION = "p/model.gltf"


def _payload(root, key=LOCATION):
    base = refactor_tests.TestVamsExecute()
    mod = base._load()
    manifest = base._manifest()
    entry = manifest["inputFiles"][0]
    entry["key"] = key
    entry["assetRootS3Key"] = root
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
    invoke = MagicMock(return_value={"StatusCode": 200})
    with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
        resp = mod.lambda_handler({"body": json.dumps(base._body())}, MagicMock())
    assert resp["statusCode"] == 200
    return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))


@pytest.mark.unit
class TestTheAssetRootReachesTheBlenderStage:
    @pytest.mark.parametrize("root", [LOCATION, "p/"])
    def test_vams_execute_forwards_the_manifest_root(self, root):
        assert _payload(root)["assetRootS3Key"] == root

    def test_open_pipeline_puts_it_on_the_state_machine_input(self):
        base = refactor_tests.TestOpenPipeline()
        mod = base._load()
        start = base._mock_start()
        event = dict(base._event(), assetRootS3Key=LOCATION)
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            mod.lambda_handler(event, MagicMock())
        assert json.loads(start.call_args.kwargs["input"])["assetRootS3Key"] == LOCATION

    @pytest.mark.parametrize("root", [LOCATION, ""])
    def test_construct_pipeline_puts_it_on_the_blender_input(self, root):
        mod = refactor_tests.TestConstructPipeline()._load()
        event = {
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": f"s3://abkt/{LOCATION}",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/MJOB/output/E1/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/p/model.gltf/genAi/metadata3dLabeling/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
        }
        if root:
            event["assetRootS3Key"] = root
        definition = json.loads(mod.lambda_handler(event, MagicMock())["definition"][0])
        blender = [s for s in definition["stages"] if s["type"] == "BLENDERRENDERER"][0]
        assert blender["inputFile"]["assetRootS3Key"] == root
