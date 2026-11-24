#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import copy
import boto3
import json

from botocore.exceptions import ClientError
from botocore.config import Config
from boto3.dynamodb.types import TypeSerializer
from common.resourceNames import get_table_name, ResourceKeys
from handlers.auth import request_to_claims
from common.auth.apiEvent import normalize_event
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate, normalize_userid_array
from handlers.authz import CasbinEnforcer
from common.dynamodb import get_asset_object_from_id, asset_authorization_object
from common.commentSubscriptionKeys import subscription_audit_id, subscription_key
from models.common import VAMSGeneralErrorResponse
from customLogging.logger import safeLogger
from customLogging.auditLogging import audited_handler, log_action

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


def get_asset(database_id, asset_id):
    """The SNS topic of the live asset (database_id, asset_id), as {"databaseId", "snsTopic"?}.

    Returns None when the database holds no live asset with this id (an archived asset, whether its
    row has moved to the "#deleted" partition or only carries the status, is not live)."""
    asset_table = dynamodb.Table(asset_table_name)
    item = asset_table.get_item(Key={'databaseId': database_id, 'assetId': asset_id}).get('Item')
    if not item or item.get('status') == 'archived':
        logger.error(f"No live asset {asset_id} in database {database_id}")
        return None
    asset_obj = {"databaseId": database_id}
    if item.get('snsTopic'):
        asset_obj["snsTopic"] = item.get('snsTopic')
    return asset_obj


def delete_sns_subscriptions(database_id, asset_id, subscribers, delete_sns=False):
    asset_table = dynamodb.Table(asset_table_name)
    asset_obj = get_asset(database_id, asset_id)

    # An asset that is no longer live has no topic to act on, so the cleanup is skipped and the
    # caller's row rewrite proceeds without it
    if asset_obj is None:
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
                Key={'databaseId': database_id, 'assetId': asset_id},
                UpdateExpression="REMOVE snsTopic",
                ConditionExpression='attribute_exists(assetId)'
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                logger.warning(f"Asset no longer exists; skipping snsTopic removal - {asset_id}")
            else:
                raise


def _row_key(body):
    return subscription_key(body["eventName"], body["entityName"], body["databaseId"], body["entityId"])


def get_subscription_obj(body):
    serializer = TypeSerializer()
    resp = dynamodb_client.get_item(
        TableName=subscription_table_name,
        Key={k: serializer.serialize(v) for k, v in _row_key(body).items()}
    )
    return resp.get('Item')


def delete_subscription(body):
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    subscription_table = dynamodb.Table(subscription_table_name)
    items = get_subscription_obj(body)

    if not items:
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "Subscription does not exists for eventName."})
        return response

    subscriber = body["subscribers"][0]
    existing_subscribers = [item["S"] for item in items["subscribers"]['L']]

    # The SNS side is released before the row is rewritten: a failed unsubscribe leaves the row
    # still listing the subscriber, so the same request can be retried. The cleanup also runs
    # for a subscriber the row no longer lists -- a retry after a failure between the two writes
    # still owes the SNS half, and it is a no-op for an endpoint the topic never carried.
    if body["entityName"] == "Asset":
        delete_sns_subscriptions(body["databaseId"], body["entityId"], list(body["subscribers"]),
                                 delete_sns=False)

    if subscriber in existing_subscribers:
        existing_subscribers.remove(subscriber)
        subscription_table.update_item(
            Key=_row_key(body),
            UpdateExpression='SET subscribers = :subscribers',
            ExpressionAttributeValues={
                ':subscribers': existing_subscribers
            }
        )

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


@audited_handler
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

        body = event['body']
        required = ("eventName", "entityName", "entityId", "databaseId", "subscribers")
        if not isinstance(body, dict) or any(name not in body for name in required):
            message = "eventName, entityName, entityId, databaseId and subscribers are required fields."
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        # The stored subscriber ids are normalized, so the ids to remove are normalized too
        body['subscribers'] = normalize_userid_array(body['subscribers'])

        (valid, message) = validate({
            'eventName': {
                'value': body['eventName'],
                'validator': 'OBJECT_NAME'
            },
            'entityName': {
                'value': body['entityName'],
                'validator': 'OBJECT_NAME'
            },
            'databaseId': {
                'value': body['databaseId'],
                'validator': 'ID'
            },
            'entityId': {
                'value': body['entityId'],
                'validator': 'ASSET_ID'
            },
            'subscribers': {
                'value': body['subscribers'],
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

        # Tier 2 on the asset of the named database, decided before its existence is revealed
        database_id, asset_id = body["databaseId"], body["entityId"]
        asset_object = get_asset_object_from_id(database_id, asset_id)
        authorization_object = asset_authorization_object(asset_object, database_id, asset_id)
        if httpMethod != 'DELETE' or not casbin_enforcer.enforce(authorization_object, "POST"):
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
        if asset_object is None:
            response['statusCode'] = 404
            response['body'] = json.dumps({"message": "Asset not found"})
            return response

        result = delete_subscription(body)
        if result.get('statusCode') == 200:
            log_action(event, "subscriptionUnsubscribe", database_id=database_id, asset_id=asset_id,
                       subscriptionId=subscription_audit_id(body.get('eventName'), body.get('entityName'),
                                                            database_id, asset_id),
                       changes={"subscribers": body.get('subscribers')})
        return result
    except VAMSGeneralErrorResponse as v:
        logger.exception(v)
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": str(v)})
        return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
