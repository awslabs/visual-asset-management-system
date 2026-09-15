#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests that constructPipeline builds the Batch definition with no S3 access of its own.

The lambda holds no S3 grant in its builder, so any S3 call it makes is denied at runtime and
logged as a warning. The handler therefore reaches Amazon S3 not at all: it derives the definition
from the event alone and always returns STARTING."""

import os
import sys
import json
import types
import importlib.util
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# Every pipeline ships a module named constructPipeline and each pipeline's tests put their own
# lambda dir on sys.path, so `import constructPipeline` in one pytest process resolves to whichever
# dir leads. Load this pipeline's file by path under a name only this suite uses.
_MODULE_NAME = "splatToolbox_constructPipeline_undertest"


def _load():
    """Execute this pipeline's constructPipeline.py fresh, under a suite-private module name."""
    path = os.path.join(_LAMBDA_DIR, "constructPipeline.py")
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    loaded = os.path.normcase(os.path.normpath(os.path.abspath(mod.__file__)))
    expected = os.path.normcase(os.path.normpath(path))
    assert loaded == expected, f"module shadow: loaded {mod.__file__}, expected {path}"
    return mod

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger


def _event():
    return {
        "jobName": "PipelineJob_20260101_000000_000_abcd1234",
        "inputS3AssetFilePath": "s3://abkt/xidM/scan.zip",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/scan.zip/3dRecon/splatToolbox",
        "inputMetadataS3Location": "s3://abkt/.../metadata.json",
        "inputConfigurationS3Location": "s3://abkt/.../config.json",
        "externalSfnTaskToken": "tok",
    }


class _RecordingS3:
    """Records every S3 call so a test can assert on calls the handler makes, not only on ones
    a stub was asked about. A stub that silently accepted a write would hide the write."""

    class exceptions:
        class ClientError(Exception):
            def __init__(self, response=None):
                self.response = response or {"Error": {"Code": "404"}}

    def __init__(self, calls):
        self._calls = calls

    def __getattr__(self, name):
        def _record(**kwargs):
            self._calls.append((name, kwargs))
            return {}
        return _record


@pytest.mark.unit
class TestConstructPipelineMakesNoS3Calls:
    def _load_with_recorded_s3(self):
        """Load constructPipeline against a boto3 whose every client records its calls.

        Returns (module, calls). A module that binds an S3 client at import and calls it inside the
        handler shows up in `calls`."""
        calls = []
        fake_boto3 = types.ModuleType("boto3")
        fake_boto3.client = lambda service, **kw: _RecordingS3(calls)
        fake_boto3.resource = lambda *a, **kw: _RecordingS3(calls)
        with patch.dict(sys.modules, {"boto3": fake_boto3}):
            mod = _load()
            mod.lambda_handler(_event(), MagicMock())
        return mod, calls

    def test_handler_makes_no_s3_calls_at_all(self):
        _mod, calls = self._load_with_recorded_s3()
        assert calls == [], f"handler reached S3: {[name for name, _ in calls]}"

    def test_writes_no_lock_object(self):
        """A per-execution lock object under `locks/` would accumulate in the auxiliary bucket,
        which has no expiration lifecycle rule."""
        _mod, calls = self._load_with_recorded_s3()
        written = [kw.get("Key") for name, kw in calls if name == "put_object"]
        assert written == [], f"lock object written: {written}"

    def test_module_binds_no_s3_client(self):
        mod = _load()
        assert not hasattr(mod, "s3_client")
        assert not hasattr(mod, "is_duplicate_job")

    def test_definition_is_derived_from_the_event_alone(self):
        """Every invocation yields the STARTING definition; there is no second status the next
        state would have to route on. The Batch task resolves `$.definition` and
        `$.externalSfnTaskToken` by path, and a missing path raises States.Runtime, which the
        task's States.ALL catch does not match."""
        mod = _load()
        out = mod.lambda_handler(_event(), MagicMock())
        assert out["status"] == "STARTING"
        assert out["jobName"] == "PipelineJob_20260101_000000_000_abcd1234"
        assert out["currentStageType"] == "SPLAT"
        assert out["externalSfnTaskToken"] == "tok"
        definition = json.loads(out["definition"][2])
        assert definition["stages"][0]["outputFiles"] == {
            "bucketName": "abkt",
            "objectDir": "pipelines/p1/MJOB/output/E1/files/",
        }
        assert definition["stages"][0]["temporaryFiles"]["bucketName"] == "aux"

    def test_construct_pipeline_builder_grants_no_s3(self):
        """The handler's no-S3 behaviour matches its IAM: the builder grants the function no bucket
        access, so an S3 call added here would be denied at runtime rather than working."""
        builder = os.path.normpath(os.path.join(
            _LAMBDA_DIR, "..", "..", "..", "..", "infra", "lib", "nestedStacks", "pipelines",
            "3dRecon", "splatToolbox", "lambdaBuilder", "splatToolboxFunctions.ts"))
        assert os.path.isfile(builder), builder
        with open(builder, encoding="utf-8") as fh:
            source = fh.read()
        start = source.index("export function buildConstructPipelineFunction")
        end = source.find("\nexport function", start + 1)
        body = source[start:end if end != -1 else len(source)]
        for grant in ("assetAuxiliaryBucket", "grantRead", "grantWrite",
                      "grantReadPermissionsToAllAssetBuckets"):
            assert grant not in body, f"builder now grants {grant}; revisit the no-S3 assertions"
