#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A RapidPipeline EKS execution that ends without PIPELINE_END still has its Kubernetes job deleted.

Stopping the execution (the VAMS execution abort, or a failure elsewhere in the workflow, which stops
every registered sub-process), its state machine timeout, or an error no Catch routes, ends the run
without PIPELINE_END, the only state that deletes the job. Amazon EventBridge then invokes the handler
with EXECUTION_END_CLEANUP for that execution's status change.

The cleanup finds the run's jobs from records CONSTRUCT_PIPELINE writes, one empty object per job name
under `pipelines/rapid-pipeline-eks/run-jobs/<execution name>/`, written before it returns the manifest,
so before any RUN_JOB can create the job. For each job it writes the run-ended marker, then deletes the
job, so a RUN_JOB still running deletes a job it creates afterwards.

The Amazon S3 and Kubernetes Jobs API stand-ins below append every call they receive to one list, so an
assertion on that list is an assertion on the order the handler made the calls in.
"""

import importlib
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
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

BUCKET = "test-auxiliary-bucket"
RECORD_PREFIX = "pipelines/rapid-pipeline-eks/run-jobs/"
MARKER_PREFIX = "pipelines/rapid-pipeline-eks/run-ended/"
STATE_MACHINE_ARN = "arn:aws:states:us-east-1:123456789012:stateMachine:rapid-pipeline-eks-vams-test"
EXECUTION_NAME = "PipelineJobEKS_20261024_155400_123_ab12cd34"
EXECUTION_ARN = ("arn:aws:states:us-east-1:123456789012:execution:rapid-pipeline-eks-vams-test:"
                 + EXECUTION_NAME)
JOB_A = "rapid-pipeline-job-a1b2c3d4e5f60718293a4b5c6d7e8f90"
JOB_B = "rapid-pipeline-job-ffff0000ffff0000ffff0000ffff0000"


def _record(job, execution=EXECUTION_NAME):
    return (BUCKET, f"{RECORD_PREFIX}{execution}/{job}")


def _put_marker(job):
    return ("s3.put_object", BUCKET, f"{MARKER_PREFIX}{job}")


def _list(token=None):
    return ("s3.list_objects_v2", BUCKET, f"{RECORD_PREFIX}{EXECUTION_NAME}/", token)


def _delete(job):
    return ("k8s.delete", job)


def _client_error(code, operation):
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class FakeS3:
    """put_object, head_object, get_object and list_objects_v2 against an in-memory set of objects.

    `page_size` bounds a listing page, so a listing of more keys is truncated and continued.
    """

    def __init__(self, calls, objects=(), put_error=None, list_error=None, page_size=1000):
        self.calls = calls
        self.objects = set(objects)
        self.put_error = put_error
        self.list_error = list_error
        self.page_size = page_size

    def put_object(self, Bucket, Key, **kwargs):
        self.calls.append(("s3.put_object", Bucket, Key))
        if self.put_error:
            raise self.put_error
        self.objects.add((Bucket, Key))
        return {}

    def head_object(self, Bucket, Key, **kwargs):
        self.calls.append(("s3.head_object", Bucket, Key))
        if (Bucket, Key) not in self.objects:
            raise _client_error("404", "HeadObject")
        return {}

    def get_object(self, Bucket, Key, **kwargs):
        # CONSTRUCT_PIPELINE's input-configuration read; no configuration exists in these runs.
        raise _client_error("NoSuchKey", "GetObject")

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None, **kwargs):
        self.calls.append(("s3.list_objects_v2", Bucket, Prefix, ContinuationToken))
        if self.list_error:
            raise self.list_error
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + self.page_size]
        truncated = start + self.page_size < len(keys)
        response = {"Contents": [{"Key": k} for k in page], "KeyCount": len(page), "IsTruncated": truncated}
        if truncated:
            response["NextContinuationToken"] = str(start + self.page_size)
        return response


def _job(name, job_id):
    return SimpleNamespace(metadata=SimpleNamespace(name=name, labels={"job-id": job_id}))


class FakeJobsApi:
    """The BatchV1Api calls create_job and the job cleanup make; `jobs` maps a job name to its job-id."""

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
    with patch.object(handler, "s3", s3), patch.object(handler, "sfn", MagicMock()), \
            patch.object(handler, "logger", logger), \
            patch.object(ku, "get_batch_v1_api", return_value=jobs), \
            patch("time.sleep", MagicMock()):
        result = handler.lambda_handler(event, _context())
    return result, logger


def _logged(mock_method, needle):
    return any(needle in str(call.args[0]) for call in mock_method.call_args_list if call.args)


_OMITTED = object()


def _construct_event(execution_name=EXECUTION_NAME):
    event = {
        "operation": "CONSTRUCT_PIPELINE",
        "jobName": "PipelineJobEKS_x",
        "inputS3AssetFilePath": "s3://abkt/xidM/test/pump.glb",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/test/pump.glb/eks/p1",
        "inputMetadataS3Location": "s3://abkt/.../metadata.json",
        "inputConfigurationS3Location": "s3://abkt/.../config.json",
        "externalSfnTaskToken": "tok",
        "outputFileType": ".usdz",
    }
    if execution_name is not _OMITTED:
        event["executionName"] = execution_name
    return event


def _cleanup_event(status="ABORTED", state_machine_arn=STATE_MACHINE_ARN, execution_arn=EXECUTION_ARN,
                   execution_name=EXECUTION_NAME):
    """The input the EventBridge rule's target builds from a status-change event."""
    return {"operation": "EXECUTION_END_CLEANUP", "executionArn": execution_arn,
            "executionName": execution_name, "stateMachineArn": state_machine_arn, "status": status}


