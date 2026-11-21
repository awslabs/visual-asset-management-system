#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Step Functions Task Token callback. Reports back to the outer VAMS workflow.

Invoked two ways: by the inner state machine's own end states, and by an Amazon EventBridge rule on
the inner execution's FAILED, TIMED_OUT and ABORTED status changes. The rule covers the ends no state
inside the execution can reach, a state machine timeout or a stopped execution, which would otherwise
leave the outer workflow task waiting out its own timeout and the claim in place.

A run that does not succeed also releases the dedup claim the gate wrote, so the same content is
eligible again for the next trigger. A successful run keeps its claim and marks it completed: that
record is what collapses the per-file trigger fan-out and repeat triggers on unchanged content, and
a completed claim never goes stale. It also retires the asset's claims on other content, so a later
trigger on earlier content (a reverted version) uploads again.
"""
import json
import os

import boto3
from botocore.config import Config
from customLogging.logger import safeLogger

import mirisClaim

logger = safeLogger(service="MirisUploadPipelineEnd")
retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
sfn = boto3.client("stepfunctions", region_name=os.environ["AWS_REGION"], config=retry_config)
s3_client = boto3.client("s3", region_name=os.environ["AWS_REGION"], config=retry_config)

EXECUTION_STATUS_DETAIL_TYPE = "Step Functions Execution Status Change"
FAILURE_ERROR = "MirisUploadFailed"
CAUSE_MAX_CHARS = 256
# The ambiguous_root reason names the tied roots from the container's report, so it carries the
# job's own output and is bounded by itself, within the 32768-character cause limit.
AMBIGUOUS_ROOT_EXIT_CODE = 10
AMBIGUOUS_ROOT_CAUSE_MAX_CHARS = 4096
AMBIGUOUS_ROOT_PATH_MAX_CHARS = 300
AMBIGUOUS_ROOTS_NAMED = 10

# Error codes Step Functions returns for a token that has already been reported, timed out, or
# belongs to a workflow that no longer exists. The EventBridge route reaches a FAILED execution
# whose failure branch already reported, so these are the expected outcome there.
CLOSED_TOKEN_ERROR_CODES = ("TaskTimedOut", "TaskDoesNotExist", "InvalidToken")

# The container's exit codes (EXIT_* in container/__main__.py) and the reason the workflow shows.
# The container never deletes the Miris asset it created, so the reasons for the codes that can
# follow its creation name the asset's vams-asset tag ({tag}), by which it can be found in Miris.
EXIT_CODE_REASONS = {
    1: "The Miris upload container failed with an unexpected error. See the container log. "
       "Any Miris asset the run created is kept; find it in Miris by the tag {tag}.",
    2: "The Miris upload container was started without a complete pipeline definition.",
    3: "The file is not a USD format the Miris upload accepts (.usd, .usda, .usdc or .usdz).",
    4: "The root USD file and the files it references are larger than the maximum upload size "
       "(app.addons.useMirisStreaming.upload.maxAssetSizeBytes), or the asset does not fit on the "
       "task's disk (file_too_large).",
    5: "The root USD file references layers or assets that are not part of the VAMS asset "
       "(unresolved_references). Upload them, or re-export with relative paths.",
    6: "The asset has no USD file to upload, every USD file in it is referenced by another, or the "
       "file a manual run was started on is not one of its USD files.",
    7: "The run has no metadata output location for the Miris stream metadata.",
    8: "Miris rejected the upload or could not process it. See the container log. "
       "Any Miris asset the run created is kept; find it in Miris by the tag {tag}.",
    10: "The asset has several root USD files (ambiguous_root); the container log's ambiguous_root "
        "entry lists them. Set the asset metadata miris_root_file to the one to stream, or remove or "
        "move the files that are not the scene.",
    11: "A file the root's layers name, even in an unselected variant or on an inactive prim, is "
        "malformed or in a format USD cannot read such as Alembic or OBJ (unreadable_layer). The "
        "container log names it; fix or convert it, or remove it or its reference.",
    12: "The USD file references a layer or asset outside the VAMS asset (reference_outside_asset), "
        "which is never packaged. The container log's reference_outside_asset entry names the files; "
        "reference files inside the asset with relative paths.",
    13: "The file version this run was started on no longer exists (input_version_missing). The "
        "container log's input_version_missing entry names the file and the version; start the run "
        "again on a version that exists.",
    14: "The Miris upload job started too late to finish within the pipeline's time limit, for "
        "example after a long AWS Batch job queue wait (insufficient_time). Nothing was uploaded to "
        "Miris; start the run again.",
    15: "The root USD file defines no geometry, so it renders nothing (root_not_renderable). The "
        "container log's root_not_renderable entry names it; stream a file that defines geometry.",
    16: "The asset metadata miris_root_file does not name a USD file in the asset (root_file_invalid). "
        "Set it to the root file's asset-relative path, such as /scene.usda, or remove it.",
    17: "The asset's text USD layers are too large to parse in the upload task's memory "
        "(text_usd_too_large); the container log names the largest. Converting them to binary "
        ".usdc usually reduces memory, especially for large arrays.",
    18: "A texture in the upload is WebP, which Miris does not accept (unsupported_texture_webp). The "
        "container log's unsupported_texture_webp entry names the files; convert them to PNG or JPEG.",
    19: "A file of the asset changed or was removed while the run was reading it "
        "(asset_changed_during_run); the container log's asset_changed_during_run entry names it. "
        "Nothing was uploaded to Miris; run the upload again once the asset's files are in place.",
}

EXECUTION_STATUS_REASONS = {
    "TIMED_OUT": "The Miris upload did not finish within the pipeline's time limit.",
    "ABORTED": "The Miris upload was stopped before it finished.",
    "FAILED": "The Miris upload pipeline failed. See the pipeline's state machine log.",
}

CONTAINER_TIME_LIMIT_REASON = "The Miris upload container exceeded its time limit and was stopped."


def _parse_json(text):
    if isinstance(text, dict):
        return text
    try:
        parsed = json.loads(text or "")
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _batch_exit_code(job):
    """The container exit code from a Batch job description, or None when the container never ran."""
    code = (job.get("Container") or {}).get("ExitCode")
    if code is None:
        attempts = job.get("Attempts") or []
        if attempts:
            code = (attempts[-1].get("Container") or {}).get("ExitCode")
    return code


def container_exit_code(error_cause):
    """The container exit code a failed Batch state's cause carries, or None."""
    cause = _parse_json(error_cause)
    if cause is None or not ("Container" in cause or "JobId" in cause):
        return None
    return _batch_exit_code(cause)


