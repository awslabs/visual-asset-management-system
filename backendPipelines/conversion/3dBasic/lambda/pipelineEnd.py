#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from customLogging.logger import safeLogger
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="EndPipeline")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)

MAX_CAUSE_CHARS = 256
# A message the container wrote itself is the text the Lambda tier would have reported, so it keeps more.
MAX_CONTAINER_MESSAGE_CHARS = 512
RUN_DOCUMENT_FILENAME = "conversion-run.json"
# Exception classes whose text is written for the user; the type name adds nothing to it.
USER_TEXT_TYPES = ("ValueError", "RuntimeError", "InputTooLargeError", "WorkerKilledForMemory",
                   "WorkerOutOfTime", "ReferencedFileError")
BATCH_FAILURE_KEYS = ("Attempts", "JobId", "Container", "StatusReason")


def _parsed_cause(error):
    cause = (error or {}).get("Cause") if isinstance(error, dict) else None
    if not isinstance(cause, str) or not cause.strip():
        return None
    try:
        parsed = json.loads(cause)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def is_batch_failure(error):
    """True when the state machine's catch carries a failed AWS Batch job (the job description Step
    Functions returns as the cause of ``States.TaskFailed``)."""
    parsed = _parsed_cause(error)
    return bool(parsed) and any(key in parsed for key in BATCH_FAILURE_KEYS) and "errorMessage" not in parsed


def results_location(event):
    """``(bucket, prefix)`` of the execution's results folder from ``outputS3AssetResultsPath``, or None."""
    uri = event.get("outputS3AssetResultsPath") if isinstance(event, dict) else None
    if not isinstance(uri, str) or not uri.startswith("s3://"):
        return None
    bucket, _, key = uri[len("s3://"):].partition("/")
    if not bucket or not key:
        return None
    return bucket, key if key.endswith("/") else key + "/"


def container_failure_message(event):
    """The error text the Batch task's entry wrote to ``conversion-run.json`` (``failed`` true) under the
    run's results folder, cleaned for the user, or None when the document is missing or says nothing."""
    location = results_location(event)
    if not location:
        return None
    bucket, prefix = location
    try:
        client = boto3.client('s3', region_name=event.get("outputBucketRegion") or os.environ["AWS_REGION"],
                              config=retry_config)
        body = client.get_object(Bucket=bucket, Key=prefix + RUN_DOCUMENT_FILENAME)["Body"].read(65536)
        document = json.loads(body)
    except Exception as e:  # noqa: BLE001 - the summary of the job's own status stands in
        logger.warning(f"The job's failure document could not be read ({type(e).__name__})")
        return None
    message = document.get("message") if isinstance(document, dict) and document.get("failed") is True else None
    if not isinstance(message, str) or not message.strip():
        return None
    for name in USER_TEXT_TYPES:
        if message.startswith(name + ": "):
            message = message[len(name) + 2:]
            break
    return message.strip()[:MAX_CONTAINER_MESSAGE_CHARS]


def batch_failure_summary(error):
    """A short message from a failed Batch job's own status when the container left no message (it was
    killed, or never started): the status reason and exit code, and where the full log is."""
    parsed = _parsed_cause(error) or {}
    attempts = parsed.get("Attempts")
    last = attempts[-1] if isinstance(attempts, list) and attempts and isinstance(attempts[-1], dict) else {}
    container = last.get("Container") if isinstance(last.get("Container"), dict) else (
        parsed.get("Container") if isinstance(parsed.get("Container"), dict) else {})
    reason = container.get("Reason") or last.get("StatusReason") or parsed.get("StatusReason")
    exit_code = container.get("ExitCode")
    detail = []
    if isinstance(reason, str) and reason.strip():
        detail.append(reason.strip()[:100])
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        detail.append(f"exit code {exit_code}")
    shown = f" ({', '.join(detail)})" if detail else ""
    return (f"The conversion job failed{shown}. See the conversion job container log of this execution "
            f"for the cause.")


def failure_cause(error, container_message=None):
    """The text a failed run reports to the workflow, cut to what Step Functions accepts: the message the
    Batch task wrote itself (``container_message``), else a short summary of a failed Batch job's status,
    else the error's message when the state machine's catch carried one (the Lambda attempt's error, the
    selector's refusal), else the cause as it came."""
    if isinstance(container_message, str) and container_message.strip():
        return container_message[:MAX_CONTAINER_MESSAGE_CHARS]
    if is_batch_failure(error):
        return batch_failure_summary(error)[:MAX_CAUSE_CHARS]
    if isinstance(error, dict) and error.get("Error") == "States.Timeout" and not error.get("Cause"):
        return "The conversion did not finish within the time allowed for it."
    cause = (error or {}).get("Cause") if isinstance(error, dict) else None
    if isinstance(cause, str) and cause.strip():
        parsed = _parsed_cause(error)
        message = parsed.get("errorMessage") if parsed else None
        cause = message if isinstance(message, str) and message else cause
        return cause[:MAX_CAUSE_CHARS]
    return 'See AWS cloudwatch logs for error cause.'


def success_output(event):
    """The task output of a successful run. A Lambda attempt that converted but left referenced files
    out answers a message in place of ``Success``; it travels as ``message``, as a preview run that
    produced nothing reports its reason."""
    output = {'status': 'Pipeline Success'}
    attempt = event.get('attempt') if isinstance(event.get('attempt'), dict) else {}
    payload = attempt.get('Payload') if isinstance(attempt.get('Payload'), dict) else {}
    body = payload.get('body')
    if isinstance(body, str) and body and body != 'Success' and payload.get('escalate') is not True:
        output['message'] = body[:2000]
    return output


def lambda_handler(event, context):
    """
    ClosePipeline
    Do any final closeouts of the pipeline
    """

    logger.info("Event Input", event=event)
    logger.info(f"Context Input: {context}")

    externalSfnTaskToken = event.get('externalSfnTaskToken', "")

    if("error" not in event):
        logger.info("Pipeline Success")
    else:
        logger.error("Pipeline Failure")
        logger.error(event["error"])

    if (externalSfnTaskToken != None and externalSfnTaskToken != ""):
        if("error" not in event):
            sfn.send_task_success(
                taskToken=event['externalSfnTaskToken'],
                output=json.dumps(success_output(event))
            )
            logger.info("Sent external task token: success")
        else:
            error = event["error"]
            sfn.send_task_failure(
                taskToken=event['externalSfnTaskToken'],
                error='Pipeline Failure: ' + str(error.get("Error", "Error"))[:200],
                cause=failure_cause(error, container_failure_message(event) if is_batch_failure(error) else None)
            )
            logger.info("Sent external task token: error")

    return event