@pytest.fixture
def deployed(monkeypatch):
    """The two variables CDK sets on the consolidated handler for the run records and the cleanup."""
    monkeypatch.setenv("RUN_ENDED_MARKER_BUCKET", BUCKET)
    monkeypatch.setenv("STATE_MACHINE_ARN", STATE_MACHINE_ARN)


@pytest.mark.unit
class TestConstructPipelineRecordsTheJob:
    def test_the_record_names_the_execution_and_the_job_in_the_manifest(self, deployed):
        calls = []
        s3 = FakeS3(calls)

        result, _ = _invoke(_construct_event(), calls, s3, FakeJobsApi(calls))

        job = result["jobManifest"]["metadata"]["name"]
        assert calls == [("s3.put_object",) + _record(job)]
        assert "error" not in result

    def test_each_attempt_records_its_own_job(self, deployed):
        """A retried ConstructPipeline draws a new job id; both names are recorded, and kept."""
        calls = []
        s3 = FakeS3(calls)

        first, _ = _invoke(_construct_event(), calls, s3, FakeJobsApi(calls))
        second, _ = _invoke(_construct_event(), calls, s3, FakeJobsApi(calls))

        names = {first["jobManifest"]["metadata"]["name"], second["jobManifest"]["metadata"]["name"]}
        assert len(names) == 2
        assert {k for _, k in s3.objects} == {_record(n)[1] for n in names}

    @pytest.mark.parametrize("execution_name", [
        _OMITTED, "", None, "a/b", "has space", "x" * 81, "../escape", "name:with:colons",
        f"{EXECUTION_NAME}\n",
    ])
    def test_no_record_without_a_usable_execution_name(self, deployed, execution_name):
        calls = []

        result, _ = _invoke(_construct_event(execution_name), calls, FakeS3(calls), FakeJobsApi(calls))

        assert calls == []
        assert "jobManifest" in result

    def test_no_record_without_a_bucket(self, monkeypatch):
        monkeypatch.delenv("RUN_ENDED_MARKER_BUCKET", raising=False)
        calls = []

        result, _ = _invoke(_construct_event(), calls, FakeS3(calls), FakeJobsApi(calls))

        assert calls == []
        assert "jobManifest" in result

    def test_a_failed_record_write_still_returns_the_manifest(self, deployed):
        calls = []

        result, logger = _invoke(_construct_event(), calls,
                                 FakeS3(calls, put_error=_client_error("AccessDenied", "PutObject")),
                                 FakeJobsApi(calls))

        job = result["jobManifest"]["metadata"]["name"]
        assert calls == [("s3.put_object",) + _record(job)]
        assert "error" not in result
        assert _logged(logger.error, _record(job)[1])