def ambiguous_root_reason(report_uri):
    """The ambiguous_root reason naming the tied roots the container listed in the run's report,
    or None when the report cannot be read (the caller keeps the reason that points to the log)."""
    bucket, _, key = (report_uri or "").replace("s3://", "", 1).partition("/")
    if not (report_uri or "").startswith("s3://") or not bucket or not key:
        return None
    try:
        report = json.loads(s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8"))
        roots = [root[:AMBIGUOUS_ROOT_PATH_MAX_CHARS] for root in report["roots"]
                 if isinstance(root, str) and root][:AMBIGUOUS_ROOTS_NAMED]
        count = max(int(report.get("count", len(roots))), len(roots))
    except Exception as e:
        logger.warning(f"Could not read the ambiguous_root report {report_uri}: {e}")
        return None
    if not roots:
        return None
    more = f" and {count - len(roots)} more" if count > len(roots) else ""
    return (f"The asset has several root USD files (ambiguous_root): {', '.join(roots)}{more}. Set the "
            f"asset metadata miris_root_file to the one to stream, or remove or move the files that are "
            f"not the scene.")


def asset_tag(asset_id):
    """The tag the container gives every Miris asset it creates for the VAMS asset `asset_id`."""
    return f"vams-asset-{asset_id or '<assetId>'}"


def failure_reason(error_type, error_cause, asset_id=""):
    """A user-facing reason for a failed state: the Batch job's exit code, a Lambda error message,
    or a generic reason. Never the raw Batch job description."""
    cause = _parse_json(error_cause)
    if cause is not None and ("Container" in cause or "JobId" in cause):
        code = _batch_exit_code(cause)
        if code in EXIT_CODE_REASONS:
            return EXIT_CODE_REASONS[code].format(tag=asset_tag(asset_id))
        status_reason = cause.get("StatusReason") or (cause.get("Container") or {}).get("Reason") or ""
        if "timeout" in status_reason.lower() or "duration" in status_reason.lower():
            return CONTAINER_TIME_LIMIT_REASON
        if code is not None:
            return f"The Miris upload container exited with code {code}. See the container log."
        if status_reason:
            return f"The Miris upload container did not run: {status_reason}"
        return "The Miris upload container did not complete. See the container log."
    if cause is not None and cause.get("errorMessage"):
        return f"The Miris upload could not be prepared: {cause['errorMessage']}"
    if error_type == "States.Timeout":
        return EXECUTION_STATUS_REASONS["TIMED_OUT"]
    return "The Miris upload failed. See the pipeline's state machine log."


def _report_failure(task_token, reason, max_chars=CAUSE_MAX_CHARS):
    try:
        sfn.send_task_failure(taskToken=task_token, error=FAILURE_ERROR, cause=reason[:max_chars])
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if code in CLOSED_TOKEN_ERROR_CODES:
            logger.info(f"Workflow task already closed ({code}); nothing to report")
            return
        raise


def _release_claim(state, task_token):
    """Release the gate's claim if this execution still holds it."""
    mirisClaim.release_claim(
        s3_client,
        logger,
        state.get("inputOutputS3AssetAuxiliaryFilesPath", ""),
        state.get("databaseId", ""),
        state.get("assetId", ""),
        state.get("claimFingerprint", ""),
        mirisClaim.owner_id(task_token),
    )


def _handle_execution_status_change(event):
    """The EventBridge route: the inner execution ended without its own end state reporting."""
    detail = event.get("detail") or {}
    status = detail.get("status", "")
    execution_input = _parse_json(detail.get("input"))
    logger.info(f"Inner execution {detail.get('executionArn', '')} ended with status {status}")
    if execution_input is None:
        logger.warning("Execution status event carried no readable input; nothing to report")
        return {"statusCode": 200, "body": "no-op"}

    task_token = execution_input.get("externalSfnTaskToken", "")
    _release_claim(execution_input, task_token)
    if task_token:
        _report_failure(task_token, EXECUTION_STATUS_REASONS.get(status, EXECUTION_STATUS_REASONS["FAILED"]))
    return {"statusCode": 200, "body": "ok"}


def lambda_handler(event, context):
    if event.get("detail-type") == EXECUTION_STATUS_DETAIL_TYPE:
        return _handle_execution_status_change(event)

    logger.info("MirisUploadPipelineEnd received event")
    task_token = event.get("externalSfnTaskToken")
    status = event.get("status", "success")

    if status == "success":
        mirisClaim.complete_claim(
            s3_client,
            logger,
            event.get("inputOutputS3AssetAuxiliaryFilesPath", ""),
            event.get("databaseId", ""),
            event.get("assetId", ""),
            event.get("claimFingerprint", ""),
            mirisClaim.owner_id(task_token),
        )
        mirisClaim.retire_superseded_claims(
            s3_client,
            logger,
            event.get("inputOutputS3AssetAuxiliaryFilesPath", ""),
            event.get("databaseId", ""),
            event.get("assetId", ""),
            event.get("claimFingerprint", ""),
        )
    else:
        _release_claim(event, task_token)

    if not task_token:
        logger.warning("No external task token in event; nothing to call back to")
        return {"statusCode": 200, "body": "no-op"}

    if status == "success":
        sfn.send_task_success(taskToken=task_token, output=json.dumps({"status": "success"}))
    else:
        error_cause = event.get("errorCause", "")
        detailed = None
        if container_exit_code(error_cause) == AMBIGUOUS_ROOT_EXIT_CODE:
            detailed = ambiguous_root_reason(event.get("ambiguousRootReportS3Uri", ""))
        if detailed:
            _report_failure(task_token, detailed, AMBIGUOUS_ROOT_CAUSE_MAX_CHARS)
        else:
            _report_failure(
                task_token,
                failure_reason(event.get("errorType", ""), error_cause, event.get("assetId", "")),
            )

    return {"statusCode": 200, "body": "ok"}
