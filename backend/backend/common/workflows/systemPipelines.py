# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Core seam for system pipelines: runs VAMS itself launches on behalf of a feature.

A system pipeline run differs from an ordinary execution in three ways, and all three are decided here
so the workflow core never imports the feature that uses them:

1. **Trigger.** It is recorded under a system trigger value (SYSTEM_TRIGGER_TYPE_TO_STORED). The value
   is never accepted from the public execute request: models.executions.EXECUTE_TRIGGER_TYPES does not
   list it, and the only way to set it is a ``lambdaCrossCall`` envelope carrying the SYSTEM_USER
   identity AND SYSTEM_PIPELINE_SOURCE_MARKER, plus the top-level SYSTEM_EXECUTION_EVENT_KEY object.
   An API Gateway request cannot carry either top-level key: the proxy integration builds the event
   itself and confines caller data to the body, headers, path and query parameters (the same trust
   rule as common.workflows.systemRecords.is_schema_import_call).
2. **Context.** The cross-call may carry an opaque JSON object, the system execution context, bounded
   to MAX_SYSTEM_EXECUTION_CONTEXT_BYTES. It is written to the first step's run manifest and to the
   main execution row only. It is never part of systemConfig / effectiveSystemConfig, the execution
   details or list projections, the Step Functions input (which the workflow lambdas log), or a re-run.
3. **Terminal callback.** When an execution reaches a terminal status, the writer that WON the shared
   terminal guard (executionOutputs.not_terminal_condition) calls notify_terminal_transition once. For a
   system trigger with a registered target, the target Lambda is invoked asynchronously with the
   terminal status. The registry is the CALLBACK_FUNCTIONS_BY_SYSTEM_TRIGGER environment variable
   ({"<stored trigger value>": "<function name>"}), set by the CDK builders only for registered
   callbacks. Non-system runs return before any AWS client is created.

   A run that ends outside VAMS is reconciled as soon as Step Functions reports it: an EventBridge
   rule on the execution status change events of the deployment's workflow state machines invokes the
   execution service, which runs the details read path's reconcile (WRITER_STATUS_EVENT) under the
   same guard, so the push and a read never both send.
   An abort sends its ABORTED callback only once the Step Functions execution is confirmed stopped;
   an abort that could not confirm it holds the callback on the main row (CALLBACK_PENDING_ATTRIBUTE)
   for a repeated abort that confirms the stop, the status change event that reports the execution
   ended, or a details read whose DescribeExecution shows it ended, whichever comes first. A trigger
   listed in STOP_INDEPENDENT_CALLBACK_TRIGGERS also gets the ABORTED callback at once from that abort
   (notify_unconfirmed_abort), and the held one on confirmation.

The callback is best-effort: it never raises, so a failing or missing target can never prevent or
change the run's own terminal record. The target must be idempotent (the payload carries a stable
idempotencyKey), because Lambda's asynchronous invocation retries a failed delivery.

Trust boundary: the launch path is gated by who can invoke the execute function (executeWorkflowV2)
directly, not by the API. Any principal holding ``lambda:InvokeFunction`` on it can send the marked
cross-call and so launch a system run under any system trigger, with any context, that the registered
callback then acts on. API Gateway invokes it too, but cannot place the top-level keys. Today only the
execution service (re-run) and the workflow trigger dispatcher hold an invoke grant, and neither sends
the marker. A new invoke grant on the execute function is a grant to launch system runs and must be
reviewed as one.