@pytest.mark.unit
class TestCleanupDeletesTheRunsJobs:
    @pytest.mark.parametrize("status", ["ABORTED", "TIMED_OUT", "FAILED"])
    def test_each_recorded_job_gets_its_marker_and_then_its_delete(self, deployed, status):
        calls = []
        s3 = FakeS3(calls, objects=[_record(JOB_A), _record(JOB_B)])
        jobs = FakeJobsApi(calls, {JOB_A: "a", JOB_B: "b"})

        result, _ = _invoke(_cleanup_event(status=status), calls, s3, jobs)

        assert calls == [_list(), _put_marker(JOB_A), _delete(JOB_A), _put_marker(JOB_B), _delete(JOB_B)]
        assert jobs.jobs == {}
        assert result["statusCode"] == 200
        assert [j["k8sJobName"] for j in result["body"]["jobs"]] == [JOB_A, JOB_B]
        assert all(j["markerWritten"] and j["deleted"] for j in result["body"]["jobs"])

    def test_a_job_that_is_already_gone_is_fine(self, deployed):
        """The usual FAILED case, where PipelineEnd ran, and a record of a job RUN_JOB never created."""
        calls = []
        jobs = FakeJobsApi(calls)

        result, _ = _invoke(_cleanup_event(status="FAILED"), calls, FakeS3(calls, objects=[_record(JOB_A)]),
                            jobs)

        assert calls == [_list(), _put_marker(JOB_A), _delete(JOB_A)]
        assert result["statusCode"] == 200
        assert result["body"]["jobs"] == [{"k8sJobName": JOB_A, "markerWritten": True, "deleted": True}]

    def test_a_run_without_records_needs_nothing(self, deployed):
        """The run ended before ConstructPipeline recorded a job, so no RUN_JOB was started."""
        calls = []

        result, _ = _invoke(_cleanup_event(), calls,
                            FakeS3(calls, objects=[_record(JOB_A, execution="PipelineJobEKS_other")]),
                            FakeJobsApi(calls, {JOB_A: "a"}))

        assert calls == [_list()]
        assert result["statusCode"] == 200
        assert result["body"]["jobs"] == []

    def test_a_listing_of_several_pages_is_read_to_the_end(self, deployed):
        calls = []
        job_c = "rapid-pipeline-job-0000aaaa0000aaaa0000aaaa0000aaaa"
        s3 = FakeS3(calls, objects=[_record(JOB_A), _record(JOB_B), _record(job_c)], page_size=2)

        result, _ = _invoke(_cleanup_event(), calls, s3, FakeJobsApi(calls))

        assert calls[:2] == [_list(), _list("2")]
        assert sorted(j["k8sJobName"] for j in result["body"]["jobs"]) == sorted([JOB_A, JOB_B, job_c])

    def test_a_key_that_is_not_a_job_name_is_skipped(self, deployed):
        calls = []
        s3 = FakeS3(calls, objects=[_record(JOB_A), (BUCKET, f"{RECORD_PREFIX}{EXECUTION_NAME}/nested/x"),
                                    (BUCKET, f"{RECORD_PREFIX}{EXECUTION_NAME}/Not_A_Job"),
                                    (BUCKET, f"{RECORD_PREFIX}{EXECUTION_NAME}/{JOB_B}\n")])

        result, _ = _invoke(_cleanup_event(), calls, s3, FakeJobsApi(calls, {JOB_A: "a", JOB_B: "b"}))

        assert calls == [_list(), _put_marker(JOB_A), _delete(JOB_A)]
        assert [j["k8sJobName"] for j in result["body"]["jobs"]] == [JOB_A]

    def test_a_failed_marker_write_still_deletes_the_job(self, deployed):
        calls = []
        s3 = FakeS3(calls, objects=[_record(JOB_A)], put_error=_client_error("AccessDenied", "PutObject"))
        jobs = FakeJobsApi(calls, {JOB_A: "a"})

        result, _ = _invoke(_cleanup_event(), calls, s3, jobs)

        assert calls == [_list(), _put_marker(JOB_A), _delete(JOB_A)]
        assert jobs.jobs == {}
        assert result["body"]["jobs"] == [{"k8sJobName": JOB_A, "markerWritten": False, "deleted": True}]

    def test_a_listing_that_fails_deletes_nothing_and_reports_it(self, deployed):
        calls = []

        result, logger = _invoke(_cleanup_event(), calls,
                                 FakeS3(calls, list_error=_client_error("InternalError", "ListObjectsV2")),
                                 FakeJobsApi(calls, {JOB_A: "a"}))

        assert calls == [_list()]
        assert result["statusCode"] >= 500
        assert _logged(logger.error, EXECUTION_NAME)


