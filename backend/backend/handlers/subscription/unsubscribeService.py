#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import copy
import boto3
import json

from botocore.exceptions import ClientError
from botocore.config import Config
from boto3.dynamodb.conditions import Key
from common.resourceNames import get_table_name, ResourceKeys
from handlers.auth import request_to_claims
from common.auth.apiEvent import normalize_event
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate, normalize_userid_array
from handlers.authz import CasbinEnforcer
from common.dynamodb import get_asset_object_from_id, query_all_items
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service="UnsubscriptionService")
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
sns_client = boto3.client('sns', config=retry_config)

# A required table name that cannot be resolved fails the module load, so the deployment reports it
# at cold start. Degrading to None instead let the module import and turned the failure into a boto3
# error on a None table name for every request afterwards -- a generic 500 naming nothing.
try:
    subscription_table_name = get_table_name(ResourceKeys.SUBSCRIPTIONS_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e


def get_asset(asset_id):
    """Resolve the databaseId and SNS topic of the asset carrying an assetId.

    assetIdGSI is partitioned on assetId, so this is a keyed read of that index paged to
    exhaustion. A single scan with a FilterExpression applies its filter only to the page
    it already read, so it answers None for an asset that exists once the table outgrows
    one page.

    Returns None when the assetId resolves to no live asset, and when it resolves to more
    than one: assetIds are unique within a database only, so an ambiguous match cannot be
    attributed to a single database's record.
    """
    asset_table = dynamodb.Table(asset_table_name)
    items = query_all_items(
        asset_table,
        IndexName='assetIdGSI',
        KeyConditionExpression=Key('assetId').eq(asset_id)
    )

    # Archiving rewrites a record under a "{databaseId}#deleted" partition, so an archived
    # row is not the live asset and must not stand in for it
    live_items = [
        item for item in items
        if not str(item.get('databaseId', '')).endswith('#deleted')
        and item.get('status') != 'archived'
    ]

    if len(live_items) != 1:
        logger.error(
            f"assetId {asset_id} matches {len(live_items)} live assets; "
            f"archived or duplicate matches: {len(items)}")
        return None

    item = live_items[0]
    asset_obj = {"databaseId": item.get('databaseId')}
    if item.get('snsTopic'):
        asset_obj["snsTopic"] = item.get('snsTopic')
    return asset_obj


def delete_sns_subscriptions(asset_id, subscribers, delete_sns=False):
    asset_table = dynamodb.Table(asset_table_name)
    asset_obj = get_asset(asset_id)

    # The subscription row has already been updated; the topic cleanup is best effort and
    # is skipped when the asset can no longer be resolved to one live record
    if asset_obj is None:
        logger.error(f"No live asset found for asset {asset_id}")
        return

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

        # The stored subscriber ids are normalized, so the ids to remove are normalized too
        event['body']['subscribers'] = normalize_userid_array(event['body']['subscribers'])

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

        # Route authorization runs ahead of the asset lookup, so a caller without access
        # to the route learns nothing about whether the requested asset exists
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

        asset_object = get_asset_object_from_id(None, event['body']["entityId"])
        if asset_object is None:
            response['statusCode'] = 404
            response['body'] = json.dumps({"message": "Asset not found"})
            return response

        asset_object.update({"object__type": "asset"})

        allowed = False
        if len(claims_and_roles["tokens"]) > 0:
            if casbin_enforcer.enforce(asset_object, "POST"):
                allowed = True

        if allowed and httpMethod == 'DELETE':
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

