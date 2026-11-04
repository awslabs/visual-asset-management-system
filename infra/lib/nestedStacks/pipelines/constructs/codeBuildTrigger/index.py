# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# Starts a pipeline container image build from a custom resource behind the CDK provider framework.
# Delivered inline (ZipFile, at most 4096 bytes) by codeBuildTrigger.ts to every *CodeBuild construct.
import time

import boto3
from botocore.config import Config
from botocore.exceptions import (BotoCoreError, ClientError, ConnectTimeoutError, EndpointConnectionError,
                                 ReadTimeoutError)

ATTEMPTS = 4
BASE_DELAY_SECONDS = 2
# Each start_build call makes at most 2 HTTP attempts (botocore standard mode, at most 1 s of backoff between
# them), each bounded by the connect and read timeouts. Worst case, inside the 60 s function timeout:
# ATTEMPTS x (2 x (1 + 3) + 1) s + (2 + 4 + 8) s of backoff here = 36 + 14 = 50 s.
CLIENT_CONFIG = Config(connect_timeout=1, read_timeout=3, retries={"mode": "standard", "max_attempts": 2})
TRANSIENT_FAULTS = (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)
RETRYABLE_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "RequestLimitExceeded",
    "AccountLimitExceededException",
    "ServiceUnavailable",
    "ServiceUnavailableException",
    "InternalFailure",
    "InternalServerError",
}


def _retryable(error):
    if isinstance(error, ClientError):
        return error.response.get("Error", {}).get("Code", "") in RETRYABLE_CODES
    # Connection and timeout faults only. Any other BotoCoreError (ParamValidationError, NoCredentialsError,
    # NoRegionError, ...) is permanent and fails on the first attempt.
    return isinstance(error, TRANSIENT_FAULTS)


def _start_build(project_name):
    client = boto3.client("codebuild", config=CLIENT_CONFIG)
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return client.start_build(projectName=project_name)["build"]["id"]
        except (ClientError, BotoCoreError) as error:
            if attempt == ATTEMPTS or not _retryable(error):
                raise RuntimeError(
                    f"Could not start CodeBuild project {project_name} "
                    f"(attempt {attempt} of {ATTEMPTS}): {error}") from error
            time.sleep(BASE_DELAY_SECONDS * 2 ** (attempt - 1))


def handler(event, context):
    # The provider framework reports the resource FAILED, with this exception's message as the reason,
    # when the handler raises, and SUCCESS with the returned Data otherwise. A Delete starts nothing and
    # never fails, so removing the stack is never blocked by the build service.
    if event.get("RequestType") not in ("Create", "Update"):
        return {}
    build_id = _start_build(event["ResourceProperties"]["ProjectName"])
    return {"Data": {"BuildId": build_id}}
