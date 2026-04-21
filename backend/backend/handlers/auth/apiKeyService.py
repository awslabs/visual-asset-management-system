# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
import secrets
import hashlib
from datetime import datetime, timezone
from botocore.config import Config
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.apiKeys import CreateApiKeyRequestModel, UpdateApiKeyRequestModel
from common.dynamodb import to_update_expr
from customLogging.auditLogging import log_auth_changes

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="ApiKeyService")

claims_and_roles = {}

try:
    api_key_table_name = os.environ["API_KEY_STORAGE_TABLE_NAME"]
    user_roles_table_name = os.environ["USER_ROLES_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

api_key_table = dynamodb.Table(api_key_table_name)
user_roles_table = dynamodb.Table(user_roles_table_name)


def handle_get(event, path):
    if '/api-keys/' in path:
        api_key_id = path.split('/api-keys/')[-1]
        return get_api_key(event, api_key_id)
    else:
        return list_api_keys(event)


def handle_post(event):
    return create_api_key(event)


def handle_put(event, path):
    if '/api-keys/' not in path:
        return validation_error(body={'message': 'apiKeyId is required'}, event=event)
    api_key_id = path.split('/api-keys/')[-1]
    return update_api_key(event, api_key_id)


def handle_delete(event, path):
    if '/api-keys/' not in path:
        return validation_error(body={'message': 'apiKeyId is required'}, event=event)
    api_key_id = path.split('/api-keys/')[-1]
    return delete_api_key(event, api_key_id)


def list_api_keys(event):
    try:
        response = api_key_table.scan()
        items = response.get('Items', [])

        for item in items:
            item.pop('apiKeyHash', None)

        return success(body={'Items': items})
    except Exception as e:
        logger.exception(f"Error listing API keys: {e}")
        return internal_error(event=event)


def get_api_key(event, api_key_id):
    (valid, message) = validate({
        'apiKeyId': {'value': api_key_id, 'validator': 'UUID'}
    })
    if not valid:
        return validation_error(body={'message': message}, event=event)

    try:
        response = api_key_table.get_item(Key={'apiKeyId': api_key_id})
        item = response.get('Item')
        if not item:
            return general_error(body={'message': 'API key not found'}, event=event)

        item.pop('apiKeyHash', None)
        return success(body=item)
    except Exception as e:
        logger.exception(f"Error getting API key: {e}")
        return internal_error(event=event)


def create_api_key(event):
    body = event.get('body')
    if not body:
        return validation_error(body={'message': 'Request body is required'}, event=event)

    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as e:
            logger.exception(f"Invalid JSON in request body: {e}")
            return validation_error(body={'message': 'Invalid JSON in request body'}, event=event)
    elif not isinstance(body, dict):
        return validation_error(body={'message': 'Request body cannot be parsed'}, event=event)

    request = parse(body, model=CreateApiKeyRequestModel)

    # Check for duplicate API key name
    existing_keys = api_key_table.scan(
        FilterExpression='apiKeyName = :name',
        ExpressionAttributeValues={':name': request.apiKeyName}
    )
    if existing_keys.get('Items'):
        return validation_error(
            body={'message': f"An API key with the name '{request.apiKeyName}' already exists. Please choose a different name."},
            event=event
        )

    # Verify userId has roles
    user_id = request.userId
    roles_response = user_roles_table.query(
        KeyConditionExpression=Key('userId').eq(user_id)
    )
    if not roles_response.get('Items'):
        return validation_error(
            body={'message': f"User '{user_id}' has no roles assigned. Cannot create API key for a user without roles."},
            event=event
        )

    # Generate the API key
    raw_key = "vams_" + secrets.token_urlsafe(48)
    key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
    api_key_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    item = {
        'apiKeyId': api_key_id,
        'apiKeyName': request.apiKeyName,
        'apiKeyHash': key_hash,
        'description': request.description,
        'userId': request.userId,
        'createdBy': claims_and_roles['tokens'][0] if claims_and_roles['tokens'] else 'unknown',
        'createdAt': now,
        'updatedAt': now,
        'expiresAt': request.expiresAt or '',
        'isActive': 'true',
    }

    try:
        api_key_table.put_item(Item=item)
        logger.info(f"API key created: {api_key_id}")

        # AUDIT LOG: API key created
        log_auth_changes(event, "apiKeyCreate", {
            "apiKeyId": api_key_id,
            "apiKeyName": request.apiKeyName,
            "userId": request.userId,
            "expiresAt": request.expiresAt or '',
            "operation": "create"
        })

        # Return the key only once — remove hash from response, add plaintext key
        response_item = {k: v for k, v in item.items() if k != 'apiKeyHash'}
        response_item['apiKey'] = raw_key

        return success(status_code=200, body=response_item)
    except Exception as e:
        logger.exception(f"Error creating API key: {e}")
        return internal_error(event=event)


def update_api_key(event, api_key_id):
    (valid, message) = validate({
        'apiKeyId': {'value': api_key_id, 'validator': 'UUID'}
    })
    if not valid:
        return validation_error(body={'message': message}, event=event)

    body = event.get('body')
    if not body:
        return validation_error(body={'message': 'Request body is required'}, event=event)

    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as e:
            logger.exception(f"Invalid JSON in request body: {e}")
            return validation_error(body={'message': 'Invalid JSON in request body'}, event=event)
    elif not isinstance(body, dict):
        return validation_error(body={'message': 'Request body cannot be parsed'}, event=event)

    request = parse(body, model=UpdateApiKeyRequestModel)

    # Fetch existing item
    response = api_key_table.get_item(Key={'apiKeyId': api_key_id})
    item = response.get('Item')
    if not item:
        return general_error(body={'message': 'API key not found'}, event=event)

    now = datetime.now(timezone.utc).isoformat()
    update_fields = {'updatedAt': now}
    if request.description is not None:
        update_fields['description'] = request.description
    if request.expiresAt is not None:
        update_fields['expiresAt'] = request.expiresAt
    if request.isActive is not None:
        update_fields['isActive'] = request.isActive

    try:
        keys_map, values_map, expr = to_update_expr(update_fields)
        api_key_table.update_item(
            Key={'apiKeyId': api_key_id},
            UpdateExpression=expr,
            ExpressionAttributeNames=keys_map,
            ExpressionAttributeValues=values_map
        )

        # AUDIT LOG: API key updated
        log_auth_changes(event, "apiKeyUpdate", {
            "apiKeyId": api_key_id,
            "updatedFields": list(update_fields.keys()),
            "operation": "update"
        })

        # Return updated item
        updated = api_key_table.get_item(Key={'apiKeyId': api_key_id})
        updated_item = updated.get('Item', {})
        updated_item.pop('apiKeyHash', None)
        return success(body=updated_item)
    except Exception as e:
        logger.exception(f"Error updating API key: {e}")
        return internal_error(event=event)


def delete_api_key(event, api_key_id):
    (valid, message) = validate({
        'apiKeyId': {'value': api_key_id, 'validator': 'UUID'}
    })
    if not valid:
        return validation_error(body={'message': message}, event=event)

    # Fetch existing item
    response = api_key_table.get_item(Key={'apiKeyId': api_key_id})
    item = response.get('Item')
    if not item:
        return general_error(body={'message': 'API key not found'}, event=event)

    try:
        api_key_table.delete_item(Key={'apiKeyId': api_key_id})
        logger.info(f"API key deleted: {api_key_id}")

        # AUDIT LOG: API key deleted
        log_auth_changes(event, "apiKeyDelete", {
            "apiKeyId": api_key_id,
            "apiKeyName": item.get('apiKeyName', ''),
            "userId": item.get('userId', ''),
            "operation": "delete"
        })

        return success(body={'message': f"API key '{api_key_id}' deleted successfully"})
    except Exception as e:
        logger.exception(f"Error deleting API key: {e}")
        return internal_error(event=event)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if method == 'GET':
            return handle_get(event, path)
        elif method == 'POST':
            return handle_post(event)
        elif method == 'PUT':
            return handle_put(event, path)
        elif method == 'DELETE':
            return handle_delete(event, path)
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
