#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""`create_job` adopts the job an earlier create attempt for the same run made.

`create_job` sends the job create through `with_retries`, which resends after a server error, a dropped
connection or a timeout. The API server can apply a create and still answer 504, the connection can drop
before the answer arrives, and the client's read timeout can fire after the request was sent, so the
resend finds the job and is answered 409. CONSTRUCT_PIPELINE generates a random 128-bit `job-id` per run,
builds the job name from it and labels the job with it, so a job of that name carrying the same label is
this run's job: `create_job` returns it. A job of that name with another `job-id`, or one that cannot be
read back to compare, is a conflict as before.

These tests run the real Kubernetes client against a local HTTP server that keeps jobs the way the API
server does, so they count the requests actually sent.
"""

import copy
import importlib
import json
import os
import re
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest
from kubernetes import client as k8s_client

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

for _key, _value in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "EKS_CLUSTER_NAME": "test-cluster",
    "CONTAINER_IMAGE_URI": "example.com/rapid-pipeline:test",
    "KUBERNETES_NAMESPACE": "default",
}.items():
    os.environ.setdefault(_key, _value)

import kubernetes_utils as ku  # noqa: E402

NAMESPACE = "default"
JOB_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
JOB_NAME = f"rapid-pipeline-job-{JOB_ID}"
OTHER_JOB_ID = "ffff0000ffff0000ffff0000ffff0000"
JOBS_PATH = f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs"
READ_JOB = ("GET", f"{JOBS_PATH}/{JOB_NAME}")
CONFLICT_ERROR = f"Job {JOB_NAME} already exists (conflict)"


def _manifest(job_id=JOB_ID):
    """A job manifest shaped like CONSTRUCT_PIPELINE's: the name and the job-id label share the run's id."""
    labels = {"app": "rapid-pipeline", "createdBy": "vams", "pipeline-type": "rapidPipelineEKS"}
    if job_id is not None:
        labels["job-id"] = job_id
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": JOB_NAME, "labels": labels},
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


def _status_body(code, reason, message):
    return {"kind": "Status", "apiVersion": "v1", "status": "Failure", "reason": reason, "code": code,
            "message": message}


class _JobsApiServer:
    """Local Kubernetes API server keeping batch/v1 Jobs in memory.

    A POST for a name that is already stored is answered 409 AlreadyExists, as Kubernetes does. Any other
    POST takes the next entry of `create_outcomes`, `(status, applied, delay)`: an applied create stores
    the job before the answer is sent, so a client that gives up or sees an error status still leaves the
    job behind, and a status of None closes the connection without an answer. Once the entries are used,
    a create is applied and answered 201.

    A GET of a job answers 200 with the stored job, or 404. `served_job_id` replaces the job-id label on
    every job served, and `get_status` answers every GET with that error status instead.
    """

    def __init__(self, create_outcomes=(), served_job_id=None, get_status=None):
        self.jobs = {}
        self.requests = []
        self._outcomes = list(create_outcomes)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, body):
                data = json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client stopped waiting

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(("POST", self.path))
                name = body["metadata"]["name"]
                if name in outer.jobs:
                    self._send(409, _status_body(409, "AlreadyExists", f'jobs.batch "{name}" already exists'))
                    return
                status, applied, delay = outer._outcomes.pop(0) if outer._outcomes else (201, True, 0)
                if applied:
                    outer.jobs[name] = body
                if delay:
                    threading.Event().wait(delay)
                if status is None:
                    self.close_connection = True
                    return
                self._send(status, body if status == 201 else _status_body(status, "Timeout", "request timed out"))

            def do_GET(self):
                outer.requests.append(("GET", self.path))
                if get_status:
                    self._send(get_status, _status_body(get_status, "InternalError", "internal error"))
                    return
                name = self.path.rsplit("/", 1)[-1]
                if not self.path.startswith(JOBS_PATH + "/") or name not in outer.jobs:
                    self._send(404, _status_body(404, "NotFound", f'jobs.batch "{name}" not found'))
                    return
                job = copy.deepcopy(outer.jobs[name])
                if served_job_id is not None:
                    job["metadata"]["labels"]["job-id"] = served_job_id
                job["status"] = {"active": 1}
                self._send(200, job)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]

    @property
    def requests_after_the_last_post(self):
        last_post = max(i for i, r in enumerate(self.requests) if r[0] == "POST")
        return self.requests[last_post + 1:]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _api_client(url):
    """The client get_k8s_client builds, pointed at a local server (no TLS, no EKS token)."""
    configuration = k8s_client.Configuration()
    configuration.host = url
    configuration.connection_pool_maxsize = 20
    configuration.retries = ku.k8s_retry_policy()
    return ku.new_k8s_api_client(k8s_client, configuration)


@pytest.fixture
def jobs_api(monkeypatch):
    """Start a `_JobsApiServer` and point kubernetes_utils' client at it, with retry backoff removed."""
    servers = []

    def start(**kwargs):
        server = _JobsApiServer(**kwargs)
        servers.append(server)
        monkeypatch.setattr(ku, "get_k8s_client", lambda: _api_client(server.url))
        return server

    monkeypatch.setattr(ku.time, "sleep", lambda seconds: None)
    yield start
    for server in servers:
        server.close()


