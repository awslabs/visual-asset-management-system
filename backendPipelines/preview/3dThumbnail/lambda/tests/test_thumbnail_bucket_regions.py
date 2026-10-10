#  SPDX-License-Identifier: Apache-2.0

"""preview/3dThumbnail: the asset buckets' Regions travel from the manifest to the Batch container (D3).

``vamsExecute`` resolves ``inputBucketRegion`` / ``outputBucketRegion`` from the manifest and passes
them to ``openPipeline``, which forwards them in the state-machine input; ``constructPipeline`` turns
them into the definition's ``bucketRegions`` map (bucket name -> Region, deployment-Region buckets
left out), which the container registers before its first S3 call. Reuses the fixtures of
``test_manifest_refactor`` (same directory; the suite runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402

# This suite runs with AWS_REGION=us-east-1. The input Region is the deployment Region because this
# pipeline runs in isolated subnets and vamsExecute rejects a cross-Region INPUT before the job is
# submitted (enforce_inputs_in_region); the output Region is not gated, so it shows a value flowing.
INPUT_REGION = "us-east-1"
OUTPUT_REGION = "eu-west-1"


def _manifest_s3(manifest):
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
    return s3


@pytest.mark.unit
class TestVamsExecuteResolvesBucketRegions:
    def _run(self, manifest):
        base = refactor.TestVamsExecuteUsesManifest()
        mod = base._load_module()
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", _manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(base._manifest_body())}, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

    def test_the_payload_carries_the_manifest_regions(self):
        manifest = refactor.TestVamsExecuteUsesManifest()._manifest()
        manifest["inputFiles"][0]["bucketRegion"] = INPUT_REGION
        manifest["outputs"]["bucketRegion"] = OUTPUT_REGION
        payload = self._run(manifest)
        assert payload["inputBucketRegion"] == INPUT_REGION
        assert payload["outputBucketRegion"] == OUTPUT_REGION

    def test_a_manifest_written_without_regions_sends_empty_regions(self):
        manifest = refactor.TestVamsExecuteUsesManifest()._manifest()
        manifest["inputFiles"][0].pop("bucketRegion", None)
        manifest["outputs"].pop("bucketRegion", None)
        payload = self._run(manifest)
        assert (payload["inputBucketRegion"], payload["outputBucketRegion"]) == ("", "")


@pytest.mark.unit
class TestOpenPipelineForwardsBucketRegions:
    def _run(self, event):
        base = refactor.TestOpenPipelineRegistration()
        mod = base._load_module()
        start = base._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(start.call_args.kwargs["input"])

    def test_the_state_machine_input_carries_the_regions(self):
        event = dict(refactor.TestOpenPipelineRegistration()._event(),
                     inputBucketRegion=INPUT_REGION, outputBucketRegion=OUTPUT_REGION)
        sfn_input = self._run(event)
        assert sfn_input["inputBucketRegion"] == INPUT_REGION
        assert sfn_input["outputBucketRegion"] == OUTPUT_REGION

    def test_an_event_from_an_older_lambda_forwards_empty_regions(self):
        sfn_input = self._run(refactor.TestOpenPipelineRegistration()._event())
        assert (sfn_input["inputBucketRegion"], sfn_input["outputBucketRegion"]) == ("", "")


@pytest.mark.unit
class TestConstructPipelineMapsBucketsToRegions:
    def _construct(self):
        return refactor.TestContainerContractAcceptsConstructOutput()._construct_module()

    def _event(self, **overrides):
        event = {
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": "s3://in-bkt/xidM/test/pump.glb",
            "outputS3AssetFilesPath": "s3://out-bkt/pipelines/p1/MJOB/output/E1/files/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux-bkt/xidM/test/pump.glb/preview/p1/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
            "assetId": "xidM",
        }
        event.update(overrides)
        return event

    def _definition(self, event):
        mod = self._construct()
        out = mod.lambda_handler(event, MagicMock())
        return json.loads(out["definition"][0])

    def test_each_asset_bucket_maps_to_its_region(self):
        definition = self._definition(self._event(
            inputBucketRegion=INPUT_REGION, outputBucketRegion=OUTPUT_REGION))
        assert definition["bucketRegions"] == {"in-bkt": INPUT_REGION, "out-bkt": OUTPUT_REGION}

    def test_deployment_region_buckets_and_the_auxiliary_bucket_are_left_out(self):
        definition = self._definition(self._event(inputBucketRegion="", outputBucketRegion=OUTPUT_REGION))
        assert definition["bucketRegions"] == {"out-bkt": OUTPUT_REGION}
        assert self._definition(self._event())["bucketRegions"] == {}

    def test_one_bucket_for_input_and_output_maps_once(self):
        mod = self._construct()
        event = self._event(outputS3AssetFilesPath="s3://in-bkt/pipelines/p1/JOB/output/E1/files/",
                            inputBucketRegion=INPUT_REGION, outputBucketRegion=INPUT_REGION)
        assert mod.bucket_regions(event) == {"in-bkt": INPUT_REGION}

    def test_the_container_definition_accepts_and_defaults_the_map(self):
        definition = self._definition(self._event(
            inputBucketRegion=INPUT_REGION, outputBucketRegion=OUTPUT_REGION))
        PipelineDefinition = refactor.TestContainerContractAcceptsConstructOutput()._container_pipeline_definition()
        assert PipelineDefinition(**definition).bucketRegions == {"in-bkt": INPUT_REGION, "out-bkt": OUTPUT_REGION}
        definition.pop("bucketRegions")
        assert PipelineDefinition(**definition).bucketRegions is None
