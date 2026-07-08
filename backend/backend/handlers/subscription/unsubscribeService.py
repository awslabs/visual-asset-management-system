#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import copy
import boto3
import json

from botocore.exceptions import ClientError
from common.resourceNames import get_table_name, ResourceKeys
from handlers.auth import request_to_claims
from common.auth.apiEvent import normalize_event
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from common.dynamodb import get_asset_object_from_id
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service="UnsubscriptionService")
main_rest_response = copy.deepcopy(STANDARD_JSON_RESPONSE)
dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
sns_client = boto3.client('sns')

try:
    subscription_table_name = get_table_name(ResourceKeys.SUBSCRIPTIONS_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving subscriptions table name")
    subscription_table_name = None

try:
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving asset table name")
    asset_table_name = None

if not (subscription_table_name and asset_table_name):
    main_rest_response['body'] = json.dumps({"message": "Failed resolving required table names"})


def get_asset(asset_id):
    resp = dynamodb_client.scan(
        TableName=asset_table_name,
        ProjectionExpression='snsTopic, databaseId',
        FilterExpression='assetId = :asset_id',
        ExpressionAttributeValues={':asset_id': {'S': asset_id}},
    )

    items = resp.get('Items')
    if items:
        asset_obj = {"databaseId": items[0].get('databaseId').get("S")}
        if items[0].get('snsTopic'):
            asset_obj["snsTopic"] = items[0].get('snsTopic').get("S")
        return asset_obj
    return None


def delete_sns_subscriptions(asset_id, subscribers, delete_sns=False):
    asset_table = dynamodb.Table(asset_table_name)
    asset_obj = get_asset(asset_id)

    if not asset_obj.get("snsTopic"):
        logger.error(f"No topic found for asset {asset_id}")
        return

    resp = sns_client.list_subscriptions_by_topic(TopicArn=asset_obj.get("snsTopic"))
    subscription_arns = [subscription['SubscriptionArn'] for subscription in resp['Subscriptions'] if subscription['Endpoint'] in subscribers]

    for subscription_arn in subscription_arns:
        if subscription_arn != "PendingConfirmation":
            sns_client.unsubscribe(SubscriptionArn=subscription_arn)

    if delete_sns:
        sns_client.delete_topic(TopicArn=asset_obj.get("snsTopic"))
        # Conditional on the record still existing: a REMOVE-only update on a
        # missing key would otherwise create a key-only phantom record
        try:
            asset_table.update_item(
                Key={'databaseId': asset_obj["databaseId"], 'assetId': asset_id},
                UpdateExpression="REMOVE snsTopic",
                ConditionExpression='attribute_exists(assetId)'
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                logger.warning(f"Asset no longer exists; skipping snsTopic removal - {asset_id}")
            else:
                raise


def get_subscription_obj(event_name, entity_name, entity_id):
    resp = dynamodb_client.get_item(
        TableName=subscription_table_name,
        Key={
            'eventName': {'S': event_name},
            'entityName_entityId': {'S': f'{entity_name}#{entity_id}'}
        }
    )
    return resp.get('Item')


def delete_subscription(body):
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    subscription_table = dynamodb.Table(subscription_table_name)
    items = get_subscription_obj(body["eventName"], body["entityName"], body["entityId"])

    if not items or body["subscribers"][0] not in [item["S"] for item in items["subscribers"]['L']]:
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "Subscription does not exists for eventName."})
        return response

    existing_subscribers = [item["S"] for item in items["subscribers"]['L']]
    existing_subscribers.remove(body["subscribers"][0])

    subscription_table.update_item(
        Key={
            'eventName': body["eventName"],
            'entityName_entityId': f'{body["entityName"]}#{body["entityId"]}'
        },
        UpdateExpression='SET subscribers = :subscribers',
        ExpressionAttributeValues={
            ':subscribers': existing_subscribers
        }
    )

    if body["entityName"] == "Asset":
        delete_sns_subscriptions(body["entityId"], list(body["subscribers"]), delete_sns=False)

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


def lambda_handler(event, context):
    normalize_event(event)
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    try:
        httpMethod = event['requestContext']['http']['method']

        # Parse request body
        if not event.get('body'):
            message = 'Request body is required'
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            logger.error(response)
            return response

        if isinstance(event['body'], str):
            event['body'] = json.loads(event['body'])

        if "eventName" not in event['body'] or "entityName" not in event['body'] or "entityId" not in event['body'] or "subscribers" not in event['body']:
            message = "eventName, entityName and entityId are required fields."
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        (valid, message) = validate({
            'eventName': {
                'value': event['body']['eventName'],
                'validator': 'OBJECT_NAME'
            },
            'entityName': {
                'value': event['body']['entityName'],
                'validator': 'OBJECT_NAME'
            },
            'entityId': {
                'value': event['body']['entityId'],
                'validator': 'ID'
            },
            'subscribers': {
                'value': event['body']['subscribers'],
                'validator': 'USERID_ARRAY'
            }
        })

        if not valid:
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        global claims_and_roles
        claims_and_roles = request_to_claims(event)
        method_allowed_on_api = False

        asset_object = get_asset_object_from_id(None, event['body']["entityId"])
        asset_object.update({"object__type": "asset"})
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if (casbin_enforcer.enforceAPI(event) and
                    casbin_enforcer.enforce(asset_object, "POST")):
                method_allowed_on_api = True

        if method_allowed_on_api and httpMethod == 'DELETE':
            return delete_subscription(event['body'])
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response

