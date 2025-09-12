# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from typing import Any, Dict, TypedDict
from customLogging.logger import safeLogger

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


def validation_error(status_code: int = 400, body: dict = {'message': 'Validation Error'}) -> APIGatewayProxyResponseV2:
    logger.error(f"Validation error: {body}")
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body)
    )

def general_error(status_code: int = 400, body: dict = {'message': 'VAMS General Error'}) -> APIGatewayProxyResponseV2:
    logger.error(f"General error: {body}")
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body)
    )


def authorization_error(status_code: int = 403, body: dict = {'message': 'Not Authorized'}) -> APIGatewayProxyResponseV2:
    logger.error(f"Not Authorized Error: {body}")
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body)
    )


def internal_error(status_code: int = 500, body: Any = {'message': 'Internal Server Error'}) -> APIGatewayProxyResponseV2:
    logger.error(f"Internal Server Error: {body}")
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
