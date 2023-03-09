#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from backend.common.validators import validate
from botocore.exceptions import ClientError

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
pipeline_database = None
unitTest = {
    "body": {
        "databaseId": "Unit_Test"
    }
}
unitTest['body']=json.dumps(unitTest['body'])

try:
    pipeline_database = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")
    response['body'] =json.dumps({"message":"Failed Loading Environment Variables"}) 

def get_all_pipelines(queryParams, showDeleted=False):
    dynamodb = boto3.client('dynamodb')
    deserializer = TypeDeserializer()

    paginator = dynamodb.get_paginator('scan')
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
        TableName=pipeline_database,
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

def get_pipelines(databaseId, showDeleted):
    table = dynamodb.Table(pipeline_database)
    if showDeleted:
        databaseId = databaseId + "#deleted"
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
    )
    return response['Items']

def get_pipeline(databaseId, pipelineId, showDeleted=False):
    table = dynamodb.Table(pipeline_database)
    if showDeleted:
        databaseId = databaseId + "#deleted"    
    response = table.get_item(Key={'databaseId': databaseId, 'pipelineId': pipelineId})
    return response.get('Item', {}) 

def delete_pipeline(databaseId, pipelineId):
    response = {
        'statusCode': 404,
        'message': 'Record not found'
    } 
    table = dynamodb.Table(pipeline_database)
    if "#deleted" in databaseId:
        return response
    item = get_pipeline(databaseId, pipelineId)
    
    if item:
        print("Deleting pipeline: ", item)
        try:
            userResource = json.loads(item['userProvidedResource'])
            if userResource['isProvided'] == False:
                if item['pipelineType'] == 'SageMaker':
                    delete_stack(item['pipelineId'])
                else: #Lambda Pipeline
                    delete_lambda(item['pipelineId'])
        except KeyError: #For pipelines created before user provided resources were implemented
            if item['pipelineType'] == 'SageMaker':
                delete_stack(item['pipelineId'])
            else: #Lambda Pipeline
                delete_lambda(item['pipelineId'])
        
        item['databaseId'] = databaseId + "#deleted"
        table.put_item(
            Item=item
        )
        result = table.delete_item(Key={'databaseId': databaseId, 'pipelineId': pipelineId})
        print(result)
        response['statusCode'] = 200
        response['message'] = "Pipeline deleted"
    return response

def delete_lambda(pipelineId):
    lambda_client = boto3.client('lambda')
    try:
        lambda_client.delete_function(
            FunctionName=pipelineId,
        )
    except Exception as e:
        print("Failed to delete lambda")
        print(e)


def delete_stack(pipelineId):
    cf_client= boto3.client('cloudformation')
    ecr_client= boto3.client('ecr')

    try:
        print("Deleting ECR repo: ", pipelineId)
        response = ecr_client.delete_repository(
            repositoryName=pipelineId,
            force=True
        )
        print("ECR response: ", response)
    except ecr_client.exceptions.RepositoryNotFoundException as nfe:
        print("ECR Repository not found: ", pipelineId)
        print(nfe)

    try: 
        print("Deleting CloudFormation stack: ", pipelineId)
        response=cf_client.delete_stack(
            StackName=pipelineId,
        )
        print("CloudFormation response: ", response)
    except ClientError as client_error:
        print("Failed to delete stack")
        print(client_error)

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
    if 'maxItems' not in queryParameters:
        queryParameters['maxItems'] = 100
        queryParameters['pageSize'] = 100
    else:
        queryParameters['pageSize'] = queryParameters['maxItems']        
    if 'startingToken' not in queryParameters:
        queryParameters['startingToken'] = None

    try:
        httpMethod = event['requestContext']['http']['method']
        print(httpMethod)
        if httpMethod == 'GET':
            if 'pipelineId' not in pathParameters:
                if 'databaseId' in pathParameters:
                    print("Validating Parameters")
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
                    print("Listing Pipelines for Database: ", pathParameters['databaseId'])
                    response['body'] = json.dumps({"message":get_pipelines(pathParameters['databaseId'], showDeleted)})
                    print(response)
                    return response
                else:
                    print("Listing All Pipelines")
                    response['body'] = json.dumps({"message":get_all_pipelines(queryParameters, showDeleted)})
                    print(response)
                    return response
            else:
                if 'databaseId' not in pathParameters:
                    message = "No database ID in API Call"
                    response['body']=json.dumps({"message":message})
                    response['statusCode'] = 400
                    print(response)
                    return response
                
                print("Validating Parameters")
                (valid, message) = validate({
                    'databaseId': {
                        'value': pathParameters['databaseId'], 
                        'validator': 'ID'
                    },
                    'pipelineId': {
                        'value': pathParameters['pipelineId'], 
                        'validator': 'ID'
                    }
                })
                if not valid:
                    print(message)
                    response['body']=json.dumps({"message": message})
                    response['statusCode'] = 400
                    return response

                print("Getting Pipeline: ", pathParameters['pipelineId'])
                response['body'] = json.dumps({"message":get_pipeline(pathParameters['databaseId'], pathParameters['pipelineId'], showDeleted)})
                print(response)
                return response
        if httpMethod == 'DELETE':
            if 'databaseId' not in pathParameters:
                message = "No database ID in API Call"
                response['body']=json.dumps({"message":message})
                response['statusCode'] = 400
                print(response)
                return response
            if 'pipelineId' not in pathParameters:
                message = "No pipeline ID in API Call"
                response['body']=json.dumps({"message":message})
                response['statusCode'] = 400
                print(response)
                return response

            print("Validating Parameters")
            (valid, message) = validate({
                'databaseId': {
                    'value': pathParameters['databaseId'], 
                    'validator': 'ID'
                }
            })
            if not valid:
                print(message)
                response['body']=json.dumps({"message": message})
                response['statusCode'] = 400
                return response
            
            print("Deleting Pipeline: ", pathParameters['pipelineId'])
            result = delete_pipeline(pathParameters['databaseId'], pathParameters['pipelineId'])
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


if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    print(test_response)
