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
from common.dynamodb import get_asset_object_from_id, query_all_items
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer

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


def get_asset_object_for_entity(entity_id):
    """Resolve the asset a subscription row points at, annotated for authorization.

    Returns None when the assetId resolves to no live asset, or to more than one asset and
    so cannot be attributed to a single database. Such a row cannot be authorized against
    an asset and is left out of the listing.
    """
    try:
        asset_object = get_asset_object_from_id(None, entity_id)
    except VAMSGeneralErrorResponse as e:
        logger.warning(f"Could not resolve the asset for subscription entity {entity_id}: {e}")
        return None

    if asset_object is None:
        logger.info(f"Subscription entity {entity_id} has no live asset")
        return None

    asset_object.update({"object__type": "asset"})
    return asset_object


def get_subscriptions(query_params):
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

    output_objects = []
    #Each distinct entityId is resolved once and reused across every row that references
    #it, so a page of subscriptions costs one asset lookup per asset rather than per row
    resolved_assets = {}
    for obj in page_iterator.get('Items', []):
        deserialized_document = {k: deserializer.deserialize(v) for k, v in obj.items()}
        entity_name, entity_id = deserialized_document["entityName_entityId"].split("#")
        output_obj = {
            "eventName": deserialized_document["eventName"],
            "entityName": entity_name,
            "entityId": entity_id,
            "subscribers": deserialized_document["subscribers"]
        }

        # Add Casbin Enforcer to check if the user has access to GET subscription of specific Assets
        if entity_id not in resolved_assets:
            resolved_assets[entity_id] = get_asset_object_for_entity(entity_id)

        asset_object = resolved_assets[entity_id]
        #A row with no asset to authorize against is dropped; the rest of the page still returns
        if asset_object is None:
            continue

        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(asset_object, "GET"):
                output_objects.append(output_obj)

    result = {
        "Items": []
    }

    result["Items"] = [
        {
            "eventName": obj["eventName"],
            "entityName": obj["entityName"],
            "entityId": obj["entityId"],
            "subscribers": obj["subscribers"],
            "entityValue": resolved_assets[obj["entityId"]].get("assetName") if obj["entityName"] == "Asset" else None,
            "databaseId": resolved_assets[obj["entityId"]].get("databaseId") if obj["entityName"] == "Asset" else None
        }
        for obj in output_objects
    ]

    if 'NextToken' in page_iterator:
        result['NextToken'] = page_iterator['NextToken']

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": result})
    return response


def create_sns_topic(asset_id, database_id):
    topic_response = sns_client.create_topic(Name=f'AssetTopic{database_id}-{asset_id}')
    return topic_response['TopicArn']


def add_sns_topic_in_asset(asset_id, database_id, sns_topic):
    asset_table = dynamodb.Table(asset_table_name)
    resp = asset_table.query(
        KeyConditionExpression='assetId = :asset_id AND databaseId = :databaseId',
        ExpressionAttributeValues={':asset_id': asset_id, ':databaseId': database_id}
    )
    items = resp.get('Items', [])
    if not items:
        logger.error(f"No asset found - {asset_id}.")
        return

    # Conditional on the record still existing: the asset may be archived or
    # deleted between the query above and this update, and an unconditional
    # update_item would re-create a phantom record containing only the key
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
    else:
        resp = sns_client.list_subscriptions_by_topic(TopicArn=asset_obj.get("snsTopic"))
        subscription_arns = [subscription['SubscriptionArn'] for subscription in resp['Subscriptions'] if subscription['Endpoint'] in subscribers]

        for subscription_arn in subscription_arns:
            if subscription_arn != "PendingConfirmation":
                sns_client.unsubscribe(SubscriptionArn=subscription_arn)

    return


