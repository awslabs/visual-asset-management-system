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
    
    # Check if tag type already exists
    try:
        existing_tag_type = table.get_item(
            Key={'tagTypeName': body["tagTypeName"]}
        )
        if 'Item' in existing_tag_type:
            response = STANDARD_JSON_RESPONSE
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Tag type already exists."})
            return response
    except Exception as e:
        # If the error is not about the item not existing, re-raise it
        if hasattr(e, 'response') and e.response.get('Error', {}).get('Code') != 'ResourceNotFoundException':
            raise e
    
    item = {
        "tagTypeName": body["tagTypeName"],
        "description": body["description"],
        "required": body.get("required", "False")
    }
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(tagTypeName)")
    return json.dumps({"message": 'Succeeded'})


def update_tag(body):
    table = dynamodb.Table(tag_type_db_table_name)
    table.update_item(
        Key={
            'tagTypeName': body["tagTypeName"]
        },
        UpdateExpression='SET description = :descriptionValue, required = :requiredValue',
        ExpressionAttributeValues={
            ':descriptionValue': body["description"],
            ':requiredValue': body.get("required", "False")
        },
        ConditionExpression='attribute_exists(tagTypeName)'
    )
    return json.dumps({"message": 'Succeeded'})


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE

    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    # Parse request body
    if not event.get('body'):
        message = 'Request body is required'
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response

    if isinstance(event['body'], str):
        try:
            event['body'] = json.loads(event['body'])
        except json.JSONDecodeError as e:
            logger.exception(f"Invalid JSON in request body: {e}")
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "Invalid JSON in request body"})
            return response

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
            },
            'required': {
                'value': event['body'].get("required", "False"),
                'validator': 'BOOL',
                'optional': True
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
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(tag_type, httpMethod) and casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

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
                {"message": "Tag type already exists."})
        else:
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
