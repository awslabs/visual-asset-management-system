# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
import boto3
import json
import uuid
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from botocore.config import Config
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr, Key
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
    validation_error_message,
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

# GSI on the API key table partitioned by userId (storageBuilder-nestedStack.ts), so a
# user's own keys are read from one partition instead of scanned out of the whole table.
API_KEY_USER_ID_INDEX_NAME = 'userIdIndex'

# Bounds on one API key listing response. The listings are user-facing GETs, so how many
# records a response carries has to be decided here rather than by how many the table holds:
# an API key record is a few hundred bytes, so accumulating every key of a large deployment
# reaches the 6 MB Lambda response limit at roughly twelve thousand of them.
#
# MAX_ITEMS is the per-response record ceiling, and is both the default and a hard cap -- a
# request asking for more is clamped to it, and the remainder is reachable through NextToken.
# It is set high enough that no ordinary deployment reaches it (the admin listing covers every
# key in the deployment) while keeping a several-fold margin under the response limit, matching
# the page sizes the asset and metadata listings default to.
# PAGE_SIZE is the Limit on each DynamoDB read behind one response, so a caller asking for a
# small page does not pay to read more records than it can receive.
API_KEY_LISTING_MAX_ITEMS = 3000
API_KEY_LISTING_PAGE_SIZE = 1000


def _resolve_page_bounds(query_params):
    """(maxItems, pageSize) for one listing response, from the request's query parameters.

    A parameter that is absent, non-numeric or below 1 falls back to its default: a listing is
    a read, and a malformed page hint is not worth failing the request over. maxItems is capped
    at API_KEY_LISTING_MAX_ITEMS so the response stays bounded whatever the caller asks for,
    and pageSize is clamped to maxItems because reading past the response ceiling only spends
    read capacity on records that will not be returned.
    """
    def _positive_int(name, default):
        try:
            value = int(query_params.get(name))
        except (TypeError, ValueError):
            return default
        return value if value >= 1 else default

    max_items = min(_positive_int('maxItems', API_KEY_LISTING_MAX_ITEMS),
                    API_KEY_LISTING_MAX_ITEMS)
    page_size = min(_positive_int('pageSize', API_KEY_LISTING_PAGE_SIZE), max_items)
    return max_items, page_size


#: Value types a DynamoDB cursor attribute can carry. A key attribute is S, N or B and the
#: cursor travels as JSON, so a decoded value is a string or a number. ``bool`` is excluded
#: explicitly because ``isinstance(True, int)`` is true in Python.
_CURSOR_VALUE_TYPES = (str, int, float)

#: Widest a legitimate cursor gets: a table key is one or two attributes, and an index cursor
#: also carries the base table's key.
_CURSOR_MAX_ATTRIBUTES = 4


def _decoded_starting_token(starting_token):
    """The DynamoDB cursor a caller's ``startingToken`` stands for.

    Two ways the parameter can be wrong, and both are the caller's, so both are answered the
    same way -- a 400. It can fail to base64/JSON decode, and it can decode to something that
    is not a cursor at all (``[]``, ``null``, a bare string, an attribute whose value is a
    nested object). Validating only the first leaves the second reaching the read, where
    DynamoDB's rejection surfaces as an internal error 500 for a malformed request parameter.

    Rule 11: neither the token nor its decoded content appears in the message the caller gets;
    the specifics go to the log.
    """
    try:
        decoded = json.loads(base64.b64decode(starting_token).decode('utf-8'))
    except (json.JSONDecodeError, ValueError, TypeError, UnicodeDecodeError) as e:
        logger.exception(f"Invalid startingToken format: {e}")
        raise VAMSGeneralErrorResponse("Invalid pagination token")

    if not isinstance(decoded, dict) or not 1 <= len(decoded) <= _CURSOR_MAX_ATTRIBUTES or any(
            not isinstance(name, str)
            or isinstance(value, bool)
            or not isinstance(value, _CURSOR_VALUE_TYPES)
            for name, value in decoded.items()):
        logger.warning(
            f"Rejected a startingToken that decoded to something other than a DynamoDB "
            f"cursor: {type(decoded).__name__} with "
            f"{len(decoded) if isinstance(decoded, dict) else 'n/a'} attributes")
        raise VAMSGeneralErrorResponse("Invalid pagination token")

    return decoded


