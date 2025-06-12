#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key, Attr
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from common.dynamodb import validate_pagination_info

claims_and_roles = {}
main_rest_response = STANDARD_JSON_RESPONSE

logger = safeLogger(service_name="GetAssetLinksService")

dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')

try:
    asset_links_db_table_name = os.environ["ASSET_LINKS_STORAGE_TABLE_NAME"]
    asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed Loading Environment Variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def batch_get_asset_names(asset_ids):
    if not asset_ids:
        return {}

    # TODO: Check if we can optimize this further
    filter_expression = " OR ".join([f"assetId = :id{i}" for i, asset_id in enumerate(asset_ids, 1)])

    expression_attribute_values = {f":id{i}": {"S": asset_id} for i, asset_id in enumerate(asset_ids, 1)}

    items = dynamodb_client.scan(
        TableName=asset_table_name,
        ProjectionExpression='assetId, assetName, databaseId, assetType, tags',
        FilterExpression=filter_expression,
        ExpressionAttributeValues=expression_attribute_values,
        Limit=1000, #Higher limit than than 500 allowed asset links in case of any overflows
    )
    return {item['assetId']['S']: {"assetName": item['assetName']['S'],
                                   "databaseId": item['databaseId']['S'],
                                   "assetType": item['assetType']['S'],
                                   "tags": [tag['S'] for tag in item['tags']['L']]} for item in items.get("Items", [])}


def get_asset_links(asset_id, query_params):
    response = STANDARD_JSON_RESPONSE
    asset_link_table = dynamodb.Table(asset_links_db_table_name)

    response_from = asset_link_table.query(
        IndexName='AssetIdFromGSI',
        Limit=750, #Higher limit than than 500 allowed asset links in case of any overflows
        KeyConditionExpression=Key('assetIdFrom').eq(asset_id)
    )

    response_to = asset_link_table.query(
        IndexName='AssetIdToGSI',
        Limit=750, #Higher limit than than 500 allowed asset links in case of any overflows
        KeyConditionExpression=Key('assetIdTo').eq(asset_id)
    )
    items = response_from['Items'] + response_to['Items']

    related_asset_ids = set()
    for item in items:
        related_asset_ids.add(item['assetIdFrom'])
        related_asset_ids.add(item['assetIdTo'])

    # Batch-get asset names for all related assetIds
    asset_names = {}
    if related_asset_ids:
        asset_names = batch_get_asset_names(list(related_asset_ids))

    # Organize the relationships in the desired format with asset names
    relationships = {
        'parent': [],
        'child': [],
        'relatedTo': []
    }

    for item in items:
        if item['relationshipType'] == "related": # TODO: Remove the hardcoding
            if item['assetIdTo'] == asset_id:
                asset_object = asset_names.get(item['assetIdFrom'], {})
                asset_object.update({
                    "object__type": "asset",
                    'assetType': asset_names.get(item['assetIdFrom'], {}).get("assetType"),
                    'tags': asset_names.get(item['assetIdFrom'], {}).get("tags"),
                })
                if len(claims_and_roles["tokens"]) > 0:
                    casbin_enforcer = CasbinEnforcer(claims_and_roles)
                    if casbin_enforcer.enforce(asset_object, "GET"):
                        relationships['relatedTo'].append({
                            'relationId': item['relationId'],
                             'assetId': item['assetIdFrom'],
                             'assetName': asset_names.get(item['assetIdFrom'], {}).get("assetName"),
                             'databaseId': asset_names.get(item['assetIdFrom'], {}).get("databaseId")
                        })
            else:
                asset_object = asset_names.get(item['assetIdTo'], {})
                asset_object.update({
                    "object__type": "asset",
                    'assetType': asset_names.get(item['assetIdTo'], {}).get("assetType"),
                    'tags': asset_names.get(item['assetIdTo'], {}).get("tags"),
                })
                if len(claims_and_roles["tokens"]) > 0:
                    casbin_enforcer = CasbinEnforcer(claims_and_roles)
                    if casbin_enforcer.enforce(asset_object, "GET"):
                        relationships['relatedTo'].append({
                            'relationId': item['relationId'],
                            'assetId': item['assetIdTo'],
                            'assetName': asset_names.get(item['assetIdTo'], {}).get("assetName"),
                            'databaseId': asset_names.get(item['assetIdTo'], {}).get("databaseId")
                        })
        elif item['assetIdTo'] == asset_id:
            asset_object = asset_names.get(item['assetIdFrom'], {})
            asset_object.update({
                "object__type": "asset",
                'assetType': asset_names.get(item['assetIdFrom'], {}).get("assetType"),
                'tags': asset_names.get(item['assetIdFrom'], {}).get("tags"),
            })
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(asset_object, "GET"):
                    relationships['parent'].append({
                        'relationId': item['relationId'],
                        'assetId': item['assetIdFrom'],
                        'assetName': asset_names.get(item['assetIdFrom'], {}).get("assetName"),
                        'databaseId': asset_names.get(item['assetIdFrom'], {}).get("databaseId")
                    })
        elif item['assetIdFrom'] == asset_id:
            asset_object = asset_names.get(item['assetIdTo'], {})
            asset_object.update({
                "object__type": "asset",
                'assetType': asset_names.get(item['assetIdTo'], {}).get("assetType"),
                'tags': asset_names.get(item['assetIdTo'], {}).get("tags"),
            })
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(asset_object, "GET"):
                    relationships['child'].append({
                        'relationId': item['relationId'],
                        'assetId': item['assetIdTo'],
                        'assetName': asset_names.get(item['assetIdTo'], {}).get("assetName"),
                        'databaseId': asset_names.get(item['assetIdTo'], {}).get("databaseId")
                    })

    response['body'] = json.dumps({"message": relationships})
    return response


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE

    path_parameters = event.get('pathParameters', {})
    queryParameters = event.get('queryStringParameters', {})
    asset_id = path_parameters.get("assetId")

    try:
        httpMethod = event['requestContext']['http']['method']

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        validate_pagination_info(queryParameters)

        if asset_id is None or len(asset_id) == 0:
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "AssetId is not valid."})
            return response

        (valid, message) = validate({
            'assetId': {
                'value': asset_id,
                'validator': 'ASSET_ID'
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

        if httpMethod == 'GET' and method_allowed_on_api:
            return get_asset_links(asset_id, queryParameters)
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

    except Exception as e:
        # Check if this is a boto3 ClientError with ConditionalCheckFailedException
        try:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                response['statusCode'] = 400
                response['body'] = json.dumps({"message": "AssetId doesn't exists."})
                return response
        except (AttributeError, KeyError, TypeError):
            # Not a ClientError or doesn't have the expected structure
            pass
        
        # Handle as a general error
        logger.exception("Error in lambda_handler")
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
