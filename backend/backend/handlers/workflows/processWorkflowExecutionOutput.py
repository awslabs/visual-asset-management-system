#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import botocore
import json
import uuid
from datetime import datetime, timedelta
from boto3.dynamodb.conditions import Key
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.s3 import validateS3AssetExtensionsAndContentType
from models.assetsV3 import AssetUploadTableModel

asset_Database = None
db_Database = None
workflow_execution_database = None
asset_upload_table_name = None
s3_asset_buckets_table = None
logger = safeLogger(service_name="ProcessWorkflowExecutionOutput")

# Constants
UPLOAD_EXPIRATION_DAYS = 1  # TTL for upload records for pipeline output

try:
    s3_asset_buckets_table = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    read_metadata_function = os.environ['READ_METADATA_LAMBDA_FUNCTION_NAME']
    create_metadata_function = os.environ['CREATE_METADATA_LAMBDA_FUNCTION_NAME']
    file_upload_function = os.environ['FILE_UPLOAD_LAMBDA_FUNCTION_NAME']
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_upload_table_name = os.environ["ASSET_UPLOAD_TABLE_NAME"]
    db_Database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]

except Exception as e:
    logger.exception("Failed loading environment variables")
    raise

s3c = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
client = boto3.client('lambda')
asset_upload_table = dynamodb.Table(asset_upload_table_name)
buckets_table = dynamodb.Table(s3_asset_buckets_table)


def _lambda_read_metadata(payload): return client.invoke(FunctionName=read_metadata_function,
                                           InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))

def _lambda_create_metadata(payload): return client.invoke(FunctionName=create_metadata_function,
                                           InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))

def _lambda_file_ingestion(payload): return client.invoke(FunctionName=file_upload_function,
                                           InvocationType='RequestResponse', Payload=json.dumps(payload).encode('utf-8'))

# def attach_execution_assets(assets, execution_id, database_id, asset_id, workflow_id):
#     logger.info("Attaching assets to execution")

#     asset_table = dynamodb.Table(asset_Database)
#     all_assets = assets

#     source_assets = asset_table.query(
#         KeyConditionExpression=Key('databaseId').eq(database_id) & Key('assetId').begins_with(
#             asset_id))
#     logger.info("Source assets: ")
#     logger.info(source_assets)
#     if source_assets['Items']:
#         all_assets.append(source_assets['Items'][0])

#     table = dynamodb.Table(workflow_execution_database)
#     pk = f'{asset_id}-{workflow_id}'

#     table.update_item(
#         Key={'pk': pk, 'sk': execution_id},
#         UpdateExpression='SET #attr1 = :val1',
#         ExpressionAttributeNames={'#attr1': 'assets'},
#         ExpressionAttributeValues={':val1': all_assets}
#     )

#     return

