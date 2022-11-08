import os
import boto3
import sys
import json
from boto3.dynamodb.conditions import Key, Attr
import datetime
from decimal import Decimal
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from validators import validate

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
        "Description": "",
        "Version": "",
        "S3Version": "",
        "DateModified": "",
        "FileSize": "",
        "objectFamily": {
            "Parent": "",
            "Children": [
            ]
        },
        "specifiedPipelines": []
    },
    "versions": [
    ],
}

unitTest = {
    "body": {
        "databaseId": "Unit_Test",
        "assetId": "Unit_Test",  # // Editable
        "bucket": "",  # // Editable
        "key": "",
        "assetType": "",
        "description": "Testing as Usual",  # // Editable
        # // will develop a query to list pipelines that can act as tags.
        "specifiedPipelines": [],
        "isDistributable": False,  # // Editable
        "Comment": "Unit Test",  # // Editable
        "previewLocation": {
            "Bucket": "",
            "Key": ""
        }
    }
}
unitTest['body'] = json.dumps(unitTest['body'])

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


def getS3MetaData(bucket, key, asset):
    #VersionId and ContentLength (bytes)
    resp = s3c.head_object(Bucket=bucket, Key=key)
    asset['currentVersion']['S3Version'] = resp['VersionId']
    asset['currentVersion']['FileSize'] = str(
        resp['ContentLength']/1000000)+'MB'
    return asset


def iter_DB(databaseId):
    table = dynamodb.Table(db_Database)
    table2 = dynamodb.Table(asset_Database)
    resp = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
    )
    resp2 = table2.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
    )
    count = len(resp2['Items'])
    item = resp['Items'][0]
    item['assetCount'] = str(count)
    print(item)
    table.put_item(Item=item)
    return

def updateParent(asset,parent):
    table=dynamodb.Table(asset_Database)
    try:
        databaseId=asset['databaseId']
        assetId=asset['assetId']
        assetS3Version=asset['currentVersion']['S3Version']
        assetVersion=asset['currentVersion']['Version']
        parentId=parent['assetId']
        parentdbId=parent['databaseId']
        pipeline=parent['specifiedPipeline']
        resp = table.query(
            KeyConditionExpression=Key('databaseId').eq(
                parentdbId) & Key('assetId').eq(parentId),
            ScanIndexForward=False,
        )
        item=''
        if len(resp['Items']) == 0:
            raise ValueError('No Parent of that AssetId') 
        else:
            item= resp['Items'][0]
            child={
                'databaseId':databaseId,
                'assetId':assetId,
                'S3Version':assetS3Version,
                'Version':assetVersion,
                'specifiedPipeline':pipeline
            }
            item['currentVersion']['objectFamily']['Children'].append(child)
            if isinstance(item['currentVersion']['objectFamily']['Parent'],dict):
                _parent=item['currentVersion']['objectFamily']['Parent']
                updateParent(item,_parent)
        table.put_item(
            Item=item
        )
        return json.dumps({"message": "Succeeded"})
    except Exception as e:
        print(str(e))
        raise ValueError('Updating Parent Error '+str(e))

def iter_Asset(body, item=None):
    asset = item
    version = 0
    dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
    if asset == None:
        asset = newObject
        asset['databaseId'] = body['databaseId']
        asset['assetId'] = body['assetId']
        asset['assetType'] = body['assetType']
        asset['assetLocation']['Key'] = body['key']
        asset['assetLocation']['Bucket'] = body['bucket']
        iter_DB(body['databaseId'])
    else:
        prevVersions = asset['versions']
        asset['currentVersion']['previewLocation']=asset['previewLocation']
        prevVersions.append(asset['currentVersion'])
        version = int(asset['currentVersion']['Version'])+1
        asset['versions'] = prevVersions
    asset['previewLocation'] = {
        "Bucket": body['previewLocation']['Bucket'],
        "Key": body['previewLocation']['Key']
    }
    asset['currentVersion'] = {
        "Comment": body['Comment'],
        'Version': str(version),
        'S3Version': "",
        'DateModified': dtNow,
        'description':body['description'],
        'specifiedPipelines':body['specifiedPipelines']
    }
    asset['specifiedPipelines']=body['specifiedPipelines']
    asset['description'] = body['description']
    asset['isDistributable'] = body['isDistributable']
    asset = getS3MetaData(body['bucket'], body['key'], asset)
    
    #attributes for generated assets
    asset['assetName'] = body.get('assetName', body['assetId'])
    asset['pipelineId'] = body.get('pipelineId', "")
    asset['executionId'] = body.get('executionId', "")

    if 'Parent' in asset:
        asset['objectFamily']['Parent']=asset['Parent']
        _parent=asset['Parent']
        updateParent(asset,_parent)
    return asset


def upload_Asset(body, returnAsset=False):
    table = dynamodb.Table(asset_Database)
    try:
        resp = table.query(
            KeyConditionExpression=Key('databaseId').eq(
                body['databaseId']) & Key('assetId').eq(body['assetId']),
            ScanIndexForward=False,
        )
        if len(resp['Items']) == 0:
            up = iter_Asset(body)
            table.put_item(Item=up)
        else:
            item = resp['Items'][0]
            up = iter_Asset(body, item)
            table.put_item(Item=up)
            print(up)
        if returnAsset:
            return json.dumps({"message": "Succeeded", "asset":up})
        else:
            return json.dumps({"message": "Succeeded"})
    except Exception as e:
        print(e)
        raise(e)


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
            response['body']=json.dumps({"message": message})
            return response
        
        if 'assetId' not in event['body']:
            message = "No assetId in API Call"
            print(message)
            response['body']=json.dumps({"message": message})
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
            'assetType': {
                'value':  event['body']['assetType'], 
                'validator': 'FILE_EXTENSION'
            }, 
            'description': {
                'value':  event['body']['description'], 
                'validator': 'STRING_256'
            }
        })
        if not valid:
            print(message)
            response['body']=json.dumps({"message": message})
            response['statusCode'] = 400
            return response
        
        returnAsset = False
        if 'returnAsset' in event:
            returnAsset = True

        print("Trying to get Data")
        response['body'] = upload_Asset(event['body'], returnAsset)
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
