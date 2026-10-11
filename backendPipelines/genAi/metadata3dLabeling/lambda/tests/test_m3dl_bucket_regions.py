#  SPDX-License-Identifier: Apache-2.0

"""genAi/metadata3dLabeling: the asset buckets' Regions travel from the manifest to the Batch container (D3).

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


def _container_pipeline_definition():
    import importlib.util
    path = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "container", "main", "utils", "pipeline", "objects.py"))
    spec = importlib.util.spec_from_file_location("m3dl_container_objects_regions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PipelineDefinition

# This suite runs with AWS_REGION=us-east-1. The input Region is the deployment Region because this
# pipeline runs in isolated subnets and vamsExecute rejects a cross-Region INPUT before the job is
# submitted (enforce_inputs_in_region) unless that Region is served by one of the VPC's cross-Region
# S3 endpoints (CROSS_REGION_S3_ENDPOINT_REGIONS, exercised below); the output Region is not gated,
# so it shows a value flowing.
INPUT_REGION = "us-east-1"
OUTPUT_REGION = "eu-west-1"


def _manifest_s3(manifest):
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
    return s3


@pytest.mark.unit
class TestVamsExecuteResolvesBucketRegions:
    def _run(self, manifest):
        base = refactor.TestVamsExecute()
        mod = base._load()
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", _manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(base._body())}, MagicMock())
        assert resp["statusCode"] == 200
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
        assert resp["statusCode"] == 200
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
    def _construct(self):
        return refactor.TestConstructPipeline()._load()

    def _event(self, **overrides):
        event = {
            "jobName": "PipelineJob_x",
            "inputS3AssetFilePath": "s3://in-bkt/xidM/test/pump.glb",
            "outputS3AssetFilesPath": "s3://out-bkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetMetadataPath": "s3://out-bkt/pipelines/p1/MJOB/output/E1/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux-bkt/xidM/test/pump.glb/genAi/metadata3dLabeling/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "externalSfnTaskToken": "tok",
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
        PipelineDefinition = _container_pipeline_definition()
        assert PipelineDefinition(**definition).bucketRegions == {"in-bkt": INPUT_REGION, "out-bkt": OUTPUT_REGION}
        definition.pop("bucketRegions")
        assert PipelineDefinition(**definition).bucketRegions is None


@pytest.mark.unit
class TestVamsExecuteAcceptsEndpointServedRegions:
    """The isolated-subnet gate admits an input Region the VPC reaches through a cross-Region S3
    interface endpoint, which the CDK names in CROSS_REGION_S3_ENDPOINT_REGIONS on this Lambda."""

    ENDPOINT_REGION = "eu-west-1"
    UNSERVED_REGION = "ap-southeast-2"

    def _handle(self, manifest, monkeypatch, endpoint_regions):
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", endpoint_regions)
        base = refactor.TestVamsExecute()
        mod = base._load()
        invoke = MagicMock(return_value={"StatusCode": 200})
        send_task_failure = MagicMock()
        with patch.object(mod, "s3_client", _manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke), \
                patch.object(mod.sfn_client, "send_task_failure", send_task_failure):
            resp = mod.lambda_handler({"body": json.dumps(base._body())}, MagicMock())
        return resp, invoke, send_task_failure

    def test_an_input_in_an_endpoint_served_region_reaches_the_payload(self, monkeypatch):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["bucketRegion"] = self.ENDPOINT_REGION
        # Two Regions in the list: the match must not depend on the value being the whole string.
        resp, invoke, send_task_failure = self._handle(
            manifest, monkeypatch, f"eu-north-1,{self.ENDPOINT_REGION}")
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputBucketRegion"] == self.ENDPOINT_REGION
        send_task_failure.assert_not_called()

    def test_an_input_in_an_unserved_region_is_rejected_before_the_job_is_submitted(self, monkeypatch):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["bucketRegion"] = self.UNSERVED_REGION
        resp, invoke, send_task_failure = self._handle(manifest, monkeypatch, self.ENDPOINT_REGION)
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        cause = send_task_failure.call_args.kwargs["cause"]
        assert "isolated subnets" in cause and self.UNSERVED_REGION in cause

    def test_without_the_variable_every_cross_region_input_is_rejected(self, monkeypatch):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["bucketRegion"] = self.ENDPOINT_REGION
        resp, invoke, send_task_failure = self._handle(manifest, monkeypatch, "")
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        send_task_failure.assert_called_once()
