#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid

from boto3.dynamodb.conditions import Key
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from common.dynamodb import get_asset_object_from_id
from customLogging.logger import safeLogger

claims_and_roles = {}
main_rest_response = STANDARD_JSON_RESPONSE
logger = safeLogger(service_name="AssetLinksService")

dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')

try:
    asset_links_db_table_name = os.environ["ASSET_LINKS_STORAGE_TABLE_NAME"]
    asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed Loading Environment Variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})

# TODO: Read from constants file
allowed_asset_links = ["parent-child", "related"]


def assets_ids_are_valid(asset_ids):
    filter_expression = " OR ".join([f"assetId = :id{i}" for i, asset_id in enumerate(asset_ids, 1)])
    expression_attribute_values = {f":id{i}": {"S": asset_id} for i, asset_id in enumerate(asset_ids, 1)}

    items = dynamodb_client.scan(
        TableName=asset_table_name,
        ProjectionExpression='assetId, assetName',
        FilterExpression=filter_expression,
        ExpressionAttributeValues=expression_attribute_values,
    )
    return True if len(items.get("Items", [])) == len(asset_ids) else False


def create_asset_links(body):
    response = STANDARD_JSON_RESPONSE
    asset_links_table = dynamodb.Table(asset_links_db_table_name)
    existing_relation = asset_links_table.query(
        KeyConditionExpression=(
                Key('assetIdFrom').eq(body["assetIdFrom"]) &
                Key('assetIdTo').eq(body["assetIdTo"])
        )
    )

    response_from = asset_links_table.query(
        IndexName='AssetIdFromGSI',
        Limit=750, #Higher limit than than 500 allowed asset links in case of any overflows
        KeyConditionExpression=Key('assetIdFrom').eq(body["assetIdFrom"])
    )

    response_to = asset_links_table.query(
        IndexName='AssetIdToGSI',
        Limit=750, #Higher limit than than 500 allowed asset links in case of any overflows
        KeyConditionExpression=Key('assetIdTo').eq(body["assetIdTo"])
    )
    itemsFrom = response_from['Items']
    itemsTo = response_to['Items']

    if not (len(itemsFrom) > 500 or len(itemsTo) > 500):
        if 'Items' in existing_relation and len(existing_relation['Items']) == 0:
            item = {
                "relationId": str(uuid.uuid4()),
                "assetIdFrom": body["assetIdFrom"],
                "assetIdTo": body["assetIdTo"],
                "relationshipType": body["relationshipType"]
            }

            asset_link_from_object = get_asset_object_from_id(body["assetIdFrom"])
            asset_link_to_object = get_asset_object_from_id(body["assetIdTo"])

            # Add Casbin Enforcer to check if the current user has permissions to POST the both the assets
            asset_link_from_object.update({"object__type": "asset"})
            asset_link_to_object.update({"object__type": "asset"})

            from_asset_link_allowed = to_asset_link_allowed = False

            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(asset_link_from_object, "POST"):
                    from_asset_link_allowed = True
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(asset_link_to_object, "POST"):
                    to_asset_link_allowed = True

            if from_asset_link_allowed and to_asset_link_allowed:
                asset_links_table.put_item(Item=item)
                response['statusCode'] = 200
                response['body'] = json.dumps({"message": "success"})
            else:
                response['statusCode'] = 403
                response['body'] = json.dumps({"message": "Not Authorized due to missing permissions for all involved assets"})
        else:
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "A relationship already exists between these two assets."})
    else:
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "One of the involved assets exceeds the 500 asset link total limit."})

    return response


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])

    try:
        httpMethod = event['requestContext']['http']['method']

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        if 'assetIdFrom' not in event['body'] or 'assetIdTo' not in event['body'] or 'relationshipType' not in event['body']:
            response['statusCode'] = 400
            message = "assetIdFrom, assetIdTo and relationshipType are required fields."
            response['body'] = json.dumps({"message": message})
            return response

        if event['body']['relationshipType'] not in allowed_asset_links:
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Relationship type " + str(event['body']['relationshipType']) + " isn't supported."})
            return response

        (valid, message) = validate({
            'assetIdFrom': {
                'value': event['body']['assetIdFrom'],
                'validator': 'ASSET_ID'
            },
            'assetIdTo': {
                'value': event['body']['assetIdTo'],
                'validator': 'ASSET_ID'
            },
            'relationshipType': {
                'value': event['body']['relationshipType'],
                'validator': 'STRING_256'
            }
        })

        if not valid:
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        if str(event['body']['assetIdFrom']) == str(event['body']['assetIdTo']):
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "assetIdFrom and assetIdTo can't be same."})
            return response

        if not assets_ids_are_valid([event['body']["assetIdFrom"], event['body']["assetIdTo"]]):
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "assetIdFrom and assetIdTo should be valid and existing."})
            return response

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if method_allowed_on_api and httpMethod == 'POST':
            return create_asset_links(event['body'])
        else:
            logger.error("Not Authorized")
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
