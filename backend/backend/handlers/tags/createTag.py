#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json

import botocore.exceptions
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service="CreateTag")
dynamodb = boto3.resource('dynamodb')

main_rest_response = STANDARD_JSON_RESPONSE

try:
    tag_db_table_name = os.environ["TAGS_STORAGE_TABLE_NAME"]
    tag_type_db_table_name = os.environ["TAG_TYPES_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def create_tag(body):
    response = STANDARD_JSON_RESPONSE
    tag_table = dynamodb.Table(tag_db_table_name)
    tag_type_table = dynamodb.Table(tag_type_db_table_name)

    response_tag_type = tag_type_table.get_item(
        Key={
            'tagTypeName': body["tagTypeName"]
        }
    )

    if 'Item' in response_tag_type:
        tag_table.put_item(
            Item={
                'tagName': body["tagName"],
                'description': body["description"],
                'tagTypeName': body["tagTypeName"]
            },
            ConditionExpression='attribute_not_exists(tagName)'
        )
    else:
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "TagTypeName " + str(body["tagTypeName"]) + " doesn't exists."})
        return response

    return json.dumps({"message": 'Succeeded'})


def update_tag(body):
    response = STANDARD_JSON_RESPONSE
    tag_table = dynamodb.Table(tag_db_table_name)
    tag_type_table = dynamodb.Table(tag_type_db_table_name)

    tag_response = tag_table.query(
        KeyConditionExpression='tagName = :tag',
        ExpressionAttributeValues={
            ':tag': body["tagName"]
        }
    )

    tag_type_response = tag_type_table.get_item(
        Key={
            'tagTypeName': body["tagTypeName"]
        }
    )

    if 'Items' in tag_response and 'Item' in tag_type_response:
        tag_table.update_item(
            Key={
                'tagName': body["tagName"]
            },
            UpdateExpression='SET tagTypeName = :tag_type, description = :desc',
            ExpressionAttributeValues={
                ':tag_type': body["tagTypeName"],
                ':desc': body["description"]
            },
            ConditionExpression='attribute_exists(tagName)'
        )
    else:
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "TagTypeName or TagName don't exists."})
        return response

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
        event['body'] = json.loads(event['body'])

    try:
        if 'tagName' not in event['body'] or 'description' not in event['body'] or 'tagTypeName' not in event['body']:
            message = "TagName, description and tagTypeName are required."
            response['body'] = json.dumps({"message": message})
            return response

        (valid, message) = validate({
            'tagName': {
                'value': event['body']['tagName'],
                'validator': 'OBJECT_NAME'
            },
            'description': {
                'value': event['body']['description'],
                'validator': 'STRING_256'
            },
            'tagTypeName': {
                'value': event['body']['tagTypeName'],
                'validator': 'OBJECT_NAME'
            }
        })

        if not valid:
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        httpMethod = event['requestContext']['http']['method']
        method_allowed_on_api = False
        tag = {
            "object__type": "tag",
            "tagName": event['body']['tagName']
        }
        # Add Casbin Enforcer to check if the current user has permissions to POST/PUT the Tag
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(tag, httpMethod) and casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if httpMethod == 'POST' and method_allowed_on_api:
            return create_tag(event['body'])
        elif httpMethod == 'PUT' and method_allowed_on_api:
            return update_tag(event['body'])
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

    except Exception as e:
        logger.exception(e)
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException' or e.response['Error'][
            'Code'] == 'TransactionCanceledException':
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "TagName " + str(
                event['body']['tagName'] + " already exists. or TagTypeName " + str(
                    event['body']['tagTypeName']) + " don't exists.")})
        else:
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
