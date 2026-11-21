#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The zipArchive Batch job carries the pipeline's job tags (vams:stackname, vams:pipeline) that the
construct sets in PIPELINE_JOB_TAGS, so a later release can scope job permissions to them."""

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

_ENV = {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1",
        "BATCH_JOB_QUEUE": "zip-queue", "BATCH_JOB_DEFINITION": "zip-jobdef"}
# The handler reads AWS_REGION when it runs, after _load has restored the environment.
os.environ.setdefault("AWS_REGION", "us-east-1")

TAGS = {"vams:stackname": "vams-core-test", "vams:pipeline": "zipArchive"}

pytestmark = pytest.mark.unit


def _load(extra_env):
    env = dict(_ENV, **extra_env)
    with patch.dict(os.environ, env):
        if "PIPELINE_JOB_TAGS" not in extra_env:
            os.environ.pop("PIPELINE_JOB_TAGS", None)
        with patch("boto3.client", return_value=MagicMock()):
            module = importlib.reload(importlib.import_module("executeBatchJob"))
    module.batch.submit_job.return_value = {"jobId": "job-1"}
    return module


def test_the_job_carries_the_pipeline_job_tags():
    mod = _load({"PIPELINE_JOB_TAGS": json.dumps(TAGS)})
    mod.lambda_handler({"jobName": "j", "definition": ["{}"], "taskToken": "tok"}, MagicMock())
    assert mod.batch.submit_job.call_args.kwargs["tags"] == TAGS


def test_without_the_tags_variable_the_job_is_submitted_untagged():
    mod = _load({})
    mod.lambda_handler({"jobName": "j", "definition": ["{}"], "taskToken": "tok"}, MagicMock())
    assert "tags" not in mod.batch.submit_job.call_args.kwargs
