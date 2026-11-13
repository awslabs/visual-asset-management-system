# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Audit Logging Module for VAMS

This module provides functions to log audit events to CloudWatch Log Groups.
All functions implement silent failure - if logging fails, the error is logged
locally but the lambda execution continues without disruption. An entry that
cannot be delivered to its audit log group is written to the function's own log
as one auditFallback line and counted in the VAMS/Audit AuditWriteFailures metric.

log_action writes action records in the record contract (auditVersion 1): the
action, its outcome, the actor, the request and the entity, with what a write
changed. audit_request opens a request's audit context, which resolves the actor
once and writes the request's successful action records and allowed authorization
records together when it ends.
The action registry is common/auditActions.py.
"""

import contextlib
import contextvars
import functools
import hashlib
import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, wait as wait_for_futures
import boto3
from botocore.config import Config
from common.timestamps import utc_now
from typing import Dict, Any, Callable, Iterator, Optional, List, Set, Tuple
from handlers.auth import request_to_claims, AUTH_METHOD_API_KEY
from common.auth.viewerAddress import viewer_ip_for_audit
from common.validators import normalize_userid
from customLogging.logger import mask_sensitive_data, safeLogger
from common.resourceNames import ResourceKeys, get_log_group_name
from common.auditActions import (
    AuditSpec,
    GROUP_ACTIONS,
    GROUP_AUTH_CHANGES,
    GROUP_AUTH_OTHER,
    GROUP_AUTHORIZATION,
    GROUP_ERRORS,
    GROUP_FILE_DOWNLOAD,
    GROUP_FILE_DOWNLOAD_STREAMED,
    GROUP_FILE_UPLOAD,
    AUDIT_ACTION_NAME_PATTERN,
    KIND_DOWNLOAD,
    KIND_READ,
    KIND_SEARCH,
    audit_spec_for_action,
    audit_spec_for_route,
    resolve_route_template,
)

# Initialize logger for audit logging module
logger = safeLogger()

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

# Initialize CloudWatch Logs client
try:
    cloudwatch_logs = boto3.client('logs', config=retry_config)
except Exception as e:
    logger.exception(f"Failed to initialize CloudWatch Logs client: {e}")
    cloudwatch_logs = None

# The client the request audit context (audit_request) writes with. It runs on the request path,
# so it is bounded rather than Rule 6's five adaptive attempts: one-second connect and two-second
# read timeouts and three attempts, under the flush's own AUDIT_FLUSH_DEADLINE_SECONDS. A record the
# flush cannot confirm in time is reported as undelivered (auditFallback) instead of holding the
# response.
flush_client_config = Config(connect_timeout=1, read_timeout=2, retries={'max_attempts': 3, 'mode': 'standard'})

try:
    audit_flush_client = boto3.client('logs', config=flush_client_config)
except Exception as e:
    logger.exception(f"Failed to initialize the audit flush CloudWatch Logs client: {e}")
    audit_flush_client = None

# PutLogEvents budgets: at most 10,000 events and 1,048,576 bytes per call, and 262,144 bytes
# per event, each byte total counting 26 bytes of overhead on top of a message's UTF-8 bytes.
# A call that breaches any of them is rejected in full.
AUDIT_BATCH_MAX_EVENTS = 10000
AUDIT_BATCH_MAX_BYTES = 1048576
AUDIT_EVENT_MAX_BYTES = 262144
AUDIT_EVENT_OVERHEAD_BYTES = 26

# Message-byte budget for a single entry. The reserved kilobyte keeps a cut entry, and the
# marker appended to it, clear of the per-event boundary.
AUDIT_EVENT_MAX_MESSAGE_BYTES = AUDIT_EVENT_MAX_BYTES - AUDIT_EVENT_OVERHEAD_BYTES - 1024
AUDIT_EVENT_TRUNCATION_MARKER = " ...[truncated]"

# The event echo is the same text on every entry of a batch, so a batch pays its bytes once per
# entry, and a bulk-download echo carries the request's own key list -- which grows with the entry
# count. Past this total for the replicated echo, the first entry of a write carries the echo and
# the rest carry a reference to it, which keeps a write proportional to the number of entries.
AUDIT_BATCH_ECHO_MAX_TOTAL_BYTES = AUDIT_BATCH_MAX_BYTES
AUDIT_EVENT_ECHO_REFERENCE = " --- [event: <shared with the first entry written at this timestamp>]"

# Characters a bracketed prefix value never carries raw: the segment delimiters, the backslash that
# starts an escape, the C0 and C1 controls (CR and LF included), and the bidirectional, zero-width
# and line/paragraph separator characters that change how a record reads without being visible.
_PREFIX_UNSAFE_CHARS = re.compile(
    "[\\[\\]\\\\\x00-\x1f\x7f-\x9f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e"
    "\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb]"
)

# The log stream this container has created in each audit log group. The stream name is the current
# date, so an entry ages out at the day boundary and CreateLogStream -- an account-wide 50 TPS
# quota -- is called once per group per day instead of once per audit write. A failed write reports its
# entries as undelivered, and a stream that went away is created again by the write after it.
_created_log_streams: Dict[str, str] = {}

# The nine audit log groups, resolved once per container at import (Rule 10) rather than on each
# write. A name absent from SSM is recorded as None instead of raising: this module's contract is
# that a failed audit write never disrupts the caller, and a hard failure here would turn one
# unpublished parameter into a cold-start 500 on every request. `_audit_log_group()` retries a name
# that did not resolve, so a transient SSM failure at import does not silence the container's audit
# trail for its whole lifetime.
_AUDIT_LOG_GROUP_KEYS = (
    ResourceKeys.AUDIT_LOG_AUTHENTICATION,
    ResourceKeys.AUDIT_LOG_AUTHORIZATION,
    ResourceKeys.AUDIT_LOG_FILEUPLOAD,
    ResourceKeys.AUDIT_LOG_FILEDOWNLOAD,
    ResourceKeys.AUDIT_LOG_FILEDOWNLOAD_STREAMED,
    ResourceKeys.AUDIT_LOG_AUTHOTHER,
    ResourceKeys.AUDIT_LOG_AUTHCHANGES,
    ResourceKeys.AUDIT_LOG_ACTIONS,
    ResourceKeys.AUDIT_LOG_ERRORS,
)

_audit_log_group_names: Dict[str, Optional[str]] = {}

# The audit log type (the parameter key's last segment, e.g. auditActions) of each resolved log
# group name, so an entry that cannot be delivered is reported under the type it was meant for.
_audit_log_types_by_name: Dict[str, str] = {}

# An entry that cannot be delivered to its audit log group is written to the function's own log
# instead, as one JSON line marked auditFallback, and counted in this CloudWatch embedded metric
# format metric. The metric is extracted from the function log, so it needs no permission beyond
# the function's own log delivery.
AUDIT_METRIC_NAMESPACE = "VAMS/Audit"
AUDIT_METRIC_WRITE_FAILURES = "AuditWriteFailures"
AUDIT_METRIC_DIMENSION = "AuditLogType"


def _audit_log_type(key) -> str:
    """The audit log type a resource key names, e.g. auditActions."""
    return key.param_key.rsplit("/", 1)[-1]


def _resolve_audit_log_group(key) -> Optional[str]:
    """Resolve one audit log-group name, returning None instead of raising."""
    try:
        name = get_log_group_name(key)
    except Exception as e:
        logger.error(f"Audit log group name not resolved for {key.param_key}: {e}")
        return None
    if name:
        _audit_log_types_by_name[name] = _audit_log_type(key)
    return name


for _key in _AUDIT_LOG_GROUP_KEYS:
    _audit_log_group_names[_key.param_key] = _resolve_audit_log_group(_key)


def _audit_log_group(key) -> Optional[str]:
    """Return the container's resolved name for an audit log group.

    A hit is a dict lookup, so an audited request makes no SSM call. A name that did not resolve at
    import is retried here and cached on success; the resolver's own negative record bounds the cost
    of a genuinely unpublished parameter to one sweep per its missing-key window per container.
    """
    name = _audit_log_group_names.get(key.param_key)
    if name:
        return name
    name = _resolve_audit_log_group(key)
    if name:
        _audit_log_group_names[key.param_key] = name
    return name


def _write_function_log_line(line: str) -> None:
    """One line on standard output, which Lambda delivers to the function's own log group."""
    print(line, flush=True)


