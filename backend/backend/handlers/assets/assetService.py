#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json

import botocore.exceptions
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from backend.common.validators import validate
from backend.handlers.assets.assetCount import update_asset_count
from backend.handlers.auth import create_ddb_filter, get_database_set, request_to_claims

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
asset_database = None
db_database = None
s3_assetVisualizer_bucket = None
unitTest = {
    "body": {
        "databaseId": "Unit_Test"
    }
}
unitTest['body'] = json.dumps(unitTest['body'])

try:
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    s3_assetVisualizer_bucket = os.environ["S3_ASSET_VISUALIZER_BUCKET"]
except:
    print("Failed Loading Environment Variables")
    response['body']['message'] = "Failed Loading Environment Variables"


def get_all_assets_with_database_filter(queryParams, databaseList):
    deserializer = TypeDeserializer()

    paginator = dynamodb_client.get_paginator('scan')

    if len(databaseList) < 1:
        return {
            'Items': [],
        }

    kwargs = {
        "TableName": asset_database,
        "PaginationConfig": {
            'MaxItems': int(queryParams['maxItems']),
            'PageSize': int(queryParams['pageSize']),
            'StartingToken': queryParams['startingToken']
        }
    }
    kwargs.update(create_ddb_filter(databaseList))

    print(kwargs)
    pageIterator = paginator.paginate(
        **kwargs,
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


def get_all_assets(queryParams, showDeleted=False):
    deserializer = TypeDeserializer()

    paginator = dynamodb_client.get_paginator('scan')
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
        TableName=asset_database,
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


def get_assets(databaseId, showDeleted=False):
    table = dynamodb.Table(asset_database)
    # indexName = 'databaseId-assetId-index'

    if showDeleted:
        databaseId = databaseId + "#deleted"
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
        Limit=1000
    )
    return response['Items']


def get_asset(databaseId, assetId, showDeleted=False):
    table = dynamodb.Table(asset_database)
    if showDeleted:
        databaseId = databaseId + "#deleted"
    response = table.get_item(Key={'databaseId': databaseId, 'assetId': assetId})
    return response.get('Item', {})


def delete_asset(databaseId, assetId, queryParameters):
    response = {
        'statusCode': 404,
        'message': 'Record not found'
    }
    table = dynamodb.Table(asset_database)
    if "#deleted" in databaseId:
        return response
    item = get_asset(databaseId, assetId)
    if item:
        print("Deleting asset: ", item)
        if "assetLocation" in item:
            if item['isMultiFile']:
                archive_multi_file(item['assetLocation'], databaseId, assetId)
                delete_assetVisualizer_files(item['assetLocation'])
            else:
                archive_file(item['assetLocation'], databaseId, assetId)
                delete_assetVisualizer_files(item['assetLocation'])
        if "previewLocation" in item:
            archive_file(item['previewLocation'], databaseId, assetId)
        item['databaseId'] = databaseId + "#deleted"
        table.put_item(
            Item=item
        )
        result = table.delete_item(Key={'databaseId': databaseId, 'assetId': assetId})
        # update assetCount after successful deletion of an asset
        update_asset_count(db_database, asset_database, queryParameters, databaseId)
        print(result)
        response['statusCode'] = 200
        response['message'] = "Asset deleted"
    return response


def archive_multi_file(location, databaseId, assetId):
    s3 = boto3.client('s3')
    bucket = ""
    prefix = ""
    if "Bucket" in location:
        bucket = location['Bucket']
    if "Key" in location:
        prefix = location['Key']
    if len(bucket) == 0 or len(prefix) == 0:
        return
    print('Archiving folder with multiple files')

    paginator = s3.get_paginator('list_objects_v2')
    files = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            files.append(obj['Key'])

    for key in files:
        try:
            response = move_to_glacier_and_mark_deleted(bucket, key, databaseId, assetId)
            print("S3 response: ", response)

        except s3.exceptions.InvalidObjectState as ios:
            print("S3 object already archived: ", key)
            print(ios)

        except botocore.exceptions.ClientError as e:
            # TODO: Most likely an error when the key doesnt exist
            print("Error occurred: ", e)

    return


def archive_file(location, databaseId, assetId):
    s3 = boto3.client('s3')

    bucket = ""
    key = ""
    if "Bucket" in location:
        bucket = location['Bucket']
    if "Key" in location:
        key = location['Key']

    if len(bucket) == 0 or len(key) == 0:
        return
    print("Archiving item: ", bucket, ":", key)

    try:
        response = move_to_glacier_and_mark_deleted(bucket, key, databaseId, assetId)
        print("S3 response: ", response)

    except s3.exceptions.InvalidObjectState as ios:
        print("S3 object already archived: ", key)
        print(ios)

    except botocore.exceptions.ClientError as e:
        # TODO: Most likely an error when the key doesnt exist
        print("Error occurred: ", e)
    return


def move_to_glacier_and_mark_deleted(bucket, key, assetId, databaseId):
    s3 = boto3.client('s3')
    return s3.copy_object(
        CopySource={
            "Bucket": bucket,
            "Key": key,
        },
        Bucket=bucket,
        Key=key,
        MetadataDirective='REPLACE',
        Metadata={
            "assetid": assetId,
            "databaseid": databaseId,
            "vams-status": "deleted",
        },
        StorageClass='GLACIER',
    )


def set_pagination_info(queryParameters):
    if 'maxItems' not in queryParameters:
        queryParameters['maxItems'] = 100
        queryParameters['pageSize'] = 100
    else:
        queryParameters['pageSize'] = queryParameters['maxItems']
    if 'startingToken' not in queryParameters:
        queryParameters['startingToken'] = None


