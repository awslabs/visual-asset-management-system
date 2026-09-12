# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 6: one Amazon Transcribe batch job over the concatenated FLAC, under the job role's identity."""

import json
import logging
import os
import random
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from botocore.exceptions import ClientError

from .definition import parse_s3_uri
from .errors import TRANSCRIBE_FAILED, PipelineRejection

logger = logging.getLogger("video_sop_bom_pipeline.transcribe")

POLL_INTERVAL_S = 30
LIMIT_RETRY_MAX_ATTEMPTS = 20
LIMIT_RETRY_MAX_S = 1800
LIMIT_BACKOFF_RANGE_S = (30, 120)
TERMINAL_STATES = ("COMPLETED", "FAILED")
SUBTITLE_FORMATS = ("vtt", "srt")

QUOTA_EXHAUSTED_CAUSE = "Amazon Transcribe concurrent-job limit still exceeded after 30 min (quota L-58D7221C)"
LANGUAGE_ID_HINT = " — set LANGUAGE_CODE explicitly for media with little or no speech"


@dataclass
class TranscriptResult:
    transcript_json_path: str
    subtitle_paths: dict
    job_name: str
    language_code: str
    transcript_json: dict = field(default_factory=dict)


def transcribe_job_name(pipeline_execution_id):
    return f"vams-video-sop-bom-{pipeline_execution_id}-{secrets.token_hex(4)}"


def parse_transcribe_uri(uri):
    """Bucket and key from an s3:// URI, a path-style https URI (s3.<region>.amazonaws.com/<bucket>/<key>)
    or a virtual-hosted one (<bucket>.s3[.<region>].amazonaws.com/<key>)."""
    if uri.startswith("s3://"):
        return parse_s3_uri(uri)
    parsed = urlparse(uri)
    host = parsed.netloc
    path = unquote(parsed.path.lstrip("/"))
    if host.startswith("s3.") or host.startswith("s3-"):
        bucket, _, key = path.partition("/")
    else:
        bucket = host.split(".s3", 1)[0]
        key = path
    if not bucket or not key or bucket == host:
        raise ValueError(f"cannot parse Transcribe output URI: {uri}")
    return bucket, key


def has_speech(transcript_json):
    items = (transcript_json.get("results") or {}).get("items") or []
    return any(item.get("type") == "pronunciation" for item in items)


def start_kwargs(definition, job_name, audio_uri):
    config = definition["config"]
    kwargs = {
        "TranscriptionJobName": job_name,
        "Media": {"MediaFileUri": audio_uri},
        "MediaFormat": "flac",
        "OutputBucketName": definition["auxBucket"],
        "OutputKey": f"{definition['auxTempPrefix']}transcribe/{job_name}.json",
        "Subtitles": {"Formats": list(SUBTITLE_FORMATS), "OutputStartIndex": 1},
    }
    if config["languageCode"] == "auto":
        kwargs["IdentifyLanguage"] = True
    else:
        kwargs["LanguageCode"] = config["languageCode"]
    if definition.get("kmsKeyArn"):
        kwargs["OutputEncryptionKMSKeyId"] = definition["kmsKeyArn"]
    return kwargs


def _code_and_message(exc):
    error = exc.response.get("Error", {})
    return error.get("Code", "ClientError"), error.get("Message", "")


def _start_with_limit_retry(clients, kwargs, sleep, rng, clock):
    """LimitExceededException means the 250-concurrent-jobs quota OR an over-long input; the second
    variant names the file and is not retried."""
    attempts = 0
    started = clock()
    while True:
        attempts += 1
        try:
            return clients.transcribe.start_transcription_job(**kwargs)
        except ClientError as exc:
            code, message = _code_and_message(exc)
            if code != "LimitExceededException":
                raise PipelineRejection(TRANSCRIBE_FAILED, f"Amazon Transcribe refused to start the job ({code}): {message}") from exc
            lowered = message.lower()
            if "too long" in lowered or "file size" in lowered or "duration" in lowered:
                raise PipelineRejection(TRANSCRIBE_FAILED, f"Amazon Transcribe rejected the audio: {message}") from exc
            delay = rng(*LIMIT_BACKOFF_RANGE_S)
            if attempts >= LIMIT_RETRY_MAX_ATTEMPTS or (clock() - started) + delay > LIMIT_RETRY_MAX_S:
                raise PipelineRejection(TRANSCRIBE_FAILED, QUOTA_EXHAUSTED_CAUSE) from exc
            logger.warning("transcribe LimitExceeded attempt=%d retry_in=%.0fs", attempts, delay)
            sleep(delay)