def _fallback_record(record: Optional[str]) -> Optional[str]:
    """
    Cap an undelivered entry so its fallback line stays inside the per-event budget once the
    entry is written as a JSON string (quotes and backslashes are escaped there).
    """
    if record is None:
        return None
    record = _truncate_message(record)
    budget = AUDIT_EVENT_MAX_MESSAGE_BYTES
    marker = AUDIT_EVENT_TRUNCATION_MARKER
    for _ in range(4):
        size = len(_serialize_json(record))
        if size <= budget:
            return record
        keep = max(0, int(len(record) * budget / size) - len(marker) - 64)
        record = record[:keep] + marker
    # Every character escapes to at most six, so this always fits.
    return record[:budget // 6 - len(marker)] + marker


def report_undelivered_audit_records(log_type: Any, reason: str, records: List[Optional[str]]) -> None:
    """
    Write audit entries that did not reach their audit log group to the function's own log, one
    JSON line per entry marked auditFallback, and count them in the AuditWriteFailures metric
    (one embedded-metric line per call). Never raises.

    Args:
        log_type: The audit log type (e.g. "auditActions"), or the ResourceKeys entry it comes from
        reason: Why the entries were not delivered, e.g. "putLogEventsFailed"
        records: The entries as they would have been written; None for an entry not formatted
    """
    try:
        if not records:
            return
        if not isinstance(log_type, str):
            log_type = _audit_log_type(log_type)
        for record in records:
            _write_function_log_line(_serialize_json({
                "auditFallback": True,
                "logGroupKey": log_type,
                "reason": reason,
                "record": _fallback_record(record),
            }))
        _write_function_log_line(_serialize_json({
            "_aws": {
                "Timestamp": int(utc_now().timestamp() * 1000),
                "CloudWatchMetrics": [{
                    "Namespace": AUDIT_METRIC_NAMESPACE,
                    "Dimensions": [[AUDIT_METRIC_DIMENSION]],
                    "Metrics": [{"Name": AUDIT_METRIC_WRITE_FAILURES, "Unit": "Count"}],
                }],
            },
            AUDIT_METRIC_DIMENSION: log_type,
            AUDIT_METRIC_WRITE_FAILURES: len(records),
            "reason": reason,
        }))
    except Exception as e:
        try:
            logger.error(f"Failed to report {len(records)} undelivered audit entries: {e}")
        except Exception:
            pass


def _event_suffix(event: Optional[Dict[str, Any]]) -> str:
    """The ` --- [event: ...]` echo appended to an entry: the masked event as ASCII JSON."""
    masked_event = mask_sensitive_data(event) if event else {}
    return f" --- [event: {_serialize_json(masked_event)}]" if masked_event else ""


# The requestContext keys the short request echo keeps, where the event has them: the request id, the
# request time (REST requestTimeEpoch, HTTP API timeEpoch), the route key and the REST resource path.
_SHORT_ECHO_CONTEXT_KEYS = ("requestId", "requestTimeEpoch", "timeEpoch", "routeKey", "resourcePath")


def _short_echo_event(event: Any) -> Any:
    """
    The part of a request event the authorization, error, auth change and auth other records echo:
    the request id and time, the method and concrete path, the route (route key, REST resource and
    resource path), the path parameters (masked, each value capped like a record value) and the
    connection addresses (``requestContext.http.sourceIp``, ``requestContext.identity.sourceIp``). No
    headers, query string or body, so a request's content never reaches these records through the
    echo and the echo stays a few hundred bytes. The keys sit at the paths the full echo had them at,
    so a query on ``requestContext.requestId`` or ``requestContext.http.path`` keeps matching. A value
    that is not a dict is returned unchanged.
    """
    if not isinstance(event, dict):
        return event
    request_context = event.get("requestContext") if isinstance(event.get("requestContext"), dict) else {}
    short_context: Dict[str, Any] = {}
    for key in _SHORT_ECHO_CONTEXT_KEYS:
        if request_context.get(key) is not None:
            short_context[key] = request_context[key]
    http = request_context.get("http") if isinstance(request_context.get("http"), dict) else {}
    short_http = {key: http[key] for key in ("method", "path", "sourceIp") if http.get(key) is not None}
    if short_http:
        short_context["http"] = short_http
    identity = request_context.get("identity") if isinstance(request_context.get("identity"), dict) else {}
    if identity.get("sourceIp") is not None:
        short_context["identity"] = {"sourceIp": identity["sourceIp"]}
    short: Dict[str, Any] = {}
    if short_context:
        short["requestContext"] = short_context
    if event.get("resource") is not None:
        short["resource"] = event["resource"]
    path_parameters = event.get("pathParameters")
    if isinstance(path_parameters, dict) and path_parameters:
        masked = mask_sensitive_data(path_parameters)
        if isinstance(masked, dict):
            short["pathParameters"] = {str(key): _cap_value(value)[0] for key, value in masked.items()}
    return short


# The audit log types whose records echo the short request (_short_echo_event) instead of the masked
# event.
_SHORT_ECHO_LOG_TYPES = frozenset({GROUP_AUTHORIZATION, GROUP_ERRORS, GROUP_AUTH_CHANGES, GROUP_AUTH_OTHER})


def _echo_event_for(log_type: str, event: Any) -> Any:
    """The event a record of this log type echoes."""
    return _short_echo_event(event) if log_type in _SHORT_ECHO_LOG_TYPES else event


def _report_unresolved_log_group(key, messages: List[str], event: Optional[Dict[str, Any]]) -> None:
    """Report entries whose audit log group name did not resolve."""
    logger.error(f"{key.env_var_names[0]} resource name not resolved")
    try:
        suffix = _event_suffix(event)
    except Exception:
        suffix = ""
    report_undelivered_audit_records(key, "logGroupUnresolved", [message + suffix for message in messages])


def _write_audit_entry(key, event_type: str, user_context: Dict[str, Any], custom_data: Any,
                       event: Optional[Dict[str, Any]], canonical: Optional[Dict[str, Any]] = None,
                       buffer: bool = False) -> None:
    """
    Format one audit entry and write it to the audit log group `key` names, or report it as
    undelivered when that name does not resolve.

    Inside the request's audit context, a call that passes ``canonical`` (action, outcome and entity
    type) gains the record contract keys in its payload and is handed to the context's delivery: a
    ``buffer`` entry is held until the request ends, any other is written at once. A call without
    ``canonical``, or outside a context, is written as before.
    """
    ctx = _context_for(event) if canonical is not None else None
    if ctx is not None:
        custom_data = _with_canonical_keys(custom_data, _legacy_canonical_keys(
            ctx, canonical.get("action"), canonical.get("outcome", OUTCOME_SUCCESS),
            canonical.get("entityType")))
    log_type = _audit_log_type(key)
    if ctx is not None and log_type != GROUP_ERRORS:
        _note_recorded(ctx, canonical.get("action"))
    message = _format_log_message(event_type, user_context, custom_data, log_type)
    echo_event = _echo_event_for(log_type, event)
    if ctx is not None:
        _enqueue_records(ctx, log_type, _records_with_echo([message], echo_event), buffer=buffer)
        return
    log_group_name = _audit_log_group(key)
    if not log_group_name:
        _report_unresolved_log_group(key, [message], echo_event)
        return
    _write_to_cloudwatch(log_group_name, message, echo_event)


def _extract_user_context(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract user context from the API event.
    
    Args:
        event: The API Gateway event
        
    Returns:
        Dictionary containing user, roles, and mfaEnabled information
    """
    # Inside the request's audit context the claims were resolved once, when it opened.
    ctx = _context_for(event)
    if ctx is not None:
        return dict(ctx.user_context)
    try:
        claims = request_to_claims(event)
        return {
            "user": claims.get("tokens", ["UNKNOWN"])[0] if claims.get("tokens") else "UNKNOWN",
            "roles": claims.get("roles", []),
            "mfaEnabled": claims.get("mfaEnabled", False)
        }
    except Exception as e:
        return {
            "user": "UNKNOWN",
            "roles": [],
            "mfaEnabled": False
        }


def _encode_prefix_value(value: Any) -> str:
    """
    Encode a value for a bracketed prefix segment of an audit record.

    The segment delimiters ([ and ]), the backslash, CR, LF and every other C0/C1 control,
    bidirectional or zero-width character are written as \\uXXXX, so each record stays one line
    whose segments are delimited only by the formatter. A value made of the characters a user id
    may contain (common/validators.py userid_pattern) is returned unchanged.

    Args:
        value: The value to place inside a [...] segment

    Returns:
        The encoded text
    """
    text = value if isinstance(value, str) else str(value)
    return _PREFIX_UNSAFE_CHARS.sub(_escape_char, text)


def _escape_char(match: "re.Match") -> str:
    return "\\u%04x" % ord(match.group(0))


def _encode_json_string(value: Any) -> str:
    """
    Serialize a value as a JSON string literal whose text carries no raw bracket.

    json.dumps escapes the controls and, with ensure_ascii, every non-ASCII character; the
    brackets are written as the JSON escapes \\u005b and \\u005d, so the literal still decodes
    to the original value.
    """
    text = value if isinstance(value, str) else str(value)
    return json.dumps(text, ensure_ascii=True).replace("[", "\\u005b").replace("]", "\\u005d")


def _encode_roles(roles: Any) -> str:
    """
    Render the roles segment value: a JSON array of the role names, each name encoded with
    _encode_json_string, so the only raw brackets are the array's own.
    """
    if not isinstance(roles, (list, tuple)):
        roles = [] if roles is None else [roles]
    return "[" + ", ".join(_encode_json_string(role) for role in roles) + "]"


def _serialize_json(value: Any) -> str:
    """Serialize an audit payload or event echo as single-line, ASCII-only JSON."""
    return json.dumps(value, ensure_ascii=True, default=str)


def _format_log_message(event_type: str, user_context: Dict[str, Any], custom_data: Any,
                        log_type: str = "unknown") -> str:
    """
    Format the audit log message with event type, user context, and custom data.

    Every value placed inside a bracketed segment is encoded with _encode_prefix_value (the
    roles list with _encode_roles), and the custom data is serialized with _serialize_json, so
    no caller-influenced value can add a segment, a line break or a hidden character to the
    record.

    Args:
        event_type: The type of event (e.g., "[AUTHENTICATION]"), built with _event_type
        user_context: Dictionary with user, roles, and mfaEnabled
        custom_data: Additional data to include in the log
        log_type: The audit log type the record is for, named if formatting fails

    Returns:
        Formatted log message string
    """
    try:
        # Format user and roles
        user = user_context.get("user", "UNKNOWN")
        roles = user_context.get("roles", [])
        mfa_enabled = user_context.get("mfaEnabled", False)

        # Build the message
        message_parts = [
            event_type,
            f"[user: {_encode_prefix_value(user)}]",
            f"[roles: {_encode_roles(roles)}]",
            f"[mfaEnabled: {_encode_prefix_value(mfa_enabled)}]"
        ]

        # Add custom data if provided
        if custom_data is not None:
            message_parts.append(_serialize_json(custom_data))

        return " ".join(message_parts)
    except Exception as e:
        logger.exception(f"Failed to format log message: {e}")
        message = f"{event_type} [ERROR: Failed to format message]"
        # The marker record is still written; what it lost is reported here.
        report_undelivered_audit_records(log_type, "formatFailed", [message])
        return message


def _event_type(*segments: Tuple[str, Any]) -> str:
    """
    Build the leading segments of an audit record, e.g. [ACTIONS][type: assetCreate].

    Args:
        segments: (label, value) pairs; a value of None renders the label alone

    Returns:
        The segments joined, each value encoded with _encode_prefix_value
    """
    parts = []
    for label, value in segments:
        if value is None:
            parts.append(f"[{label}]")
        else:
            parts.append(f"[{label}: {_encode_prefix_value(value)}]")
    return "".join(parts)


def _message_byte_length(message: str) -> int:
    """
    Measure a log message against the PutLogEvents byte budgets.

    Args:
        message: A log message

    Returns:
        The message's UTF-8 byte count
    """
    return len(message.encode('utf-8'))


def _truncate_message(message: str) -> str:
    """
    Cut a log message down to the per-event PutLogEvents budget, marking where it was cut.

    Args:
        message: The formatted log message, event echo included

    Returns:
        The message unchanged, or its leading bytes followed by the truncation marker
    """
    encoded = message.encode('utf-8')
    if len(encoded) <= AUDIT_EVENT_MAX_MESSAGE_BYTES:
        return message

    keep = AUDIT_EVENT_MAX_MESSAGE_BYTES - len(AUDIT_EVENT_TRUNCATION_MARKER.encode('utf-8'))
    return encoded[:keep].decode('utf-8', errors='ignore') + AUDIT_EVENT_TRUNCATION_MARKER


def _prepare_log_event(timestamp: int, message: str) -> tuple:
    """
    Build one log event together with the byte cost it charges against the per-call budget.

    The message is measured once, here. A message over the per-event budget is cut first and
    the cut message measured, so the batch arithmetic never re-measures an entry.

    Args:
        timestamp: The event timestamp, shared by every entry of one write
        message: The formatted log message, event echo included

    Returns:
        A tuple of the log event and its byte cost: message bytes plus the per-event overhead
    """
    message_bytes = _message_byte_length(message)
    if message_bytes > AUDIT_EVENT_MAX_MESSAGE_BYTES:
        message = _truncate_message(message)
        message_bytes = _message_byte_length(message)

    return ({'timestamp': timestamp, 'message': message},
            message_bytes + AUDIT_EVENT_OVERHEAD_BYTES)


def _chunk_log_events(prepared_events: List[tuple]) -> List[List[Dict[str, Any]]]:
    """
    Split prepared log events into batches that fit both PutLogEvents budgets.

    Each entry arrives with the byte cost measured when it was built -- its UTF-8 message bytes
    plus AUDIT_EVENT_OVERHEAD_BYTES, since counting message length alone under-counts a batch.
    The running total carries that cost forward, so the batches are cut in one pass over the
    entries with no entry measured a second time.

    Args:
        prepared_events: (log event, byte cost) pairs, in the order they should be written

    Returns:
        A list of non-empty batches, each inside the event-count and byte budgets
    """
    batches = []
    batch = []
    batch_bytes = 0

    for log_event, event_bytes in prepared_events:
        if batch and (len(batch) >= AUDIT_BATCH_MAX_EVENTS
                      or batch_bytes + event_bytes > AUDIT_BATCH_MAX_BYTES):
            batches.append(batch)
            batch = []
            batch_bytes = 0
        batch.append(log_event)
        batch_bytes += event_bytes

    if batch:
        batches.append(batch)

    return batches


def _write_to_cloudwatch(log_group_name: str, message: str, event: Dict[str, Any]) -> None:
    """
    Write audit log entry to CloudWatch with silent failure.

    Args:
        log_group_name: The CloudWatch log group name
        message: The formatted log message
        event: The original event (for masking sensitive data)
    """
    _write_batch_to_cloudwatch(log_group_name, [message], event)


def _records_with_echo(messages: List[str], event: Optional[Dict[str, Any]]) -> List[str]:
    """
    Append the event echo to each formatted message, the way _write_batch_to_cloudwatch writes them.

    The echo is identical on every entry. Once replicating it would pass
    AUDIT_BATCH_ECHO_MAX_TOTAL_BYTES, the first entry carries it and the rest carry a reference.
    """
    event_suffix = _event_suffix(event)
    following_suffix = event_suffix
    if (event_suffix and len(messages) > 1
            and _message_byte_length(event_suffix) * len(messages)
            > AUDIT_BATCH_ECHO_MAX_TOTAL_BYTES):
        following_suffix = AUDIT_EVENT_ECHO_REFERENCE
    return [message + (event_suffix if index == 0 else following_suffix)
            for index, message in enumerate(messages)]


def _put_audit_records(log_group_name: str, records: List[str], client: Any) -> None:
    """
    Write complete records (event echo included) to one audit log group with silent failure.

    Creates the day's log stream once per group per container, cuts the records into batches inside
    the PutLogEvents budgets, and reports every record a failure loses as undelivered. Never raises.

    Args:
        log_group_name: The CloudWatch log group name
        records: The records, in the order they should be written
        client: The CloudWatch Logs client to write with; None reports the records undelivered
    """
    log_type = _audit_log_types_by_name.get(log_group_name, "unknown")
    try:
        if not client:
            logger.error("CloudWatch Logs client not initialized, cannot write audit log")
            report_undelivered_audit_records(log_type, "clientUnavailable", records)
            return

        # Create log stream name based on current date
        log_stream_name = utc_now().strftime("%Y/%m/%d")

        # Ensure log stream exists (create if it doesn't), once per group per day per container
        if _created_log_streams.get(log_group_name) != log_stream_name:
            try:
                client.create_log_stream(
                    logGroupName=log_group_name,
                    logStreamName=log_stream_name
                )
            except client.exceptions.ResourceAlreadyExistsException:
                # Log stream already exists, which is fine
                pass
            except Exception as e:
                logger.exception(f"Failed to create log stream {log_stream_name} in {log_group_name}: {e}")
                report_undelivered_audit_records(log_type, "createLogStreamFailed", records)
                return
            _created_log_streams[log_group_name] = log_stream_name

        timestamp = int(utc_now().timestamp() * 1000)
        batches = _chunk_log_events([_prepare_log_event(timestamp, record) for record in records])
        # From here each batch reports its own entries if its write fails.
        records = []

        # Write to CloudWatch, one call per batch inside the PutLogEvents budgets. Each batch
        # is attempted on its own so a rejected batch does not take the ones after it with it.
        for batch in batches:
            try:
                client.put_log_events(
                    logGroupName=log_group_name,
                    logStreamName=log_stream_name,
                    logEvents=batch
                )
            except Exception as e:
                # The stream may have gone away under the entry recorded above, so the entry goes
                # to the function log instead and the write after this one creates the stream again.
                _created_log_streams.pop(log_group_name, None)
                logger.exception(
                    f"Failed to write {len(batch)} audit entries to CloudWatch log group "
                    f"{log_group_name}: {e}"
                )
                report_undelivered_audit_records(
                    log_type, "putLogEventsFailed", [log_event["message"] for log_event in batch])

    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to write audit log to CloudWatch log group {log_group_name}: {e}")
        report_undelivered_audit_records(log_type, "unexpectedError", records)


def _write_batch_to_cloudwatch(log_group_name: str, messages: list, event: Dict[str, Any]) -> None:
    """
    Write multiple audit log entries to CloudWatch with silent failure.

    Batching matters for bulk operations: one put_log_events round trip per batch instead
    of one per entry. Entries are chunked to stay inside both PutLogEvents budgets -- the
    event count and the byte total -- and an entry over the per-event byte budget is
    truncated rather than dropped. Every entry is written whatever the budgets require.

    The event echo is what drives the byte total, and it is identical on every entry: an
    echo that grows with the entry count, as a bulk download's does, would cost entries x
    event bytes to replicate. Once its replication would pass
    AUDIT_BATCH_ECHO_MAX_TOTAL_BYTES the echo is written on the first entry and referenced
    from the rest, so the bytes written grow with the number of entries and not with their
    square.

    Args:
        log_group_name: The CloudWatch log group name
        messages: The formatted log messages
        event: The original event (for masking sensitive data; appended to each entry)
    """
    log_type = _audit_log_types_by_name.get(log_group_name, "unknown")
    # What is reported as undelivered if the write stops before any batch is sent.
    records = list(messages)
    try:
        records = _records_with_echo(messages, event)
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to write audit log to CloudWatch log group {log_group_name}: {e}")
        report_undelivered_audit_records(log_type, "unexpectedError", records)
        return
    _put_audit_records(log_group_name, records, cloudwatch_logs)


def log_authentication(event: Dict[str, Any], authenticated: bool, custom_data: Optional[Any] = None) -> None:
    """
    Log authentication events with silent failure.

    Args:
        event: The API Gateway event
        authenticated: Whether authentication was successful
        custom_data: Additional data to log (optional)
    """
    try:
        user_context = _extract_user_context(event)
        event_type = _event_type(("AUTHENTICATION", None), ("authenticated", authenticated))
        _write_audit_entry(ResourceKeys.AUDIT_LOG_AUTHENTICATION, event_type, user_context, custom_data, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log authentication audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_AUTHENTICATION, "unexpectedError", [None])


# Authorization entries collected inside deferred_authorization_audit(), as (log group, message, event)
# in the order they were recorded; None outside the block, where each entry is written as it is recorded.
_deferred_authorization_entries: contextvars.ContextVar = contextvars.ContextVar(
    "_deferred_authorization_entries", default=None
)


def _write_deferred_authorization_entries(entries: List[Tuple[str, str, Dict[str, Any]]]) -> None:
    """
    Write collected authorization entries with _write_batch_to_cloudwatch, one write per run of
    entries that share a log group and an event, in the order they were recorded.

    Args:
        entries: (log group name, formatted message, event) tuples
    """
    run_key = None
    run_messages: List[str] = []
    run_event: Dict[str, Any] = {}
    for log_group_name, message, event in entries:
        try:
            key = (log_group_name, json.dumps(event, sort_keys=True, default=str))
        except Exception:
            # An event that cannot be serialized is grouped by identity instead.
            key = (log_group_name, id(event))
        if run_messages and key != run_key:
            _write_batch_to_cloudwatch(run_key[0], run_messages, run_event)
            run_messages = []
        run_key, run_event = key, event
        run_messages.append(message)
    if run_messages:
        _write_batch_to_cloudwatch(run_key[0], run_messages, run_event)


@contextlib.contextmanager
def deferred_authorization_audit() -> Iterator[None]:
    """
    Collect the log_authorization entries recorded inside the block and write them together when
    it ends, in batches, instead of one CloudWatch call per entry.

    For handlers that decide many objects one by one (search checks every hit), where an in-line
    write per refused decision costs a round trip each. Every entry is kept and written, also when
    the block raises; entries share the timestamp of the write. A nested block adds to the outer one.
    """
    if _deferred_authorization_entries.get() is not None:
        yield
        return
    entries: List[Tuple[str, str, Dict[str, Any]]] = []
    token = _deferred_authorization_entries.set(entries)
    try:
        yield
    finally:
        _deferred_authorization_entries.reset(token)
        try:
            _write_deferred_authorization_entries(entries)
        except Exception as e:
            # Silent failure - log locally but don't raise
            logger.exception(f"Failed to write {len(entries)} deferred authorization audit entries: {e}")
            report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_AUTHORIZATION, "unexpectedError",
                                             [message for _, message, _ in entries])


def log_authorization(claims_and_roles: Dict[str, Any], authorized: bool, custom_data: Optional[Any] = None) -> None:
    """
    Log authorization events with silent failure using claims_and_roles directly.

    Inside the request audit context of this thread, the record gains the contract keys of the
    request (``action``, ``outcome`` ``success`` or ``denied``, ``entity``, ``actor``, ``request``,
    ``recordId``, ``eventTime``; a legacy key of the same name, such as the checked ``action``, moves
    under ``legacy``) and echoes the request's short echo. An allowed decision is buffered and written
    in the request's end-of-request flush, also inside deferred_authorization_audit(). A denial is
    written at once, or at the end of a deferred_authorization_audit() block. Outside a context the
    record and its echo are unchanged.

    Args:
        claims_and_roles: The claims and roles dictionary
        authorized: Whether authorization was successful
        custom_data: Additional data to log (optional)
    """
    try:
        # Extract user context from claims_and_roles
        user_context = {
            "user": claims_and_roles.get("tokens", ["UNKNOWN"])[0] if claims_and_roles.get("tokens") else "UNKNOWN",
            "roles": claims_and_roles.get("roles", []),
            "mfaEnabled": claims_and_roles.get("mfaEnabled", False)
        }
        
        event_type = _event_type(("AUTHORIZATION", None), ("authorized", authorized))

        # Create a minimal mock event for CloudWatch logging
        mock_event = {
            'requestContext': {
                'authorizer': {
                    'jwt': {
                        'claims': {
                            'sub': user_context["user"]
                        }
                    }
                }
            }
        }

        ctx = current_audit_context()
        echo_event = mock_event
        if ctx is not None:
            custom_data = _with_canonical_keys(custom_data, _authorization_keys(ctx, authorized))
            echo_event = _short_echo_event(ctx.event) or mock_event

        if _buffer_allowed_authorization(ctx, authorized, event_type, user_context, custom_data, echo_event):
            return

        message = _format_log_message(event_type, user_context, custom_data,
                                      _audit_log_type(ResourceKeys.AUDIT_LOG_AUTHORIZATION))

        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_AUTHORIZATION)
        if not log_group_name:
            _report_unresolved_log_group(ResourceKeys.AUDIT_LOG_AUTHORIZATION, [message], echo_event)
            return

        pending = _deferred_authorization_entries.get()
        if pending is not None:
            pending.append((log_group_name, message, echo_event))
            return

        _write_to_cloudwatch(log_group_name, message, echo_event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log authorization audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_AUTHORIZATION, "unexpectedError", [None])


def format_authorization_denial_record(claims_and_roles: Dict[str, Any], probe_endpoint: str,
                                       custom_data: Any) -> str:
    """
    Format the record of a denial found by a permission-check endpoint: the record
    log_authorization writes for a denial, with a [probe: <endpoint>] segment after the event type.

    Args:
        claims_and_roles: The claims and roles dictionary of the caller
        probe_endpoint: The permission-check endpoint that made the check, e.g. "GET /auth/routes"
        custom_data: The denied check

    Returns:
        The formatted record
    """
    tokens = claims_and_roles.get("tokens") or []
    user_context = {
        "user": tokens[0] if tokens else "UNKNOWN",
        "roles": claims_and_roles.get("roles", []),
        "mfaEnabled": claims_and_roles.get("mfaEnabled", False)
    }
    event_type = (_event_type(("AUTHORIZATION", None), ("authorized", False)) + " "
                  + _event_type(("probe", probe_endpoint)))
    return _format_log_message(event_type, user_context, custom_data,
                               _audit_log_type(ResourceKeys.AUDIT_LOG_AUTHORIZATION))


def log_authorization_api(event: Dict[str, Any], authorized: bool, custom_data: Optional[Any] = None) -> None:
    """
    Log API authorization events with silent failure.

    The record echoes the request's short echo (``_short_echo_event``: request id and time, method,
    path, route, path parameters, connection addresses), never its headers, query string or body.
    Inside the request audit context opened for this event, the record also gains the contract keys
    (``action`` and ``entity`` of the route, ``outcome`` ``success`` or ``denied``, ``actor``,
    ``request``, ``recordId``, ``eventTime``; the checked method, which the record names as
    ``action``, moves to ``legacy.action``), and an allowed decision is buffered and written in the
    request's end-of-request flush; a denial is written at once. Outside a context every decision is
    written at once, without the contract keys.

    Args:
        event: The API Gateway event
        authorized: Whether authorization was successful
        custom_data: Additional data to log (optional)
    """
    try:
        user_context = _extract_user_context(event)
        event_type = _event_type(("AUTHORIZATION", None), ("authorized", authorized))
        ctx = _context_for(event)
        if ctx is not None:
            custom_data = _with_canonical_keys(custom_data, _authorization_keys(ctx, authorized))
        if _buffer_allowed_authorization(ctx, authorized, event_type, user_context,
                                         custom_data, _short_echo_event(event)):
            return
        _write_audit_entry(ResourceKeys.AUDIT_LOG_AUTHORIZATION, event_type, user_context, custom_data, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log authorization audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_AUTHORIZATION, "unexpectedError", [None])


def _buffer_allowed_authorization(ctx: Optional["AuditContext"], authorized: Any, event_type: str,
                                  user_context: Dict[str, Any], custom_data: Any,
                                  event: Optional[Dict[str, Any]]) -> bool:
    """
    Hand an allowed authorization record to the request audit context's end-of-request flush.

    Only a decision that is exactly ``True`` and made inside an open context is buffered; a denial,
    or any decision outside a context, is left to the caller to write at once. The record is
    formatted exactly as an immediate write formats it (same prefix, payload and echo); only when it
    is written changes. ``event`` is the event the record echoes. Returns True when the record was
    handed over.
    """
    if ctx is None or authorized is not True:
        return False
    log_type = _audit_log_type(ResourceKeys.AUDIT_LOG_AUTHORIZATION)
    message = _format_log_message(event_type, user_context, custom_data, log_type)
    _enqueue_records(ctx, log_type, _records_with_echo([message], event), buffer=True)
    return True


def _authorization_keys(ctx: "AuditContext", authorized: Any) -> Dict[str, Any]:
    """
    The contract keys an authorization record gains inside a request audit context: the route's
    action and entity (with its id when the path names it), ``outcome`` ``success`` for an allowed
    decision and ``denied`` otherwise, and the request's actor, request, recordId and eventTime.
    """
    route_spec = ctx.route_spec
    outcome = OUTCOME_SUCCESS if authorized is True else OUTCOME_DENIED
    keys = _legacy_canonical_keys(ctx, route_spec.action if route_spec else None, outcome,
                                  route_spec.entity_type if route_spec else None)
    if route_spec is not None and "entity" in keys:
        id_field = _ENTITY_ID_FIELDS.get(route_spec.entity_type)
        entity_id = _path_ids(ctx.event).get(id_field) if id_field else None
        if entity_id:
            keys["entity"]["id"] = _cap_value(entity_id)[0]
    return keys


def log_file_upload(
    event: Dict[str, Any],
    database_id: str,
    asset_id: str,
    file_path: str,
    upload_denied: bool,
    upload_denied_reason: Optional[str] = None,
    custom_data: Optional[Any] = None
) -> None:
    """
    Log file upload events with silent failure.

    Args:
        event: The API Gateway event
        database_id: The database ID
        asset_id: The asset ID
        file_path: The file path
        upload_denied: Whether the upload was denied
        upload_denied_reason: Reason for denial (optional)
        custom_data: Additional data to log (optional)
    """
    try:
        user_context = _extract_user_context(event)
        event_type = "[FILEUPLOAD]"
        
        # Build file upload specific data
        upload_data = {
            "databaseId": database_id,
            "assetId": asset_id,
            "filePath": file_path,
            "uploadDenied": upload_denied
        }
        
        if upload_denied_reason:
            upload_data["uploadDeniedReason"] = upload_denied_reason
        
        if custom_data:
            upload_data["customData"] = custom_data
        
        canonical = _legacy_canonical(event, legacy_function="log_file_upload", default_action="fileUpload",
                                      default_entity_type="file",
                                      outcome=OUTCOME_DENIED if upload_denied else OUTCOME_SUCCESS)
        _write_audit_entry(ResourceKeys.AUDIT_LOG_FILEUPLOAD, event_type, user_context, upload_data, event,
                           canonical=canonical, buffer=not upload_denied)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log file upload audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_FILEUPLOAD, "unexpectedError", [None])


def log_file_download(
    event: Dict[str, Any],
    database_id: str,
    asset_id: str,
    file_path: str,
    custom_data: Optional[Any] = None
) -> None:
    """
    Log file download events with silent failure.

    Args:
        event: The API Gateway event
        database_id: The database ID
        asset_id: The asset ID
        file_path: The file path
        custom_data: Additional data to log (optional)
    """
    try:
        user_context = _extract_user_context(event)
        event_type = "[FILEDOWNLOAD]"
        
        # Build file download specific data
        download_data = {
            "databaseId": database_id,
            "assetId": asset_id,
            "filePath": file_path
        }
        
        if custom_data:
            download_data["customData"] = custom_data
        
        canonical = _legacy_canonical(event, legacy_function="log_file_download", default_action="fileDownload",
                                      default_entity_type="file")
        _write_audit_entry(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD, event_type, user_context, download_data, event,
                           canonical=canonical, buffer=True)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log file download audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD, "unexpectedError", [None])


def log_file_download_bulk(
    event: Dict[str, Any],
    database_id: str,
    asset_id: str,
    file_entries: list,
    custom_data_base: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log a bulk file download (one entry per file) with silent failure.

    Args:
        event: The API Gateway event
        database_id: The database ID
        asset_id: The asset ID
        file_entries: List of dicts with 'filePath' and optional 'versionId'
        custom_data_base: Data common to every entry (e.g. downloadType)
    """
    messages: List[str] = []
    try:
        user_context = _extract_user_context(event)
        event_type = "[FILEDOWNLOAD]"
        log_type = _audit_log_type(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD)
        ctx = _context_for(event)
        canonical = _legacy_canonical(event, legacy_function="log_file_download", default_action="fileDownload",
                                      default_entity_type="file")

        for entry in file_entries:
            download_data = {
                "databaseId": database_id,
                "assetId": asset_id,
                "filePath": entry.get("filePath")
            }
            custom_data = dict(custom_data_base or {})
            if entry.get("versionId") is not None:
                custom_data["versionId"] = entry.get("versionId")
            if custom_data:
                download_data["customData"] = custom_data
            if ctx is not None:
                download_data = _with_canonical_keys(download_data, _legacy_canonical_keys(
                    ctx, canonical["action"], canonical["outcome"], canonical["entityType"]))
            messages.append(_format_log_message(event_type, user_context, download_data, log_type))

        if ctx is not None:
            # One record per file, all delivered in the request's flush.
            _note_recorded(ctx, canonical["action"])
            _enqueue_records(ctx, log_type, _records_with_echo(messages, event), buffer=True)
            return

        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD)
        if not log_group_name:
            _report_unresolved_log_group(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD, messages, event)
            return

        _write_batch_to_cloudwatch(log_group_name, messages, event)

    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log bulk file download audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD, "unexpectedError",
                                         messages or [None])


