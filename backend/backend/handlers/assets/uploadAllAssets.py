#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import pathlib
import uuid
from boto3.dynamodb.conditions import Key

asset_Database = None
db_Database = None
workflow_execution_database = None

try:
    upload_function=os.environ['UPLOAD_LAMBDA_FUNCTION_NAME']
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_Database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]

except Exception as e:
    print("Failed Loading Environment Variables")
    raise
s3c = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
client = boto3.client('lambda')
_lambda = lambda payload: client.invoke(FunctionName=upload_function,InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))

def getS3MetaData(bucket, key, asset):
    #VersionId and ContentLength (bytes)
    resp = s3c.head_object(Bucket=bucket, Key=key)
    asset['currentVersion']['S3Version'] = resp['VersionId']
    asset['currentVersion']['FileSize'] = str(
        resp['ContentLength']/1000000)+'MB'
    return asset

def attach_execution_assets(assets, execution_id, database_id, asset_id, workflow_id):
    print("Attaching assets to execution")

    asset_table = dynamodb.Table(asset_Database)
    all_assets = assets

    source_assets = asset_table.query(
        KeyConditionExpression=Key('databaseId').eq(database_id) & Key('assetId').begins_with(
            asset_id)        )
    print("Source assets: ", source_assets)
    if source_assets['Items']:
        all_assets.append(source_assets['Items'][0])

    table = dynamodb.Table(workflow_execution_database)
    pk = f'{asset_id}-{workflow_id}'

    table.update_item(
        Key={'pk': pk, 'sk': execution_id},
        UpdateExpression='SET #attr1 = :val1',
        ExpressionAttributeNames={'#attr1': 'assets'},
        ExpressionAttributeValues={':val1': all_assets}
    )

    return

def lambda_handler(event, context):
    print(event)
    if isinstance(event['body'], str):
        data = json.loads(event['body'])
    else:
        data = event['body']
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
    
    bucket_name = data['bucket']
    prefix = data['key'] if data['key'][0] != '/' else data['key'][1:]
    print(bucket_name, prefix)

    s3 = boto3.client("s3")
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.list_objects_v2
    all_outputs = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
    if 'IsTruncated' in all_outputs and all_outputs['IsTruncated']:
        print(
            "WARN: s3 object listing exceeds 1,000 objects,",
            "this is unexpected for this operation with the bucket and prefix",
            bucket_name, prefix
        )
    print(all_outputs)
    assets = []
    if 'Contents' in all_outputs:
        files = [x['Key'] for x in all_outputs['Contents'] if '/' != x['Key'][-1]]
        for file in files:
            outputType = data['outputType']
            pipelineName = data['pipeline']
            asset_prefix = data['executionId']
            asset_uuid = str(uuid.uuid4())
            l_payload = {
                "body": {
                    "databaseId": data['databaseId'],
                    "assetId": f"asset_{asset_prefix}_{asset_uuid}"[0:60],
                    "assetName": f"{file.split('/')[-1]}",
                    "pipelineId": pipelineName,
                    "executionId": data['executionId'],
                    "bucket": bucket_name,
                    "key": file,
                    "assetType": outputType,
                    "description": data['description'],
                    "specifiedPipelines": [],
                    "isDistributable": True,
                    "Comment": "",
                    "previewLocation": {
                        "Bucket": "",
                        "Key": ""
                    }
                },
                "returnAsset": True
            }
            print("Uploading asset:", l_payload)
            upload_response = _lambda(l_payload)
            print("uploadAsset response:", upload_response)
            stream = upload_response.get('Payload', "")
            if stream:
                json_response = json.loads(stream.read().decode("utf-8"))
                print("uploadAsset payload:", json_response)
                if "body" in json_response:
                    response_body = json.loads(json_response['body'])
                    if "asset" in response_body:
                        assets.append(response_body['asset'])
            response['body'] = str(all_outputs)
    else:
        response['body'] = "No files found"
    attach_execution_assets(assets, data['executionId'], data['databaseId'], data['assetId'], data['workflowId'])
    return(response)