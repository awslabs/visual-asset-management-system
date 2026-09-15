#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pipeline sub-process registration lambda.

EventBridge-triggered (not API). A pipeline step may optionally report the lower-level
resources it created -- its Step Functions sub-execution and/or CloudWatch log locations --
by putting an event on the orchestration bus:

    PutEvents(
        EventBusName = <orchestration bus>,
        Source       = "<eventSourcePrefix>.execution.<executionId>.pipeline.<pipelineExecutionId>",
        DetailType   = "pipeline.execution.register",
        Detail       = {
            "pipelineExecutionId": "...",                                  # required
            "subExecution": { "resourceType": "stepFunctionsExecution",    # optional; defaults to
                              "stateMachineArn": "...", "executionArn": "...",  #   stepFunctionsExecution
                              "stageName": "...", "label": "..." },        # optional descriptive fields
            "logs": [ { "logGroupArn": "...", "logGroupName": "...",
                        "logStreamName": "...", "logStreamPrefix": "...",
                        "stageName": "...", "label": "...",                # optional: owning state name,
                        "sourceType": "stateMachine|lambda|batch|ecs|container|custom" } ]  # display name, kind
        })

The event Source must end in ".pipeline.<pipelineExecutionId>" for the id the Detail names; an
event whose Source names a different pipeline execution is ignored. Log locations are unique per
row by (log group, stream, prefix): a redelivered or repeated registration of a location the row
already holds only fills in descriptive fields the stored entry lacks.

A reported subExecution may be any sub-process resource type (Step Functions execution today;
AWS Batch job, ECS/Fargate task, etc. later) — it is typed by ``resourceType`` and carries
whichever locator keys apply (executionArn/stateMachineArn, jobArn, taskArn, ...). All reported
types are stored now; the abort path acts only on Step Functions executions today and surfaces a
non-fatal warning for types it cannot yet stop.