def log_file_download_streamed(
    event: Dict[str, Any],
    database_id: str,
    asset_id: str,
    file_path: str,
    custom_data: Optional[Any] = None
) -> None:
    """
    Log streamed file download events with silent failure.

    Args:
        event: The API Gateway event
        database_id: The database ID
        asset_id: The asset ID
        file_path: The file path
        custom_data: Additional data to log (optional)
    """
    try:
        user_context = _extract_user_context(event)
        event_type = "[FILEDOWNLOAD-STREAMED]"
        
        # Build file download specific data
        download_data = {
            "databaseId": database_id,
            "assetId": asset_id,
            "filePath": file_path
        }
        
        if custom_data:
            download_data["customData"] = custom_data
        
        canonical = _legacy_canonical(event, legacy_function="log_file_download_streamed",
                                      default_action="fileStream", default_entity_type="file")
        _write_audit_entry(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD_STREAMED, event_type, user_context, download_data,
                           event, canonical=canonical, buffer=True)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log streamed file download audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD_STREAMED, "unexpectedError", [None])


def _log_auth_record(key, label: str, secondary_type: str, event: Dict[str, Any], custom_data: Any, *,
                     outcome: str, reason: Optional[str], entity_id: Optional[str],
                     ids: Optional[Dict[str, Any]], item_count: Optional[int],
                     changes: Optional[Dict[str, Any]], previous: Optional[Dict[str, Any]],
                     request_parameters: Optional[Dict[str, Any]]) -> None:
    """
    Write an auth change or auth other record.

    The prefix is ``[<label>][type: <secondary_type>]`` in every case. Outside the request audit
    context the record is the earlier one: the legacy payload and the short request echo. Inside it
    the payload is the legacy payload (masked) plus the record contract keys: ``action`` (the route's
    action when the secondary type is the one the route registers, else the secondary type's own),
    ``outcome``, ``entity``, ``actor``, ``request``, the top-level ids, ``changes`` / ``previous`` /
    ``requestParameters`` (masked and capped like an action record's) and ``legacyType`` (the
    ``[type: ...]`` value). A legacy key of a contract key's name with a different value moves under
    ``legacy``. The record is bounded to AUDIT_RECORD_MAX_BYTES with the compact action echo; a
    success is buffered for the request's flush, any other outcome is written at once.
    """
    user_context = _extract_user_context(event)
    event_type = _event_type((label, None), ("type", secondary_type))
    ctx = _context_for(event)
    if ctx is None:
        _write_audit_entry(key, event_type, user_context, custom_data, event)
        return
    log_type = _audit_log_type(key)
    action, entity_type = _legacy_route_action(ctx, secondary_type)
    action = action or secondary_type
    outcome_written = outcome if outcome in AUDIT_OUTCOMES else OUTCOME_FAILED
    legacy = mask_sensitive_data(custom_data) if custom_data is not None else None
    record_ids: Dict[str, Any] = {}
    if isinstance(legacy, dict):
        record_ids.update({k: v for k, v in legacy.items() if k in AUDIT_ID_FIELDS and v is not None})
    record_ids.update({k: v for k, v in (ids or {}).items() if v is not None})
    legacy_bytes = len(_serialize_json(legacy)) if legacy is not None else 0
    head = _format_log_message(event_type, user_context, None, log_type)

    def build_payload(max_bytes: int) -> Dict[str, Any]:
        contract = build_audit_payload(
            action, actor=ctx.actor, request=ctx.request, outcome=outcome_written, reason=reason,
            entity_type=entity_type, entity_id=entity_id, ids=record_ids, item_count=item_count,
            changes=changes, previous=previous, request_parameters=request_parameters,
            max_bytes=max(AUDIT_VALUE_MAX_BYTES, max_bytes - legacy_bytes))
        contract["legacyType"] = secondary_type
        return _with_canonical_keys(legacy, contract)

    record = _bounded_action_record(head, build_payload, _compact_echo(event))
    _note_recorded(ctx, action)
    _enqueue_records(ctx, log_type, [record], buffer=outcome_written == OUTCOME_SUCCESS)


