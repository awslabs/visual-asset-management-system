# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The inner task-token callbacks: the process's final act under the .sync Batch task."""

import json
import logging
import os
import sys

from botocore.exceptions import ClientError

from . import REPORTER
from .errors import ERROR_CODES, PIPELINE_ERROR

logger = logging.getLogger("video_sop_bom_pipeline.sfn")

TASK_TOKEN_ENV = "TASK_TOKEN"
# SendTaskFailure API limits.
ERROR_MAX_CHARS = 256
CAUSE_MAX_CHARS = 32768


def _task_token():
    token = os.environ.get(TASK_TOKEN_ENV, "")
    logger.info("TASK_TOKEN present=%s", bool(token))
    return token


def success_payload(file_count, summary_key):
    return {"reporter": REPORTER, "status": "SUCCEEDED", "fileCount": file_count, "summaryKey": summary_key}


def send_task_success(clients, payload):
    """Report success on the inner token. Returns False when no token is set (a local run)."""
    if payload.get("reporter") != REPORTER:
        raise ValueError("the success payload must carry the reporter marker")
    token = _task_token()
    if not token:
        logger.warning("no TASK_TOKEN; SendTaskSuccess skipped")
        return False
    try:
        clients.sfn.send_task_success(taskToken=token, output=json.dumps(payload))
    except ClientError as exc:
        logger.error("SendTaskSuccess failed: %s", exc.response.get("Error", {}).get("Code", "ClientError"))
        sys.exit(1)
    logger.info("SendTaskSuccess delivered fileCount=%s", payload.get("fileCount"))
    return True


def send_task_failure(clients, code, cause):
    """Report a handled failure: `code` in error (one of the six), the readable sentence in cause."""
    if code not in ERROR_CODES:
        logger.warning("unknown rejection code %r reported as %s", code, PIPELINE_ERROR)
        code = PIPELINE_ERROR
    cause = str(cause)
    for known in ERROR_CODES:
        prefix = known + ": "
        if cause.startswith(prefix):
            cause = cause[len(prefix):]
    logger.error("REJECTION %s: %s", code, cause)
    token = _task_token()
    if not token:
        logger.warning("no TASK_TOKEN; SendTaskFailure skipped")
        return False
    try:
        clients.sfn.send_task_failure(taskToken=token, error=code[:ERROR_MAX_CHARS], cause=cause[:CAUSE_MAX_CHARS])
    except ClientError as exc:
        logger.error("SendTaskFailure failed: %s", exc.response.get("Error", {}).get("Code", "ClientError"))
        sys.exit(1)
    return True
