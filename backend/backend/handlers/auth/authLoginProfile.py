# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import boto3
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from customConfigCommon.customAuthLoginProfile import customAuthProfileLoginWriteOverride
from handlers.auth import request_to_claims
from common.resourceNames import get_table_name, ResourceKeys
from common.validators import validate
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_auth_other
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.authLoginProfile import UpdateLoginProfileRequestModel

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="AuthLoginProfile")

claims_and_roles = {}

try:
    user_table_name = get_table_name(ResourceKeys.USER_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving user table name")
    user_table_name = None

user_table = dynamodb.Table(user_table_name) if user_table_name else None


def create_update_user(userId, email, lambdaRequestEvent):
    """Create or update the user's stored profile, applying any organization override."""
    userProfile = {
        'userId': userId,
        'email': email,
    }

    # Override with any custom organization profile information
    userProfileO = customAuthProfileLoginWriteOverride(userProfile, lambdaRequestEvent)

    # Sanity check the override result
    if userProfileO is None or not isinstance(userProfileO, dict):
        userProfileO = userProfile

    # Ensure userId was not altered by the override
    userProfileO['userId'] = userId

    user_table.put_item(Item=userProfileO)
    return userProfileO


def get_user(userId):
    """Return the stored profile for userId, or an identity-only profile when none exists."""
    response = user_table.get_item(Key={'userId': userId})
    # A user with no stored profile (e.g. not yet assigned any roles) has no item;
    # return the identity so login can still proceed.
    return response.get("Item") or {'userId': userId}


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles

    try:
        claims_and_roles = request_to_claims(event)
        authorizerUserId = None
        if len(claims_and_roles["tokens"]) > 0:
            authorizerUserId = claims_and_roles["tokens"][0]

        pathParameters = event.get('pathParameters') or {}
        pathUserId = pathParameters.get('userId', "")

        method = event['requestContext']['http']['method']

        # Validate the path userId
        (valid, message) = validate({
            'userId': {
                'value': pathUserId,
                'validator': 'USERID'
            }
        })
        if not valid:
            return validation_error(body={'message': message}, event=event)

        # SELF-USER ROUTE: when the path userId matches the caller, auto-authorize.
        # Users may not be in the roles system yet but must still read/update their profile.
        if pathUserId and method in ("POST", "GET") and authorizerUserId == pathUserId:
            if method == "POST":
                email = ""
                body = event.get('body')
                if body:
                    if isinstance(body, str):
                        body = json.loads(body)
                    request = parse(body, model=UpdateLoginProfileRequestModel)
                    email = request.email or ""
                profile = create_update_user(pathUserId, email, event)

                # AUDIT LOG: User profile created/updated
                log_auth_other(event, "userProfileUpdate", {
                    "userId": pathUserId,
                    "operation": "create_update",
                    "selfAccess": True
                })
                return success(body=profile)

            # GET
            profile = get_user(pathUserId)

            # AUDIT LOG: User profile retrieved
            log_auth_other(event, "userProfileGet", {
                "userId": pathUserId,
                "operation": "get",
                "selfAccess": True
            })
            return success(body=profile)

        # ADMINISTRATION ROUTE (viewing another user) is not implemented yet.
        return authorization_error()

    except json.JSONDecodeError as e:
        logger.exception(f"Invalid JSON in request body: {e}")
        return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
