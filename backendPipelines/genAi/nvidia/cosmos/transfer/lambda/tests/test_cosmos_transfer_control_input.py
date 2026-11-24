#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Cosmos Transfer: the control input is checked where the run starts, and its asset root travels.

``COSMOS_TRANSFER_CONTROL_PATH`` (or the template's ``CONTROL_PATH``) names a file of the run's asset
relative to its root. ``vamsExecute`` accepts only such a path, forwards it normalized (a leading ``/``
names the asset root), and fails the run before ``openPipeline`` is invoked for any other value, so no
GPU job is scheduled for it. The asset root the container joins it to travels from the manifest entry's
``assetRootS3Key`` as ``inputAssetRootS3Key``. Reuses the fixtures of ``test_manifest_refactor`` (same
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


def _handle(control_path, manifest=None, body=None):
    base = refactor.TestVamsExecute()
    mod = base._load()
    s3 = base._s3_with(manifest or base._manifest(), base._metadata_envelope(control_path=control_path),
                       {"DISABLE_GUARDRAILS": "true"})
    invoke = MagicMock(return_value={"StatusCode": 200})
    failure = MagicMock()
    with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke), \
            patch.object(mod.sfn_client, "send_task_failure", failure):
        resp = mod.lambda_handler({"body": json.dumps(body or base._body())}, MagicMock())
    return resp, invoke, failure


def _payload(invoke):
    return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))


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
        assert _payload(invoke)["controlPath"] == expected
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
    @pytest.mark.parametrize("root", ["vams/xidM/", "projects/site-a/"], ids=["base-prefix", "existing-folder"])
    def test_the_payload_carries_the_first_entrys_root(self, root):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0].update(key=f"{root}clips/source.mp4", assetRootS3Key=root)
        resp, invoke, _ = _handle("signals/control.mp4", manifest)
        assert resp["statusCode"] == 200, resp
        assert _payload(invoke)["inputAssetRootS3Key"] == root

    def test_without_a_manifest_the_payload_names_no_root(self):
        base = refactor.TestVamsExecute()
        body = base._body()
        body.pop("inputManifestS3Location")
        mod = base._load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("no manifest")
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 200, resp
        assert _payload(invoke)["inputAssetRootS3Key"] == ""

    def test_open_pipeline_passes_it_to_the_state_machine(self):
        opener = refactor.TestOpenPipeline()
        mod = opener._load()
        start = opener._mock_start()
        event = dict(opener._event(), inputAssetRootS3Key="projects/site-a/")
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        assert json.loads(start.call_args.kwargs["input"])["inputAssetRootS3Key"] == "projects/site-a/"

    def test_construct_pipeline_puts_it_on_the_definition(self):
        mod = refactor.TestConstructPipeline()._load()
        out = mod.lambda_handler({"controlPath": "signals/control.mp4", "inputAssetRootS3Key": "projects/site-a/",
                                  "inputS3AssetFilePath": "s3://abkt/projects/site-a/clips/source.mp4",
                                  "assetId": "xidM"}, MagicMock())
        definition = json.loads(out["definition"][2])
        assert (definition["controlPath"], definition["inputAssetRootS3Key"]) == (
            "signals/control.mp4", "projects/site-a/")
