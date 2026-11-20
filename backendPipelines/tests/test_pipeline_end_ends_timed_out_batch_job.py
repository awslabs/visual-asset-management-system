#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A pipelineEnd whose Batch state has its own bound ends the run's job when that bound fires.

Step Functions ends a `.sync` Batch job when the state's own bound fires, so for a `.sync` state this is a
backup; a job a Lambda submitted under WAIT_FOR_TASK_TOKEN has no owner at all once the token wait times
out, and would start or go on after its run has already failed. Each of these pipelineEnd handlers looks
the run's job up by its run-unique name on the queues in BATCH_JOB_QUEUES and terminates it while it is
not finished. Image to 3D and Video SOP/BOM have the same check in their own suites; the four
pipelines here have no pipelineEnd suite of their own.
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_PIPELINES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_HANDLERS = [
    "preview/3dThumbnail/lambda/pipelineEnd.py",
    "preview/pcPotreeViewer/lambda/pipelineEnd.py",
    "conversion/coordinateTransform/lambda/pipelineEnd.py",
    "conversion/cogConvert/lambda/pipelineEnd.py",
]


def _load(rel_path):
    path = os.path.join(_PIPELINES_DIR, rel_path)
    lambda_dir = os.path.dirname(path)
    name = "pipeline_end_" + rel_path.replace("/", "_").replace(".py", "")
    sys.path.insert(0, lambda_dir)
    try:
        with patch.dict(os.environ, {"AWS_REGION": "us-east-1"}), \
                patch("boto3.client", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(lambda_dir)


def _end(module, error, jobs):
    """Run the handler with ``jobs`` listed on queue-a and nothing on queue-b."""
    batch = MagicMock()
    batch.list_jobs.side_effect = lambda jobQueue, filters: {
        "jobSummaryList": jobs if jobQueue == "queue-a" else []}
    event = {"jobName": "PipelineJob_20261118_000000_000_abcdef12", "externalSfnTaskToken": "tok",
             "error": error}
    with patch.object(module, "batch", batch), patch.object(module, "sfn", MagicMock()), \
            patch.object(module, "BATCH_JOB_QUEUES", ["queue-a", "queue-b"]):
        module.lambda_handler(event, MagicMock())
    return batch


@pytest.mark.unit
@pytest.mark.parametrize("rel_path", _HANDLERS)
def test_a_state_timeout_ends_the_runs_unfinished_job_on_every_queue(rel_path):
    module = _load(rel_path)
    batch = _end(module, {"Error": "States.Timeout"}, [
        {"jobId": "j-queued", "status": "RUNNABLE"},
        {"jobId": "j-done", "status": "SUCCEEDED"},
    ])
    assert [c.kwargs["jobQueue"] for c in batch.list_jobs.call_args_list] == ["queue-a", "queue-b"]
    assert all(
        c.kwargs["filters"] == [{"name": "JOB_NAME",
                                 "values": ["PipelineJob_20261118_000000_000_abcdef12"]}]
        for c in batch.list_jobs.call_args_list)
    assert [c.kwargs["jobId"] for c in batch.terminate_job.call_args_list] == ["j-queued"]


@pytest.mark.unit
@pytest.mark.parametrize("rel_path", _HANDLERS)
def test_another_failure_ends_no_job(rel_path):
    module = _load(rel_path)
    batch = _end(module, {"Error": "States.TaskFailed", "Cause": "exit 1"},
                 [{"jobId": "j", "status": "RUNNING"}])
    batch.list_jobs.assert_not_called()
    batch.terminate_job.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("rel_path", _HANDLERS)
def test_a_failed_lookup_still_reports_the_failure(rel_path):
    module = _load(rel_path)
    batch, sfn = MagicMock(), MagicMock()
    batch.list_jobs.side_effect = RuntimeError("throttled")
    event = {"jobName": "J", "externalSfnTaskToken": "tok", "error": {"Error": "States.Timeout"}}
    with patch.object(module, "batch", batch), patch.object(module, "sfn", sfn), \
            patch.object(module, "BATCH_JOB_QUEUES", ["queue-a"]):
        module.lambda_handler(event, MagicMock())
    sfn.send_task_failure.assert_called_once()
