#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""PIPELINE_END records that a run has ended, and RUN_JOB deletes a job it creates after that.

RunJob's retrier resends the RUN_JOB invocation after Step Functions-side client errors, which can be
raised while the earlier invocation is still running, so that invocation can create the run's job after
PipelineEnd (or PipelineEndSuccess) has deleted it. PIPELINE_END therefore writes a run-ended marker, an
Amazon S3 object keyed by the Kubernetes job name, before it deletes the job, and RUN_JOB checks for the
marker after every create. A create the delete did not remove came after the delete, so its check comes
after the marker write and finds the marker.

The Amazon S3, Step Functions and Kubernetes Jobs API stand-ins below append every call they receive to
one list, so an assertion on that list is an assertion on the order the handler made the calls in.
"""

import importlib
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
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

MARKER_BUCKET = "test-auxiliary-bucket"
MARKER_PREFIX = "pipelines/rapid-pipeline-eks/run-ended/"
JOB_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
JOB_NAME = f"rapid-pipeline-job-{JOB_ID}"
MARKER_KEY = f"{MARKER_PREFIX}{JOB_NAME}"
TOKEN = "task-token-0123456789"

PUT_MARKER = ("s3.put_object", MARKER_BUCKET, MARKER_KEY)
HEAD_MARKER = ("s3.head_object", MARKER_BUCKET, MARKER_KEY)
CREATE_JOB = ("k8s.create", JOB_NAME)
DELETE_JOB = ("k8s.delete", JOB_NAME)


def _client_error(code, operation):
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class FakeS3:
    """put_object and head_object against an in-memory set of (bucket, key) pairs."""

    def __init__(self, calls, objects=(), put_error=None, head_error=None):
        self.calls = calls
        self.objects = set(objects)
        self.put_error = put_error
        self.head_error = head_error

    def put_object(self, Bucket, Key, **kwargs):
        self.calls.append(("s3.put_object", Bucket, Key))
        if self.put_error:
            raise self.put_error
        self.objects.add((Bucket, Key))
        return {}

    def head_object(self, Bucket, Key, **kwargs):
        self.calls.append(("s3.head_object", Bucket, Key))
        if self.head_error:
            raise self.head_error
        if (Bucket, Key) not in self.objects:
            raise _client_error("404", "HeadObject")
        return {}


class FakeSfn:
    """The two task-token callbacks PIPELINE_END sends."""

    def __init__(self, calls):
        self.calls = calls

    def send_task_failure(self, taskToken, error, cause):
        self.calls.append(("sfn.send_task_failure", taskToken))

    def send_task_success(self, taskToken, output):
        self.calls.append(("sfn.send_task_success", taskToken))


def _job(name, job_id):
    return SimpleNamespace(metadata=SimpleNamespace(name=name, labels={"job-id": job_id}))


class FakeJobsApi:
    """The BatchV1Api calls create_job and the job cleanup make.

    `jobs` maps a job name to its job-id label. A create of a name that is already there is answered
    409, as Kubernetes does, and reading or deleting a name that is not there is answered 404.
    """

    def __init__(self, calls, jobs=None):
        self.calls = calls
        self.jobs = dict(jobs or {})

    def read_namespaced_job(self, name, namespace):
        if name not in self.jobs:
            raise ApiException(status=404, reason="Not Found")
        return _job(name, self.jobs[name])

    def read_namespaced_job_status(self, name, namespace):
        return self.read_namespaced_job(name, namespace)

    def create_namespaced_job(self, namespace, body):
        name = body["metadata"]["name"]
        self.calls.append(("k8s.create", name))
        if name in self.jobs:
            raise ApiException(status=409, reason="Conflict")
        self.jobs[name] = body["metadata"]["labels"]["job-id"]
        return _job(name, self.jobs[name])

    def delete_namespaced_job(self, name, namespace, body=None):
        self.calls.append(("k8s.delete", name))
        if name not in self.jobs:
            raise ApiException(status=404, reason="Not Found")
        del self.jobs[name]
        return {}


def _manifest():
    """A job manifest shaped like CONSTRUCT_PIPELINE's: the name and the job-id label share the run's id."""
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": JOB_NAME, "labels": {"app": "rapid-pipeline", "job-id": JOB_ID}},
        "spec": {
            "template": {
                "metadata": {"labels": {"app": "rapid-pipeline"}},
                "spec": {
                    "containers": [{"name": "rapid-pipeline", "image": "example.com/rapid-pipeline:test"}],
                    "restartPolicy": "Never",
                },
            },
        },
    }


def _run_job_event(job_name="PipelineJobEKS_x"):
    return {"operation": "RUN_JOB", "jobName": job_name, "jobManifest": _manifest(),
            "externalSfnTaskToken": TOKEN}


_OMITTED = object()


def _pipeline_end_event(error, k8s_job_name=JOB_NAME, job_name="PipelineJobEKS_x"):
    """PipelineEnd's payload when `error` is true, PipelineEndSuccess's (no `error` key) otherwise."""
    event = {"operation": "PIPELINE_END", "jobName": job_name, "externalSfnTaskToken": TOKEN}
    if k8s_job_name is not _OMITTED:
        event["k8sJobName"] = k8s_job_name
    if error:
        event["error"] = {"Error": "RunJobFailed", "Cause": "the RUN_JOB invocation failed"}
    return event


def _context():
    context = MagicMock()
    context.aws_request_id = "req-test"
    context.get_remaining_time_in_millis.return_value = 600000
    return context


def _load_handler():
    if "consolidated_handler" in sys.modules:
        return importlib.reload(sys.modules["consolidated_handler"])
    return importlib.import_module("consolidated_handler")


def _invoke(event, calls, s3, jobs):
    """Run the event through the lambda entry point with the stand-ins in place."""
    handler = _load_handler()
    ku = sys.modules["kubernetes_utils"]
    logger = MagicMock()
    with patch.object(handler, "s3", s3), patch.object(handler, "sfn", FakeSfn(calls)), \
            patch.object(handler, "logger", logger), \
            patch.object(ku, "get_batch_v1_api", return_value=jobs), \
            patch("time.sleep", MagicMock()):
        result = handler.lambda_handler(event, _context())
    return result, logger


@pytest.fixture
def marker_bucket(monkeypatch):
    monkeypatch.setenv("RUN_ENDED_MARKER_BUCKET", MARKER_BUCKET)


def _logged(mock_method, needle):
    return any(needle in str(call.args[0]) for call in mock_method.call_args_list if call.args)


@pytest.mark.unit
class TestPipelineEndWritesTheMarkerBeforeTheDelete:
    @pytest.mark.parametrize("error, callback", [
        (True, "sfn.send_task_failure"),  # PipelineEnd
        (False, "sfn.send_task_success"),  # PipelineEndSuccess
    ])
    def test_the_marker_is_written_after_the_callback_and_before_the_delete(self, marker_bucket, error,
                                                                            callback):
        calls = []
        s3 = FakeS3(calls)
        jobs = FakeJobsApi(calls, {JOB_NAME: JOB_ID})

        result, _ = _invoke(_pipeline_end_event(error=error), calls, s3, jobs)

        assert calls == [(callback, TOKEN), PUT_MARKER, DELETE_JOB]
        assert (MARKER_BUCKET, MARKER_KEY) in s3.objects
        assert jobs.jobs == {}
        assert result["statusCode"] == 200
        assert result["body"]["cleanup"]["success"] is True

    @pytest.mark.parametrize("k8s_job_name", ["failure-before-creation", "unknown", "", None, _OMITTED])
    def test_no_marker_is_written_for_a_run_that_created_no_job(self, marker_bucket, k8s_job_name):
        calls = []
        s3 = FakeS3(calls)

        result, _ = _invoke(_pipeline_end_event(error=True, k8s_job_name=k8s_job_name), calls, s3,
                            FakeJobsApi(calls))

        assert [c for c in calls if c[0].startswith("s3.")] == []
        assert calls[0] == ("sfn.send_task_failure", TOKEN)
        assert result["statusCode"] == 200

    @pytest.mark.parametrize("put_error", [
        _client_error("AccessDenied", "PutObject"),
        EndpointConnectionError(endpoint_url="https://s3.us-east-1.amazonaws.com"),
    ])
    def test_a_failed_marker_write_still_deletes_the_job_and_sends_the_callback(self, marker_bucket,
                                                                                put_error):
        calls = []
        jobs = FakeJobsApi(calls, {JOB_NAME: JOB_ID})

        result, logger = _invoke(_pipeline_end_event(error=True), calls,
                                 FakeS3(calls, put_error=put_error), jobs)

        assert calls == [("sfn.send_task_failure", TOKEN), PUT_MARKER, DELETE_JOB]
        assert jobs.jobs == {}
        assert result["statusCode"] == 200
        assert result["body"]["status"] == "FAILED"
        assert result["body"]["error"] == _pipeline_end_event(error=True)["error"]
        assert result["body"]["cleanup"]["success"] is True
        assert result["body"]["notification"] == {"success": True, "error": None}
        assert _logged(logger.error, MARKER_KEY)

    def test_without_a_marker_bucket_the_job_is_deleted_as_before(self, monkeypatch):
        monkeypatch.delenv("RUN_ENDED_MARKER_BUCKET", raising=False)
        calls = []
        jobs = FakeJobsApi(calls, {JOB_NAME: JOB_ID})

        result, _ = _invoke(_pipeline_end_event(error=True), calls, FakeS3(calls), jobs)

        assert calls == [("sfn.send_task_failure", TOKEN), DELETE_JOB]
        assert result["statusCode"] == 200


@pytest.mark.unit
class TestRunJobChecksTheMarkerAfterTheCreate:
    @pytest.mark.parametrize("missing", ["404", "NoSuchKey"])
    def test_without_a_marker_the_job_is_returned_as_before(self, marker_bucket, missing):
        calls = []
        jobs = FakeJobsApi(calls)

        result, _ = _invoke(_run_job_event(), calls,
                            FakeS3(calls, head_error=_client_error(missing, "HeadObject")), jobs)

        assert calls == [CREATE_JOB, HEAD_MARKER]
        assert list(jobs.jobs) == [JOB_NAME]
        assert result["statusCode"] == 200
        assert result["body"]["status"] == "RUNNING"
        assert result["body"]["k8sJobName"] == JOB_NAME

    def test_a_marker_found_after_the_create_deletes_the_job_and_fails_the_attempt(self, marker_bucket):
        calls = []
        jobs = FakeJobsApi(calls)

        result, _ = _invoke(_run_job_event(), calls,
                            FakeS3(calls, objects=[(MARKER_BUCKET, MARKER_KEY)]), jobs)

        assert calls == [CREATE_JOB, HEAD_MARKER, DELETE_JOB]
        assert jobs.jobs == {}
        assert result["statusCode"] >= 400
        body = result["body"]
        assert body["status"] == "FAILED"
        # RunJobSucceeded? sends a body without k8sJobName to HandleRunJobError.
        assert "k8sJobName" not in body
        assert body["error"]["Error"] == "RunAlreadyEnded"
        assert JOB_NAME in body["error"]["Cause"]

    @pytest.mark.parametrize("marker", [False, True])
    def test_a_job_adopted_after_a_409_is_checked_too(self, marker_bucket, marker):
        """An earlier attempt for this run created the job, so this create is answered 409 and adopts it."""
        calls = []
        jobs = FakeJobsApi(calls, {JOB_NAME: JOB_ID})
        objects = [(MARKER_BUCKET, MARKER_KEY)] if marker else []

        result, _ = _invoke(_run_job_event(), calls, FakeS3(calls, objects=objects), jobs)

        if marker:
            assert calls == [CREATE_JOB, HEAD_MARKER, DELETE_JOB]
            assert jobs.jobs == {}
            assert result["body"]["error"]["Error"] == "RunAlreadyEnded"
            assert "k8sJobName" not in result["body"]
        else:
            assert calls == [CREATE_JOB, HEAD_MARKER]
            assert list(jobs.jobs) == [JOB_NAME]
            assert result["body"]["status"] == "RUNNING"
            assert result["body"]["k8sJobName"] == JOB_NAME

    @pytest.mark.parametrize("head_error", [
        _client_error("403", "HeadObject"),
        _client_error("500", "HeadObject"),
        EndpointConnectionError(endpoint_url="https://s3.us-east-1.amazonaws.com"),
    ])
    def test_a_check_that_cannot_be_made_keeps_the_job_and_logs_a_warning(self, marker_bucket, head_error):
        calls = []
        jobs = FakeJobsApi(calls)

        result, logger = _invoke(_run_job_event(), calls, FakeS3(calls, head_error=head_error), jobs)

        assert calls == [CREATE_JOB, HEAD_MARKER]
        assert list(jobs.jobs) == [JOB_NAME]
        assert result["statusCode"] == 200
        assert result["body"]["k8sJobName"] == JOB_NAME
        assert _logged(logger.warning, MARKER_KEY)

    def test_without_a_marker_bucket_the_job_is_returned_unchecked(self, monkeypatch):
        monkeypatch.delenv("RUN_ENDED_MARKER_BUCKET", raising=False)
        calls = []

        result, logger = _invoke(_run_job_event(), calls, FakeS3(calls), FakeJobsApi(calls))

        assert calls == [CREATE_JOB]
        assert result["body"]["k8sJobName"] == JOB_NAME
        assert _logged(logger.warning, JOB_NAME)


@pytest.mark.unit
class TestALateCreateIsDeleted:
    def test_a_job_created_after_pipeline_end_is_deleted_by_the_invocation_that_created_it(self,
                                                                                         marker_bucket):
        """The race itself: PipelineEnd runs while an earlier RUN_JOB invocation has not yet created the
        job, so its delete finds nothing; that invocation then creates the job and deletes it again. The
        two calls carry different VAMS job names, as nothing but the Kubernetes job name links them."""
        calls = []
        s3 = FakeS3(calls)
        jobs = FakeJobsApi(calls)

        _invoke(_pipeline_end_event(error=True, job_name="PipelineJobEKS_end"), calls, s3, jobs)
        result, _ = _invoke(_run_job_event(job_name="PipelineJobEKS_late"), calls, s3, jobs)

        assert calls == [("sfn.send_task_failure", TOKEN), PUT_MARKER, DELETE_JOB,
                         CREATE_JOB, HEAD_MARKER, DELETE_JOB]
        assert jobs.jobs == {}
        assert result["body"]["error"]["Error"] == "RunAlreadyEnded"


@pytest.mark.unit
class TestMarkerLocation:
    def test_the_key_is_the_job_name_under_the_fixed_prefix_in_the_configured_bucket(self, monkeypatch):
        monkeypatch.setenv("RUN_ENDED_MARKER_BUCKET", "another-auxiliary-bucket")
        handler = _load_handler()

        assert handler.RUN_ENDED_MARKER_PREFIX == MARKER_PREFIX
        assert handler.run_ended_marker_location(JOB_NAME) == ("another-auxiliary-bucket", MARKER_KEY)

    @pytest.mark.parametrize("name", [
        "", None, "unknown", "failure-before-creation", f"../{JOB_NAME}", "Rapid/Pipeline", "a" * 254,
    ])
    def test_a_name_that_is_not_a_job_name_has_no_marker(self, marker_bucket, name):
        assert _load_handler().run_ended_marker_location(name) is None

    def test_no_marker_without_a_configured_bucket(self, monkeypatch):
        monkeypatch.delenv("RUN_ENDED_MARKER_BUCKET", raising=False)

        assert _load_handler().run_ended_marker_location(JOB_NAME) is None