def log_auth_other(event: Dict[str, Any], secondary_type: str, custom_data: Optional[Any] = None, *,
                   outcome: str = "success", reason: Optional[str] = None,
                   entity_id: Optional[str] = None, ids: Optional[Dict[str, Any]] = None,
                   item_count: Optional[int] = None, changes: Optional[Dict[str, Any]] = None,
                   previous: Optional[Dict[str, Any]] = None,
                   request_parameters: Optional[Dict[str, Any]] = None) -> None:
    """
    Log other authentication-related events with silent failure.

    Inside the request audit context the record carries the record contract keys as well as the
    legacy payload (see _log_auth_record). Pass what a write changed as ``changes`` (and the prior
    values the handler already holds as ``previous``), what a read asked for as
    ``request_parameters``. Never pass a secret: redaction applies, but the caller chooses the fields.

    Args:
        event: The API Gateway event
        secondary_type: The secondary type of auth event, the record's [type: ...] value
        custom_data: The legacy payload (optional)
        outcome, reason, entity_id, ids, item_count, changes, previous, request_parameters: As for
            log_action; used inside a request audit context
    """
    try:
        _log_auth_record(ResourceKeys.AUDIT_LOG_AUTHOTHER, "AUTHOTHER", secondary_type, event, custom_data,
                         outcome=outcome, reason=reason, entity_id=entity_id, ids=ids,
                         item_count=item_count, changes=changes, previous=previous,
                         request_parameters=request_parameters)
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log auth other audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_AUTHOTHER, "unexpectedError", [None])


def log_auth_changes(event: Dict[str, Any], secondary_type: str, custom_data: Optional[Any] = None, *,
                     outcome: str = "success", reason: Optional[str] = None,
                     entity_id: Optional[str] = None, ids: Optional[Dict[str, Any]] = None,
                     item_count: Optional[int] = None, changes: Optional[Dict[str, Any]] = None,
                     previous: Optional[Dict[str, Any]] = None,
                     request_parameters: Optional[Dict[str, Any]] = None) -> None:
    """
    Log authentication/authorization changes with silent failure.

    Inside the request audit context the record carries the record contract keys as well as the
    legacy payload (see _log_auth_record). Pass each field the change set, with its new value, as
    ``changes`` and the prior values the handler already holds as ``previous``. Never pass a secret
    (an API key, a password, a temporary password): redaction applies, but the caller chooses the
    fields.

    Args:
        event: The API Gateway event
        secondary_type: The secondary type of auth change, the record's [type: ...] value
        custom_data: The legacy payload (optional)
        outcome, reason, entity_id, ids, item_count, changes, previous, request_parameters: As for
            log_action; used inside a request audit context
    """
    try:
        _log_auth_record(ResourceKeys.AUDIT_LOG_AUTHCHANGES, "AUTHCHANGES", secondary_type, event, custom_data,
                         outcome=outcome, reason=reason, entity_id=entity_id, ids=ids,
                         item_count=item_count, changes=changes, previous=previous,
                         request_parameters=request_parameters)
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log auth changes audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_AUTHCHANGES, "unexpectedError", [None])


def log_actions(event: Dict[str, Any], secondary_type: str, custom_data: Optional[Any] = None,
                outcome: str = "success") -> None:
    """
    Log general actions with silent failure.

    Args:
        event: The API Gateway event
        secondary_type: The secondary type of action
        custom_data: Additional data to log (optional)
        outcome: Inside an audit context, the outcome the record carries (success or failed); a
            failed record is written at once rather than buffered
    """
    try:
        user_context = _extract_user_context(event)
        event_type = _event_type(("ACTIONS", None), ("type", secondary_type))
        canonical = _legacy_canonical(event, secondary_type=secondary_type, outcome=outcome)
        _write_audit_entry(ResourceKeys.AUDIT_LOG_ACTIONS, event_type, user_context, custom_data, event,
                           canonical=canonical, buffer=outcome == "success")
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log actions audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_ACTIONS, "unexpectedError", [None])


