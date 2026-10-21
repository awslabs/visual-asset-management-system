# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Claims, events, fixture access and fakes shared by the edhContainerService tests.

The handler is driven through ``lambda_handler`` with its module attributes replaced:
``request_to_claims`` (runs the real ``normalize_event`` first so REST-shaped events are covered),
``CasbinEnforcer``, ``EdhClient``, the ``log_actions`` audit hook and, when a test asks, the
``eks_enabled`` switch. ``FakeEdhClient`` answers each client wrapper from a script and records
every call, so a test can assert that an EDH mutation was -- or was not -- attempted. Record shapes
follow the sanitized captures: a Batch job is the boto3 describe shape plus the client's
``job_handle``; an EKS job is the Kubernetes Job dict EDH returns plus ``eks_cluster``, ``images``
and ``job_handle``; an EKS cluster is its bare name.
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from common.auth.apiEvent import normalize_event

import backend.backend.handlers.addon.edh.edhContainerService as service
import backend.backend.handlers.addon.edh.common.edhClient as edh_client

USER = "alice"
USER_ROLE = "edhUser"

USER_CLAIMS = {"tokens": [USER], "roles": [USER_ROLE], "mfaEnabled": True, "authMethod": "jwt"}
NO_IDENTITY_CLAIMS = {"tokens": [], "roles": [], "mfaEnabled": False, "authMethod": "jwt"}
SYSTEM_CLAIMS = {"tokens": ["SYSTEM_USER"], "roles": [], "mfaEnabled": True, "authMethod": "jwt"}

CATALOG_PATH = "/addon/edh/containers/catalog"
JOBS_PATH = "/addon/edh/containers/jobs"

BATCH_JOB_ID = "3f2a9c1e-4b7d-4e0a-9d2b-6c1f8a7e5b30"
JOB_DEFINITION_ARN = "arn:aws:batch:us-east-1:123456789012:job-definition/sim-train:3"
EKS_CLUSTER = "edh-eks-a"
EKS_IMAGE = "123456789012.dkr.ecr.us-east-1.amazonaws.com/vams-t1-edh-sim-worker:abc"

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def job_path(job_id):
    return f"{JOBS_PATH}/{job_id}"


def _authorizer_claims():
    return {
        "vams:tokens": json.dumps([USER]),
        "vams:roles": json.dumps([USER_ROLE]),
        "vams:authMethod": "jwt",
    }


def make_event(method, path, *, body=None, path_params=None, query=None, rest=False):
    """An API Gateway event. ``rest=True`` builds the REST (v1) proxy shape with explicit nulls.

    ``body`` may be a dict (JSON-encoded) or a string (sent as-is, for malformed-body cases).
    """
    if rest:
        event = {
            "httpMethod": method,
            "path": path,
            "pathParameters": path_params,
            "queryStringParameters": query,
            "requestContext": {
                "identity": {"sourceIp": "10.0.0.1"},
                "authorizer": _authorizer_claims(),
            },
            "headers": {"Authorization": "Bearer test-token"},
        }
    else:
        event = {
            "requestContext": {
                "http": {"method": method, "path": path},
                "authorizer": {"jwt": {"claims": _authorizer_claims()}},
            },
            "headers": {"authorization": "Bearer test-token"},
        }
        if path_params is not None:
            event["pathParameters"] = path_params
        if query is not None:
            event["queryStringParameters"] = query
    if body is not None:
        event["body"] = body if isinstance(body, str) else json.dumps(body)
    return event


def response_body(response):
    return json.loads(response["body"])


def fixture_document(file_name):
    """A sanitized EDH capture by its registry name, or a skip when the spike did not write it."""
    path = os.path.join(FIXTURES_DIR, file_name)
    if not os.path.exists(path):
        pytest.skip(f"the spike did not capture {file_name}")
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def fixture_records(file_name, key_field=None):
    """The EDH records of a capture, unwrapped the way the client unwraps a response."""
    fixture = fixture_document(file_name)
    return edh_client._as_list(edh_client._payload(fixture["response"]["json"]), key_field=key_field)


def batch_job(**overrides):
    """A Batch job as the caller's listing returns it: the describe shape plus the client's handle."""
    record = {
        "jobId": BATCH_JOB_ID,
        "jobName": "render-1",
        "jobArn": f"arn:aws:batch:us-east-1:123456789012:job/{BATCH_JOB_ID}",
        "jobQueue": "arn:aws:batch:us-east-1:123456789012:job-queue/gpu-queue",
        "jobQueueName": "gpu-queue",
        "jobDefinition": JOB_DEFINITION_ARN,
        "status": "RUNNING",
        "createdAt": 1789041600000,
        "tags": {"edh:JobOwner": "vabc", "edh:ClusterId": "edh-test"},
    }
    record.update(overrides)
    record.setdefault(edh_client.EDH_JOB_HANDLE_FIELD, f"batch:{record['jobId']}")
    return record


