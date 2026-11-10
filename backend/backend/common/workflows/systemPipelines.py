# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Core seam for system pipelines: runs VAMS itself launches on behalf of a feature.

A system pipeline run differs from an ordinary execution in two ways, and both are decided here so
the workflow core never imports, names or calls the feature that uses them:

1. **Trigger.** It is recorded under a system trigger value the launching feature chooses. Core does
   not list the values: it accepts any value matching the documented format (is_system_trigger:
   SYSTEM_TRIGGER_PREFIX followed by a safe identifier, at most MAX_SYSTEM_TRIGGER_LENGTH characters)
   and stores it exactly as given. The value is never accepted from the public execute request:
   models.executions.EXECUTE_TRIGGER_TYPES does not list it, and the only way to set it is a
   ``lambdaCrossCall`` envelope carrying the SYSTEM_USER identity AND SYSTEM_PIPELINE_SOURCE_MARKER,
   plus the top-level SYSTEM_EXECUTION_EVENT_KEY object. An API Gateway request cannot carry either
   top-level key: the proxy integration builds the event itself and confines caller data to the body,
   headers, path and query parameters (the same trust rule as
   common.workflows.systemRecords.is_schema_import_call).
2. **Context.** The cross-call may carry an opaque JSON object, the system execution context, bounded
   to MAX_SYSTEM_EXECUTION_CONTEXT_BYTES. It is written to the first step's run manifest and to the
   main execution row only. It is never part of systemConfig / effectiveSystemConfig, the execution
   details or list projections, the Step Functions input (which the workflow lambdas log), or a re-run.

There is no terminal callback. A feature that launches a system run stores the returned
workflowExecutionId on its own record and reads the run's status from the ordinary execution tracking
(the execution rows, the read-time reconcile, and the Step Functions status change rule that
reconciles any run stopped outside VAMS) when its own record is read. It cleans up its own leftover
artefacts with S3 lifecycle rules. Core workflow code never names a specific pipeline or feature.

Trust boundary: the launch path is gated by who can invoke the execute function (executeWorkflowV2)
directly, not by the API. Any principal holding ``lambda:InvokeFunction`` on it can send the marked
cross-call and so launch a system run under any well-formed system trigger, with any context. API
Gateway invokes it too, but cannot place the top-level keys. Today only the execution service
(re-run) and the workflow trigger dispatcher hold an invoke grant, and neither sends the marker. A new
invoke grant on the execute function is a grant to launch system runs and must be reviewed as one.

This module has NO import-time AWS or environment dependency, so it imports and unit-tests in isolation
like the other ``common.workflows`` record helpers.
"""

import json
import re
from typing import Optional, Tuple

from customLogging.logger import safeLogger

logger = safeLogger()

# The marker a system-pipeline launcher adds to its cross-call envelope:
# {"lambdaCrossCall": {"userName": "SYSTEM_USER", "source": SYSTEM_PIPELINE_SOURCE_MARKER}}.
SYSTEM_PIPELINE_SOURCE_MARKER = "vamsSystemPipeline"

# The identity a system-pipeline launch runs as. The marker is trusted only together with it.
SYSTEM_PIPELINE_USER_NAME = "SYSTEM_USER"

# Top-level key of the launch event carrying {"triggerType": <system trigger value>, "context": {...}}.
SYSTEM_EXECUTION_EVENT_KEY = "systemExecution"

# System trigger value contract. A launching feature picks its own value; core validates only this
# format and stores the value as given (the request and stored spellings are the same). The fixed
# prefix keeps every system value apart from the public stored values (Manual, File-Upload), so a
# list filter or a re-run check needs no registry of features: e.g. "System-MyFeature".
SYSTEM_TRIGGER_PREFIX = "System-"
MAX_SYSTEM_TRIGGER_LENGTH = 64
# The prefix, then one or more ASCII alphanumeric words joined by single hyphens.
_SYSTEM_TRIGGER_RE = re.compile(r"System-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")

# Bound on the serialized system execution context. It is stored on the main execution row, which also
# holds the run's log and error text under the 400 KB item limit, so it stays small: a feature with more
# to hand its pipeline writes an object of its own and passes the key.
MAX_SYSTEM_EXECUTION_CONTEXT_BYTES = 4096

# Main-row attribute holding the serialized context (system runs only).
SYSTEM_EXECUTION_CONTEXT_ATTRIBUTE = "systemExecutionContext"
# Manifest key holding the parsed context (system runs only).
SYSTEM_EXECUTION_CONTEXT_MANIFEST_KEY = "systemExecutionContext"

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


def is_system_trigger(trigger_type) -> bool:
    """Whether a triggerType value is a well-formed system trigger value: SYSTEM_TRIGGER_PREFIX, then
    ASCII alphanumeric words joined by single hyphens, at most MAX_SYSTEM_TRIGGER_LENGTH characters.
    Decided by format alone; core keeps no list of system trigger values."""
    return (isinstance(trigger_type, str)
            and len(trigger_type) <= MAX_SYSTEM_TRIGGER_LENGTH
            and _SYSTEM_TRIGGER_RE.fullmatch(trigger_type) is not None)


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
    """The (system trigger value, serialized context) a launch event asks for, or None for an ordinary
    launch.

    Only a marked system-pipeline cross-call (is_system_pipeline_call) is read. Any other event that
    carries the system execution key has it ignored, so no other caller can reach the system trigger.
    A marked call must name a well-formed system trigger value (is_system_trigger), which is returned
    unchanged, and, when it carries a context, a JSON object within MAX_SYSTEM_EXECUTION_CONTEXT_BYTES;
    otherwise SystemExecutionError is raised. An absent context is stored as the empty object."""
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
    trigger_type = spec.get("triggerType")
    if not is_system_trigger(trigger_type):
        raise SystemExecutionError(
            f"system execution trigger must match {SYSTEM_TRIGGER_PREFIX}<identifier> "
            f"(at most {MAX_SYSTEM_TRIGGER_LENGTH} characters)")
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
    return trigger_type, text
