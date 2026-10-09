#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Asset buckets in other Regions, as seen from the pipelines.

The workflow manifest names the Region of every input file's bucket (`bucketRegion`) and of the
output bucket (`outputs.bucketRegion`); an empty value is the deployment Region. `manifestHelper`
exposes both on the resolved inputs and builds an S3 client per Region, so a pipeline that reads an
input in another Region signs the request for that Region.

The four Batch pipelines whose compute runs in the VPC's isolated subnets (Potree viewer, 3D
thumbnail, GenAI metadata labeling, coordinate transform) reach Amazon S3 in the deployment Region
only, so their `vamsExecute` handlers reject a cross-Region input before submitting the job, from
inside the `try` that reports `SendTaskFailure`. The seven private-subnet pipelines and the
Lambda-container pipelines are not gated: their compute has a route to Amazon S3 in the bucket
Region.

The fourteen `manifestHelper.py` copies are one file; byte identity is asserted first so an edit
applied to one copy and not propagated fails here.
"""

import hashlib
import importlib.util
import os
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MANIFEST_HELPER_COPIES = (
    "backendPipelines/3dRecon/splatToolbox/lambda/manifestHelper.py",
    "backendPipelines/conversion/coordinateTransform/lambda/manifestHelper.py",
    "backendPipelines/genAi/metadata3dLabeling/lambda/manifestHelper.py",
    "backendPipelines/genAi/nvidia/cosmos/3/lambda/manifestHelper.py",
    "backendPipelines/genAi/nvidia/cosmos/predict/lambda/manifestHelper.py",
    "backendPipelines/genAi/nvidia/cosmos/reason/lambda/manifestHelper.py",
    "backendPipelines/genAi/nvidia/cosmos/transfer/lambda/manifestHelper.py",
    "backendPipelines/genAi/nvidia/gr00t/lambda/manifestHelper.py",
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
)

PRIVATE_SUBNET_OR_LAMBDA_VAMS_EXECUTE = (
    "backendPipelines/3dRecon/splatToolbox/lambda/vamsExecuteSplatToolboxPipeline.py",
    "backendPipelines/genAi/nvidia/cosmos/3/lambda/vamsExecuteCosmos3Pipeline.py",
    "backendPipelines/genAi/nvidia/cosmos/predict/lambda/vamsExecuteCosmosText2WorldPipeline.py",
    "backendPipelines/genAi/nvidia/cosmos/predict/lambda/vamsExecuteCosmosVideo2WorldPipeline.py",
    "backendPipelines/genAi/nvidia/cosmos/reason/lambda/vamsExecuteCosmosReasonPipeline.py",
    "backendPipelines/genAi/nvidia/cosmos/transfer/lambda/vamsExecuteCosmosTransferPipeline.py",
    "backendPipelines/genAi/nvidia/gr00t/lambda/vamsExecuteGr00tFinetunePipeline.py",
    "backendPipelines/multi/modelOps/lambda/vamsExecuteModelOps.py",
    "backendPipelines/multi/rapidPipeline/lambda/vamsExecuteRapidPipeline.py",
    "backendPipelines/multi/rapidPipelineEKS/lambda/vamsExecuteRapidPipelineEKS.py",
    "backendPipelines/simulation/isaacLabTraining/lambda/vamsExecuteIsaacLabPipeline.py",
)


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
        digests = {relative: hashlib.md5(_read(relative)).hexdigest() for relative in MANIFEST_HELPER_COPIES}
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

    def test_a_cross_region_input_raises_naming_both_regions(self, helper):
        with pytest.raises(Exception) as excinfo:
            helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("us-east-1")))
        message = str(excinfo.value)
        assert "us-east-1" in message and "us-west-2" in message
        assert "isolated subnets" in message
        assert "Cross-Region inputs are not supported for this pipeline" in message

    def test_an_explicit_region_overrides_the_environment(self, helper):
        helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("us-east-1")), region="us-east-1")
        with pytest.raises(Exception):
            helper.enforce_inputs_in_region(helper.resolve_inputs({}, _manifest("us-west-2")), region="us-east-1")


class TestIsolatedSubnetHandlersGateCrossRegionInputs:
    @pytest.mark.parametrize("relative", ISOLATED_SUBNET_VAMS_EXECUTE)
    def test_the_gate_runs_after_the_single_file_check_inside_the_reporting_try(self, relative):
        source = _read(relative).decode("utf-8")
        single = source.index("manifestHelper.enforce_single_input_file(resolved)")
        gate = source.index("manifestHelper.enforce_inputs_in_region(resolved)")
        assert gate > single, f"{relative}: the Region gate must follow the single-file check"
        # Both sit in the handler's try block, whose except reports SendTaskFailure.
        between = source[single:gate]
        assert "except" not in between, f"{relative}: the Region gate is outside the reporting try"
        assert "abort_external_workflow" in source

    @pytest.mark.parametrize("relative", PRIVATE_SUBNET_OR_LAMBDA_VAMS_EXECUTE)
    def test_private_subnet_pipelines_are_not_gated(self, relative):
        # Positive control for the list above: the gate is a property of isolated-subnet compute,
        # not of every pipeline.
        assert "enforce_inputs_in_region" not in _read(relative).decode("utf-8"), relative
