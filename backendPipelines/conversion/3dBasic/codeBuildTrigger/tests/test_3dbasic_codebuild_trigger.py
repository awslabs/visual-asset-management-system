#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the 3D Basic Conversion CodeBuild trigger (``codeBuildTrigger.py``).

The status mapping is what decides whether a deployment waits, succeeds, or fails and rolls back, so
every CodeBuild ``buildStatus`` value is asserted, plus the two shortcuts that start no build (Delete,
and an image tag already in the repository).
"""

import importlib.util
import os

import pytest
from botocore.exceptions import ClientError

_TRIGGER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROPS = {
    "ProjectName": "Conversion3dBasicProject",
    "SourceHash": "a" * 64,
    "EcrRepositoryUri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/vams-prod-3dbasic",
    "ImageTag": "a" * 32,
}


def _load(monkeypatch):
    # The module creates its clients at import, which needs a Region and nothing else.
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location(
        "conversion_3dbasic_codebuild_trigger", os.path.join(_TRIGGER_DIR, "codeBuildTrigger.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCodeBuild:
    def __init__(self, builds=None):
        self.builds = builds if builds is not None else []
        self.started = []
        self.queried = []

    def start_build(self, projectName):
        self.started.append(projectName)
        return {"build": {"id": f"{projectName}:build-1"}}

    def batch_get_builds(self, ids):
        self.queried.append(ids)
        return {"builds": self.builds}


class FakeEcr:
    def __init__(self, present=False, error_code=None):
        self.present = present
        self.error_code = error_code
        self.described = []

    def describe_images(self, repositoryName, imageIds):
        self.described.append((repositoryName, imageIds))
        if self.error_code:
            raise ClientError({"Error": {"Code": self.error_code}}, "DescribeImages")
        if not self.present:
            raise ClientError({"Error": {"Code": "ImageNotFoundException"}}, "DescribeImages")
        return {"imageDetails": [{"imageTags": [imageIds[0]["imageTag"]]}]}


@pytest.fixture
def trigger(monkeypatch):
    module = _load(monkeypatch)
    module.codebuild = FakeCodeBuild()
    module.ecr = FakeEcr()
    return module


@pytest.mark.parametrize(
    "status,expected",
    [
        ("SUCCEEDED", "complete"),
        ("IN_PROGRESS", "pending"),
        ("FAILED", "failed"),
        ("FAULT", "failed"),
        ("STOPPED", "failed"),
        ("TIMED_OUT", "failed"),
        ("", "pending"),
        ("SOME_FUTURE_STATUS", "pending"),
    ],
)
def test_classify_build_status(trigger, status, expected):
    assert trigger.classify_build_status(status) == expected


def test_every_documented_terminal_failure_status_fails(trigger):
    # The CodeBuild API's buildStatus enum, minus the two non-failure members. A status added to the
    # failure set by mistake (or dropped from it) changes whether a broken image fails the deploy.
    documented = {"SUCCEEDED", "FAILED", "FAULT", "TIMED_OUT", "IN_PROGRESS", "STOPPED"}
    assert trigger.FAILED_STATUSES == documented - {"SUCCEEDED", "IN_PROGRESS"}


@pytest.mark.parametrize("request_type", ["Create", "Update"])
def test_on_event_starts_the_project_build_when_the_tag_is_absent(trigger, request_type):
    result = trigger.on_event({"RequestType": request_type, "ResourceProperties": PROPS}, None)

    assert trigger.codebuild.started == ["Conversion3dBasicProject"]
    assert trigger.ecr.described == [("vams-prod-3dbasic", [{"imageTag": "a" * 32}])]
    assert result == {
        "Data": {"BuildId": "Conversion3dBasicProject:build-1", "ImageTag": "a" * 32}
    }


def test_on_event_starts_no_build_when_the_tag_is_already_present(trigger):
    trigger.ecr = FakeEcr(present=True)

    result = trigger.on_event({"RequestType": "Update", "ResourceProperties": PROPS}, None)

    assert trigger.codebuild.started == []
    assert result == {"Data": {"ImageTag": "a" * 32}}


def test_on_event_propagates_an_ecr_error_other_than_a_missing_image(trigger):
    trigger.ecr = FakeEcr(error_code="AccessDeniedException")

    with pytest.raises(ClientError):
        trigger.on_event({"RequestType": "Create", "ResourceProperties": PROPS}, None)
    assert trigger.codebuild.started == []


def test_on_event_does_nothing_on_delete(trigger):
    result = trigger.on_event({"RequestType": "Delete", "ResourceProperties": PROPS}, None)

    assert result == {}
    assert trigger.codebuild.started == []
    assert trigger.ecr.described == []


def _waiting_event(build_id="Conversion3dBasicProject:build-1", request_type="Create"):
    return {
        "RequestType": request_type,
        "ResourceProperties": PROPS,
        "Data": {"BuildId": build_id, "ImageTag": "a" * 32},
    }


def test_is_complete_when_the_build_succeeded(trigger):
    trigger.codebuild = FakeCodeBuild(builds=[{"buildStatus": "SUCCEEDED"}])

    assert trigger.is_complete(_waiting_event(), None) == {"IsComplete": True}
    assert trigger.codebuild.queried == [["Conversion3dBasicProject:build-1"]]


def test_is_not_complete_while_the_build_runs(trigger):
    trigger.codebuild = FakeCodeBuild(builds=[{"buildStatus": "IN_PROGRESS"}])

    # The provider framework rejects Data on a not-complete response, so the reply carries none.
    assert trigger.is_complete(_waiting_event(), None) == {"IsComplete": False}


@pytest.mark.parametrize("status", ["FAILED", "FAULT", "STOPPED", "TIMED_OUT"])
def test_is_complete_raises_with_the_reason_when_the_build_failed(trigger, status):
    trigger.codebuild = FakeCodeBuild(
        builds=[
            {
                "buildStatus": status,
                "phases": [
                    {"phaseType": "PRE_BUILD", "phaseStatus": "SUCCEEDED"},
                    {
                        "phaseType": "BUILD",
                        "phaseStatus": status,
                        "contexts": [{"message": "Error while executing command: docker build"}],
                    },
                ],
                "logs": {"deepLink": "https://console.example/codebuild/log"},
            }
        ]
    )

    with pytest.raises(RuntimeError) as raised:
        trigger.is_complete(_waiting_event(), None)

    message = str(raised.value)
    assert f"Conversion3dBasicProject:build-1 ended {status}" in message
    assert "phase BUILD: Error while executing command: docker build" in message
    assert "Build log: https://console.example/codebuild/log" in message


def test_is_complete_failure_reason_without_phase_detail(trigger):
    trigger.codebuild = FakeCodeBuild(builds=[{"buildStatus": "FAULT"}])

    with pytest.raises(RuntimeError, match=r"^CodeBuild build \S+ ended FAULT$"):
        trigger.is_complete(_waiting_event(), None)


def test_is_complete_raises_when_the_build_is_unknown(trigger):
    trigger.codebuild = FakeCodeBuild(builds=[])

    with pytest.raises(RuntimeError, match="was not found"):
        trigger.is_complete(_waiting_event(), None)


def test_is_complete_without_a_build_id_is_complete(trigger):
    # on_event found the tag already present and started nothing.
    event = {"RequestType": "Update", "ResourceProperties": PROPS, "Data": {"ImageTag": "a" * 32}}

    assert trigger.is_complete(event, None) == {"IsComplete": True}
    assert trigger.codebuild.queried == []


def test_is_complete_on_delete_is_complete(trigger):
    assert trigger.is_complete(_waiting_event(request_type="Delete"), None) == {"IsComplete": True}
    assert trigger.codebuild.queried == []
