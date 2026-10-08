# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 0/1: API probes for Transcribe, Bedrock and the auxiliary bucket, then the disk budget — all
before the first byte is downloaded, so a mis-provisioned deployment fails in minute one."""

import logging
import shutil
from urllib.parse import urlparse

import botocore.exceptions
from botocore.exceptions import ClientError

from .errors import CONNECTIVITY_ERROR, LIMIT_EXCEEDED, PipelineRejection

logger = logging.getLogger("video_sop_bom_pipeline.preflight")

# A job name that cannot exist: GetTranscriptionJob answers BadRequestException, which proves the
# endpoint is reachable and the job role may call the API.
PREFLIGHT_JOB_NAME = "vams-video-sop-bom-preflight"
TRANSCRIBE_EXPECTED_CODES = ("BadRequestException", "NotFoundException")
BEDROCK_MODEL_ACCESS_CODES = ("AccessDeniedException", "ResourceNotFoundException", "ValidationException")

DISK_WORKING_FACTOR = 1.5
DISK_HEADROOM_BYTES = 2 * 1024**3


def _host_of(exc, fallback):
    url = getattr(exc, "kwargs", {}).get("endpoint_url") or ""
    return urlparse(url).netloc or fallback


def _connect_cause(exc, fallback_host, endpoint_label):
    # The Route 53 Resolver answers public names in every VPC, so a missing interface endpoint is a
    # connect timeout, never a resolution failure.
    return (
        f"could not connect to {_host_of(exc, fallback_host)} from the isolated subnet (connect timeout) "
        f"— the {endpoint_label} interface endpoint is missing (app.useGlobalVpc.addVpcEndpoints)."
    )


def _code_and_message(exc):
    error = exc.response.get("Error", {})
    return error.get("Code", "ClientError"), error.get("Message", "")


def check_connectivity(clients, definition):
    """Transcribe sentinel GetTranscriptionJob, Bedrock converse(maxTokens=1), aux HeadBucket."""
    try:
        clients.preflight_transcribe.get_transcription_job(TranscriptionJobName=PREFLIGHT_JOB_NAME)
    except botocore.exceptions.ConnectionError as exc:
        raise PipelineRejection(CONNECTIVITY_ERROR, _connect_cause(exc, "transcribe", "Amazon Transcribe")) from exc
    except ClientError as exc:
        code, message = _code_and_message(exc)
        if code not in TRANSCRIBE_EXPECTED_CODES:
            raise PipelineRejection(
                CONNECTIVITY_ERROR,
                f"Amazon Transcribe preflight failed ({code}) — the job role cannot call GetTranscriptionJob: {message}",
            ) from exc
    logger.info("preflight transcribe reachable")

    model_id = definition["bedrockModelId"]
    try:
        clients.preflight_bedrock.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "ping"}]}],
            inferenceConfig={"maxTokens": 1},
        )
    except botocore.exceptions.ConnectionError as exc:
        raise PipelineRejection(CONNECTIVITY_ERROR, _connect_cause(exc, "bedrock-runtime", "Bedrock Runtime")) from exc
    except ClientError as exc:
        code, message = _code_and_message(exc)
        if code in BEDROCK_MODEL_ACCESS_CODES:
            raise PipelineRejection(
                CONNECTIVITY_ERROR,
                f"Amazon Bedrock model {model_id} is not accessible from this account/Region ({code}) "
                "— enable model access in the Bedrock console.",
            ) from exc
        raise PipelineRejection(CONNECTIVITY_ERROR, f"Amazon Bedrock preflight failed ({code}): {message}") from exc
    logger.info("preflight bedrock model=%s reachable", model_id)

    bucket = definition["auxBucket"]
    try:
        clients.s3.head_bucket(Bucket=bucket)
    except botocore.exceptions.ConnectionError as exc:
        raise PipelineRejection(CONNECTIVITY_ERROR, _connect_cause(exc, "s3", "Amazon S3 gateway")) from exc
    except ClientError as exc:
        code, _ = _code_and_message(exc)
        raise PipelineRejection(
            CONNECTIVITY_ERROR, f"the auxiliary bucket {bucket} is not reachable or readable by the job role ({code})."
        ) from exc
    logger.info("preflight aux bucket reachable")


def required_bytes(total_bytes):
    return int(total_bytes * DISK_WORKING_FACTOR) + DISK_HEADROOM_BYTES


def check_disk_budget(work_dir, total_bytes):
    """Refuse before download when Σbytes × 1.5 + 2 GiB will not fit on the ephemeral volume."""
    free = shutil.disk_usage(work_dir).free
    required = required_bytes(total_bytes)
    gib = 1024**3
    logger.info("disk budget inputs=%d bytes required=%d bytes free=%d bytes", total_bytes, required, free)
    if free < required:
        raise PipelineRejection(
            LIMIT_EXCEEDED,
            f"the selected videos total {total_bytes / gib:.1f} GiB and the run needs about {required / gib:.1f} GiB "
            f"of scratch space, but only {free / gib:.1f} GiB is free on the job's ephemeral volume "
            "(VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB in infra/config/config.ts).",
        )
