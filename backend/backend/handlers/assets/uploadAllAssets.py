#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.s3 import validateS3AssetExtensionsAndContentType

asset_Database = None
db_Database = None
workflow_execution_database = None
bucket_name = None
logger = safeLogger(service_name="UploadAllAssets")

try:
    bucket_name = os.environ["S3_ASSET_STORAGE_BUCKET"]
    upload_function = os.environ['UPLOAD_LAMBDA_FUNCTION_NAME']
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_Database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]

except Exception as e:
    logger.exception("Failed loading environment variables")
    raise

s3c = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
client = boto3.client('lambda')


def _lambda(payload): return client.invoke(FunctionName=upload_function,
                                           InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))

# def getS3MetaData(key, asset):
#     # VersionId and ContentLength (bytes)
#     resp = s3c.head_object(Bucket=bucket_name, Key=key)
#     asset['currentVersion']['S3Version'] = resp['VersionId']
#     asset['currentVersion']['FileSize'] = str(
#         resp['ContentLength']/1000000)+'MB'
#     return asset

def attach_execution_assets(assets, execution_id, database_id, asset_id, workflow_id):
    logger.info("Attaching assets to execution")

    asset_table = dynamodb.Table(asset_Database)
    all_assets = assets

    source_assets = asset_table.query(
        KeyConditionExpression=Key('databaseId').eq(database_id) & Key('assetId').begins_with(
            asset_id))
    logger.info("Source assets: ")
    logger.info(source_assets)
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

    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    claims_and_roles = request_to_claims(event)

    try:
        logger.info(event)
        if isinstance(event['body'], str):
            event['body'] = json.loads(event['body'])

        data = event['body']

        #Input validation
        if 'databaseId' not in event['body']:
            message = "No databaseId in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        if 'assetId' not in event['body']:
            message = "No assetId in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response
        
        if 'assetName' not in event['body']:
            message = "No assetName in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response
        
        if 'key' not in event['body']:
            message = "No file path key in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        logger.info("Validating parameters")
        #required fields
        (valid, message) = validate({
            'databaseId': {
                'value': event['body']['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': event['body']['assetId'],
                'validator': 'ID'
            },
            'assetName': {
                'value': event['body']['assetName'],
                'validator': 'OBJECT_NAME'
            },
            'description': {
                'value': event['body']['description'],
                'validator': 'STRING_256'
            },
            'assetPathKey': {
                'value': event['body']['key'],
                'validator': 'ASSET_PATH'
            }
        })
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        prefixAssetId = data['key'] if data['key'][0] != '/' else data['key'][1:]
        logger.info(prefixAssetId)

        #Split object key by path and return the first value (the asset ID)
        asset_idFromPath = prefixAssetId.split("/")[0]

        #Check if the asset ID is the same as the asset ID from the path
        if asset_idFromPath != event['body']['assetId']:
            response['body'] = json.dumps({"message": "Asset ID from key path does not match the asset ID"})
            response['statusCode'] = 400
            return response

        #ABAC Checks
        http_method = event['requestContext']['http']['method']
        operation_allowed_on_asset = False
        request_object = {
            "object__type": "api",
            "route__path": event['requestContext']['http']['path'] #"/" + event['requestContext']['http']['path'].split("/")[1]
        }
        logger.info(request_object)

        asset = {
            "object__type": "asset",
            "databaseId": event['body']['databaseId'],
            "assetName": event['body']['assetName'],
            "assetId": event['body']['assetId'],
            "assetType": event['body']['outputType'],
            "tags": event.get('body').get('tags', [])
        }

        logger.info(asset)

        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", asset, "PUT") and casbin_enforcer.enforce(
                    f"user::{user_name}", request_object, http_method):
                operation_allowed_on_asset = True
                break

        if operation_allowed_on_asset:
            #Do MIME check on whatever is uploaded to S3 at this point for this asset, before we do DynamoDB insertion, to validate it's not malicious
            if(not validateS3AssetExtensionsAndContentType(bucket_name, prefixAssetId)):
                #TODO: Delete asset and all versions of it from bucket
                #TODO: Change workflow so files get uplaoded first and then this function/workflow should run, error if no asset files are uploaded yet when running this
                response['statusCode'] = 403
                response['body'] = json.dumps({"message": "An uploaded asset contains a potentially malicious executable type object. Unable to process asset upload."})
                return response

            # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.list_objects_v2
            all_outputs = s3c.list_objects_v2(Bucket=bucket_name, Prefix=prefixAssetId)
            if 'IsTruncated' in all_outputs and all_outputs['IsTruncated']:
                logger.warning(
                    "WARN: s3 object listing exceeds 1,000 objects,"+
                    "this is unexpected for this operation with the bucket and prefix"+
                    bucket_name+" "+prefixAssetId
                )
            logger.info(all_outputs)
            assets = []
            if 'Contents' in all_outputs:
                files = [x['Key'] for x in all_outputs['Contents'] if '/' != x['Key'][-1]]
                for file in files:
                    outputType = data['outputType']
                    pipelineName = data['pipeline']
                    l_payload = {
                        "body": {
                            "databaseId": data['databaseId'],
                            "assetId": data['assetId'],
                            "assetName": data['assetName'],
                            "pipelineId": pipelineName,
                            "executionId": data['executionId'],
                            "tags": data.get("tags", []),
                            "key": file,
                            "assetType": outputType,
                            "description": data['description'],
                            "specifiedPipelines": [],
                            "isDistributable": True,
                            "Comment": "",
                            "previewLocation": {
                                "Key": ""
                            }
                        },
                        "returnAsset": True
                    }
                    logger.info("Uploading asset:")
                    logger.info(l_payload)
                    upload_response = _lambda(l_payload)
                    logger.info("uploadAsset response:")
                    logger.info(upload_response)
                    stream = upload_response.get('Payload', "")
                    if stream:
                        json_response = json.loads(stream.read().decode("utf-8"))
                        logger.info("uploadAsset payload:", json_response)
                        if "body" in json_response:
                            response_body = json.loads(json_response['body'])
                            if "asset" in response_body:
                                assets.append(response_body['asset'])
                    response['body'] = str(all_outputs)
            else:
                response['body'] = json.dumps({"message": "No files found"})
            attach_execution_assets(assets, data['executionId'], data['databaseId'], data['assetId'], data['workflowId'])
            return response
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except Exception as e:
        response['statusCode'] = 500
        logger.exception(e)
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
