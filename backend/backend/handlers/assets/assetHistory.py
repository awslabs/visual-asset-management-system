# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import boto3
import json
import base64
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.resourceNames import ResourceKeys, get_table_name
from common.apiRoutes import API_GET_ASSET_HISTORY
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.assetHistory import (
    GetAssetHistoryRequestModel,
    AssetHistoryRecordModel,
    GetAssetHistoryResponseModel,
)

# Configure AWS clients with retry configuration
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="AssetHistory")

# Global variables for claims and roles
claims_and_roles = {}

# Load resource names
try:
    asset_history_table_name = get_table_name(ResourceKeys.ASSET_HISTORY_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

history_table = dynamodb.Table(asset_history_table_name)
asset_table = dynamodb.Table(asset_table_name)


def get_asset_for_authorization(database_id, asset_id):
    """Fetch the asset record for object-level enforcement, checking the live
    partition first and the archived partition second. Returns None when the
    asset does not exist in either (e.g. permanently deleted)."""
    response = asset_table.get_item(Key={'databaseId': database_id, 'assetId': asset_id})
    item = response.get('Item')
    if not item:
        response = asset_table.get_item(
            Key={'databaseId': f"{database_id}#deleted", 'assetId': asset_id}
        )
        item = response.get('Item')
    return item


def get_asset_history(event, database_id, asset_id, request_model):
    """Query the asset history table for one asset, newest first, one page."""
    query_kwargs = {
        'KeyConditionExpression': Key('databaseId:assetId').eq(f"{database_id}:{asset_id}"),
        'ScanIndexForward': False,
        'Limit': request_model.pageSize,
    }
    if request_model.startingToken:
        try:
            query_kwargs['ExclusiveStartKey'] = json.loads(
                base64.b64decode(request_model.startingToken).decode('utf-8')
            )
        except Exception:
            logger.warning("Invalid startingToken provided")
            return validation_error(body={'message': "Invalid startingToken"}, event=event)

    response = history_table.query(**query_kwargs)

    records = [
        AssetHistoryRecordModel(**item) for item in response.get('Items', [])
    ]
    next_token = None
    if 'LastEvaluatedKey' in response:
        next_token = base64.b64encode(
            json.dumps(response['LastEvaluatedKey']).encode('utf-8')
        ).decode('utf-8')

    response_model = GetAssetHistoryResponseModel(Items=records, NextToken=next_token)
    body = {'message': 'Success'}
    body.update(response_model.dict())
    return success(body=body)


def handle_get_request(event):
    """Handle GET requests for asset history"""
    path = event['requestContext']['http']['path']
    path_params = event.get('pathParameters', {}) or {}
    query_params = event.get('queryStringParameters', {}) or {}

    if not API_GET_ASSET_HISTORY.matches(path):
        return validation_error(body={'message': "Route not found"}, event=event)

    database_id = path_params.get('databaseId')
    asset_id = path_params.get('assetId')

    (valid, message) = validate({
        'databaseId': {'value': database_id, 'validator': 'ID'},
        'assetId': {'value': asset_id, 'validator': 'ASSET_ID'},
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message}, event=event)

    request_model = parse({
        'pageSize': query_params.get('pageSize', 100),
        'startingToken': query_params.get('startingToken'),
    }, model=GetAssetHistoryRequestModel)

    # Object-level authorization against the asset record (live, then
    # archived). History of a permanently deleted asset is not exposed via
    # the API until an asset with the same ID exists again.
    asset = get_asset_for_authorization(database_id, asset_id)
    if not asset:
        return general_error(status_code=404, body={'message': "Asset not found"}, event=event)

    asset['object__type'] = 'asset'
    casbin_enforcer = CasbinEnforcer(claims_and_roles)
    if not casbin_enforcer.enforce(asset, "GET"):
        return authorization_error()

    return get_asset_history(event, database_id, asset_id, request_model)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset history APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        method = event['requestContext']['http']['method']

        # Check API authorization
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if method == 'GET':
            return handle_get_request(event)
        else:
            return validation_error(body={'message': "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
