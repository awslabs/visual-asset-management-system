#  SPDX-License-Identifier: Apache-2.0

"""GenAI CAD STEP agent: the asset buckets' Regions travel from the manifest to the agent container.

``vamsExecute`` resolves ``inputBucketRegion`` / ``outputBucketRegion`` from the manifest and passes
them to ``openPipeline``, which forwards them in the state-machine input; ``constructPipeline`` turns
them into the definition's ``bucketRegions`` map (bucket name -> Region, deployment-Region buckets
left out), and both run tasks -- ``invokeAgentRuntime`` (Amazon Bedrock AgentCore) and
``executeBatchJob`` (AWS Batch on Fargate) -- hand that definition to the container unchanged. The
handler applies no Region gate: the container runs outside the VPC (AgentCore) or in private subnets
(Fargate), both with a route to Amazon S3 in the bucket Region. Reuses the fixtures of
``test_cad_step_agent_handlers`` (same directory; the suite runs alone)."""

import datetime
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import test_cad_step_agent_handlers as handlers  # noqa: E402

# The suite runs with AWS_REGION=us-east-1 (handlers._ENV); both Regions below are elsewhere.
INPUT_REGION = "eu-west-1"
OUTPUT_REGION = "eu-central-1"


def _manifest(input_region=INPUT_REGION, output_region=OUTPUT_REGION, with_input=True):
    """A workflow manifest as executeWorkflow writes it, with the bucket Regions set."""
    return {
        "schemaVersion": 1,
        "inputFiles": [{"relativePath": "/part.stp", "databaseId": "db1", "assetId": "xasset1",
                        "assetRootS3Key": "xasset1/", "auxPreviewPrefix": "db1/xasset1/part.stp/preview",
                        "bucket": "in-bkt", "bucketRegion": input_region, "bucketAccountId": "",
                        "key": "xasset1/part.stp"}] if with_input else [],
        "inputMetadataS3Location": "s3://run/meta.json",
        "outputs": {"bucket": "out-bkt", "bucketRegion": output_region, "files": "p/cad/J/output/E1/files/",
                    "previews": "p/cad/J/output/E1/previews/", "metadata": "p/cad/J/output/E1/metadata/",
                    "results": "p/cad/J/output/E1/results/"},
        "auxBucket": "aux-bkt",
        "auxTempPrefix": "pipelines/cad/E1/",
        "auxPreviewPipelineSuffix": "",
        "systemConfig": {},
    }


def _manifest_s3(manifest):
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
    return s3


# ---------------------------------------------------------------------------------------------------
# vamsExecute: the real vendored manifestHelper resolves the Regions; the handler forwards both
# ---------------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestVamsExecuteForwardsBucketRegions:
    def _run(self, manifest):
        mod = handlers._load("vamsExecuteCadStepAgentPipeline.py")
        invoke = MagicMock(return_value={"StatusCode": 200})
        send_failure = MagicMock()
        body = {"TaskToken": handlers._TOKEN, "inputManifestS3Location": "s3://run/manifest.json"}
        with patch.object(mod, "s3_client", _manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke), \
                patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 200
        send_failure.assert_not_called()
        return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

    def test_the_payload_carries_the_manifest_regions(self):
        payload = self._run(_manifest())
        assert payload["inputBucketRegion"] == INPUT_REGION
        assert payload["outputBucketRegion"] == OUTPUT_REGION
        assert payload["inputS3AssetFilePath"] == "s3://in-bkt/xasset1/part.stp"
        assert payload["outputS3AssetResultsPath"] == "s3://out-bkt/p/cad/J/output/E1/results/"

    def test_a_cross_region_input_is_not_gated(self):
        # A private-subnet pipeline: an input in a Region with no cross-Region S3 endpoint still runs.
        with patch.dict(os.environ, {"CROSS_REGION_S3_ENDPOINT_REGIONS": ""}):
            payload = self._run(_manifest(input_region="ap-southeast-2"))
        assert payload["inputBucketRegion"] == "ap-southeast-2"

    def test_a_generate_run_forwards_the_output_region_only(self):
        payload = self._run(_manifest(with_input=False))
        assert (payload["inputS3AssetFilePath"], payload["inputBucketRegion"]) == ("", "")
        assert payload["outputBucketRegion"] == OUTPUT_REGION

    def test_a_manifest_written_without_regions_sends_empty_regions(self):
        manifest = _manifest()
        manifest["inputFiles"][0].pop("bucketRegion")
        manifest["outputs"].pop("bucketRegion")
        payload = self._run(manifest)
        assert (payload["inputBucketRegion"], payload["outputBucketRegion"]) == ("", "")


# ---------------------------------------------------------------------------------------------------
# openPipeline: the state-machine input carries both Regions
# ---------------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestOpenPipelineForwardsBucketRegions:
    def _sfn_input(self, event):
        mod = handlers._load("openPipeline.py")
        start = MagicMock(return_value={"executionArn": "arn:aws:states:us-east-1:1:execution:cad:x",
                                        "startDate": datetime.datetime(2026, 1, 1)})
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(start.call_args.kwargs["input"])

    def test_the_state_machine_input_carries_the_regions(self):
        event = dict(handlers._open_event("s3://in-bkt/xasset1/part.stp"),
                     inputBucketRegion=INPUT_REGION, outputBucketRegion=OUTPUT_REGION)
        sfn_input = self._sfn_input(event)
        assert sfn_input["inputBucketRegion"] == INPUT_REGION
        assert sfn_input["outputBucketRegion"] == OUTPUT_REGION

    def test_an_event_from_an_older_lambda_forwards_empty_regions(self):
        sfn_input = self._sfn_input(handlers._open_event("s3://in-bkt/xasset1/part.stp"))
        assert (sfn_input["inputBucketRegion"], sfn_input["outputBucketRegion"]) == ("", "")


