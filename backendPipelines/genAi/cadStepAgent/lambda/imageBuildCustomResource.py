# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Image build custom resource handlers for the GenAI CAD STEP agent pipeline (CDK Provider framework).

``on_event`` starts the AWS CodeBuild build that produces the agent container image and names the
build id as the resource's physical id; ``is_complete`` is polled until that build reaches a terminal
status. CloudFormation therefore holds the resource in progress for the length of the build, and a
resource that depends on it (the Amazon Bedrock AgentCore Runtime, the AWS Batch job definition) is
created or updated only once the image tag it names exists in the repository.
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


def on_event(event, context):
    """Start the build on Create and Update; Delete has nothing to undo (the image stays in ECR)."""
    request_type = event.get("RequestType", "")
    if request_type in ("Create", "Update"):
        project_name = event["ResourceProperties"]["ProjectName"]
        response = codebuild_client().start_build(projectName=project_name)
        build_id = response["build"]["id"]
        logger.info("started build %s of project %s", build_id, project_name)
        return {"PhysicalResourceId": build_id, "Data": {"BuildId": build_id}}
    return {"PhysicalResourceId": event.get("PhysicalResourceId", "none")}


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
    """Polled by the Provider framework until the build named by the physical id is terminal."""
    request_type = event.get("RequestType", "")
    build_id = event.get("PhysicalResourceId", "")
    if request_type == "Delete":
        return completion(request_type, build_id, None, None)
    status, phase = describe_build(build_id)
    logger.info("build %s status=%s phase=%s", build_id, status, phase)
    return completion(request_type, build_id, status, phase)
