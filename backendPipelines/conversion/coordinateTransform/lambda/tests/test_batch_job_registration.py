#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Abort must be able to terminate this pipeline's AWS Batch job.

The job is submitted from a Lambda under the Step Functions WAIT_FOR_TASK_TOKEN integration, NOT
through the `.sync` (RUN_JOB) Batch integration. That distinction is the whole reason these tests
exist: with `.sync`, Step Functions owns the job's lifecycle and stopping the state machine stops the
job. Under WAIT_FOR_TASK_TOKEN nothing owns it, so registering only the sub-state-machine leaves an
aborted job running — and billing — until it finishes on its own. The Batch job itself has to be
registered by id.

The prefix that identifies the execution reaches this Lambda through three hops (openPipeline's state
machine input -> constructPipeline's re-emitted payload -> the batch task's payload), and a break at
any one of them silently disables registration rather than failing. Each hop is covered here.
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
    "BATCH_JOB_QUEUE": "coord-transform-queue",
    "BATCH_JOB_DEFINITION": "coord-transform-jobdef",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
}.items():
    os.environ.setdefault(_k, _v)

PREFIX = "vams.prod-us-east-1.execution.exec-abc.pipeline.pexec-123"


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


@pytest.mark.unit
class TestBatchJobRegistration:
    def test_submitted_job_is_registered_on_the_orchestration_bus(self):
        mod = _load_execute_batch_job()
        mod.lambda_handler(
            {"jobName": "CoordXform_1", "definition": ["{}"], "taskToken": "tok",
             "orchestrationEventPrefix": PREFIX},
            MagicMock())

        mod.events_client.put_events.assert_called_once()
        entry = mod.events_client.put_events.call_args.kwargs["Entries"][0]
        assert entry["EventBusName"] == "vams-orchestration"
        assert entry["DetailType"] == "pipeline.execution.register"
        detail = json.loads(entry["Detail"])
        # The abort path routes on pipelineExecutionId and terminates on jobId; both are required.
        assert detail["pipelineExecutionId"] == "pexec-123"
        assert detail["subExecution"] == {"resourceType": "batchJob", "jobId": "job-xyz"}

    def test_registration_happens_after_the_job_exists(self):
        # Registering a job id that was never submitted would leave the abort path terminating
        # nothing, and would report a resource the execution does not actually own.
        mod = _load_execute_batch_job()
        order = []
        mod.batch.submit_job.side_effect = lambda **kw: (order.append("submit"),
                                                         {"jobId": "job-xyz"})[1]
        mod.events_client.put_events.side_effect = lambda **kw: order.append("register")

        mod.lambda_handler(
            {"jobName": "j", "definition": ["{}"], "orchestrationEventPrefix": PREFIX},
            MagicMock())
        assert order == ["submit", "register"]

    def test_a_registration_failure_never_fails_the_pipeline(self):
        # The job is already running by this point; raising here would fail the step while leaving
        # the job going — strictly worse than an unregistered job.
        mod = _load_execute_batch_job()
        mod.events_client.put_events.side_effect = RuntimeError("bus unavailable")

        result = mod.lambda_handler(
            {"jobName": "j", "definition": ["{}"], "orchestrationEventPrefix": PREFIX},
            MagicMock())
        assert result["jobId"] == "job-xyz"
        assert result["status"] == "SUBMITTED"

    @pytest.mark.parametrize("event_extra", [{}, {"orchestrationEventPrefix": ""}])
    def test_no_prefix_skips_registration_without_erroring(self, event_extra):
        # A direct/local invocation carries no orchestration context. Skipping is correct; crashing
        # would break a path that legitimately has nothing to register.
        mod = _load_execute_batch_job()
        result = mod.lambda_handler(
            {"jobName": "j", "definition": ["{}"], **event_extra}, MagicMock())
        mod.events_client.put_events.assert_not_called()
        assert result["jobId"] == "job-xyz"

    def test_an_unrecognized_prefix_skips_registration(self):
        # Without a derivable pipelineExecutionId the registration event cannot be routed, so
        # emitting it would only produce an unattributable record.
        mod = _load_execute_batch_job()
        mod.lambda_handler(
            {"jobName": "j", "definition": ["{}"], "orchestrationEventPrefix": "garbage"},
            MagicMock())
        mod.events_client.put_events.assert_not_called()


@pytest.mark.unit
class TestPrefixReachesTheBatchLambda:
    """The three hops the prefix travels. A break in any one disables registration silently."""

    def _lambda_source(self, name):
        # Read rather than import: these modules require their own runtime env vars at import time,
        # and only their source text is under assertion here.
        return open(os.path.join(_LAMBDA_DIR, name), encoding="utf-8").read()

    def test_open_pipeline_puts_the_prefix_into_the_state_machine_input(self):
        # It must be part of sfn_input, not merely read from the event for the sub-SFN registration.
        source = self._lambda_source("openPipeline.py")
        sfn_input = source.split("sfn_input = {")[1].split("}")[0]
        assert '"orchestrationEventPrefix": orchestration_event_prefix,' in sfn_input

    def test_construct_pipeline_re_emits_the_prefix(self):
        # This task's outputPath is $.Payload, which REPLACES the state — anything not re-emitted
        # here is gone by the time the batch task runs.
        source = self._lambda_source("constructPipeline.py")
        assert '"orchestrationEventPrefix": event.get("orchestrationEventPrefix", ""),' in source

    def test_the_batch_task_payload_passes_the_prefix(self):
        construct = os.path.normpath(os.path.join(
            _LAMBDA_DIR, "..", "..", "..", "..", "infra", "lib", "nestedStacks", "pipelines",
            "conversion", "coordinateTransform", "constructs", "coordinateTransform-construct.ts"))
        source = open(construct, encoding="utf-8").read()
        assert '"orchestrationEventPrefix.$": "$.orchestrationEventPrefix"' in source

    def test_the_lambda_builder_wires_the_orchestration_bus(self):
        # Without ORCHESTRATION_BUS_NAME and PutEvents, registration is skipped at run time with
        # only an info log — indistinguishable from a direct invocation.
        builder = os.path.normpath(os.path.join(
            _LAMBDA_DIR, "..", "..", "..", "..", "infra", "lib", "nestedStacks", "pipelines",
            "conversion", "coordinateTransform", "lambdaBuilder",
            "coordinateTransformFunctions.ts"))
        source = open(builder, encoding="utf-8").read()
        batch_builder = source.split("export function buildExecuteBatchJobFunction")[1]
        assert "ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName" in batch_builder
        assert "orchestrationBus.grantPutEventsTo(fun)" in batch_builder
