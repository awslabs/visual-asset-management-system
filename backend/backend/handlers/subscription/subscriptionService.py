#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import copy
import boto3
import json

from botocore.exceptions import ClientError
from botocore.config import Config
from common.resourceNames import get_table_name, ResourceKeys
from handlers.auth import request_to_claims
from common.auth.apiEvent import normalize_event
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate, normalize_userid_array
from models.common import VAMSGeneralErrorResponse
from handlers.authz import CasbinEnforcer
from common.dynamodb import get_asset_object_from_id, asset_authorization_object, batch_get_items
from common.commentSubscriptionKeys import (
    SUBSCRIPTION_PARTITION_KEY,
    SUBSCRIPTION_SORT_KEY,
    subscription_audit_id,
    subscription_key,
)
from customLogging.logger import safeLogger
from customLogging.auditLogging import audited_handler, log_action
from common.dynamodb import validate_pagination_info
from common.snsTopicNames import asset_sns_topic_name
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

claims_and_roles = {}
logger = safeLogger(service="SubscriptionService")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
sns_client = boto3.client('sns', config=retry_config)

# Hard-coded allowed values for subscription fields
ALLOWED_EVENT_NAMES = [
    'Asset Version Change'
]

ALLOWED_ENTITY_NAMES = [
    'Asset'
]

def validate_subscription_fields(body):
    """Validate subscription fields against allowed values"""

    # Validate eventName
    if body['eventName'] not in ALLOWED_EVENT_NAMES:
        raise ValueError(f"Invalid eventName. Allowed values: {', '.join(ALLOWED_EVENT_NAMES)}")

    # Validate entityName
    if body['entityName'] not in ALLOWED_ENTITY_NAMES:
        raise ValueError(f"Invalid entityName. Allowed values: {', '.join(ALLOWED_ENTITY_NAMES)}")

    return True

