# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The image-build custom resource holds CloudFormation until the CodeBuild build is terminal.

The Amazon Bedrock AgentCore Runtime validates its container image at CreateAgentRuntime time, so a
runtime created while the build is still running fails with "image identifier does not exist". The
Provider-framework pair below is what makes the build synchronous from CloudFormation's point of view:
``on_event`` starts the build and names it, ``is_complete`` maps its status onto the framework's reply.
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    name = "cad_step_agent_image_build_undertest"
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(_LAMBDA_DIR, "imageBuildCustomResource.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _client(status=None, phase="BUILD", found=True):
    client = MagicMock()
    client.batch_get_builds.return_value = {
        "builds": [{"id": "proj:1", "buildStatus": status, "currentPhase": phase}] if found else []}
    client.start_build.return_value = {"build": {"id": "proj:1"}}
    return client


@pytest.mark.unit
class TestOnEvent:
    @pytest.mark.parametrize("request_type", ["Create", "Update"])
    def test_create_and_update_start_the_build_and_name_it_as_the_physical_id(self, request_type, monkeypatch):
        mod = _load()
        client = _client()
        monkeypatch.setattr(mod, "codebuild_client", lambda: client)
        reply = mod.on_event({"RequestType": request_type, "ResourceProperties": {"ProjectName": "proj"}}, None)
        client.start_build.assert_called_once_with(projectName="proj")
        assert reply["PhysicalResourceId"] == "proj:1"
        assert reply["Data"]["BuildId"] == "proj:1"

    def test_delete_starts_nothing_and_keeps_the_physical_id(self, monkeypatch):
        mod = _load()
        client = _client()
        monkeypatch.setattr(mod, "codebuild_client", lambda: client)
        reply = mod.on_event({"RequestType": "Delete", "PhysicalResourceId": "proj:old",
                              "ResourceProperties": {"ProjectName": "proj"}}, None)
        client.start_build.assert_not_called()
        assert reply == {"PhysicalResourceId": "proj:old"}


@pytest.mark.unit
class TestIsComplete:
    def test_a_running_build_is_not_complete(self, monkeypatch):
        mod = _load()
        monkeypatch.setattr(mod, "codebuild_client", lambda: _client("IN_PROGRESS", "INSTALL"))
        assert mod.is_complete({"RequestType": "Create", "PhysicalResourceId": "proj:1"}, None) == {"IsComplete": False}

    def test_a_succeeded_build_completes_with_its_id(self, monkeypatch):
        mod = _load()
        monkeypatch.setattr(mod, "codebuild_client", lambda: _client("SUCCEEDED", "COMPLETED"))
        reply = mod.is_complete({"RequestType": "Update", "PhysicalResourceId": "proj:1"}, None)
        assert reply["IsComplete"] is True
        assert reply["Data"] == {"BuildId": "proj:1", "BuildStatus": "SUCCEEDED"}

    @pytest.mark.parametrize("status", ["FAILED", "FAULT", "STOPPED", "TIMED_OUT"])
    def test_every_terminal_failure_raises_naming_the_build_and_phase(self, status, monkeypatch):
        mod = _load()
        monkeypatch.setattr(mod, "codebuild_client", lambda: _client(status, "POST_BUILD"))
        with pytest.raises(RuntimeError) as excinfo:
            mod.is_complete({"RequestType": "Create", "PhysicalResourceId": "proj:1"}, None)
        message = str(excinfo.value)
        assert "proj:1" in message and status in message and "POST_BUILD" in message

    def test_an_unknown_build_id_raises_rather_than_waiting_forever(self, monkeypatch):
        mod = _load()
        monkeypatch.setattr(mod, "codebuild_client", lambda: _client(found=False))
        with pytest.raises(RuntimeError):
            mod.is_complete({"RequestType": "Create", "PhysicalResourceId": "proj:gone"}, None)

    def test_delete_is_complete_without_asking_codebuild(self, monkeypatch):
        mod = _load()
        client = _client()
        monkeypatch.setattr(mod, "codebuild_client", lambda: client)
        assert mod.is_complete({"RequestType": "Delete", "PhysicalResourceId": "proj:1"}, None) == {"IsComplete": True}
        client.batch_get_builds.assert_not_called()
