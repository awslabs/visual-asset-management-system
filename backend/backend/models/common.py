# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from typing import Any, Dict, TypedDict, Optional
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_errors

logger = safeLogger(service_name="CommonModels")

class APIGatewayProxyResponseV2(TypedDict):
    isBase64Encoded: bool
    statusCode: int
    headers: Dict[str, str]
    body: str


def commonHeaders() -> Dict[str, str]:
    return {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache, no-store',
    }


def success(status_code: int = 200, body: Any = {'message': 'Success'}) -> APIGatewayProxyResponseV2:
    logger.info(f"Success response: {body}")
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body)
    )


def validation_error(status_code: int = 400, body: dict = {'message': 'Validation Error'}, event: Optional[Dict[str, Any]] = None) -> APIGatewayProxyResponseV2:
    logger.error(f"Validation error: {body}")
    
    # AUDIT LOG: Log validation error if event provided
    if event:
        try:
            log_errors(event, "validation", {
                "statusCode": status_code,
                "errorMessage": body.get('message', 'Validation Error')
            })
        except Exception as audit_error:
            logger.exception(f"Failed to log validation error audit: {audit_error}")
    
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body)
    )

def general_error(status_code: int = 400, body: dict = {'message': 'VAMS General Error'}, event: Optional[Dict[str, Any]] = None) -> APIGatewayProxyResponseV2:
    logger.error(f"General error: {body}")
    
    # AUDIT LOG: Log general error if event provided
    if event:
        try:
            log_errors(event, "general", {
                "statusCode": status_code,
                "errorMessage": body.get('message', 'VAMS General Error')
            })
        except Exception as audit_error:
            logger.exception(f"Failed to log general error audit: {audit_error}")
    
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body)
    )


def authorization_error(status_code: int = 403, body: dict = {'message': 'Not Authorized'}, event: Optional[Dict[str, Any]] = None) -> APIGatewayProxyResponseV2:
    logger.error(f"Not Authorized Error: {body}")
    
    #Logged as part of Casbin auth checks
    # # AUDIT LOG: Log authorization error if event provided
    # # Note: This logs the error response, not the authorization check itself
    # # Authorization checks are logged by the Casbin enforcer
    # if event:
    #     try:
    #         log_errors(event, "authorization", {
    #             "statusCode": status_code,
    #             "errorMessage": body.get('message', 'Not Authorized')
    #         })
    #     except Exception as audit_error:
    #         logger.exception(f"Failed to log authorization error audit: {audit_error}")
    
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body)
    )


def internal_error(status_code: int = 500, body: Any = {'message': 'Internal Server Error'}, event: Optional[Dict[str, Any]] = None) -> APIGatewayProxyResponseV2:
    logger.error(f"Internal Server Error: {body}")
    
    # AUDIT LOG: Log internal error if event provided
    if event:
        try:
            log_errors(event, "internal", {
                "statusCode": status_code,
                "errorMessage": body.get('message', 'Internal Server Error') if isinstance(body, dict) else str(body)
            })
        except Exception as audit_error:
            logger.exception(f"Failed to log internal error audit: {audit_error}")
    
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body)
    )


#Define VAMS Custom Exceptions

class VAMSGeneralError(Exception):
    pass

class VAMSGeneralErrorResponse(VAMSGeneralError):
    def __init__(self, message, status_code=400):
        super().__init__(f"VAMS General Error: {message}")
        self.status_code = status_code