# A required table name that cannot be resolved fails the module load, so the deployment reports it
# at cold start. Degrading to None instead let the module import and turned the failure into a boto3
# error on a None table name for every request afterwards -- a generic 500 naming nothing.
try:
    subscription_table_name = get_table_name(ResourceKeys.SUBSCRIPTIONS_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    user_table_name = get_table_name(ResourceKeys.USER_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e


def _wire_key(event_name, entity_name, database_id, entity_id):
    """The subscription row's key in the low-level client (wire) form."""
    serializer = TypeSerializer()
    return {k: serializer.serialize(v)
            for k, v in subscription_key(event_name, entity_name, database_id, entity_id).items()}


def _row_entity(document):
    """(entityName, databaseId, entityId) of a stored subscription row.

    The row carries the three as attributes; the sort key is read only for a row without them.
    Entity names and database ids never contain ':', so the first two ':' separate the parts."""
    if document.get("entityName") and document.get("databaseId") and document.get("entityId"):
        return document["entityName"], document["databaseId"], document["entityId"]
    entity_name, database_id, entity_id = str(document.get(SUBSCRIPTION_SORT_KEY, "")).split(":", 2)
    return entity_name, database_id, entity_id


def _read_live_assets(keys):
    """The live asset rows for a set of (databaseId, assetId) keys, as {(databaseId, assetId): row}.

    One BatchGetItem per 100 keys. A key the batch read could not complete is read again on its
    own, so a throttled read is never taken for an asset that is gone; a row that is gone (or
    archived, which moves it out of the live partition) is absent from the result."""
    if not keys:
        return {}
    request_keys = [{'databaseId': database_id, 'assetId': asset_id} for database_id, asset_id in keys]
    rows, unresolved = batch_get_items(dynamodb, asset_table_name, request_keys)
    if unresolved:
        asset_table = dynamodb.Table(asset_table_name)
        for key in unresolved:
            item = asset_table.get_item(Key=key).get('Item')
            if item:
                rows.append(item)
    return {(row.get('databaseId'), row.get('assetId')): row for row in rows}


def get_subscriptions(query_params, event=None):
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    deserializer = TypeDeserializer()
    paginator = dynamodb_client.get_paginator('scan')

    page_iterator = paginator.paginate(
        TableName=subscription_table_name,
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ).build_full_result()

    rows = []
    for obj in page_iterator.get('Items', []):
        document = {k: deserializer.deserialize(v) for k, v in obj.items()}
        entity_name, database_id, entity_id = _row_entity(document)
        rows.append({
            "eventName": document["eventName"],
            "entityName": entity_name,
            "databaseId": database_id,
            "entityId": entity_id,
            "subscribers": document.get("subscribers", []),
        })

    # Each distinct asset is read once for the page, by its database and asset id
    assets = _read_live_assets({(row["databaseId"], row["entityId"]) for row in rows
                                if row["entityName"] == "Asset"})

    items = []
    casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
    for row in rows:
        asset = assets.get((row["databaseId"], row["entityId"])) if row["entityName"] == "Asset" else None
        # A row with no asset to authorize against is dropped; the rest of the page still returns
        if asset is None or casbin_enforcer is None:
            continue
        if not casbin_enforcer.enforce({**asset, "object__type": "asset"}, "GET"):
            continue
        items.append({
            "eventName": row["eventName"],
            "entityName": row["entityName"],
            "entityId": row["entityId"],
            "subscribers": row["subscribers"],
            "entityValue": asset.get("assetName"),
            "databaseId": row["databaseId"],
        })

    result = {"Items": items}
    if 'NextToken' in page_iterator:
        result['NextToken'] = page_iterator['NextToken']

    if event is not None:
        log_action(event, "subscriptionList", item_count=len(result["Items"]),
                   request_parameters=dict(query_params or {}))

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": result})
    return response


def create_sns_topic(asset_id, database_id):
    topic_response = sns_client.create_topic(
        Name=asset_sns_topic_name(database_id, asset_id))
    return topic_response['TopicArn']


def add_sns_topic_in_asset(asset_id, database_id, sns_topic):
    asset_table = dynamodb.Table(asset_table_name)
    # Conditional on the record still existing: the asset may be archived or deleted since it was
    # read, and an unconditional update_item would re-create a phantom record containing only the key
    try:
        asset_table.update_item(
            Key={'databaseId': database_id, 'assetId': asset_id},
            UpdateExpression='SET snsTopic = :sns_topic',
            ConditionExpression='attribute_exists(assetId)',
            ExpressionAttributeValues={':sns_topic': sns_topic}
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            logger.warning(f"Asset no longer exists; skipping snsTopic update - {asset_id}")
            return
        raise


def get_asset(database_id, asset_id):
    """The SNS topic of the live asset (database_id, asset_id), as {"databaseId", "snsTopic"?}.

    Returns None when the database holds no live asset with this id: an archived asset offers no
    subscription, whether its row has moved to the "#deleted" partition or only carries the status."""
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

    # The subscription row has already been updated; the topic cleanup is best effort and
    # is skipped when the asset is no longer live
    if asset_obj is None:
        return

    if not asset_obj.get("snsTopic"):
        logger.error(f"No topic found for asset {asset_id}")
        return

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
    else:
        resp = sns_client.list_subscriptions_by_topic(TopicArn=asset_obj.get("snsTopic"))
        subscription_arns = [subscription['SubscriptionArn'] for subscription in resp['Subscriptions'] if subscription['Endpoint'] in subscribers]

        for subscription_arn in subscription_arns:
            if subscription_arn != "PendingConfirmation":
                sns_client.unsubscribe(SubscriptionArn=subscription_arn)

    return


def create_sns_subscriptions(database_id, asset_id, emails):
    asset_obj = get_asset(database_id, asset_id)

    # The subscription row is written before this runs, so an asset that is no longer live is
    # reported rather than dereferenced into a 500
    if asset_obj is None:
        raise VAMSGeneralErrorResponse("Asset could not be resolved for this subscription.")

    asset_sns_topic = asset_obj.get("snsTopic")

    if not asset_sns_topic:
        asset_sns_topic = create_sns_topic(asset_id, database_id)
        add_sns_topic_in_asset(asset_id, database_id, asset_sns_topic)

    for subscriber in emails:
        sns_client.subscribe(
            TopicArn=asset_sns_topic,
            Protocol='email',
            Endpoint=f'{subscriber}'
        )


def get_subscription_obj(event_name, entity_name, database_id, entity_id):
    resp = dynamodb_client.get_item(
        TableName=subscription_table_name,
        Key=_wire_key(event_name, entity_name, database_id, entity_id)
    )
    return resp.get('Item')


def get_userProfile_Email(userId):

    #Try to get user email information
    user_table = dynamodb.Table(user_table_name)
    response = user_table.get_item(
        Key={
            'userId': userId
        }
    )

    #Lookup user profile email and use that.
    #If not available or blank, set to userID and validate it's in email format
    email = None
    if 'Item' in response:
        email = response['Item'].get('email','')

    if not email or email == '':
        (valid, message) = validate({
            'userIdEmail': {
                'value': userId,
                'validator': 'EMAIL'
            }
        })

        if not valid:
            email = "INVALID_FORMAT"
        else: 
            email = userId

    return email


def _row_key(body):
    return subscription_key(body["eventName"], body["entityName"], body["databaseId"], body["entityId"])


def create_subscription(body):
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    subscription_table = dynamodb.Table(subscription_table_name)
    
    # Validate subscription fields against allowed values
    try:
        validate_subscription_fields(body)
    except ValueError as e:
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": str(e)})
        return response
    
    items = get_subscription_obj(body["eventName"], body["entityName"], body["databaseId"], body["entityId"])

    #Lookup users email
    emails = []
    for subscriber in body["subscribers"]:
        email = get_userProfile_Email(subscriber)
        if email == "INVALID_FORMAT":
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": f"Subscriber {subscriber} does not have a valid email to use."})
            return response
        else:
            emails.append(email)

    if not items:
        subscription_table.put_item(
            Item={
                **_row_key(body),
                'entityName': body["entityName"],
                'databaseId': body["databaseId"],
                'entityId': body["entityId"],
                'subscribers': body["subscribers"]
            }
        )

        if body["entityName"] == "Asset":
            logger.info("creating subscription")
            create_sns_subscriptions(body["databaseId"], body["entityId"], emails)

    else:
        existing_subscribers = [item["S"] for item in items["subscribers"]['L']]
        if any(new_subscriber in existing_subscribers for new_subscriber in body["subscribers"]):
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Subscription already exists for some of the specified subscribers."})
            return response
        else:
            if body["entityName"] == "Asset":
                create_sns_subscriptions(body["databaseId"], body["entityId"], emails)

            subscription_table.update_item(
                Key=_row_key(body),
                UpdateExpression='SET subscribers = :subscribers',
                ExpressionAttributeValues={
                    ':subscribers': existing_subscribers + body["subscribers"]
                }
            )

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


def update_subscription(body):
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    subscription_table = dynamodb.Table(subscription_table_name)
    
    # Validate subscription fields against allowed values
    try:
        validate_subscription_fields(body)
    except ValueError as e:
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": str(e)})
        return response
    
    items = get_subscription_obj(body["eventName"], body["entityName"], body["databaseId"], body["entityId"])

    if not items:
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "Subscription does not exists for eventName."})
        return response

    existing_subscribers = [item["S"] for item in items["subscribers"]['L']]
    new_subscribers = body["subscribers"]
    deleted_subscribers = set(existing_subscribers) - set(new_subscribers)
    added_subscribers = set(new_subscribers) - set(existing_subscribers)

    #Lookup users email
    emailsAdded = []
    emailsDeleted = []
    for subscriber in added_subscribers:
        email = get_userProfile_Email(subscriber)
        if email == "INVALID_FORMAT":
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": f"Subscriber {subscriber} does not have a valid email to use."})
            return response
        else:
            emailsAdded.append(email)
    for subscriber in deleted_subscribers:
        email = get_userProfile_Email(subscriber)
        if email == "INVALID_FORMAT":
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": f"Subscriber {subscriber} does not have a valid email to use."})
            return response
        else:
            emailsDeleted.append(email)

    subscription_table.update_item(
        Key=_row_key(body),
        UpdateExpression='SET subscribers = :subscribers',
        ExpressionAttributeValues={
            ':subscribers': body["subscribers"]
        }
    )

    if body["entityName"] == "Asset":
        create_sns_subscriptions(body["databaseId"], body["entityId"], list(emailsAdded))
        delete_sns_subscriptions(body["databaseId"], body["entityId"], list(emailsDeleted), delete_sns=False)

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


def delete_subscription(body):
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    subscription_table = dynamodb.Table(subscription_table_name)
    try:
        subscription_table.delete_item(
            Key=_row_key(body),
            ConditionExpression='attribute_exists(#pk) AND attribute_exists(#sk)',
            ExpressionAttributeNames={'#pk': SUBSCRIPTION_PARTITION_KEY, '#sk': SUBSCRIPTION_SORT_KEY}
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Subscription not found for the specified event and entity"})
        else:
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "An unexpected error occurred while executing the request"})
        return response


    if body["entityName"] == "Asset":
        delete_sns_subscriptions(body["databaseId"], body["entityId"], None, delete_sns=True)

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