@pytest.mark.unit
class TestCleanupRejectsAnyOtherEvent:
    @pytest.mark.parametrize("state_machine_arn", [
        "arn:aws:states:us-east-1:123456789012:stateMachine:another-state-machine",
        "arn:aws:states:us-east-1:210987654321:stateMachine:rapid-pipeline-eks-vams-test",
        "arn:aws:states:us-west-2:123456789012:stateMachine:rapid-pipeline-eks-vams-test",
        "arn:aws-us-gov:states:us-east-1:123456789012:stateMachine:rapid-pipeline-eks-vams-test",
        "", None,
    ])
    def test_an_event_for_another_state_machine_touches_nothing(self, deployed, state_machine_arn):
        calls = []
        jobs = FakeJobsApi(calls, {JOB_A: "a"})

        result, logger = _invoke(_cleanup_event(state_machine_arn=state_machine_arn), calls,
                                 FakeS3(calls, objects=[_record(JOB_A)]), jobs)

        assert calls == []
        assert result["statusCode"] == 403
        assert list(jobs.jobs) == [JOB_A]
        assert logger.error.called

    def test_without_its_own_state_machine_arn_the_handler_touches_nothing(self, monkeypatch):
        monkeypatch.setenv("RUN_ENDED_MARKER_BUCKET", BUCKET)
        monkeypatch.delenv("STATE_MACHINE_ARN", raising=False)
        calls = []

        result, _ = _invoke(_cleanup_event(), calls, FakeS3(calls, objects=[_record(JOB_A)]),
                            FakeJobsApi(calls, {JOB_A: "a"}))

        assert calls == []
        assert result["statusCode"] == 403

    @pytest.mark.parametrize("status", ["RUNNING", "SUCCEEDED", "PENDING_REDRIVE", "", None])
    def test_a_status_that_needs_no_cleanup_touches_nothing(self, deployed, status):
        """A RUNNING execution's job is in use, and a SUCCEEDED run went through PipelineEndSuccess."""
        calls = []

        result, _ = _invoke(_cleanup_event(status=status), calls, FakeS3(calls, objects=[_record(JOB_A)]),
                            FakeJobsApi(calls, {JOB_A: "a"}))

        assert calls == []
        assert result["statusCode"] == 400

    @pytest.mark.parametrize("execution_arn, execution_name", [
        (EXECUTION_ARN.replace("rapid-pipeline-eks-vams-test", "another-state-machine"), EXECUTION_NAME),
        (EXECUTION_ARN, "PipelineJobEKS_another"),
        (EXECUTION_ARN + "/x", EXECUTION_NAME + "/x"),
        (None, EXECUTION_NAME),
        (EXECUTION_ARN, None),
        (f"{EXECUTION_ARN}\n", f"{EXECUTION_NAME}\n"),
    ])
    def test_an_execution_that_is_not_this_state_machines_touches_nothing(self, deployed, execution_arn,
                                                                         execution_name):
        calls = []

        result, _ = _invoke(_cleanup_event(execution_arn=execution_arn, execution_name=execution_name),
                            calls, FakeS3(calls, objects=[_record(JOB_A)]), FakeJobsApi(calls, {JOB_A: "a"}))

        assert calls == []
        assert result["statusCode"] == 400