def log_errors(event: Dict[str, Any], secondary_type: str, custom_data: Optional[Any] = None) -> None:
    """
    Log errors with silent failure.

    Args:
        event: The API Gateway event
        secondary_type: The secondary type of error
        custom_data: Additional data to log (optional)
    """
    try:
        user_context = _extract_user_context(event)
        event_type = _event_type(("ERRORS", None), ("type", secondary_type))
        canonical = _legacy_canonical(event, outcome=OUTCOME_FAILED)
        _write_audit_entry(ResourceKeys.AUDIT_LOG_ERRORS, event_type, user_context, custom_data, event,
                           canonical=canonical, buffer=False)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log errors audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_ERRORS, "unexpectedError", [None])


def _gateway_user_context(verified_context: Any) -> Dict[str, Any]:
    """
    The prefix user context of an allowed authorizer record, from the context the authorizer built
    out of the verified token (or API key): the user id the handlers resolve (``vams:tokens``, then
    ``cognito:username``, ``username``, ``sub``, ``upn``, ``email``, as ``request_to_claims`` reads
    them, normalized the same way), the roles the authorizer read and the MFA state it resolved.
    """
    user_context: Dict[str, Any] = {"user": "unknown", "roles": [], "mfaEnabled": False}
    if not isinstance(verified_context, dict):
        return user_context
    user = None
    tokens = verified_context.get("vams:tokens")
    if isinstance(tokens, str):
        try:
            parsed = json.loads(tokens)
            if isinstance(parsed, list) and parsed:
                user = parsed[0]
        except ValueError:
            user = None
    if not user:
        for claim in ("cognito:username", "username", "sub", "upn", "email"):
            if verified_context.get(claim):
                user = verified_context[claim]
                break
    if user:
        user_context["user"] = normalize_userid(str(user)) or "unknown"
    roles = verified_context.get("vams:roles")
    if isinstance(roles, str):
        try:
            parsed = json.loads(roles)
            if isinstance(parsed, list):
                user_context["roles"] = [str(role) for role in parsed]
        except ValueError:
            pass
    mfa_value = verified_context.get("vams:mfaEnabled", "false")
    user_context["mfaEnabled"] = mfa_value == "true" if isinstance(mfa_value, str) else bool(mfa_value)
    return user_context


def log_authorization_gateway(event: Dict[str, Any], authorized: bool, failure_reason: Optional[str] = None,
                              token_audience: Optional[str] = None,
                              verified_context: Optional[Dict[str, Any]] = None) -> None:
    """
    Log authorization events from API Gateway authorizer with silent failure.

    SECURITY: This function is designed for the API Gateway authorizer which runs
    BEFORE normal request processing. Its payload carries only non-sensitive data:
    - User ID, roles and MFA state (only from the verified claims of an allowed request, the
      ``verified_context`` the authorizer built; a denial names the user ``unknown``)
    - Authorization result (success/failure)
    - Generic failure reason (no token details or sensitive data)
    - Source IP address
    - The accepted audience (Cognito app client id or external IdP audience) a verified
      token was issued to, so calls made through an additional client are distinguishable
      It is a public client identifier, never token content.
    The record's echo is the short request echo (_short_echo_event): request id and time, method,
    path, route, path parameters and connection addresses, never a header.

    NEVER logs:
    - Raw JWT tokens
    - Authorization headers, or any other header on the credential key list
    - Token signatures
    - Detailed validation errors that could expose token structure

    Args:
        event: The API Gateway authorizer event
        authorized: Whether authorization was successful
        failure_reason: Generic failure reason (optional, for failures only)
        token_audience: Accepted audience of the verified token (optional, successes only)
        verified_context: The authorizer context built from the verified token or API key (allowed
            requests only); an ignored-path allow has none and names the user ``unknown``
    """
    try:
        # Only the verified claims of an allowed request name the user.
        user_context = _gateway_user_context(verified_context if authorized else None)
        
        # Get source IP (safe to log)
        source_ip = event.get('requestContext', {}).get('http', {}).get('sourceIp', 'unknown')
        
        # Create safe log message
        event_type = _event_type(("AUTHORIZATION", None), ("authorized", authorized))
        custom_data = {
            "sourceIp": source_ip,
            "failureReason": failure_reason if not authorized else None
        }
        if authorized and token_audience:
            custom_data["tokenAudience"] = str(token_audience)
        _write_audit_entry(ResourceKeys.AUDIT_LOG_AUTHORIZATION, event_type, user_context, custom_data, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log authorization gateway audit event: {e}")
        report_undelivered_audit_records(ResourceKeys.AUDIT_LOG_AUTHORIZATION, "unexpectedError", [None])


# ---------------------------------------------------------------------------------------------------
# Action records (record contract version 1), the request audit context and its collector
# ---------------------------------------------------------------------------------------------------

# The record contract version. Within a version keys are only added; renaming or removing a key
# needs a new version.
AUDIT_RECORD_VERSION = 1

# Size caps on what a record carries. A value is one entry of changes, previous, requestParameters
# or details, or one id field, measured as its JSON text; a value over the cap is replaced by
# {"truncated": true, "sha256", "bytes", "preview"}. The record cap bounds the whole payload; past it
# the largest values lose their preview, then whole sections are replaced by their hash.
AUDIT_VALUE_MAX_BYTES = 2048
AUDIT_RECORD_MAX_BYTES = 32768
AUDIT_VALUE_PREVIEW_CHARS = 512

# The request audit context writes its buffered records once, when the handler returns, with every
# group written in parallel and the whole flush bounded by this deadline. A timer flushes early when
# the function nears its timeout, so a timing-out invocation still delivers.
AUDIT_FLUSH_DEADLINE_SECONDS = 1.5
AUDIT_PRE_TIMEOUT_MARGIN_MS = 750
# The time a flush leaves before the function's timeout to write the auditFallback lines and the
# AuditWriteFailures metric of the records it could not confirm. A flush waits at most
# min(AUDIT_FLUSH_DEADLINE_SECONDS, remaining time - this reserve), so a pre-timeout flush that meets
# a slow write still reports what it lost before the runtime ends the invocation.
AUDIT_FALLBACK_RESERVE_MS = 250

OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_DENIED = "denied"
AUDIT_OUTCOMES = frozenset({OUTCOME_SUCCESS, OUTCOME_FAILED, OUTCOME_DENIED})

AUTH_METHOD_COGNITO = "cognito"
AUTH_METHOD_EXTERNAL_IDP = "externalIdp"
AUTH_METHOD_JWT_UNKNOWN_ISSUER = "jwt"
AUTH_METHOD_API_KEY_RECORD = "apiKey"
AUTH_METHOD_SYSTEM = "system"

SYSTEM_USER = "SYSTEM_USER"

# Inside an audit context a legacy record's payload gains the contract keys; a legacy key of the
# same name with a different value is moved under this key.
AUDIT_LEGACY_KEY = "legacy"

# The id fields a record carries at the top level, by their record key.
AUDIT_ID_FIELDS = (
    "databaseId", "assetId", "filePath", "versionId", "assetVersionId", "pipelineId", "templateId",
    "workflowId", "executionId", "executionGroupId", "assetLinkId", "commentId", "tagId", "tagTypeId",
    "metadataSchemaId", "subscriptionId", "roleName", "constraintId", "targetUserId", "apiKeyId",
    "uploadId", "triggerType", "sessionId", "targetDatabaseId", "targetAssetId", "targetFilePath",
)

# The id field that names an entity of each type, used when log_action is given no entity_id.
_ENTITY_ID_FIELDS = {
    "database": "databaseId", "asset": "assetId", "file": "filePath", "folder": "filePath",
    "assetVersion": "assetVersionId", "fileVersion": "versionId", "comment": "commentId",
    "metadataSchema": "metadataSchemaId", "assetLink": "assetLinkId", "tag": "tagId",
    "tagType": "tagTypeId", "subscription": "subscriptionId", "pipeline": "pipelineId",
    "pipelineTemplate": "templateId", "workflow": "workflowId", "workflowTrigger": "triggerType",
    "execution": "executionId", "executionGroup": "executionGroupId", "role": "roleName",
    "userRole": "targetUserId", "constraint": "constraintId", "user": "targetUserId",
    "apiKey": "apiKeyId", "workstation": "sessionId",
}

# Actions of these kinds record the request only: log_action drops the write sections (changes,
# previous) a call passes for one, whatever the call site, and logs an error naming the action.
_REQUEST_ONLY_KINDS = frozenset({KIND_READ, KIND_SEARCH, KIND_DOWNLOAD})
_WRITE_ONLY_SECTIONS = ("changes", "previous")

# Whether a read or search record carries itemCount, the number of items or hits it returned (a
# count, never the items). Held in this one switch while the maintainers confirm it; set it False
# to drop the field from read and search records.
AUDIT_READ_ITEM_COUNT = True

# Whether read and search actions write records: the deployment's app.auditLogging.recordReads,
# set on every Lambda by CDK as this environment variable. Only "false" turns them off, so a function
# deployed without the variable keeps recording reads. Downloads and exports are recorded either way.
AUDIT_RECORD_READS_ENV = "AUDIT_RECORD_READS"
_READ_SWITCH_KINDS = frozenset({KIND_READ, KIND_SEARCH})


def audit_records_reads() -> bool:
    """Whether this deployment records read and search actions (read on each call)."""
    return os.environ.get(AUDIT_RECORD_READS_ENV, "").strip().lower() != "false"


# The record sections whose values are what a write wrote or a read asked for.
_VALUE_SECTIONS = ("changes", "previous", "requestParameters", "details")

# The prefix label of each audit log group.
_GROUP_LABELS = {
    GROUP_ACTIONS: "ACTIONS",
    "auditAuthChanges": "AUTHCHANGES",
    "auditAuthOther": "AUTHOTHER",
    GROUP_AUTHORIZATION: "AUTHORIZATION",
    GROUP_ERRORS: "ERRORS",
    GROUP_FILE_UPLOAD: "FILEUPLOAD",
    GROUP_FILE_DOWNLOAD: "FILEDOWNLOAD",
    GROUP_FILE_DOWNLOAD_STREAMED: "FILEDOWNLOAD-STREAMED",
}

# The file groups' records carry no [type: ...] segment, as their legacy records do not, so the
# documented prefix patterns keep matching; the action is in the payload.
_GROUPS_WITHOUT_TYPE_SEGMENT = frozenset({GROUP_FILE_UPLOAD, GROUP_FILE_DOWNLOAD, GROUP_FILE_DOWNLOAD_STREAMED})

# The audit log group key of each audit log type.
_AUDIT_LOG_KEYS_BY_TYPE = {_audit_log_type(_key): _key for _key in _AUDIT_LOG_GROUP_KEYS}


class AuditContext:
    """What one request's audit records share: the actor and request facts, resolved once, and the
    records buffered until the request ends. Created by :func:`audit_request`."""

    def __init__(self, event: Dict[str, Any], lambda_context: Any, user_context: Dict[str, Any],
                 actor: Dict[str, Any], request: Dict[str, Any], route_spec: Optional[AuditSpec]):
        self.event = event
        self.lambda_context = lambda_context
        self.user_context = user_context
        self.actor = actor
        self.request = request
        self.route_spec = route_spec
        self.pending: Dict[str, List[str]] = {}
        self.lock = threading.Lock()
        self.timer: Optional[threading.Timer] = None
        # Set when the buffer must not hold records any longer (the request ended, or the function
        # is too close to its timeout to wait); a record recorded after that is written at once.
        self.write_through = False
        # The actions this request has recorded outside the errors group, so audited_handler records
        # a failed outcome for the route's action only when the handler recorded none.
        self.recorded_actions: Set[str] = set()


_audit_context: contextvars.ContextVar = contextvars.ContextVar("_audit_context", default=None)

# The flush pool, created on first use: one worker per audit log group, so a request that touched
# several groups writes them concurrently. Its threads persist across invocations of the container.
_flush_executor: Optional[ThreadPoolExecutor] = None
_flush_executor_lock = threading.Lock()


def _get_flush_executor() -> ThreadPoolExecutor:
    global _flush_executor
    if _flush_executor is None:
        with _flush_executor_lock:
            if _flush_executor is None:
                _flush_executor = ThreadPoolExecutor(max_workers=len(_AUDIT_LOG_GROUP_KEYS),
                                                     thread_name_prefix="audit-flush")
    return _flush_executor


def current_audit_context() -> Optional[AuditContext]:
    """The audit context of the request being served on this thread, or None."""
    return _audit_context.get()


def _note_recorded(ctx: Optional[AuditContext], action: Optional[str]) -> None:
    if ctx is not None and action:
        with ctx.lock:
            ctx.recorded_actions.add(action)


def _context_for(event: Any) -> Optional[AuditContext]:
    """The current audit context when it was opened for this event, else None."""
    ctx = _audit_context.get()
    if ctx is not None and ctx.event is event:
        return ctx
    return None


def _event_time() -> str:
    """The current UTC time as ISO 8601 with milliseconds and Z."""
    now = utc_now()
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000)


