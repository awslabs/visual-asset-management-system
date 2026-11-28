#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The RapidPipeline (ECS) timeout stop route, from the two lambdas' side.

When the ``RapidPipelineRunFargate`` state reaches its task-level ``TimeoutSeconds``, its first catch
adds ``States.Timeout`` at ``$.error`` and runs the stop route before ``PipelineEndTask``:

*   ``ListTimedOutTask`` (``ecs:listTasks``, the cluster's running tasks) adds its response at
    ``$.timedOutTasks``;
*   when it listed a task, ``DescribeTimedOutTasks`` (``ecs:describeTasks``) adds the ARNs of the
    described tasks whose ``group`` is the execution name at ``$.timedOutTaskArns``;
*   a Map of ``ecs:stopTask`` over those ARNs discards its results;
*   a failed call is caught into ``$.stopError`` and the run still reaches ``PipelineEndTask``.

So ``pipelineEnd`` can receive ``$.timedOutTasks``, ``$.timedOutTaskArns`` and ``$.stopError`` beside
``$.error``. What it reports to the parent workflow must be the timeout, whatever happened to the
stop: the stop is best-effort, and the parent needs to know its run failed, not why a cleanup call
did.

The route finds the task through its task group, which the Fargate state sets to
``$$.Execution.Name``. That is the ``name`` openPipeline passes to ``start_execution``. The Amazon ECS
Developer Guide limits a task group name to 255 characters (the RunTask API reference documents no
limit of its own), and a group that does not match one execution alone could stop another run's task
or none, so openPipeline's name must fit and be unique per execution.

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
OTHER_TASK_ARN = "arn:aws:ecs:us-east-1:111122223333:task/rapid-cluster/fedcba9876543210"

# The Amazon ECS Developer Guide, "Group related Amazon ECS tasks": "A task group name must be 255 or
# fewer characters." The RunTask API reference documents no length or character limit for group.
TASK_GROUP_MAX_LENGTH = 255
# The group names ECS gives a task by default: standalone tasks get "family:<task definition
# family>", service tasks "service:<service name>". A label in either form could match another task.
DEFAULT_TASK_GROUP = re.compile(r"^(family|service):")


def _is_valid_task_group(name):
    return 1 <= len(name) <= TASK_GROUP_MAX_LENGTH and not DEFAULT_TASK_GROUP.match(name)


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
            "Cause": "The task timed out after 17040 seconds.",
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
            pytest.param(
                {
                    "timedOutTasks": {"TaskArns": [TASK_ARN, OTHER_TASK_ARN]},
                    "timedOutTaskArns": [TASK_ARN],
                },
                id="task-matched-and-stopped",
            ),
            pytest.param({"timedOutTasks": {"TaskArns": []}}, id="no-task-running-any-more"),
            pytest.param(
                {"timedOutTasks": {"TaskArns": [OTHER_TASK_ARN]}, "timedOutTaskArns": []},
                id="no-running-task-is-this-runs",
            ),
            pytest.param(
                {"stopError": {"Error": "Ecs.AccessDeniedException", "Cause": "not authorized"}},
                id="list-failed",
            ),
            pytest.param(
                {
                    "timedOutTasks": {"TaskArns": [TASK_ARN]},
                    "stopError": {
                        "Error": "States.DataLimitExceeded",
                        "Cause": "The state/task returned a result with a size exceeding the maximum.",
                    },
                },
                id="describe-failed",
            ),
            pytest.param(
                {
                    "timedOutTasks": {"TaskArns": [TASK_ARN]},
                    "timedOutTaskArns": [TASK_ARN],
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
class TestTheExecutionNameIsAValidTaskGroup:
    def test_every_execution_name_fits_a_task_group(self):
        names = _started_execution_names(runs=50)
        assert len(names) == 50
        for name in names:
            assert _is_valid_task_group(name), f"{name!r} is not a valid ECS task group label"

    def test_the_names_are_unique_per_execution(self):
        """DescribeTimedOutTasks keeps the tasks whose group equals the execution name, so two
        executions sharing a name could stop each other's task. Step Functions already refuses a
        reused execution name, and the name carries a random suffix; this pins that the suffix is
        there."""
        names = _started_execution_names(runs=50)
        assert len(set(names)) == len(names)

    def test_the_check_rejects_what_cannot_label_one_run(self):
        """Control for the check itself, so a loosened check cannot make the tests above vacuous."""
        assert _is_valid_task_group("PipelineJob_20261024_200000_000_0123abcd")
        assert _is_valid_task_group("a" * TASK_GROUP_MAX_LENGTH)
        for bad in ["", "a" * (TASK_GROUP_MAX_LENGTH + 1), "family:rapidpipeline", "service:web"]:
            assert not _is_valid_task_group(bad), bad
