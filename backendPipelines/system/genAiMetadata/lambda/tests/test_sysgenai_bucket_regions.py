#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""system/genAiMetadata: the asset buckets' Regions travel from the manifest to the branch handlers.

``vamsExecute`` resolves ``inputBucketRegion`` / ``outputBucketRegion`` from the manifest; when the
pipeline's functions run in the VPC (``PIPELINE_LAMBDAS_IN_VPC``) it admits only an input Region the
isolated subnets can reach (the deployment Region, or one with a cross-Region S3 endpoint named in
``CROSS_REGION_S3_ENDPOINT_REGIONS``), and outside the VPC any Region. It passes both Regions to ``openPipeline``,
which forwards them in the state-machine input; ``constructPipeline`` reads the input file with a client
for its Region and turns the Regions into the state's ``bucketRegions`` map (bucket name -> Region,
deployment-Region buckets left out), which the containers register before their first S3 call."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

import sysgenai_harness as h
import test_sysgenai_construct_pipeline as construct
import test_sysgenai_open_pipeline as open_pipeline
import test_sysgenai_vams_execute as vams_execute

# The suite runs with AWS_REGION=us-east-1 (the harness default). A cross-Region INPUT is admitted only
# through an endpoint-served Region; the output Region is not gated, so it shows a value flowing.
REMOTE_REGION = "eu-west-1"
OUTPUT_REGION = "ap-southeast-2"


def _manifest_with_regions(input_region, output_region):
    manifest = vams_execute._manifest()
    manifest["inputFiles"][0]["bucketRegion"] = input_region
    manifest["outputs"]["bucketRegion"] = output_region
    return manifest


@pytest.mark.unit
class TestVamsExecuteCarriesAndGatesTheRegions:
    def test_the_payload_carries_the_manifest_regions(self, monkeypatch):
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", REMOTE_REGION)
        mod, response = vams_execute._run(vams_execute._body(), _manifest_with_regions(REMOTE_REGION, OUTPUT_REGION))
        assert response["statusCode"] == 200
        payload = vams_execute._payload(mod)
        assert payload["inputBucketRegion"] == REMOTE_REGION
        assert payload["outputBucketRegion"] == OUTPUT_REGION

    def test_a_manifest_written_without_regions_sends_empty_regions(self):
        manifest = vams_execute._manifest()
        manifest["inputFiles"][0].pop("bucketRegion", None)
        manifest["outputs"].pop("bucketRegion", None)
        mod, response = vams_execute._run(vams_execute._body(), manifest)
        assert response["statusCode"] == 200
        payload = vams_execute._payload(mod)
        assert (payload["inputBucketRegion"], payload["outputBucketRegion"]) == ("", "")

    def test_without_a_vpc_an_input_in_a_region_with_no_endpoint_is_admitted(self, monkeypatch):
        """The functions run outside the VPC unless every Lambda is in it, and read any Region."""
        monkeypatch.delenv("CROSS_REGION_S3_ENDPOINT_REGIONS", raising=False)
        monkeypatch.delenv("PIPELINE_LAMBDAS_IN_VPC", raising=False)
        mod, response = vams_execute._run(vams_execute._body(), _manifest_with_regions(REMOTE_REGION, ""))
        assert response["statusCode"] == 200
        mod.sfn_client.send_task_failure.assert_not_called()
        assert vams_execute._payload(mod)["inputBucketRegion"] == REMOTE_REGION

    def test_with_the_lambdas_outside_the_vpc_flag_false_the_input_is_admitted(self, monkeypatch):
        monkeypatch.delenv("CROSS_REGION_S3_ENDPOINT_REGIONS", raising=False)
        monkeypatch.setenv("PIPELINE_LAMBDAS_IN_VPC", "false")
        _mod, response = vams_execute._run(vams_execute._body(), _manifest_with_regions(REMOTE_REGION, ""))
        assert response["statusCode"] == 200

    def test_an_input_in_a_region_with_no_endpoint_is_rejected_and_reported(self, monkeypatch):
        """With every Lambda in the VPC (useForAllLambdas) the readers are in the isolated subnets."""
        monkeypatch.delenv("CROSS_REGION_S3_ENDPOINT_REGIONS", raising=False)
        monkeypatch.setenv("PIPELINE_LAMBDAS_IN_VPC", "true")
        mod, response = vams_execute._run(vams_execute._body(), _manifest_with_regions(REMOTE_REGION, ""))
        assert response["statusCode"] == 500
        mod.lambda_client.invoke.assert_not_called()
        vams_execute._assert_token_failed(mod)
        cause = mod.sfn_client.send_task_failure.call_args.kwargs["cause"]
        assert "Cross-Region inputs are not supported" in cause
        assert "runs in the VPC's isolated subnets" in cause

    def test_an_input_in_an_endpoint_served_region_is_admitted(self, monkeypatch):
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", f"us-west-2, {REMOTE_REGION}")
        monkeypatch.setenv("PIPELINE_LAMBDAS_IN_VPC", "true")
        mod, response = vams_execute._run(vams_execute._body(), _manifest_with_regions(REMOTE_REGION, ""))
        assert response["statusCode"] == 200
        mod.sfn_client.send_task_failure.assert_not_called()

    def test_the_gate_runs_after_the_single_file_check_inside_the_reporting_try(self):
        with open(os.path.join(h.LAMBDA_DIR, "vamsExecuteSystemGenAiMetadataPipeline.py"), encoding="utf-8") as handle:
            source = handle.read()
        single = source.index("manifestHelper.enforce_single_input_file(resolved)")
        gate = source.index("manifestHelper.enforce_inputs_in_region(resolved)")
        assert gate > single
        assert "if lambdas_in_vpc():" in source[single:gate]
        assert "except" not in source[single:gate]
        assert "enforce_same_region_io" not in source