@pytest.mark.unit
class TestAStoppedRunLeavesNoJob:
    def _manifest_from_construct(self, calls, s3, jobs):
        result, _ = _invoke(_construct_event(), calls, s3, jobs)
        return result["jobManifest"]

    def _run_job(self, manifest, calls, s3, jobs):
        event = {"operation": "RUN_JOB", "jobName": "PipelineJobEKS_x", "jobManifest": manifest,
                 "externalSfnTaskToken": "tok"}
        result, _ = _invoke(event, calls, s3, jobs)
        return result

    def test_a_job_running_when_the_execution_is_stopped_is_deleted(self, deployed):
        calls = []
        s3 = FakeS3(calls)
        jobs = FakeJobsApi(calls)
        manifest = self._manifest_from_construct(calls, s3, jobs)
        job = manifest["metadata"]["name"]
        assert self._run_job(manifest, calls, s3, jobs)["body"]["k8sJobName"] == job
        assert list(jobs.jobs) == [job]

        _invoke(_cleanup_event(), calls, s3, jobs)

        assert jobs.jobs == {}

    def test_a_job_created_after_the_cleanup_deletes_itself(self, deployed):
        """The execution is stopped while a RUN_JOB invocation is still running, and that invocation
        creates the job only after the cleanup has run."""
        calls = []
        s3 = FakeS3(calls)
        jobs = FakeJobsApi(calls)
        manifest = self._manifest_from_construct(calls, s3, jobs)
        job = manifest["metadata"]["name"]

        _invoke(_cleanup_event(), calls, s3, jobs)
        late = self._run_job(manifest, calls, s3, jobs)

        assert late["body"]["error"]["Error"] == "RunAlreadyEnded"
        assert jobs.jobs == {}
        assert calls[-3:] == [("k8s.create", job), ("s3.head_object", BUCKET, f"{MARKER_PREFIX}{job}"),
                              _delete(job)]


@pytest.mark.unit
class TestRecordLocation:
    def test_the_records_are_keyed_by_execution_under_the_fixed_prefix_in_the_configured_bucket(self,
                                                                                            monkeypatch):
        monkeypatch.setenv("RUN_ENDED_MARKER_BUCKET", "another-auxiliary-bucket")
        handler = _load_handler()

        assert handler.RUN_JOBS_RECORD_PREFIX == RECORD_PREFIX
        assert handler.run_jobs_record_prefix(EXECUTION_NAME) == (
            "another-auxiliary-bucket", f"{RECORD_PREFIX}{EXECUTION_NAME}/")
        assert handler.EXECUTION_END_STATUSES == frozenset({"ABORTED", "TIMED_OUT", "FAILED"})

    def test_a_name_with_a_trailing_newline_gets_no_record(self, deployed):
        """The name checks match the whole name, so a trailing newline is not a usable name."""
        calls = []
        s3 = FakeS3(calls)
        handler = _load_handler()

        with patch.object(handler, "s3", s3), patch.object(handler, "logger", MagicMock()):
            assert handler.run_jobs_record_prefix(f"{EXECUTION_NAME}\n") is None
            assert handler.write_run_job_record(EXECUTION_NAME, f"{JOB_A}\n") is False
            assert handler.write_run_job_record(f"{EXECUTION_NAME}\n", JOB_A) is False

        assert calls == []
