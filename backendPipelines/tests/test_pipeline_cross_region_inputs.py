#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Asset buckets in other Regions, as seen from the pipelines.

The workflow manifest names the Region of every input file's bucket (`bucketRegion`) and of the
output bucket (`outputs.bucketRegion`); an empty value is the deployment Region. `manifestHelper`
exposes both on the resolved inputs and builds an S3 client per Region, so a pipeline that reads an
input in another Region signs the request for that Region.

The five Batch pipelines whose compute runs in the VPC's isolated subnets (Potree viewer, 3D
thumbnail, GenAI metadata labeling, coordinate transform, Video SOP/BOM) reach Amazon S3 in the deployment Region
through the S3 gateway endpoint and in each cross-Region asset bucket's Region through the
cross-Region S3 interface endpoint the VPC builder creates there (`addCrossRegionS3Endpoints`). The
CDK names those Regions in `CROSS_REGION_S3_ENDPOINT_REGIONS` on their `vamsExecute` Lambdas, and
the handlers reject an input in any other Region before submitting the job, from inside the `try`
that reports `SendTaskFailure`. The eight private-subnet pipelines, the GenAI CAD STEP agent (its
container runs on Amazon Bedrock AgentCore outside the VPC, or on AWS Batch in private subnets) and
the Lambda-container pipelines are not gated: their compute has a route to Amazon S3 in the bucket
Region.

