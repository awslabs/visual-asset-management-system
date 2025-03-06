#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json

from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from common.dynamodb import get_asset_object_from_id
from customLogging.logger import safeLogger
from common.validators import validate

claims_and_roles = {}
logger = safeLogger(service_name="DeleteAssetLinksService")

dynamodb = boto3.resource('dynamodb')
main_rest_response = STANDARD_JSON_RESPONSE

try:
    asset_links_db_table_name = os.environ["ASSET_LINKS_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed Loading Environment Variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def delete_asset_link(relation_id):
    response = STANDARD_JSON_RESPONSE
    asset_links_table = dynamodb.Table(asset_links_db_table_name)
    relation = asset_links_table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr('relationId').eq(relation_id)
    )

    items = relation.get('Items', [])
    if items:
        for item in items:
            asset_link_from_object = get_asset_object_from_id(item['assetIdFrom'])
            asset_link_to_object = get_asset_object_from_id(item['assetIdTo'])

            # Add Casbin Enforcer to check if the current user has permissions to POST the both the assets
            asset_link_from_object.update({"object__type": "asset"})
            asset_link_to_object.update({"object__type": "asset"})

            from_asset_link_allowed = to_asset_link_allowed = False

            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(asset_link_from_object, "DELETE"):
                    from_asset_link_allowed = True
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(asset_link_to_object, "DELETE"):
                    to_asset_link_allowed = True

            if from_asset_link_allowed and to_asset_link_allowed:
                asset_links_table.delete_item(
                    Key={
                        'assetIdFrom': item['assetIdFrom'],
                        'assetIdTo': item['assetIdTo'],
                    }
                )
                response['statusCode'] = 200
                response['body'] = json.dumps({"message": "success"})
            else:
                response['statusCode'] = 403
                response['body'] = json.dumps({"message": "Action not Allowed"})
    else:
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "RelationId is not valid."})

    return response


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    path_parameters = event.get('pathParameters', {})
    relation_id = path_parameters.get("relationId")

    try:
        httpMethod = event['requestContext']['http']['method']

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        if relation_id is None or len(relation_id) == 0:
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "RelationId is not valid."})
            return response

        (valid, message) = validate({
            'relationshipId': {
                'value': relation_id,
                'validator': 'UUID'
            }
        })

        if not valid:
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if method_allowed_on_api and httpMethod == 'DELETE':
            return delete_asset_link(relation_id)
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

    except Exception as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Relation Id doesn't exists."})
        else:
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