@pytest.mark.unit
class TestOpenPipelineForwardsTheRegions:
    def test_the_state_machine_input_carries_the_regions(self):
        mod = open_pipeline._load()
        event = open_pipeline._event("s3://abkt/xidM/models/pump.glb",
                                     inputBucketRegion=REMOTE_REGION, outputBucketRegion=OUTPUT_REGION)
        response = open_pipeline._run(mod, event)
        assert response["statusCode"] == 200
        sfn_input = json.loads(mod.sfn.start_execution.call_args.kwargs["input"])
        assert sfn_input["inputBucketRegion"] == REMOTE_REGION
        assert sfn_input["outputBucketRegion"] == OUTPUT_REGION

    def test_an_event_from_an_older_lambda_forwards_empty_regions(self):
        mod = open_pipeline._load()
        response = open_pipeline._run(mod, open_pipeline._event("s3://abkt/xidM/models/pump.glb"))
        assert response["statusCode"] == 200
        sfn_input = json.loads(mod.sfn.start_execution.call_args.kwargs["input"])
        assert (sfn_input["inputBucketRegion"], sfn_input["outputBucketRegion"]) == ("", "")


@pytest.mark.unit
class TestConstructPipelineBucketRegions:
    KEY = "xidM/models/pump.glb"

    def test_the_state_maps_each_cross_region_bucket_to_its_region(self):
        s3 = construct._s3_with(self.KEY, b"glTF" + b"\x00" * 60, "model/gltf-binary")
        regional = construct.h.FakeS3({("abkt", self.KEY): b"glTF" + b"\x00" * 60})
        regional.heads[("abkt", self.KEY)] = s3.heads[("abkt", self.KEY)]
        mod = h.load_handler("constructPipeline")
        mod.s3_client = s3
        with patch.object(mod.manifestHelper, "s3_client_for_region", return_value=regional) as factory:
            state = mod.lambda_handler(construct._state(
                inputBucketRegion=REMOTE_REGION,
                outputS3AssetFilesPath="s3://outbkt/pipelines/sgm/sgm/output/E1/files/",
                outputBucketRegion=OUTPUT_REGION), MagicMock())
        factory.assert_called_once_with(REMOTE_REGION)
        assert state["bucketRegions"] == {"abkt": REMOTE_REGION, "outbkt": OUTPUT_REGION}
        # The input file's head, header and digest reads all went to the Region client, not the default.
        assert construct._input_gets(s3, self.KEY) == []
        assert construct._input_gets(regional, self.KEY)
        # The configuration and the analysis manifest stay on the default (deployment-Region) client.
        assert any(k == construct.CONFIG_KEY for _, k, _ in s3.gets)

    def test_deployment_region_buckets_are_left_out_and_read_by_the_default_client(self):
        s3 = construct._s3_with(self.KEY, b"glTF" + b"\x00" * 60, "model/gltf-binary")
        mod = h.load_handler("constructPipeline")
        mod.s3_client = s3
        with patch.object(mod.manifestHelper, "s3_client_for_region") as factory:
            state = mod.lambda_handler(construct._state(inputBucketRegion="", outputBucketRegion=""), MagicMock())
        factory.assert_not_called()
        assert state["bucketRegions"] == {}
        assert construct._input_gets(s3, self.KEY)

    def test_the_manifest_asset_root_reaches_the_branch_state(self):
        """The same hop carries the manifest's asset root: vamsExecute's payload, openPipeline's state-machine
        input, then constructPipeline's state, which the render branches receive as their event."""
        manifest = vams_execute._manifest()
        manifest["inputFiles"][0].update({"assetRootS3Key": "projects/site-7/", "key": "projects/site-7/models/pump.glb"})
        vams, _response = vams_execute._run(vams_execute._body(), manifest)
        payload = vams_execute._payload(vams)
        opener = open_pipeline._load()
        open_pipeline._run(opener, payload)
        sfn_input = json.loads(opener.sfn.start_execution.call_args.kwargs["input"])
        key = "projects/site-7/models/pump.glb"
        s3 = construct._s3_with(key, b"glTF" + b"\x00" * 60, "model/gltf-binary")
        mod = h.load_handler("constructPipeline")
        mod.s3_client = s3
        state = mod.lambda_handler(dict(sfn_input, inputS3AssetFilePath=f"s3://abkt/{key}",
                                        inputBucketRegion="", outputBucketRegion=""), MagicMock())
        assert state["assetRootS3Key"] == "projects/site-7/"

    def test_a_state_from_an_older_lambda_carries_an_empty_map(self):
        s3 = construct._s3_with(self.KEY, b"glTF" + b"\x00" * 60, "model/gltf-binary")
        mod, state = construct._run(construct._state(), s3)
        assert state["bucketRegions"] == {}

    def test_bucket_regions_reads_the_bucket_name_off_each_uri(self):
        mod = h.load_handler("constructPipeline")
        assert mod.bucket_regions({
            "inputS3AssetFilePath": "s3://in-bkt/a/b.glb", "inputBucketRegion": "eu-west-1",
            "outputS3AssetFilesPath": "s3://out-bkt/p/files/", "outputBucketRegion": "eu-west-1",
        }) == {"in-bkt": "eu-west-1", "out-bkt": "eu-west-1"}
        assert mod.bucket_regions({"inputS3AssetFilePath": "s3://in-bkt/a", "inputBucketRegion": ""}) == {}
        assert mod.bucket_regions({"inputS3AssetFilePath": "not-a-uri", "inputBucketRegion": "eu-west-1"}) == {}


