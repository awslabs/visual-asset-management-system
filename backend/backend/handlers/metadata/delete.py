# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.metadata import build_response, ValidationError, table, validate_event
from handlers.authz import CasbinEnforcer

from common.dynamodb import get_asset_object_from_id
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service="DeleteMetadata")


def delete_item(databaseId, assetId):
    table.delete_item(
        Key={
            "databaseId": databaseId,
            "assetId": assetId,
        },
    )

    # Disabled as we are not sure how metadata will be restricted. Currently, it is being restricted,
    # if the user has view/edit permissions on the asset
    # db_response = table.get_item(
    #     Key={
    #         "databaseId": databaseId,
    #         "assetId": assetId,
    #     }
    # )
    # metadata = db_response.get("Item", {})
    #
    # if metadata:
    #     allowed = False
    #     # Add Casbin Enforcer to check if the current user has permissions to DELETE the metadata:
    #     metadata.update({
    #         "object__type": "metadata"
    #     })
    #     for user_name in claims_and_roles["tokens"]:
    #         casbin_enforcer = CasbinEnforcer(user_name)
    #         if casbin_enforcer.enforce(f"user::{user_name}", metadata, "DELETE"):
    #             allowed = True
    #             break
    #
    #     if allowed:
    #         table.delete_item(
    #             Key={
    #                 "databaseId": databaseId,
    #                 "assetId": assetId,
    #             },
    #         )
    #     else:
    #         raise ValidationError(403, "Not Authorized")
    # else:
    #     raise ValidationError(404, "Metadata not found")


def lambda_handler(event, context):
    logger.info(event)

    http_method = event['requestContext']['http']['method']

    global claims_and_roles
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

    try:
        if method_allowed_on_api:
            validate_event(event)
            databaseId = event['pathParameters']['databaseId']
            assetId = event['pathParameters']['assetId']

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

                if allowed:
                    delete_item(databaseId, assetId)
                    response = {"status": "OK", "message": "{assetId} deleted".format(assetId=assetId)}
                    return build_response(200, json.dumps(response))
                else:
                    raise ValidationError(403, "Not Authorized")
            else:
                raise ValidationError(404, "Asset not found for metadata")
        else:
            raise ValidationError(403, "Not Authorized")
    except ValidationError as ex:
        logger.exception(ex)
        return build_response(ex.code, json.dumps(ex.resp))
    except Exception as e:
        logger.exception(e)
        return build_response(500, "Internal Server Error")
