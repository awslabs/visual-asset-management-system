# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import traceback

from backend.handlers.metadata import logger, mask_sensitive_data, build_response, create_or_update, validate_event, validate_body, ValidationError
from backend.handlers.auth import get_database_set, request_to_claims


def lambda_handler(event, context):
    logger.info(mask_sensitive_data(event))
    try:

        validate_event(event)

        body = validate_body(event)
        databaseId = event['pathParameters']['databaseId']
        assetId = event['pathParameters']['assetId']
        if ('queryStringParameters' in event and 'prefix' in event['queryStringParameters']):
            assetId = event['queryStringParameters']['prefix']

        claims_and_roles = request_to_claims(event)

        if "super-admin" in claims_and_roles['roles']:
            create_or_update(databaseId, assetId, body['metadata'])
            return build_response(200, json.dumps({"status": "OK"}))

        databases = get_database_set(claims_and_roles['tokens'])
        if databaseId in databases or "super-admin" in claims_and_roles['roles']:
            create_or_update(databaseId, assetId, body['metadata'])
            return build_response(200, json.dumps({"status": "OK"}))
        else:
            return build_response(403, json.dumps({
                "status": "Not Authorized",
                "requestid": event['requestContext']['requestId']
            }))

    except ValidationError as ex:
        logger.info(traceback.format_exc())
        return build_response(ex.code, json.dumps(ex.resp))
    except Exception as ex:
        logger.error(traceback.format_exc())
        return build_response(500, "Server Error")