The seventeen `manifestHelper.py` copies are one file; byte identity is asserted first so an edit
applied to one copy and not propagated fails here.
"""

import hashlib
import importlib.util
import os
import re
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MANIFEST_HELPER_COPIES = (
    "backendPipelines/3dRecon/splatToolbox/lambda/manifestHelper.py",
    "backendPipelines/conversion/coordinateTransform/lambda/manifestHelper.py",
    "backendPipelines/genAi/cadStepAgent/lambda/manifestHelper.py",
    "backendPipelines/genAi/imageTo3d/lambda/manifestHelper.py",
    "backendPipelines/genAi/metadata3dLabeling/lambda/manifestHelper.py",
    "backendPipelines/genAi/nvidia/cosmos/3/lambda/manifestHelper.py",
    "backendPipelines/genAi/nvidia/cosmos/predict/lambda/manifestHelper.py",
    "backendPipelines/genAi/nvidia/cosmos/reason/lambda/manifestHelper.py",
    "backendPipelines/genAi/nvidia/cosmos/transfer/lambda/manifestHelper.py",
    "backendPipelines/genAi/nvidia/gr00t/lambda/manifestHelper.py",
    "backendPipelines/genAi/videoSopBom/lambda/manifestHelper.py",
    "backendPipelines/multi/modelOps/lambda/manifestHelper.py",
    "backendPipelines/multi/rapidPipeline/lambda/manifestHelper.py",
    "backendPipelines/multi/rapidPipelineEKS/lambda/manifestHelper.py",
    "backendPipelines/preview/3dThumbnail/lambda/manifestHelper.py",
    "backendPipelines/preview/pcPotreeViewer/lambda/manifestHelper.py",
    "backendPipelines/simulation/isaacLabTraining/lambda/manifestHelper.py",
)

ISOLATED_SUBNET_VAMS_EXECUTE = (
    "backendPipelines/preview/pcPotreeViewer/lambda/vamsExecutePreviewPcPotreeViewerPipeline.py",
    "backendPipelines/preview/3dThumbnail/lambda/vamsExecutePreview3dThumbnailPipeline.py",
    "backendPipelines/genAi/metadata3dLabeling/lambda/vamsExecuteGenAiMetadata3dLabelingPipeline.py",
    "backendPipelines/conversion/coordinateTransform/lambda/vamsExecuteCoordinateTransformPipeline.py",
    "backendPipelines/genAi/videoSopBom/lambda/vamsExecuteVideoSopBomPipeline.py",
)

# Private-subnet pipelines whose VAMS-owned container addresses each asset bucket in its own Region: the
# handler forwards both manifest Regions to openPipeline, and the chain carries them to the container.
REGION_CONSUMING_VAMS_EXECUTE = (
    "backendPipelines/3dRecon/splatToolbox/lambda/vamsExecuteSplatToolboxPipeline.py",
    # The CAD STEP agent's container runs on AgentCore (outside the VPC) or on AWS Batch in private subnets.
    "backendPipelines/genAi/cadStepAgent/lambda/vamsExecuteCadStepAgentPipeline.py",
    "backendPipelines/genAi/imageTo3d/lambda/vamsExecuteImageTo3dPipeline.py",
    "backendPipelines/genAi/nvidia/cosmos/3/lambda/vamsExecuteCosmos3Pipeline.py",
    "backendPipelines/genAi/nvidia/cosmos/predict/lambda/vamsExecuteCosmosText2WorldPipeline.py",
    "backendPipelines/genAi/nvidia/cosmos/predict/lambda/vamsExecuteCosmosVideo2WorldPipeline.py",
    "backendPipelines/genAi/nvidia/cosmos/reason/lambda/vamsExecuteCosmosReasonPipeline.py",
    "backendPipelines/genAi/nvidia/cosmos/transfer/lambda/vamsExecuteCosmosTransferPipeline.py",
    "backendPipelines/genAi/nvidia/gr00t/lambda/vamsExecuteGr00tFinetunePipeline.py",
    "backendPipelines/simulation/isaacLabTraining/lambda/vamsExecuteIsaacLabPipeline.py",
)

# Private-subnet pipelines that hand s3:// locations to a third-party container image whose SDK VAMS
# does not control: same-Region only, rejected at vamsExecute.
SAME_REGION_ONLY_VAMS_EXECUTE = (
    "backendPipelines/multi/modelOps/lambda/vamsExecuteModelOps.py",
    "backendPipelines/multi/rapidPipeline/lambda/vamsExecuteRapidPipeline.py",
    "backendPipelines/multi/rapidPipelineEKS/lambda/vamsExecuteRapidPipelineEKS.py",
)

# The Lambda-container pipelines have no vamsExecute handler: their entry point reads the manifest
# itself and builds a client for the Region it names.


def _read(relative):
    with open(os.path.join(_REPO_ROOT, *relative.split("/")), "rb") as f:
        return f.read()


def _load_manifest_helper():
    path = os.path.join(_REPO_ROOT, *MANIFEST_HELPER_COPIES[0].split("/"))
    spec = importlib.util.spec_from_file_location("manifestHelper_cross_region_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    return _load_manifest_helper()


def _manifest(input_region="", output_region=""):
    return {
        "inputFiles": [{"relativePath": "/scan.e57", "bucket": "remote-bkt", "bucketRegion": input_region,
                        "bucketAccountId": "", "key": "xid/scan.e57", "assetId": "xid", "databaseId": "db",
                        "assetRootS3Key": "xid/", "auxPreviewPrefix": "db/xid/scan.e57/preview"}],
        "outputs": {"bucket": "run-bkt", "bucketRegion": output_region, "files": "o/files/",
                    "previews": "o/previews/", "metadata": "o/metadata/", "results": "o/results/"},
        "auxBucket": "aux-bkt", "auxTempPrefix": "pipelines/p/E1/", "auxPreviewPipelineSuffix": "",
        "inputMetadataS3Location": "s3://run-bkt/metadata.json", "systemConfig": {},
    }


class TestManifestHelperCopiesAreOneFile:
    def test_every_copy_is_listed(self):
        found = sorted(
            os.path.relpath(os.path.join(root, name), _REPO_ROOT).replace(os.sep, "/")
            for root, _, names in os.walk(os.path.join(_REPO_ROOT, "backendPipelines"))
            for name in names if name == "manifestHelper.py")
        assert found == sorted(MANIFEST_HELPER_COPIES)

    def test_every_copy_is_byte_identical(self):
        digests = {
            relative: hashlib.md5(_read(relative), usedforsecurity=False).hexdigest()
            for relative in MANIFEST_HELPER_COPIES
        }
        assert len(set(digests.values())) == 1, digests


class TestResolvedInputsCarryTheRegions:
    def test_regions_from_the_manifest(self, helper):
        resolved = helper.resolve_inputs({}, _manifest("us-east-1", "us-west-2"))
        assert resolved["inputBucketRegion"] == "us-east-1"
        assert resolved["outputBucketRegion"] == "us-west-2"
        assert resolved["inputFiles"][0]["bucketRegion"] == "us-east-1"

    def test_empty_regions_for_a_manifest_written_without_them_and_for_a_legacy_payload(self, helper):
        manifest = _manifest()
        del manifest["inputFiles"][0]["bucketRegion"]
        del manifest["outputs"]["bucketRegion"]
        resolved = helper.resolve_inputs({}, manifest)
        assert resolved["inputBucketRegion"] == ""
        assert resolved["outputBucketRegion"] == ""
        legacy = helper.resolve_inputs({"inputS3AssetFilePath": "s3://b/k"}, None)
        assert legacy["inputBucketRegion"] == ""
        assert legacy["outputBucketRegion"] == ""

    def test_input_file_region_reads_one_entry(self, helper):
        assert helper.input_file_region({"bucketRegion": "eu-west-1"}) == "eu-west-1"
        assert helper.input_file_region({}) == ""
        assert helper.input_file_region(None) == ""


class TestRegionalClients:
    def test_one_client_per_region_with_adaptive_retries_and_no_endpoint_url(self, helper):
        a = helper.s3_client_for_region("us-east-1")
        assert a is helper.s3_client_for_region("us-east-1")
        assert a.meta.region_name == "us-east-1"
        assert a.meta.config.retries.get("mode") == "adaptive"
        assert a.meta.config.retries.get("total_max_attempts") == 6
        assert a.meta.endpoint_url == "https://s3.us-east-1.amazonaws.com"

    def test_empty_region_is_the_deployment_region(self, helper):
        assert helper.s3_client_for_region("").meta.region_name == "us-west-2"
        assert helper.s3_client_for_region().meta.region_name == "us-west-2"


class TestEnforceInputsInRegion:
    def test_same_region_and_unplaced_inputs_pass(self, helper):
        helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("us-west-2")))
        helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("")))
        helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("")), region="us-west-2")
        helper.enforce_inputs_in_region({"inputFiles": []})
        helper.enforce_inputs_in_region(None)

    def test_a_cross_region_input_raises_naming_both_regions_and_both_remedies(self, helper):
        with pytest.raises(Exception) as excinfo:
            helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("us-east-1")))
        message = str(excinfo.value)
        assert "us-east-1" in message and "us-west-2" in message
        assert "isolated subnets" in message
        assert "Cross-Region inputs are not supported for this pipeline" in message
        # The operator has two ways through: the cross-Region endpoint flag, or private subnets.
        assert "app.useGlobalVpc.addCrossRegionS3Endpoints" in message
        assert "private subnets" in message

    def test_an_explicit_region_overrides_the_environment(self, helper):
        helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("us-east-1")), region="us-east-1")
        with pytest.raises(Exception):
            helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("us-west-2")), region="us-east-1")


class TestEndpointServedRegionsPassTheGate:
    """The VPC builder creates a cross-Region S3 interface endpoint, in the isolated subnets, for every
    Region that holds an external asset bucket (addCrossRegionS3Endpoints). The CDK names those
    Regions in CROSS_REGION_S3_ENDPOINT_REGIONS on the isolated-subnet pipelines' vamsExecute Lambdas,
    and an input in one of them passes the gate; only a Region with no endpoint is rejected."""

    def test_the_variable_is_parsed_as_a_trimmed_comma_separated_list(self, helper, monkeypatch):
        monkeypatch.delenv("CROSS_REGION_S3_ENDPOINT_REGIONS", raising=False)
        assert helper.cross_region_s3_endpoint_regions() == ()
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", "")
        assert helper.cross_region_s3_endpoint_regions() == ()
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", "us-east-1, eu-west-1,")
        assert helper.cross_region_s3_endpoint_regions() == ("us-east-1", "eu-west-1")

    def test_an_input_in_an_endpoint_served_region_passes(self, helper, monkeypatch):
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", "us-east-1,eu-west-1")
        helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("us-east-1")))
        helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("eu-west-1")))
        helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("us-west-2")))

    def test_an_input_in_a_region_with_no_endpoint_still_raises_naming_the_served_regions(self, helper, monkeypatch):
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", "us-east-1")
        with pytest.raises(Exception) as excinfo:
            helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("eu-west-1")))
        message = str(excinfo.value)
        assert "eu-west-1" in message and "us-west-2" in message
        assert "cross-Region S3 endpoints, in us-east-1" in message
        assert "isolated subnets" in message

    def test_an_empty_variable_preserves_the_deployment_region_only_gate(self, helper, monkeypatch):
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", "")
        with pytest.raises(Exception) as excinfo:
            helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("us-east-1")))
        assert "cross-Region S3 endpoints, in" not in str(excinfo.value)

    def test_an_explicit_reachable_list_overrides_the_environment(self, helper, monkeypatch):
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", "us-east-1")
        with pytest.raises(Exception):
            helper.enforce_inputs_in_region(
                helper.resolve_inputs({}, _manifest("us-east-1")), reachable_regions=())
        helper.enforce_inputs_in_region(
            helper.resolve_inputs({}, _manifest("eu-west-1")), reachable_regions=("eu-west-1",))

    def test_remote_input_regions_lists_what_the_gate_would_reject(self, helper):
        resolved = helper.resolve_inputs({}, _manifest("us-east-1"))
        resolved["inputFiles"].append(dict(resolved["inputFiles"][0], bucketRegion="eu-west-1"))
        resolved["inputFiles"].append(dict(resolved["inputFiles"][0], bucketRegion=""))
        assert helper.remote_input_regions(resolved) == ["eu-west-1", "us-east-1"]
        assert helper.remote_input_regions(resolved, reachable_regions=("us-east-1",)) == ["eu-west-1"]
        assert helper.remote_input_regions(resolved, region="us-east-1", reachable_regions=("eu-west-1",)) == []
        assert helper.remote_input_regions(None) == []


class TestEnforceSameRegionIo:
    """The gate for a pipeline that hands s3:// locations to a third-party container image."""

    def test_same_region_and_unplaced_buckets_pass(self, helper):
        helper.enforce_same_region_io(helper.resolve_inputs({}, _manifest("us-west-2", "us-west-2")))
        helper.enforce_same_region_io(helper.resolve_inputs({}, _manifest("", "")))
        helper.enforce_same_region_io(helper.resolve_inputs({}, _manifest("", "")), region="us-west-2")
        helper.enforce_same_region_io({"inputFiles": []})
        helper.enforce_same_region_io(None)

    def test_a_cross_region_input_raises_naming_the_image_and_both_regions(self, helper):
        with pytest.raises(Exception) as excinfo:
            helper.enforce_same_region_io(helper.resolve_inputs({}, _manifest("us-east-1")))
        message = str(excinfo.value)
        assert "third-party container image" in message
        assert "us-east-1" in message and "us-west-2" in message
        assert "Cross-Region buckets are not supported for this pipeline" in message

    def test_a_cross_region_output_bucket_raises_too(self, helper):
        with pytest.raises(Exception) as excinfo:
            helper.enforce_same_region_io(helper.resolve_inputs({}, _manifest("", "eu-west-1")))
        assert "the output bucket is in eu-west-1" in str(excinfo.value)
        with pytest.raises(Exception) as excinfo:
            helper.enforce_same_region_io(helper.resolve_inputs({}, _manifest("us-east-1", "eu-west-1")))
        message = str(excinfo.value)
        assert "us-east-1" in message and "the output bucket is in eu-west-1" in message

    def test_the_endpoint_variable_does_not_widen_this_gate(self, helper, monkeypatch):
        # A cross-Region S3 endpoint lets VAMS code reach the bucket; it says nothing about the
        # vendor image's SDK, so the same-Region-only rule holds regardless.
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", "us-east-1")
        with pytest.raises(Exception):
            helper.enforce_same_region_io(helper.resolve_inputs({}, _manifest("us-east-1")))


