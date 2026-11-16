#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""What `executeBatchJob` logs about the Batch submission it makes.

The handler logs the whole `submit_job` request (`Submitting Batch job: {submit_params}`). Its
`containerOverrides.environment` carries the callback value as `{"name": "SFN_TASK_TOKEN", "value": ...}`,
and this pipeline's own `customLogging/logger.py` masks that pair by its name, whatever the value's
length. The value used here is short, so only the name rule can mask it.

The real logger copy is loaded by path, so a stub that another test module installs as
`customLogging.logger` cannot stand in for it.
"""

import hashlib
import importlib
import importlib.util
import io
import json
import logging
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

for _k, _v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "BATCH_JOB_QUEUE": "isaaclab-queue",
    "BATCH_JOB_DEFINITION": "isaaclab-jobdef",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "BATCH_JOB_LOG_GROUP_NAME": "/aws/batch/job",
    "BATCH_JOB_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/batch/job",
}.items():
    os.environ.setdefault(_k, _v)

# Shorter than the logger's 200-character shape floor.
_CALLBACK_VALUE = "CALLBACK-VALUE-5e21b8"
assert len(_CALLBACK_VALUE) < 200
_REDACTED = "<redacted>"


def _pipeline_logger():
    """This pipeline's own `customLogging/logger.py`, loaded by path under a unique name."""
    path = os.path.join(_LAMBDA_DIR, "customLogging", "logger.py")
    name = "isaaclab_lambda_logger_" + hashlib.md5(path.encode(), usedforsecurity=False).hexdigest()
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_execute_batch_job():
    with patch("boto3.client") as mock_client:
        clients = {}

        def _factory(name, *a, **kw):
            clients.setdefault(name, MagicMock())
            return clients[name]

        mock_client.side_effect = _factory
        module = importlib.reload(importlib.import_module("executeBatchJob"))
    module.batch.submit_job.return_value = {"jobId": "job-xyz"}
    return module


def _event():
    return {
        "jobName": "isaac-1",
        "definition": json.dumps({"trainingConfig": {"mode": "train"}}),
        "taskToken": _CALLBACK_VALUE,
        "outputS3AssetFilesPath": "s3://b/a/",
        "inputS3AssetFilePath": "s3://b/a/in.usd",
    }


@pytest.fixture(scope="module")
def pipeline_logger():
    return _pipeline_logger()


@pytest.fixture
def submitted(pipeline_logger, monkeypatch, request):
    """Run the handler with the real logger on a stream; return (submit_job kwargs, emitted lines)."""
    module = _load_execute_batch_job()
    stream = io.StringIO()
    # A distinct service per test: Powertools binds handlers to the named logger and reuses it.
    real_logger = pipeline_logger.safeLogger(
        service="isaaclab-submit-log-" + request.node.name, logger_handler=logging.StreamHandler(stream))
    monkeypatch.setattr(module, "logger", real_logger)

    module.lambda_handler(_event(), None)

    return module.batch.submit_job.call_args.kwargs, stream.getvalue()


@pytest.mark.unit
def test_the_submission_carries_the_value_under_sfn_task_token(submitted):
    """The shape the masking below is about: without it these tests would pass on nothing."""
    submit_params, _ = submitted
    environment = submit_params["containerOverrides"]["environment"]
    assert {"name": "SFN_TASK_TOKEN", "value": _CALLBACK_VALUE} in environment


@pytest.mark.unit
def test_the_submit_params_rendered_by_an_f_string_are_masked(submitted, pipeline_logger):
    submit_params, _ = submitted

    out = pipeline_logger.mask_sensitive_data(f"Submitting Batch job: {submit_params}")

    assert _CALLBACK_VALUE not in out
    assert f"'name': 'SFN_TASK_TOKEN', 'value': '{_REDACTED}'" in out
    assert "'jobName': 'isaac-1'" in out


@pytest.mark.unit
def test_the_submit_params_as_a_structured_field_are_masked(submitted, pipeline_logger):
    submit_params, _ = submitted

    out = pipeline_logger.mask_sensitive_data({"submitParams": submit_params})

    assert _CALLBACK_VALUE not in json.dumps(out)
    environment = out["submitParams"]["containerOverrides"]["environment"]
    assert {"name": "SFN_TASK_TOKEN", "value": _REDACTED} in environment
    assert {"name": "OUTPUT_S3_PATH", "value": "s3://b/a/"} in environment


@pytest.mark.unit
def test_the_lines_the_handler_emits_carry_no_value(submitted):
    _, emitted = submitted

    assert "Submitting Batch job:" in emitted
    assert _CALLBACK_VALUE not in emitted
