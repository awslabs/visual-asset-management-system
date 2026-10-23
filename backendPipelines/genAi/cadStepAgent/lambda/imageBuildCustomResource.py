# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Image build wait handler for the GenAI CAD STEP agent pipeline (CDK Provider framework).

The build is started by the shared CodeBuild build-trigger handler
(``infra/lib/nestedStacks/pipelines/constructs/codeBuildTrigger/index.py``), the provider's onEvent
handler for every ``*CodeBuild`` construct, which returns ``{"Data": {"BuildId": ...}}``. The provider
framework merges that reply into the event it hands this module's ``is_complete``, which is polled until
the build reaches a terminal status. CloudFormation therefore holds the resource in progress for the
length of the build, and a resource that depends on it (the Amazon Bedrock AgentCore Runtime, the AWS
Batch job definition) is created or updated only once the image tag it names exists in the repository.

The build id is read from ``Data.BuildId``, never from the physical id: the shared handler returns no
physical id, so on an Update the physical id is still the one an earlier deployment recorded, and
polling it would report an earlier, finished build as this deployment's.
"""

import logging

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

# CodeBuild build statuses (batch_get_builds ``buildStatus``).
BUILD_SUCCEEDED = "SUCCEEDED"
BUILD_IN_PROGRESS = "IN_PROGRESS"
BUILD_FAILED_STATUSES = ("FAILED", "FAULT", "STOPPED", "TIMED_OUT")

_codebuild = None


def codebuild_client():
    global _codebuild
    if _codebuild is None:
        _codebuild = boto3.client("codebuild", config=retry_config)
    return _codebuild


def describe_build(build_id, client=None):
    """The (status, current phase) of one build, or (None, None) when CodeBuild does not know the id."""
    client = client or codebuild_client()
    builds = client.batch_get_builds(ids=[build_id]).get("builds", [])
    if not builds:
        return None, None
    build = builds[0]
    return build.get("buildStatus", ""), build.get("currentPhase", "")


def completion(request_type, build_id, status, phase):
    """The Provider-framework isComplete reply for a build status; raises on a terminal failure."""
    if request_type == "Delete":
        return {"IsComplete": True}
    if status is None:
        raise RuntimeError(f"CodeBuild build {build_id} was not found")
    if status == BUILD_SUCCEEDED:
        return {"IsComplete": True, "Data": {"BuildId": build_id, "BuildStatus": status}}
    if status in BUILD_FAILED_STATUSES:
        raise RuntimeError(
            f"CodeBuild build {build_id} ended with status {status} in phase {phase}; "
            "the container image was not pushed. Read the build's log in the CodeBuild console.")
    return {"IsComplete": False}


def is_complete(event, context):
    """Polled by the Provider framework until the build the onEvent reply named is terminal."""
    request_type = event.get("RequestType", "")
    if request_type == "Delete":
        return completion(request_type, "", None, None)
    build_id = (event.get("Data") or {}).get("BuildId", "")
    if not build_id:
        raise RuntimeError(
            "The build trigger returned no BuildId, so there is no build to wait for; "
            "the container image may not have been pushed.")
    status, phase = describe_build(build_id)
    logger.info("build %s status=%s phase=%s", build_id, status, phase)
    return completion(request_type, build_id, status, phase)