def _forwards_both_regions(source):
    """The handler reads both resolved Regions (either quote style) to pass them on."""
    return all(re.search(r"resolved\[['\"]%sBucketRegion['\"]\]" % kind, source) for kind in ("input", "output"))


def _vams_execute_handlers():
    return sorted(
        os.path.relpath(os.path.join(root, name), _REPO_ROOT).replace(os.sep, "/")
        for root, _, names in os.walk(os.path.join(_REPO_ROOT, "backendPipelines"))
        for name in names
        if name.startswith("vamsExecute") and name.endswith(".py") and os.path.basename(root) == "lambda")


class TestEveryVamsExecuteHandlerConsumesOrGatesTheRegion:
    """Repo-wide rule: a pipeline either carries the manifest's bucket Regions to compute that signs for
    them, or rejects a cross-Region bucket at launch. A handler in neither class would start a job that
    addresses a cross-Region bucket as if it were in the deployment Region."""

    def test_every_handler_is_in_exactly_one_class(self):
        listed = ISOLATED_SUBNET_VAMS_EXECUTE + REGION_CONSUMING_VAMS_EXECUTE + SAME_REGION_ONLY_VAMS_EXECUTE
        assert len(set(listed)) == len(listed), "a handler is listed twice"
        assert _vams_execute_handlers() == sorted(listed)

    @pytest.mark.parametrize("relative", ISOLATED_SUBNET_VAMS_EXECUTE)
    def test_the_isolated_subnet_gate_runs_after_the_single_file_check_inside_the_reporting_try(self, relative):
        source = _read(relative).decode("utf-8")
        # A single-file pipeline validates its input count with the shared helper; a multi-file one
        # (Video SOP/BOM) with its own gate. Either way the Region gate follows the resolved manifest
        # and sits in the same try block, whose except reports SendTaskFailure.
        anchor = "manifestHelper.enforce_single_input_file(resolved)"
        if anchor not in source:
            anchor = "manifestHelper.resolve_pipeline_inputs(data, s3_client)"
        single = source.index(anchor)
        gate = source.index("manifestHelper.enforce_inputs_in_region(resolved)")
        assert gate > single, f"{relative}: the Region gate must follow the input check"
        between = source[single:gate]
        assert "except" not in between, f"{relative}: the Region gate is outside the reporting try"
        assert "abort_external_workflow" in source
        # The isolated-subnet pipelines also carry the Regions onward: their Batch containers sign for them.
        assert _forwards_both_regions(source), relative
        assert "enforce_same_region_io" not in source

    @pytest.mark.parametrize("relative", REGION_CONSUMING_VAMS_EXECUTE)
    def test_a_region_consuming_handler_forwards_both_regions_and_is_not_gated(self, relative):
        source = _read(relative).decode("utf-8")
        assert _forwards_both_regions(source), relative
        assert "enforce_inputs_in_region" not in source, relative
        assert "enforce_same_region_io" not in source, relative

    @pytest.mark.parametrize("relative", SAME_REGION_ONLY_VAMS_EXECUTE)
    def test_a_same_region_only_handler_gates_after_the_single_file_check_inside_the_reporting_try(self, relative):
        source = _read(relative).decode("utf-8")
        single = source.index("manifestHelper.enforce_single_input_file(resolved)")
        gate = source.index("manifestHelper.enforce_same_region_io(resolved)")
        assert gate > single, relative
        assert "except" not in source[single:gate], relative
        assert "send_task_failure" in source, relative
        # Not the isolated-subnet gate: the compute reaches the Region, the vendor image is the limit.
        assert "enforce_inputs_in_region" not in source, relative
