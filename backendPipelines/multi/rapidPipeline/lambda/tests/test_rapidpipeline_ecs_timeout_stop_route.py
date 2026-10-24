#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The RapidPipeline (ECS) timeout stop route, from the two lambdas' side.

When the ``RapidPipelineRunFargate`` state reaches its task-level ``TimeoutSeconds``, its first catch
adds ``States.Timeout`` at ``$.error`` and runs the stop route before ``PipelineEndTask``:

*   ``ListTimedOutTask`` (``ecs:listTasks`` filtered by ``StartedBy`` = the execution name) adds its
    response at ``$.timedOutTasks``;
*   a Map of ``ecs:stopTask`` discards its results;
*   a failed call is caught into ``$.stopError`` and the run still reaches ``PipelineEndTask``.

So ``pipelineEnd`` can receive ``$.timedOutTasks`` and ``$.stopError`` beside ``$.error``. What it
reports to the parent workflow must be the timeout, whatever happened to the stop: the stop is
best-effort, and the parent needs to know its run failed, not why a cleanup call did.

The route finds the task through ``StartedBy``, which the Fargate state sets to ``$$.Execution.Name``.
That is the ``name`` openPipeline passes to ``start_execution``. ECS rejects a ``startedBy`` that is
longer than 128 characters or holds anything but letters, numbers, hyphens, underscores and forward
slashes, and a rejected ``RunTask`` fails every run, so openPipeline's name must always fit.

The event shapes come from the state machine in
``infra/lib/nestedStacks/pipelines/multi/rapidPipeline/constructs/rapidPipeline-construct.ts``.
"""

import datetime
import importlib.util
import os
import re
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
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:111122223333:stateMachine:sm",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".stp,.step,.glb,.obj",
}.items():
    os.environ.setdefault(k, v)

TASK_TOKEN = "tok-123"
TASK_ARN = "arn:aws:ecs:us-east-1:111122223333:task/rapid-cluster/0123456789abcdef"

# RunTask's startedBy: "Up to 128 letters (uppercase and lowercase), numbers, hyphens (-), forward
# slash (/), and underscores (_) are allowed."
STARTED_BY = re.compile(r"^[A-Za-z0-9_/-]{1,128}$")


def _load(file_name, module_name):
    """This pipeline's own copy of ``file_name``, loaded by path under a name no other suite uses.

    Several pipelines ship a ``pipelineEnd.py`` and an ``openPipeline.py``, and the rapidPipeline and
    modelOps copies of pipelineEnd are byte-identical, so a bare-name import could bind to another
    pipeline's file and still pass.
    """
    path = os.path.join(_LAMBDA_DIR, file_name)
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    assert os.path.abspath(module.__file__) == os.path.abspath(path)
    return module


def _pipeline_end():
    return _load("pipelineEnd.py", "_rapidpipeline_pipeline_end_for_timeout_stop_route")


def _open_pipeline():
    return _load("openPipeline.py", "_rapidpipeline_open_pipeline_for_timeout_stop_route")


def _timed_out_event(**route_fields):
    """constructPipeline's output (the Fargate state's input) with the Timeout catch's error added."""
    event = {
        "jobName": "PipelineJob_20261024_200000_000_0123abcd",
        "commands": ["/bin/sh", "-c", "aws s3 cp s3://abkt/xidM/model.obj . && /rpdx/rpdx ..."],
        "externalSfnTaskToken": TASK_TOKEN,
        "status": "STARTING",
        "error": {
            "Error": "States.Timeout",
            "Cause": "The task timed out after 17100 seconds.",
        },
    }
    event.update(route_fields)
    return event


def _report(event):
    mod = _pipeline_end()
    sfn = MagicMock()
    with patch.object(mod, "sfn", sfn):
        returned = mod.lambda_handler(event, MagicMock())
    return returned, sfn


@pytest.mark.unit
class TestTheParentIsToldOfTheTimeoutWhateverTheStopDid:
    @pytest.mark.parametrize(
        "route_fields",
        [
            pytest.param({"timedOutTasks": {"TaskArns": [TASK_ARN]}}, id="task-listed-and-stopped"),
            pytest.param({"timedOutTasks": {"TaskArns": []}}, id="no-task-running-any-more"),
            pytest.param(
                {"stopError": {"Error": "Ecs.AccessDeniedException", "Cause": "not authorized"}},
                id="list-failed",
            ),
            pytest.param(
                {
                    "timedOutTasks": {"TaskArns": [TASK_ARN]},
                    "stopError": {
                        "Error": "Ecs.InvalidParameterException",
                        "Cause": "The referenced task was not found.",
                    },
                },
                id="stop-failed",
            ),
        ],
    )
    def test_the_failure_reported_is_the_timeout(self, route_fields):
        returned, sfn = _report(_timed_out_event(**route_fields))
        assert sfn.send_task_failure.call_count == 1
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == TASK_TOKEN
        assert sfn.send_task_failure.call_args.kwargs["error"] == "Pipeline Failure: States.Timeout"
        sfn.send_task_success.assert_not_called()
        # The handler returns its input, and the run's Fail state reads $.error from it.
        assert returned["error"]["Error"] == "States.Timeout"


def _open_pipeline_event():
    """A payload that passes openPipeline's checks and reaches ``start_execution``."""
    return {
        "inputS3AssetFilePath": "s3://abkt/xid/cad/part.glb",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/JOB/output/E1/files/",
        "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/JOB/output/E1/previews/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/JOB/output/E1/metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/p1/E1/",
        "outputFileType": "glb",
        "sfnExternalTaskToken": TASK_TOKEN,
    }


def _started_execution_names(runs):
    mod = _open_pipeline()
    sfn = MagicMock()
    sfn.start_execution.return_value = {
        "executionArn": "arn:aws:states:us-east-1:111122223333:execution:sm:x",
        "startDate": datetime.datetime(2026, 10, 24, 20, 0, 0),
    }
    with patch.object(mod, "sfn", sfn), patch.object(mod, "events_client", MagicMock()):
        for _ in range(runs):
            mod.lambda_handler(_open_pipeline_event(), MagicMock())
    return [c.kwargs["name"] for c in sfn.start_execution.call_args_list]


@pytest.mark.unit
class TestTheExecutionNameIsAValidStartedBy:
    def test_every_execution_name_fits_started_by(self):
        names = _started_execution_names(runs=50)
        assert len(names) == 50
        for name in names:
            assert STARTED_BY.match(name), f"{name!r} is not a valid ECS startedBy"

    def test_the_names_are_unique_per_execution(self):
        """ListTasks filters by startedBy, so two executions sharing a name could stop each other's
        task. Step Functions already refuses a reused execution name, and the name carries a random
        suffix; this pins that the suffix is there."""
        names = _started_execution_names(runs=50)
        assert len(set(names)) == len(names)

    def test_the_pattern_rejects_what_ecs_rejects(self):
        """Control for the pattern itself, so a loosened regex cannot make the test above vacuous."""
        assert STARTED_BY.match("PipelineJob_20261024_200000_000_0123abcd")
        for bad in ["", "a" * 129, "Pipeline Job", "PipelineJob:1", "PipelineJob.1"]:
            assert not STARTED_BY.match(bad), bad
