#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The asset root the Isaac Lab run resolves its relative paths against is the manifest entry's
``assetRootS3Key`` in the bucket ``assetRootBucket``.

From the second workflow step on, an input an earlier step rewrote is read from the run bucket, while
its asset root stays in the asset's own bucket. Pairing the entry's ``bucket`` with that root named a
folder that does not exist in the run bucket. A manifest written before ``assetRootBucket`` pairs the
root with the entry's ``bucket``, as before."""

import importlib
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:IsaacLab",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "",
    "STATE_MACHINE_LOG_GROUP_ARN": "",
}.items():
    os.environ.setdefault(k, v)

MANIFEST_KEY = "pipelines/workflowExecutionInputs/E1/pipeline2/manifest.json"
CONFIG_KEY = "pipelines/workflowExecutionInputs/E1/pipeline2/config.json"


def _load():
    if "vamsExecuteIsaacLabPipeline" in sys.modules:
        return importlib.reload(sys.modules["vamsExecuteIsaacLabPipeline"])
    return importlib.import_module("vamsExecuteIsaacLabPipeline")


def _s3_for(manifest):
    s3 = MagicMock()

    def _get_object(Bucket, Key, **kw):  # noqa: N803 - boto3 kwarg names
        body = manifest if Key.endswith("manifest.json") else {"trainingConfig": {"mode": "train"}}
        return {"Body": MagicMock(read=lambda b=json.dumps(body).encode("utf-8"): b)}

    s3.get_object.side_effect = _get_object
    return s3


def _sfn_input(entry):
    mod = _load()
    manifest = {
        "inputFiles": [entry],
        "outputs": {"bucket": "run-bkt", "files": "pipelines/p2/J/output/E1/files/"},
        "systemConfig": {"orchestrationBusArn": "arn:bus",
                         "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P2"},
    }
    body = {"TaskToken": "tok", "inputManifestS3Location": f"s3://run-bkt/{MANIFEST_KEY}",
            "inputConfigurationS3Location": f"s3://run-bkt/{CONFIG_KEY}"}
    start = MagicMock(return_value={"executionArn": "arn:aws:states:us-east-1:1:execution:IsaacLab:x"})
    with patch.object(mod, "s3_client", _s3_for(manifest)), \
            patch.object(mod.sfn_client, "start_execution", start), \
            patch.object(mod.events_client, "put_events", MagicMock()):
        resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
    assert resp["statusCode"] == 200, resp
    return json.loads(start.call_args.kwargs["input"])


@pytest.mark.unit
class TestAssetRootBucket:
    def test_a_shadowed_input_resolves_against_the_asset_bucket(self):
        sfn_input = _sfn_input({
            "bucket": "run-bkt", "key": "pipelines/p1/J/output/E1/files/scene.usd",
            "relativePath": "/scene.usd", "assetId": "xidM", "databaseId": "dbM",
            "assetRootS3Key": "projects/site-a/", "assetRootBucket": "asset-bkt"})
        assert (sfn_input["bucketAsset"], sfn_input["inputAssetLocationKey"]) == (
            "asset-bkt", "projects/site-a/")

    def test_a_manifest_without_the_root_bucket_keeps_the_entry_bucket(self):
        sfn_input = _sfn_input({
            "bucket": "asset-bkt", "key": "xidM/scene.usd", "relativePath": "/scene.usd",
            "assetId": "xidM", "databaseId": "dbM", "assetRootS3Key": "xidM/"})
        assert (sfn_input["bucketAsset"], sfn_input["inputAssetLocationKey"]) == ("asset-bkt", "xidM/")