def get_handler_with_tokens(event, response, pathParameters, queryParameters, tokens):
    requestid = event['requestContext']['requestId']
    if "assetId" in pathParameters and "databaseId" in pathParameters:

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

        databases = get_database_set(tokens)
        if pathParameters['databaseId'] not in databases:
            response['body'] = json.dumps({
                "message": "Not Authorized",
                "requestid": requestid,
            })
            response['statusCode'] = 403
            return response

        print("Getting Asset: ", pathParameters['assetId'])
        response['body'] = json.dumps({
            "message": get_asset(pathParameters['databaseId'],
                                 pathParameters['assetId'], ),
            "requestid": requestid,
        })
        print(response)
        return response

    if "databaseId" in pathParameters:
        print("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': pathParameters['databaseId'],
                'validator': 'ID',
            }
        })
        if not valid:
            print(message)
            response['body'] = json.dumps({
                "message": message,
                "requestid": requestid,
            })
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

        print("Listing Assets for Database: ", pathParameters['databaseId'])
        response['body'] = json.dumps({
            "message": get_assets(pathParameters['databaseId'], ),
            "requestid": requestid,
        })
        print(response)
        return response

    if "assetId" in pathParameters and not "databaseId" in pathParameters:
        message = "No database ID in API Call"
        response['body'] = json.dumps({"message": message, "requestid": requestid})
        response['statusCode'] = 400
        print(response)
        return response

    databases = get_database_set(tokens)
    if len(databases) > 0:
        print("Listing All Assets with filter")
        response['body'] = json.dumps({
            "message": get_all_assets_with_database_filter(queryParameters, databases),
            "requestid": requestid,
        })
    else:
        print("database list was empty, returning empty")
        response['body'] = json.dumps({
            "message": [],
            "requestid": requestid,
        })

    print(response)
    return response


def get_handler(response, pathParameters, queryParameters):

    showDeleted = False

    if 'showDeleted' in queryParameters:
        showDeleted = queryParameters['showDeleted']

    if 'assetId' not in pathParameters:
        if 'databaseId' in pathParameters:
            print("Validating parameters")
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

            print("Listing Assets for Database: ", pathParameters['databaseId'])
            response['body'] = json.dumps({"message": get_assets(pathParameters['databaseId'], showDeleted)})
            print(response)
            return response
        else:
            print("Listing All Assets")
            response['body'] = json.dumps({"message": get_all_assets(queryParameters, showDeleted)})
            print(response)
            return response
    else:
        if 'databaseId' not in pathParameters:
            message = "No database ID in API Call"
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

        print("Getting Asset: ", pathParameters['assetId'])
        response['body'] = json.dumps({"message": get_asset(
            pathParameters['databaseId'], pathParameters['assetId'], showDeleted)})
        print(response)
        return response


def delete_handler_with_tokens(event, response, pathParameters, queryParameters, tokens):
    requestid = event['requestContext']['requestId']

    if 'databaseId' not in pathParameters:
        message = "No database ID in API Call"
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

    databases = get_database_set(tokens)
    if pathParameters['databaseId'] not in databases:
        response['body'] = json.dumps({
            "message": "Not Authorized",
            "requestid": requestid,
        })
        response['statusCode'] = 403
        return response

    print("Deleting Asset: ", pathParameters['assetId'])
    result = delete_asset(pathParameters['databaseId'], pathParameters['assetId'], queryParameters)
    response['body'] = json.dumps({"message": result['message']})
    response['statusCode'] = result['statusCode']
    print(response)
    return response


def delete_handler(response, pathParameters, queryParameters):

    if 'databaseId' not in pathParameters:
        message = "No database ID in API Call"
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

    print("Deleting Asset: ", pathParameters['assetId'])
    result = delete_asset(pathParameters['databaseId'], pathParameters['assetId'], queryParameters)
    response['body'] = json.dumps({"message": result['message']})
    response['statusCode'] = result['statusCode']
    print(response)
    return response


def delete_assetVisualizer_files(assetLocation):
    s3 = boto3.client('s3')

    key = ""
    if "Key" in assetLocation:
        key = assetLocation['Key']

    if len(key) == 0:
        return

    # Add the folder deliminiator to the end of the key
    key = key + '/'

    print("Deleting Temporary Asset Visualizer Files Under Folder: ", s3_assetVisualizer_bucket, ":", key)

    try:
        # Get all assets in assetVisualizer bucket (unversioned, temporary files for the web visualizers) for deletion
        # Use assetLocation key as root folder key for assetVisualizerFiles
        assetVisualizerBucketFilesDeleted = []
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=s3_assetVisualizer_bucket, Prefix=key):
            for item in page['Contents']:
                assetVisualizerBucketFilesDeleted.append(item['Key'])
                print("Deleting visualizer asset file: ", item['Key'])
                s3.delete_object(Bucket=s3_assetVisualizer_bucket, Key=item['Key'])
                # print(item)

    except Exception as e:
        print("Error: ", e)

    return


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
        httpMethod = event['requestContext']['http']['method']
        print(httpMethod)

        claims_and_roles = request_to_claims(event)

        if "super-admin" in claims_and_roles['roles']:
            if httpMethod == 'GET':
                return get_handler(response, pathParameters, queryParameters)
            if httpMethod == 'DELETE':
                return delete_handler(response, pathParameters, queryParameters)
        elif "assets" in claims_and_roles['roles']:
            if httpMethod == 'GET':
                return get_handler_with_tokens(event, response, pathParameters, queryParameters, claims_and_roles['tokens'])
            if httpMethod == 'DELETE':
                return delete_handler_with_tokens(event, response, pathParameters, queryParameters, claims_and_roles['tokens'])
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
