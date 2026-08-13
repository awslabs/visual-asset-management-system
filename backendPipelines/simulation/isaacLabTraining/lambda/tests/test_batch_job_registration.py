#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Abort must be able to terminate this pipeline's AWS Batch job.

The job is submitted from a Lambda under the Step Functions WAIT_FOR_TASK_TOKEN integration, NOT
through the `.sync` (RUN_JOB) Batch integration. With `.sync`, Step Functions owns the job's lifecycle
and stopping the state machine stops the job; under WAIT_FOR_TASK_TOKEN nothing owns it, so an
un-registered job keeps running (and billing) after a VAMS abort. Registering the sub-state-machine is
not sufficient for the same reason — the Batch job id has to be registered too.

Reaching this Lambda, `orchestrationEventPrefix` has to survive a `Pass` state whose explicit
`parameters` REPLACE the state. That is not a theoretical hazard: the field was originally omitted
there, which made the batch task's `"$.orchestrationEventPrefix"` reference a non-existent path — a
States.Runtime failure of the whole pipeline, not a quietly skipped registration. The state-machine
wiring is asserted here for that reason.
"""

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

for _k, _v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "BATCH_JOB_QUEUE": "isaaclab-queue",
    "BATCH_JOB_DEFINITION": "isaaclab-jobdef",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
}.items():
    os.environ.setdefault(_k, _v)

PREFIX = "vams.prod-us-east-1.execution.exec-abc.pipeline.pexec-123"

def _repo_root():
    """Walk up to the repo root rather than counting `..` segments — pipeline directories sit at
    differing depths, and a miscounted relative path fails as a missing file (which reads like a
    broken test) instead of as the assertion these tests are making."""
    path = _LAMBDA_DIR
    while path != os.path.dirname(path):
        if os.path.isdir(os.path.join(path, "infra")) and os.path.isdir(
                os.path.join(path, "backendPipelines")):
            return path
        path = os.path.dirname(path)
    raise RuntimeError("repo root not found from " + _LAMBDA_DIR)


_INFRA_PIPELINE = os.path.join(
    _repo_root(), "infra", "lib", "nestedStacks", "pipelines", "simulation", "isaacLabTraining")
_CONSTRUCT = os.path.join(_INFRA_PIPELINE, "constructs", "isaacLabTraining-construct.ts")
_BUILDER = os.path.join(_INFRA_PIPELINE, "lambdaBuilder", "isaacLabTrainingFunctions.ts")


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


def _event(**extra):
    base = {"jobName": "isaac-1", "definition": json.dumps({"trainingConfig": {}}),
            "taskToken": "tok", "outputS3AssetFilesPath": "s3://b/a/",
            "inputS3AssetFilePath": "s3://b/a/in.usd"}
    base.update(extra)
    return base


@pytest.mark.unit
class TestBatchJobRegistration:
    def test_submitted_job_is_registered_on_the_orchestration_bus(self):
        mod = _load_execute_batch_job()
        mod.lambda_handler(_event(orchestrationEventPrefix=PREFIX), MagicMock())

        mod.events_client.put_events.assert_called_once()
        entry = mod.events_client.put_events.call_args.kwargs["Entries"][0]
        assert entry["EventBusName"] == "vams-orchestration"
        assert entry["DetailType"] == "pipeline.execution.register"
        detail = json.loads(entry["Detail"])
        # The abort path routes on pipelineExecutionId and terminates on jobId; both are required.
        assert detail["pipelineExecutionId"] == "pexec-123"
        assert detail["subExecution"] == {"resourceType": "batchJob", "jobId": "job-xyz"}

    def test_registration_happens_after_the_job_exists(self):
        # Registering an id that was never submitted would leave abort terminating nothing.
        mod = _load_execute_batch_job()
        order = []
        mod.batch.submit_job.side_effect = lambda **kw: (order.append("submit"),
                                                        {"jobId": "job-xyz"})[1]
        mod.events_client.put_events.side_effect = lambda **kw: order.append("register")

        mod.lambda_handler(_event(orchestrationEventPrefix=PREFIX), MagicMock())
        assert order == ["submit", "register"]

    def test_a_registration_failure_never_fails_the_pipeline(self):
        # The job is already running; raising here fails the step while leaving it going.
        mod = _load_execute_batch_job()
        mod.events_client.put_events.side_effect = RuntimeError("bus unavailable")

        result = mod.lambda_handler(_event(orchestrationEventPrefix=PREFIX), MagicMock())
        assert result["jobId"] == "job-xyz"
        assert result["status"] == "SUBMITTED"

    @pytest.mark.parametrize("extra", [{}, {"orchestrationEventPrefix": ""}])
    def test_no_prefix_skips_registration_without_erroring(self, extra):
        mod = _load_execute_batch_job()
        result = mod.lambda_handler(_event(**extra), MagicMock())
        mod.events_client.put_events.assert_not_called()
        assert result["jobId"] == "job-xyz"

    def test_an_unrecognized_prefix_skips_registration(self):
        # Without a derivable pipelineExecutionId the event cannot be routed anywhere.
        mod = _load_execute_batch_job()
        mod.lambda_handler(_event(orchestrationEventPrefix="garbage"), MagicMock())
        mod.events_client.put_events.assert_not_called()

    def test_a_multi_node_job_still_registers(self):
        # numNodes > 1 rewrites submit_params into nodeOverrides; registration must be unaffected.
        mod = _load_execute_batch_job()
        mod.lambda_handler(_event(orchestrationEventPrefix=PREFIX, numNodes=3), MagicMock())
        submitted = mod.batch.submit_job.call_args.kwargs
        assert "nodeOverrides" in submitted and "containerOverrides" not in submitted
        mod.events_client.put_events.assert_called_once()


@pytest.mark.unit
class TestPrefixSurvivesTheStateMachine:
    """The prefix has to reach the batch task through a state-REPLACING Pass state."""

    def _construct(self):
        return open(_CONSTRUCT, encoding="utf-8").read()

    def test_the_prepare_state_forwards_the_prefix(self):
        # PrepareExecutionState's `parameters` REPLACE the state, so a field absent here is
        # unreachable downstream. This omission previously broke the pipeline outright.
        source = self._construct()
        prepare = source.split('new sfn.Pass(this, "PrepareExecutionState"')[1].split("});")[0]
        assert '"orchestrationEventPrefix.$"' in prepare

    def test_the_prepare_state_reads_the_prefix_from_the_original_input(self):
        # The open lambda does NOT echo this field in either mode (train or evaluate), so sourcing it
        # from $.openResult.Payload would resolve to nothing.
        source = self._construct()
        prepare = source.split('new sfn.Pass(this, "PrepareExecutionState"')[1].split("});")[0]
        assert '"orchestrationEventPrefix.$": "$.orchestrationEventPrefix"' in prepare
        assert '"orchestrationEventPrefix.$": "$.openResult' not in prepare

    def test_neither_open_pipeline_mode_echoes_the_prefix(self):
        # Guards the assumption the test above depends on. If a future change starts echoing it,
        # sourcing from the original input still works — but this documents why it is not sourced
        # from the payload today.
        source = open(os.path.join(_LAMBDA_DIR, "openPipeline.py"), encoding="utf-8").read()
        for builder in ("def build_training_config", "def build_evaluation_config"):
            body = source.split(builder)[1].split("\ndef ")[0]
            assert "orchestrationEventPrefix" not in body

    def test_the_batch_task_payload_passes_the_prefix(self):
        source = self._construct()
        batch_task = source.split('new tasks.LambdaInvoke(this, "ExecuteBatchJobState"')[1]
        assert '"orchestrationEventPrefix.$": "$.orchestrationEventPrefix"' in batch_task

    def test_the_vams_execute_lambda_puts_the_prefix_into_the_state_machine_input(self):
        source = open(os.path.join(_LAMBDA_DIR, "vamsExecuteIsaacLabPipeline.py"),
                      encoding="utf-8").read()
        sfn_input = source.split("sfn_input = {")[1].split("\n        }")[0]
        assert '"orchestrationEventPrefix": resolved["orchestrationEventPrefix"],' in sfn_input

    def test_the_lambda_builder_wires_the_orchestration_bus(self):
        # Without ORCHESTRATION_BUS_NAME and PutEvents, registration is skipped at run time with an
        # info log only — indistinguishable from a direct invocation.
        # This builder is a class assigning `this.executeBatchJobFunction`, not a buildX() function,
        # so the slice runs from that assignment to the next function's assignment.
        source = open(_BUILDER, encoding="utf-8").read()
        batch_builder = source.split("this.executeBatchJobFunction = new lambda.Function")[1]
        batch_builder = batch_builder.split("this.closePipelineFunction = new lambda.Function")[0]
        assert "ORCHESTRATION_BUS_NAME" in batch_builder
        assert "grantPutEventsTo" in batch_builder