This module has NO import-time AWS or environment dependency, so it imports and unit-tests in isolation
like the other ``common.workflows`` record helpers. boto3 is imported only when a callback is sent.
"""

import json
import os
from typing import Optional, Tuple

from customLogging.logger import safeLogger

logger = safeLogger()

# The marker a system-pipeline launcher adds to its cross-call envelope:
# {"lambdaCrossCall": {"userName": "SYSTEM_USER", "source": SYSTEM_PIPELINE_SOURCE_MARKER}}.
SYSTEM_PIPELINE_SOURCE_MARKER = "vamsSystemPipeline"

# The identity a system-pipeline launch runs as. The marker is trusted only together with it.
SYSTEM_PIPELINE_USER_NAME = "SYSTEM_USER"

# Top-level key of the launch event carrying {"triggerType": <request value>, "context": {...}}.
SYSTEM_EXECUTION_EVENT_KEY = "systemExecution"

# System trigger values: the request form a marked cross-call names, mapped to the stored value the
# execution records carry (the same lowercase-request / stored-display split as the public
# TRIGGER_TYPE_TO_STORED in models.executions). Each value reserves one registry key.
SYSTEM_TRIGGER_TYPE_TO_STORED = {"zipDownload": "Zip-Download"}
SYSTEM_TRIGGER_TYPES = tuple(SYSTEM_TRIGGER_TYPE_TO_STORED.values())

# Bound on the serialized system execution context. It is stored on the main execution row, which also
# holds the run's log and error text under the 400 KB item limit, so it stays small: a feature with more
# to hand its pipeline writes an object of its own and passes the key.
MAX_SYSTEM_EXECUTION_CONTEXT_BYTES = 4096

# Main-row attribute holding the serialized context (system runs only).
SYSTEM_EXECUTION_CONTEXT_ATTRIBUTE = "systemExecutionContext"
# Manifest key holding the parsed context (system runs only).
SYSTEM_EXECUTION_CONTEXT_MANIFEST_KEY = "systemExecutionContext"

# Main-row attribute an abort sets (system runs only) when it wrote ABORTED but could not confirm the
# Step Functions execution stopped. Its value is the terminal status whose callback is held back. The
# path that later confirms the stop (a repeated abort, the status change event, or a details read)
# removes it with a conditional write and, having won that write, sends the held callback, so the
# callback is still sent exactly once.
CALLBACK_PENDING_ATTRIBUTE = "systemCallbackPending"

# Environment variable holding the callback registry (JSON object, stored trigger -> function name).
CALLBACK_REGISTRY_ENV = "CALLBACK_FUNCTIONS_BY_SYSTEM_TRIGGER"

# Payload contract of the terminal callback.
CALLBACK_TYPE = "systemPipelineTerminal"
CALLBACK_SCHEMA_VERSION = 1
# Bound on the executionError text the payload carries.
MAX_CALLBACK_ERROR_CHARS = 2048

# The terminal writers, named in the callback payload and its log lines.
WRITER_END_STATE = "endState"
WRITER_ERROR_HANDLER = "errorHandler"
WRITER_ABORT = "abort"
WRITER_RECONCILE_LIST = "reconcileList"
WRITER_RECONCILE_DETAILS = "reconcileDetails"
# The push reconcile: the execution service handling a Step Functions execution status change event
# for a run that reached a terminal state outside VAMS (stopped from the console or API, timed out or
# failed at the state machine level), so its callback does not wait for someone to open the run.
WRITER_STATUS_EVENT = "statusEvent"
# An abort that could not confirm the stop, handing the ABORTED status straight to a callback that is
# safe to run while the state machine may still be running (STOP_INDEPENDENT_CALLBACK_TRIGGERS). The
# held callback (CALLBACK_PENDING_ATTRIBUTE) stays on the row and is still sent on confirmation.
WRITER_ABORT_UNCONFIRMED = "abortUnconfirmed"

# Stored system trigger values whose callback decides only from state the feature itself guards, so it
# gives the same outcome whether or not the run's state machine has stopped. An abort that could not
# confirm the stop sends these the ABORTED callback at once (notify_unconfirmed_abort) instead of
# leaving the feature's own record to wait for the confirmation (the status change event, a repeated
# abort or a details read), which is lost when the event is. The held callback is still sent once the
# stop is confirmed, so a callback registered here must be idempotent across two ABORTED deliveries
# that may arrive in either order. A feature opts in by adding its stored trigger value here, next to
# its SYSTEM_TRIGGER_TYPE_TO_STORED entry; a trigger not listed waits for the confirmed stop.
# Zip-Download (#1125): the callback aborts the job's open uploads before it decides and an archive that
# landed wins (common/zipDownloads.finalize_ended_run); the zipArchive container stops when it finds its
# job finalized. That is the rule the zip routes' read-time reconcile already applies to an ABORTED run.
STOP_INDEPENDENT_CALLBACK_TRIGGERS = frozenset({"Zip-Download"})

_TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT")

# Re-run refusal body (400): a system run is launched by the feature that owns it, with a context a
# re-run cannot reproduce, and replaying it as an ordinary run would execute it outside that feature.
SYSTEM_RERUN_REFUSED_MESSAGE = (
    "This execution was started by VAMS for a system feature and cannot be re-run. Start the "
    "operation again from the feature that created it.")

# Refusal body (400) for a marked cross-call whose system execution object is malformed. Fixed text:
# the launcher is internal code, and the details go to the log.
SYSTEM_EXECUTION_INVALID_MESSAGE = "Invalid system execution request."


class SystemExecutionError(ValueError):
    """A marked system-pipeline cross-call whose system execution object is malformed."""


def is_system_pipeline_call(event) -> bool:
    """True only for a system-pipeline launcher's cross-call: the reserved system identity AND the
    system-pipeline source marker. An API request (which cannot carry a top-level lambdaCrossCall key),
    a trigger dispatch, a re-run and the schema importer are not system-pipeline calls."""
    if not isinstance(event, dict):
        return False
    cross_call = event.get("lambdaCrossCall")
    if not isinstance(cross_call, dict):
        return False
    return (cross_call.get("userName") == SYSTEM_PIPELINE_USER_NAME
            and cross_call.get("source") == SYSTEM_PIPELINE_SOURCE_MARKER)


def serialize_context(context) -> str:
    """The stored form of a system execution context: compact, key-sorted JSON text."""
    return json.dumps(context, separators=(",", ":"), sort_keys=True, default=str)


def resolve_system_execution(event) -> Optional[Tuple[str, str]]:
    """The (stored trigger value, serialized context) a launch event asks for, or None for an ordinary
    launch.

    Only a marked system-pipeline cross-call (is_system_pipeline_call) is read. Any other event that
    carries the system execution key has it ignored, so no other caller can reach the system trigger.
    A marked call must name a known system trigger and, when it carries a context, a JSON object within
    MAX_SYSTEM_EXECUTION_CONTEXT_BYTES; otherwise SystemExecutionError is raised. An absent context is
    stored as the empty object."""
    if not isinstance(event, dict):
        return None
    if not is_system_pipeline_call(event):
        if SYSTEM_EXECUTION_EVENT_KEY in event:
            logger.warning("Ignored a system execution object on a launch that is not a marked "
                           "system-pipeline call")
        return None
    spec = event.get(SYSTEM_EXECUTION_EVENT_KEY)
    if not isinstance(spec, dict):
        raise SystemExecutionError("system execution object is missing or not an object")
    request_trigger = spec.get("triggerType")
    stored = SYSTEM_TRIGGER_TYPE_TO_STORED.get(request_trigger) if isinstance(request_trigger, str) \
        else None
    if not stored:
        raise SystemExecutionError("system execution names an unknown system trigger")
    context = spec.get("context")
    if context is None:
        context = {}
    if not isinstance(context, dict):
        raise SystemExecutionError("system execution context must be a JSON object")
    try:
        text = serialize_context(context)
    except (TypeError, ValueError) as e:
        raise SystemExecutionError(f"system execution context is not serializable: {e}")
    if len(text.encode("utf-8")) > MAX_SYSTEM_EXECUTION_CONTEXT_BYTES:
        raise SystemExecutionError(
            f"system execution context exceeds {MAX_SYSTEM_EXECUTION_CONTEXT_BYTES} bytes")
    return stored, text


def is_system_trigger(trigger_type) -> bool:
    """Whether a stored triggerType is a system trigger value."""
    return isinstance(trigger_type, str) and trigger_type in SYSTEM_TRIGGER_TYPES


def stored_context(main_item) -> dict:
    """The system execution context stored on a main execution row ({} when absent or unreadable)."""
    raw = (main_item or {}).get(SYSTEM_EXECUTION_CONTEXT_ATTRIBUTE) if isinstance(main_item, dict) \
        else None
    if not raw or not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def callback_registry() -> dict:
    """The callback registry from the environment: {stored trigger value: function name}. Entries whose
    key is not a system trigger value, or whose target is not a non-empty string, are dropped; an
    unreadable variable reads as an empty registry."""
    raw = os.environ.get(CALLBACK_REGISTRY_ENV, "") or ""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.error(f"{CALLBACK_REGISTRY_ENV} is not valid JSON; no system pipeline callback is sent")
        return {}
    if not isinstance(parsed, dict):
        logger.error(f"{CALLBACK_REGISTRY_ENV} is not a JSON object; no system pipeline callback is sent")
        return {}
    return {key: value for key, value in parsed.items()
            if is_system_trigger(key) and isinstance(value, str) and value}


def build_terminal_callback_payload(main_item, status, stop_date="", execution_error="", writer=""):
    """The payload a registered callback target receives for one terminal transition."""
    item = main_item or {}
    execution_id = item.get("workflowExecutionId", "") or ""
    error_text = execution_error if isinstance(execution_error, str) else ""
    return {
        "callbackType": CALLBACK_TYPE,
        "schemaVersion": CALLBACK_SCHEMA_VERSION,
        # Stable per transition: a run reaches exactly one terminal status, so a redelivery (Lambda's
        # asynchronous retry, or a writer re-running after it sent) carries the same key.
        "idempotencyKey": f"{execution_id}:{status}",
        "workflowExecutionId": execution_id,
        "workflowDatabaseId": item.get("workflowDatabaseId", "") or "",
        "workflowId": item.get("workflowId", "") or "",
        "triggerType": item.get("triggerType", "") or "",
        "executionStatus": status,
        "executionStopDate": stop_date or item.get("executionStopDate", "") or "",
        "executionError": error_text[:MAX_CALLBACK_ERROR_CHARS],
        "writer": writer or "",
        "systemExecutionContext": stored_context(item),
    }


_lambda_client = None


def _default_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        import boto3
        from botocore.config import Config
        _lambda_client = boto3.client(
            "lambda", config=Config(retries={"max_attempts": 5, "mode": "adaptive"}))
    return _lambda_client


def notify_terminal_transition(main_item, status, stop_date="", execution_error="", writer="",
                               lambda_client=None) -> bool:
    """Send the terminal callback for a system-pipeline run, once per transition. Returns True when a
    callback was handed to Lambda.

    The CALLER guarantees once-per-transition: it calls this only after its own guarded terminal write
    succeeded, so the writer that lost the race to another terminal writer never calls it. `main_item`
    is the execution's main row as read before (or returned by) that write; its triggerType decides
    whether anything is sent, and its stored context travels in the payload.

    Never raises. A non-system run, a system run with no registered target, or a non-terminal status
    returns False without creating any AWS client. A failed invoke is logged and returns False; the
    run's terminal record is already written and is not touched."""
    try:
        if not isinstance(main_item, dict) or status not in _TERMINAL_STATUSES:
            return False
        trigger_type = main_item.get("triggerType")
        if not is_system_trigger(trigger_type):
            return False
        target = callback_registry().get(trigger_type)
        execution_id = main_item.get("workflowExecutionId", "") or ""
        if not target:
            logger.info(f"No terminal callback is registered for system trigger {trigger_type}; "
                        f"execution {execution_id} reached {status}")
            return False
        payload = build_terminal_callback_payload(
            main_item, status, stop_date=stop_date, execution_error=execution_error, writer=writer)
        client = lambda_client or _default_lambda_client()
        # Event (asynchronous): the writer never waits on, or fails with, the target.
        client.invoke(FunctionName=target, InvocationType="Event",
                      Payload=json.dumps(payload, default=str).encode("utf-8"))
        logger.info(f"Sent the {status} terminal callback for system execution {execution_id} "
                    f"(writer {writer or 'unknown'})")
        return True
    except Exception as e:
        logger.warning(f"System pipeline terminal callback failed (non-critical; the terminal record "
                       f"is unchanged): {e}")
        return False


def notify_unconfirmed_abort(main_item, status, stop_date="", execution_error="",
                             lambda_client=None) -> bool:
    """Hand an abort's terminal status to the run's callback at once when the abort could not confirm
    the stop and the trigger's callback does not depend on it (STOP_INDEPENDENT_CALLBACK_TRIGGERS).

    The caller has won its guarded ABORTED write and set CALLBACK_PENDING_ATTRIBUTE; this does not
    touch that attribute, so the held callback still goes out on confirmation and the callback sees
    the status twice (writer WRITER_ABORT_UNCONFIRMED, then WRITER_ABORT, with the same
    idempotencyKey). Any other trigger returns False without creating an AWS client. Never raises
    (notify_terminal_transition)."""
    if not isinstance(main_item, dict) or \
            main_item.get("triggerType") not in STOP_INDEPENDENT_CALLBACK_TRIGGERS:
        return False
    return notify_terminal_transition(main_item, status, stop_date=stop_date,
                                      execution_error=execution_error,
                                      writer=WRITER_ABORT_UNCONFIRMED, lambda_client=lambda_client)
