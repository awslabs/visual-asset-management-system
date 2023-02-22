#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import sys
import json
from boto3.dynamodb.conditions import Key, Attr
import datetime
from decimal import Decimal
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from backend.common.validators import validate

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
newObject = {
    "databaseId": "",
    "assetId": "",
    "description": "No Description",
    "assetType": "We will put the extension here",
    "assetLocation": {
        "Bucket": "bucket",
        "Key": "key"
    },
    "previewLocation": {
        "Bucket": "bucket",
        "Key": "key"
    },
    "authEdit": [],
    "isDistributable": False,
    "currentVersion": {
        "Comment": "",
        "Version": "",
        "S3Version": "",
        "DateModified": "",
        "FileSize": ""
    },
    "versions": [
    ],
    "objectFamily": {
        "Parent": {
        },
        "Children": [
        ]
    },
    "specifiedPipelines": []
}

unitTest = {
    "body": {
        "databaseId": "Unit_Test",
        "assetId": "Unit_Test", #// Editable
        "bucket": "", #// Editable
        "key": "",
        "assetType": "",
        "description": "Testing as Usual", #// Editable
        "specifiedPipelines": [], #// will develop a query to list pipelines that can act as tags.
        "isDistributable": False, #// Editable
        "Comment": "Unit Test", #// Editable
        "previewLocation": {
            "Bucket": "",
            "Key": ""
        }
    }
}
unitTest['body']=json.dumps(unitTest['body'])


asset_Database = None
db_Database = None


try:
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_Database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")
    response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def _deserialize(raw_data):
    result = {}
    if not raw_data:
        return result

    deserializer = TypeDeserializer()

    for key, val in raw_data.items():
        result[key] = deserializer.deserialize(val)

    return result

def get_account_id():
    client = boto3.client("sts")
    return client.get_caller_identity()["Account"]

# Note
# (from https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.copy_object)
# You can store individual objects of up to 5 TB in Amazon S3.
# You create a copy of your object up to 5 GB in size in a single
# atomic action using this API. However, to copy an object greater
# than 5 GB, you must use the multipart upload Upload Part -
# Copy (UploadPartCopy) API. For more information, see Copy Object
# Using the REST Multipart Upload API.
#
def copy_object_and_return_new_version(bucket, key, asset):
    #VersionId and ContentLength (bytes)
    copy_source={
        'Bucket':bucket,
        'Key':key,
        'VersionId':asset['currentVersion']['S3Version']
    }
    account_id = get_account_id()
    resp=s3c.copy_object(
        Bucket=bucket,
        CopySource=copy_source,
        Key=key,
        ExpectedBucketOwner=account_id,
        ExpectedSourceBucketOwner=account_id,
    )
    asset['currentVersion']['S3Version'] = resp['VersionId']
    return asset

def assetReversion(item, version):
    asset = item
    print("Asset: ", asset)
    dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
    prevVersions = asset['versions']
    cV=asset['currentVersion']
    cV['previewLocation'] = {
        "Bucket": asset['previewLocation']['Bucket'],
        "Key": asset['previewLocation']['Key']
    }
    prevVersions.append(cV)
    asset['versions'] = prevVersions
    for i in prevVersions:
        if i["Version"]==version:
            asset['currentVersion']=i
            break
    
    bucket = asset['assetLocation']['Bucket']
    key = asset['assetLocation']['Key']
    asset = copy_object_and_return_new_version(bucket, key, asset)
    return asset


def revert_Asset(databaseId, assetId, version):
    table = dynamodb.Table(asset_Database)
    try:
        resp = table.query(
            KeyConditionExpression=Key('databaseId').eq(
                databaseId) & Key('assetId').eq(assetId),
            ScanIndexForward=False,
        )
        print(resp)
        item = resp['Items'][0]
        up = assetReversion(item,version)
        table.put_item(Item=up)
        print('Revert Asset '+ json.dumps(up))
        return json.dumps({"message": "Succeeded"})

    except Exception as e:
        print(e)
        return json.dumps({"message": str(e)}) 


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

    pathParams = event.get('pathParameters', {})
    print(pathParams)
    if 'databaseId' not in pathParams:
        message = "No database iD in API Call"
        response['body']=json.dumps({"message":message})
        print(response)
        return response
    databaseId = pathParams['databaseId']
    
    if 'assetId' not in pathParams:
        message = "No assetId iD in API Call"
        response['body']=json.dumps({"message":message})
        print(response)
        return response
    assetId = pathParams['assetId']

    try:
        print("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': pathParams['databaseId'], 
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

        if 'version' not in event['body']:
            message = "No version in API Call"
            response['body']=json.dumps({'message':message})
            print(message)
            return response
        version = event['body']['version']
        print("Trying to get Data")
        response['body'] = revert_Asset(databaseId, assetId, version)
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
