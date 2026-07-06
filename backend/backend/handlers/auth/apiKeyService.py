# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import boto3
import json
import uuid
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from botocore.config import Config
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.apiRoutes import (
    API_AUTH_API_KEY_BY_ID,
    API_AUTH_USER_API_KEYS,
    API_AUTH_USER_API_KEY_BY_ID,
)
from common.resourceNames import get_table_name, ResourceKeys
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.apiKeys import (
    CreateApiKeyRequestModel, UpdateApiKeyRequestModel,
    CreateUserApiKeyRequestModel, UpdateUserApiKeyRequestModel,
    USER_API_KEY_MAX_EXPIRATION_DAYS, parse_iso8601_datetime,
)
from common.dynamodb import to_update_expr
from customLogging.auditLogging import log_auth_changes

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="ApiKeyService")

claims_and_roles = {}

try:
    api_key_table_name = get_table_name(ResourceKeys.API_KEY_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving API key table name")
    api_key_table_name = None

try:
    user_roles_table_name = get_table_name(ResourceKeys.USER_ROLES_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving user roles table name")
    user_roles_table_name = None

api_key_table = dynamodb.Table(api_key_table_name) if api_key_table_name else None
user_roles_table = dynamodb.Table(user_roles_table_name) if user_roles_table_name else None


def _extract_api_key_id(event, path):
    """Get the apiKeyId from path parameters (the last path segment as fallback)."""
    return (event.get('pathParameters') or {}).get('apiKeyId') or path.rsplit('/', 1)[-1]


def _is_user_scope(path):
    """True when the request arrived on the user-level self-service routes
    (/auth/user/api-keys...). User scope restricts all operations to the
    requesting user's own keys and enforces mandatory expiration."""
    return API_AUTH_USER_API_KEYS.matches(path) or API_AUTH_USER_API_KEY_BY_ID.matches(path)


def _requesting_user_id():
    """The authenticated user the request is acting as."""
    return claims_and_roles['tokens'][0] if claims_and_roles.get('tokens') else None


def _max_user_expiration(created_at_iso):
    """Latest allowed expiration for a user key: creation + the max window."""
    return parse_iso8601_datetime(created_at_iso) + timedelta(days=USER_API_KEY_MAX_EXPIRATION_DAYS)


def _validate_user_expiration(expires_at_iso, created_at_iso, event):
    """Validate a user-scope expiration against the key's creation time.

    Returns an error response when invalid, or None when valid. The expiration
    must be in the future and no later than USER_API_KEY_MAX_EXPIRATION_DAYS
    after the key's original creation.
    """
    expires_at = parse_iso8601_datetime(expires_at_iso)
    now = datetime.now(timezone.utc)
    if expires_at <= now:
        return validation_error(
            body={'message': 'Expiration date must be in the future'}, event=event)
    if expires_at > _max_user_expiration(created_at_iso):
        return validation_error(
            body={'message': f"Expiration date cannot be more than {USER_API_KEY_MAX_EXPIRATION_DAYS} days "
                             f"after the key's creation date. Create a new key to extend access."},
            event=event)
    return None


def handle_get(event, path):
    user_scope = _is_user_scope(path)
    if API_AUTH_API_KEY_BY_ID.matches(path) or API_AUTH_USER_API_KEY_BY_ID.matches(path):
        return get_api_key(event, _extract_api_key_id(event, path), user_scope=user_scope)
    else:
        return list_api_keys(event, user_scope=user_scope)


def handle_post(event, path):
    if _is_user_scope(path):
        return create_user_api_key(event)
    return create_api_key(event)


def handle_put(event, path):
    user_scope = _is_user_scope(path)
    if not (API_AUTH_API_KEY_BY_ID.matches(path) or API_AUTH_USER_API_KEY_BY_ID.matches(path)):
        return validation_error(body={'message': 'apiKeyId is required'}, event=event)
    return update_api_key(event, _extract_api_key_id(event, path), user_scope=user_scope)


def handle_delete(event, path):
    user_scope = _is_user_scope(path)
    if not (API_AUTH_API_KEY_BY_ID.matches(path) or API_AUTH_USER_API_KEY_BY_ID.matches(path)):
        return validation_error(body={'message': 'apiKeyId is required'}, event=event)
    return delete_api_key(event, _extract_api_key_id(event, path), user_scope=user_scope)


def list_api_keys(event, user_scope=False):
    try:
        response = api_key_table.scan()
        items = response.get('Items', [])

        if user_scope:
            # User scope: only the requesting user's own keys
            user_id = _requesting_user_id()
            items = [item for item in items if item.get('userId') == user_id]

        for item in items:
            item.pop('apiKeyHash', None)

        return success(body={'Items': items})
    except Exception as e:
        logger.exception(f"Error listing API keys: {e}")
        return internal_error(event=event)


def get_api_key(event, api_key_id, user_scope=False):
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

        if user_scope and item.get('userId') != _requesting_user_id():
            # Do not reveal the existence of other users' keys
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


def create_user_api_key(event):
    """Create a self-service API key tied to the requesting user.

    The key is always created for the authenticated caller (no userId in the
    request), requires an expiration date, and the expiration may be at most
    USER_API_KEY_MAX_EXPIRATION_DAYS from creation.
    """
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

    request = parse(body, model=CreateUserApiKeyRequestModel)

    user_id = _requesting_user_id()
    if not user_id:
        return authorization_error()

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()

    # Enforce the user-key expiration window from creation time
    error_response = _validate_user_expiration(request.expiresAt, now, event)
    if error_response is not None:
        return error_response

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

    # Verify the requesting user has roles
    roles_response = user_roles_table.query(
        KeyConditionExpression=Key('userId').eq(user_id)
    )
    if not roles_response.get('Items'):
        return validation_error(
            body={'message': "Your user has no roles assigned. Cannot create an API key."},
            event=event
        )

    # Generate the API key (same storage shape as admin-created keys)
    raw_key = "vams_" + secrets.token_urlsafe(48)
    key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
    api_key_id = str(uuid.uuid4())

    item = {
        'apiKeyId': api_key_id,
        'apiKeyName': request.apiKeyName,
        'apiKeyHash': key_hash,
        'description': request.description,
        'userId': user_id,
        'createdBy': user_id,
        'createdAt': now,
        'updatedAt': now,
        'expiresAt': request.expiresAt,
        'isActive': 'true',
    }

    try:
        api_key_table.put_item(Item=item)
        logger.info(f"User API key created: {api_key_id}")

        # AUDIT LOG: API key created (user self-service)
        log_auth_changes(event, "apiKeyCreate", {
            "apiKeyId": api_key_id,
            "apiKeyName": request.apiKeyName,
            "userId": user_id,
            "expiresAt": request.expiresAt,
            "operation": "create",
            "scope": "user"
        })

        # Return the key only once — remove hash from response, add plaintext key
        response_item = {k: v for k, v in item.items() if k != 'apiKeyHash'}
        response_item['apiKey'] = raw_key

        return success(status_code=200, body=response_item)
    except Exception as e:
        logger.exception(f"Error creating user API key: {e}")
        return internal_error(event=event)


def update_api_key(event, api_key_id, user_scope=False):
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

    request = parse(body, model=UpdateUserApiKeyRequestModel if user_scope else UpdateApiKeyRequestModel)

    # Fetch existing item
    response = api_key_table.get_item(Key={'apiKeyId': api_key_id})
    item = response.get('Item')
    if not item:
        return general_error(body={'message': 'API key not found'}, event=event)

    if user_scope:
        # User scope: only the owner may modify, and any expiration change must
        # stay within the max window from the key's ORIGINAL creation date.
        if item.get('userId') != _requesting_user_id():
            return general_error(body={'message': 'API key not found'}, event=event)
        if request.expiresAt is not None:
            created_at = item.get('createdAt') or datetime.now(timezone.utc).isoformat()
            error_response = _validate_user_expiration(request.expiresAt, created_at, event)
            if error_response is not None:
                return error_response

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


def delete_api_key(event, api_key_id, user_scope=False):
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

    if user_scope and item.get('userId') != _requesting_user_id():
        # Do not reveal the existence of other users' keys
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
            return handle_post(event, path)
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