def _poll(clients, job_name, sleep):
    while True:
        job = clients.transcribe.get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJob"]
        status = job.get("TranscriptionJobStatus")
        if status in TERMINAL_STATES:
            return job
        logger.info("transcribe job=%s status=%s", job_name, status)
        sleep(POLL_INTERVAL_S)


def delete_prefix(s3, bucket, prefix):
    """Delete every object under `prefix`; returns the count."""
    deleted = 0
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        for start in range(0, len(keys), 1000):
            chunk = keys[start:start + 1000]
            s3.delete_objects(Bucket=bucket, Delete={"Objects": chunk, "Quiet": True})
            deleted += len(chunk)
        if not page.get("IsTruncated"):
            return deleted
        token = page.get("NextContinuationToken")


def _download(clients, uri, dst):
    bucket, key = parse_transcribe_uri(uri)
    clients.s3.download_file(bucket, key, dst)


def run_transcription(clients, definition, audio_uri, work_dir, *, state=None, sleep=time.sleep,
                      rng=random.uniform, clock=time.monotonic):
    """Start, poll every 30 s, download transcript (+ subtitles when present), delete the terminal job."""
    job_name = transcribe_job_name(definition["pipelineExecutionId"])
    _start_with_limit_retry(clients, start_kwargs(definition, job_name, audio_uri), sleep, rng, clock)
    if state is not None:
        state.transcribe_job_name = job_name
    logger.info("transcribe job=%s started", job_name)

    job = _poll(clients, job_name, sleep)

    audio_prefix = f"{definition['auxTempPrefix']}audio/"
    try:
        removed = delete_prefix(clients.s3, definition["auxBucket"], audio_prefix)
        logger.info("aux audio/ deleted objects=%d", removed)
    except ClientError as exc:
        logger.warning("aux audio/ deletion failed: %s", _code_and_message(exc)[0])

    if job.get("TranscriptionJobStatus") == "FAILED":
        reason = job.get("FailureReason") or "no FailureReason given"
        cause = f"Amazon Transcribe job failed: {reason}"
        lowered = reason.lower()
        if "language" in lowered and "identif" in lowered:
            cause += LANGUAGE_ID_HINT
        raise PipelineRejection(TRANSCRIBE_FAILED, cause)

    transcript_uri = (job.get("Transcript") or {}).get("TranscriptFileUri")
    if not transcript_uri:
        raise PipelineRejection(TRANSCRIBE_FAILED, "Amazon Transcribe reported COMPLETED without a TranscriptFileUri.")
    transcript_path = os.path.join(work_dir, "transcript.json")
    _download(clients, transcript_uri, transcript_path)

    subtitle_paths = {}
    for uri in (job.get("Subtitles") or {}).get("SubtitleFileUris") or []:
        extension = uri.rsplit(".", 1)[-1].lower()
        if extension in SUBTITLE_FORMATS:
            dst = os.path.join(work_dir, f"transcript.{extension}")
            _download(clients, uri, dst)
            subtitle_paths[extension] = dst

    with open(transcript_path, "r", encoding="utf-8") as handle:
        transcript_json = json.load(handle)
    language_code = (
        (transcript_json.get("results") or {}).get("language_code")
        or job.get("LanguageCode")
        or definition["config"]["languageCode"]
    )

    try:
        clients.transcribe.delete_transcription_job(TranscriptionJobName=job_name)
    except ClientError as exc:
        logger.warning("DeleteTranscriptionJob on the terminal job failed: %s", _code_and_message(exc)[0])
    if state is not None:
        state.transcribe_job_name = None
    logger.info("transcribe job=%s complete language=%s subtitles=%d", job_name, language_code, len(subtitle_paths))
    return TranscriptResult(
        transcript_json_path=transcript_path,
        subtitle_paths=subtitle_paths,
        job_name=job_name,
        language_code=language_code,
        transcript_json=transcript_json,
    )
