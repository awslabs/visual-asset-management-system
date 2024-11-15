# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from handlers.metadata import build_response, create_or_update, validate_event, validate_body, ValidationError
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import get_asset_object_from_id

claims_and_roles = {}
logger = safeLogger(service="CreateUpdateMetadata")


def lambda_handler(event, context):
    global claims_and_roles
    http_method = event['requestContext']['http']['method']
    logger.info(event)
    try:

        validate_event(event)
        body = validate_body(event)
        databaseId = event['pathParameters']['databaseId']
        assetId = event['pathParameters']['assetId']

        claims_and_roles = request_to_claims(event)
        method_allowed_on_api = False
        request_object = {
            "object__type": "api",
            "route__path": event['requestContext']['http']['path']
        }
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", request_object, http_method):
                method_allowed_on_api = True
                break

        if method_allowed_on_api:
            asset_of_metadata = get_asset_object_from_id(assetId)
            if asset_of_metadata:
                allowed = False
                # Add Casbin Enforcer to check if the current user has permissions to POST the asset:
                asset_of_metadata.update({
                    "object__type": "asset"
                })
                for user_name in claims_and_roles["tokens"]:
                    casbin_enforcer = CasbinEnforcer(user_name)
                    if casbin_enforcer.enforce(f"user::{user_name}", asset_of_metadata, "POST"):
                        allowed = True
                        break

                #Use prefix (if given) now that we have done base asset ID checks
                if ('queryStringParameters' in event and 'prefix' in event['queryStringParameters']):
                    assetId = event['queryStringParameters']['prefix']

                if allowed:
                    create_or_update(databaseId, assetId, body['metadata'])
                    return build_response(200, json.dumps({"status": "OK"}))
                else:
                    logger.error("403: Not Authorized")
                    return build_response(403, json.dumps({
                        "status": "Not Authorized",
                        "requestid": event['requestContext']['requestId']
                    }))
        else:
            logger.error("403: Not Authorized")
            return build_response(403, json.dumps({
                "status": "Not Authorized",
                "requestid": event['requestContext']['requestId']
            }))

    except ValidationError as ex:
        logger.exception(ex)
        return build_response(ex.code, json.dumps(ex.resp))
    except Exception as e:
        logger.exception(e)
        return build_response(500, "Internal Server Error")
