#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance jobs handler (SQS-invoked, the compliance job queue `ComplianceJobQueue`).

Runs the two compliance jobs that continue themselves, one message per run, until they are done:

  - the move of a deleted database's compliance data under `<databaseId>#deleted`
    (`common.compliance.databaseRetirement`), queued by the database service's delete;
  - a database binding change applied to the database's records in the background
    (`complianceBindingApply`, D161), queued by the binding service's bind, change, removal or
    automatic evaluation turned on.

Each run works until its deadline (the Lambda's remaining time less `RUN_SAFETY_MARGIN_SECONDS`)
and, when the job is not done, queues its continuation on the job queue. The evaluations a binding
change queues go to the compliance trigger's queue (`COMPLIANCE_TRIGGER_QUEUE_URL`), where the
trigger evaluates them as it evaluates any asset change, with Lambda's recursive-loop detection on.
This function runs with the detection off (a large job needs more runs than it allows), so each job
counts its own runs against a limit derived from its size and stops past it
(`complianceBindingApply.run_limit`, `databaseRetirement.run_limit`).

A message carries ids only, read from a raw queue body (never an SNS envelope): the move's database
id, or the apply's database, apply and run ids. Everything a run does is read from the stored job
record and the database row; the schema, the operation and the actor are never taken from the
message. A message that names no job is logged and dropped. A run that fails is reported as a batch
item failure, so SQS redelivers its message and, past the queue's receive limit, keeps it in the
job dead-letter queue (`ComplianceJobDLQ`).
"""

import json
import os
import time

import boto3
from botocore.config import Config

from common.compliance import databaseRetirement
from common.resourceNames import ResourceKeys, get_table_name
from common.validators import validate
from customLogging.auditLogging import audited_handler, current_audit_context, log_action
from customLogging.logger import safeLogger
from handlers.compliance import complianceBindingApply as binding_apply
from handlers.compliance import complianceEvaluationStore as store

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
# The move re-scopes the deleted database's cascades; the other tables are the store's.
cascade_table = dynamodb.Table(get_table_name(ResourceKeys.COMPLIANCE_CASCADE_STORAGE_TABLE))
sqs_client = boto3.client("sqs", config=retry_config)
# Set by complianceFunctions.ts: the job queue (continuations), its dead-letter queue (a move
# stopped at its run limit is kept there), and the trigger's queue (a binding change's evaluations).
job_queue_url = os.environ.get("COMPLIANCE_JOB_QUEUE_URL", "")
job_dlq_url = os.environ.get("COMPLIANCE_JOB_DLQ_URL", "")
trigger_queue_url = os.environ.get("COMPLIANCE_TRIGGER_QUEUE_URL", "")
logger = safeLogger(service_name="ComplianceJobs")


def _record(action, **fields):
    """Record this handler's compliance system action (`common/auditActions.py`) with `log_action`,
    on the event of the invocation's audit context, so it is delivered with its other records."""
    ctx = current_audit_context()
    log_action(getattr(ctx, "event", None) or {}, action, **fields)


@audited_handler
def lambda_handler(event, context):
    """Run the job each record of one SQS batch names, and report the records whose run failed
    (`batchItemFailures`), so only those are redelivered."""
    failures = []
    for record in event.get("Records", []) or []:
        message_id = record.get("messageId", "") if isinstance(record, dict) else ""
        if not run_job(record, context):
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def run_job(record, context) -> bool:
    """Route one queue record to its job. Returns False when the run failed (the record is then
    redelivered)."""
    move_database = databaseRetirement.move_message_database(record)
    if move_database is not None:
        return run_database_move(move_database, context)
    target = binding_apply.message_target(record)
    if target is not None:
        database_id, apply_id, run_id = target
        return run_binding_apply(database_id, apply_id, run_id, context, receive_count(record))
    logger.info("Job queue record names no compliance job, skipping")
    return True


def receive_count(record):
    """How many times SQS has delivered a queue record (`ApproximateReceiveCount`, 1 when absent)."""
    try:
        return max(1, int(((record or {}).get("attributes") or {}).get(
            "ApproximateReceiveCount", "1")))
    except (TypeError, ValueError, AttributeError):
        return 1


def _remaining_seconds(context):
    return context.get_remaining_time_in_millis() / 1000 if context else 60


def run_binding_apply(database_id, apply_id, run_id, context, receive_count=1) -> bool:
    """Run a database binding change (`complianceBindingApply.run`) until this invocation's
    deadline, and queue the next message on the job queue when it is not done. Only the ids come
    from the message; the change itself is read from the stored apply and the database row.
    `receive_count` is the message's delivery count, recorded on the apply so its status knows
    whether the queue delivers the message again if this run fails. Returns False when the run
    failed, so the message is redelivered (and, past the queue's receive limit, kept in its
    dead-letter queue) rather than queued again."""
    (valid, message) = validate({
        "databaseId": {"value": database_id, "validator": "ID"},
        "applyId": {"value": apply_id, "validator": "UUID"},
        "runId": {"value": run_id, "validator": "UUID"},
    })
    if not valid:
        logger.error(f"Compliance binding apply message rejected: {message}")
        return True
    deadline = binding_apply.monotonic() + max(
        1, _remaining_seconds(context) - databaseRetirement.RUN_SAFETY_MARGIN_SECONDS)
    try:
        done = binding_apply.run(database_id, apply_id, run_id, deadline, sqs=sqs_client,
                                 queue_url=trigger_queue_url, receive_count=receive_count)
    except Exception as e:
        logger.exception(f"Compliance binding apply of database {database_id} failed: {e}")
        _record("complianceBindingApply", outcome="failed", reason="internal",
                database_id=database_id, details={"applyId": apply_id, "runId": run_id})
        return False
    # One record per run of the change (a change too large for one invocation runs again from its
    # saved progress, and records that run).
    _record("complianceBindingApply", database_id=database_id,
            details={"applyId": apply_id, "runId": run_id, "done": bool(done)})
    if not done:
        record = binding_apply.get_record(database_id) or {}
        if record.get("applyId") != apply_id or record.get("runId") != run_id:
            return True
        if not binding_apply.send_message(sqs_client, job_queue_url, record):
            # This run's own message is delivered again (its progress is saved); the apply is
            # not recorded as having a message queued meanwhile.
            binding_apply.message_not_queued(record)
            return False
        logger.info(f"Compliance binding apply of database {database_id} continues in a new message")
    return True


def retirement_tables():
    """The tables the move touches: the store's, and the cascade table."""
    return databaseRetirement.Tables(
        dynamodb=dynamodb, schema=store.schema_table, asset_state=store.asset_state_table,
        evaluation=store.evaluation_table, cascade=cascade_table, audit=store.audit_table)


def run_database_move(database_id, context) -> bool:
    """Run the move of a deleted database's compliance data (`databaseRetirement.run`) until this
    invocation's deadline, and queue the next message on the job queue when it is not done. The run
    is counted against the move's run limit first (`databaseRetirement.begin_run`); a run past it
    moves nothing (`_stop_move`). Returns False when the run failed, so the message is redelivered
    (and, past the queue's receive limit, kept in its dead-letter queue) rather than queued
    again."""
    (valid, message) = validate({"databaseId": {"value": database_id, "validator": "ID"}})
    if not valid:
        logger.error(f"Compliance move message rejected: {message}")
        return True
    tables = retirement_tables()
    deadline = time.monotonic() + max(
        1, _remaining_seconds(context) - databaseRetirement.RUN_SAFETY_MARGIN_SECONDS)
    try:
        decision, record = databaseRetirement.begin_run(tables, database_id)
        if decision == databaseRetirement.RUN_NOTHING:
            return True
        if decision == databaseRetirement.RUN_STOP:
            return _stop_move(tables, database_id, record)
        done = databaseRetirement.run(tables, database_id, deadline)
    except Exception as e:
        logger.exception(f"Compliance move of deleted database {database_id} failed: {e}")
        _record("complianceDatabaseRetire", outcome="failed", reason="internal",
                database_id=database_id)
        return False
    # One record per run of the move, which continues across runs from its saved page.
    _record("complianceDatabaseRetire", database_id=database_id,
            details={"movedTo": databaseRetirement.deleted_id(database_id), "done": bool(done)})
    if not done:
        if not job_queue_url:
            logger.error("No compliance job queue to continue the compliance move on")
            return False
        try:
            sqs_client.send_message(QueueUrl=job_queue_url, MessageBody=json.dumps(
                databaseRetirement.move_message(database_id)))
        except Exception as e:
            logger.exception(f"Could not queue the continuation of the compliance move of "
                             f"{database_id}; its message is delivered again: {e}")
            return False
        logger.info(f"Compliance move of deleted database {database_id} continues in a new message")
    return True


def _stop_move(tables, database_id, record) -> bool:
    """A move past its run limit: its message is put in the job queue's dead-letter queue, where
    a move that keeps failing ends, and the record notes why with its run count started again, so
    sending the message again (a redrive) resumes it from its last saved page. When the message
    cannot be put there, the run is reported failed (its message is delivered again, and stops
    again) and the count is kept."""
    logger.error(f"Compliance move of deleted database {database_id} stopped: run "
                 f"{record.get('runs')} is past its limit of {record.get('runLimit')}")
    kept = False
    if job_dlq_url:
        try:
            sqs_client.send_message(QueueUrl=job_dlq_url, MessageBody=json.dumps(
                databaseRetirement.move_message(database_id)))
            kept = True
        except Exception as e:
            logger.exception(f"Could not keep the stopped compliance move of {database_id} in the "
                             f"dead-letter queue: {e}")
    try:
        databaseRetirement.stop_at_run_limit(tables, database_id, record.get("generation"),
                                             restart_count=kept)
    except Exception as e:
        logger.exception(f"Could not record the stop of the compliance move of {database_id}: {e}")
    return kept