def _oversized_cross_region_run(monkeypatch, endpoint_regions):
    """constructPipeline on a 3 MiB mesh in a REMOTE_REGION bucket with the Fargate renderer deployed and
    a 2 MiB Lambda limit; the input is read through a stub regional client."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", endpoint_regions)
    s3 = construct._s3_with("xidM/models/pump.glb", b"glTF....", ContentLength=3 * 1024 * 1024)
    mod = h.load_handler("constructPipeline", {"MAX_INPUT_FILE_SIZE_MB": "2", "USE_FARGATE_RENDERER": "true"})
    mod.s3_client = s3
    with patch.object(mod.manifestHelper, "s3_client_for_region", return_value=s3):
        state = mod.lambda_handler(construct._state(inputBucketRegion=REMOTE_REGION), MagicMock())
    return state, s3.json_at("aux", construct.MANIFEST_KEY)


@pytest.mark.unit
class TestFargateRendererReadsOnlyEndpointServedRegions:
    """The Fargate renderer runs in the isolated subnets. An over-limit file has no Lambda branch, so a
    file whose bucket Region the VPC cannot reach is described from its attributes, with a warning."""

    def test_an_oversized_input_in_a_region_without_an_endpoint_is_described_from_its_attributes(
            self, monkeypatch):
        state, manifest = _oversized_cross_region_run(monkeypatch, "")
        assert (state["renderBranch"], state["renderSkipped"]) == ("NONE", "size")
        assert manifest["renderBranch"] == "NONE"
        assert any("Fargate renderer not used" in w and REMOTE_REGION in w for w in manifest["warnings"])

    def test_an_oversized_input_in_an_endpoint_served_region_goes_to_fargate(self, monkeypatch):
        state, manifest = _oversized_cross_region_run(monkeypatch, f"us-west-2,{REMOTE_REGION}")
        assert (state["renderBranch"], state["renderSkipped"]) == ("FARGATE", None)
        assert not any("Fargate renderer not used" in w for w in manifest["warnings"])
