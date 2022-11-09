#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key, Attr
from backend.common.validators import validate

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
dynamodb = boto3.resource('dynamodb')
try:
    pipeline_Database = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")
    
    response['body'] =json.dumps({"message":"Failed Loading Environment Variables"}) 

def enablePipeline(databaseId, pipelineId):
    table = dynamodb.Table(pipeline_Database)
    print("Enabling pipeline")
    response = table.update_item(
        Key={
            'databaseId': databaseId, 
            'pipelineId': pipelineId
        }, 
        UpdateExpression='SET #enabled = :true',
        ExpressionAttributeNames = {
            '#enabled': 'enabled'
        },
        ExpressionAttributeValues = {
            ':true': True
        }
    )
    print(response)

def lambda_handler(event, context):
    print(event)
    if 'pipelineId' not in event:
        response['statusCode'] = 500
        print("Pipeline id not provided")
        return response
    if 'databaseId' not in event:
        response['statusCode'] = 500
        print("databaseId id not provided")
        return response
    else:
        print("Validating Parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': event['body']['databaseId'], 
                'validator': 'ID'
            },
            'pipelineId': {
                'value': event['body']['pipelineId'], 
                'validator': 'ID'
            }
        })
        if not valid:
            print(message)
            response['body']=json.dumps({"message": message})
            response['statusCode'] = 400
            return response
        try: 
            enablePipeline(event['databaseId'], event['pipelineId'])
            print("Pipeline is enabled")
            response['body'] = json.dumps({"message": "Success"})
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