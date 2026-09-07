# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Audit Logging Module for VAMS

This module provides functions to log audit events to CloudWatch Log Groups.
All functions implement silent failure - if logging fails, the error is logged
locally but the lambda execution continues without disruption.
"""

import json
import boto3
from botocore.config import Config
from datetime import datetime
from typing import Dict, Any, Optional, List
from handlers.auth import request_to_claims
from customLogging.logger import mask_sensitive_data, safeLogger
from common.resourceNames import ResourceKeys, get_log_group_name

# Initialize logger for audit logging module
logger = safeLogger(service_name="AuditLogging")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

# Initialize CloudWatch Logs client
try:
    cloudwatch_logs = boto3.client('logs', config=retry_config)
except Exception as e:
    logger.exception(f"Failed to initialize CloudWatch Logs client: {e}")
    cloudwatch_logs = None

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

# The log stream this container has created in each audit log group. The stream name is the current
# date, so an entry ages out at the day boundary and CreateLogStream -- an account-wide 50 TPS
# quota -- is called once per group per day instead of once per audit write. A failed write drops
# the entry, so a stream that went away is created again by the write after it.
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


def _resolve_audit_log_group(key) -> Optional[str]:
    """Resolve one audit log-group name, returning None instead of raising."""
    try:
        return get_log_group_name(key)
    except Exception as e:
        logger.error(f"Audit log group name not resolved for {key.param_key}: {e}")
        return None


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


def _extract_user_context(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract user context from the API event.
    
    Args:
        event: The API Gateway event
        
    Returns:
        Dictionary containing user, roles, and mfaEnabled information
    """
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