def eks_job(name="render-eks", *, cluster=EKS_CLUSTER, namespace="default", status=None, **overrides):
    """An EKS job as the caller's listing returns it (the uid key already injected by the client)."""
    record = {
        "uid": "ab12cd34-ef56-4a78-9b0c-1d2e3f4a5b6c",
        "metadata": {"name": name, "namespace": namespace, "uid": "ab12cd34-ef56-4a78-9b0c-1d2e3f4a5b6c",
                     "labels": {"edh_JobOwner": "vabc", "edh_ClusterId": "edh-test"}},
        "status": status if status is not None else {"active": 1},
        "pod_ips": ["10.0.0.1"],
        "images": [EKS_IMAGE],
        "eks_cluster": cluster,
        edh_client.EDH_JOB_HANDLE_FIELD: f"eks:{cluster}/{namespace}/{name}",
    }
    record.update(overrides)
    return record


class FakeEdhClient:
    """A scripted stand-in for ``EdhClient``.

    ``results`` maps a wrapper name to the value it returns, the exception instance it raises, or a
    callable of the wrapper's arguments. Every call is recorded as ``(wrapper, args)``; keyword
    arguments are appended to ``args`` in the order the wrapper declares them, only when given.
    """

    def __init__(self, **results):
        self.results = {
            "list_batch_job_definitions": [],
            "list_batch_queues": [],
            "list_eks_clusters": [],
            "submit_job": {"job_id": BATCH_JOB_ID, "job_handle": f"batch:{BATCH_JOB_ID}",
                           "message": f"Job render-1 submitted successfully. Job ID: {BATCH_JOB_ID}"},
            "list_jobs": [],
            "kill_job": {"message": f"Job {BATCH_JOB_ID} terminated successfully."},
        }
        self.results.update(results)
        self.calls = []

    def _answer(self, wrapper, *args):
        self.calls.append((wrapper, args))
        result = self.results[wrapper]
        if callable(result):
            result = result(*args)
        if isinstance(result, Exception):
            raise result
        return result

    def list_batch_job_definitions(self, status=None):
        return self._answer("list_batch_job_definitions")

    def list_batch_queues(self):
        return self._answer("list_batch_queues")

    def list_eks_clusters(self):
        return self._answer("list_eks_clusters")

    def submit_job(self, edh_uid, backend, form):
        return self._answer("submit_job", edh_uid, backend, form)

    def list_jobs(self, edh_uid, backend, *, cluster=None, namespace=None):
        extra = (cluster, namespace) if cluster is not None or namespace is not None else ()
        return self._answer("list_jobs", edh_uid, backend, *extra)

    def kill_job(self, edh_uid, backend, job_id):
        return self._answer("kill_job", edh_uid, backend, job_id)

    def calls_to(self, wrapper):
        return [args for name, args in self.calls if name == wrapper]


def install_container_fakes(monkeypatch, *, claims=None, edh=None, enforce=None, eks_enabled=None):
    """Replace the handler module's collaborators; returns the doubles installed."""
    claims = dict(USER_CLAIMS if claims is None else claims)
    edh = edh if edh is not None else FakeEdhClient()

    def claims_for(event):
        normalize_event(event)
        return dict(claims)

    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.side_effect = enforce if enforce is not None else (lambda obj, act: True)
    enforcer_factory = MagicMock(return_value=enforcer)
    client_factory = MagicMock(return_value=edh)
    log_actions = MagicMock()

    monkeypatch.setattr(service, "request_to_claims", claims_for)
    monkeypatch.setattr(service, "CasbinEnforcer", enforcer_factory)
    monkeypatch.setattr(service, "EdhClient", client_factory)
    monkeypatch.setattr(service, "log_actions", log_actions)
    if eks_enabled is not None:
        monkeypatch.setattr(service, "eks_enabled", eks_enabled)
    return SimpleNamespace(
        module=service,
        enforcer=enforcer,
        enforcer_factory=enforcer_factory,
        client_factory=client_factory,
        edh=edh,
        log_actions=log_actions,
    )


def edh_proxy_records(fakes):
    """The custom_data of every edhProxy record written through log_actions."""
    return [call.args[2] for call in fakes.log_actions.call_args_list if call.args[1] == "edhProxy"]
