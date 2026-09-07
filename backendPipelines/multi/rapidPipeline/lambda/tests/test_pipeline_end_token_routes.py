#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""rapidPipeline ``pipelineEnd`` finds the workflow's callback token on both routes into it.

``pipelineEnd`` is the only state that reports the VAMS workflow's task token once the container has
run, and it is reached by two routes that carry the token in different places:

*   **Completed ECS task.** ``RapidPipelineRunFargate`` declares no ``resultPath``, so the task
    description REPLACES the state input. The fields ``constructPipeline`` emitted are gone and the
    token survives only as the container environment override the task was started with
    (``Overrides.ContainerOverrides[].Environment[]``).
*   **Caught ECS error.** ``addCatch(handleRapidPipelineError, { resultPath: "$.error" })`` keeps the
    state's raw input and adds the error to it, so the token is the ``externalSfnTaskToken`` field
    ``constructPipeline`` emitted and there is no container description at all.

The failure route is the one that matters most: it is the route a failed conversion takes, and a
token left unreported there holds the workflow task RUNNING for this pipeline's full 14400-second
taskTimeout (``vamsSchema/pipeline.json``, ``waitForCallback: "Enabled"``) while the container has
already stopped.

The two event shapes here are built from the state machine in
``infra/lib/nestedStacks/pipelines/multi/rapidPipeline/constructs/rapidPipeline-construct.ts`` (the
task's result path, the catch's result path, and the single ``externalSfnTaskToken`` environment
override), and the ``constructPipeline`` output shape is its handler's own return value.
"""

import os
import sys
import types
import importlib.util
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

for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(k, v)

TASK_TOKEN = "tok-123"

_PIPELINE_END = os.path.join(_LAMBDA_DIR, "pipelineEnd.py")
_MODULE_NAME = "_pipeline_end_for_rapidpipeline_token_routes"


def _load():
    """This pipeline's own ``pipelineEnd``, loaded by path under a name no other suite uses.

    Twelve pipelines ship a ``pipelineEnd.py`` and each suite puts its own lambda directory on
    ``sys.path``, so a bare-name import binds to whichever copy reached ``sys.modules`` first and a
    reload re-resolves the same way. The rapidPipeline and modelOps copies are byte-identical, so
    that collision is silent: one of the two suites would assert against the other's file and a
    one-sided edit to either would still pass.
    """
    module = sys.modules.get(_MODULE_NAME)
    if module is None:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _PIPELINE_END)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
    assert module.__file__ == _PIPELINE_END, \
        f"loaded {module.__file__}, expected this pipeline's own copy at {_PIPELINE_END}"
    return module


def _construct_pipeline_output():
    """What ``constructPipeline`` emits, which is the ECS task's state input."""
    return {
        "jobName": "PipelineJob_x",
        "commands": ["/bin/sh", "-c", "aws s3 cp s3://abkt/xidM/model.obj . && /rpdx/rpdx ..."],
        "inputMetadataS3Location": "s3://abkt/.../metadata.json",
        "inputConfigurationS3Location": "s3://abkt/.../config.json",
        "externalSfnTaskToken": TASK_TOKEN,
        "status": "STARTING",
    }


def _completed_ecs_task_event():
    """The completed-task description, which replaces the state input."""
    return {
        "TaskArn": "arn:aws:ecs:us-east-1:1:task/rapidpipeline-cluster/abc",
        "LastStatus": "STOPPED",
        "Overrides": {
            "ContainerOverrides": [{
                "Name": "RapidPipelineContainer",
                "Command": ["/bin/sh", "-c", "aws s3 cp s3://abkt/xidM/model.obj . && ..."],
                "Environment": [{"Name": "externalSfnTaskToken", "Value": TASK_TOKEN}],
            }],
        },
    }


def _caught_error_event():
    """The caught-error shape: the task's raw input with the error added at ``$.error``."""
    event = _construct_pipeline_output()
    event["error"] = {
        "Error": "States.TaskFailed",
        "Cause": '{"Error":"The task stopped with exit code 1"}',
    }
    return event


def _run(event):
    mod = _load()
    sfn = MagicMock()
    raised = None
    with patch.object(mod, "sfn", sfn):
        try:
            mod.lambda_handler(event, MagicMock())
        except Exception as e:  # noqa: BLE001 - the assertion is about whether this route survives
            raised = e
    return raised, sfn


@pytest.mark.unit
class TestTheCaughtErrorRouteReportsTheToken:
    """The route a failed container takes. It carries no task description, so a reader that only
    looks there cannot report at all."""

    def test_the_token_is_reported_failed(self):
        raised, sfn = _run(_caught_error_event())
        assert raised is None, f"the failure route did not survive reading the token: {raised!r}"
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == TASK_TOKEN
        assert sfn.send_task_success.call_count == 0

    def test_the_reported_error_names_the_state_machine_error(self):
        _, sfn = _run(_caught_error_event())
        assert "States.TaskFailed" in sfn.send_task_failure.call_args.kwargs["error"]


@pytest.mark.unit
class TestTheCompletedTaskRouteReportsTheToken:
    """Control for the change above: the success route reads the token from the task description and
    must keep doing so, since the fields constructPipeline emitted are not in that shape. Both cases
    here hold against the unfixed handler as well — that is the point of them."""

    def test_the_token_is_reported_successful(self):
        raised, sfn = _run(_completed_ecs_task_event())
        assert raised is None
        assert sfn.send_task_success.call_count == 1
        assert sfn.send_task_success.call_args.kwargs["taskToken"] == TASK_TOKEN
        assert sfn.send_task_failure.call_count == 0

    def test_a_task_description_without_the_environment_name_still_resolves(self):
        """The task declares one environment override, so the value is the token whether or not the
        name is carried in the description. This is what keeps the already-working route off a
        key-casing assumption that cannot be checked against a live run."""
        event = _completed_ecs_task_event()
        del event["Overrides"]["ContainerOverrides"][0]["Environment"][0]["Name"]
        raised, sfn = _run(event)
        assert raised is None
        assert sfn.send_task_success.call_args.kwargs["taskToken"] == TASK_TOKEN


@pytest.mark.unit
class TestATokenlessEventReportsNothing:
    """A shape carrying no token must return quietly rather than raise. Reaching into a fixed
    position raised IndexError on both of these, which fails the PipelineEndTask itself."""

    def test_an_empty_environment_list_skips_the_callback(self):
        event = _completed_ecs_task_event()
        event["Overrides"]["ContainerOverrides"][0]["Environment"] = []
        raised, sfn = _run(event)
        assert raised is None
        sfn.send_task_success.assert_not_called()
        sfn.send_task_failure.assert_not_called()

    def test_an_empty_container_overrides_list_skips_the_callback(self):
        raised, sfn = _run({"Overrides": {"ContainerOverrides": []}, "LastStatus": "STOPPED"})
        assert raised is None
        sfn.send_task_success.assert_not_called()
        sfn.send_task_failure.assert_not_called()
