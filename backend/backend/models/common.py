# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from typing import Any, Dict, TypedDict


class APIGatewayProxyResponseV2(TypedDict):
    isBase64Encoded: bool
    statusCode: int
    headers: Dict[str, str]
    body: str


def commonHeaders() -> Dict[str, str]:
    return {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*'
    }


def success(status_code: int = 200, body: Any = {'message': 'Success'}) -> APIGatewayProxyResponseV2:
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body)
    )


def validation_error(status_code: int = 422, body: dict = {'message': 'Validation Error'}) -> APIGatewayProxyResponseV2:
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body)
    )


def internal_error(status_code: int = 500, body: Any = {'message': 'Validation Error'}) -> APIGatewayProxyResponseV2:
    return APIGatewayProxyResponseV2(
        isBase64Encoded=False,
        statusCode=status_code,
        headers=commonHeaders(),
        body=json.dumps(body)
    )