def _authorizer_claims(event: Dict[str, Any]) -> Dict[str, Any]:
    """The claims the authorizer passed in the request context (any authorizer shape), or {}."""
    request_context = event.get("requestContext") if isinstance(event, dict) else None
    authorizer = request_context.get("authorizer") if isinstance(request_context, dict) else None
    if not isinstance(authorizer, dict):
        return {}
    jwt = authorizer.get("jwt")
    if isinstance(jwt, dict) and isinstance(jwt.get("claims"), dict):
        return jwt["claims"]
    if isinstance(authorizer.get("lambda"), dict):
        return authorizer["lambda"]
    return authorizer


def _user_context_from_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    tokens = claims.get("tokens") or []
    return {
        "user": tokens[0] if tokens else "UNKNOWN",
        "roles": claims.get("roles", []),
        "mfaEnabled": claims.get("mfaEnabled", False),
    }


# The key a handler sets on the event it forwards to another VAMS Lambda function with its caller's
# authorizer context (an execute reading metadata, an ingest creating the asset). The forwarded call is
# recorded as a cross-call on behalf of the caller, with the caller's request id, rather than as the
# caller's own API action. It names who the work is attributed to only; authorization reads the
# forwarded authorizer context as before. A Lambda invoke payload is set by its invoker, not by an API
# client, so the key cannot arrive from the API.
AUDIT_INTERNAL_CALL_KEY = "vamsAuditInternalCall"


def internal_call_marker(event: Any = None) -> Dict[str, Any]:
    """The marker a handler puts under AUDIT_INTERNAL_CALL_KEY on a call it forwards for this request
    (the request ``event`` was opened for, or the current request when none is given)."""
    marker: Dict[str, Any] = {}
    ctx = _context_for(event) if event is not None else _audit_context.get()
    if ctx is not None:
        if ctx.request.get("requestId"):
            marker["requestId"] = ctx.request["requestId"]
        if ctx.actor.get("userId"):
            marker["onBehalfOf"] = ctx.actor["userId"]
    return marker


