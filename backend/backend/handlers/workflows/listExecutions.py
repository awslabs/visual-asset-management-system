#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os

import boto3
from boto3.dynamodb.conditions import Key
from backend.common.validators import validate

sfn = boto3.client('stepfunctions')
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

try:
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")
    response['body'] = json.dumps({"message": "Failed Loading Environment Variables"})


def get_executions(asset_id, workflow_id):
    print("Listing executions")
    table = dynamodb.Table(workflow_execution_database)
    pk = f'{asset_id}-{workflow_id}'
    response = table.query(
        KeyConditionExpression=Key('pk').eq(pk),
        ScanIndexForward=False,
    )
    result = []
    for item in response['Items']:
        assets=item.get('assets', [])
        workflow_arn = item['workflow_arn']
        execution_arn = workflow_arn.replace("stateMachine", "execution")
        execution_arn = execution_arn + ":" + item['execution_id']
        print(execution_arn)
        execution = sfn.describe_execution(
            executionArn=execution_arn
        )
        print(execution)
        startDate = execution.get('startDate', "")
        if startDate:
            startDate = startDate.strftime("%m/%d/%Y, %H:%M:%S")
        stopDate = execution.get('stopDate', "")
        if stopDate:
            stopDate = stopDate.strftime("%m/%d/%Y, %H:%M:%S")

        result.append({
            'executionId': execution['name'],
            'executionStatus': execution['status'],
            'startDate': startDate,
            'stopDate': stopDate,
            'Items': assets
        })

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
    pathParams = event.get('pathParameters', {})
    if 'assetId' not in pathParams:
        message = "No assetID in API Call"
        response['body'] = json.dumps({"message": message})
        print(response)
        return response

    if 'workflowId' not in pathParams:
        message = "No workflow in API Call"
        response['body'] = json.dumps({"message": message})
        print(response)
        return response

    try:

        print("Validating Parameters")
        (valid, message) = validate({
            'workflowId': {
                'value': pathParams['workflowId'], 
                'validator': 'ID'
            }, 
            'assetId': {
                'value': pathParams['assetId'], 
                'validator': 'ID'
            },
        })

        if not valid:
            print(message)
            response['body']=json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        print("Listing Workflow Executions")
        response['body'] = json.dumps({"message": get_executions(pathParams['assetId'], pathParams['workflowId'])})
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