A single standing EventBridge rule (source prefix = deployment eventSourcePrefix, detail-type
= "pipeline.execution.register") routes every such event to this lambda. The lambda appends
the reported resources onto the targeted PipelineExecutions row's typed lists
(registeredSubExecutions / registeredLogs), so abort and full-mode log retrieval can later
act on them. Registration is optional and additive: it does not replace the task-token
success/failure callback a pipeline already uses.
"""

import json
import boto3
import botocore
from botocore.config import Config
from boto3.dynamodb.conditions import Key
from customLogging.logger import safeLogger
from common.resourceNames import get_table_name, ResourceKeys
from common.validators import validate

logger = safeLogger(service="RegisterPipelineExecution")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)

try:
    pipeline_executions_table = get_table_name(ResourceKeys.PIPELINE_EXECUTIONS_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

REGISTER_DETAIL_TYPE = "pipeline.execution.register"

# Statuses that mean the pipeline execution has finished. Registration is asynchronous — the pipeline
# emits the event after it has the resource's id — so an event can arrive after the row is terminal.
ABORTED_STATUS = "ABORTED"
TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", ABORTED_STATUS, "TIMED_OUT")

# Upper bounds on the two lists one row stores. A pipeline looping on registration could otherwise
# push the row toward the DynamoDB item limit; entries past the cap are skipped and named in a
# warning. The execution view lists every stored log entry, so the cap is also the listing bound.
MAX_REGISTERED_LOGS_STORED = 50
MAX_REGISTERED_SUB_EXECUTIONS_STORED = 50


# Sub-process resource types a pipeline may register. Step Functions executions are the only
# type the abort path can stop today; the others are accepted and stored now (so the data model
# is complete) and become abortable in a later stage.
RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION = "stepFunctionsExecution"

# Locator keys carried through verbatim for any sub-process resource, so a new resource type
# (Batch job, ECS task, ...) can be registered without changing this lambda. Each maps to the
# validator its value must satisfy; whichever are present on the reported entry are validated and
# preserved, absent (or invalid) ones are omitted.
_SUB_EXECUTION_LOCATOR_VALIDATORS = {
    "stateMachineArn": "ARN",   # Step Functions state machine (definition)
    "executionArn": "ARN",      # Step Functions execution (running instance) — abortable today
    "jobArn": "ARN",            # AWS Batch job
    "jobId": "ID",              # AWS Batch / Deadline Cloud job id (not an ARN)
    "taskArn": "ARN",           # ECS/Fargate task
    "clusterArn": "ARN",        # ECS cluster (needed to stop a task)
    "farmId": "ID",             # Deadline Cloud farm (with queueId+jobId locates a job)
    "queueId": "ID",            # Deadline Cloud queue
    "arn": "ARN",               # generic fallback ARN for any other resource type
}

# resourceType is a short identifier (camelCase resource-type name); bound it like an ID.
_RESOURCE_TYPE_VALIDATOR = "ID"

# Descriptive fields a log location (and a sub-process) may carry: the sub-state-machine state that
# owns it, a display name, and the kind of resource it belongs to. Stored alongside the locator so the
# execution view can list log sources by stage; a stored entry lacking one may be filled in by a later
# registration of the same location.
LOG_METADATA_FIELDS = ("stageName", "label", "sourceType")
_DESCRIPTIVE_FIELD_VALIDATORS = {"stageName": "SFN_STATE_NAME", "label": "DISPLAY_LABEL"}
LOG_SOURCE_TYPE_FALLBACK = "custom"


def _field_valid(field_name, value, validator):
    """Return True if value passes the named validator. Best-effort: never raises (a validator
    error is treated as invalid so the field is dropped rather than crashing registration)."""
    try:
        ok, _msg = validate({field_name: {"value": value, "validator": validator}})
        return bool(ok)
    except Exception as e:  # nosec B110 - defensive; an invalid field is simply dropped
        logger.warning(f"Validation error for {field_name} (dropping field): {e}")
        return False


def _normalize_sub_execution(sub):
    """Normalize + validate a reported sub-process resource to a typed entry. Always carries a
    ``resourceType`` (defaulting to a Step Functions execution for back-compat with pipelines
    that report a bare {stateMachineArn, executionArn}) plus whichever locator keys were reported
    AND pass field validation. Invalid resourceType or locator values are dropped (logged), not
    stored. Returns None if no valid locator/identifier remains."""
    if not isinstance(sub, dict):
        return None
    resource_type = sub.get("resourceType", "") or RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION
    if not _field_valid("resourceType", resource_type, _RESOURCE_TYPE_VALIDATOR):
        logger.warning(f"Dropping sub-process with invalid resourceType: {resource_type!r}")
        return None
    entry = {"resourceType": resource_type}
    for key, validator in _SUB_EXECUTION_LOCATOR_VALIDATORS.items():
        val = sub.get(key, "") or ""
        if not val:
            continue
        if _field_valid(key, val, validator):
            entry[key] = val
        else:
            logger.warning(f"Dropping invalid {key} on registered sub-process ({resource_type})")
    # Drop entries that carried only a resourceType with no valid resource locator at all.
    if len(entry) == 1:
        return None
    # Descriptive fields ride along after the locator check so they can never stand in for one.
    for key, validator in _DESCRIPTIVE_FIELD_VALIDATORS.items():
        val = sub.get(key, "") or ""
        if not val:
            continue
        if _field_valid(key, val, validator):
            entry[key] = val
        else:
            logger.warning(f"Dropping invalid {key} on registered sub-process ({resource_type})")
    return entry


def _normalize_log(log):
    """Normalize + validate a reported log location to {logGroupArn, logGroupName, logStreamName,
    logStreamPrefix, stageName, label, sourceType}. logStreamPrefix scopes full-mode log retrieval for
    sources that emit to a known stream prefix (e.g. AWS Batch/ECS); stageName/label/sourceType
    describe the location for the execution view. Each field is format-validated; an invalid field is
    dropped (logged), except sourceType, where an unrecognised value is stored as 'custom'. Every key
    is always present ("" when not reported). Returns None if no valid log group identifier (ARN or
    name) remains."""
    if not isinstance(log, dict):
        return None

    def _checked(field, validator):
        val = (log.get(field, "") or "")
        if not val:
            return ""
        if _field_valid(field, val, validator):
            return val
        logger.warning(f"Dropping invalid {field} on registered log location")
        return ""

    source_type = (log.get("sourceType", "") or "")
    if source_type and not _field_valid("sourceType", source_type, "LOG_SOURCE_TYPE"):
        logger.warning(f"Unrecognised sourceType {source_type!r} on registered log location; "
                       f"stored as {LOG_SOURCE_TYPE_FALLBACK}")
        source_type = LOG_SOURCE_TYPE_FALLBACK

    entry = {
        "logGroupArn": _checked("logGroupArn", "CLOUDWATCH_LOG_GROUP_ARN"),
        "logGroupName": _checked("logGroupName", "CLOUDWATCH_LOG_GROUP_NAME"),
        "logStreamName": _checked("logStreamName", "LOG_STREAM_NAME"),
        "logStreamPrefix": _checked("logStreamPrefix", "LOG_STREAM_NAME"),
        "stageName": _checked("stageName", "SFN_STATE_NAME"),
        "label": _checked("label", "DISPLAY_LABEL"),
        "sourceType": source_type,
    }
    if not entry["logGroupArn"] and not entry["logGroupName"]:
        return None
    return entry


def _not_already_registered(new_entries, existing_entries):
    """Drop entries the row already carries. EventBridge delivery is at-least-once, so a
    redelivered registration event reports locators that are already stored; appending them again
    would duplicate CloudWatch reads and abort calls for the same resource."""
    existing = [e for e in (existing_entries or []) if isinstance(e, dict)]
    return [e for e in new_entries if e not in existing]


def _location_key(log):
    """Where a registered log entry points: (log group, exact stream, stream prefix). The group is
    the ARN with any trailing ':*' removed, else the name, so the two spellings of one group agree."""
    arn = (log.get("logGroupArn") or "")
    if arn.endswith(":*"):
        arn = arn[:-2]
    return (arn or (log.get("logGroupName") or ""),
            log.get("logStreamName") or "",
            log.get("logStreamPrefix") or "")


def _plan_log_writes(new_logs, existing_logs):
    """Split incoming log entries against the row's list by location.

    Returns (append, merges): `append` holds the entries whose location the row does not carry yet
    (duplicates within the event collapsed, descriptive fields filled from later copies); `merges`
    holds (index, patch) pairs for locations already stored, where `patch` is the descriptive fields
    the incoming entry names and the stored entry leaves empty. A stored value is never replaced."""
    existing = [e for e in (existing_logs or []) if isinstance(e, dict)]
    index_by_location = {}
    for i, entry in enumerate(existing):
        index_by_location.setdefault(_location_key(entry), i)
    append, seen, merges = [], {}, {}
    for log in new_logs:
        key = _location_key(log)
        stored_index = index_by_location.get(key)
        if stored_index is not None:
            stored = existing[stored_index]
            patch = merges.setdefault(stored_index, {})
            for field in LOG_METADATA_FIELDS:
                if log.get(field) and not stored.get(field) and field not in patch:
                    patch[field] = log[field]
            continue
        if key in seen:
            kept = append[seen[key]]
            for field in LOG_METADATA_FIELDS:
                if not kept.get(field) and log.get(field):
                    kept[field] = log[field]
            continue
        seen[key] = len(append)
        append.append(dict(log))
    return append, [(i, merges[i]) for i in sorted(merges) if merges[i]]


def _load_row(table, pipeline_execution_id, consistent=False):
    """The PipelineExecutions row for the id (looked up by PK so the SK is learned), or None. The read
    after a lost race is strongly consistent, so the retry plans against what the other writer stored
    rather than a replica that has not caught up."""
    kwargs = {"KeyConditionExpression": Key('pipelineExecutionId').eq(pipeline_execution_id)}
    if consistent:
        kwargs["ConsistentRead"] = True
    resp = table.query(**kwargs)
    rows = resp.get('Items', [])
    return rows[0] if rows else None


def _capped(entries, existing_len, cap, what, pipeline_execution_id):
    """The prefix of `entries` that fits under `cap` given `existing_len` already stored; warns
    about the rest."""
    room = max(0, cap - existing_len)
    if len(entries) > room:
        logger.warning(f"Pipeline execution {pipeline_execution_id} stores {existing_len} {what}; "
                       f"{len(entries) - room} of {len(entries)} newly reported skipped at the cap of {cap}")
        return entries[:room]
    return entries


def _append_registrations(table, key, new_subs, new_logs, existing_subs_len=None, existing_logs_len=None):
    """One atomic list_append of both lists. Given the lengths the caller read, the append is conditioned
    on them: a second writer that appended in between fails the condition instead of duplicating a
    location. Without them it is unconditional — DynamoDB serialises concurrent list_appends, so no
    entry is lost, at the cost of possibly storing a location twice."""
    values = {":s": new_subs, ":l": new_logs, ":empty": []}
    kwargs = {
        "Key": key,
        "UpdateExpression": (
            "SET registeredSubExecutions = list_append(if_not_exists(registeredSubExecutions, :empty), :s), "
            "registeredLogs = list_append(if_not_exists(registeredLogs, :empty), :l)"),
        "ExpressionAttributeValues": values,
    }
    if existing_subs_len is not None:
        kwargs["ConditionExpression"] = (
            "(attribute_not_exists(registeredSubExecutions) OR size(registeredSubExecutions) = :ns) "
            "AND (attribute_not_exists(registeredLogs) OR size(registeredLogs) = :nl)")
        values[":ns"] = existing_subs_len
        values[":nl"] = existing_logs_len
    table.update_item(**kwargs)


def _merge_log_metadata(table, key, index, stored_entry, patch):
    """Fill descriptive fields on the stored element at `index`, guarded by that element's location so
    a list that changed underneath never has another element rewritten. Nested SET paths on an
    existing element replace attributes in place; assigning a whole new element to the indexed path is
    never done because that form appends when the index does not exist."""
    set_parts = ", ".join(f"registeredLogs[{index}].{field} = :m_{field}" for field in sorted(patch))
    values = {
        ":loc_arn": stored_entry.get("logGroupArn", "") or "",
        ":loc_stream": stored_entry.get("logStreamName", "") or "",
        ":loc_prefix": stored_entry.get("logStreamPrefix", "") or "",
    }
    values.update({f":m_{field}": value for field, value in patch.items()})
    table.update_item(
        Key=key,
        UpdateExpression="SET " + set_parts,
        ConditionExpression=(
            f"registeredLogs[{index}].logGroupArn = :loc_arn AND "
            f"registeredLogs[{index}].logStreamName = :loc_stream AND "
            f"registeredLogs[{index}].logStreamPrefix = :loc_prefix"),
        ExpressionAttributeValues=values,
    )


def _is_conditional_failure(error):
    return error.response.get("Error", {}).get("Code", "") == "ConditionalCheckFailedException"


def register(detail, source=""):
    """Append the reported sub-execution / log locations onto the PipelineExecutions row for
    detail.pipelineExecutionId. `source` is the EventBridge event Source; it must end in
    ".pipeline.<pipelineExecutionId>" for that same id, so a pipeline can only ever register onto its
    own pipeline execution. No-op when the Source does not match, the row is unknown or nothing valid
    was reported."""
    pipeline_execution_id = (detail or {}).get("pipelineExecutionId", "")
    if not pipeline_execution_id:
        logger.warning("Registration event missing pipelineExecutionId; ignoring")
        return
    # pipelineExecutionId is used as a DynamoDB key; reject a malformed value rather than query
    # with it (ASSET_ID covers the GUID/filename-safe id format).
    if not _field_valid("pipelineExecutionId", pipeline_execution_id, "ASSET_ID"):
        logger.warning("Registration event has invalid pipelineExecutionId; ignoring")
        return
    if not (source or "").endswith(f".pipeline.{pipeline_execution_id}"):
        logger.warning(f"Registration event Source {source!r} does not name pipeline execution "
                       f"{pipeline_execution_id}; ignoring")
        return

    new_subs = []
    sub = _normalize_sub_execution(detail.get("subExecution"))
    if sub:
        new_subs.append(sub)
    new_logs = [n for n in (_normalize_log(log) for log in (detail.get("logs") or [])) if n]

    if not new_subs and not new_logs:
        logger.info(f"Registration event for {pipeline_execution_id} carried no ARNs; nothing to record")
        return

    table = dynamodb.Table(pipeline_executions_table)
    # The append is conditioned on the list lengths read below; a concurrent writer fails it, so the
    # row is re-read (consistently) and the plan recomputed once. A second lost race appends
    # unconditionally rather than dropping the registration.
    for attempt in range(2):
        row = _load_row(table, pipeline_execution_id, consistent=attempt > 0)
        if row is None:
            logger.warning(f"No PipelineExecutions row for {pipeline_execution_id}; ignoring registration")
            return
        existing_subs = [e for e in (row.get("registeredSubExecutions") or []) if isinstance(e, dict)]
        existing_logs = [e for e in (row.get("registeredLogs") or []) if isinstance(e, dict)]

        # Skip what the row already carries so an at-least-once redelivery does not store the same
        # sub-process twice; a log location the row holds is filled in, never duplicated.
        append_subs = _not_already_registered(new_subs, existing_subs)
        append_logs, merges = _plan_log_writes(new_logs, existing_logs)
        append_subs = _capped(append_subs, len(existing_subs), MAX_REGISTERED_SUB_EXECUTIONS_STORED,
                              "sub-executions", pipeline_execution_id)
        append_logs = _capped(append_logs, len(existing_logs), MAX_REGISTERED_LOGS_STORED,
                              "log locations", pipeline_execution_id)
        if not append_subs and not append_logs and not merges:
            logger.info(f"Registration event for {pipeline_execution_id} reported only already-registered "
                        f"locators; nothing to record")
            return

        # A sub-process reported onto a row that is already ABORTED is a resource the abort could not
        # have seen: it stops what the row carried when it read it. The entry is still recorded,
        # because being on the row is what lets a repeated abort of the workflow execution stop it —
        # but nothing else will act on it unaided, so it is named at warning level rather than logged
        # as routine.
        row_status = row.get("executionStatus", "")
        if attempt == 0 and append_subs and row_status == ABORTED_STATUS:
            logger.warning(
                f"Sub-process registration for pipeline execution {pipeline_execution_id} arrived after "
                f"the row was stamped {ABORTED_STATUS}, so the abort did not stop it. Recorded so a "
                f"repeated abort of workflow execution {row.get('workflowExecutionId', '')} stops it. "
                f"Resource types: {[s.get('resourceType', '') for s in append_subs]}")
        elif attempt == 0 and append_subs and row_status in TERMINAL_STATUSES:
            logger.info(f"Sub-process registration for pipeline execution {pipeline_execution_id} arrived "
                        f"after the row reached {row_status}; recorded for log retrieval")

        key = {"pipelineExecutionId": pipeline_execution_id,
               "workflowExecutionId": row.get("workflowExecutionId", "")}
        if append_subs or append_logs:
            try:
                _append_registrations(table, key, append_subs, append_logs,
                                      len(existing_subs), len(existing_logs))
            except botocore.exceptions.ClientError as e:
                if not _is_conditional_failure(e):
                    raise
                if attempt == 0:
                    logger.info(f"Registered lists for {pipeline_execution_id} changed during registration; "
                                f"re-reading once")
                    continue
                # The abort path can only stop what is on the row, so a registration is never dropped
                # for contention: the entries the re-read did not already hold go on unconditionally.
                # The worst case is a location stored twice, which the read side collapses by location
                # and the abort path tolerates (stopping a stopped resource is benign).
                logger.warning(f"Registration append for pipeline execution {pipeline_execution_id} lost "
                               f"two consecutive races; appending {len(append_subs)} sub-execution(s) "
                               f"and {len(append_logs)} log(s) unconditionally")
                _append_registrations(table, key, append_subs, append_logs)
        for index, patch in merges:
            try:
                _merge_log_metadata(table, key, index, existing_logs[index], patch)
            except botocore.exceptions.ClientError as e:
                if not _is_conditional_failure(e):
                    raise
                logger.warning(f"Stored log entry {index} of pipeline execution {pipeline_execution_id} "
                               f"moved before its descriptive fields could be filled; skipped")
        logger.info(f"Registered {len(append_subs)} sub-execution(s) + {len(append_logs)} log(s), "
                    f"filled {len(merges)} stored log(s), for pipeline execution {pipeline_execution_id}")
        return


def lambda_handler(event, context):
    """EventBridge-invoked. The event is an EventBridge envelope; the registration payload is
    in event['detail']. Best-effort: logs and returns on any error (a registration failure must
    not disrupt the pipeline, which reports success/failure via its task token)."""
    logger.info(event)
    try:
        detail = event.get("detail", {})
        if isinstance(detail, str):
            detail = json.loads(detail)
        register(detail or {}, source=event.get("source", "") or "")
    except Exception as e:
        logger.exception(f"Error registering pipeline sub-process (non-critical): {e}")
    return {"handled": True}
