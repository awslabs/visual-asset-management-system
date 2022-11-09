#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key, Attr
from botocore.config import Config
from botocore.exceptions import ClientError
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
asset_Database = None
unitTest = {
    "body": {
        "databaseId": "Unit_Test"
    }
}
unitTest['body']=json.dumps(unitTest['body'])

try:
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    region = os.environ['AWS_REGION']
except:
    print("Failed Loading Environment Variables")
    response['body']['message'] = "Failed Loading Environment Variables"

s3_config = Config(signature_version='s3v4')
s3_client = boto3.client('s3', region_name=region, endpoint_url=f'https://s3.{region}.amazonaws.com', config=s3_config)

def get_Assets(databaseId, assetId):
    table = dynamodb.Table(asset_Database)
    # indexName = 'databaseId-assetId-index'
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(
            databaseId) & Key('assetId').eq(assetId),
        ScanIndexForward=False
    )
    return response['Items']


def get_Asset(databaseId, assetId, version):
    items = get_Assets(databaseId, assetId)
    if not items and len(items) == 0:
        return "Error: Asset not found"
    item = items[0]
    bucket = item['assetLocation']['Bucket']
    key = item['assetLocation']['Key']
    isDistributable = item['isDistributable']
    if isinstance(isDistributable, bool):
        if not isDistributable:
            return "Error: Asset not distributable"
    else:
        # invalid type of isDistributable is treated as asset not distributable
        print("isDistributable invalid type: ", isDistributable, type(isDistributable))
        return "Error: Asset not distributable"
    if version != "Latest" or version != "" or version != item['currentVersion']['Version']:
        return s3_client.generate_presigned_url('get_object', Params={
            'Bucket': bucket,
            'Key': key
        }, ExpiresIn=3600)
    else:
        versions = item['versions']
        for i in versions:
            if i['Version'] == version:
                return s3_client.generate_presigned_url('get_object', Params={
                    'Bucket': bucket,
                    'Key': key,
                    'VersionId': i['S3Version']
                }, ExpiresIn=3600)
        return "Error: Asset not found"


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
    event['body'] = json.loads(event['body'])
    if 'databaseId' not in event['body'] or 'assetId' not in event['body']:
        message = "DatabaseId or assetId not in API Call"
        response['body'] = json.dumps({"message": message})
        print(response)
        return response
    try:
        print("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': event['body']['databaseId'], 
                'validator': 'ID'
            },
            'assetId': {
                'value': event['body']['assetId'], 
                'validator': 'ID'
            },
        })
        if not valid:
            print(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        print("Listing Assets")
        if 'version' in event['body']:
            url = get_Asset(event['body']['databaseId'], event['body']['assetId'], event['body']['version'])
            if url == "Error: Asset not found":
                response['statusCode'] = 404
            elif url == "Error: Asset not distributable":
                response['statusCode'] = 401
            response['body'] = json.dumps({"message": url})
        else:
            url = get_Asset(event['body']['databaseId'], event['body']['assetId'], "")
            if url == "Error: Asset not found":
                response['statusCode'] = 404
            elif url == "Error: Asset not distributable":
                response['statusCode'] = 401
            response['body'] = json.dumps({"message": url})

        print(response)
        return response
    except (ClientError, Exception) as e:
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