# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The image-build custom resource holds CloudFormation until the CodeBuild build is terminal.

The Amazon Bedrock AgentCore Runtime validates its container image at CreateAgentRuntime time, so a
runtime created while the build is still running fails with "image identifier does not exist". The
build is started by the shared CodeBuild build-trigger handler every ``*CodeBuild`` construct uses as
its provider's onEvent; ``is_complete`` below waits for the build that reply names and maps its status
onto the framework's reply. The provider framework merges the onEvent reply into the event it hands
``is_complete``; ``_framework_event`` reproduces that merge.
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.abspath(os.path.join(_LAMBDA_DIR, "..", "..", "..", ".."))
_SHARED_TRIGGER = os.path.join(
    _REPO_ROOT, "infra", "lib", "nestedStacks", "pipelines", "constructs", "codeBuildTrigger", "index.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_wait():
    return _load("cad_step_agent_image_build_undertest",
                 os.path.join(_LAMBDA_DIR, "imageBuildCustomResource.py"))


def _client(status=None, phase="BUILD", found=True, build_id="proj:1"):
    client = MagicMock()
    client.batch_get_builds.return_value = {
        "builds": [{"id": build_id, "buildStatus": status, "currentPhase": phase}] if found else []}
    client.start_build.return_value = {"build": {"id": build_id}}
    return client


def _framework_event(request, on_event_reply):
    """The isComplete event the CDK provider framework builds from the request and the onEvent reply.

    The reply's fields win over the request's, and a reply without a PhysicalResourceId keeps the
    request's (Update, Delete) or takes the RequestId (Create) -- the framework's defaults.
    """
    physical_id = on_event_reply.get("PhysicalResourceId") or (
        request["RequestId"] if request["RequestType"] == "Create" else request["PhysicalResourceId"])
    return {**request, **on_event_reply, "PhysicalResourceId": physical_id}


@pytest.mark.unit
class TestTheSharedTriggerFeedsTheWaiter:
    @pytest.mark.parametrize("request_type", ["Create", "Update"])
    def test_the_waiter_polls_the_build_the_shared_trigger_started(self, request_type, monkeypatch):
        trigger = _load("cad_step_agent_shared_trigger_undertest", _SHARED_TRIGGER)
        started = _client(build_id="proj:new")
        monkeypatch.setattr(trigger.boto3, "client", lambda *args, **kwargs: started)
        request = {"RequestType": request_type, "RequestId": "req-1", "PhysicalResourceId": "proj:old",
                   "ResourceProperties": {"ProjectName": "proj"}}
        reply = trigger.handler(request, None)
        started.start_build.assert_called_once_with(projectName="proj")
        assert reply == {"Data": {"BuildId": "proj:new"}}

        wait = _load_wait()
        polled = _client("IN_PROGRESS", "BUILD", build_id="proj:new")
        monkeypatch.setattr(wait, "codebuild_client", lambda: polled)
        assert wait.is_complete(_framework_event(request, reply), None) == {"IsComplete": False}
        polled.batch_get_builds.assert_called_once_with(ids=["proj:new"])

    def test_an_update_never_reads_the_earlier_build_from_the_physical_id(self, monkeypatch):
        # The shared trigger returns no physical id, so an Update keeps the earlier deployment's. That
        # earlier build SUCCEEDED; reading it would let the runtime move to a tag not yet pushed.
        wait = _load_wait()
        polled = _client("IN_PROGRESS", "BUILD", build_id="proj:new")
        monkeypatch.setattr(wait, "codebuild_client", lambda: polled)
        event = {"RequestType": "Update", "PhysicalResourceId": "proj:old", "Data": {"BuildId": "proj:new"}}
        assert wait.is_complete(event, None) == {"IsComplete": False}
        polled.batch_get_builds.assert_called_once_with(ids=["proj:new"])

    def test_a_reply_without_a_build_id_fails_the_resource(self, monkeypatch):
        wait = _load_wait()
        client = _client()
        monkeypatch.setattr(wait, "codebuild_client", lambda: client)
        with pytest.raises(RuntimeError, match="no BuildId"):
            wait.is_complete({"RequestType": "Create", "PhysicalResourceId": "req-1"}, None)
        client.batch_get_builds.assert_not_called()


@pytest.mark.unit
class TestIsComplete:
    def test_a_running_build_is_not_complete(self, monkeypatch):
        mod = _load_wait()
        monkeypatch.setattr(mod, "codebuild_client", lambda: _client("IN_PROGRESS", "INSTALL"))
        event = {"RequestType": "Create", "Data": {"BuildId": "proj:1"}}
        assert mod.is_complete(event, None) == {"IsComplete": False}

    def test_a_succeeded_build_completes_with_its_id(self, monkeypatch):
        mod = _load_wait()
        monkeypatch.setattr(mod, "codebuild_client", lambda: _client("SUCCEEDED", "COMPLETED"))
        reply = mod.is_complete({"RequestType": "Update", "Data": {"BuildId": "proj:1"}}, None)
        assert reply["IsComplete"] is True
        assert reply["Data"] == {"BuildId": "proj:1", "BuildStatus": "SUCCEEDED"}

    @pytest.mark.parametrize("status", ["FAILED", "FAULT", "STOPPED", "TIMED_OUT"])
    def test_every_terminal_failure_raises_naming_the_build_and_phase(self, status, monkeypatch):
        mod = _load_wait()
        monkeypatch.setattr(mod, "codebuild_client", lambda: _client(status, "POST_BUILD"))
        with pytest.raises(RuntimeError) as excinfo:
            mod.is_complete({"RequestType": "Create", "Data": {"BuildId": "proj:1"}}, None)
        message = str(excinfo.value)
        assert "proj:1" in message and status in message and "POST_BUILD" in message

    def test_an_unknown_build_id_raises_rather_than_waiting_forever(self, monkeypatch):
        mod = _load_wait()
        monkeypatch.setattr(mod, "codebuild_client", lambda: _client(found=False))
        with pytest.raises(RuntimeError):
            mod.is_complete({"RequestType": "Create", "Data": {"BuildId": "proj:gone"}}, None)

    def test_delete_is_complete_without_asking_codebuild(self, monkeypatch):
        mod = _load_wait()
        client = _client()
        monkeypatch.setattr(mod, "codebuild_client", lambda: client)
        assert mod.is_complete({"RequestType": "Delete", "PhysicalResourceId": "proj:1"}, None) == {"IsComplete": True}
        client.batch_get_builds.assert_not_called()

    def test_the_module_no_longer_carries_a_start_handler_of_its_own(self):
        # The start step is the shared trigger (codeBuildTriggerHandler.test.ts holds the construct to it).
        assert not hasattr(_load_wait(), "on_event")