def _log_subscription_write(event, action, result):
    """Record a subscription write that succeeded and hand its response back."""
    if result.get('statusCode') != 200:
        return result
    body = event['body']
    subscribers = body.get('subscribers') or []
    log_action(event, action, database_id=body.get('databaseId'), asset_id=body.get('entityId'),
               subscriptionId=subscription_audit_id(body.get('eventName'), body.get('entityName'),
                                                    body.get('databaseId'), body.get('entityId')),
               item_count=len(subscribers),
               changes={"eventName": body.get('eventName'), "entityName": body.get('entityName'),
                        "subscribers": subscribers})
    return result


@audited_handler
def lambda_handler(event, context):
    normalize_event(event)
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    try:
        httpMethod = event['requestContext']['http']['method']

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        queryParameters = event.get('queryStringParameters', {})
        validate_pagination_info(queryParameters)

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

        #Handle GET request
        if httpMethod == 'GET':
            return get_subscriptions(queryParameters, event=event)
        
        # Parse request body
        if not event.get('body'):
            message = 'Request body is required'
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            logger.error(response)
            return response

        #Expect body from this point forward and non-GET requests
        if isinstance(event['body'], str):
            event['body'] = json.loads(event['body'])

        body = event['body']
        if not isinstance(body, dict) or not body.get("eventName") or not body.get("entityName") \
                or not body.get("entityId") or not body.get("databaseId") or not body.get("subscribers"):
            message = "eventName, entityName, entityId, databaseId and subscribers are required fields."
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        # The subscriber ids key the subscription row and are looked up in the user table for
        # their e-mail addresses, so the normalized form is what is validated and stored
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

        if body["entityName"] != "Asset":
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "EntityName provided not supported for subscriptions"})
            return response

        # Tier 2 on the asset of the named database, decided before its existence is revealed. Every
        # subscription write is a POST on the asset, a delete included.
        database_id, asset_id = body["databaseId"], body["entityId"]
        asset_object = get_asset_object_from_id(database_id, asset_id)
        authorization_object = asset_authorization_object(asset_object, database_id, asset_id)
        if not casbin_enforcer.enforce(authorization_object, "POST"):
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
        if asset_object is None:
            response['statusCode'] = 404
            response['body'] = json.dumps({"message": "Asset not found"})
            return response

        if httpMethod == 'POST':
            return _log_subscription_write(event, "subscriptionCreate", create_subscription(body))
        if httpMethod == 'PUT':
            return _log_subscription_write(event, "subscriptionUpdate", update_subscription(body))
        if httpMethod == 'DELETE':
            return _log_subscription_write(event, "subscriptionDelete", delete_subscription(body))
        response['statusCode'] = 405
        response['body'] = json.dumps({"message": "Method not allowed"})
        return response
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