# ---------------------------------------------------------------------------------------------------
# constructPipeline: the definition maps each asset bucket outside the deployment Region
# ---------------------------------------------------------------------------------------------------
def _event(with_input=True, **overrides):
    event = handlers._construct_event(with_input=with_input)
    event.update(outputS3AssetFilesPath="s3://out-bkt/xasset1/",
                 outputS3AssetMetadataPath="s3://out-bkt/xasset1/metadata/",
                 outputS3AssetResultsPath="s3://out-bkt/p/cad/J/output/E1/results/")
    if with_input:
        event["inputS3AssetFilePath"] = "s3://in-bkt/xasset1/sub/dir/part.stp"
    event.update(overrides)
    return event


@pytest.mark.unit
class TestConstructPipelineMapsBucketsToRegions:
    def _definition(self, event, mode="modify"):
        mod = handlers._load("constructPipeline.py")
        resp, send_failure, exc = handlers.TestConstructPipeline()._run(mod, event, handlers._config(mode))
        assert exc is None and send_failure.call_count == 0
        return json.loads(resp["definition"][0])

    def test_each_asset_bucket_maps_to_its_region(self):
        definition = self._definition(_event(inputBucketRegion=INPUT_REGION, outputBucketRegion=OUTPUT_REGION))
        assert definition["bucketRegions"] == {"in-bkt": INPUT_REGION, "out-bkt": OUTPUT_REGION}
        # The metadata and results prefixes are in the output bucket, so the container finds them in the map.
        assert definition["outputMetadata"]["bucketName"] == "out-bkt"
        assert definition["outputResults"]["bucketName"] == "out-bkt"

    def test_deployment_region_buckets_and_the_auxiliary_bucket_are_left_out(self):
        definition = self._definition(_event(inputBucketRegion="", outputBucketRegion=OUTPUT_REGION))
        assert definition["bucketRegions"] == {"out-bkt": OUTPUT_REGION}
        assert "aux" not in definition["bucketRegions"]
        assert self._definition(_event())["bucketRegions"] == {}

    def test_a_generate_run_maps_the_output_bucket_only(self):
        definition = self._definition(
            _event(with_input=False, inputBucketRegion=INPUT_REGION, outputBucketRegion=OUTPUT_REGION),
            mode="generate")
        assert definition["inputFile"] is None
        assert definition["bucketRegions"] == {"out-bkt": OUTPUT_REGION}

    def test_one_bucket_for_input_and_output_maps_once(self):
        mod = handlers._load("constructPipeline.py")
        event = _event(outputS3AssetFilesPath="s3://in-bkt/xasset1/",
                       inputBucketRegion=INPUT_REGION, outputBucketRegion=INPUT_REGION)
        assert mod.bucket_regions(event) == {"in-bkt": INPUT_REGION}


# ---------------------------------------------------------------------------------------------------
# Both runtimes: the run task forwards the definition, bucketRegions included, untouched
# ---------------------------------------------------------------------------------------------------
_DEFINITION = dict(handlers._FORWARDED_DEFINITION, bucketRegions={"in-bkt": INPUT_REGION, "out-bkt": OUTPUT_REGION})


@pytest.mark.unit
class TestBothRuntimesForwardBucketRegions:
    def test_the_batch_job_environment_carries_the_map(self):
        mod = handlers._load("executeBatchJob.py")
        submit = MagicMock(return_value={"jobId": "job-1"})
        with patch.object(mod.batch, "submit_job", submit), patch.object(mod.events_client, "put_events", MagicMock()):
            mod.lambda_handler({"jobName": "CadStepAgent_x", "definition": [json.dumps(_DEFINITION)],
                                "taskToken": handlers._TOKEN}, MagicMock())
        env = {e["name"]: e["value"] for e in submit.call_args.kwargs["containerOverrides"]["environment"]}
        assert json.loads(env["CAD_AGENT_DEFINITION"]) == _DEFINITION
        assert json.loads(env["CAD_AGENT_DEFINITION"])["bucketRegions"] == {"in-bkt": INPUT_REGION, "out-bkt": OUTPUT_REGION}

    def test_the_agentcore_payload_carries_the_map(self):
        mod = handlers._load("invokeAgentRuntime.py")
        stream = MagicMock()
        stream.read.return_value = json.dumps({"accepted": True}).encode("utf-8")
        invoke = MagicMock(return_value={"response": stream})
        with patch.object(mod.agentcore, "invoke_agent_runtime", invoke), \
                patch.object(mod.sfn, "send_task_failure", MagicMock()):
            mod.lambda_handler({"jobName": "CadStepAgent_x", "definition": [json.dumps(_DEFINITION)],
                                "taskToken": handlers._TOKEN}, MagicMock())
        payload = json.loads(invoke.call_args.kwargs["payload"])
        assert payload["definition"] == _DEFINITION
        assert payload["definition"]["bucketRegions"] == {"in-bkt": INPUT_REGION, "out-bkt": OUTPUT_REGION}
