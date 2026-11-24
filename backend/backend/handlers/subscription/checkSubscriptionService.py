#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import copy
import boto3
from botocore.config import Config
import json

from common.constants import STANDARD_JSON_RESPONSE
from common.resourceNames import get_table_name, ResourceKeys
from common.validators import validate, normalize_userid
from handlers.auth import request_to_claims
from common.auth.apiEvent import normalize_event
from handlers.authz import CasbinEnforcer
from common.dynamodb import get_asset_object_from_id, asset_authorization_object
from common.commentSubscriptionKeys import subscription_key
from models.common import VAMSGeneralErrorResponse
from customLogging.logger import safeLogger
from customLogging.auditLogging import audited_handler, log_action

claims_and_roles = {}
logger = safeLogger(service="CheckSubscriptionService")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)

# A required table name that cannot be resolved fails the module load, so the deployment reports it
# at cold start. Degrading to None instead let the module import and turned the failure into a boto3
# error on a None table name for every request afterwards -- a generic 500 naming nothing.
try:
    subscription_table_name = get_table_name(ResourceKeys.SUBSCRIPTIONS_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

# The one event and entity a check answers for
EVENT_NAME = "Asset Version Change"
ENTITY_NAME = "Asset"


def check_subscriptions(body):
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    subscription_table = dynamodb.Table(subscription_table_name)
    item = subscription_table.get_item(
        Key=subscription_key(EVENT_NAME, ENTITY_NAME, body["databaseId"], body["assetId"])
    ).get('Item')

    if item and body["userId"] in (item.get('subscribers') or []):
        response['statusCode'] = 200
        response['body'] = json.dumps({"message": "success"})
    else:
        response['statusCode'] = 200
        response['body'] = json.dumps({"message": "Subscription doesn't exists."})
    return response


@audited_handler
def lambda_handler(event, context):
    normalize_event(event)
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)

    # Parse request body
    if not event.get('body'):
        message = 'Request body is required'
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response

    try:
        if isinstance(event['body'], str):
            event['body'] = json.loads(event['body'])
    except json.JSONDecodeError as e:
        logger.exception(f"Invalid JSON in request body: {e}")
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "Invalid JSON in request body"})
        return response

    try:
        httpMethod = event['requestContext']['http']['method']
        body = event['body']

        if not isinstance(body, dict) or not body.get('userId') or not body.get('assetId') \
                or not body.get('databaseId'):
            message = "userId, assetId and databaseId are required fields."
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        # The stored subscriber ids are normalized, so the id being checked is normalized too
        body['userId'] = normalize_userid(body['userId'])

        (valid, message) = validate({
            'userId': {
                'value': body['userId'],
                'validator': 'USERID'
            },
            'databaseId': {
                'value': body['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': body['assetId'],
                'validator': 'ASSET_ID'
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
        database_id, asset_id = body["databaseId"], body["assetId"]
        asset_object = get_asset_object_from_id(database_id, asset_id)
        authorization_object = asset_authorization_object(asset_object, database_id, asset_id)
        if httpMethod != 'POST' or not casbin_enforcer.enforce(authorization_object, "GET"):
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
        if asset_object is None:
            response['statusCode'] = 404
            response['body'] = json.dumps({"message": "Asset not found"})
            return response

        result = check_subscriptions(body)
        log_action(event, "subscriptionCheck", database_id=database_id, asset_id=asset_id,
                   request_parameters={"userId": body['userId'], "databaseId": database_id,
                                       "assetId": asset_id})
        return result
    except VAMSGeneralErrorResponse as v:
        # Raised by the asset lookup when an id is empty or the read fails. The message names no
        # caller-supplied value, so it is returned as the client-facing reason.
        logger.exception(v)
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": str(v)})
        return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