def get_default_bucket_details(bucketId):
    """Get default S3 bucket details from database default bucket DynamoDB"""
    try:

        bucket_response = buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(bucketId),
            Limit=1
        )
        # Use the first item from the query results
        bucket = bucket_response.get("Items", [{}])[0] if bucket_response.get("Items") else {}
        bucket_id = bucket.get('bucketId')
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix')

        #Check to make sure we have what we need
        if not bucket_name or not base_assets_prefix:
            raise Exception(f"Error getting database default bucket details: missing bucket_name or base_assets_prefix")
        
        #Make sure we end in a slash for the path
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'

        # Remove leading slash from file path if present
        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]

        return {
            'bucketId': bucket_id,
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
    except Exception as e:
        logger.exception(f"Error getting bucket details: {e}")
        raise Exception(f"Error getting bucket details: {str(e)}")

def verify_get_path_objects(bucketName: str, pathPrefix: str):

    #Do MIME check on whatever is uploaded to S3 at this point for this asset, before we do DynamoDB insertion, to validate it's not malicious
    if(not validateS3AssetExtensionsAndContentType(bucketName, pathPrefix)):
        raise Exception("Pipeline uploaded objects contains a potentially malicious executable type object. Unable to process asset upload.")

    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.list_objects_v2
    all_outputs = s3c.list_objects_v2(Bucket=bucketName, Prefix=pathPrefix)
    if 'IsTruncated' in all_outputs and all_outputs['IsTruncated']:
        logger.warning(
            "WARN: s3 object listing exceeds 1,000 objects,"+
            "this is unexpected for this operation with the bucket and prefix"+
            bucketName+" "+pathPrefix
        )
    logger.info(all_outputs)

    return all_outputs

def lookup_existing_asset(database_id, asset_id):
    asset_table = dynamodb.Table(asset_Database)
    asset = asset_table.get_item(
        Key={'databaseId': database_id, 'assetId': asset_id})
    if 'Item' in asset:
        return asset['Item']
    else:
        return None

def create_external_upload_record(asset_id, database_id, upload_type, temporary_prefix):
    """Create an external upload record in DynamoDB"""
    try:
        # Generate upload ID
        upload_id = f"y{str(uuid.uuid4())}"
        
        # Calculate expiration time (7 days from now)
        now = datetime.utcnow()
        expires_at = int((now + timedelta(days=UPLOAD_EXPIRATION_DAYS)).timestamp())
        
        # Create upload record
        upload_record = AssetUploadTableModel(
            uploadId=upload_id,
            assetId=asset_id,
            databaseId=database_id,
            uploadType=upload_type,
            createdAt=now.isoformat(),
            expiresAt=expires_at,
            totalFiles=0,  # Will be updated later
            totalParts=0,  # Not relevant for external uploads
            status="initialized",
            isExternalUpload=True,
            temporaryPrefix=temporary_prefix
        )
        
        # Save to DynamoDB
        asset_upload_table.put_item(Item=upload_record.to_dict())
        
        return upload_id
    except Exception as e:
        logger.exception(f"Error creating external upload record: {e}")
        raise e

def update_s3_object_metadata(key, asset_id, database_id, upload_id, bucket_name):
    """Update S3 object metadata with asset and upload information"""
    try:
        # Get current object metadata
        head_response = s3c.head_object(Bucket=bucket_name, Key=key)
        content_type = head_response.get('ContentType', 'application/octet-stream')
        
        # Copy object to itself with new metadata
        s3c.copy_object(
            CopySource={'Bucket': bucket_name, 'Key': key},
            Bucket=bucket_name,
            Key=key,
            ContentType=content_type,
            Metadata={
                'databaseid': database_id,
                'assetid': asset_id,
                'uploadid': upload_id
            },
            MetadataDirective='REPLACE'
        )
        
        return True
    except Exception as e:
        logger.exception(f"Error updating S3 object metadata: {e}")
        return False

def process_external_upload(upload_id, asset_id, database_id, upload_type, files, temporary_prefix, request_context):
    """Process an external upload using the fileIngestion Lambda"""
    try:
        # Prepare the request payload
        file_list = []
        for file_key in files:
            # Extract the file name from the key
            file_name = os.path.basename(file_key)
            
            # Add to file list
            file_list.append({
                "key": file_name,
                "tempKey": file_key
            })
        
        # Create the request body
        body = {
            "assetId": asset_id,
            "databaseId": database_id,
            "uploadType": upload_type,
            "files": file_list
        }
        
        # Create the Lambda payload to simulate an API Gateway request
        lambda_payload = {
            "requestContext": request_context,
            "pathParameters": {
                "uploadId": upload_id
            },
            "body": json.dumps(body),
            "path": f"/uploads/{upload_id}/complete/external",
            "httpMethod": "POST"
        }
        
        # Invoke the Lambda function
        response = _lambda_file_ingestion(lambda_payload)
        
        # Process the response
        if response and 'Payload' in response:
            stream = response['Payload']
            if stream:
                json_response = json.loads(stream.read().decode("utf-8"))
                logger.info("fileIngestion response:")
                logger.info(json_response)
                
                if "statusCode" in json_response and json_response["statusCode"] == 200:
                    if "body" in json_response:
                        return json.loads(json_response["body"])
                    else:
                        logger.error("No body in fileIngestion response")
                        return None
                else:
                    logger.error(f"Error in fileIngestion response: {json_response}")
                    return None
            else:
                logger.error("No payload stream in fileIngestion response")
                return None
        else:
            logger.error("Invalid response from fileIngestion Lambda")
            return None
    except Exception as e:
        logger.exception(f"Error processing external upload: {e}")
        return None


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    logger.info(event)

    try:
        if 'body' in event:
            if isinstance(event['body'], str):
                event['body'] = json.loads(event['body'])
        else:
            message = "No Body in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response
        
        #sub in body for event
        event = event["body"]

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

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
                'validator': 'ASSET_ID'
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

        # Get existing asset
        asset = lookup_existing_asset(event['databaseId'], event['assetId'])
        if not asset:
            logger.error(f"Asset {event['assetId']} not found in database {event['databaseId']}")
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": f"Asset {event['assetId']} not found in database {event['databaseId']}"})
            return response

        #ABAC Checks for Asset
        #ABAC Implementation Deviation - Not called through API. Username passed through Pipeline Execution Call.
        asset.update({
            "object__type": "asset",
        })
        logger.info(asset)

        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(asset, "PUT"):
                operation_allowed_on_asset = True

        if operation_allowed_on_asset:
            # Get bucket details from asset's bucketId
            bucketDetails = get_default_bucket_details(asset['bucketId'])
            bucket_name = bucketDetails['bucketName']

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
                    
                    # Filter for image files
                    image_files = [f for f in files if f.endswith('.jpeg') or f.endswith('.jpg') or f.endswith('.png')]
                    
                    if image_files:
                        # Only process the first image file
                        preview_file = image_files[0]
                        
                        try:
                            # Create external upload record
                            upload_id = create_external_upload_record(
                                event['assetId'],
                                event['databaseId'],
                                "assetPreview",
                                previewPathKey
                            )
                            
                            # Update S3 object metadata
                            update_s3_object_metadata(
                                preview_file,
                                event['assetId'],
                                event['databaseId'],
                                upload_id,
                                bucket_name
                            )
                            
                            # Process the external upload
                            result = process_external_upload(
                                upload_id,
                                event['assetId'],
                                event['databaseId'],
                                "assetPreview",
                                [preview_file],
                                previewPathKey,
                                requestContext
                            )
                            
                            if result:
                                logger.info("Preview upload completed successfully")
                            else:
                                logger.error("Preview upload failed")
                        except Exception as e:
                            logger.exception(f"Error processing preview upload: {e}")
                    else:
                        logger.error("No image files found in preview folder")

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
                    
                    if files:
                        try:
                            # Create external upload record
                            upload_id = create_external_upload_record(
                                event['assetId'],
                                event['databaseId'],
                                "assetFile",
                                filesPathKey
                            )
                            
                            # Update S3 object metadata for each file
                            for file in files:
                                update_s3_object_metadata(
                                    file,
                                    event['assetId'],
                                    event['databaseId'],
                                    upload_id,
                                    bucket_name
                                )
                            
                            # Process the external upload
                            result = process_external_upload(
                                upload_id,
                                event['assetId'],
                                event['databaseId'],
                                "assetFile",
                                files,
                                filesPathKey,
                                requestContext
                            )
                            
                            # if result and "assetType" in result:
                            #     # Create a simplified asset object for attachment
                            #     asset = {
                            #         "databaseId": event['databaseId'],
                            #         "assetId": event['assetId'],
                            #         "assetType": result["assetType"],
                            #         "version": result["version"],
                            #         "pipeline": event.get('pipeline', ''),
                            #         "executionId": event.get('executionId', '')
                            #     }
                            #     assets.append(asset)
                            #     logger.info("Asset file upload completed successfully")
                            # else:
                            #     logger.error("Asset file upload failed or returned incomplete data")
                                
                            # # Attach assets to execution record
                            # attach_execution_assets(assets, event['executionId'], event['databaseId'], event['assetId'], event['workflowId'])
                        except Exception as e:
                            logger.exception(f"Error processing asset file upload: {e}")
                    else:
                        logger.warning("No files found in asset output folder")


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