def create_sns_subscriptions(asset_id, emails):
    asset_obj = get_asset(asset_id)

    # The subscription row is written before this runs, so an assetId that no longer resolves
    # to one live asset is reported rather than dereferenced into a 500
    if asset_obj is None:
        logger.error(f"No live asset found for asset {asset_id}")
        raise VAMSGeneralErrorResponse("Asset could not be resolved for this subscription.")

    asset_sns_topic = asset_obj.get("snsTopic")

    if not asset_sns_topic:
        asset_sns_topic = create_sns_topic(asset_id, asset_obj["databaseId"])
        add_sns_topic_in_asset(asset_id, asset_obj["databaseId"], asset_sns_topic)

    for subscriber in emails:
        sns_client.subscribe(
            TopicArn=asset_sns_topic,
            Protocol='email',
            Endpoint=f'{subscriber}'
        )


def get_subscription_obj(event_name, entity_name, entity_id):
    resp = dynamodb_client.get_item(
        TableName=subscription_table_name,
        Key={
            'eventName': {'S': event_name},
            'entityName_entityId': {'S': f'{entity_name}#{entity_id}'}
        }
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
    
    items = get_subscription_obj(body["eventName"], body["entityName"], body["entityId"])

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
                'eventName': body["eventName"],
                'entityName_entityId': f'{body["entityName"]}#{body["entityId"]}',
                'subscribers': body["subscribers"]
            }
        )

        if body["entityName"] == "Asset":
            logger.info("creating subscription")
            create_sns_subscriptions(body["entityId"], emails)

    else:
        existing_subscribers = [item["S"] for item in items["subscribers"]['L']]
        if any(new_subscriber in existing_subscribers for new_subscriber in body["subscribers"]):
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Subscription already exists for some of the specified subscribers."})
            return response
        else:
            if body["entityName"] == "Asset":
                create_sns_subscriptions(body["entityId"], emails)

            subscription_table.update_item(
                Key={
                    'eventName': body["eventName"],
                    'entityName_entityId': f'{body["entityName"]}#{body["entityId"]}'
                },
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
    
    items = get_subscription_obj(body["eventName"], body["entityName"], body["entityId"])

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
        Key={
            'eventName': body["eventName"],
            'entityName_entityId': f'{body["entityName"]}#{body["entityId"]}'
        },
        UpdateExpression='SET subscribers = :subscribers',
        ExpressionAttributeValues={
            ':subscribers': body["subscribers"]
        }
    )

    if body["entityName"] == "Asset":
        create_sns_subscriptions(body["entityId"], list(emailsAdded))
        delete_sns_subscriptions(body["entityId"], list(emailsDeleted), delete_sns=False)

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


def delete_subscription(body):
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    subscription_table = dynamodb.Table(subscription_table_name)
    try:
        subscription_table.delete_item(
            Key={
                'eventName': body["eventName"],
                'entityName_entityId': f'{body["entityName"]}#{body["entityId"]}'
            },
            ConditionExpression='attribute_exists(eventName) AND attribute_exists(entityName_entityId)'
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
        delete_sns_subscriptions(body["entityId"], None, delete_sns=True)

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


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
            return get_subscriptions(queryParameters)
        
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

        if not event['body'].get("eventName") or not event['body'].get("entityName") or not event['body'].get("entityId") or not event['body'].get("subscribers"):
            message = "eventName, entityName and entityId are required fields."
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        # The subscriber ids key the subscription row and are looked up in the user table for
        # their e-mail addresses, so the normalized form is what is validated and stored
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

        if event['body']["entityName"] == "Asset":
            allowed = False
            asset_object = get_asset_object_from_id(None, event['body']["entityId"])
            if asset_object is None:
                response['statusCode'] = 404
                response['body'] = json.dumps({"message": "Asset not found"})
                return response

            asset_object.update({"object__type": "asset"})

            if len(claims_and_roles["tokens"]) > 0:
                #This is a POST on asset as we are technically only modifying the asset for subscriptions (even a delete subscription)
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(asset_object, "POST"):
                    allowed = True

            if allowed and httpMethod == 'POST':
                return create_subscription(event['body'])
            elif allowed and httpMethod == 'PUT':
                return update_subscription(event['body'])
            elif allowed and httpMethod == 'DELETE':
                return delete_subscription(event['body'])
            else:
                response['statusCode'] = 403
                response['body'] = json.dumps({"message": "Not Authorized"})
                return response
        else:
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "EntityName provided not supported for subscriptions"})
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
