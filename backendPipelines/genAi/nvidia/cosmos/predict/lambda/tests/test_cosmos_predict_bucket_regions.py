#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""genAi/nvidia/cosmos/predict: the asset buckets' Regions travel from the manifest to the Batch container.

``vamsExecute`` resolves ``inputBucketRegion`` / ``outputBucketRegion`` from the manifest and passes
them to ``openPipeline``, which forwards them in the state-machine input; ``constructPipeline`` turns
them into the definition's ``bucketRegions`` map (bucket name -> Region, deployment-Region buckets
left out), which the container registers before its first ``aws s3`` command so each command carries
``--region`` for the bucket it addresses. Reuses the fixtures of ``test_manifest_refactor`` (same
directory; the suite runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402

# This suite runs with AWS_REGION=us-east-1; both Regions differ from it so each shows a value flowing.
INPUT_REGION = "eu-west-1"
OUTPUT_REGION = "ap-southeast-2"
INPUT_PATH_KEY = "inputS3AssetFilePath"


def _s3_router(manifest, metadata_envelope, config):
    """An s3 mock whose get_object serves the manifest, the metadata envelope or the configuration
    by key suffix, whatever order the handler reads them in."""
    def get_object(Bucket, Key):
        if Key.endswith("manifest.json"):
            payload = manifest
        elif Key.endswith("metadata.json"):
            payload = metadata_envelope
        elif Key.endswith("config.json"):
            payload = config
        else:
            raise Exception(f"unexpected key {Key}")
        return {"Body": MagicMock(read=lambda p=payload: json.dumps(p).encode("utf-8"))}
    s3 = MagicMock()
    s3.get_object.side_effect = get_object
    return s3


@pytest.mark.unit
class TestVamsExecuteResolvesBucketRegions:
    def _run(self, manifest):
        base = refactor.TestVamsExecute()
        mod = base._load("video2world")
        s3 = _s3_router(manifest, base._metadata_envelope("video2world"), {})
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(base._body("video2world"))}, MagicMock())
        assert resp["statusCode"] == 200, resp
        return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

    def test_the_payload_carries_the_manifest_regions(self):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["bucketRegion"] = INPUT_REGION
        manifest["outputs"]["bucketRegion"] = OUTPUT_REGION
        payload = self._run(manifest)
        assert payload["inputBucketRegion"] == INPUT_REGION
        assert payload["outputBucketRegion"] == OUTPUT_REGION

    def test_a_manifest_written_without_regions_sends_empty_regions(self):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0].pop("bucketRegion", None)
        manifest["outputs"].pop("bucketRegion", None)
        payload = self._run(manifest)
        assert (payload["inputBucketRegion"], payload["outputBucketRegion"]) == ("", "")


@pytest.mark.unit
class TestOpenPipelineForwardsBucketRegions:
    def _run(self, event):
        base = refactor.TestOpenPipeline()
        mod = base._load()
        start = base._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200, resp
        return json.loads(start.call_args.kwargs["input"])

    def test_the_state_machine_input_carries_the_regions(self):
        event = dict(refactor.TestOpenPipeline()._event(),
                     inputBucketRegion=INPUT_REGION, outputBucketRegion=OUTPUT_REGION)
        sfn_input = self._run(event)
        assert sfn_input["inputBucketRegion"] == INPUT_REGION
        assert sfn_input["outputBucketRegion"] == OUTPUT_REGION

    def test_an_event_from_an_older_lambda_forwards_empty_regions(self):
        sfn_input = self._run(refactor.TestOpenPipeline()._event())
        assert (sfn_input["inputBucketRegion"], sfn_input["outputBucketRegion"]) == ("", "")


@pytest.mark.unit
class TestConstructPipelineMapsBucketsToRegions:
    def _event(self, **overrides):
        event = {
            INPUT_PATH_KEY: "s3://in-bkt/xidM/clip.mp4",
            "outputS3AssetFilesPath": "s3://out-bkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://out-bkt/pipelines/p1/MJOB/output/E1/previews/",
            "outputS3AssetMetadataPath": "s3://out-bkt/pipelines/p1/MJOB/output/E1/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/p/E1/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "assetId": "xidM",
            "databaseId": "dbM",
            "externalSfnTaskToken": "tok",
            "modelType": "video2world",
            "cosmosPrompt": "a calm robot",
        }
        event.update(overrides)
        return event

    def _definition(self, event):
        mod = refactor.TestConstructPipeline()._load()
        out = mod.lambda_handler(event, MagicMock())
        return json.loads(out["definition"][2])

    def test_each_asset_bucket_maps_to_its_region(self):
        definition = self._definition(self._event(
            inputBucketRegion=INPUT_REGION, outputBucketRegion=OUTPUT_REGION))
        assert definition["bucketRegions"] == {"in-bkt": INPUT_REGION, "out-bkt": OUTPUT_REGION}

    def test_deployment_region_buckets_and_the_auxiliary_bucket_are_left_out(self):
        definition = self._definition(self._event(inputBucketRegion="", outputBucketRegion=OUTPUT_REGION))
        assert definition["bucketRegions"] == {"out-bkt": OUTPUT_REGION}
        assert self._definition(self._event())["bucketRegions"] == {}

    def test_one_bucket_for_input_and_output_maps_once(self):
        mod = refactor.TestConstructPipeline()._load()
        event = self._event(outputS3AssetFilesPath="s3://in-bkt/pipelines/p1/JOB/output/E1/files/",
                            inputBucketRegion=INPUT_REGION, outputBucketRegion=INPUT_REGION)
        assert mod.bucket_regions(event) == {"in-bkt": INPUT_REGION}
