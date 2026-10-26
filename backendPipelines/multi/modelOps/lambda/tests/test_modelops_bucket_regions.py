#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""multi/modelOps: an asset bucket in another Region is rejected before the job is submitted.

This pipeline hands ``s3://`` locations to a third-party container image, whose own SDK reads the
input and writes the output; VAMS neither controls nor verifies that image's cross-Region behaviour,
so the pipeline is same-Region only. ``vamsExecute`` calls ``manifestHelper.enforce_same_region_io``
right after the single-file check, inside the ``try`` whose ``except`` reports ``SendTaskFailure``,
so a cross-Region input or output fails the workflow task fast with a cause naming the pipeline as
same-Region only instead of after the compute is provisioned. Every VAMS-owned S3 call in this
pipeline addresses the default run bucket or the auxiliary bucket, both in the deployment Region.
Reuses the fixtures of ``test_manifest_refactor`` (same directory; the suite runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402

# This suite runs with AWS_REGION=us-east-1.
DEPLOYMENT_REGION = "us-east-1"
REMOTE_REGION = "eu-west-1"


def _manifest_s3(manifest):
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
    return s3


@pytest.mark.unit
class TestVamsExecuteIsSameRegionOnly:
    def _handle(self, manifest):
        base = refactor.TestVamsExecute()
        mod = base._load()
        invoke = MagicMock(return_value={"StatusCode": 200, "Payload": MagicMock(read=lambda: b"")})
        send_task_failure = MagicMock()
        with patch.object(mod, "s3_client", _manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke), \
                patch.object(mod.sfn_client, "send_task_failure", send_task_failure):
            resp = mod.lambda_handler({"body": json.dumps(base._body())}, MagicMock())
        return resp, invoke, send_task_failure

    def test_same_region_and_unplaced_buckets_pass(self):
        for input_region, output_region in ((DEPLOYMENT_REGION, DEPLOYMENT_REGION), ("", "")):
            manifest = refactor.TestVamsExecute()._manifest()
            manifest["inputFiles"][0]["bucketRegion"] = input_region
            manifest["outputs"]["bucketRegion"] = output_region
            resp, invoke, send_task_failure = self._handle(manifest)
            assert resp["statusCode"] == 200, resp
            invoke.assert_called_once()
            send_task_failure.assert_not_called()

    def test_a_cross_region_input_fails_the_task_token_before_the_job_is_submitted(self):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["bucketRegion"] = REMOTE_REGION
        resp, invoke, send_task_failure = self._handle(manifest)
        assert resp["statusCode"] != 200
        invoke.assert_not_called()
        send_task_failure.assert_called_once()
        cause = send_task_failure.call_args.kwargs["cause"]
        assert "third-party container image" in cause
        assert REMOTE_REGION in cause and DEPLOYMENT_REGION in cause

    def test_a_cross_region_output_bucket_is_rejected_too(self):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["outputs"]["bucketRegion"] = REMOTE_REGION
        resp, invoke, send_task_failure = self._handle(manifest)
        assert resp["statusCode"] != 200
        invoke.assert_not_called()
        assert "output bucket is in " + REMOTE_REGION in send_task_failure.call_args.kwargs["cause"]

    def test_the_gate_follows_the_single_file_check_inside_the_reporting_try(self):
        with open(os.path.join(_LAMBDA_DIR, "vamsExecuteModelOps.py"), encoding="utf-8") as f:
            source = f.read()
        single = source.index("manifestHelper.enforce_single_input_file(resolved)")
        gate = source.index("manifestHelper.enforce_same_region_io(resolved)")
        assert gate > single and "except" not in source[single:gate]
        # The isolated-subnet gate is a different rule: this pipeline's compute reaches the Region, its
        # container image is what does not.
        assert "enforce_inputs_in_region" not in source
