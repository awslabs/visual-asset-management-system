# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import json
from decimal import Decimal
import traceback

from backend.handlers.metadata import logger, mask_sensitive_data, build_response, table, validate_event, ValidationError
from backend.handlers.auth import get_database_set, request_to_claims


def get_metadata(databaseId, assetId):
    resp = table.get_item(
        Key={
            "databaseId": databaseId,
            "assetId": assetId,
        }
    )
    if "Item" not in resp:
        raise ValidationError(404, "Item Not Found")
    return resp['Item']


def lambda_handler(event, context):
    logger.info(mask_sensitive_data(event))
    try:
        validate_event(event)
        databaseId = event['pathParameters']['databaseId']
        assetId = event['pathParameters']['assetId']

        claims_and_roles = request_to_claims(event)
        databases = get_database_set(claims_and_roles['tokens'])
        if databaseId in databases or "super-user" in claims_and_roles['roles']:
            return build_response(200, json.dumps({
                "version": "1",
                "metadata": get_metadata(databaseId, assetId)
            }))
        else:
            raise ValidationError(403, "Not Authorized")

    except ValidationError as ex:
        logger.info(traceback.format_exc())
        return build_response(ex.code, json.dumps(ex.resp))
    except Exception as ex:
        logger.error(traceback.format_exc())
        return build_response(500, "Server Error")
