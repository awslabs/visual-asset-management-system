# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Terminal callback of zip download runs (system trigger ``Zip-Download``).

Registered in the system-pipeline callback registry (common/workflows/systemPipelines.py,
CALLBACK_FUNCTIONS_BY_SYSTEM_TRIGGER) and invoked asynchronously by whichever terminal writer records
a zip download run's terminal status: the end state, the error handler, an abort from the workflow
executions views, a group abort, or the reconcile of a run stopped outside VAMS.

It finalizes the job row the container could not (common/zipDownloads.finalize_ended_run): a run
that succeeded without the archive object, a run aborted from the executions views ("Cancelled from
workflow executions"), a failure or timeout the container never reported. It aborts the job's open
uploads before it decides, and an archive that already landed wins (SUCCEEDED) whatever the run's
status. It is idempotent: every
write conditions on the row still being non-terminal and on the row belonging to this run, and
Lambda's asynchronous retries replay the same payload. It trusts only the stored row and the
server-only context; the payload's status decides which rule applies.

No API route reaches this function; only the terminal writers' roles may invoke it.
"""

import boto3
from botocore.config import Config

from common import zipDownloads as zd
from common.resourceNames import ResourceKeys, get_bucket_name, get_table_name
from common.s3 import deployment_region, region_routing_s3_client, register_bucket_region
from common.workflows import systemPipelines as sp
from customLogging.logger import safeLogger

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
s3 = region_routing_s3_client()
logger = safeLogger(service_name="ZipDownloadCallback")

try:
    zip_table_name = get_table_name(ResourceKeys.ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE)
    aux_bucket_name = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

# The auxiliary bucket is the stack's own, in this deployment's Region: every abort, list and delete
# here is signed for that Region without an asset buckets table read, which this function's role does
# not hold and which would log AccessDenied on every call (#1125 D17).
register_bucket_region(aux_bucket_name, deployment_region())
zip_table = dynamodb.Table(zip_table_name)


def _ignored(reason):
    logger.info(f"Zip download callback ignored: {reason}")
    return {"status": "ignored", "reason": reason}


def handle_callback(payload) -> dict:
    if not isinstance(payload, dict):
        return _ignored("payload is not an object")
    if payload.get("callbackType") != sp.CALLBACK_TYPE:
        return _ignored("not a system pipeline terminal callback")
    if payload.get("schemaVersion") != sp.CALLBACK_SCHEMA_VERSION:
        return _ignored("unsupported callback schema version")
    if payload.get("triggerType") != zd.ZIP_DOWNLOAD_STORED_TRIGGER:
        return _ignored("not a zip download run")
    context = payload.get("systemExecutionContext")
    if not isinstance(context, dict):
        return _ignored("no system execution context")
    user_id, request_id = context.get("userId"), context.get("requestId")
    if not isinstance(user_id, str) or not user_id or not zd.is_request_id(request_id):
        return _ignored("context does not name a zip download")
    execution_id = payload.get("workflowExecutionId") or ""

    row = zip_table.get_item(Key=zd.row_key(user_id, request_id), ConsistentRead=True).get("Item")
    if not row:
        return _ignored("zip download no longer exists")
    if row.get("databaseId") != context.get("databaseId") or row.get("assetId") != context.get("assetId"):
        logger.warning(f"Zip download callback context does not match job {request_id}")
        return _ignored("context does not match the job")
    row_execution = row.get("workflowExecutionId") or ""
    if row_execution and execution_id and row_execution != execution_id:
        return _ignored("callback is for another run of the job")

    # Aborts the job's open uploads before deciding, so a landed archive wins (finalize_ended_run).
    status, changed = zd.finalize_ended_run(
        zip_table, s3, aux_bucket_name, row, payload.get("executionStatus", ""),
        payload.get("executionError", "") or "", logger)
    if not status:
        # Already terminal (the container, the user's cancel or an earlier delivery won). A run that
        # did not succeed still gets its incomplete uploads cleaned, which is idempotent.
        if payload.get("executionStatus") != "SUCCEEDED" and row.get("status") != zd.STATUS_SUCCEEDED:
            prefix = zd.request_prefix(row["databaseId"], row["assetId"], row["userId"], row["requestId"])
            zd.abort_open_uploads(s3, aux_bucket_name, prefix, logger)
            # Not final either: the delayed sweep aborts the recorded upload again later.
            zd.schedule_delayed_sweep(row, logger)
        # A job that ended without an archive (the container's FAILED, the user's cancel) keeps no
        # working objects: this also removes a resolved manifest the container wrote after a cancel.
        if row.get("status") in (zd.STATUS_FAILED, zd.STATUS_CANCELLED):
            zd.delete_working_objects(s3, aux_bucket_name, row, logger)
        return {"status": "unchanged", "jobStatus": row.get("status", "")}
    logger.info(f"Zip download {request_id}: run {execution_id} ended "
                f"{payload.get('executionStatus')}; job {'set to ' + status if changed else 'unchanged'}")
    return {"status": "finalized" if changed else "unchanged", "jobStatus": status}


def handle_sweep(event) -> dict:
    """One pass of a job's delayed sweep (the zip download sweep state machine, #1125 D16): abort
    the archive uploads the job recorded that are still listed, unless its archive landed."""
    result = zd.sweep_recorded_uploads(zip_table, s3, aux_bucket_name, event.get("zipSweep") or {},
                                       final=bool(event.get("final")), logger=logger)
    logger.info(f"Zip download delayed sweep ({'final' if event.get('final') else 'first'} pass): "
                f"{result}")
    return result


def lambda_handler(event, context):
    if isinstance(event, dict) and event.get("action") == "sweep":
        # Raises on a failed check or listing, so the state machine retries the pass.
        return handle_sweep(event)
    try:
        return handle_callback(event)
    except Exception as e:
        # Raising makes Lambda retry the asynchronous delivery, which is safe (idempotent writes).
        logger.exception(f"Zip download callback failed: {e}")
        raise
