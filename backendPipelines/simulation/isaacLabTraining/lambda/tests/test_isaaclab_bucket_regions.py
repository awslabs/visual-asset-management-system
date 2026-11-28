#  SPDX-License-Identifier: Apache-2.0

"""simulation/isaacLabTraining: the asset buckets' Regions travel from the manifest to the Batch job (D3).

``vamsExecute`` resolves ``inputBucketRegion`` / ``outputBucketRegion`` from the manifest and puts them
in the state-machine input; ``openPipeline`` reads the asset bucket with a client for the input
bucket's Region and writes the job config's ``bucketRegions`` map (bucket name -> Region,
deployment-Region buckets left out), which the container registers before its first S3 call. Reuses
the fixtures of ``test_manifest_refactor`` (same directory; the suite runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402

# This suite runs with AWS_REGION=us-east-1. This pipeline runs in private subnets, so a cross-Region
# input is admitted and its Region must reach the job.
INPUT_REGION = "eu-west-1"
OUTPUT_REGION = "eu-central-1"


@pytest.mark.unit
class TestVamsExecuteResolvesBucketRegions:
    def _run(self, manifest):
        base = refactor.TestVamsExecute()
        mod = base._load()
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
        start = MagicMock(return_value={"executionArn": "arn:aws:states:us-east-1:1:execution:Isaac:job"})
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.sfn_client, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler({"body": json.dumps(base._body())}, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(start.call_args.kwargs["input"])

    def test_the_state_machine_input_carries_the_manifest_regions(self):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["bucketRegion"] = INPUT_REGION
        manifest["outputs"]["bucketRegion"] = OUTPUT_REGION
        sfn_input = self._run(manifest)
        assert sfn_input["inputBucketRegion"] == INPUT_REGION
        assert sfn_input["outputBucketRegion"] == OUTPUT_REGION

    def test_a_manifest_written_without_regions_sends_empty_regions(self):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0].pop("bucketRegion", None)
        manifest["outputs"].pop("bucketRegion", None)
        sfn_input = self._run(manifest)
        assert (sfn_input["inputBucketRegion"], sfn_input["outputBucketRegion"]) == ("", "")


@pytest.mark.unit
class TestOpenPipelineMapsBucketsToRegions:
    def _event(self, **overrides):
        event = dict(refactor.TestOpenPipeline()._event(),
                     inputS3AssetFilePath="s3://in-bkt/xidM/scene.usd",
                     outputS3AssetFilesPath="s3://out-bkt/pipelines/p1/MJOB/output/E1/files/")
        event.update(overrides)
        return event

    def _job_config(self, event):
        mod = refactor.TestOpenPipeline()._load()
        s3 = MagicMock(get_object=MagicMock(side_effect=Exception("no file config")))
        with patch.object(mod, "s3_client", s3):
            out = mod.lambda_handler(event, MagicMock())
        return json.loads(out["definition"])

    def test_each_asset_bucket_maps_to_its_region(self):
        job_config = self._job_config(self._event(
            inputBucketRegion=INPUT_REGION, outputBucketRegion=OUTPUT_REGION))
        assert job_config["bucketRegions"] == {"in-bkt": INPUT_REGION, "out-bkt": OUTPUT_REGION}

    def test_deployment_region_buckets_are_left_out(self):
        assert self._job_config(self._event(
            inputBucketRegion="", outputBucketRegion=OUTPUT_REGION))["bucketRegions"] == {"out-bkt": OUTPUT_REGION}
        assert self._job_config(self._event())["bucketRegions"] == {}

    def test_the_input_file_is_read_with_the_input_bucket_region_client(self):
        mod = refactor.TestOpenPipeline()._load()
        event = self._event(inputS3AssetFilePath="s3://in-bkt/xidM/config.json",
                            inputBucketRegion=INPUT_REGION, outputBucketRegion=OUTPUT_REGION)
        default_client = MagicMock(name="default")
        regional = MagicMock(name=INPUT_REGION)
        regional.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(
            {"trainingConfig": {"maxIterations": 7}}).encode("utf-8"))}
        with patch.object(mod, "s3_client", default_client), \
                patch.object(mod.manifestHelper, "s3_client_for_region",
                             MagicMock(return_value=regional)) as for_region:
            out = mod.lambda_handler(event, MagicMock())
        for_region.assert_called_with(INPUT_REGION)
        regional.get_object.assert_called_once_with(Bucket="in-bkt", Key="xidM/config.json")
        default_client.get_object.assert_not_called()
        # The file's standing defaults were read through that client
        assert json.loads(out["definition"])["trainingConfig"]["maxIterations"] == 7

    def test_an_unplaced_bucket_is_read_with_the_default_client(self):
        mod = refactor.TestOpenPipeline()._load()
        mod._bucket_regions.clear()
        event = self._event(inputS3AssetFilePath="s3://in-bkt/xidM/config.json")
        default_client = MagicMock(name="default")
        default_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"{}")}
        with patch.object(mod, "s3_client", default_client):
            mod.lambda_handler(event, MagicMock())
        default_client.get_object.assert_called_once_with(Bucket="in-bkt", Key="xidM/config.json")