def _read_api_key_page(reader, base_kwargs, max_items, page_size, starting_token):
    """One response worth of API key records, plus the cursor for the next one.

    Reads through ``reader`` (the table's ``query`` or ``scan``), ``page_size`` records at a
    time, until ``max_items`` records are in hand or the table is exhausted, and returns the
    DynamoDB cursor to resume from when it stopped early. Both halves matter and neither alone
    is enough:

    * A single read is a partial answer that looks complete — DynamoDB returns at most 1 MB
      per call and reports the rest only through ``LastEvaluatedKey`` — so stopping after one
      read hides keys the owner holds (Rule 14).
    * Reading to exhaustion instead accumulates the whole table in memory for a synchronous
      response (Rule 15), so the accumulation stops at ``max_items`` and the remainder is
      offered as a token rather than silently dropped.

    Continuation is decided by the PRESENCE of ``LastEvaluatedKey``, which is how DynamoDB
    reports the end of a listing and what keeps the loop finite against a stubbed reader whose
    ``.get()`` answers every key with a truthy mock.
    """
    exclusive_start_key = None
    if starting_token:
        exclusive_start_key = _decoded_starting_token(starting_token)

    items = []
    next_key = None
    # True until the caller's own cursor has been read from. A cursor that is shaped like one
    # but names attributes this table has no key for is still the caller's malformed parameter,
    # so DynamoDB's rejection of that first read is reported as a 400 rather than a 500. Later
    # reads resume from cursors this function produced, where the same error is a real fault.
    resuming_from_caller_cursor = exclusive_start_key is not None
    while True:
        read_kwargs = dict(base_kwargs)
        read_kwargs['Limit'] = min(page_size, max_items - len(items))
        if exclusive_start_key:
            read_kwargs['ExclusiveStartKey'] = exclusive_start_key

        try:
            response = reader(**read_kwargs)
        except ClientError as e:
            if (resuming_from_caller_cursor
                    and e.response.get('Error', {}).get('Code') == 'ValidationException'):
                logger.exception(f"startingToken is not a cursor for this listing: {e}")
                raise VAMSGeneralErrorResponse("Invalid pagination token")
            raise
        resuming_from_caller_cursor = False
        items.extend(response.get('Items', []))

        if 'LastEvaluatedKey' not in response:
            next_key = None
            break

        next_key = response['LastEvaluatedKey']
        if len(items) >= max_items:
            break
        exclusive_start_key = next_key

    return items, next_key


def _api_key_name_exists(api_key_name, user_id=None):
    """True when a stored key already carries this name, within the scope asked for.

    ``user_id`` narrows the check to that user's own keys, read through the userId GSI. The
    self-service route passes it, so names are unique per owner there rather than across the
    deployment. Two reasons, and the first is the security one:

    * A deployment-wide check answers, to any unprivileged caller, whether some other user
      holds a given key name — a membership oracle over other users' key names, probed one
      create attempt at a time. It also lets any caller permanently deny a name to everyone
      else.
    * Nothing resolves an API key by name: authentication is by hash and every route addresses
      a key by apiKeyId, so the name is a label for its owner and carries no cross-user
      meaning.

    With no ``user_id`` the check is deployment-wide, which is what the admin route has always
    intended and what its callers (who can already see every key) expect.

    Either scope pages to exhaustion. A filtered read applies its filter after reading up to
    1 MB of items, so a match can sit on any page and no page is evidence about the others;
    stopping at the first one reports "no match" for a name that does exist.
    """
    if user_id is not None:
        reader = api_key_table.query
        read_kwargs = {
            'IndexName': API_KEY_USER_ID_INDEX_NAME,
            'KeyConditionExpression': Key('userId').eq(user_id),
            'FilterExpression': Attr('apiKeyName').eq(api_key_name),
        }
    else:
        reader = api_key_table.scan
        read_kwargs = {
            'FilterExpression': 'apiKeyName = :name',
            'ExpressionAttributeValues': {':name': api_key_name},
        }

    while True:
        response = reader(**read_kwargs)
        if response.get('Items'):
            return True
        if 'LastEvaluatedKey' not in response:
            return False
        read_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


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
    query_params = event.get('queryStringParameters', {}) or {}
    max_items, page_size = _resolve_page_bounds(query_params)

    user_id = None
    if user_scope:
        # User scope: only the requesting user's own keys, read from the userId GSI
        # rather than filtered out of a table-wide scan in Python.
        user_id = _requesting_user_id()
        if not user_id:
            return authorization_error()
        reader = api_key_table.query
        base_kwargs = {
            'IndexName': API_KEY_USER_ID_INDEX_NAME,
            'KeyConditionExpression': Key('userId').eq(user_id),
        }
    else:
        reader = api_key_table.scan
        base_kwargs = {}

    try:
        items, next_key = _read_api_key_page(
            reader, base_kwargs, max_items, page_size, query_params.get('startingToken'))

        if user_scope:
            # Ownership is already the key condition, so this drops nothing in normal
            # operation. It is here so that a wrong index, a GSI whose projection ever
            # changes, or a future edit to the read cannot turn into another user's key
            # appearing in this response.
            items = [item for item in items if item.get('userId') == user_id]

        for item in items:
            item.pop('apiKeyHash', None)

        result = {'Items': items}
        if next_key:
            # More keys remain. Reported as both a token and a flag, so a caller that does
            # not follow the token can still tell the listing is incomplete.
            result['NextToken'] = base64.b64encode(
                json.dumps(next_key).encode('utf-8')).decode('utf-8')
            result['truncated'] = True
            logger.warning("API key listing truncated; more results available via NextToken")

        return success(body=result)
    except VAMSGeneralErrorResponse:
        raise
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

    # Check for duplicate API key name across the deployment (admin scope)
    if _api_key_name_exists(request.apiKeyName):
        logger.info(f"Rejected duplicate API key name for user {request.userId}")
        return validation_error(
            body={'message': "An API key with that name already exists. Please choose a different name."},
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

    # Check for duplicate API key name among the caller's OWN keys. A name another user holds
    # neither blocks this create nor is observable through its rejection (_api_key_name_exists).
    if _api_key_name_exists(request.apiKeyName, user_id=user_id):
        logger.info(f"Rejected duplicate API key name for user {user_id}")
        return validation_error(
            body={'message': "An API key with that name already exists for your user. Please choose a different name."},
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
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
