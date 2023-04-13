#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import sys
import json
from boto3.dynamodb.conditions import Key, Attr, AttributeNotExists
from botocore.exceptions import ClientError

import datetime
from decimal import Decimal
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from backend.common.validators import validate
from backend.common.dynamodb import to_update_expr

dynamodb = boto3.resource('dynamodb')
s3c = boto3.client('s3')

response = {
    'statusCode': 200,
    'body': '',
    'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Credentials': True,
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
    }
}

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
    print("Failed Loading Environment Variables")
    response['statusCode'] = 500
    response['body'] = json.dumps({
        "message": "Failed Loading Environment Variables"
    })


def upload_Asset(body):
    print("Setting Table")
    table = dynamodb.Table(db_Database)
    print("Setting Time Stamp")
    dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')

    item = {
        'description': body['description'],
        'acl': body['acl'],
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
        # this just means the record already exists and we are updating an existing record
        if ex.response['Error']['Code'] == 'ConditionalCheckFailedException':
            pass
        else:
            raise ex

    return json.dumps({"message": 'Succeeded'})


def lambda_handler(event, context):
    print(event)
    response = {
        'statusCode': 200,
        'body': '',
        'headers': {
            'Content-Type': 'application/json',
                'Access-Control-Allow-Credentials': True,
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
        }
    }
    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])
    # event['body']=json.loads(event['body'])
    try:
        if 'databaseId' not in event['body']:
            message = "No databaseId in API Call"
            response['body'] = json.dumps({"message": message})
            print(response['body'])
            return response

        print("Validating parameters")
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
            print(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        print("Trying to get Data")
        response['body'] = upload_Asset(event['body'])
        print(response)
        return response
    except Exception as e:
        print(str(e))
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Database "+str(event['body']['databaseId']+" already exists.")})
            return response
        else:
            response['statusCode'] = 500
            print("Error!", e.__class__, "occurred.")
            try:
                print(e)
                response['body'] = json.dumps({"message": str(e)})
            except:
                print("Can't Read Error")
                response['body'] = json.dumps({"message": "An unexpected error occurred while executing the request"})
            return response
