#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.types import TypeDeserializer
from boto3.dynamodb.conditions import Key
from backend.common.validators import validate

dynamodb = boto3.resource('dynamodb')
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
db_database = None
deserializer = TypeDeserializer()
try:
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    workflow_database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    pipeline_database = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]

except:
    print("Failed Loading Environment Variables")
    response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})

def get_databases(queryParams, showDeleted=False):
    db = boto3.client('dynamodb')

    paginator = db.get_paginator('scan')
    operator = "NOT_CONTAINS"
    if showDeleted:
        operator = "CONTAINS"
    filter = {
        "databaseId":{
            "AttributeValueList":[ {"S":"#deleted"} ],
            "ComparisonOperator": f"{operator}"
        }
    }
    pageIterator = paginator.paginate(
        TableName=db_database,
        ScanFilter=filter,
        PaginationConfig={
            'MaxItems': int(queryParams['maxItems']),
            'PageSize': int(queryParams['pageSize']), 
            'StartingToken': queryParams['startingToken']
        }
    ).build_full_result()
    
    print("Fetching results")
    result = {}
    items = []
    for item in pageIterator['Items']:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}
        items.append(deserialized_document)
    result['Items'] = items

    if 'NextToken' in pageIterator:
        result['NextToken'] = pageIterator['NextToken']
    return result

def get_database(databaseId, showDeleted = False):
    print("Getting database: ", databaseId)
    table = dynamodb.Table(db_database)
    if showDeleted:
        databaseId = databaseId + "#deleted"
    response = table.get_item(Key={'databaseId': databaseId})
    return response.get('Item', {}) 

def delete_database(databaseId):
    response = {
        'statusCode': 404,
        'message': 'Record not found'
    } 

    table = dynamodb.Table(db_database)
    if "#deleted" in databaseId:
        return response

    if check_workflows(databaseId):
        response['statusCode'] = 404
        response['message'] = "Database contains active workflows"
        return response
    if check_pipelines(databaseId):
        response['statusCode'] = 404
        response['message'] = "Database contains active pipelines"
        return response
    if check_assets(databaseId):
        response['statusCode'] = 404
        response['message'] = "Database contains active assets"
        return response

    item = get_database(databaseId)
    if item:
        print("Deleting database: ", item)
        item['databaseId'] = databaseId + "#deleted"
        table.put_item(
            Item=item
        )
        result = table.delete_item(Key={'databaseId': databaseId})
        print(result)
        response['statusCode'] = 200
        response['message'] = "Database deleted"

    return response

def check_workflows(databaseId):
    result = False
    table = dynamodb.Table(workflow_database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
        Limit=1
    )
    if response['Count'] > 0:
        result = True
    return result

def check_pipelines(databaseId):
    result = False
    table = dynamodb.Table(pipeline_database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
        Limit=1
    )
    if response['Count'] > 0:
        result = True
    return result

def check_assets(databaseId):
    result = False
    table = dynamodb.Table(asset_database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
        Limit=1
    )
    if response['Count'] > 0:
        result = True
    return result

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

    pathParameters = event.get('pathParameters', {})
    queryParameters = event.get('queryStringParameters', {})
    showDeleted = False
    if 'showDeleted' in queryParameters:
        showDeleted = queryParameters['showDeleted']

    try:
        httpMethod = event['requestContext']['http']['method']
        print(httpMethod)
        if httpMethod == 'GET':
            if 'databaseId' not in pathParameters:
                print("Listing Databases")
                if 'maxItems' not in queryParameters:
                    queryParameters['maxItems'] = 100
                    queryParameters['pageSize'] = 100
                else:
                    queryParameters['pageSize'] = queryParameters['maxItems']
                    
                if 'startingToken' not in queryParameters:
                    queryParameters['startingToken'] = None

                response['body'] = json.dumps({"message": get_databases(queryParameters, showDeleted)})
                print(response)
                return response
            else:
                print("Validating parameters")
                (valid, message) = validate({
                    'databaseId': {
                        'value': pathParameters['databaseId'], 
                        'validator': 'ID'
                    },
                })
                
                if not valid:
                    print(message)
                    response['body']=json.dumps({"message": message})
                    response['statusCode'] = 400
                    return response

                print("Getting Database")
                response['body'] = json.dumps({"message":get_database(pathParameters['databaseId'], showDeleted)})
                print(response)
                return response
        if httpMethod == 'DELETE':
            if 'databaseId' not in pathParameters:
                message = "No database ID in API Call"
                response['body']=json.dumps({"message":message})
                response['statusCode'] = 400
                print(response)
                return response
            else:
                print("Validating parameters")
                (valid, message) = validate({
                    'databaseId': {
                        'value': pathParameters['databaseId'], 
                        'validator': 'ID'
                    },
                })
                
                if not valid:
                    print(message)
                    response['body']=json.dumps({"message": message})
                    response['statusCode'] = 400
                    return response
                               
                print("Deleting Database")
                result = delete_database(pathParameters['databaseId'])
                response['body'] = json.dumps({"message":result['message']})
                response['statusCode'] = result['statusCode']
                print(response)
                return response

    except Exception as e:
        response['statusCode'] = 500
        print("Error!", e.__class__, "occurred.")
        try:
            print(e)
            response['body'] = json.dumps({"message": str(e)})
        except:
            print("Can't Read Error")
            response['body'] = json.dumps({"message": "An unexpected error occurred while executing the request"})
        return response
