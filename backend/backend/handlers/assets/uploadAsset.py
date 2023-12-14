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
from backend.handlers.assets.assetCount import update_asset_count
from collections import defaultdict

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
asset_database = None
db_database = None

try:
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
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


def getS3MetaData(bucket: str, key: str, asset):
    if asset['isMultiFile']:
        return asset
    # VersionId and ContentLength (bytes)
    else:
        resp = s3c.head_object(Bucket=bucket, Key=key)
        asset['currentVersion']['S3Version'] = resp['VersionId']
        asset['currentVersion']['FileSize'] = str(
            resp['ContentLength'] / 1000000) + 'MB'
    return asset


def updateParent(asset, parent):
    table = dynamodb.Table(asset_database)
    try:
        databaseId = asset['databaseId']
        assetId = asset['assetId']
        assetS3Version = asset['currentVersion']['S3Version']
        assetVersion = asset['currentVersion']['Version']
        parentId = parent['assetId']
        parentdbId = parent['databaseId']
        pipeline = parent['specifiedPipeline']
        resp = table.query(
            KeyConditionExpression=Key('databaseId').eq(
                parentdbId) & Key('assetId').eq(parentId),
            ScanIndexForward=False,
        )
        item = ''
        if len(resp['Items']) == 0:
            raise ValueError('No Parent of that AssetId')
        else:
            item = resp['Items'][0]
            child = {
                'databaseId': databaseId,
                'assetId': assetId,
                'S3Version': assetS3Version,
                'Version': assetVersion,
                'specifiedPipeline': pipeline
            }
            item['currentVersion']['objectFamily']['Children'].append(child)
            if isinstance(item['currentVersion']['objectFamily']['Parent'], dict):
                _parent = item['currentVersion']['objectFamily']['Parent']
                updateParent(item, _parent)
        table.put_item(
            Item=item
        )
        return json.dumps({"message": "Succeeded"})
    except Exception as e:
        print(str(e))
        raise ValueError('Updating Parent Error ' + str(e))


def iter_Asset(body, item=None):
    asset = item
    version = 1
    dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
    if asset == None:
        asset = defaultdict(dict)
        asset['databaseId'] = body['databaseId']
        asset['assetId'] = body['assetId']
        asset['assetType'] = body['assetType']
        asset['assetLocation']['Key'] = body['key']
        asset['assetLocation']['Bucket'] = body['bucket']
    else:
        if 'versions' not in asset:
            prevVersions = []
        else:
            prevVersions = asset['versions']
        if 'previewLocation' in asset:
            asset['currentVersion']['previewLocation'] = asset['previewLocation']
        prevVersions.append(asset['currentVersion'])
        version = int(asset['currentVersion']['Version']) + 1
        asset['versions'] = prevVersions

    if 'previewLocation' in body and body['previewLocation'] is not None:
        asset['previewLocation'] = {
            "Bucket": body['previewLocation']['Bucket'],
            "Key": body['previewLocation']['Key']
        }

    asset['assetLocation'] = {
        "Bucket": body['bucket'],
        "Key": body['key']
    }
    asset['assetType'] = body['assetType']
    asset['currentVersion'] = {
        "Comment": body['Comment'],
        'Version': str(version),
        'S3Version': "",
        'DateModified': dtNow,
        'description': body['description'],
        'specifiedPipelines': body['specifiedPipelines']
    }
    asset['isMultiFile'] = body.get('isMultiFile', False)
    asset['specifiedPipelines'] = body['specifiedPipelines']
    asset['description'] = body['description']
    asset['isDistributable'] = body['isDistributable']
    # Since we started supporting folders / multiple files as a single asset
    # We will have no idea if the asset upload is complete at this point
    # TODO: Temporarily disabled revisioning information till we complete implementation for it.
    # asset = getS3MetaData(body['bucket'], body['key'], asset)

    # attributes for generated assets
    asset['assetName'] = body.get('assetName', body['assetId'])
    asset['pipelineId'] = body.get('pipelineId', "")
    asset['executionId'] = body.get('executionId', "")
    if 'Parent' in asset:
        asset['objectFamily']['Parent'] = asset['Parent']
        _parent = asset['Parent']
        updateParent(asset, _parent)
    return asset


def upload_Asset(body, queryParameters, returnAsset=False):
    table = dynamodb.Table(asset_database)
    try:
        resp = table.query(
            KeyConditionExpression=Key('databaseId').eq(
                body['databaseId']) & Key('assetId').eq(body['assetId']),
            ScanIndexForward=False,
        )
        # upload a new asset
        if not resp['Items'] and len(resp['Items']) == 0:
            up = iter_Asset(body)
            table.put_item(Item=up)
            print(up)
            # update assetCount after successful update of new asset
            update_asset_count(db_database, asset_database, queryParameters, body['databaseId'])
        # update an existing asset
        else:
            item = resp['Items'][0]
            up = iter_Asset(body, item)
            table.put_item(Item=up)
            print(up)
        if returnAsset:
            return json.dumps({"message": "Succeeded", "asset": up})
        else:
            return json.dumps({"message": "Succeeded"})
    except Exception as e:
        print(e)
        raise (e)


def lambda_handler(event, context):
    print(event)

    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])
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
        if 'databaseId' not in event['body']:
            message = "No databaseId in API Call"
            print(message)
            response['body'] = json.dumps({"message": message})
            return response

        if 'assetId' not in event['body']:
            message = "No assetId in API Call"
            print(message)
            response['body'] = json.dumps({"message": message})
            return response

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

        returnAsset = False
        if 'returnAsset' in event:
            returnAsset = True

        print("Trying to get Data")

        # prepare pagination query parameters for update asset count
        queryParameters = event.get('queryStringParameters', {})
        if 'maxItems' not in queryParameters:
            queryParameters['maxItems'] = 100
            queryParameters['pageSize'] = 100
        else:
            queryParameters['pageSize'] = queryParameters['maxItems']
        if 'startingToken' not in queryParameters:
            queryParameters['startingToken'] = None

        response['body'] = upload_Asset(event['body'], queryParameters, returnAsset)
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
