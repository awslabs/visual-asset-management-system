#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""PIPELINE_END called with only a job name deletes the job and sends Step Functions nothing.

After a RunJob error, the state machine invokes PIPELINE_END a second time, once every RUN_JOB
invocation that could still create the job has ended, with only `jobName` and `k8sJobName` in the
payload. The parent workflow was already sent the failure by the first PIPELINE_END, so this call
must not send a second callback, and it must delete the job whether or not a late create made one.

The Kubernetes Jobs API below records the requests the handler makes, so an assertion on what it
recorded is an assertion on what reached the cluster.
"""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

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
    "EKS_CLUSTER_NAME": "test-cluster",
    "CONTAINER_IMAGE_URI": "example.com/rapid-pipeline:test",
    "KUBERNETES_NAMESPACE": "default",
}.items():
    os.environ.setdefault(_k, _v)

JOB_NAME = "rapid-pipeline-job-a1b2c3d4e5f60718293a4b5c6d7e8f90"


class RecordingJobsApi:
    """The two BatchV1Api calls the cleanup makes. `exists` is whether the job is there to delete."""

    def __init__(self, exists):
        self.exists = exists
        self.deletes = []

    def delete_namespaced_job(self, name, namespace, body=None):
        self.deletes.append((name, namespace))
        if not self.exists:
            raise ApiException(status=404, reason="Not Found")
        self.exists = False
        return {}

    def read_namespaced_job_status(self, name, namespace):
        if not self.exists:
            raise ApiException(status=404, reason="Not Found")
        return {}


def _ctx():
    ctx = MagicMock()
    ctx.aws_request_id = "req-test"
    ctx.get_remaining_time_in_millis.return_value = 300000
    return ctx


def _cleanup_only(job_exists):
    """Run the token-less PIPELINE_END through the lambda entry point."""
    if "consolidated_handler" in sys.modules:
        mod = importlib.reload(sys.modules["consolidated_handler"])
    else:
        mod = importlib.import_module("consolidated_handler")
    ku = sys.modules["kubernetes_utils"]
    jobs = RecordingJobsApi(exists=job_exists)
    sfn = MagicMock()
    event = {"operation": "PIPELINE_END", "jobName": "PipelineJobEKS_x", "k8sJobName": JOB_NAME}
    with patch.object(mod, "sfn", sfn), patch.object(ku, "get_batch_v1_api", return_value=jobs), \
            patch("time.sleep", MagicMock()):
        result = mod.lambda_handler(event, _ctx())
    return result, sfn, jobs


@pytest.mark.unit
class TestPipelineEndWithoutToken:
    def test_a_job_created_late_is_deleted_and_no_callback_is_sent(self):
        result, sfn, jobs = _cleanup_only(job_exists=True)

        assert jobs.deletes == [(JOB_NAME, "default")]
        assert jobs.exists is False
        assert sfn.send_task_failure.call_count == 0
        assert sfn.send_task_success.call_count == 0
        assert result["statusCode"] == 200
        assert result["body"]["cleanup"]["success"] is True
        assert result["body"]["notification"] == {"success": True, "error": None}

    def test_no_job_to_delete_is_reported_as_already_deleted(self):
        # The usual case: no RUN_JOB invocation created the job after the first delete.
        result, sfn, jobs = _cleanup_only(job_exists=False)

        assert jobs.deletes == [(JOB_NAME, "default")]
        assert sfn.send_task_failure.call_count == 0
        assert sfn.send_task_success.call_count == 0
        assert result["statusCode"] == 200
        assert result["body"]["cleanup"] == {"success": True, "message": "Job already deleted"}
