#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json

from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.dynamodb import get_asset_object_from_id
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service="CheckSubscriptionService")
dynamodb = boto3.resource('dynamodb')

main_rest_response = STANDARD_JSON_RESPONSE

try:
    subscription_table_name = os.environ["SUBSCRIPTIONS_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def check_subscriptions(body):
    response = STANDARD_JSON_RESPONSE
    # TODO: Read this from constants.
    event_name = "Asset Version Change"
    entity_name = "Asset"
    subscription_table = dynamodb.Table(subscription_table_name)
    result = subscription_table.query(
        IndexName='eventName-entityName_entityId-index',
        KeyConditionExpression='#entityNameId = :entityNameId AND #eventName = :eventName',
        FilterExpression='contains(#subscribers, :userId)',
        ExpressionAttributeNames={
            '#entityNameId': 'entityName_entityId',
            '#eventName': 'eventName',
            '#subscribers': 'subscribers',
        },
        ExpressionAttributeValues={
            ':entityNameId': f'{entity_name}#{body["assetId"]}',
            ':eventName': event_name,
            ':userId': body["userId"],
        }
    )

    item = result.get('Items', [])
    if item:
        response['statusCode'] = 200
        response['body'] = json.dumps({"message": "success"})
    else:
        response['statusCode'] = 200
        response['body'] = json.dumps({"message": "Subscription doesn't exists."})
    return response


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])

    try:
        httpMethod = event['requestContext']['http']['method']

        if not event['body'].get('userId') or not event['body'].get('assetId'):
            message = "userId and assetId are required fields."
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        (valid, message) = validate({
            'userId': {
                'value': event['body']['userId'],
                'validator': 'USERID'
            },
            'assetId': {
                'value': event['body']['assetId'],
                'validator': 'ASSET_ID'
            }
        })

        if not valid:
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        global claims_and_roles
        claims_and_roles = request_to_claims(event)
        method_allowed_on_api = False

        asset_object = get_asset_object_from_id(event['body']["assetId"])
        asset_object.update({"object__type": "asset"})
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if (casbin_enforcer.enforceAPI(event) and
                    casbin_enforcer.enforce(asset_object, "GET")):
                method_allowed_on_api = True

        if method_allowed_on_api and httpMethod == 'POST':
            return check_subscriptions(event['body'])
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