@pytest.mark.unit
class TestCreateAdoptsTheJobAnEarlierAttemptMade:
    def test_a_create_answered_504_is_adopted_on_the_conflicting_resend(self, jobs_api):
        server = jobs_api(create_outcomes=[(504, True, 0)])

        assert ku.create_job(_manifest(), namespace=NAMESPACE) == JOB_NAME
        assert len(server.posts) == 2
        assert list(server.jobs) == [JOB_NAME]

    def test_a_create_whose_answer_outlived_the_read_timeout_is_adopted(self, jobs_api, monkeypatch):
        monkeypatch.setattr(ku, "K8S_REQUEST_TIMEOUT", (1, 0.5))
        server = jobs_api(create_outcomes=[(201, True, 1.5)])

        assert ku.create_job(_manifest(), namespace=NAMESPACE) == JOB_NAME
        assert len(server.posts) == 2

    def test_a_create_whose_connection_dropped_before_the_answer_is_adopted(self, jobs_api):
        server = jobs_api(create_outcomes=[(None, True, 0)])

        assert ku.create_job(_manifest(), namespace=NAMESPACE) == JOB_NAME
        assert len(server.posts) == 2
        assert list(server.jobs) == [JOB_NAME]

    def test_a_first_create_answered_409_adopts_this_runs_job(self, jobs_api):
        """The job already exists before this call, as after a repeated invocation of the same RUN_JOB:
        the name is generated per run, so a job carrying the manifest's job-id is still this run's job.
        The existence check ahead of the create finds it and only logs, so the create is sent."""
        server = jobs_api()
        server.jobs[JOB_NAME] = _manifest()

        assert ku.create_job(_manifest(), namespace=NAMESPACE) == JOB_NAME
        assert len(server.posts) == 1

    def test_a_create_that_succeeds_is_sent_once(self, jobs_api):
        """The control: with no conflict the create is the only POST and returns the name."""
        server = jobs_api()

        assert ku.create_job(_manifest(), namespace=NAMESPACE) == JOB_NAME
        assert len(server.posts) == 1


