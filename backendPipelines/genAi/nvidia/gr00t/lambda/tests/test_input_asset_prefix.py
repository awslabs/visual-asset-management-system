#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the gr00t fine-tune input asset prefix resolution.

The pipeline trains on the WHOLE asset, so the value it hands the container must be an S3 PREFIX.
A whole-asset selection already resolves to a prefix; a single-FILE selection resolves to an object
key, whose asset root comes from the manifest's first input file (``assetRootS3Key``)."""

import os
import sys
import json
import types
import importlib
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
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
}.items():
    os.environ.setdefault(k, v)


def _load():
    if "vamsExecuteGr00tFinetunePipeline" in sys.modules:
        return importlib.reload(sys.modules["vamsExecuteGr00tFinetunePipeline"])
    return importlib.import_module("vamsExecuteGr00tFinetunePipeline")


@pytest.mark.unit
class TestResolveInputAssetPrefix:
    def test_whole_asset_selection_is_used_as_is(self):
        mod = _load()
        resolved = {
            "inputS3AssetFilePath": "s3://abkt/xid/",
            "inputFiles": [{"bucket": "abkt", "key": "xid/", "assetRootS3Key": "xid/"}],
        }
        assert mod.resolve_input_asset_prefix(resolved) == "s3://abkt/xid/"

    def test_single_file_selection_resolves_to_asset_root_prefix(self):
        mod = _load()
        resolved = {
            "inputS3AssetFilePath": "s3://abkt/xid/data/episode_0.parquet",
            "inputFiles": [{"bucket": "abkt", "key": "xid/data/episode_0.parquet",
                            "assetRootS3Key": "xid/"}],
        }
        # The object key must NOT become a prefix (s3://abkt/xid/data/episode_0.parquet/ matches
        # nothing); the asset root is the training prefix.
        assert mod.resolve_input_asset_prefix(resolved) == "s3://abkt/xid/"

    def test_single_file_without_manifest_root_falls_back_to_parent_prefix(self):
        mod = _load()
        resolved = {"inputS3AssetFilePath": "s3://abkt/xid/data/episode_0.parquet", "inputFiles": []}
        assert mod.resolve_input_asset_prefix(resolved) == "s3://abkt/xid/data/"

    def test_empty_input_path_stays_empty(self):
        mod = _load()
        assert mod.resolve_input_asset_prefix({"inputS3AssetFilePath": "", "inputFiles": []}) == ""


@pytest.mark.unit
class TestHandlerForwardsPrefix:
    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
        }

    def _s3_for(self, manifest):
        s3 = MagicMock()

        def get_object(Bucket, Key, **kw):  # noqa: N803 - boto3 kwarg names
            if Key.endswith("manifest.json"):
                body = json.dumps(manifest).encode("utf-8")
                return {"Body": MagicMock(read=lambda b=body: b)}
            raise Exception(f"unexpected key {Key}")

        s3.get_object.side_effect = get_object
        return s3

    def test_file_selection_payload_carries_asset_root_prefix(self):
        mod = _load()
        manifest = {
            "inputFiles": [{"bucket": "abkt", "key": "xid/data/episode_0.parquet",
                            "assetId": "xid", "databaseId": "db", "assetRootS3Key": "xid/"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/JOB/output/E1/files/"},
        }
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", self._s3_for(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputS3AssetPath"] == "s3://abkt/xid/"
