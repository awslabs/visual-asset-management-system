#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Build trigger for the 3D Basic Conversion Lambda container image (``useCodeBuild``).

The two handlers back one AWS CloudFormation custom resource through the AWS CDK provider framework.
``on_event`` starts the AWS CodeBuild build that pushes the image to Amazon ECR, and ``is_complete``
reports when that build has finished. The deployment therefore waits for the image: AWS Lambda resolves
a container image when it creates or updates the function, so the function cannot be deployed before
the image exists.

A build whose content-addressed tag is already in the repository is not started again. This is what
keeps an update rollback from rebuilding: the rollback re-sends the previous properties, whose tag is
the image the function was running before the update.
"""

import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})

codebuild = boto3.client("codebuild", config=retry_config)
ecr = boto3.client("ecr", config=retry_config)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SUCCEEDED_STATUSES = frozenset({"SUCCEEDED"})
FAILED_STATUSES = frozenset({"FAILED", "FAULT", "STOPPED", "TIMED_OUT"})


def classify_build_status(status):
    """Map a CodeBuild ``buildStatus`` to ``"complete"``, ``"failed"`` or ``"pending"``."""
    if status in SUCCEEDED_STATUSES:
        return "complete"
    if status in FAILED_STATUSES:
        return "failed"
    return "pending"


def _repository_name(repository_uri):
    """The repository name of an ``<registry host>/<repository name>`` URI."""
    return repository_uri.split("/", 1)[1]


def _image_present(repository_uri, image_tag):
    try:
        ecr.describe_images(
            repositoryName=_repository_name(repository_uri),
            imageIds=[{"imageTag": image_tag}],
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ImageNotFoundException":
            return False
        raise
    return True


def _failure_reason(build_id, status, build):
    """One line for the CloudFormation status reason: the build, its status, the failing phase and log."""
    reason = f"CodeBuild build {build_id} ended {status}"
    failed_phase = next(
        (p for p in build.get("phases", []) if p.get("phaseStatus") in FAILED_STATUSES), None
    )
    if failed_phase:
        reason += f" in phase {failed_phase.get('phaseType')}"
        messages = [c["message"] for c in failed_phase.get("contexts", []) if c.get("message")]
        if messages:
            reason += f": {messages[0]}"
    deep_link = (build.get("logs") or {}).get("deepLink")
    if deep_link:
        reason += f". Build log: {deep_link}"
    return reason


def on_event(event, context):
    """Start the image build on Create and Update; nothing on Delete."""
    if event["RequestType"] == "Delete":
        return {}

    props = event["ResourceProperties"]
    image_tag = props["ImageTag"]
    if _image_present(props["EcrRepositoryUri"], image_tag):
        logger.info("Image tag %s is already in the repository; no build started", image_tag)
        return {"Data": {"ImageTag": image_tag}}

    build_id = codebuild.start_build(projectName=props["ProjectName"])["build"]["id"]
    logger.info("Started build %s for image tag %s", build_id, image_tag)
    return {"Data": {"BuildId": build_id, "ImageTag": image_tag}}


def is_complete(event, context):
    """Complete when the build succeeded; raise when it ended in any other terminal status."""
    if event["RequestType"] == "Delete":
        return {"IsComplete": True}

    build_id = (event.get("Data") or {}).get("BuildId")
    if not build_id:
        return {"IsComplete": True}

    builds = codebuild.batch_get_builds(ids=[build_id]).get("builds", [])
    if not builds:
        raise RuntimeError(f"CodeBuild build {build_id} was not found")

    build = builds[0]
    status = build.get("buildStatus", "")
    outcome = classify_build_status(status)
    logger.info("Build %s status %s", build_id, status)
    if outcome == "failed":
        raise RuntimeError(_failure_reason(build_id, status, build))
    return {"IsComplete": outcome == "complete"}
