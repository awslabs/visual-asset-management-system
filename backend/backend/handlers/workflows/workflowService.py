#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from backend.common.validators import validate
from backend.handlers.auth import create_ddb_filter, get_database_set, request_to_claims

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
workflow_database = None
unitTest = {
    "body": {
        "databaseId": "Unit_Test"
    }
}
unitTest['body'] = json.dumps(unitTest['body'])

try:
    workflow_database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")
    response['body'] = json.dumps({"message": "Failed Loading Environment Variables"})


def get_all_workflows_with_database_filter(queryParams, databaseList):
    dynamodb = boto3.client('dynamodb')
    deserializer = TypeDeserializer()

    paginator = dynamodb.get_paginator('scan')
    kwargs = {
        "TableName": workflow_database,
        "PaginationConfig": {
            'MaxItems': int(queryParams['maxItems']),
            'PageSize': int(queryParams['pageSize']),
            'StartingToken': queryParams['startingToken']
        }
    }
    kwargs.update(create_ddb_filter(databaseList))
    pageIterator = paginator.paginate(
        **kwargs
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


def get_all_workflows(queryParams, showDeleted=False):
    dynamodb = boto3.client('dynamodb')
    deserializer = TypeDeserializer()

    paginator = dynamodb.get_paginator('scan')
    operator = "NOT_CONTAINS"
    if showDeleted:
        operator = "CONTAINS"
    filter = {
        "databaseId": {
            "AttributeValueList": [{"S": "#deleted"}],
            "ComparisonOperator": f"{operator}"
        }
    }
    pageIterator = paginator.paginate(
        TableName=workflow_database,
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


def get_workflows(databaseId, showDeleted=False):
    table = dynamodb.Table(workflow_database)
    if showDeleted:
        databaseId = databaseId + "#deleted"
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
    )
    return response['Items']


def get_workflow(databaseId, workflowId, showDeleted=False):
    table = dynamodb.Table(workflow_database)
    if showDeleted:
        databaseId = databaseId + "#deleted"
    response = table.get_item(Key={'databaseId': databaseId, 'workflowId': workflowId})
    return response.get('Item', {})


def delete_workflow(databaseId, workflowId):
    response = {
        'statusCode': 404,
        'message': 'Record not found'
    }
    table = dynamodb.Table(workflow_database)
    if "#deleted" in databaseId:
        return response
    item = get_workflow(databaseId, workflowId)
    if item:
        print("Deleting workflow: ", item)
        delete_stepfunction(item['workflow_arn'])
        item['databaseId'] = databaseId + "#deleted"
        table.put_item(
            Item=item
        )
        result = table.delete_item(Key={'databaseId': databaseId, 'workflowId': workflowId})
        print(result)
        response['statusCode'] = 200
        response['message'] = "Workflow deleted"
    return response


def delete_stepfunction(workflowArn):
    sf_client = boto3.client('stepfunctions')

    print("Deleting StepFunctions: ", workflowArn)
    response = sf_client.delete_state_machine(
        stateMachineArn=workflowArn
    )
    print("StepFunctions Response: ", response)

    return response


def get_handler(event, response, pathParameters, queryParameters, showDeleted):
    if 'workflowId' not in pathParameters:
        if 'databaseId' in pathParameters:

            print("Validating Parameters")
            (valid, message) = validate({
                'databaseId': {
                    'value': pathParameters['databaseId'],
                    'validator': 'ID'
                }
            })

            if not valid:
                print(message)
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response

            print("Listing Workflows for Database: ", pathParameters['databaseId'])
            response['body'] = json.dumps({"message": get_workflows(pathParameters['databaseId'], showDeleted)})
            print(response)
            return response
        else:
            print("Listing All Workflows")
            response['body'] = json.dumps({"message": get_all_workflows(queryParameters, showDeleted)})
            print(response)
            return response
    else:
        if 'databaseId' not in pathParameters:
            message = "No database ID in API Call"
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            print(response)
            return response

        print("Validating Parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': pathParameters['databaseId'],
                'validator': 'ID'
            },
            'workflowId': {
                'value': pathParameters['workflowId'],
                'validator': 'ID'
            }
        })

        if not valid:
            print(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        print("Getting Workflow: ", pathParameters['workflowId'])
        response['body'] = json.dumps({"message": get_workflow(
            pathParameters['databaseId'], pathParameters['workflowId'], showDeleted)})
        print(response)
        return response


def get_handler_with_tokens(event, response, pathParameters, queryParameters, tokens):
    requestid = event['requestContext']['requestId']
    if "workflowId" in pathParameters and "databaseId" in pathParameters:
        print("Validating Parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': pathParameters['databaseId'],
                'validator': 'ID'
            },
            'workflowId': {
                'value': pathParameters['workflowId'],
                'validator': 'ID'
            }
        })

        if not valid:
            print(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        databases = get_database_set(tokens)
        if pathParameters['databaseId'] not in databases:
            response['body'] = json.dumps({
                "message": "Not Authorized",
                "requestid": requestid,
            })
            response['statusCode'] = 403
            return response

        print("Getting Workflow: ", pathParameters['workflowId'])
        response['body'] = json.dumps({"message": get_workflow(
            pathParameters['databaseId'], pathParameters['workflowId'])})
        print(response)
        return response

    if "databaseId" in pathParameters:
        print("Validating Parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': pathParameters['databaseId'],
                'validator': 'ID'
            }
        })

        if not valid:
            print(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        databases = get_database_set(tokens)
        if pathParameters['databaseId'] not in databases:
            response['body'] = json.dumps({
                "message": "Not Authorized",
                "requestid": requestid,
            })
            response['statusCode'] = 403
            return response

        print("Listing Workflows for Database: ", pathParameters['databaseId'])
        response['body'] = json.dumps({"message": get_workflows(pathParameters['databaseId'])})
        print(response)
        return response

    print("Listing All Workflows")
    databases = get_database_set(tokens)
    response['body'] = json.dumps({"message": get_all_workflows_with_database_filter(queryParameters, databases)})
    print(response)
    return response


def delete_handler_with_tokens(event, response, pathParameters, tokens):
    requestid = event['requestContext']['requestId']

    if 'databaseId' not in pathParameters:
        message = "No database ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        print(response)
        return response
    if 'workflowId' not in pathParameters:
        message = "No workflow ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        print(response)
        return response

    databases = get_database_set(tokens)
    if pathParameters['databaseId'] not in databases:
        response['body'] = json.dumps({
            "message": "Not Authorized",
            "requestid": requestid,
        })
        response['statusCode'] = 403
        return response

    print("Validating Parameters")
    (valid, message) = validate({
        'databaseId': {
            'value': pathParameters['databaseId'],
            'validator': 'ID'
        },
        'workflowId': {
            'value': pathParameters['workflowId'],
            'validator': 'ID'
        }
    })

    if not valid:
        print(message)
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        return response

    print("Deleting Workflow: ", pathParameters['workflowId'])
    result = delete_workflow(pathParameters['databaseId'], pathParameters['workflowId'])
    response['body'] = json.dumps({"message": result['message']})
    response['statusCode'] = result['statusCode']
    print(response)
    return response


def delete_handler(event, response, pathParameters):
    if 'databaseId' not in pathParameters:
        message = "No database ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        print(response)
        return response
    if 'workflowId' not in pathParameters:
        message = "No workflow ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        print(response)
        return response

    print("Validating Parameters")
    (valid, message) = validate({
        'databaseId': {
            'value': pathParameters['databaseId'],
            'validator': 'ID'
        },
        'workflowId': {
            'value': pathParameters['workflowId'],
            'validator': 'ID'
        }
    })

    if not valid:
        print(message)
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        return response

    print("Deleting Workflow: ", pathParameters['workflowId'])
    result = delete_workflow(pathParameters['databaseId'], pathParameters['workflowId'])
    response['body'] = json.dumps({"message": result['message']})
    response['statusCode'] = result['statusCode']
    print(response)
    return response


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
        claims_and_roles = request_to_claims(event)
        if "super-admin" in claims_and_roles['roles']:
            if httpMethod == 'GET':
                return get_handler(event, response, pathParameters, queryParameters, showDeleted)
            if httpMethod == 'DELETE':
                return delete_handler(event, response, pathParameters)
        elif "workflows" in claims_and_roles['roles']:
            if httpMethod == 'GET':
                return get_handler_with_tokens(event, response, pathParameters, queryParameters, claims_and_roles['tokens'])
            else:
                return delete_handler_with_tokens(event, response, pathParameters, claims_and_roles['tokens'])
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
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
