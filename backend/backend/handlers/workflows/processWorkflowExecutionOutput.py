#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import botocore
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
    read_metadata_function = os.environ['READ_METADATA_LAMBDA_FUNCTION_NAME']
    create_metadata_function = os.environ['CREATE_METADATA_LAMBDA_FUNCTION_NAME']
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_Database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]

except Exception as e:
    logger.exception("Failed loading environment variables")
    raise

s3c = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
client = boto3.client('lambda')


def _lambda_upload(payload): return client.invoke(FunctionName=upload_function,
                                           InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))

def _lambda_read_metadata(payload): return client.invoke(FunctionName=read_metadata_function,
                                           InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))

def _lambda_create_metadata(payload): return client.invoke(FunctionName=create_metadata_function,
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


def verify_get_path_objects(bucketName: str, pathPrefix: str):

    #Do MIME check on whatever is uploaded to S3 at this point for this asset, before we do DynamoDB insertion, to validate it's not malicious
    if(not validateS3AssetExtensionsAndContentType(bucket_name, pathPrefix)):
        raise Exception("Pipeline uploaded objects contains a potentially malicious executable type object. Unable to process asset upload.")

    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.list_objects_v2
    all_outputs = s3c.list_objects_v2(Bucket=bucket_name, Prefix=pathPrefix)
    if 'IsTruncated' in all_outputs and all_outputs['IsTruncated']:
        logger.warning(
            "WARN: s3 object listing exceeds 1,000 objects,"+
            "this is unexpected for this operation with the bucket and prefix"+
            bucket_name+" "+pathPrefix
        )
    logger.info(all_outputs)

    return all_outputs


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    logger.info(event)

    if 'body' in event:
        if isinstance(event['body'], str):
            event['body'] = json.loads(event['body'])
    else:
        message = "No Body in API Call"
        logger.error(message)
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": message})
        return response
    
    try:
        #sub in body for event
        event = event["body"]

        #Input validation
        if 'databaseId' not in event:
            message = "No databaseId in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        if 'assetId' not in event:
            message = "No assetId in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response
        
        if 'executingRequestContext' not in event:
            message = "No executingRequestContext in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        if 'executingUserName' not in event:
            message = "No executingUserName in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response
        

        logger.info("Validating parameters")
        #required fields
        (valid, message) = validate({
            'databaseId': {
                'value': event['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': event['assetId'],
                'validator': 'ID'
            },
            'executingUserName': {
                'value': event['executingUserName'],
                'validator': 'USERID'
            },
            'assetFilesPathPipelineKey': {
                'value': event.get("filesPathKey", ""),
                'validator': 'ASSET_PATH_PIPELINE',
                'optional': True
            },
            'assetMetadataPathPipelineKey': {
                'value': event.get('metadataPathKey',""),
                'validator': 'ASSET_PATH_PIPELINE',
                'optional': True
            },
            'assetPreviewPathPipelineKey': {
                'value': event.get('previewPathKey', ""),
                'validator': 'ASSET_PATH_PIPELINE',
                'optional': True
            }
        })
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response
        
        userName = event['executingUserName']
        requestContext = event['executingRequestContext']

        #ABAC Checks for Asset
        #ABAC Implementation Deviation - Not called through API. Username passed through Pipeline Execution Call. 
        asset = {
            "object__type": "asset",
            "databaseId": event['databaseId'],
            "assetName": event['assetId'],
            "assetType": event['outputType'],
            "tags": event.get('tags', [])
        }
        logger.info(asset)

        casbin_enforcer = CasbinEnforcer(userName)
        if casbin_enforcer.enforce(f"user::{userName}", asset, "PUT"):
            operation_allowed_on_asset = True

        if operation_allowed_on_asset:
            #Handle preview outputs
            if ('previewPathKey' in event):
                previewPathKey = event['previewPathKey']
                
                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(bucket_name, previewPathKey)
                except Exception as e:
                    logger.error(e)

                if 'Contents' in objectsFound:
                    files = [x['Key'] for x in objectsFound['Contents'] if '/' != x['Key'][-1]]

                    if(len(files) > 1):
                        logger.error("Multiple files present in pipeline output preview folder. Limiting to top 1 for now.")

                    for file in files:
                        #Only process files that end in image extension for now
                        if file.endswith('.jpeg') or file.endswith('.jpg') or file.endswith('.png'):
                            logger.info("PREVIEW UPLOAD TO BE IMPLEMENTED") 
                            #TODO: Add preview file upload

                            break #only process 1 preview file for now
                        else:
                            logger.error("Files present in pipeline output preview folder outside of an image. Skipping...")

            #Handle metadata outputs
            if('metadataPathKey' in event):
                metadataPathKey = event['metadataPathKey']

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(bucket_name, metadataPathKey)
                except Exception as e:
                    logger.error(e)

                if 'Contents' in objectsFound:
                    metadata = {}

                    #Get existing metadata through read metadata invoke
                    l_payload = {
                        "requestContext": requestContext,
                        "pathParameters": {
                            "databaseId": event['databaseId'],
                            "assetId": event['assetId']
                        } 
                    }
                    logger.info("Getting metadata")
                    logger.info(l_payload)
                    read_metadata_response = _lambda_read_metadata(l_payload)
                    logger.info("read metadata response:")
                    #logger.info(read_metadata_response)
                    stream = read_metadata_response['Payload']
                    if stream:
                        json_response = json.loads(stream.read().decode("utf-8"))
                        logger.info("readMetadata payload:")
                        logger.info(json_response)
                        if "body" in json_response:
                            response_body = json.loads(json_response['body'])
                            if "metadata" in response_body:
                                metadata = response_body['metadata']

                    #Check if we don't yet have any metadata for this asset (shouldn't be possible so another fault must have occured)
                    if('assetId' in metadata and 'databaseId' in metadata):
                        files = [x['Key'] for x in objectsFound['Contents'] if '/' != x['Key'][-1]]
                        logger.info("Files present in pipeline output metadata folder:")
                        logger.info(files)
                        for file in files:
                            #Only process files that end in JSON extension for now
                            if file.lower().endswith('.json'):
                                objectResponse = s3c.get_object(Bucket=bucket_name, Key=file)
                                objectData = objectResponse['Body'].read().decode("utf-8")
                                data = {}
                                try:
                                    data = json.loads(objectData)
                                    logger.info(data)
                                except Exception as e:
                                    logger.error("Metadata object type for Pipeline Process Output File is not JSON parsable")
                                    logger.error(e)

                                #Loop through each key/value in JSON dictionary and add to or update existing entries
                                for k, v in data.items():
                                    if isinstance(v, dict):
                                        logger.warn("Not able to process sub-dictionaries right now for metadata elements")
                                    elif isinstance(v, list):
                                        #Check if first element is a string, if it is, join all of them together as a comma-deliminated list
                                        if isinstance(v[0], str):
                                            metadata[str(k)] = ",".join(v)
                                    else:
                                        if(str(k) != 'assetId' and str(k) != 'databaseId' and str(v) != ""):
                                            metadata[str(k)] = str(v)
                            else:
                                logger.error("Files present in pipeline output metadata folder outside of JSON. Skipping...")

                        logger.info(metadata)

                        #Pop off assetId and databaseId from metadata (otherwise won't save on invoke as they are the primary/sort keys on DB)
                        metadata.pop('assetId', None)
                        metadata.pop('databaseId', None)

                        #Make sure we don't have an empty dictionary, and then save
                        if metadata:
                            #Conduct final save of metadata
                            l_payload = {
                                "requestContext": requestContext,
                                "pathParameters": {
                                    "databaseId": event['databaseId'],
                                    "assetId": event['assetId']
                                },
                                "body": json.dumps({
                                    "version": "1",
                                    "metadata": metadata
                                })
                            }
                            logger.info("Saving metadata")
                            logger.info(l_payload)
                            create_metadata_response = _lambda_create_metadata(l_payload)
                            logger.info("create metadata response:")
                            logger.info(read_metadata_response)
                            stream = create_metadata_response['Payload']
                            if stream:
                                json_response = json.loads(stream.read().decode("utf-8"))
                                logger.info("createMetadata payload:")
                                logger.info(json_response)
                                if "statusCode" in json_response:
                                    #log error if we have anything but a 200 status code
                                    if(json_response['statusCode'] != 200):
                                        logger.error("Error code not 200 for saving metadata back to database for asset. Skipping...")
                                else:
                                    logger.error("Error status code not present for saving metadata back to database for asset. Skipping...")
                        else:
                            logger.warn("Empty metadata dictionary on save. Skipping....")

                    else:
                        logger.error("Metadata database entry not found for asset. Skipping...")

            #Handle asset file outputs
            if('filesPathKey' in event):
                filesPathKey = event['filesPathKey']

                objectsFound = {}
                try:
                    objectsFound = verify_get_path_objects(bucket_name, filesPathKey)
                except Exception as e:
                    logger.error(e)

                assets = []
                if 'Contents' in objectsFound:
                    files = [x['Key'] for x in objectsFound['Contents'] if '/' != x['Key'][-1]]
                    logger.info("Files present in pipeline output asset folder:")
                    logger.info(files)
                    for file in files:
                        outputType = event['outputType']
                        pipelineName = event['pipeline']
                        l_payload = {
                            "requestContext": requestContext,
                            "body": {
                                "databaseId": event['databaseId'],
                                "assetId": event['assetId'],
                                #"assetName": event['assetName'], #We don't have asset ID and is not required for updating an existing asset by uploadAsset
                                "pipelineId": pipelineName,
                                "executionId": event['executionId'],
                                "tags": event.get("tags", []),
                                "key": file,
                                "assetType": outputType,
                                #"description": event['description'],#We don't have asset description and is not required for updating an existing asset by uploadAsset
                                "specifiedPipelines": [],
                                "isDistributable": True,
                                "uploadTempLocation": True,
                                "Comment": "",
                                "assetLocation": {
                                    "Key": file
                                },
                                "previewLocation": {
                                    "Key": ""
                                }
                            },
                            "returnAsset": True
                        }
                        logger.info("Uploading asset:")
                        logger.info(l_payload)
                        upload_response = _lambda_upload(l_payload)
                        logger.info("uploadAsset response:")
                        logger.info(upload_response)
                        stream = upload_response['Payload']
                        if stream:
                            json_response = json.loads(stream.read().decode("utf-8"))
                            logger.info("uploadAsset payload:")
                            logger.info(json_response)
                            if "body" in json_response:
                                response_body = json.loads(json_response['body'])
                                if "asset" in response_body:
                                    assets.append(response_body['asset'])
                        response['body'] = str(objectsFound)

                    attach_execution_assets(assets, event['executionId'], event['databaseId'], event['assetId'], event['workflowId'])


            response['statusCode'] = 200
            response['body'] = json.dumps({"message": "Workflow Execution Output Processing Complete"})
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
