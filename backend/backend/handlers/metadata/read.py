# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from handlers.metadata import build_response, metadata_table, validate_event, ValidationError, normalize_s3_path
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import get_asset_object_from_id
from decimal import Decimal
from common.dynamodb import validate_pagination_info


claims_and_roles = {}
logger = safeLogger(service="ReadMetadata")


def generate_prefixes(path):
    prefixes = []
    parts = path.split('/')
    for i in range(1, len(parts)):
        prefix = '/'.join(parts[:i]) + '/'
        prefixes.insert(0, prefix)

    if (not path.endswith('/')):
        prefixes.insert(0, path)
    return prefixes

def get_metadata_with_prefix(databaseId, assetId, prefix):
    result = {}
    if prefix is not None:

        for paths in generate_prefixes(prefix):
            resp = metadata_table.get_item(
                Key={
                    "databaseId": databaseId,
                    "assetId": paths,
                }
            )
            if "Item" in resp:
                result = resp['Item'] | result
        try:
            asset_metadata = get_metadata(databaseId, assetId)
            result = asset_metadata | result
            return result
        except ValidationError:
            return result
    else:
        return get_metadata(databaseId, assetId)


def get_metadata(databaseId, assetId):
    resp = metadata_table.get_item(
        Key={
            "databaseId": databaseId,
            "assetId": assetId,
        }
    )
    if "Item" not in resp:
        raise ValidationError(404, "Item Not Found")

    # Convert values that are of type decimal to string (to prevent JSON parse errors on response return)
    for key, value in resp['Item'].items():
        if isinstance(value, Decimal):
            resp['Item'][key] = str(value)

    return resp['Item']


def lambda_handler(event, context):
    global claims_and_roles
    logger.info(event)
    try:
        validate_event(event)
        databaseId = event['pathParameters']['databaseId']
        assetId = event['pathParameters']['assetId']
        prefix = None
        if ('queryStringParameters' in event
                and 'prefix' in event['queryStringParameters']):
            prefix = (event['queryStringParameters']
                      and event['queryStringParameters']['prefix'])

        queryParameters = event.get('queryStringParameters', {})
        validate_pagination_info(queryParameters)

        claims_and_roles = request_to_claims(event)
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if method_allowed_on_api:
            asset_of_metadata = get_asset_object_from_id(databaseId, assetId)
            if asset_of_metadata:
                allowed = False
                # Add Casbin Enforcer to check if the current user has permissions to GET the asset:
                asset_of_metadata.update({
                    "object__type": "asset"
                })
                if len(claims_and_roles["tokens"]) > 0:
                    casbin_enforcer = CasbinEnforcer(claims_and_roles)
                    if casbin_enforcer.enforce(asset_of_metadata, "GET"):
                        allowed = True

                if allowed:
                    metadata = get_metadata_with_prefix(databaseId, assetId, prefix)

                    # remove private keys that start with underscores
                    for key in list(metadata.keys()):
                        if key.startswith("_"):
                            del metadata[key]

                    return build_response(200, json.dumps({
                        "version": "1",
                        "metadata": metadata,
                    }))
                else:
                    raise ValidationError(403, "Not Authorized")
            else:
                raise ValidationError(403, "Not Authorized")
        else:
            raise ValidationError(403, "Not Authorized")

    except ValidationError as ex:
        logger.exception(ex)
        return build_response(ex.code, json.dumps(ex.resp))
    except Exception as e:
        logger.exception(e)
        return build_response(500, "Internal Server Error")
