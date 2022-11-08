import os
import boto3
import sys
import json
from boto3.dynamodb.conditions import Key, Attr
import datetime
from decimal import Decimal
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

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

unitTest2={'body':'{"databaseId":"Unit_Test","assetId":"Josh Is wrong","description":"descriptionsdf","isDistributable":false,"Comment":"ghsdf"}'}

asset_Database = None
db_Database = None

try:
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_Database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")
    response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def modifyAsset(item,body):
    asset = item
    dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
    if 'description' in body:
        asset['description']=body['description']
    if 'isDistributable' in body:
        asset['isDistributable']=body['isDistributable']
    if 'Comment' in body:
        asset['Comment']=body['Comment']
    return asset


def revert_Asset(body):
    table = dynamodb.Table(asset_Database)
    try:
        resp = table.query(
            KeyConditionExpression=Key('databaseId').eq(
                body['databaseId']) & Key('assetId').eq(body['assetId']),
            ScanIndexForward=False,
        )
        item = resp['Items'][0]
        up = modifyAsset(item,body)
        table.put_item(Item=up)
        print(up)
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
    event=json.loads(event['body'])
    print(event)
    try:
        if 'databaseId' not in event or 'assetId' not in event:
            message = "No databaseId, assetId, and/or version in API Call"
            response['body']=json.dumps({'message':message})
            return response
        print("Trying to get Data")
        response['body'] = revert_Asset(event)
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
            response['body'] = "Error in Lambda"
        print("Next entry.")
        return response