def _resolve_actor(event: Dict[str, Any], claims: Dict[str, Any],
                   on_behalf_of: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Resolve who a request runs as: the prefix user context and the record's actor object.

    The actor's userId is the effective identity, the same value the prefix [user: ...] carries.
    authMethod is apiKey for an API-key caller (with apiKeyId), cognito or externalIdp for a token
    (from the issuer the authorizer verified; jwt when the context names no issuer), and system for a
    Lambda cross-call or an event that did not come through the API. A cross-call carries
    crossCall true and, when it ran for a user, onBehalfOf.
    """
    user_context = _user_context_from_claims(claims)
    is_event = isinstance(event, dict)
    cross_call = event.get("lambdaCrossCall") if is_event else None
    request_context = event.get("requestContext") if is_event else None
    has_authorizer = isinstance(request_context, dict) and "authorizer" in request_context

    actor: Dict[str, Any] = {}
    if isinstance(cross_call, dict):
        actor["authMethod"] = AUTH_METHOD_SYSTEM
        actor["crossCall"] = True
        caller = cross_call.get("onBehalfOf") or cross_call.get("userName")
        if caller and caller != SYSTEM_USER:
            actor["onBehalfOf"] = str(caller)
    elif not has_authorizer:
        actor["authMethod"] = AUTH_METHOD_SYSTEM
        if user_context["user"] == "UNKNOWN":
            user_context["user"] = SYSTEM_USER
    elif claims.get("authMethod") == AUTH_METHOD_API_KEY:
        actor["authMethod"] = AUTH_METHOD_API_KEY_RECORD
        if claims.get("apiKeyId"):
            actor["apiKeyId"] = str(claims["apiKeyId"])
    else:
        issuer = str(_authorizer_claims(event).get("iss") or "")
        if not issuer:
            actor["authMethod"] = AUTH_METHOD_JWT_UNKNOWN_ISSUER
        elif "cognito-idp." in issuer:
            actor["authMethod"] = AUTH_METHOD_COGNITO
        else:
            actor["authMethod"] = AUTH_METHOD_EXTERNAL_IDP

    internal = event.get(AUDIT_INTERNAL_CALL_KEY) if is_event else None
    if isinstance(internal, dict):
        actor["crossCall"] = True
        caller = internal.get("onBehalfOf") or user_context["user"]
        if caller and caller not in (SYSTEM_USER, "UNKNOWN"):
            actor["onBehalfOf"] = str(caller)
    if on_behalf_of:
        actor["onBehalfOf"] = str(on_behalf_of)
    actor = {"userId": user_context["user"], **actor}
    return user_context, actor


def _request_facts(event: Dict[str, Any], lambda_context: Any) -> Dict[str, Any]:
    """
    The record's request object: requestId, method, route (the template, never the concrete path),
    sourceIp and lambdaRequestId. A key that does not apply is left out.

    sourceIp is the viewer address for a request that came through the deployment's CloudFront, as
    the authorizer resolved it (common/auth/viewerAddress.py: the forwarding headers are read only
    when the request carries the origin verification value the authorizer accepted), and otherwise
    the address API Gateway reports for the connection.
    """
    request: Dict[str, Any] = {}
    if isinstance(event, dict):
        request_context = event.get("requestContext") if isinstance(event.get("requestContext"), dict) else {}
        http = request_context.get("http") if isinstance(request_context.get("http"), dict) else {}
        identity = request_context.get("identity") if isinstance(request_context.get("identity"), dict) else {}
        method = http.get("method") or event.get("httpMethod")
        path = http.get("path") or event.get("path") or event.get("rawPath")
        template_hint = event.get("resource")
        route_key = request_context.get("routeKey")
        if not template_hint and isinstance(route_key, str) and " " in route_key:
            template_hint = route_key.split(" ", 1)[1]
        internal = event.get(AUDIT_INTERNAL_CALL_KEY)
        request["requestId"] = request_context.get("requestId") or (
            internal.get("requestId") if isinstance(internal, dict) else None)
        request["method"] = method.upper() if isinstance(method, str) else None
        if path:
            request["route"] = resolve_route_template(path, template_hint) or "unmatched"
        request["sourceIp"] = viewer_ip_for_audit(event) or http.get("sourceIp") or identity.get("sourceIp")
    aws_request_id = getattr(lambda_context, "aws_request_id", None)
    if isinstance(aws_request_id, str):
        request["lambdaRequestId"] = aws_request_id
    return {key: value for key, value in request.items() if value is not None}


def _claims_for(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        claims = request_to_claims(event)
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def _new_audit_context(event: Dict[str, Any], lambda_context: Any,
                       on_behalf_of: Optional[str]) -> AuditContext:
    # request_to_claims also normalizes a REST event into the shape the request facts read.
    claims = _claims_for(event)
    user_context, actor = _resolve_actor(event, claims, on_behalf_of)
    request = _request_facts(event, lambda_context)
    route_spec = audit_spec_for_route(request.get("route"), request.get("method"))
    return AuditContext(event, lambda_context, user_context, actor, request, route_spec)


def _start_pre_timeout_timer(ctx: AuditContext) -> None:
    """Flush the buffer shortly before the function times out, so a timing-out invocation delivers."""
    remaining = getattr(ctx.lambda_context, "get_remaining_time_in_millis", None)
    if not callable(remaining):
        return
    try:
        delay_ms = int(remaining()) - AUDIT_PRE_TIMEOUT_MARGIN_MS
    except Exception:
        return
    if delay_ms <= 0:
        # Too close to the timeout to hold anything back.
        ctx.write_through = True
        return
    timer = threading.Timer(delay_ms / 1000.0, _pre_timeout_flush, args=(ctx,))
    timer.daemon = True
    ctx.timer = timer
    timer.start()


def _pre_timeout_flush(ctx: AuditContext) -> None:
    with ctx.lock:
        ctx.write_through = True
    flush_audit_context(ctx)


@contextlib.contextmanager
def audit_request(event: Dict[str, Any], lambda_context: Any = None, *,
                  on_behalf_of: Optional[str] = None) -> Iterator[Optional[AuditContext]]:
    """
    Open the audit context of one request. Every Lambda entrypoint enters it around its work::

        def lambda_handler(event, context):
            with audit_request(event, context):
                ...

    Inside it the actor (user, roles, MFA, auth method, API key id, cross-call and on-behalf-of) and
    the request facts (request id, method, route template, source IP, Lambda request id) are
    resolved once, and every audit function reuses them instead of resolving claims per record.
    Successful action records and allowed authorization records are buffered and written when the
    block ends, one parallel write per audit log group, bounded by AUDIT_FLUSH_DEADLINE_SECONDS;
    denials, failures and errors are written at once. A record the flush cannot confirm in time is
    reported as undelivered.

    A nested block joins the outer one. Outside a context every audit function writes as it is
    called, as before. Records written from another thread (a worker pool) are written at once,
    since a context belongs to the thread that opened it. Never raises from its own work.

    Args:
        event: The Lambda event the handler received
        lambda_context: The Lambda context (for the request id and the pre-timeout flush)
        on_behalf_of: The user a system invocation runs for, when the handler knows it
    """
    if _audit_context.get() is not None:
        yield _audit_context.get()
        return
    ctx: Optional[AuditContext] = None
    try:
        ctx = _new_audit_context(event, lambda_context, on_behalf_of)
    except Exception as e:
        logger.exception(f"Failed to open the request audit context: {e}")
    if ctx is None:
        yield None
        return
    token = _audit_context.set(ctx)
    try:
        _start_pre_timeout_timer(ctx)
    except Exception as e:
        logger.exception(f"Failed to start the audit pre-timeout flush: {e}")
    try:
        yield ctx
    finally:
        _audit_context.reset(token)
        try:
            if ctx.timer is not None:
                ctx.timer.cancel()
            with ctx.lock:
                ctx.write_through = True
            flush_audit_context(ctx)
        except Exception as e:
            logger.exception(f"Failed to flush the request audit records: {e}")


def audited_handler(handler: Callable) -> Callable:
    """
    Decorate a Lambda entrypoint ``handler(event, context)`` to run inside :func:`audit_request`.

    When the request's route registers an action and the handler recorded none for it, an error
    response (or an exception) is recorded as that action with ``outcome="failed"`` and a generic
    ``reason`` from the status code (``validation``, ``notFound``, ``conflict``, ``internal`` ...),
    in the action's own group, with the path's id fields. A 401 or 403 is not: the authorization
    record is the denial's record.
    """

    @functools.wraps(handler)
    def wrapper(event, context=None, *args, **kwargs):
        outermost = _audit_context.get() is None
        with audit_request(event, context) as ctx:
            try:
                response = handler(event, context, *args, **kwargs)
            except Exception:
                if outermost:
                    _record_route_failure(ctx, event, 500)
                raise
            if outermost:
                status = _response_status(response)
                if status is not None and status >= 400:
                    _record_route_failure(ctx, event, status)
            return response

    return wrapper


# The generic reason a failed record names for an error status; any other 4xx is clientError and
# every 5xx is internal.
_FAILURE_REASONS = {400: "validation", 404: "notFound", 405: "validation", 409: "conflict",
                    410: "notFound", 412: "conflict", 413: "tooLarge", 415: "validation",
                    422: "validation", 429: "throttled"}
_DENIAL_STATUSES = frozenset({401, 403})
# The groups whose records name their [type: ...] value as legacyType.
_AUTH_RECORD_GROUPS = frozenset({GROUP_AUTH_CHANGES, GROUP_AUTH_OTHER})


def audit_failure_reason(status: int) -> str:
    """The generic reason category of an error status code."""
    if status >= 500:
        return "internal"
    return _FAILURE_REASONS.get(status, "clientError")


def _response_status(response: Any) -> Optional[int]:
    status = response.get("statusCode") if isinstance(response, dict) else getattr(response, "statusCode", None)
    if isinstance(status, bool):
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _path_ids(event: Any) -> Dict[str, Any]:
    """The path parameters that are record id fields (databaseId, assetId, workflowId, ...)."""
    path_parameters = event.get("pathParameters") if isinstance(event, dict) else None
    if not isinstance(path_parameters, dict):
        return {}
    return {key: value for key, value in path_parameters.items()
            if key in AUDIT_ID_FIELDS and isinstance(value, str) and value}


def _record_route_failure(ctx: Optional[AuditContext], event: Any, status: int) -> None:
    """Record the route's action as failed when the handler recorded nothing for it. Never raises."""
    try:
        if ctx is None or ctx.route_spec is None or status in _DENIAL_STATUSES:
            return
        action = ctx.route_spec.action
        if action in ctx.recorded_actions:
            return
        # Written with the request's flush, which follows at once: the request has ended, so holding
        # the record loses nothing and saves the request a write of its own.
        log_action(ctx.event, action, outcome=OUTCOME_FAILED, reason=audit_failure_reason(status),
                   flush_with_request=True, **_path_ids(ctx.event))
    except Exception as e:
        logger.exception(f"Failed to record the request's failed action: {e}")


def _flush_deadline_seconds(ctx: Optional[AuditContext]) -> float:
    """
    How long a flush of this context may wait: AUDIT_FLUSH_DEADLINE_SECONDS, cut to the function's
    remaining time less AUDIT_FALLBACK_RESERVE_MS, and never below zero. At zero the writes are still
    started but every record is reported as undelivered at once, while there is time to print it.
    """
    remaining = getattr(getattr(ctx, "lambda_context", None), "get_remaining_time_in_millis", None)
    if not callable(remaining):
        return AUDIT_FLUSH_DEADLINE_SECONDS
    try:
        left_seconds = (int(remaining()) - AUDIT_FALLBACK_RESERVE_MS) / 1000.0
    except Exception:
        return AUDIT_FLUSH_DEADLINE_SECONDS
    return max(0.0, min(AUDIT_FLUSH_DEADLINE_SECONDS, left_seconds))


def _deliver_records(records_by_type: Dict[str, List[str]], client: Any,
                     deadline_seconds: float = AUDIT_FLUSH_DEADLINE_SECONDS) -> None:
    """
    Write records to their audit log groups: one write per group, the groups in parallel, the whole
    bounded by ``deadline_seconds`` (see _flush_deadline_seconds). A group whose write is not
    confirmed by the deadline is reported as undelivered (its write may still land, so delivery is
    at least once; recordId identifies a duplicate). Never raises.
    """
    work = []
    for log_type, records in records_by_type.items():
        if not records:
            continue
        key = _AUDIT_LOG_KEYS_BY_TYPE.get(log_type)
        name = _audit_log_group(key) if key is not None else None
        if not name:
            if key is not None:
                logger.error(f"{key.env_var_names[0]} resource name not resolved")
            report_undelivered_audit_records(log_type, "logGroupUnresolved", records)
            continue
        work.append((log_type, name, records))
    if not work:
        return
    try:
        executor = _get_flush_executor()
        futures = {executor.submit(_put_audit_records, name, records, client): (log_type, records)
                   for log_type, name, records in work}
    except Exception as e:
        logger.exception(f"Failed to start the audit flush: {e}")
        for log_type, _, records in work:
            report_undelivered_audit_records(log_type, "unexpectedError", records)
        return
    done, not_done = wait_for_futures(futures, timeout=deadline_seconds)
    for future in not_done:
        log_type, records = futures[future]
        logger.error(f"Audit flush deadline passed with {len(records)} {log_type} records unconfirmed")
        report_undelivered_audit_records(log_type, "flushDeadlineExceeded", records)
    for future in done:
        error = future.exception()
        if error is not None:
            log_type, records = futures[future]
            report_undelivered_audit_records(log_type, "unexpectedError", records)


def flush_audit_context(ctx: AuditContext) -> None:
    """Write and clear the records the context has buffered. Never raises."""
    try:
        with ctx.lock:
            pending, ctx.pending = ctx.pending, {}
        if pending:
            _deliver_records(pending, audit_flush_client, _flush_deadline_seconds(ctx))
    except Exception as e:
        logger.exception(f"Failed to flush the request audit records: {e}")


def _enqueue_records(ctx: Optional[AuditContext], log_type: str, records: List[str], buffer: bool) -> None:
    """
    Hand complete records to delivery: buffer them in the context, or write them now (with the
    bounded flush client inside a context, the module client outside one).
    """
    if ctx is not None and buffer:
        with ctx.lock:
            if not ctx.write_through:
                ctx.pending.setdefault(log_type, []).extend(records)
                return
    client = audit_flush_client if ctx is not None else cloudwatch_logs
    if ctx is not None:
        _deliver_records({log_type: records}, client, _flush_deadline_seconds(ctx))
        return
    key = _AUDIT_LOG_KEYS_BY_TYPE.get(log_type)
    name = _audit_log_group(key) if key is not None else None
    if not name:
        if key is not None:
            logger.error(f"{key.env_var_names[0]} resource name not resolved")
        report_undelivered_audit_records(log_type, "logGroupUnresolved", records)
        return
    _put_audit_records(name, records, client)


# --- Value caps -------------------------------------------------------------------------------------

def audit_value_sha256(value: Any) -> str:
    """
    The SHA-256 a cut value carries, as hex: of the UTF-8 bytes of a string value, and of the
    canonical JSON text (sorted keys, no spaces, ASCII) of any other value.
    """
    if isinstance(value, str):
        data = value.encode("utf-8")
    else:
        data = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str,
                          separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _value_bytes(value: Any) -> int:
    """The size of a value: its UTF-8 bytes for a string, its JSON text otherwise."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(_serialize_json(value))


def _cut_value(value: Any, with_preview: bool = True) -> Dict[str, Any]:
    stub: Dict[str, Any] = {"truncated": True, "sha256": audit_value_sha256(value), "bytes": _value_bytes(value)}
    if with_preview:
        text = value if isinstance(value, str) else _serialize_json(value)
        stub["preview"] = text[:AUDIT_VALUE_PREVIEW_CHARS]
        # A preview of non-ASCII or escape-heavy text can still serialize past the cap; shorten it.
        while len(_serialize_json(stub)) > AUDIT_VALUE_MAX_BYTES and stub["preview"]:
            stub["preview"] = stub["preview"][:len(stub["preview"]) // 2]
    return stub


def _cap_value(value: Any) -> Tuple[Any, bool]:
    """The value, or its cut form when its JSON text passes AUDIT_VALUE_MAX_BYTES."""
    if len(_serialize_json(value)) <= AUDIT_VALUE_MAX_BYTES:
        return value, False
    return _cut_value(value), True


def _cap_record(payload: Dict[str, Any], originals: Dict[str, Dict[str, Any]],
                limit: int = AUDIT_RECORD_MAX_BYTES) -> bool:
    """
    Bound a payload to ``limit`` bytes of JSON text, in place. First the largest values of the value
    sections lose their preview (keeping the hash), then whole sections are replaced by their hash,
    then the sections and the extra ids are dropped. Returns True when anything was cut.
    """
    if len(_serialize_json(payload)) <= limit:
        return False
    # The running size is adjusted by each replacement's difference rather than re-serializing the
    # whole payload per field, so a record with thousands of fields stays cheap to bound.
    size = len(_serialize_json(payload))
    candidates = []
    for section in _VALUE_SECTIONS:
        values = payload.get(section)
        if isinstance(values, dict) and section in originals:
            for key, value in values.items():
                candidates.append((len(_serialize_json(value)), section, key))
    for value_size, section, key in sorted(candidates, reverse=True):
        stub = _cut_value(originals[section][key], with_preview=False)
        stub_size = len(_serialize_json(stub))
        if stub_size >= value_size:
            # Every remaining value is already smaller than its cut form would be.
            break
        payload[section][key] = stub
        size += stub_size - value_size
        if size <= limit and len(_serialize_json(payload)) <= limit:
            return True
    sections = sorted((len(_serialize_json(payload[section])), section)
                      for section in _VALUE_SECTIONS if section in originals)
    for _, section in reversed(sections):
        payload[section] = {"truncated": True, "sha256": audit_value_sha256(originals[section]),
                            "fieldCount": len(originals[section])}
        if len(_serialize_json(payload)) <= limit:
            return True
    for key in list(payload):
        if key in _VALUE_SECTIONS or key in AUDIT_ID_FIELDS:
            payload.pop(key, None)
    return True


# --- Record building --------------------------------------------------------------------------------

def _non_negative_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def build_audit_payload(
    action: str,
    *,
    actor: Dict[str, Any],
    request: Dict[str, Any],
    outcome: str = OUTCOME_SUCCESS,
    reason: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    ids: Optional[Dict[str, Any]] = None,
    bytes_count: Optional[int] = None,
    file_count: Optional[int] = None,
    item_count: Optional[int] = None,
    duration_ms: Optional[int] = None,
    changes: Optional[Dict[str, Any]] = None,
    previous: Optional[Dict[str, Any]] = None,
    request_parameters: Optional[Dict[str, Any]] = None,
    details: Optional[Dict[str, Any]] = None,
    max_bytes: int = AUDIT_RECORD_MAX_BYTES,
) -> Dict[str, Any]:
    """
    Build a version 1 record payload, its JSON text bounded to ``max_bytes``. Keys that do not
    apply are left out, never written as null.

    ``changes`` holds each field a write set with its new value, ``previous`` the prior values the
    handler already held, ``requestParameters`` what a read, search or download asked for (filters,
    page size, ids); never what was returned. All four value sections and ``details`` pass through
    the log redaction (``mask_sensitive_data``) and the size caps.
    """
    outcome = outcome if outcome in AUDIT_OUTCOMES else OUTCOME_FAILED
    payload: Dict[str, Any] = {
        "auditVersion": AUDIT_RECORD_VERSION,
        "recordId": str(uuid.uuid4()),
        "action": action,
        "outcome": outcome,
    }
    if outcome != OUTCOME_SUCCESS and reason:
        reason_text = str(reason)
        payload["reason"] = reason_text if AUDIT_ACTION_NAME_PATTERN.match(reason_text) else "other"
    payload["eventTime"] = _event_time()
    payload["actor"] = dict(actor)
    payload["request"] = dict(request)

    truncated = False
    id_values: Dict[str, Any] = {}
    for key, value in (ids or {}).items():
        if value is None or key not in AUDIT_ID_FIELDS:
            continue
        capped, cut = _cap_value(value if isinstance(value, str) else str(value))
        truncated = truncated or cut
        id_values[key] = capped

    if entity_type:
        entity: Dict[str, Any] = {"type": entity_type}
        if entity_id is None:
            entity_id = (ids or {}).get(_ENTITY_ID_FIELDS.get(entity_type, ""))
        if entity_id is not None:
            entity["id"], cut = _cap_value(entity_id if isinstance(entity_id, str) else str(entity_id))
            truncated = truncated or cut
        payload["entity"] = entity
    payload.update(id_values)

    for key, value in (("bytes", bytes_count), ("fileCount", file_count), ("itemCount", item_count),
                       ("durationMs", duration_ms)):
        number = _non_negative_int(value)
        if number is not None:
            payload[key] = number

    originals: Dict[str, Dict[str, Any]] = {}
    for section, values in (("changes", changes), ("previous", previous),
                            ("requestParameters", request_parameters), ("details", details)):
        if not isinstance(values, dict) or not values:
            continue
        masked = mask_sensitive_data(values)
        if not isinstance(masked, dict):
            payload[section] = {"redacted": True}
            continue
        masked = {str(key): value for key, value in masked.items() if value is not None}
        if not masked:
            continue
        originals[section] = masked
        capped_section = {}
        for key, value in masked.items():
            capped_section[key], cut = _cap_value(value)
            truncated = truncated or cut
        payload[section] = capped_section

    if _cap_record(payload, originals, max_bytes):
        truncated = True
    if truncated:
        payload["truncated"] = True
    return payload


def _compact_echo(event: Any) -> str:
    """
    The event echo of an action record: the request id, method, concrete path and route key only --
    no headers, query string or body (what a write changed is in the payload's own sections).
    """
    if not isinstance(event, dict):
        return ""
    request_context = event.get("requestContext") if isinstance(event.get("requestContext"), dict) else {}
    http = request_context.get("http") if isinstance(request_context.get("http"), dict) else {}
    echo_http = {key: http.get(key) for key in ("method", "path") if http.get(key) is not None}
    echo_context: Dict[str, Any] = {}
    if request_context.get("requestId") is not None:
        echo_context["requestId"] = request_context.get("requestId")
    if echo_http:
        echo_context["http"] = echo_http
    if request_context.get("routeKey") is not None:
        echo_context["routeKey"] = request_context.get("routeKey")
    if not echo_context:
        return ""
    return f" --- [event: {_serialize_json({'requestContext': echo_context})}]"


# The echo an action record carries when its compact echo would take the record past
# AUDIT_RECORD_MAX_BYTES.
AUDIT_ECHO_TRUNCATED = ' --- [event: {"truncated": true}]'


def _bounded_action_record(head: str, build_payload: Callable[[int], Dict[str, Any]], echo: str) -> str:
    """
    Assemble an action record (prefix segments, payload, echo) bounded to AUDIT_RECORD_MAX_BYTES as
    a whole. The payload is built to fit beside the prefix; if the echo then takes the record over,
    the echo is replaced by AUDIT_ECHO_TRUNCATED, and if it is still over, the payload's values are
    cut further. A prefix too long to leave room (a caller with hundreds of long role names) cuts
    the record itself, ending in AUDIT_EVENT_TRUNCATION_MARKER as a partial record.
    """
    head_bytes = _message_byte_length(head) + 1

    def assemble(payload: Dict[str, Any], suffix: str) -> str:
        return head + " " + _serialize_json(payload) + suffix

    payload = build_payload(AUDIT_RECORD_MAX_BYTES - head_bytes)
    record = assemble(payload, echo)
    if _message_byte_length(record) <= AUDIT_RECORD_MAX_BYTES:
        return record
    if echo:
        payload["truncated"] = True
        echo = AUDIT_ECHO_TRUNCATED
        record = assemble(payload, echo)
        if _message_byte_length(record) <= AUDIT_RECORD_MAX_BYTES:
            return record
    payload = build_payload(AUDIT_RECORD_MAX_BYTES - head_bytes - _message_byte_length(echo))
    payload["truncated"] = True
    record = assemble(payload, echo)
    if _message_byte_length(record) <= AUDIT_RECORD_MAX_BYTES:
        return record
    marker = AUDIT_EVENT_TRUNCATION_MARKER
    keep = AUDIT_RECORD_MAX_BYTES - _message_byte_length(marker)
    return record.encode("utf-8")[:keep].decode("utf-8", errors="ignore") + marker


def _action_prefix(group: str, prefix_type: str, outcome: str) -> str:
    if outcome == OUTCOME_DENIED:
        return _event_type(("AUTHORIZATION", None), ("authorized", False))
    label = _GROUP_LABELS.get(group, "ACTIONS")
    if group in _GROUPS_WITHOUT_TYPE_SEGMENT:
        return _event_type((label, None))
    return _event_type((label, None), ("type", prefix_type))


def log_action(
    event: Dict[str, Any],
    action: Optional[str] = None,
    *,
    outcome: str = OUTCOME_SUCCESS,
    reason: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    database_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    file_path: Optional[str] = None,
    bytes: Optional[int] = None,  # noqa: A002 -- the record key it fills is named bytes
    file_count: Optional[int] = None,
    item_count: Optional[int] = None,
    duration_ms: Optional[int] = None,
    changes: Optional[Dict[str, Any]] = None,
    previous: Optional[Dict[str, Any]] = None,
    request_parameters: Optional[Dict[str, Any]] = None,
    details: Optional[Dict[str, Any]] = None,
    on_behalf_of: Optional[str] = None,
    flush_with_request: bool = False,
    **ids: Any,
) -> None:
    """
    Record one action in the record contract version 1, with silent failure.

    Call it after the write succeeded (``outcome="success"``), when an authorized attempt failed
    (``outcome="failed"`` with a generic ``reason`` such as ``notFound``, ``validation``,
    ``conflict`` or ``internal``), or when a data-level check denied it (``outcome="denied"``).
    ``action`` is a registered action name (``common/auditActions.py``); None takes the action
    registered for the request's route. The action's group, ``[type: ...]`` value and entity type
    come from the registry; a denial is written to the authorization group.

    A write records what it wrote or changed: ``changes`` maps each field set to its new value and
    ``previous`` the prior values the handler already loaded (never read a record only to fill
    it). A read, search or download records what was asked (``request_parameters``), never what was
    returned. Redaction applies to every value section, a value over 2 KB is cut to its hash and a
    preview, and a record is bounded to 32 KB. Never pass file content.

    Inside :func:`audit_request` a successful record is buffered and written when the request ends;
    otherwise, and for denials and failures, it is written at once.

    Args:
        event: The Lambda event of the request
        action: The registered action name, or None for the route's action
        outcome: success, failed or denied
        reason: Generic reason category on failed or denied, never error text
        entity_type: Overrides the action's registered entity type
        entity_id: The entity's id; defaults to the id field that names the entity type
        database_id, asset_id, file_path: The ids of the asset data the action touched
        bytes, file_count, item_count, duration_ms: Sizes and counts the handler already knows
        changes, previous, request_parameters, details: The record's value sections
        on_behalf_of: The user a system action ran for (an execution's initiator, a queued
            upload's uploader), recorded as actor.onBehalfOf on this record
        flush_with_request: Inside a context, hold a failed record for the request's flush instead of
            writing it at once (the outcome record audited_handler writes as the request ends)
        **ids: Further id fields by their record key (``AUDIT_ID_FIELDS``, e.g. ``versionId``);
            any other keyword is recorded in details
    """
    try:
        ctx = _context_for(event)
        audit_spec = audit_spec_for_action(action) if action else (ctx.route_spec if ctx else None)
        if audit_spec is None and not action:
            logger.error("log_action called without an action outside a registered route")
            return
        action_name = action or audit_spec.action
        _note_recorded(ctx, action_name)
        if audit_spec is None:
            logger.error(f"Audit action {action_name!r} is not registered in common/auditActions.py")
        group = audit_spec.group if audit_spec else GROUP_ACTIONS
        prefix_type = (audit_spec.legacy_type or audit_spec.action) if audit_spec else action_name
        kind = audit_spec.kind if audit_spec else None
        if kind in _READ_SWITCH_KINDS and outcome == OUTCOME_SUCCESS and not audit_records_reads():
            # The deployment turned read records off; a denied or failed read is still recorded.
            return
        if kind in _REQUEST_ONLY_KINDS:
            # A read, search or download records what was asked, never what was written or returned.
            passed = [name for name, value in (("changes", changes), ("previous", previous)) if value]
            if passed:
                logger.error(f"Audit action {action_name!r} is a {kind} action, dropped {', '.join(passed)}")
            changes = previous = None
            if kind != KIND_DOWNLOAD and not AUDIT_READ_ITEM_COUNT:
                item_count = None

        if ctx is not None:
            user_context, actor, request = dict(ctx.user_context), ctx.actor, ctx.request
        else:
            user_context, actor = _resolve_actor(event, _claims_for(event))
            request = _request_facts(event, None)
        if on_behalf_of:
            actor = {**actor, "onBehalfOf": str(on_behalf_of)}

        all_ids = {key: value for key, value in ids.items() if key in AUDIT_ID_FIELDS}
        extra = {key: value for key, value in ids.items() if key not in AUDIT_ID_FIELDS}
        if extra:
            details = {**(details or {}), **extra}
        for key, value in (("databaseId", database_id), ("assetId", asset_id), ("filePath", file_path)):
            if value is not None:
                all_ids[key] = value

        outcome_written = outcome if outcome in AUDIT_OUTCOMES else OUTCOME_FAILED
        target_group = GROUP_AUTHORIZATION if outcome_written == OUTCOME_DENIED else group
        head = _format_log_message(_action_prefix(group, prefix_type, outcome_written), user_context,
                                   None, target_group)

        def build_payload(max_bytes: int) -> Dict[str, Any]:
            return build_audit_payload(
                action_name, actor=actor, request=request, outcome=outcome_written, reason=reason,
                entity_type=entity_type or (audit_spec.entity_type if audit_spec else None),
                entity_id=entity_id, ids=all_ids, bytes_count=bytes, file_count=file_count,
                item_count=item_count, duration_ms=duration_ms, changes=changes, previous=previous,
                request_parameters=request_parameters, details=details, max_bytes=max_bytes)

        if group in _AUTH_RECORD_GROUPS:
            build_contract_payload = build_payload

            def build_payload(max_bytes: int) -> Dict[str, Any]:
                # An auth change or auth other record names its [type: ...] value as legacyType, as
                # the records log_auth_changes and log_auth_other write do.
                payload = build_contract_payload(max_bytes)
                payload["legacyType"] = prefix_type
                return payload

        record = _bounded_action_record(head, build_payload, _compact_echo(event))
        hold = outcome_written == OUTCOME_SUCCESS or (flush_with_request and outcome_written == OUTCOME_FAILED)
        _enqueue_records(ctx, target_group, [record], buffer=hold)
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log action audit event: {e}")
        report_undelivered_audit_records(GROUP_ACTIONS, "unexpectedError", [None])


def _legacy_canonical_keys(ctx: AuditContext, action: Optional[str], outcome: str,
                           entity_type: Optional[str]) -> Dict[str, Any]:
    """The contract keys a legacy record gains inside an audit context."""
    keys: Dict[str, Any] = {
        "auditVersion": AUDIT_RECORD_VERSION,
        "recordId": str(uuid.uuid4()),
    }
    if action:
        keys["action"] = action
    keys["outcome"] = outcome
    keys["eventTime"] = _event_time()
    keys["actor"] = dict(ctx.actor)
    keys["request"] = dict(ctx.request)
    if entity_type:
        keys["entity"] = {"type": entity_type}
    return keys


def _with_canonical_keys(custom_data: Any, canonical: Dict[str, Any]) -> Dict[str, Any]:
    """
    A legacy payload with the contract keys added. The contract keys win: a legacy key of the same
    name with a different value (an AMI recipe build's ``outcome`` of ``in-progress``, a payload's
    own ``action``) is moved under ``legacy`` (``{"legacy": {"outcome": "in-progress"}}``), so every
    contract key keeps its contract type and values and the legacy value is still recorded. A legacy
    payload that already has a ``legacy`` key keeps it inside that object as ``legacy.legacy``.
    """
    if isinstance(custom_data, dict):
        merged = dict(custom_data)
        moved: Dict[str, Any] = {}
        for key, value in canonical.items():
            if key in merged and merged[key] != value:
                moved[key] = merged[key]
            merged[key] = value
        if moved:
            if AUDIT_LEGACY_KEY in merged:
                moved[AUDIT_LEGACY_KEY] = merged[AUDIT_LEGACY_KEY]
            merged[AUDIT_LEGACY_KEY] = moved
        return merged
    merged = dict(canonical)
    if custom_data is not None:
        merged["customData"] = custom_data
    return merged


def _legacy_route_action(ctx: AuditContext, secondary_type: Optional[str] = None,
                         legacy_function: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    The canonical action and entity type a legacy record inside a context is for: the route's action
    when the record is the one the route registers (its legacy type, its name, or its legacy
    function), else the secondary type's own registered action.
    """
    route_spec = ctx.route_spec
    if route_spec is not None:
        if secondary_type is None and legacy_function is None:
            # An error record is for the action the route registers.
            return route_spec.action, route_spec.entity_type
        if secondary_type is not None and secondary_type in (route_spec.legacy_type, route_spec.action):
            return route_spec.action, route_spec.entity_type
        if legacy_function is not None and route_spec.legacy_function == legacy_function:
            return route_spec.action, route_spec.entity_type
    if secondary_type is not None:
        registered = audit_spec_for_action(secondary_type)
        return secondary_type, registered.entity_type if registered else None
    return None, None


def _legacy_canonical(event: Any, *, secondary_type: Optional[str] = None,
                      legacy_function: Optional[str] = None, default_action: Optional[str] = None,
                      default_entity_type: Optional[str] = None,
                      outcome: str = OUTCOME_SUCCESS) -> Optional[Dict[str, Any]]:
    """The canonical action, outcome and entity type of a legacy record, or None outside a context."""
    ctx = _context_for(event)
    if ctx is None:
        return None
    action, entity_type = _legacy_route_action(ctx, secondary_type, legacy_function)
    return {"action": action or default_action, "outcome": outcome,
            "entityType": entity_type or default_entity_type}
