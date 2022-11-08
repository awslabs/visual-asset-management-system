import os
import boto3
import json
from boto3.dynamodb.conditions import Key, Attr
from validators import validate

dynamodb = boto3.resource('dynamodb')
s3c = boto3.resource('s3')
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
except:
    print("Failed Loading Environment Variables")
    response['body']['message'] = "Failed Loading Environment Variables"


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
    if len(items) == 0:
        return "Failure"
    item = items[0]
    bucket = item['assetLocation']['Bucket']
    key = item['assetLocation']['Key']
    s3version = ''
    if version != "Latest" or version != "" or version != item['currentVersion']['Version']:
        return s3c.generate_presigned_url('get_object', Params={
            'Bucket': bucket,
            'Key': key
        }, ExpiresIn=10)
    else:
        versions = item['versions']
        for i in version:
            if i['Version'] == versions:
                return s3c.generate_presigned_url('get_object', Params={
                    'Bucket': bucket,
                    'Key': key,
                    'VersionId': i['S3Version']
                }, ExpiresIn=10)
        return "Failure"


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
            response['body']=json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        print("Listing Assets")
        if 'version' in event['body']:
            response['body'] = json.dumps({"message": get_Assets(
                event['body']['databaseId'], event['body']['assetId'], event['body']['version'])})
        else:
            response['body'] = json.dumps({"message": get_Assets(
                event['body']['databaseId'], event['body']['assetId'], "")})
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