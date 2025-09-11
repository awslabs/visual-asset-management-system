#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import boto3
import botocore.exceptions
import os
from customConfigCommon.customAuthLoginProfile import customAuthProfileLoginWriteOverride
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from customLogging.logger import safeLogger
from common.validators import validate

logger = safeLogger(service_name="AuthLoginProfile")
dynamodb = boto3.resource('dynamodb')

claims_and_roles = {}
main_rest_response = STANDARD_JSON_RESPONSE

try:
    user_table_name = os.environ["USER_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})

def create_update_user(userId, email, lambdaRequestEvent):

    userProfile = {
            'userId': userId,
            'email': email,
        }

    #Override with any custom organization profile information
    userProfileO = customAuthProfileLoginWriteOverride(userProfile, lambdaRequestEvent)

    #Do some sanity checks
    if userProfileO == None or userProfileO is not dict:
        userProfileO = userProfile

    #Make sure userId wasn't messed with so reset just in case
    userProfileO['userId'] = userId

    user_table = dynamodb.Table(user_table_name)
    user_table.put_item(
        Item=userProfileO
    )

    return {"message": {"Items": [userProfileO]}}

def get_user(userId):
    user_table = dynamodb.Table(user_table_name)
    response = user_table.get_item(
        Key={
            'userId': userId
        }
    )
    return {"message": {"Items": [response["Item"]]}}

def lambda_handler(event, _):
    response = STANDARD_JSON_RESPONSE

    try:
        claims_and_roles = request_to_claims(event)
        authorizerUserId = None
        if len(claims_and_roles["tokens"]) > 0:
            authorizerUserId = claims_and_roles["tokens"][0]

        #Format body but body not required
        if 'body' in event:
            try:
                if isinstance(event['body'], str):
                    event['body'] = json.loads(event['body'])
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                response['statusCode'] = 400
                response['body'] = json.dumps({"message": "Invalid JSON in request body"})
                return response
        else:
            event["body"] = {}

        pathParameters = event.get('pathParameters', {})

        pathUserId = ""
        if 'userId' in pathParameters:
            pathUserId = pathParameters.get('userId')

        emailBody = ""
        if 'email' in event["body"]:
            emailBody = event["body"].get('email')

        method = event['requestContext']['http']['method']

        #Validation Checks
        logger.info("Validating parameters")
        (valid, message) = validate({
            'userId': {
                'value': pathUserId,
                'validator': 'USERID',
                #'optional': True
            },
            'email': {
                'value': emailBody,
                'validator': 'EMAIL',
                'optional': True
            },
        })
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        #Routes
        if(pathUserId and (method == "POST" or method == "GET") and authorizerUserId == pathUserId):
            #SELF-USER ROUTE - If userId and Claims UserId match, auto-authorize (they may not be in the roles systems yet but allow user profile updating)
            logger.info("Authorizer UserId and Path UserId match, auto-authorize")

            if method == "POST":
                #POST
                #Create or update user
                logger.info("Create or update user")
                response["body"] = json.dumps(create_update_user(pathUserId, emailBody, event))
                response["statusCode"] = 200
                return response
            elif method == "GET":
                #GET
                #Get user
                logger.info("Get user")
                response["body"] = json.dumps(get_user(pathUserId))
                response["statusCode"] = 200
                return response

        else:
            #ADMINISTRATION ROUTE
            #logger.info("Authorizer UserId and Path UserId do not match, check roles for administration")
            #TODO - Not done yet so just unauthorized for now
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

    except Exception as e:
        response["statusCode"] = 500
        logger.exception(e)
        response["body"] = json.dumps({"message": "Internal Server Error"})

        return response

