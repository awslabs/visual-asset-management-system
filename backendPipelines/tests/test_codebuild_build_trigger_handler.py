#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The CodeBuild build-trigger handler fails the deployment when it cannot start a build.

Every pipeline built with `useCodeBuild: true` starts its container image build from a custom resource
behind the CDK provider framework. The handler used to call `cfnresponse.send(...)`, which behind the
framework reports to nothing, and to swallow every exception -- so a build that could not be started
returned normally and the framework reported SUCCESS, leaving the AWS Batch job definition pointing at
an image tag that was never pushed. The framework reports FAILED only when the handler raises.

The handler file is the one all five `*CodeBuild` constructs inline
(`infra/lib/nestedStacks/pipelines/constructs/codeBuildTrigger.ts`); it is executed here with a
stubbed CodeBuild client.
"""

import importlib.util
import pathlib

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

HANDLER = (pathlib.Path(__file__).resolve().parents[2]
           / "infra" / "lib" / "nestedStacks" / "pipelines" / "constructs" / "codeBuildTrigger" / "index.py")


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": f"{code} message"}}, "StartBuild")


class _CodeBuild:
    """A CodeBuild client whose start_build answers from a script: an exception to raise, or a build id."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def start_build(self, projectName):
        self.calls.append(projectName)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return {"build": {"id": outcome}}


@pytest.fixture
def trigger(monkeypatch):
    """``(module, install, sleeps)``: the handler module, a function installing a stub client, and the
    backoff delays it slept."""
    spec = importlib.util.spec_from_file_location("codebuild_build_trigger_handler", HANDLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sleeps = []
    if hasattr(module, "time"):
        monkeypatch.setattr(module.time, "sleep", sleeps.append)

    def install(client):
        monkeypatch.setattr(module.boto3, "client", lambda service: client if service == "codebuild" else None)
        return client

    return module, install, sleeps


def _event(request_type, project="vams-coordtransform-build"):
    return {"RequestType": request_type, "ResourceProperties": {"ProjectName": project}}


@pytest.mark.unit
class TestCreateAndUpdate:
    @pytest.mark.parametrize("request_type", ["Create", "Update"])
    def test_a_started_build_returns_its_id(self, trigger, request_type):
        module, install, sleeps = trigger
        client = install(_CodeBuild("proj:build-1"))
        assert module.handler(_event(request_type), None) == {"Data": {"BuildId": "proj:build-1"}}
        assert client.calls == ["vams-coordtransform-build"]
        assert sleeps == []

    @pytest.mark.parametrize("transient", [
        _client_error("ThrottlingException"),
        _client_error("AccountLimitExceededException"),
        _client_error("ServiceUnavailableException"),
        EndpointConnectionError(endpoint_url="https://codebuild.example.invalid"),
    ], ids=["throttling", "concurrent-build-limit", "service-unavailable", "connection"])
    def test_a_transient_failure_is_retried_with_backoff(self, trigger, transient):
        module, install, sleeps = trigger
        client = install(_CodeBuild(transient, transient, "proj:build-3"))
        assert module.handler(_event("Create"), None) == {"Data": {"BuildId": "proj:build-3"}}
        assert len(client.calls) == 3
        assert sleeps == [2, 4]

    def test_a_permanent_failure_raises_at_once_and_names_the_project(self, trigger):
        module, install, sleeps = trigger
        client = install(_CodeBuild(_client_error("AccessDeniedException")))
        with pytest.raises(RuntimeError) as raised:
            module.handler(_event("Create"), None)
        assert "vams-coordtransform-build" in str(raised.value)
        assert "AccessDeniedException" in str(raised.value)
        assert len(client.calls) == 1
        assert sleeps == []

    def test_a_transient_failure_that_persists_raises_after_the_last_attempt(self, trigger):
        module, install, sleeps = trigger
        client = install(_CodeBuild(*[_client_error("ThrottlingException")] * module.ATTEMPTS))
        with pytest.raises(RuntimeError, match=f"attempt {module.ATTEMPTS} of {module.ATTEMPTS}"):
            module.handler(_event("Update"), None)
        assert len(client.calls) == module.ATTEMPTS
        assert sleeps == [2, 4, 8]


@pytest.mark.unit
class TestDelete:
    def test_delete_starts_nothing_and_never_raises(self, trigger):
        module, install, _sleeps = trigger
        # A client that would fail if it were called at all.
        client = install(_CodeBuild(_client_error("AccessDeniedException")))
        assert module.handler(_event("Delete"), None) == {}
        assert client.calls == []

    def test_delete_without_properties_does_not_raise(self, trigger):
        module, _install, _sleeps = trigger
        assert module.handler({"RequestType": "Delete"}, None) == {}


@pytest.mark.unit
def test_the_handler_does_not_use_cfnresponse():
    """Behind the provider framework a cfnresponse call reports to nothing; only the return value and a
    raised exception reach AWS CloudFormation."""
    assert "cfnresponse" not in HANDLER.read_text(encoding="utf-8")
