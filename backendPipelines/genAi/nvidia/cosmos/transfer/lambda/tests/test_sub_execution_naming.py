#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The name openPipeline runs the cosmos transfer state machine under.

A workflow may carry several triggers of one type, so one upload fans out to simultaneous runs of the
SAME control type; Step Functions rejects a repeated name with ExecutionAlreadyExists, which
openPipeline turns into a generic 500. A random suffix alone would fix the collision but break SFN
retry idempotence -- a retried invocation would start a SECOND multi-hour GPU sub-execution -- so the
name is derived from the pipeline execution id, with the random suffix kept for direct invocations
that carry no orchestration prefix.

The name is the sub-state-machine EXECUTION name only: constructPipeline mints its own uuid-based
jobName and the BatchSubmitJob task reads that one, so no Batch job name or S3 key depends on it."""

import os
import sys
import types
import datetime
import importlib
from unittest.mock import MagicMock

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# Stub customLogging so the lambdas import without aws_lambda_powertools.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

# openPipeline reads these at import time (boto3 clients + module-level env).
for _k, _v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:CosmosTransfer",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/CosmosTransfer",
    "STATE_MACHINE_LOG_GROUP_ARN":
        "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/CosmosTransfer:*",
}.items():
    os.environ.setdefault(_k, _v)


def _open_pipeline():
    if "openPipeline" in sys.modules:
        return importlib.reload(sys.modules["openPipeline"])
    return importlib.import_module("openPipeline")


def _prefix(pipeline_execution_id):
    return f"vams.prod.execution.E1.pipeline.{pipeline_execution_id}"


@pytest.mark.unit
class TestSubStateMachineExecutionName:
    def test_two_runs_of_the_same_control_type_get_different_names(self):
        # The fan-out case: same control type, same wall-clock second, different pipeline executions.
        mod = _open_pipeline()
        assert mod.build_job_name("edge", _prefix("P1")) != mod.build_job_name("edge", _prefix("P2"))

    def test_the_same_run_always_derives_the_same_name(self):
        # An SFN retry re-invokes this lambda with the same body; a second start_execution under a
        # NEW name would launch a duplicate GPU sub-execution billed against one VAMS execution.
        mod = _open_pipeline()
        prefix = _prefix("P1")
        assert mod.build_job_name("edge", prefix) == mod.build_job_name("edge", prefix)

    def test_a_direct_invocation_without_a_prefix_is_still_unique(self):
        mod = _open_pipeline()
        names = {mod.build_job_name("edge", "") for _ in range(20)}
        assert len(names) == 20

    def test_the_name_obeys_the_step_functions_constraints(self):
        mod = _open_pipeline()
        # A pipeline execution id is a 32-character GUID; check both name shapes at that width,
        # across every control type CONTROL_TYPE_MAP accepts (segmentation is the longest).
        for prefix in (_prefix("a" * 32), ""):
            for control_type in ("edge", "depth", "seg", "segmentation", "blur", "vis"):
                name = mod.build_job_name(control_type, prefix)
                assert len(name) <= 80
                assert ":" not in name and "/" not in name

    def test_the_control_type_still_leads_the_name(self):
        mod = _open_pipeline()
        assert mod.build_job_name("depth", _prefix("P1")).startswith("cosmos-transfer-depth-")
        assert mod.build_job_name("edge", "").startswith("cosmos-transfer-edge-")


@pytest.mark.unit
class TestConcurrentFanOutThroughTheHandler:
    """The same requirement stated at the handler boundary, so it measures the name Step Functions
    actually receives rather than the helper in isolation."""

    BASE = {"inputS3AssetFilePath": "s3://b/xid/in.mp4",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xid/"}

    def _execution_name(self, pipeline_execution_id, monkeypatch):
        mod = _open_pipeline()
        seen = {}

        class _Sfn:
            def start_execution(self, **kwargs):
                seen["name"] = kwargs["name"]
                return {"executionArn": "arn:aws:states:us-east-1:1:execution:sm:e",
                        "startDate": datetime.datetime(2026, 1, 1)}

        monkeypatch.setattr(mod, "sfn", _Sfn())
        # Orchestration-event emission is incidental to this assertion.
        monkeypatch.setattr(mod, "events_client", MagicMock())
        result = mod.lambda_handler(
            {**self.BASE, "orchestrationEventPrefix": _prefix(pipeline_execution_id)}, None)
        assert result["statusCode"] == 200, result
        return seen["name"]

    def test_the_fan_out_reaches_step_functions_under_distinct_names(self, monkeypatch):
        first = self._execution_name("P1", monkeypatch)
        second = self._execution_name("P2", monkeypatch)
        assert first != second, (
            f"both pipeline executions reached start_execution as {first!r}; the second is the "
            f"ExecutionAlreadyExists rejection")