def _format_log_message(event_type: str, user_context: Dict[str, Any], custom_data: Any) -> str:
    """
    Format the audit log message with event type, user context, and custom data.
    
    Args:
        event_type: The type of event (e.g., "[AUTHENTICATION]")
        user_context: Dictionary with user, roles, and mfaEnabled
        custom_data: Additional data to include in the log
        
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
            f"[user: {user}]",
            f"[roles: {json.dumps(roles)}]",
            f"[mfaEnabled: {mfa_enabled}]"
        ]
        
        # Add custom data if provided
        if custom_data is not None:
            if isinstance(custom_data, dict):
                try:
                    message_parts.append(json.dumps(custom_data))
                except Exception as e:
                    message_parts.append(str(custom_data))
            else:
                message_parts.append(str(custom_data))
        
        return " ".join(message_parts)
    except Exception as e:
        logger.exception(f"Failed to format log message: {e}")
        return f"{event_type} [ERROR: Failed to format message]"


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
    try:
        if not cloudwatch_logs:
            logger.error("CloudWatch Logs client not initialized, cannot write audit log")
            return

        # Mask sensitive data from the event before logging
        if event:
            masked_event = mask_sensitive_data(event)
        else:
            masked_event = {}

        # Create log stream name based on current date
        log_stream_name = datetime.utcnow().strftime("%Y/%m/%d")

        # Ensure log stream exists (create if it doesn't), once per group per day per container
        if _created_log_streams.get(log_group_name) != log_stream_name:
            try:
                cloudwatch_logs.create_log_stream(
                    logGroupName=log_group_name,
                    logStreamName=log_stream_name
                )
            except cloudwatch_logs.exceptions.ResourceAlreadyExistsException:
                # Log stream already exists, which is fine
                pass
            except Exception as e:
                logger.exception(f"Failed to create log stream {log_stream_name} in {log_group_name}: {e}")
                return
            _created_log_streams[log_group_name] = log_stream_name

        #Add event at the end of each message.
        event_suffix = f" --- [event: {json.dumps(masked_event)}]" if masked_event else ""

        # What the entries after the first carry: the echo itself while replicating it stays inside
        # AUDIT_BATCH_ECHO_MAX_TOTAL_BYTES, a reference to the first entry's copy beyond that.
        following_suffix = event_suffix
        if (event_suffix and len(messages) > 1
                and _message_byte_length(event_suffix) * len(messages)
                > AUDIT_BATCH_ECHO_MAX_TOTAL_BYTES):
            following_suffix = AUDIT_EVENT_ECHO_REFERENCE

        timestamp = int(datetime.utcnow().timestamp() * 1000)
        prepared_events = []
        for index, message in enumerate(messages):
            suffix = event_suffix if index == 0 else following_suffix
            prepared_events.append(_prepare_log_event(timestamp, message + suffix))

        # Write to CloudWatch, one call per batch inside the PutLogEvents budgets. Each batch
        # is attempted on its own so a rejected batch does not take the ones after it with it.
        for batch in _chunk_log_events(prepared_events):
            try:
                cloudwatch_logs.put_log_events(
                    logGroupName=log_group_name,
                    logStreamName=log_stream_name,
                    logEvents=batch
                )
            except Exception as e:
                # The stream may have gone away under the entry recorded above, so the entry is
                # dropped and the write after this one creates the stream again.
                _created_log_streams.pop(log_group_name, None)
                logger.exception(
                    f"Failed to write {len(batch)} audit entries to CloudWatch log group "
                    f"{log_group_name}: {e}"
                )

    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to write audit log to CloudWatch log group {log_group_name}: {e}")


def log_authentication(event: Dict[str, Any], authenticated: bool, custom_data: Optional[Any] = None) -> None:
    """
    Log authentication events with silent failure.

    Args:
        event: The API Gateway event
        authenticated: Whether authentication was successful
        custom_data: Additional data to log (optional)
    """
    try:
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_AUTHENTICATION)
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHENTICATION resource name not resolved")
            return
        
        user_context = _extract_user_context(event)
        event_type = f"[AUTHENTICATION][authenticated: {authenticated}]"
        message = _format_log_message(event_type, user_context, custom_data)
        
        _write_to_cloudwatch(log_group_name, message, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log authentication audit event: {e}")


def log_authorization(claims_and_roles: Dict[str, Any], authorized: bool, custom_data: Optional[Any] = None) -> None:
    """
    Log authorization events with silent failure using claims_and_roles directly.

    Args:
        claims_and_roles: The claims and roles dictionary
        authorized: Whether authorization was successful
        custom_data: Additional data to log (optional)
    """
    try:
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_AUTHORIZATION)
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHORIZATION resource name not resolved")
            return
        
        # Extract user context from claims_and_roles
        user_context = {
            "user": claims_and_roles.get("tokens", ["UNKNOWN"])[0] if claims_and_roles.get("tokens") else "UNKNOWN",
            "roles": claims_and_roles.get("roles", []),
            "mfaEnabled": claims_and_roles.get("mfaEnabled", False)
        }
        
        event_type = f"[AUTHORIZATION][authorized: {authorized}]"
        message = _format_log_message(event_type, user_context, custom_data)
        
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
        
        _write_to_cloudwatch(log_group_name, message, mock_event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log authorization audit event: {e}")


def log_authorization_api(event: Dict[str, Any], authorized: bool, custom_data: Optional[Any] = None) -> None:
    """
    Log API authorization events with silent failure using full API Gateway event.

    Args:
        event: The API Gateway event
        authorized: Whether authorization was successful
        custom_data: Additional data to log (optional)
    """
    try:
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_AUTHORIZATION)
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHORIZATION resource name not resolved")
            return
        
        user_context = _extract_user_context(event)
        event_type = f"[AUTHORIZATION][authorized: {authorized}]"
        message = _format_log_message(event_type, user_context, custom_data)
        
        _write_to_cloudwatch(log_group_name, message, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log authorization audit event: {e}")


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
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_FILEUPLOAD)
        if not log_group_name:
            logger.error("AUDIT_LOG_FILEUPLOAD resource name not resolved")
            return
        
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
        
        message = _format_log_message(event_type, user_context, upload_data)
        
        _write_to_cloudwatch(log_group_name, message, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log file upload audit event: {e}")


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
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD)
        if not log_group_name:
            logger.error("AUDIT_LOG_FILEDOWNLOAD resource name not resolved")
            return
        
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
        
        message = _format_log_message(event_type, user_context, download_data)
        
        _write_to_cloudwatch(log_group_name, message, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log file download audit event: {e}")


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
    try:
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD)
        if not log_group_name:
            logger.error("AUDIT_LOG_FILEDOWNLOAD resource name not resolved")
            return

        user_context = _extract_user_context(event)
        event_type = "[FILEDOWNLOAD]"

        messages = []
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
            messages.append(_format_log_message(event_type, user_context, download_data))

        _write_batch_to_cloudwatch(log_group_name, messages, event)

    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log bulk file download audit event: {e}")


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
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD_STREAMED)
        if not log_group_name:
            logger.error("AUDIT_LOG_FILEDOWNLOAD_STREAMED resource name not resolved")
            return
        
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
        
        message = _format_log_message(event_type, user_context, download_data)
        
        _write_to_cloudwatch(log_group_name, message, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log streamed file download audit event: {e}")


def log_auth_other(event: Dict[str, Any], secondary_type: str, custom_data: Optional[Any] = None) -> None:
    """
    Log other authentication-related events with silent failure.

    Args:
        event: The API Gateway event
        secondary_type: The secondary type of auth event
        custom_data: Additional data to log (optional)
    """
    try:
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_AUTHOTHER)
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHOTHER resource name not resolved")
            return
        
        user_context = _extract_user_context(event)
        event_type = f"[AUTHOTHER][type: {secondary_type}]"
        message = _format_log_message(event_type, user_context, custom_data)
        
        _write_to_cloudwatch(log_group_name, message, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log auth other audit event: {e}")


def log_auth_changes(event: Dict[str, Any], secondary_type: str, custom_data: Optional[Any] = None) -> None:
    """
    Log authentication/authorization changes with silent failure.

    Args:
        event: The API Gateway event
        secondary_type: The secondary type of auth change
        custom_data: Additional data to log (optional)
    """
    try:
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_AUTHCHANGES)
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHCHANGES resource name not resolved")
            return
        
        user_context = _extract_user_context(event)
        event_type = f"[AUTHCHANGES][type: {secondary_type}]"
        message = _format_log_message(event_type, user_context, custom_data)
        
        _write_to_cloudwatch(log_group_name, message, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log auth changes audit event: {e}")


def log_actions(event: Dict[str, Any], secondary_type: str, custom_data: Optional[Any] = None) -> None:
    """
    Log general actions with silent failure.

    Args:
        event: The API Gateway event
        secondary_type: The secondary type of action
        custom_data: Additional data to log (optional)
    """
    try:
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_ACTIONS)
        if not log_group_name:
            logger.error("AUDIT_LOG_ACTIONS resource name not resolved")
            return
        
        user_context = _extract_user_context(event)
        event_type = f"[ACTIONS][type: {secondary_type}]"
        message = _format_log_message(event_type, user_context, custom_data)
        
        _write_to_cloudwatch(log_group_name, message, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log actions audit event: {e}")


def log_errors(event: Dict[str, Any], secondary_type: str, custom_data: Optional[Any] = None) -> None:
    """
    Log errors with silent failure.

    Args:
        event: The API Gateway event
        secondary_type: The secondary type of error
        custom_data: Additional data to log (optional)
    """
    try:
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_ERRORS)
        if not log_group_name:
            logger.error("AUDIT_LOG_ERRORS resource name not resolved")
            return
        
        user_context = _extract_user_context(event)
        event_type = f"[ERRORS][type: {secondary_type}]"
        message = _format_log_message(event_type, user_context, custom_data)
        
        _write_to_cloudwatch(log_group_name, message, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log errors audit event: {e}")


def log_authorization_gateway(event: Dict[str, Any], authorized: bool, failure_reason: Optional[str] = None) -> None:
    """
    Log authorization events from API Gateway authorizer with silent failure.

    SECURITY: This function is designed for the API Gateway authorizer which runs
    BEFORE normal request processing. It only logs non-sensitive data:
    - User ID (only from verified JWT claims after successful authorization)
    - Authorization result (success/failure)
    - Generic failure reason (no token details or sensitive data)
    - Source IP address

    NEVER logs:
    - Raw JWT tokens
    - Authorization headers
    - Token signatures
    - Detailed validation errors that could expose token structure

    Args:
        event: The API Gateway authorizer event
        authorized: Whether authorization was successful
        failure_reason: Generic failure reason (optional, for failures only)
    """
    try:
        log_group_name = _audit_log_group(ResourceKeys.AUDIT_LOG_AUTHORIZATION)
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHORIZATION resource name not resolved")
            return
        
        # Extract ONLY safe user context
        user_context = {
            "user": "unknown",
            "roles": [],
            "mfaEnabled": False
        }
        
        # Only extract user ID if token was successfully verified
        # The 'context' field is only present after successful JWT verification
        if authorized and 'context' in event:
            context = event.get('context', {})
            user_context["user"] = context.get('sub', 'unknown')
            # MFA status from verified claims
            mfa_value = context.get('mfaEnabled', 'false')
            user_context["mfaEnabled"] = mfa_value == 'true' if isinstance(mfa_value, str) else bool(mfa_value)
        
        # Get source IP (safe to log)
        source_ip = event.get('requestContext', {}).get('http', {}).get('sourceIp', 'unknown')
        
        # Create safe log message
        event_type = f"[AUTHORIZATION][authorized: {authorized}]"
        custom_data = {
            "sourceIp": source_ip,
            "failureReason": failure_reason if not authorized else None
        }
        
        message = _format_log_message(event_type, user_context, custom_data)
        _write_to_cloudwatch(log_group_name, message, event)
        
    except Exception as e:
        # Silent failure - log locally but don't raise
        logger.exception(f"Failed to log authorization gateway audit event: {e}")