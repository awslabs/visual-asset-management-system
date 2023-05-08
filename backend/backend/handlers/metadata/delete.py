# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import json
from decimal import Decimal
from backend.common.validators import validate
import traceback

from backend.handlers.auth import get_database_set, request_to_claims
from backend.handlers.metadata import logger, mask_sensitive_data, build_response, ValidationError, table, validate_event


def delete_item(databaseId, assetId):
    table.delete_item(
        Key={
            "databaseId": databaseId,
            "assetId": assetId,
        },
    )


def lambda_handler(event, context):
    logger.info(mask_sensitive_data(event))
    try:
        validate_event(event)
        databaseId = event['pathParameters']['databaseId']
        assetId = event['pathParameters']['assetId']
        claims_and_roles = request_to_claims(event)
        databases = get_database_set(claims_and_roles['tokens'])

        if databaseId in databases or "super-admin" in claims_and_roles['roles']:
            delete_item(databaseId, assetId)
            response = {"status": "OK", "message": "{assetId} deleted".format(assetId=assetId)}
            return build_response(200, json.dumps(response))
        else:
            raise ValidationError(403, "Not Authorized")
    except ValidationError as ex:
        logger.info(traceback.format_exc())
        return build_response(ex.code, json.dumps(ex.resp))
    except Exception as ex:
        logger.error(traceback.format_exc())
        return build_response(500, "Server Error")
