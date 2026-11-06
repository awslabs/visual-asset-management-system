# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Task Lambda of the large object copy state machine (one function, five actions).

The state machine (infra: lambdaBuilder/largeObjectCopyEngine.ts) runs, per job:

    Prepare -> Map(part batches) { CopyBatch } -> Complete -> Notify
    any state's Catch -> Fail -> Notify -> Fail state

- prepare:   pin the source version, refuse blocked or oversized objects, plan the parts, create the
             destination multipart upload with the final metadata, content headers and bucket-owner
             ACL (reused when the job already has one), and return the batches.
- copyBatch: copy the missing parts of one batch with UploadPartCopy (ListParts first, so a retry
             copies only what is missing). An in-VPC copy between Regions streams the parts instead,
             since an S3 interface endpoint does not serve UploadPartCopy across Regions.
- complete:  verify the parts, complete the upload, check the new version, delete the staged source
             (by version), run the post-landing side effects (asset type, subscription email, audit)
             and mark the job SUCCEEDED.
- fail:      abort the multipart upload, delete an upload's staged object (a workflow output's staged
             object is kept for inspection), audit, and mark the job FAILED with a generic reason.
- notify:    count the job on its ingestion batch exactly once and, when the batch is complete,
             report it once: SendTaskSuccess to the waiting workflow, or (for a workflow running a
             definition that does not wait) an asynchronous finalize of its output step.

Invoked only by the state machine; there is no API route. Every input is the job document the
starter built, and the starter validated the source location against the upload record.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from common import largeObjectCopy as loc
from common.resourceNames import ResourceKeys, get_table_name
from common.s3 import (
    bucket_region_for_name,
    is_object_version_archived,
    lambdas_in_vpc,
    list_all_objects,
    region_routing_s3_client,
    register_bucket_region,
    s3_client_for_region,
    validateUnallowedFileExtensionAndContentType,
)
from common.s3MetadataKeys import COPY_PRESERVED_HEADER_FIELDS, replace_metadata_copy_args
from common.s3PathPatterns import PREVIEW_FILE_PATTERN
from customLogging.auditLogging import log_file_upload
from customLogging.logger import safeLogger

logger = safeLogger(service_name="LargeObjectCopy")

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
# Matches the largest part-copy pool (threadsPerBatch is validated to at most 32).
s3_pool_config = Config(retries={"max_attempts": 8, "mode": "adaptive"}, max_pool_connections=32)

# Sample size for the asset-type classification after a file lands (same as the queue consumer).
ASSET_TYPE_DETECTION_SAMPLE_SIZE = 1000
# Parts streamed at once for an in-VPC cross-Region copy, which holds each part in memory.
STREAMED_COPY_THREADS = 2

