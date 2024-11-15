#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json

from common.validators import validate
from common.constants import STANDARD_JSON_RESPONSE
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service="CreateTagType")
dynamodb = boto3.resource('dynamodb')

main_rest_response = STANDARD_JSON_RESPONSE

try:
    tag_type_db_table_name = os.environ["TAG_TYPES_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def create_tag_type(body):
    table = dynamodb.Table(tag_type_db_table_name)
    item = {
        "tagTypeName": body["tagTypeName"],
        "description": body["description"]
    }
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(tagTypeName)")
    return json.dumps({"message": 'Succeeded'})


def update_tag(body):
    table = dynamodb.Table(tag_type_db_table_name)
    table.update_item(
        Key={
            'tagTypeName': body["tagTypeName"]
        },
        UpdateExpression='SET description = :value',
        ExpressionAttributeValues={
            ':value': body["description"]
        }
    )
    return json.dumps({"message": 'Succeeded'})


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE

    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])

    try:
        if 'tagTypeName' not in event['body'] or 'description' not in event['body']:
            message = "TagTypeName and description are required in API Call"
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        (valid, message) = validate({
            'tagTypeName': {
                'value': event['body']['tagTypeName'],
                'validator': 'OBJECT_NAME'
            },
            'description': {
                'value': event['body']['description'],
                'validator': 'STRING_256'
            }
        })

        if not valid:
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        httpMethod = event['requestContext']['http']['method']
        method_allowed_on_api = False
        # Add Casbin Enforcer to check if the current user has permissions to POST/PUT the Tag Types
        tag_type = {
            "object__type": "tagType",
            "tagTypeName": event['body']['tagTypeName']
        }
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", tag_type, httpMethod) and casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
                break

        if httpMethod == 'POST' and method_allowed_on_api:
            return create_tag_type(event['body'])
        elif httpMethod == 'PUT' and method_allowed_on_api:
            return update_tag(event['body'])
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

    except Exception as e:
        logger.exception(e)
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            response['statusCode'] = 400
            response['body'] = json.dumps(
                {"message": "TagTypeName " + str(event['body']['tagTypeName'] + " already exists.")})
        else:
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
