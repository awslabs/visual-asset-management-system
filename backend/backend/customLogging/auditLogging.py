# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Audit Logging Module for VAMS

This module provides functions to log audit events to CloudWatch Log Groups.
All functions implement silent failure - if logging fails, the error is logged
locally but the lambda execution continues without disruption.
"""

import os
import json
import boto3
from datetime import datetime
from typing import Dict, Any, Optional, List
from handlers.auth import request_to_claims
from customLogging.logger import mask_sensitive_data, safeLogger

# Initialize logger for audit logging module
logger = safeLogger(service_name="AuditLogging")

# Initialize CloudWatch Logs client
try:
    cloudwatch_logs = boto3.client('logs')
except Exception as e:
    logger.exception(f"Failed to initialize CloudWatch Logs client: {e}")
    cloudwatch_logs = None


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


def _write_to_cloudwatch(log_group_name: str, message: str, event: Dict[str, Any]) -> None:
    """
    Write audit log entry to CloudWatch with silent failure.
    
    Args:
        log_group_name: The CloudWatch log group name
        message: The formatted log message
        event: The original event (for masking sensitive data)
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
        
        # Ensure log stream exists (create if it doesn't)
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

        #Add event at the end of the message.
        if masked_event:
            message += f" --- [event: {json.dumps(masked_event)}]"
        
        # Prepare log event
        timestamp = int(datetime.utcnow().timestamp() * 1000)
        log_event = {
            'logGroupName': log_group_name,
            'logStreamName': log_stream_name,
            'logEvents': [
                {
                    'timestamp': timestamp,
                    'message': message
                }
            ]
        }
        
        # Write to CloudWatch
        cloudwatch_logs.put_log_events(**log_event)
        
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
        log_group_name = os.environ.get("AUDIT_LOG_AUTHENTICATION")
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHENTICATION environment variable not set")
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
        log_group_name = os.environ.get("AUDIT_LOG_AUTHORIZATION")
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHORIZATION environment variable not set")
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
        log_group_name = os.environ.get("AUDIT_LOG_AUTHORIZATION")
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHORIZATION environment variable not set")
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
        log_group_name = os.environ.get("AUDIT_LOG_FILEUPLOAD")
        if not log_group_name:
            logger.error("AUDIT_LOG_FILEUPLOAD environment variable not set")
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
        log_group_name = os.environ.get("AUDIT_LOG_FILEDOWNLOAD")
        if not log_group_name:
            logger.error("AUDIT_LOG_FILEDOWNLOAD environment variable not set")
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
        log_group_name = os.environ.get("AUDIT_LOG_FILEDOWNLOAD_STREAMED")
        if not log_group_name:
            logger.error("AUDIT_LOG_FILEDOWNLOAD_STREAMED environment variable not set")
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
        log_group_name = os.environ.get("AUDIT_LOG_AUTHOTHER")
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHOTHER environment variable not set")
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
        log_group_name = os.environ.get("AUDIT_LOG_AUTHCHANGES")
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHCHANGES environment variable not set")
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
        log_group_name = os.environ.get("AUDIT_LOG_ACTIONS")
        if not log_group_name:
            logger.error("AUDIT_LOG_ACTIONS environment variable not set")
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
        log_group_name = os.environ.get("AUDIT_LOG_ERRORS")
        if not log_group_name:
            logger.error("AUDIT_LOG_ERRORS environment variable not set")
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
        log_group_name = os.environ.get("AUDIT_LOG_AUTHORIZATION")
        if not log_group_name:
            logger.error("AUDIT_LOG_AUTHORIZATION environment variable not set")
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