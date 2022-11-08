import boto3
import json
import os
from boto3.dynamodb.conditions import Key
from validators import validate

s3_client = boto3.client('s3')
dynamo_client = boto3.resource('dynamodb')

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
    asset_database = os.environ['ASSET_STORAGE_TABLE_NAME']
except:
    print("Failed loading environment variables")
    
    response['body'] = json.dumps(
    {"message": "Failed Loading Environment Variables"})
    response['statusCode'] = 500

def get_asset_path(databaseId, assetId):
    print("Trying to get asset from database")
    table = dynamo_client.Table(asset_database)
    record = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('assetId').eq(assetId)
    )
    return (record['Items'][0]['assetLocation'])

def get_headers(bucket, key):
    print("Trying to get headers")
    resp = s3_client.select_object_content(
        Bucket=bucket,
        Key=key,
        ExpressionType='SQL',
        Expression="SELECT * FROM s3object limit 1",
        InputSerialization = {'CSV': {"FileHeaderInfo": "NONE"}},
        OutputSerialization = {'CSV': {}},
    )
    records = []
    for event in resp['Payload']:
        if 'Records' in event:
            record = event['Records']['Payload'].decode('utf-8');
            print(record)
            records.append(record)
    return records

def get_records(bucket, key, columnNames):
    print("Trying to get records")
    resp = s3_client.select_object_content(
        Bucket=bucket,
        Key=key,
        ExpressionType='SQL',
        Expression=f"SELECT {columnNames} FROM s3object",
        InputSerialization = {'CSV': {"FileHeaderInfo": "USE"}},
        OutputSerialization = {'CSV': {}},
    )
    records = []
    for event in resp['Payload']:
        if 'Records' in event:
            record = event['Records']['Payload'].decode('utf-8');
            records.append(record)
    return records

def split_records(records):
    result = []
    for record in records:
        rows = record.split("\n")
        for row in rows:
            row = row.split(",")
            result.append(row)
    return result

def validateColumnNames(headers, columns):
    for column in columns:
        if column not in headers:
            raise ValueError(f"{column} is not present in asset")


def get_metadata(databaseId, assetId, columnNames):
    location = get_asset_path(databaseId, assetId)
    headers = get_headers(location['Bucket'], location['Key'])
    header_records = split_records(headers)
    columns = columnNames.split(",")
    validateColumnNames(header_records[0], columns)
    records = get_records(location['Bucket'], location['Key'], columnNames)
    item_records = split_records(records)
    print("Constructing result")
    items = []
    for item in item_records[:-1]:
        items.append(dict(zip(columns, item)))

    result = {}
    result['Items']= items
    return result


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
    queryParams = event.get('queryStringParameters', {})
    pathParams = event.get('pathParameters', {})
    
    try:
        if 'assetId' not in pathParams or 'databaseId' not in pathParams:
            print("assetId or databaseId parameter is not present")
            message = "Required parameters not present in the request"
            response['body']=json.dumps({"message": message})
            response['statusCode'] = 400
            return response
        if 'list' not in queryParams:
            print("list parameter is not present")
            message = "list parameter is required to fetch the columns"
            response['body']=json.dumps({"message": message})
            response['statusCode'] = 400
            return response     
        else:
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

            print("Fetching metadata")
            result = get_metadata(pathParams['databaseId'], pathParams['assetId'], queryParams['list'])
            print(result)
            response['body'] = json.dumps({"message": result})
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