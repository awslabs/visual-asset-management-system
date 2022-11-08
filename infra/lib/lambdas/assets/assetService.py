import os
import boto3
import json
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from validators import validate

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
asset_database = None
unitTest = {
    "body": {
        "databaseId": "Unit_Test"
    }
}
unitTest['body']=json.dumps(unitTest['body'])

try:
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")
    response['body']['message'] = "Failed Loading Environment Variables"

def get_all_assets(queryParams, showDeleted=False):
    dynamodb = boto3.client('dynamodb')
    deserializer = TypeDeserializer()

    paginator = dynamodb.get_paginator('scan')
    operator = "NOT_CONTAINS"
    if showDeleted:
        operator = "CONTAINS"
    filter = {
        "databaseId":{
            "AttributeValueList":[ {"S":"#deleted"} ],
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

def get_assets(databaseId, showDeleted):
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

def get_asset(databaseId, assetId, showDeleted = False):
    table = dynamodb.Table(asset_database)
    if showDeleted:
        databaseId = databaseId + "#deleted"
    response = table.get_item(Key={'databaseId': databaseId, 'assetId': assetId})
    return response.get('Item', {}) 

def delete_asset(databaseId, assetId):
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
            archive_file(item['assetLocation'])
        if "previewLocation" in item:
            archive_file(item['previewLocation'])
        item['databaseId'] = databaseId + "#deleted"
        table.put_item(
            Item=item
        )
        result = table.delete_item(Key={'databaseId': databaseId, 'assetId': assetId})
        print(result)
        response['statusCode'] = 200
        response['message'] = "Asset deleted"
    return response

def archive_file(location):
    s3 = boto3.client('s3')

    bucket = ""
    key = ""
    if "Bucket" in location:
        bucket = location['Bucket']
    if "Key" in location:
        key = location['Key']

    if len(bucket)==0 or len(key)==0:
        return
    print("Archiving item: ", bucket, ":", key)

    source = {
        'Bucket': bucket,
        'Key': key
    }

    try:
        response = s3.copy(
            source, bucket, key,
            ExtraArgs = {
                'StorageClass': 'GLACIER',
                'MetadataDirective': 'COPY'
            }
        )
        print("S3 response: ", response)

    except s3.exceptions.InvalidObjectState as ios:
        print("S3 object already archived: ", key)
        print(ios)
    
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
        if httpMethod == 'GET':
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
                        response['body']=json.dumps({"message": message})
                        response['statusCode'] = 400
                        return response

                    print("Listing Assets for Database: ", pathParameters['databaseId'])
                    response['body'] = json.dumps({"message":get_assets(pathParameters['databaseId'], showDeleted)})
                    print(response)
                    return response
                else:
                    print("Listing All Assets")
                    response['body'] = json.dumps({"message":get_all_assets(queryParameters, showDeleted)})
                    print(response)
                    return response                    
            else:
                if 'databaseId' not in pathParameters:
                    message = "No database ID in API Call"
                    response['body']=json.dumps({"message":message})
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
                    response['body']=json.dumps({"message": message})
                    response['statusCode'] = 400
                    return response

                print("Getting Asset: ", pathParameters['assetId'])
                response['body'] = json.dumps({"message":get_asset(pathParameters['databaseId'], pathParameters['assetId'], showDeleted)})
                print(response)
                return response
        if httpMethod == 'DELETE':
            if 'databaseId' not in pathParameters:
                message = "No database ID in API Call"
                response['body']=json.dumps({"message":message})
                response['statusCode'] = 400
                print(response)
                return response
            if 'assetId' not in pathParameters:
                message = "No asset ID in API Call"
                response['body']=json.dumps({"message":message})
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
                response['body']=json.dumps({"message": message})
                response['statusCode'] = 400
                return response

            print("Deleting Asset: ", pathParameters['assetId'])
            result = delete_asset(pathParameters['databaseId'], pathParameters['assetId'])
            response['body'] = json.dumps({"message":result['message']})
            response['statusCode'] = result['statusCode']
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