@pytest.mark.unit
class TestAnotherJobOfTheSameNameIsStillAConflict:
    def test_a_conflicting_job_with_another_job_id_fails(self, jobs_api):
        server = jobs_api(create_outcomes=[(504, True, 0)], served_job_id=OTHER_JOB_ID)

        with pytest.raises(ValueError, match=r"already exists \(conflict\)") as err:
            ku.create_job(_manifest(), namespace=NAMESPACE)
        assert str(err.value) == CONFLICT_ERROR
        assert len(server.posts) == 2
        assert server.requests_after_the_last_post == [READ_JOB]

    def test_a_pre_existing_job_this_run_did_not_create_fails(self, jobs_api):
        """A job of this name that carries another job-id, such as one made outside the pipeline, is a
        conflict: the job read after the 409 is compared, whatever the existence check found."""
        server = jobs_api()
        server.jobs[JOB_NAME] = _manifest(job_id=OTHER_JOB_ID)

        with pytest.raises(ValueError) as err:
            ku.create_job(_manifest(), namespace=NAMESPACE)
        assert str(err.value) == CONFLICT_ERROR
        assert len(server.posts) == 1
        assert server.requests_after_the_last_post == [READ_JOB]

    def test_without_a_job_id_label_in_the_manifest_nothing_is_adopted(self, jobs_api):
        """A missing label on both sides must not read as a match, so the job is not even read."""
        server = jobs_api()
        server.jobs[JOB_NAME] = _manifest(job_id=None)

        with pytest.raises(ValueError) as err:
            ku.create_job(_manifest(job_id=None), namespace=NAMESPACE)
        assert str(err.value) == CONFLICT_ERROR
        assert len(server.posts) == 1
        assert server.requests_after_the_last_post == []

    def test_a_conflicting_job_that_cannot_be_read_fails(self, jobs_api):
        """The read after the 409 takes the module's read budget of 2 attempts, and no more."""
        server = jobs_api(create_outcomes=[(504, True, 0)], get_status=500)

        with pytest.raises(ValueError) as err:
            ku.create_job(_manifest(), namespace=NAMESPACE)
        assert str(err.value) == CONFLICT_ERROR
        assert len(server.posts) == 2
        assert server.requests_after_the_last_post == [READ_JOB] * 2


def _load_handler():
    if "consolidated_handler" in sys.modules:
        return importlib.reload(sys.modules["consolidated_handler"])
    return importlib.import_module("consolidated_handler")


def _context():
    context = MagicMock()
    context.aws_request_id = "req-test"
    context.get_remaining_time_in_millis.return_value = 600000
    return context


@pytest.mark.unit
class TestEachRunGetsItsOwnJobId:
    """Adoption takes a job with this run's name and job-id label as this run's own, which holds only while
    two runs never draw the same id: CONSTRUCT_PIPELINE's id is a random 128-bit value, and the job name,
    labels, annotation and JOB_ID env var all carry it."""

    def _construct(self, handler):
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("AccessDenied")  # no per-pipeline config to read
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
        with patch.object(handler, "s3", s3):
            return handler.lambda_handler(event, _context())["jobManifest"]

    def test_the_job_id_is_a_random_128_bit_value_shared_by_name_labels_and_env(self):
        handler = _load_handler()
        manifest = self._construct(handler)
        job_id = manifest["metadata"]["labels"]["job-id"]
        container = manifest["spec"]["template"]["spec"]["containers"][0]

        assert re.fullmatch(r"[0-9a-f]{32}", job_id), job_id
        assert manifest["metadata"]["name"] == f"rapid-pipeline-job-{job_id}"
        assert len(manifest["metadata"]["name"]) <= 63
        assert manifest["metadata"]["annotations"]["vams.aws.amazon.com/job-id"] == job_id
        assert manifest["spec"]["template"]["metadata"]["labels"]["job-id"] == job_id
        assert {"name": "JOB_ID", "value": job_id} in container["env"]
        assert self._construct(handler)["metadata"]["labels"]["job-id"] != job_id


@pytest.mark.unit
class TestRunJobReportsTheAdoptedJob:
    def test_run_job_returns_running_with_the_job_name_after_a_504_create(self, jobs_api):
        """RUN_JOB's body is what the state machine routes on: a RUNNING status carrying k8sJobName goes
        on to poll the job, where a failure would set k8sJobName to failure-before-creation."""
        handler = _load_handler()
        assert handler.create_job is ku.create_job, "the handler must call the kubernetes_utils patched here"
        server = jobs_api(create_outcomes=[(504, True, 0)])

        result = handler.lambda_handler(
            {"operation": "RUN_JOB", "jobName": "PipelineJobEKS_x", "jobManifest": _manifest(),
             "externalSfnTaskToken": ""},
            _context(),
        )

        assert result["statusCode"] == 200, result
        assert result["body"]["status"] == "RUNNING"
        assert result["body"]["k8sJobName"] == JOB_NAME
        assert len(server.posts) == 2