try:
    jobs_table_name = get_table_name(ResourceKeys.LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    send_email_function_name = os.environ["SEND_EMAIL_FUNCTION_NAME"]
    # Output step function, invoked to finalize an execution whose state machine does not wait.
    process_output_function_name = os.environ["PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

s3 = region_routing_s3_client()
dynamodb = boto3.resource("dynamodb", config=retry_config)
dynamodb_client = boto3.client("dynamodb", config=retry_config)
sfn_client = boto3.client("stepfunctions", config=retry_config)
lambda_client = boto3.client("lambda", config=retry_config)
jobs_table = dynamodb.Table(jobs_table_name)
asset_table = dynamodb.Table(asset_table_name)


def _register_regions(job):
    register_bucket_region(job["source"].get("bucket"), job["source"].get("region"))
    register_bucket_region(job["dest"].get("bucket"), job["dest"].get("region"))


def _client_for_bucket(bucket):
    return s3_client_for_region(bucket_region_for_name(bucket))


def _streams_parts(job):
    """True when parts cannot be copied server side: Lambdas in the VPC and buckets in two Regions."""
    return (lambdas_in_vpc()
            and bucket_region_for_name(job["source"]["bucket"])
            != bucket_region_for_name(job["dest"]["bucket"]))


def _error_code(error):
    return getattr(error, "response", {}).get("Error", {}).get("Code", "")


def _ensure_not_cancelled(job_id):
    if loc.get_job_row(jobs_table, job_id).get("status") == loc.STATUS_CANCELLED:
        raise loc.CopyRefused(loc.REASON_CANCELLED)


# --------------------------------------------------------------------------------------------- #
# prepare
# --------------------------------------------------------------------------------------------- #

def prepare(job):
    _register_regions(job)
    job_id = job["jobId"]
    _ensure_not_cancelled(job_id)
    source = job["source"]
    dest = job["dest"]

    # Previews never exceed 5 MB and keep the synchronous path; refuse defensively.
    if job.get("uploadType") == "assetPreview" or PREVIEW_FILE_PATTERN in (job.get("relativeKey") or ""):
        raise loc.CopyRefused("Preview files are not copied by the large object copy engine.")

    head_args = {"Bucket": source["bucket"], "Key": source["key"]}
    if source.get("versionId"):
        head_args["VersionId"] = source["versionId"]
    try:
        head = s3.head_object(**head_args)
    except ClientError as e:
        if _error_code(e) in ("404", "NoSuchKey", "NotFound", "NoSuchVersion"):
            raise loc.SourceChanged(loc.REASON_SOURCE_CHANGED)
        raise
    if source.get("etag") and head.get("ETag") and head["ETag"] != source["etag"]:
        raise loc.SourceChanged(loc.REASON_SOURCE_CHANGED)

    # The content check still runs before anything lands, on the staged object and on the name it
    # lands under.
    content_type = head.get("ContentType", "") or ""
    if (not validateUnallowedFileExtensionAndContentType(source["key"], content_type)
            or not validateUnallowedFileExtensionAndContentType(dest["key"], content_type)):
        raise loc.CopyRefused(loc.REASON_BLOCKED)

    size = int(head.get("ContentLength", 0) or 0)
    cfg = loc.settings()
    part_size, part_count = loc.plan_parts(size, cfg["minPartSizeBytes"])
    batches = loc.plan_batches(part_count, cfg["partsPerBatch"])

    source = {**source, "size": size, "etag": head.get("ETag", ""),
              "versionId": head.get("VersionId") or source.get("versionId") or ""}
    job = {**job, "source": source, "partSize": part_size, "partCount": part_count,
           "threads": cfg["threadsPerBatch"]}

    row = loc.get_job_row(jobs_table, job_id)
    upload_id = row.get("uploadIdS3", "")
    if not upload_id:
        # Metadata, content headers and the ACL are set once, here, on the final object. The
        # metadata is exactly what the synchronous path's REPLACE copy writes (2 KB check included).
        copy_args = replace_metadata_copy_args(
            {**{f: head[f] for f in COPY_PRESERVED_HEADER_FIELDS if head.get(f)},
             **(job.get("contentHeaders") or {})},
            job.get("metadata") or {})
        copy_args.pop("MetadataDirective", None)
        created = _client_for_bucket(dest["bucket"]).create_multipart_upload(
            Bucket=dest["bucket"], Key=dest["key"], ACL="bucket-owner-full-control", **copy_args)
        upload_id = created["UploadId"]
        # Recorded only while the job is not cancelled: a cancel that read the row before the
        # upload existed could not abort it, so this attempt aborts it instead.
        recorded = loc.update_job_row(
            jobs_table, job_id,
            {"uploadIdS3": upload_id, "status": loc.STATUS_RUNNING, "partSize": part_size,
             "partCount": part_count, "sourceSize": size, "sourceVersionId": source["versionId"]},
            condition="attribute_not_exists(uploadIdS3) AND (attribute_not_exists(#s) OR #s <> :c)",
            condition_values={":c": loc.STATUS_CANCELLED})
        if not recorded:
            # Cancelled meanwhile, or a concurrent attempt recorded its upload first: drop this
            # upload, and use the recorded one unless the job was cancelled.
            _abort_quietly(dest["bucket"], dest["key"], upload_id)
            row = loc.get_job_row(jobs_table, job_id)
            if row.get("status") == loc.STATUS_CANCELLED:
                raise loc.CopyRefused(loc.REASON_CANCELLED)
            upload_id = row.get("uploadIdS3", "")
    job["uploadIdS3"] = upload_id
    logger.info(f"Prepared copy job {job_id}: {size} bytes in {part_count} parts of {part_size} "
                f"bytes, {len(batches)} batches")
    return {"job": job, "batches": batches}


def _abort_quietly(bucket, key, upload_id):
    try:
        _client_for_bucket(bucket).abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
    except Exception as e:
        logger.warning(f"Could not abort multipart upload {upload_id}: {e}")


# --------------------------------------------------------------------------------------------- #
# copyBatch
# --------------------------------------------------------------------------------------------- #

def _listed_parts(bucket, key, upload_id, start=1, end=None):
    """{part_number: (size, etag)} of the parts already uploaded in [start, end] (all pages)."""
    client = _client_for_bucket(bucket)
    parts = {}
    marker = start - 1
    while True:
        kwargs = {"Bucket": bucket, "Key": key, "UploadId": upload_id, "PartNumberMarker": marker}
        if end is not None:
            kwargs["MaxParts"] = max(1, min(1000, end - marker))
        response = client.list_parts(**kwargs)
        for part in response.get("Parts", []) or []:
            number = int(part["PartNumber"])
            if end is not None and number > end:
                return parts
            parts[number] = (int(part.get("Size", 0)), part.get("ETag", ""))
        if not response.get("IsTruncated"):
            return parts
        marker = int(response.get("NextPartNumberMarker", marker))
        if end is not None and marker >= end:
            return parts


def _copy_part(job, number):
    source = job["source"]
    dest = job["dest"]
    first, last = loc.part_range(number, job["partSize"], source["size"])
    dest_client = _client_for_bucket(dest["bucket"])
    try:
        if _streams_parts(job):
            get_args = {"Bucket": source["bucket"], "Key": source["key"],
                        "Range": f"bytes={first}-{last}", "IfMatch": source["etag"]}
            if source.get("versionId"):
                get_args["VersionId"] = source["versionId"]
            body = _client_for_bucket(source["bucket"]).get_object(**get_args)["Body"].read()
            dest_client.upload_part(Bucket=dest["bucket"], Key=dest["key"], UploadId=job["uploadIdS3"],
                                    PartNumber=number, Body=body)
            return
        copy_source = {"Bucket": source["bucket"], "Key": source["key"]}
        if source.get("versionId"):
            copy_source["VersionId"] = source["versionId"]
        dest_client.upload_part_copy(
            Bucket=dest["bucket"], Key=dest["key"], UploadId=job["uploadIdS3"], PartNumber=number,
            CopySource=copy_source, CopySourceIfMatch=source["etag"],
            CopySourceRange=f"bytes={first}-{last}")
    except ClientError as e:
        if _error_code(e) in ("PreconditionFailed", "NoSuchKey", "NoSuchVersion", "404"):
            raise loc.SourceChanged(loc.REASON_SOURCE_CHANGED)
        if _error_code(e) == "NoSuchUpload":
            # The upload was aborted (cancelled job): nothing left to copy into.
            raise loc.CopyRefused(loc.REASON_CANCELLED)
        raise


def copy_batch(job, batch):
    _register_regions(job)
    start, end = int(batch["startPart"]), int(batch["endPart"])
    have = _listed_parts(job["dest"]["bucket"], job["dest"]["key"], job["uploadIdS3"], start, end)
    missing = []
    for number in range(start, end + 1):
        first, last = loc.part_range(number, job["partSize"], job["source"]["size"])
        if (have.get(number) or (None,))[0] != last - first + 1:
            missing.append(number)
    if missing:
        threads = STREAMED_COPY_THREADS if _streams_parts(job) else int(job.get("threads") or 8)
        with ThreadPoolExecutor(max_workers=max(1, min(threads, len(missing)))) as pool:
            list(pool.map(lambda n: _copy_part(job, n), missing))
        logger.info(f"Copy job {job['jobId']}: copied parts {missing[0]}..{missing[-1]} "
                    f"({len(missing)} of batch {start}-{end})")
    _record_batch_copied(job["jobId"], start, end - start + 1)
    return {"copied": len(missing)}


def _record_batch_copied(job_id, start_part, part_count):
    """Count a fully copied batch on the job row exactly once (display-only part progress): the
    batch's first part number joins the copiedBatches set in the same write that adds its parts, and
    the write is skipped when the set already holds it (a retried or resumed batch). Best-effort."""
    try:
        jobs_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="ADD partsCopied :n, copiedBatches :b",
            ConditionExpression="attribute_exists(jobId) AND NOT contains(copiedBatches, :s)",
            ExpressionAttributeValues={":n": int(part_count), ":b": {int(start_part)},
                                       ":s": int(start_part)})
    except ClientError as e:
        if _error_code(e) != "ConditionalCheckFailedException":
            logger.info(f"Could not record the copy progress of job {job_id} (non-critical): {e}")
    except Exception as e:
        logger.info(f"Could not record the copy progress of job {job_id} (non-critical): {e}")


# --------------------------------------------------------------------------------------------- #
# complete
# --------------------------------------------------------------------------------------------- #

def complete(job):
    _register_regions(job)
    dest = job["dest"]
    source = job["source"]
    client = _client_for_bucket(dest["bucket"])
    expected = {n: (lambda r: r[1] - r[0] + 1)(loc.part_range(n, job["partSize"], source["size"]))
                for n in range(1, job["partCount"] + 1)}
    try:
        # ListParts answers NoSuchUpload when an earlier attempt of this state completed the upload.
        parts = _listed_parts(dest["bucket"], dest["key"], job["uploadIdS3"])
        if {n: size for n, (size, _etag) in parts.items()} != expected:
            raise RuntimeError(f"Copy job {job['jobId']} has {len(parts)} of {len(expected)} "
                               f"parts at their planned sizes")
        client.complete_multipart_upload(
            Bucket=dest["bucket"], Key=dest["key"], UploadId=job["uploadIdS3"],
            MultipartUpload={"Parts": [{"PartNumber": n, "ETag": parts[n][1]} for n in sorted(parts)]})
    except ClientError as e:
        if _error_code(e) != "NoSuchUpload":
            raise
        # Completed by an earlier attempt of this state (its result was lost): the object it wrote
        # is the current version when its size and multipart part count match the plan.
        logger.info(f"Copy job {job['jobId']}: upload already completed, verifying the object")
    head = client.head_object(Bucket=dest["bucket"], Key=dest["key"])
    etag = (head.get("ETag") or "").strip('"')
    if int(head.get("ContentLength", -1)) != int(source["size"]) or not etag.endswith(f"-{job['partCount']}"):
        raise RuntimeError(f"Copy job {job['jobId']}: the landed object does not match the plan")
    version_id = head.get("VersionId", "") or ""

    if job.get("deleteSourceOnSuccess"):
        _delete_source(source)
    _after_landing(job)
    loc.update_job_row(jobs_table, job["jobId"], {"status": loc.STATUS_SUCCEEDED,
                                                  "destVersionId": version_id})
    return {"job": {**job, "destVersionId": version_id}}


def _delete_source(source):
    """Delete the staged object's pinned version (a delete without a version id only adds a marker
    on a versioned bucket). Best-effort, as the synchronous path's temp delete is."""
    try:
        client = _client_for_bucket(source["bucket"])
        if source.get("versionId"):
            try:
                client.delete_object(Bucket=source["bucket"], Key=source["key"],
                                     VersionId=source["versionId"])
                return
            except ClientError as e:
                if _error_code(e) != "AccessDenied":
                    raise
        client.delete_object(Bucket=source["bucket"], Key=source["key"])
    except Exception as e:
        logger.warning(f"Could not delete the staged object {source.get('key')}: {e}")


def _audit_event(job):
    user_id = job.get("changeUserId") or "SYSTEM_USER"
    cross_call = {"userName": user_id}
    if user_id != "SYSTEM_USER":
        cross_call["mfaEnabled"] = False
    return {"lambdaCrossCall": cross_call}


def _audit(job, denied, reason, status):
    try:
        log_file_upload(_audit_event(job), job.get("databaseId"), job.get("assetId"),
                        job.get("relativeKey"), denied, reason,
                        {"uploadId": job.get("uploadId"), "uploadType": job.get("uploadType"),
                         "status": status, "processingType": "large_file_async",
                         "copyJobId": job.get("jobId")})
    except Exception as e:
        logger.exception(f"Failed to write the audit entry of copy job {job.get('jobId')}: {e}")


def _determine_asset_type(bucket, prefix):
    """Empty / single file (its extension) / folder classification, as the queue consumer does."""
    files = []
    for item in list_all_objects(bucket, prefix, client=s3, max_objects=ASSET_TYPE_DETECTION_SAMPLE_SIZE):
        if item["Key"].endswith("/"):
            continue
        try:
            if is_object_version_archived(bucket, item["Key"], None, client=s3):
                continue
        except Exception:
            pass
        files.append(item["Key"])
        if len(files) > 1:
            return "folder"
    if not files:
        return None
    name = os.path.basename(files[0])
    return ("." + name.split(".")[-1].lower()) if "." in name else "unknown"


def _after_landing(job):
    """Asset type, subscription email and audit entry for a landed file. Best-effort: the file is in
    the asset, so a failure here is logged and does not fail the job."""
    try:
        asset = asset_table.get_item(Key={"databaseId": job["databaseId"], "assetId": job["assetId"]}).get("Item")
        if asset:
            prefix = (asset.get("assetLocation") or {}).get("Key", f"{job['assetId']}/")
            asset_type = _determine_asset_type(job["dest"]["bucket"], prefix) or asset.get("assetType") or "none"
            asset_table.update_item(
                Key={"databaseId": job["databaseId"], "assetId": job["assetId"]},
                UpdateExpression="SET assetType = :t",
                ConditionExpression="attribute_exists(databaseId) AND attribute_exists(assetId)",
                ExpressionAttributeValues={":t": asset_type})
    except Exception as e:
        logger.exception(f"Could not update the asset type after copy job {job['jobId']}: {e}")
    try:
        lambda_client.invoke(FunctionName=send_email_function_name, InvocationType="Event",
                             Payload=json.dumps({"databaseId": job["databaseId"],
                                                 "assetId": job["assetId"]}))
    except Exception as e:
        logger.exception(f"Could not send the subscription email after copy job {job['jobId']}: {e}")
    _audit(job, False, None, "completed_async")


# --------------------------------------------------------------------------------------------- #
# fail
# --------------------------------------------------------------------------------------------- #

def _failure_reason(error):
    """A generic reason for the job row and the execution error, from the caught Step Functions
    error ({Error, Cause})."""
    name = (error or {}).get("Error", "") or ""
    cause = (error or {}).get("Cause", "") or ""
    message = ""
    try:
        message = (json.loads(cause) or {}).get("errorMessage", "") if cause.startswith("{") else ""
    except (ValueError, AttributeError):
        message = ""
    if name == "SourceChanged":
        return loc.REASON_SOURCE_CHANGED
    if name == "CopyRefused" and message in (loc.REASON_BLOCKED, loc.REASON_TOO_LARGE,
                                              loc.REASON_CANCELLED):
        return message
    if name == "CopyRefused":
        return loc.REASON_BLOCKED if "blocked" in message.lower() else loc.REASON_COPY_FAILED
    return loc.REASON_COPY_FAILED


def fail(job, error):
    _register_regions(job)
    reason = _failure_reason(error)
    logger.error(f"Copy job {job.get('jobId')} failed: {(error or {}).get('Error', '')}")
    upload_id = job.get("uploadIdS3") or loc.get_job_row(jobs_table, job["jobId"]).get("uploadIdS3", "")
    if upload_id:
        # Failed copies discard their partial upload at once.
        _abort_quietly(job["dest"]["bucket"], job["dest"]["key"], upload_id)
    if job.get("kind") == loc.KIND_UPLOAD:
        # An upload's staged object is not kept (the queue consumer's cleanup behaves the same way);
        # a workflow output's stays under the run's pipelines/ folder for inspection or a re-run.
        _delete_source(job["source"])
    _audit(job, True, reason, "rejected_async")
    loc.update_job_row(jobs_table, job["jobId"], {"status": loc.STATUS_FAILED, "error": reason},
                       condition="attribute_not_exists(#s) OR #s <> :c",
                       condition_values={":c": loc.STATUS_CANCELLED})
    return {"job": {**job, "error": reason}}


# --------------------------------------------------------------------------------------------- #
# notify
# --------------------------------------------------------------------------------------------- #

def notify(job, status):
    row = loc.get_job_row(jobs_table, job["jobId"])
    final_status = row.get("status") if row.get("status") in loc.TERMINAL_JOB_STATUSES else status
    batch_id = job.get("batchId", "")
    counted = loc.count_job_terminal(dynamodb_client, jobs_table_name, job["jobId"], batch_id,
                                     final_status)
    if counted and job.get("workflowExecutionId") and batch_id:
        _record_progress(job, final_status)
    if batch_id:
        report_batch_if_complete(batch_id)
    return {"status": final_status}


def _record_progress(job, status):
    """Best-effort progress count on the main execution row (display only)."""
    try:
        table = dynamodb.Table(get_table_name(ResourceKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2))
        table.update_item(
            Key={"workflowExecutionId": job["workflowExecutionId"],
                 "workflowDatabaseId:workflowId": f"{job.get('workflowDatabaseId', '')}:{job.get('workflowId', '')}"},
            UpdateExpression="ADD #oi.filesDone :one, #oi.bytesDone :b",
            ConditionExpression="attribute_exists(#oi)",
            ExpressionAttributeNames={"#oi": "outputIngestion"},
            ExpressionAttributeValues={
                ":one": 1, ":b": int(job["source"].get("size") or 0) if status == loc.STATUS_SUCCEEDED else 0})
    except Exception as e:
        logger.info(f"Could not record ingestion progress (non-critical): {e}")


def report_batch_if_complete(batch_id):
    """Report a complete batch exactly once (the output step's await mode does the same after it
    registers its task token, since the copies may finish first)."""
    claimed = loc.claim_notification(jobs_table, batch_id)
    if not claimed:
        return False
    return loc.deliver_batch_report(jobs_table, claimed, sfn_client, lambda_client,
                                    process_output_function_name)


ACTIONS = ("prepare", "copyBatch", "complete", "fail", "notify")


def lambda_handler(event, context):
    action = (event or {}).get("action")
    job = (event or {}).get("job") or {}
    if action not in ACTIONS or not job.get("jobId"):
        raise ValueError("Unknown copy engine action or missing job")
    logger.info(f"Copy engine action {action} for job {job.get('jobId')}")
    if action == "prepare":
        return prepare(job)
    if action == "copyBatch":
        return copy_batch(job, event.get("batch") or {})
    if action == "complete":
        return complete(job)
    if action == "fail":
        return fail(job, event.get("error") or {})
    return notify(job, event.get("status") or loc.STATUS_FAILED)
