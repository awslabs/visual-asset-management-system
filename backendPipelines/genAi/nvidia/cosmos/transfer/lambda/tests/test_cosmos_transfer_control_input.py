#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Cosmos Transfer: the control input is checked where the run starts, and its asset root travels.

``COSMOS_TRANSFER_CONTROL_PATH`` (or the template's ``CONTROL_PATH``) names a file of the run's asset
relative to its root. ``vamsExecute`` accepts only such a path, forwards it normalized (a leading ``/``
names the asset root), and fails the run before ``openPipeline`` is invoked for any other value, so no
GPU job is scheduled for it. The asset root the container joins it to travels from the manifest entry as
``inputAssetRootS3Key`` and ``inputAssetRootBucket``. Reuses the fixtures of ``test_manifest_refactor``
and ``test_cosmos_transfer_bucket_regions`` (same directory; the suite runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402
import test_cosmos_transfer_bucket_regions as regions  # noqa: E402


def _handle(control_path, manifest=None):
    base = refactor.TestVamsExecute()
    mod = base._load()
    s3 = regions._s3_router(manifest or base._manifest(), base._metadata_envelope(control_path=control_path),
                            {"DISABLE_GUARDRAILS": "true"})
    invoke = MagicMock(return_value={"StatusCode": 200})
    failure = MagicMock()
    with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke), \
            patch.object(mod.sfn_client, "send_task_failure", failure):
        resp = mod.lambda_handler({"body": json.dumps(base._body())}, MagicMock())
    return resp, invoke, failure


@pytest.mark.unit
class TestTheControlPathIsCheckedWhereTheRunStarts:
    @pytest.mark.parametrize("value,expected", [
        ("signals/control.mp4", "signals/control.mp4"),
        ("/signals/control.mp4", "signals/control.mp4"),
        ("  signals/control.mp4 ", "signals/control.mp4"),
        ("", ""),
    ])
    def test_a_path_in_the_asset_is_forwarded_normalized(self, value, expected):
        resp, invoke, failure = _handle(value)
        assert resp["statusCode"] == 200, resp
        assert json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))["controlPath"] == expected
        failure.assert_not_called()

    @pytest.mark.parametrize("value", [
        "../xidB/control.mp4",
        "signals/../../xidB/control.mp4",
        "signals//control.mp4",
        "s3://other-bkt/control.mp4",
        "signals\\control.mp4",
        "signals/\x07control.mp4",
        "s/" + "a" * 1100,
    ])
    def test_any_other_value_fails_the_run_before_it_is_opened(self, value):
        resp, invoke, failure = _handle(value)
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        failure.assert_called_once()
        assert "must name a file in the asset" in failure.call_args.kwargs["cause"]


@pytest.mark.unit
class TestTheAssetRootTravels:
    def test_the_payload_carries_the_first_entrys_root(self):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0].update(relativePath="/clips/source.mp4", assetRootS3Key="vams/xidM/",
                                         assetRootBucket="abkt")
        resp, invoke, _ = _handle("signals/control.mp4", manifest)
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert (payload["inputRelativePath"], payload["inputAssetRootS3Key"], payload["inputAssetRootBucket"]) == (
            "/clips/source.mp4", "vams/xidM/", "abkt")

    def test_construct_pipeline_puts_it_on_the_definition(self):
        construct = regions.TestConstructPipelineMapsBucketsToRegions()
        definition = construct._definition(construct._event(
            controlPath="signals/control.mp4", inputAssetRootS3Key="vams/xidM/", inputAssetRootBucket="abkt"))
        assert (definition["controlPath"], definition["inputAssetRootS3Key"], definition["inputAssetRootBucket"]) == (
            "signals/control.mp4", "vams/xidM/", "abkt")
