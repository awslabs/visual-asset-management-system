#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from botocore.exceptions import ClientError
import datetime
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from common.dynamodb import to_update_expr
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service="CreateDatabase")

dynamodb = boto3.resource('dynamodb')
s3c = boto3.client('s3')

main_rest_response = STANDARD_JSON_RESPONSE

unitTest = {
    "body": {
        "databaseId": "Unit_Test",
        "description": "Testing Out Lambda Functions",
    }
}
unitTest['body'] = json.dumps(unitTest['body'])
db_Database = None

try:
    db_Database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['statusCode'] = 500
    main_rest_response['body'] = json.dumps({
        "message": "Failed Loading Environment Variables"
    })


def upload_Asset(body):
    logger.info("Setting Table")
    table = dynamodb.Table(db_Database)
    logger.info("Setting Time Stamp")
    dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')

    item = {
        'description': body['description'],
        # 'dateCreated': json.dumps(dtNow),
        # 'assetCount': json.dumps(0)
    }
    keys_map, values_map, expr = to_update_expr(item)
    table.update_item(
        Key={
            'databaseId': body['databaseId'],
        },
        UpdateExpression=expr,
        ExpressionAttributeNames=keys_map,
        ExpressionAttributeValues=values_map,
    )

    keys_map, values_map, expr = to_update_expr({
        'assetCount': json.dumps(0),
        'dateCreated': json.dumps(dtNow),
    })
    try:
        table.update_item(
            Key={
                'databaseId': body['databaseId'],
            },
            UpdateExpression=expr,
            ExpressionAttributeNames=keys_map,
            ExpressionAttributeValues=values_map,
            ConditionExpression="attribute_not_exists(assetCount)"
        )
    except ClientError as ex:
        # this just means the record already exists, and we are updating an existing record
        if ex.response['Error']['Code'] == 'ConditionalCheckFailedException':
            pass
        else:
            raise ex

    return json.dumps({"message": 'Succeeded'})


def lambda_handler(event, context):

    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    claims_and_roles = request_to_claims(event)

    logger.info(event)
    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])
    try:
        if 'databaseId' not in event['body']:
            message = "No databaseId in API Call"
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            logger.info(response['body'])
            return response

        logger.info("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': event['body']['databaseId'],
                'validator': 'ID'
            },
            'description': {
                'value': event['body']['description'],
                'validator': 'STRING_256'
            }
        })

        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        logger.info("Trying to get Data")

        allowed = False
        # Add Casbin Enforcer to check if the current user has permissions to PUT the database:
        database = {
            "object__type": "database",
            "databaseId": event['body']['databaseId']
        }
        if len(claims_and_roles["tokens"]) > 0:
            # There should be a constraint which allows PUT on this (or all)
            # databases (can use contains .* in the constraint to allow for all)
            # AND also allow PUT method on this API
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if (casbin_enforcer.enforce(database, "PUT")
                    and casbin_enforcer.enforceAPI(event)):
                allowed = True

        if allowed:
            response['body'] = upload_Asset(event['body'])
        else:
            response['body'] = json.dumps({"message": "Not allowed to create/update database"})
            response['statusCode'] = 403
        logger.info(response)
        return response
    except Exception as e:
        logger.exception(e)
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            response['statusCode'] = 400
            response['body'] = json.dumps(
                {"message": "Database " + str(event['body']['databaseId'] + " already exists.")})
        else:
            response['statusCode'] = 500
            logger.exception(e)
            response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
