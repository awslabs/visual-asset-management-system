#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from backend.common.validators import validate

dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
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
comment_database = None
unitTest = {
    "body": {
        "assetId": "Unit_Test"
    }
}
unitTest['body'] = json.dumps(unitTest['body'])

try:
    comment_database = os.environ["COMMENT_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Comment Storage Environment Variables")
    response['body']['message'] = "Failed Loading Comment Storage Environment Variables"

def get_all_comments(queryParams, showDeleted=False):
    '''
    Function to get all of the comments from the database
    '''
    deserializer = TypeDeserializer()

    paginator = dynamodb_client.get_paginator('scan')
    operator = "NOT_CONTAINS"
    if showDeleted:
        operator = "CONTAINS"
    filter = {
        "assetId": {
            "AttributeValueList": [{"S": "#deleted"}],
            "ComparisonOperator": f"{operator}"
        }
    }

    pageIterator = paginator.paginate(
        TableName=comment_database,
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

def get_comments(assetId, showDeleted=False):
    '''
    Gets all of the comments associated with a specific assetId
    (all comments for the specific asset)
    '''
    table = dynamodb.Table(comment_database)

    # if showDeleted:
    #     as = assetId + "#deleted"
    response = table.query(
        KeyConditionExpression=Key('assetId').eq(assetId),
        ScanIndexForward=False,
        Limit=1000
    )
    return response['Items']

def get_comments_version(assetId, assetVersionId, showDeleted=False):
    '''
    Get all of the comments for a specific assetId versionId pair
    (all comments for a specific version of an asset)
    '''
    table = dynamodb.Table(comment_database)

    # if showDeleted:
    #     as = assetId + "#deleted"

    # Queries partition key (assetId) and queries sort keys that begin_with the desired asset version
    response = table.query(
        KeyConditionExpression=Key('assetId').eq(assetId) & Key('assetVersionId:commentId').begins_with(assetVersionId),
        ScanIndexForward=False,
        Limit=1000
    )
    print(response['Items'])
    return response['Items']


def get_single_comment(assetId, assetVersionIdAndcommentId, showDeleted=False):
    '''
    Gets a specific comment from the assetId and the assetVersionId:commentId
    '''
    print("Getting single comment")
    table = dynamodb.Table(comment_database)
    # if showDeleted:
    #     databaseId = databaseId + "#deleted"
    response = table.get_item(Key={'assetId': assetId, 'assetVersionId:commentId': assetVersionIdAndcommentId})
    return response.get('Item', {})


def delete_comment(assetId, assetVersionIdAndcommentId, queryParameters):
    '''
    Deletes a specific comment from the database
    (actually just adds #deleted tag)
    '''
    response = {
        'statusCode': 404,
        'message': 'Record not found'
    }
    table = dynamodb.Table(comment_database)
    if "#deleted" in assetId:
        return response
    item = get_single_comment(assetId, assetVersionIdAndcommentId)
    if item:
        print("Deleting asset: ", item)
        item['assetId'] = assetId + "#deleted"
        table.put_item(
            Item=item
        )
        response = table.delete_item(Key={'assetId': assetId, 'assetVersionId:commentId': assetVersionIdAndcommentId})
        # update assetCount after successful deletion of an asset
        print(result)
        response['statusCode'] = 200
        response['message'] = "Asset deleted"
    return response

def set_pagination_info(queryParameters):
    '''
    Sets the pagination infor from the query parameters
    '''
    if 'maxItems' not in queryParameters:
        queryParameters['maxItems'] = 100
        queryParameters['pageSize'] = 100
    else:
        queryParameters['pageSize'] = queryParameters['maxItems']
    if 'startingToken' not in queryParameters:
        queryParameters['startingToken'] = None

def get_handler(response, pathParameters, queryParameters):
    '''
    Function to handle the request and route it to the right function
    '''
    showDeleted = False

    if 'showDeleted' in queryParameters:
        showDeleted = queryParameters['showDeleted']

    if 'assetVersionId:commentId' not in pathParameters:
        # if we have an assetVersionId and assetId, call get_comments_version
        if 'assetVersionId' in pathParameters and 'assetId' in pathParameters:
            print("Validating parameters")
            (valid, message) = validate({
                'assetId': {
                    'value': pathParameters['assetId'],
                    'validator': 'ID'
                }
            })
            if not valid:
                print(message)
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response

            print("Listing comments for asset:", pathParameters['assetId'], "and version", pathParameters['assetVersionId'])
            response['body'] = json.dumps({"message": get_comments_version(
                                                                            pathParameters['assetId'], 
                                                                            pathParameters['assetVersionId'],
                                                                            showDeleted
                                                                        )})
            print(response)
            return response

        # if we just have assetId, call get_comments
        if 'assetId' in pathParameters:
            print("Validating parameters")
            (valid, message) = validate({
                'assetId': {
                    'value': pathParameters['assetId'],
                    'validator': 'ID'
                }
            })
            if not valid:
                print(message)
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response

            print("Listing comments for asset:", pathParameters['assetId'])
            response['body'] = json.dumps({"message": get_comments(
                                                                    pathParameters['assetId'],
                                                                    showDeleted
                                                                )})
            print(response)
            return response
        else:
            # if we have nothing, call get_all_comments
            print("Listing All Comments")
            response['body'] = json.dumps({"message": get_all_comments(
                                                                        queryParameters,
                                                                        showDeleted
                                                                    )})
            print(response)
            return response
    else:
        # error, no assetId in call
        if 'assetId' not in pathParameters:
            message = "No asset ID in API Call"
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            print(response)
            return response

        print("Validating parameters")

        split_arr = pathParameters["assetVersionId:commentId"].split(':')
        print("Validating parameters")
        (valid, message) = validate({
            'assetId': {
                'value': pathParameters['assetId'],
                'validator': 'ID'
            },
            'commentId': {
                'value': split_arr[1],
                'validator': 'ID'
            }
        })

        if not valid:
            print(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        print("Getting comment with assetId", pathParameters['assetId'], "and assetVersionId:commentId", pathParameters['assetVersionId:commentId'])
        response['body'] = json.dumps({"message": get_single_comment(
                                                                    pathParameters['assetId'], 
                                                                    pathParameters['assetVersionId:commentId'],
                                                                    showDeleted
                                                                )})
        print(response)
        return response


def delete_handler(response, pathParameters, queryParameters):
    if 'assetId' not in pathParameters:
        message = "No asset ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        print(response)
        return response
    if 'assetId' not in pathParameters:
        message = "No asset ID in API Call"
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        print(response)
        return response

    print("Validating parameters")
    (valid, message) = validate({
        'databaseId': {
            'value': pathParameters['databaseId'],
            'validator': 'ID'
        },
        'assetId': {
            'value': pathParameters['assetId'],
            'validator': 'ID'
        },
    })
    if not valid:
        print(message)
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        return response

    print("Deleting Asset: ", pathParameters['assetVersionId:commentId'])
    result = delete_comment(pathParameters['assetId'], pathParameters['assetVersionId:commentId'], queryParameters)
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

    set_pagination_info(queryParameters)

    try:
        # route the api call based on tags
        httpMethod = event['requestContext']['http']['method']
        print(httpMethod)

        if httpMethod == 'GET':
            return get_handler(response, pathParameters, queryParameters)
        if httpMethod == 'DELETE':
            return delete_handler(response, pathParameters, queryParameters)

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
